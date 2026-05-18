import math
from html import escape

from odoo import _, models
from odoo.exceptions import UserError


LINE_TYPE_COMPONENT = "component"
LINE_TYPE_SUBASSEMBLY = "subassembly"


class MrpBomAvailabilityEngine(models.AbstractModel):
    _name = "mrp.bom.availability.engine"
    _description = "BoM Availability Planner Engine"

    def compute(self, wizard):
        wizard.ensure_one()

        aggregated_requirements = {}
        overview_nodes = []
        self._explode_bom(
            wizard.bom_id,
            wizard.product_id,
            factor=1.0,
            overview_nodes=overview_nodes,
            aggregated=aggregated_requirements,
            visited_bom_ids=set(),
        )

        if not aggregated_requirements:
            values = wizard._empty_result_values(_("No BoM components found."))
            values["availability_overview_html"] = self._empty_overview_html(
                _("No BoM components found.")
            )
            return values

        products = self.env["product.product"].browse(list(aggregated_requirements))
        available_qty_by_product = self._get_available_quantities(
            products,
            wizard.location_ids,
            wizard.availability_basis,
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
            "availability_overview_html": self._prepare_overview_html(
                overview_nodes,
                aggregated_requirements,
                available_qty_by_product,
                can_produce_qty,
            ),
        }

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

    def _prepare_overview_html(
        self,
        overview_nodes,
        aggregated_requirements,
        available_qty_by_product,
        overall_can_produce_qty,
    ):
        if not overview_nodes:
            return self._empty_overview_html(_("No BoM components found."))

        header = {
            "component": escape(_("Component")),
            "required": escape(_("Required / Unit")),
            "available": escape(_("Available")),
            "can_produce": escape(_("Can Produce")),
            "status": escape(_("Status")),
        }
        return """
            <style>
                .bap-overview { margin-top: 12px; border-top: 1px solid #d8dde6; font-size: 13px; }
                .bap-row, .bap-head {
                    display: grid;
                    grid-template-columns: minmax(360px, 1fr) 130px 120px 120px 120px;
                    column-gap: 16px;
                    align-items: center;
                    min-height: 36px;
                    border-bottom: 1px solid #eef0f4;
                }
                .bap-head { color: #5f6b7a; font-weight: 600; }
                .bap-product { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
                .bap-number { text-align: right; white-space: nowrap; }
                .bap-children { margin-left: 24px; }
                .bap-subassembly > summary { list-style-position: outside; cursor: pointer; }
                .bap-subassembly > summary::-webkit-details-marker { color: #5f6b7a; }
                .bap-structure { color: #5f6b7a; background: #f8f9fb; }
                .bap-bottleneck { color: #8a5a00; font-weight: 600; background: #fff8e8; }
                .bap-ok { color: #287a3e; }
                .bap-zero, .bap-insufficient { color: #c62828; }
            </style>
            <div class="bap-overview">
                <div class="bap-head">
                    <div>%(component)s</div>
                    <div class="bap-number">%(required)s</div>
                    <div class="bap-number">%(available)s</div>
                    <div class="bap-number">%(can_produce)s</div>
                    <div>%(status)s</div>
                </div>
                %(rows)s
            </div>
        """ % {
            **header,
            "rows": "".join(
                self._render_overview_node(
                    node,
                    aggregated_requirements,
                    available_qty_by_product,
                    overall_can_produce_qty,
                )
                for node in overview_nodes
            )
        }

    def _render_overview_node(
        self,
        node,
        aggregated_requirements,
        available_qty_by_product,
        overall_can_produce_qty,
    ):
        product = node["product"]
        if node["line_type"] == LINE_TYPE_SUBASSEMBLY:
            row = self._render_overview_row(
                product=product,
                required_qty=node["required_qty"],
                uom_name=product.uom_id.name,
                available_qty=None,
                can_produce_qty=None,
                status=_("Structure"),
                css_class="bap-row bap-structure",
            )
            children = "".join(
                self._render_overview_node(
                    child,
                    aggregated_requirements,
                    available_qty_by_product,
                    overall_can_produce_qty,
                )
                for child in node["children"]
            )
            return """
                <details class="bap-subassembly" open>
                    <summary>%s</summary>
                    <div class="bap-children">%s</div>
                </details>
            """ % (row, children)

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
            css_class = "bap-row bap-bottleneck"
        elif available_qty <= 0:
            status = _("Zero Available")
            css_class = "bap-row bap-zero"
        elif can_produce_qty <= 0:
            status = _("Not Enough")
            css_class = "bap-row bap-insufficient"
        else:
            status = _("Enough")
            css_class = "bap-row bap-ok"

        return self._render_overview_row(
            product=product,
            required_qty=node["required_qty"],
            uom_name=product.uom_id.name,
            available_qty=available_qty,
            can_produce_qty=can_produce_qty,
            status=status,
            css_class=css_class,
        )

    def _render_overview_row(
        self,
        product,
        required_qty,
        uom_name,
        available_qty,
        can_produce_qty,
        status,
        css_class,
    ):
        available = self._format_qty(available_qty) if available_qty is not None else "—"
        can_produce = (
            self._format_qty(can_produce_qty, precision=0)
            if can_produce_qty is not None
            else "—"
        )
        return """
            <div class="%s">
                <div class="bap-product" title="%s">%s</div>
                <div class="bap-number">%s %s</div>
                <div class="bap-number">%s</div>
                <div class="bap-number">%s</div>
                <div>%s</div>
            </div>
        """ % (
            css_class,
            escape(product.display_name),
            escape(product.display_name),
            self._format_qty(required_qty),
            escape(uom_name or ""),
            available,
            can_produce,
            escape(status),
        )

    def _empty_overview_html(self, message):
        return '<p class="text-muted">%s</p>' % escape(message)

    def _format_qty(self, qty, precision=4):
        if qty is None:
            return "—"
        return ("%0.*f" % (precision, qty)).rstrip("0").rstrip(".") or "0"
