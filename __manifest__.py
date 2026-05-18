{
    "name": "MRP BoM Availability Planner",
    "summary": "Calculate how many finished products can be produced from current BoM component stock.",
    "version": "17.0.1.0.0",
    "category": "Manufacturing/Manufacturing",
    "author": "Pinout LTD",
    "license": "Other OSI approved licence",
    "depends": ["mrp", "stock"],
    "data": [
        "security/ir.model.access.csv",
        "views/mrp_bom_availability_wizard_views.xml",
    ],
    "installable": True,
    "application": False,
}
