# MRP BoM Availability Planner

Odoo 17 CE addon for calculating how many finished products can be produced
from current component stock for a selected product variant and bill of
materials.

## Features

- Calculate producible finished quantity from a selected BoM.
- Show the main bottleneck component.
- Show component shortages for a target quantity.
- Show components that are available with enough stock.
- Read stock from selected internal stock locations.
- Support On Hand and Available / Unreserved availability basis.
- Explode multi-level BoMs.
- Show exploded subassembly context lines and full BoM paths.
- Filter variant-specific BoM lines using Apply on Variants.
- Aggregate repeated components in the exploded BoM.
- Normalize component quantities to the product default unit of measure.

## Dependencies

- mrp
- stock

## Installation

Add the module to an Odoo addons path, update the Apps list, and install
MRP BoM Availability Planner.

## Usage

Use Manufacturing -> Reporting -> BoM Availability Planner to open the
availability wizard.

Select a product variant, bill of materials, stock locations, target quantity,
and availability basis. Use Compute Availability to calculate component
requirements, available quantities, shortages, and bottlenecks.

Enable Explode Subassemblies to calculate raw component requirements from
multi-level BoMs. In the MVP, existing subassembly stock is not consumed first;
the planner fully explodes subassembly BoMs.

Result lines show a human-readable summary of the locations where available
stock was found.

The module only reads current stock.quant quantities. It does not consider
forecasted receipts, planned purchases, reservations, lots, serial numbers,
routings, or operation capacity.

The module does not create RFQs, manufacturing orders, procurements, stock
moves, reservations, or inventory adjustments.

## License

Apache-2.0
