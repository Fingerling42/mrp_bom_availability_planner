from odoo import api, fields, models, _


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
    company_id = fields.Many2one(
        "res.company",
        default=lambda self: self.env.company,
        required=True,
        readonly=True,
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
    availability_overview_data = fields.Json(
        string="Availability Overview Data",
        readonly=True,
    )

    @api.model
    def _default_location_ids(self):
        return self.env["stock.location"].search(
            [
                ("usage", "=", "internal"),
                ("company_id", "in", [False, self.env.company.id]),
            ],
            limit=1,
        )

    @api.model
    def action_open_planner(self):
        wizard = self.create({})
        return wizard._reopen_wizard()

    @api.onchange("product_id")
    def _onchange_product_id(self):
        engine = self.env["mrp.bom.availability.engine"]
        for wizard in self:
            wizard.update(wizard._reset_result_values())
            wizard.bom_id = (
                engine.get_matching_bom(wizard.product_id)
                if wizard.product_id
                else False
            )

    def action_clear_result(self):
        self.ensure_one()
        self.write(self._reset_result_values())
        return self._reopen_wizard()

    def action_compute_availability(self):
        self.ensure_one()

        # Keep the wizard as a UI coordinator; the engine owns all explosion and
        # stock availability rules.
        summary_values = self.env["mrp.bom.availability.engine"].compute(self)
        self.write(summary_values)
        return self._reopen_wizard()

    def _reset_result_values(self):
        return {
            "can_produce_qty": 0.0,
            "bottleneck_product_id": False,
            "bottleneck_qty": 0.0,
            "summary": False,
            "availability_overview_data": False,
        }

    def _empty_result_values(self, summary):
        values = self._reset_result_values()
        values["summary"] = summary
        return values

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
