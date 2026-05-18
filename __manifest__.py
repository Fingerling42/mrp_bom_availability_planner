{
    "name": "MRP BoM Availability Planner",
    "summary": "Calculate how many finished products can be produced from current BoM component stock.",
    "version": "17.0.1.0.0",
    "category": "Manufacturing/Manufacturing",
    "author": "Pinout LTD",
    "license": "Other OSI approved licence",
    "depends": ["mrp", "stock", "web"],
    "data": [
        "security/ir.model.access.csv",
        "data/cleanup_obsolete_records.xml",
        "views/mrp_bom_availability_wizard_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "mrp_bom_availability_planner/static/src/components/availability_overview/availability_overview.js",
            "mrp_bom_availability_planner/static/src/components/availability_overview/availability_overview.xml",
            "mrp_bom_availability_planner/static/src/components/availability_overview/availability_overview.scss",
        ],
    },
    "installable": True,
    "application": False,
}
