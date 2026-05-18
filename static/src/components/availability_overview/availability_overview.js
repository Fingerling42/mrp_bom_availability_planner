/** @odoo-module **/

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { Component, onWillStart, useState } from "@odoo/owl";

export class BomAvailabilityOverview extends Component {
    setup() {
        this.orm = useService("orm");
        this.state = useState({
            isLoading: true,
            setupData: {},
        });

        onWillStart(async () => {
            await this.loadSetupData();
        });
    }

    async loadSetupData() {
        this.state.isLoading = true;
        this.state.setupData = await this.orm.call(
            "mrp.bom.availability.engine",
            "get_client_action_data"
        );
        this.state.isLoading = false;
    }

    get title() {
        return this.state.setupData.title || _t("BoM Availability Planner");
    }

    get loadingLabel() {
        return _t("Loading...");
    }
}

BomAvailabilityOverview.template =
    "mrp_bom_availability_planner.BomAvailabilityOverview";

registry
    .category("actions")
    .add("mrp_bom_availability_planner.overview", BomAvailabilityOverview);
