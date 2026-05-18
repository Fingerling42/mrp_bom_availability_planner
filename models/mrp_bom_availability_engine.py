import math

from odoo import _, models
from odoo.exceptions import UserError

from .mrp_bom_availability_wizard_line import (
    LINE_TYPE_COMPONENT,
    LINE_TYPE_SUBASSEMBLY,
    STATE_OK,
    STATE_SHORTAGE,
    STATE_ZERO,
)


class MrpBomAvailabilityEngine(models.AbstractModel):
    _name = "mrp.bom.availability.engine"
    _description = "BoM Availability Planner Engine"

    def compute(self, wizard):
        wizard.ensure_one()
        aggregated_requirements = {}
        structure_lines = []
        self._explode_bom(
            wizard,
            wizard.bom_id,
            wizard.product_id,
            factor=1.0,
            level=0,
            path=wizard.product_id.display_name,
            aggregated=aggregated_requirements,
            structure_lines=structure_lines,
            visited_bom_ids=set(),
        )
        return self._prepare_result_lines(
            wizard,
            aggregated_requirements,
            structure_lines,
        )

    def get_matching_bom(self, product):
        if not product:
            return self.env["mrp.bom"]

        bom_model = self.env["mrp.bom"]
        try:
            # Odoo's native lookup understands product variants; the search below
            # is only a guarded fallback for signature differences.
            found = bom_model._bom_find(products=product)
            bom = self._extract_bom_find_result(found, product)
            if bom:
                return bom[:1]
        except TypeError:
            pass

        company_domain = [("company_id", "in", [False, self.env.company.id])]
        exact_bom = bom_model.search(
            [
                *company_domain,
                ("product_id", "=", product.id),
                ("type", "in", ["normal", "phantom"]),
            ],
            order="sequence, id",
            limit=1,
        )
        if exact_bom:
            return exact_bom

        template_boms = bom_model.search(
            [
                *company_domain,
                ("product_id", "=", False),
                ("product_tmpl_id", "=", product.product_tmpl_id.id),
                ("type", "in", ["normal", "phantom"]),
            ],
            order="sequence, id",
        )
        return template_boms.filtered(
            lambda bom: self._is_bom_applicable_to_product(bom, product)
        )[:1]

    def _explode_bom(
        self,
        wizard,
        bom,
        product,
        factor,
        level=0,
        path=False,
        aggregated=None,
        structure_lines=None,
        visited_bom_ids=None,
    ):
        aggregated = aggregated if aggregated is not None else {}
        structure_lines = structure_lines if structure_lines is not None else []
        visited_bom_ids = visited_bom_ids if visited_bom_ids is not None else set()
        if not bom or not product:
            return aggregated

        if bom.id in visited_bom_ids:
            raise UserError(_("Recursive BoM detected at %s.") % bom.display_name)

        visited_bom_ids.add(bom.id)
        bom_qty = bom.product_qty or 1.0
        factor_in_bom_uom = product.uom_id._compute_quantity(factor, bom.product_uom_id)
        line_factor = factor_in_bom_uom / bom_qty

        for bom_line in bom.bom_line_ids.sorted(
            key=lambda line: (line.sequence, line.id)
        ):
            component = bom_line.product_id
            if not component or not self._is_bom_line_applicable(bom_line, product):
                continue

            line_qty = bom_line.product_qty * line_factor
            component_path = "%s / %s" % (
                path or bom.display_name,
                component.display_name,
            )
            child_bom = self.get_matching_bom(component)

            # MVP behavior: when explode_subassemblies=True, the planner assumes
            # subassemblies are produced from components and does not net available
            # subassembly stock first.
            if wizard.explode_subassemblies and child_bom:
                child_factor = bom_line.product_uom_id._compute_quantity(
                    line_qty, component.uom_id
                )
                # Subassembly rows are context for the visible BoM tree. They do
                # not participate in the final bottleneck calculation.
                if child_factor > 0 or wizard.include_zero_required:
                    structure_lines.append(
                        {
                            "product": component,
                            "required_qty": child_factor,
                            "level": level,
                            "parent_bom": bom,
                            "route_note": component_path,
                            "sequence": bom_line.sequence,
                        }
                    )
                self._explode_bom(
                    wizard,
                    child_bom,
                    component,
                    factor=child_factor,
                    level=level + 1,
                    path=component_path,
                    aggregated=aggregated,
                    structure_lines=structure_lines,
                    visited_bom_ids=set(visited_bom_ids),
                )
                continue

            required_qty = bom_line.product_uom_id._compute_quantity(
                line_qty, component.uom_id
            )
            if required_qty <= 0 and not wizard.include_zero_required:
                continue

            key = component.id
            requirement = aggregated.setdefault(
                key,
                {
                    "product": component,
                    "required_qty": 0.0,
                    "level": level,
                    "parent_bom": bom,
                    "route_notes": set(),
                },
            )
            requirement["required_qty"] += required_qty
            requirement["level"] = min(requirement["level"], level)
            if requirement["parent_bom"] != bom:
                requirement["parent_bom"] = False
            requirement["route_notes"].add(component_path)

        visited_bom_ids.remove(bom.id)
        return aggregated

    def _is_bom_applicable_to_product(self, bom, product):
        if bom.product_id:
            return bom.product_id == product
        if bom.product_tmpl_id != product.product_tmpl_id:
            return False

        product_variants = getattr(bom, "product_variant_ids", False)
        if product_variants:
            return product in product_variants

        possible_attribute_values = getattr(
            bom, "possible_product_template_attribute_value_ids", False
        )
        if possible_attribute_values:
            product_values = product.product_template_attribute_value_ids
            return bool(product_values & possible_attribute_values)

        return True

    def _extract_bom_find_result(self, found, product):
        if not isinstance(found, dict):
            return found

        bom = found.get(product) or found.get(product.id)
        if bom:
            return bom

        for found_product, found_bom in found.items():
            if getattr(found_product, "id", found_product) == product.id:
                return found_bom
        return self.env["mrp.bom"]

    def _is_bom_line_applicable(self, bom_line, product):
        required_values = bom_line.bom_product_template_attribute_value_ids
        if not required_values:
            return True
        product_value_ids = set(product.product_template_attribute_value_ids.ids)
        return set(required_values.ids).issubset(product_value_ids)

    def _get_available_quantities(self, products, locations, availability_basis):
        qty_by_product = {product.id: 0.0 for product in products}
        location_note_by_product = {product.id: "" for product in products}
        if not products or not locations:
            return qty_by_product, location_note_by_product

        # read_group keeps the stock query bounded even when an exploded BoM has
        # many repeated components across selected locations.
        groups = self.env["stock.quant"].read_group(
            [
                ("product_id", "in", products.ids),
                ("location_id", "in", locations.ids),
            ],
            ["product_id", "quantity", "reserved_quantity"],
            ["product_id", "location_id"],
            lazy=False,
        )
        product_by_id = {product.id: product for product in products}
        location_lines_by_product = {product.id: [] for product in products}
        for group in groups:
            product_id = group["product_id"][0]
            location_id = group["location_id"][0]
            location_name = group["location_id"][1]
            quantity = group.get("quantity", 0.0)
            if availability_basis == "available":
                quantity -= group.get("reserved_quantity", 0.0)
            product_qty = product_by_id[product_id].uom_id._compute_quantity(
                quantity,
                product_by_id[product_id].uom_id,
            )
            if product_qty:
                qty_by_product[product_id] += product_qty
                location_lines_by_product[product_id].append(
                    (location_name, product_qty, location_id)
                )

        for product_id, location_lines in location_lines_by_product.items():
            location_note_by_product[product_id] = "; ".join(
                "%s: %s" % (location_name, qty)
                for location_name, qty, _location_id in sorted(
                    location_lines, key=lambda item: (item[0], item[2])
                )
            )

        return qty_by_product, location_note_by_product

    def _prepare_result_lines(
        self, wizard, aggregated_requirements, structure_lines=None
    ):
        structure_lines = structure_lines or []
        if not aggregated_requirements and not structure_lines:
            return [], wizard._empty_result_values(_("No BoM components found."))

        product_ids = set(aggregated_requirements.keys())
        product_ids.update(line["product"].id for line in structure_lines)
        products = self.env["product.product"].browse(list(product_ids))
        available_qty_by_product, location_note_by_product = (
            self._get_available_quantities(
                products,
                wizard.location_ids,
                wizard.availability_basis,
            )
        )
        prepared_lines = []

        for sequence, structure_line in enumerate(
            sorted(
                structure_lines,
                key=lambda data: (data["route_note"], data["sequence"]),
            ),
            start=1,
        ):
            product = structure_line["product"]
            prepared_lines.append(
                self._prepare_result_line_values(
                    wizard,
                    sequence * 10,
                    structure_line["level"],
                    structure_line["parent_bom"],
                    product,
                    structure_line["required_qty"],
                    structure_line["route_note"],
                    LINE_TYPE_SUBASSEMBLY,
                    available_qty_by_product,
                    location_note_by_product,
                )
            )

        component_lines = []
        start_sequence = len(prepared_lines) + 1
        for sequence, requirement in enumerate(
            sorted(
                aggregated_requirements.values(),
                key=lambda data: (
                    sorted(data["route_notes"])[0] if data["route_notes"] else "",
                    data["product"].display_name,
                ),
            ),
            start=start_sequence,
        ):
            product = requirement["product"]
            required_qty_per_unit = requirement["required_qty"]
            if required_qty_per_unit <= 0 and not wizard.include_zero_required:
                continue

            route_note = "\n".join(sorted(requirement["route_notes"]))
            component_line = self._prepare_result_line_values(
                wizard,
                sequence * 10,
                requirement["level"],
                requirement["parent_bom"],
                product,
                required_qty_per_unit,
                route_note,
                LINE_TYPE_COMPONENT,
                available_qty_by_product,
                location_note_by_product,
            )
            prepared_lines.append(component_line)
            component_lines.append(component_line)

        if not component_lines:
            return [], wizard._empty_result_values(_("No BoM components found."))

        # Re-sort after aggregation so context rows and leaf components appear as
        # a readable BoM-like hierarchy in the one2many tree.
        prepared_lines.sort(
            key=lambda line: (
                line["route_note"] or "",
                0 if line["line_type"] == LINE_TYPE_SUBASSEMBLY else 1,
                line["product_id"],
            )
        )
        for sequence, line in enumerate(prepared_lines, start=1):
            line["sequence"] = sequence * 10

        overall_can_produce = min(line["can_produce_qty"] for line in component_lines)
        for line in component_lines:
            line["is_bottleneck"] = line["can_produce_qty"] == overall_can_produce
            line["bottleneck_note"] = _("Bottleneck") if line["is_bottleneck"] else ""

        bottleneck_line = sorted(
            (line for line in component_lines if line["is_bottleneck"]),
            key=lambda line: (
                line["can_produce_qty"],
                -line["shortage_qty"],
                self.env["product.product"].browse(line["product_id"]).display_name,
            ),
        )[0]
        bottleneck_product = self.env["product.product"].browse(
            bottleneck_line["product_id"]
        )
        shortage_count = sum(1 for line in component_lines if line["shortage_qty"] > 0)
        zero_count = sum(
            1 for line in component_lines if line["availability_state"] == STATE_ZERO
        )
        summary = _(
            "%(qty)s unit(s) from %(location_count)s location(s). "
            "Bottleneck: %(product)s. Shortages: %(shortage_count)s; zero stock: %(zero_count)s."
        ) % {
            "qty": overall_can_produce,
            "location_count": len(wizard.location_ids),
            "product": bottleneck_product.display_name,
            "shortage_count": shortage_count,
            "zero_count": zero_count,
        }

        return [(0, 0, line) for line in prepared_lines], {
            "can_produce_qty": overall_can_produce,
            "bottleneck_product_id": bottleneck_product.id,
            "bottleneck_qty": bottleneck_line["available_qty"],
            "summary": summary,
        }

    def _prepare_result_line_values(
        self,
        wizard,
        sequence,
        level,
        parent_bom,
        product,
        required_qty_per_unit,
        route_note,
        line_type,
        available_qty_by_product,
        location_note_by_product,
    ):
        available_qty = available_qty_by_product.get(product.id, 0.0)
        required_qty_for_target = required_qty_per_unit * wizard.target_qty
        can_produce_qty = (
            max(math.floor(available_qty / required_qty_per_unit), 0)
            if required_qty_per_unit > 0
            else 0
        )
        shortage_qty = max(required_qty_for_target - available_qty, 0.0)
        if available_qty <= 0:
            availability_state = STATE_ZERO
        elif shortage_qty > 0:
            availability_state = STATE_SHORTAGE
        else:
            availability_state = STATE_OK

        return {
            "sequence": sequence,
            "line_type": line_type,
            "level": level,
            "parent_bom_id": parent_bom.id if parent_bom else False,
            "product_id": product.id,
            "component_label": self._format_component_label(product, level, line_type),
            "product_uom_id": product.uom_id.id,
            "required_qty_per_unit": required_qty_per_unit,
            "required_qty_for_target": required_qty_for_target,
            "available_qty": available_qty,
            "can_produce_qty": can_produce_qty,
            "shortage_qty": shortage_qty,
            "availability_state": availability_state,
            "available_location_note": location_note_by_product.get(product.id, ""),
            "route_note": route_note,
        }

    def _format_component_label(self, product, level, line_type):
        # Standard one2many trees cannot expand/collapse like the BoM Overview,
        # so the label carries a lightweight visual hierarchy instead.
        prefix = "↳ " * max(level, 0)
        marker = "▸ " if line_type == LINE_TYPE_SUBASSEMBLY else ""
        return "%s%s%s" % (prefix, marker, product.display_name)
