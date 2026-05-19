# MRP BoM Availability Planner

Odoo 17 CE addon for calculating how many finished products can be produced
from current component stock for a selected product variant and bill of
materials.

## Features

- Calculate producible finished quantity from a selected BoM.
- Show the main bottleneck component.
- Prepare structured component availability data for a BoM Overview-like UI.
- Read stock from selected internal stock locations.
- Support On Hand and Available / Unreserved availability basis.
- Explode multi-level BoMs.
- Keep subassembly context without counting subassembly stock as a bottleneck.
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
availability overview.

Select a product variant, bill of materials, stock locations, and availability
basis. Use Compute Availability to calculate component requirements, available
quantities, producible quantity, and bottlenecks.

The backend prepares multi-level BoM availability data for an overview UI.
Availability and bottleneck metrics are calculated for leaf components; existing
subassembly stock is not consumed first.

The module only reads current stock.quant quantities. It does not consider
forecasted receipts, planned purchases, reservations, lots, serial numbers,
routings, or operation capacity.

The module does not create RFQs, manufacturing orders, procurements, stock
moves, reservations, or inventory adjustments.

## License

Apache-2.0
