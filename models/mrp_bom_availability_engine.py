import math

from odoo import _, models
from odoo.exceptions import UserError


LINE_TYPE_COMPONENT = "component"
LINE_TYPE_SUBASSEMBLY = "subassembly"


class MrpBomAvailabilityEngine(models.AbstractModel):
    _name = "mrp.bom.availability.engine"
    _description = "BoM Availability Planner Engine"

    def compute(self, wizard):
        wizard.ensure_one()
        return self.get_availability_data(
            wizard.product_id.id,
            wizard.bom_id.id,
            wizard.location_ids.ids,
            wizard.availability_basis,
        )

    def get_availability_data(
        self,
        product_id,
        bom_id,
        location_ids,
        availability_basis="on_hand",
    ):
        product = (
            self.env["product.product"].browse(product_id).exists()
            if product_id
            else self.env["product.product"]
        )
        bom = (
            self.env["mrp.bom"].browse(bom_id).exists()
            if bom_id
            else self.env["mrp.bom"]
        )
        locations = (
            self.env["stock.location"].browse(location_ids).exists()
            if location_ids
            else self.env["stock.location"]
        )
        self._validate_availability_inputs(product, bom, locations, availability_basis)

        aggregated_requirements = {}
        overview_nodes = []
        self._explode_bom(
            bom,
            product,
            factor=1.0,
            overview_nodes=overview_nodes,
            aggregated=aggregated_requirements,
            visited_bom_ids=set(),
        )

        if not aggregated_requirements:
            values = self._empty_result_values(_("No BoM components found."))
            values["availability_overview_data"] = self._empty_overview_data(
                _("No BoM components found.")
            )
            return values

        products = self.env["product.product"].browse(list(aggregated_requirements))
        available_qty_by_product = self._get_available_quantities(
            products,
            locations,
            availability_basis,
        )
        bottleneck_product, bottleneck_available, can_produce_qty = (
            self._get_bottleneck(aggregated_requirements, available_qty_by_product)
        )

        return {
            "can_produce_qty": can_produce_qty,
            "bottleneck_product_id": bottleneck_product.id,
            "bottleneck_qty": bottleneck_available,
            "summary": _(
                "%(qty)s unit(s) can be produced from selected locations. "
                "Bottleneck: %(product)s."
            )
            % {
                "qty": can_produce_qty,
                "product": bottleneck_product.display_name,
            },
            "availability_overview_data": self._prepare_overview_data(
                overview_nodes,
                aggregated_requirements,
                available_qty_by_product,
                can_produce_qty,
            ),
        }

    def _validate_availability_inputs(
        self,
        product,
        bom,
        locations,
        availability_basis,
    ):
        if not product:
            raise UserError(_("Select a Product Variant."))
        if not bom:
            raise UserError(_("Select a Bill of Materials."))
        if not locations:
            raise UserError(_("Select at least one Location."))
        if availability_basis not in ("on_hand", "available"):
            raise UserError(_("Select a valid availability basis."))
        if bom.type not in ("normal", "phantom"):
            raise UserError(_("Select a manufacturing or kit Bill of Materials."))
        if bom.company_id and bom.company_id != self.env.company:
            raise UserError(_("Select a Bill of Materials from the current company."))
        if any(
            location.company_id and location.company_id != self.env.company
            for location in locations
        ):
            raise UserError(_("Select only locations from the current company."))
        if not self._is_bom_applicable_to_product(bom, product):
            raise UserError(_("Selected Bill of Materials does not match the product."))

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
        bom,
        product,
        factor,
        overview_nodes,
        aggregated,
        visited_bom_ids,
    ):
        if not bom or not product:
            return

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
            if line_qty <= 0:
                continue

            child_bom = self.get_matching_bom(component)
            required_qty = bom_line.product_uom_id._compute_quantity(
                line_qty, component.uom_id
            )

            # Subassembly rows are visual structure only. The bottleneck is
            # calculated from leaf components, matching the fully exploded MVP.
            if child_bom:
                child_nodes = []
                overview_nodes.append(
                    {
                        "line_type": LINE_TYPE_SUBASSEMBLY,
                        "product": component,
                        "required_qty": required_qty,
                        "children": child_nodes,
                    }
                )
                self._explode_bom(
                    child_bom,
                    component,
                    factor=required_qty,
                    overview_nodes=child_nodes,
                    aggregated=aggregated,
                    visited_bom_ids=set(visited_bom_ids),
                )
                continue

            overview_nodes.append(
                {
                    "line_type": LINE_TYPE_COMPONENT,
                    "product": component,
                    "required_qty": required_qty,
                    "children": [],
                }
            )
            requirement = aggregated.setdefault(
                component.id,
                {
                    "product": component,
                    "required_qty": 0.0,
                },
            )
            requirement["required_qty"] += required_qty

        visited_bom_ids.remove(bom.id)

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
        if not products or not locations:
            return qty_by_product

        # read_group keeps the stock query bounded even when an exploded BoM has
        # many repeated components across selected locations.
        groups = self.env["stock.quant"].read_group(
            [
                ("product_id", "in", products.ids),
                ("location_id", "in", locations.ids),
            ],
            ["product_id", "quantity", "reserved_quantity"],
            ["product_id"],
        )
        for group in groups:
            product_id = group["product_id"][0]
            quantity = group.get("quantity", 0.0)
            if availability_basis == "available":
                quantity -= group.get("reserved_quantity", 0.0)
            qty_by_product[product_id] = quantity

        return qty_by_product

    def _get_bottleneck(self, aggregated_requirements, available_qty_by_product):
        candidate_lines = []
        for requirement in aggregated_requirements.values():
            product = requirement["product"]
            required_qty = requirement["required_qty"]
            available_qty = available_qty_by_product.get(product.id, 0.0)
            can_produce_qty = (
                max(math.floor(available_qty / required_qty), 0)
                if required_qty > 0
                else 0
            )
            candidate_lines.append(
                {
                    "product": product,
                    "available_qty": available_qty,
                    "can_produce_qty": can_produce_qty,
                }
            )

        bottleneck = sorted(
            candidate_lines,
            key=lambda line: (
                line["can_produce_qty"],
                line["product"].display_name,
            ),
        )[0]
        return (
            bottleneck["product"],
            bottleneck["available_qty"],
            bottleneck["can_produce_qty"],
        )

    def _prepare_overview_data(
        self,
        overview_nodes,
        aggregated_requirements,
        available_qty_by_product,
        overall_can_produce_qty,
    ):
        if not overview_nodes:
            return self._empty_overview_data(_("No BoM components found."))

        return {
            "columns": [
                {"name": "component", "label": _("Component")},
                {"name": "required_qty", "label": _("Required / Unit")},
                {"name": "available_qty", "label": _("Available")},
                {"name": "can_produce_qty", "label": _("Can Produce")},
                {"name": "status", "label": _("Status")},
            ],
            "lines": [
                self._prepare_overview_node(
                    node,
                    aggregated_requirements,
                    available_qty_by_product,
                    overall_can_produce_qty,
                )
                for node in overview_nodes
            ],
        }

    def _prepare_overview_node(
        self,
        node,
        aggregated_requirements,
        available_qty_by_product,
        overall_can_produce_qty,
    ):
        product = node["product"]
        if node["line_type"] == LINE_TYPE_SUBASSEMBLY:
            return {
                **self._prepare_product_node(product, node["required_qty"]),
                "line_type": LINE_TYPE_SUBASSEMBLY,
                "status": "structure",
                "status_label": _("Structure"),
                "children": [
                    self._prepare_overview_node(
                        child,
                        aggregated_requirements,
                        available_qty_by_product,
                        overall_can_produce_qty,
                    )
                    for child in node["children"]
                ],
            }

        requirement = aggregated_requirements[product.id]
        required_qty = requirement["required_qty"]
        available_qty = available_qty_by_product.get(product.id, 0.0)
        can_produce_qty = (
            max(math.floor(available_qty / required_qty), 0)
            if required_qty > 0
            else 0
        )
        if can_produce_qty == overall_can_produce_qty:
            status = _("Bottleneck")
            status_code = "bottleneck"
        elif available_qty <= 0:
            status = _("Zero Available")
            status_code = "zero_available"
        elif can_produce_qty <= 0:
            status = _("Not Enough")
            status_code = "not_enough"
        else:
            status = _("Enough")
            status_code = "enough"

        return {
            **self._prepare_product_node(product, node["required_qty"]),
            "line_type": LINE_TYPE_COMPONENT,
            "available_qty": available_qty,
            "can_produce_qty": can_produce_qty,
            "status": status_code,
            "status_label": status,
            "children": [],
        }

    def _prepare_product_node(self, product, required_qty):
        return {
            "product_id": product.id,
            "product_display_name": product.display_name,
            "required_qty": required_qty,
            "uom_id": product.uom_id.id,
            "uom_name": product.uom_id.name,
        }

    def _empty_overview_data(self, message):
        return {
            "columns": [],
            "lines": [],
            "message": message,
        }

    def _empty_result_values(self, summary):
        return {
            "can_produce_qty": 0.0,
            "bottleneck_product_id": False,
            "bottleneck_qty": 0.0,
            "summary": summary,
            "availability_overview_data": False,
        }
