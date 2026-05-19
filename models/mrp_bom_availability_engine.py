import math

from odoo import _, api, models
from odoo.exceptions import UserError


LINE_TYPE_COMPONENT = "component"
LINE_TYPE_SUBASSEMBLY = "subassembly"


class MrpBomAvailabilityEngine(models.AbstractModel):
    _name = "mrp.bom.availability.engine"
    _description = "BoM Availability Planner Engine"

    @api.model
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
        aggregated_subassemblies = {}
        overview_nodes = []
        self._explode_bom(
            bom,
            product,
            factor=1.0,
            overview_nodes=overview_nodes,
            aggregated=aggregated_requirements,
            aggregated_subassemblies=aggregated_subassemblies,
            visited_bom_ids=set(),
        )

        if not aggregated_requirements:
            values = self._empty_result_values(_("No BoM components found."))
            values["availability_overview_data"] = self._empty_overview_data(
                _("No BoM components found.")
            )
            return values

        product_ids = set(aggregated_requirements)
        self._collect_overview_product_ids(overview_nodes, product_ids)
        products = self.env["product.product"].browse(list(product_ids))
        available_qty_by_product = self._get_available_quantities(
            products,
            locations,
            availability_basis,
        )
        self._compute_overview_capacities(
            overview_nodes,
            aggregated_requirements,
            aggregated_subassemblies,
            available_qty_by_product,
        )
        can_produce_qty = self._get_root_capacity(overview_nodes)
        bottleneck_node = self._find_bottleneck_node(overview_nodes, can_produce_qty)
        bottleneck_product = bottleneck_node["product"]
        bottleneck_available = bottleneck_node.get("available_qty", 0.0)
        overview_data = self._prepare_overview_data(
            product,
            overview_nodes,
            aggregated_requirements,
            aggregated_subassemblies,
            available_qty_by_product,
            can_produce_qty,
        )

        return {
            "can_produce_qty": can_produce_qty,
            "bottleneck_product_id": bottleneck_product.id,
            "bottleneck_product_display_name": bottleneck_product.display_name,
            "bottleneck_qty": bottleneck_available,
            "summary": _(
                "%(qty)s unit(s) can be produced from selected locations. "
                "Bottleneck: %(product)s."
            )
            % {
                "qty": can_produce_qty,
                "product": bottleneck_product.display_name,
            },
            "availability_overview_data": overview_data,
        }

    @api.model
    def get_client_action_data(self):
        default_locations = self.env["stock.location"].search(
            [
                ("usage", "=", "internal"),
                ("company_id", "in", [False, self.env.company.id]),
            ],
            limit=1,
        )
        return {
            "title": _("BoM Availability Planner"),
            "empty_message": _("No availability data yet."),
            "company_id": self.env.company.id,
            "availability_basis_options": [
                {"value": "on_hand", "label": _("On Hand")},
                {"value": "available", "label": _("Available / Unreserved")},
            ],
            "default_location_ids": default_locations.ids,
            "default_location_names": default_locations.mapped("display_name"),
        }

    @api.model
    def get_matching_bom_data(self, product_id):
        product = self.env["product.product"].browse(product_id).exists()
        bom = self.get_matching_bom(product) if product else self.env["mrp.bom"]
        return self._record_display_data(bom)

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
        aggregated_subassemblies,
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

            if child_bom:
                child_nodes = []
                requirement = aggregated_subassemblies.setdefault(
                    component.id,
                    {
                        "product": component,
                        "required_qty": 0.0,
                        "occurrence_count": 0,
                    },
                )
                requirement["required_qty"] += required_qty
                requirement["occurrence_count"] += 1
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
                    aggregated_subassemblies=aggregated_subassemblies,
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
                    "occurrence_count": 0,
                },
            )
            requirement["required_qty"] += required_qty
            requirement["occurrence_count"] += 1

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
                ("location_id", "child_of", locations.ids),
                ("company_id", "in", [False, self.env.company.id]),
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

    def _collect_overview_product_ids(self, nodes, product_ids):
        for node in nodes:
            product_ids.add(node["product"].id)
            self._collect_overview_product_ids(node["children"], product_ids)

    def _compute_overview_capacities(
        self,
        nodes,
        aggregated_requirements,
        aggregated_subassemblies,
        available_qty_by_product,
    ):
        for node in nodes:
            product = node["product"]
            available_qty = available_qty_by_product.get(product.id, 0.0)
            node["available_qty"] = available_qty
            if node["line_type"] == LINE_TYPE_SUBASSEMBLY:
                self._compute_overview_capacities(
                    node["children"],
                    aggregated_requirements,
                    aggregated_subassemblies,
                    available_qty_by_product,
                )
                production_capacity = self._get_root_capacity(node["children"])
                total_required_qty = aggregated_subassemblies[product.id][
                    "required_qty"
                ]
                stock_capacity = self._capacity_from_qty(
                    available_qty,
                    total_required_qty,
                )
                # A subassembly can satisfy demand either from finished stock or
                # by producing more units from its own BoM. The finished stock
                # capacity is based on total demand across the tree, so repeated
                # subassemblies share the same stock instead of double-counting it.
                node["stock_capacity_qty"] = stock_capacity
                node["production_capacity_qty"] = production_capacity
                node["capacity_qty"] = stock_capacity + production_capacity
                continue

            total_required_qty = aggregated_requirements[product.id]["required_qty"]
            node["capacity_qty"] = self._capacity_from_qty(
                available_qty,
                total_required_qty,
            )

    def _get_root_capacity(self, nodes):
        if not nodes:
            return 0
        return min(node["capacity_qty"] for node in nodes)

    def _capacity_from_qty(self, available_qty, required_qty):
        if required_qty <= 0:
            return 0
        return max(math.floor(available_qty / required_qty), 0)

    def _find_bottleneck_node(self, nodes, target_capacity):
        candidates = sorted(nodes, key=lambda node: node["product"].display_name)
        for node in candidates:
            if node["capacity_qty"] != target_capacity:
                continue
            if node["line_type"] == LINE_TYPE_SUBASSEMBLY:
                production_capacity = node.get("production_capacity_qty", 0)
                stock_capacity = node.get("stock_capacity_qty", 0)
                if (
                    node["children"]
                    and production_capacity == target_capacity
                    and production_capacity >= stock_capacity
                ):
                    return self._find_bottleneck_node(node["children"], target_capacity)
            return node
        return candidates[0]

    def _prepare_overview_data(
        self,
        product,
        overview_nodes,
        aggregated_requirements,
        aggregated_subassemblies,
        available_qty_by_product,
        overall_can_produce_qty,
    ):
        if not overview_nodes:
            return self._empty_overview_data(_("No BoM components found."))

        seen_component_ids = set()
        seen_subassembly_ids = set()
        children = [
            self._prepare_overview_node(
                node,
                aggregated_requirements,
                aggregated_subassemblies,
                available_qty_by_product,
                overall_can_produce_qty,
                seen_component_ids=seen_component_ids,
                seen_subassembly_ids=seen_subassembly_ids,
                line_path="0.%s" % index,
                level=1,
                covered_by_stock=False,
            )
            for index, node in enumerate(overview_nodes, start=1)
        ]

        return {
            "columns": [
                {"name": "component", "label": _("Component")},
                {"name": "required_qty", "label": _("Required / Unit")},
                {"name": "available_qty", "label": _("Available")},
                {"name": "can_produce_qty", "label": _("Can Produce Finished")},
                {"name": "status", "label": _("Status")},
            ],
            "lines": [
                {
                    **self._prepare_product_node(product, 1.0),
                    "line_id": "0",
                    "level": 0,
                    "line_type": "finished",
                    "available_qty": None,
                    "can_produce_qty": overall_can_produce_qty,
                    "capacity_qty": overall_can_produce_qty,
                    "status": "finished",
                    "status_label": _("Finished Product"),
                    "children": children,
                }
            ],
        }

    def _prepare_overview_node(
        self,
        node,
        aggregated_requirements,
        aggregated_subassemblies,
        available_qty_by_product,
        overall_can_produce_qty,
        seen_component_ids,
        seen_subassembly_ids,
        line_path,
        level,
        covered_by_stock,
    ):
        product = node["product"]
        if node["line_type"] == LINE_TYPE_SUBASSEMBLY:
            capacity_qty = node["capacity_qty"]
            stock_capacity = node.get("stock_capacity_qty", 0)
            stock_covers_current_need = (
                stock_capacity > 0 and stock_capacity >= overall_can_produce_qty
            )
            children = [
                self._prepare_overview_node(
                    child,
                    aggregated_requirements,
                    aggregated_subassemblies,
                    available_qty_by_product,
                    overall_can_produce_qty,
                    seen_component_ids,
                    seen_subassembly_ids,
                    line_path="%s.%s" % (line_path, child_index),
                    level=level + 1,
                    covered_by_stock=covered_by_stock or stock_covers_current_need,
                )
                for child_index, child in enumerate(node["children"], start=1)
            ]
            requirement = aggregated_subassemblies[product.id]
            is_shared_subassembly = requirement["occurrence_count"] > 1
            is_first_occurrence = product.id not in seen_subassembly_ids
            seen_subassembly_ids.add(product.id)
            if covered_by_stock:
                status_code = "covered_by_stock"
                status = _("Covered by Stock")
            elif is_shared_subassembly and not is_first_occurrence:
                status_code = "shared_stock"
                status = _("Shared Stock")
            elif stock_covers_current_need:
                status_code = "available_stock"
                status = _("Available Stock")
            elif capacity_qty == overall_can_produce_qty:
                status_code = "limited"
                status = _("Limited")
            else:
                status_code = "enough"
                status = _("Enough")
            return {
                **self._prepare_product_node(product, node["required_qty"]),
                "line_id": line_path,
                "level": level,
                "line_type": LINE_TYPE_SUBASSEMBLY,
                "available_qty": node["available_qty"] if is_first_occurrence else None,
                "can_produce_qty": capacity_qty,
                "capacity_qty": capacity_qty,
                "status": status_code,
                "status_label": status,
                "children": children,
            }

        requirement = aggregated_requirements[product.id]
        available_qty = available_qty_by_product.get(product.id, 0.0)
        occurrence_count = requirement["occurrence_count"]
        is_shared_component = occurrence_count > 1
        is_first_occurrence = product.id not in seen_component_ids
        seen_component_ids.add(product.id)
        can_produce_qty = node["capacity_qty"]
        if covered_by_stock:
            status = _("Covered by Stock")
            status_code = "covered_by_stock"
        elif is_shared_component and not is_first_occurrence:
            status = _("Shared Stock")
            status_code = "shared_stock"
        elif can_produce_qty == overall_can_produce_qty:
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
            "line_id": line_path,
            "level": level,
            "line_type": LINE_TYPE_COMPONENT,
            "available_qty": available_qty if is_first_occurrence else None,
            "can_produce_qty": None,
            "capacity_qty": can_produce_qty,
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
            "bottleneck_product_display_name": False,
            "bottleneck_qty": 0.0,
            "summary": summary,
            "availability_overview_data": False,
        }

    def _record_display_data(self, record):
        if not record:
            return False
        record.ensure_one()
        return {
            "id": record.id,
            "display_name": record.display_name,
        }
