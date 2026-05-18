import math

from odoo import api, fields, models, _
from odoo.exceptions import UserError


class MrpBomAvailabilityWizard(models.TransientModel):
    _name = "mrp.bom.availability.wizard"
    _description = "BoM Availability Planner"
    _rec_name = "name"

    name = fields.Char(
        default=lambda self: _("BoM Availability Planner"),
        readonly=True,
    )

    product_id = fields.Many2one(
        "product.product",
        string="Product Variant",
        domain="[('type', 'in', ['product', 'consu'])]",
    )
    bom_id = fields.Many2one(
        "mrp.bom",
        string="Bill of Materials",
    )
    location_ids = fields.Many2many(
        "stock.location",
        string="Locations",
        required=True,
        domain="[('usage', '=', 'internal')]",
        default=lambda self: self._default_location_ids(),
        help="Internal stock locations used to calculate available component quantities.",
    )
    target_qty = fields.Float(
        string="Target Quantity",
        default=1.0,
        required=True,
    )
    product_uom_id = fields.Many2one(
        related="product_id.uom_id",
        string="Product Unit of Measure",
        readonly=True,
    )
    product_tmpl_id = fields.Many2one(
        related="product_id.product_tmpl_id",
        string="Product Template",
        readonly=True,
    )
    availability_basis = fields.Selection(
        selection=[
            ("on_hand", "On Hand"),
            ("available", "Available / Unreserved"),
        ],
        string="Availability Basis",
        default="on_hand",
        required=True,
    )
    explode_subassemblies = fields.Boolean(
        string="Explode Subassemblies",
        default=True,
    )
    include_zero_required = fields.Boolean(
        string="Show Zero Required Lines",
        default=False,
    )
    line_ids = fields.One2many(
        "mrp.bom.availability.wizard.line",
        "wizard_id",
        string="Availability Lines",
        readonly=True,
    )
    can_produce_qty = fields.Float(
        string="Can Produce Now",
        readonly=True,
    )
    bottleneck_product_id = fields.Many2one(
        "product.product",
        string="Main Bottleneck",
        readonly=True,
    )
    bottleneck_qty = fields.Float(
        string="Bottleneck Component Availability",
        readonly=True,
    )
    summary = fields.Text(
        string="Summary",
        readonly=True,
    )

    @api.model
    def _default_location_ids(self):
        return self.env["stock.location"].search([("usage", "=", "internal")], limit=1)

    @api.model
    def action_open_planner(self):
        wizard = self.create({})
        return wizard._reopen_wizard()

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for wizard in self:
            wizard.line_ids = [(5, 0, 0)]
            wizard.can_produce_qty = 0.0
            wizard.bottleneck_product_id = False
            wizard.bottleneck_qty = 0.0
            wizard.summary = False
            wizard.bom_id = (
                wizard._get_matching_bom(wizard.product_id)
                if wizard.product_id
                else False
            )

    def action_clear_lines(self):
        self.ensure_one()
        self.line_ids.unlink()
        self.write(
            {
                "can_produce_qty": 0.0,
                "bottleneck_product_id": False,
                "bottleneck_qty": 0.0,
                "summary": False,
            }
        )
        return self._reopen_wizard()

    def action_compute_availability(self):
        self.ensure_one()
        if not self.product_id:
            raise UserError(_("Select a Product Variant."))
        if not self.bom_id:
            raise UserError(_("Select a Bill of Materials."))
        if self.target_qty < 0:
            raise UserError(_("Target Quantity cannot be negative."))
        if not self.location_ids:
            raise UserError(_("Select at least one Location."))

        self.line_ids.unlink()
        aggregated_requirements = {}
        self._explode_bom(
            self.bom_id,
            self.product_id,
            factor=1.0,
            level=0,
            path=self.product_id.display_name,
            aggregated=aggregated_requirements,
            visited_bom_ids=set(),
        )

        line_commands, summary_values = self._prepare_result_lines(
            aggregated_requirements
        )
        self.write(
            {
                "line_ids": line_commands,
                **summary_values,
            }
        )
        return self._reopen_wizard()

    def _reopen_wizard(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("BoM Availability Planner"),
            "res_model": self._name,
            "view_mode": "form",
            "res_id": self.id,
            "target": "current",
            "context": {"form_view_initial_mode": "edit"},
        }

    def _explode_bom(
        self,
        bom,
        product,
        factor,
        level=0,
        path=False,
        aggregated=None,
        visited_bom_ids=None,
    ):
        self.ensure_one()
        aggregated = aggregated if aggregated is not None else {}
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
            child_bom = self._get_matching_bom(component)

            # MVP behavior: when explode_subassemblies=True, the planner assumes
            # subassemblies are produced from components and does not net available
            # subassembly stock first.
            if self.explode_subassemblies and child_bom:
                child_factor = bom_line.product_uom_id._compute_quantity(
                    line_qty, component.uom_id
                )
                self._explode_bom(
                    child_bom,
                    component,
                    factor=child_factor,
                    level=level + 1,
                    path=component_path,
                    aggregated=aggregated,
                    visited_bom_ids=set(visited_bom_ids),
                )
                continue

            required_qty = bom_line.product_uom_id._compute_quantity(
                line_qty, component.uom_id
            )
            if required_qty <= 0 and not self.include_zero_required:
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

    def _get_matching_bom(self, product):
        self.ensure_one()
        if not product:
            return self.env["mrp.bom"]

        bom_model = self.env["mrp.bom"]
        try:
            found = bom_model._bom_find(products=product)
            if isinstance(found, dict):
                bom = found.get(product) or found.get(product.id)
                if not bom:
                    for found_product, found_bom in found.items():
                        if getattr(found_product, "id", found_product) == product.id:
                            bom = found_bom
                            break
            else:
                bom = found
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

        return bom_model.search(
            [
                *company_domain,
                ("product_id", "=", False),
                ("product_tmpl_id", "=", product.product_tmpl_id.id),
                ("type", "in", ["normal", "phantom"]),
            ],
            order="sequence, id",
            limit=1,
        )

    def _is_bom_line_applicable(self, bom_line, product):
        required_values = bom_line.bom_product_template_attribute_value_ids
        if not required_values:
            return True
        product_value_ids = set(product.product_template_attribute_value_ids.ids)
        return set(required_values.ids).issubset(product_value_ids)

    def _get_available_quantities(self, products, locations):
        self.ensure_one()
        qty_by_product = {product.id: 0.0 for product in products}
        location_note_by_product = {product.id: "" for product in products}
        if not products or not locations:
            return qty_by_product, location_note_by_product

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
            if self.availability_basis == "available":
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

    def _prepare_result_lines(self, aggregated_requirements):
        self.ensure_one()
        if not aggregated_requirements:
            return [], {
                "can_produce_qty": 0.0,
                "bottleneck_product_id": False,
                "bottleneck_qty": 0.0,
                "summary": _("No BoM components found."),
            }

        products = self.env["product.product"].browse(
            list(aggregated_requirements.keys())
        )
        available_qty_by_product, location_note_by_product = (
            self._get_available_quantities(
                products,
                self.location_ids,
            )
        )
        prepared_lines = []

        for sequence, requirement in enumerate(
            sorted(
                aggregated_requirements.values(),
                key=lambda data: data["product"].display_name,
            ),
            start=1,
        ):
            product = requirement["product"]
            required_qty_per_unit = requirement["required_qty"]
            if required_qty_per_unit <= 0 and not self.include_zero_required:
                continue

            available_qty = available_qty_by_product.get(product.id, 0.0)
            required_qty_for_target = required_qty_per_unit * self.target_qty
            can_produce_qty = (
                max(math.floor(available_qty / required_qty_per_unit), 0)
                if required_qty_per_unit > 0
                else 0
            )
            shortage_qty = max(required_qty_for_target - available_qty, 0.0)
            if available_qty <= 0:
                availability_state = "zero"
            elif shortage_qty > 0:
                availability_state = "shortage"
            else:
                availability_state = "ok"

            route_notes = sorted(requirement["route_notes"])
            if len(route_notes) > 1:
                route_note = _("Multiple BoM paths")
            else:
                route_note = route_notes[0] if route_notes else False

            prepared_lines.append(
                {
                    "sequence": sequence * 10,
                    "level": requirement["level"],
                    "parent_bom_id": requirement["parent_bom"].id
                    if requirement["parent_bom"]
                    else False,
                    "product_id": product.id,
                    "product_uom_id": product.uom_id.id,
                    "required_qty_per_unit": required_qty_per_unit,
                    "required_qty_for_target": required_qty_for_target,
                    "available_qty": available_qty,
                    "can_produce_qty": can_produce_qty,
                    "shortage_qty": shortage_qty,
                    "availability_state": availability_state,
                    "available_location_note": location_note_by_product.get(
                        product.id, ""
                    ),
                    "route_note": route_note,
                }
            )

        if not prepared_lines:
            return [], {
                "can_produce_qty": 0.0,
                "bottleneck_product_id": False,
                "bottleneck_qty": 0.0,
                "summary": _("No BoM components found."),
            }

        overall_can_produce = min(line["can_produce_qty"] for line in prepared_lines)
        for line in prepared_lines:
            line["is_bottleneck"] = line["can_produce_qty"] == overall_can_produce

        bottleneck_line = sorted(
            (line for line in prepared_lines if line["is_bottleneck"]),
            key=lambda line: (
                line["can_produce_qty"],
                -line["shortage_qty"],
                self.env["product.product"].browse(line["product_id"]).display_name,
            ),
        )[0]
        bottleneck_product = self.env["product.product"].browse(
            bottleneck_line["product_id"]
        )
        shortage_count = sum(1 for line in prepared_lines if line["shortage_qty"] > 0)
        zero_count = sum(
            1 for line in prepared_lines if line["availability_state"] == "zero"
        )
        summary = _(
            "Can produce now: %(qty)s unit(s).\n"
            "Availability calculated across %(location_count)s selected location(s).\n"
            "Main bottleneck: %(product)s.\n"
            "%(shortage_count)s component(s) short for target, %(zero_count)s with zero availability."
        ) % {
            "qty": overall_can_produce,
            "location_count": len(self.location_ids),
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


class MrpBomAvailabilityWizardLine(models.TransientModel):
    _name = "mrp.bom.availability.wizard.line"
    _description = "BoM Availability Planner Line"
    _order = "sequence, id"

    wizard_id = fields.Many2one(
        "mrp.bom.availability.wizard",
        required=True,
        ondelete="cascade",
    )
    sequence = fields.Integer(default=10)
    level = fields.Integer(
        string="BoM Level",
    )
    parent_bom_id = fields.Many2one(
        "mrp.bom",
        string="Parent BoM",
    )
    product_id = fields.Many2one(
        "product.product",
        string="Component",
        required=True,
    )
    product_uom_id = fields.Many2one(
        "uom.uom",
        string="UoM",
        required=True,
    )
    required_qty_per_unit = fields.Float(
        string="Required per Finished Unit",
        digits="Product Unit of Measure",
    )
    required_qty_for_target = fields.Float(
        string="Required for Target",
        digits="Product Unit of Measure",
    )
    available_qty = fields.Float(
        string="Available Qty",
        digits="Product Unit of Measure",
    )
    can_produce_qty = fields.Float(
        string="Can Produce",
        digits="Product Unit of Measure",
    )
    shortage_qty = fields.Float(
        string="Shortage for Target",
        digits="Product Unit of Measure",
    )
    is_bottleneck = fields.Boolean(
        string="Bottleneck",
    )
    availability_state = fields.Selection(
        selection=[
            ("ok", "Enough"),
            ("shortage", "Shortage"),
            ("zero", "Zero Available"),
        ],
        string="State",
    )
    available_location_note = fields.Char(
        string="Available Locations",
        readonly=True,
        help="Human-readable summary of where available stock was found.",
    )
    route_note = fields.Char(
        string="BoM Path / Note",
    )
