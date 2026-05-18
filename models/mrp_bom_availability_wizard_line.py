from odoo import fields, models


LINE_TYPE_COMPONENT = "component"
LINE_TYPE_SUBASSEMBLY = "subassembly"

STATE_OK = "ok"
STATE_SHORTAGE = "shortage"
STATE_ZERO = "zero"


# Lines are transient report rows only; they store the last computation result
# shown inside the planner wizard and never drive stock moves or reservations.
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
    line_type = fields.Selection(
        selection=[
            (LINE_TYPE_COMPONENT, "Component"),
            (LINE_TYPE_SUBASSEMBLY, "Subassembly"),
        ],
        string="Type",
        default=LINE_TYPE_COMPONENT,
        readonly=True,
    )
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
    component_label = fields.Char(
        string="Component",
        readonly=True,
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
        string="Is Bottleneck",
    )
    bottleneck_note = fields.Char(
        string="Bottleneck",
        readonly=True,
    )
    availability_state = fields.Selection(
        selection=[
            (STATE_OK, "Enough"),
            (STATE_SHORTAGE, "Shortage"),
            (STATE_ZERO, "Zero Available"),
        ],
        string="State",
    )
    available_location_note = fields.Char(
        string="Available Locations",
        readonly=True,
        help="Human-readable summary of where available stock was found.",
    )
    route_note = fields.Text(
        string="BoM Path",
    )
