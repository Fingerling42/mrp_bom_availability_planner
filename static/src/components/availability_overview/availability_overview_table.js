/** @odoo-module **/

import { Component } from "@odoo/owl";
import { BomAvailabilityOverviewLine } from "./availability_overview_line";

export class BomAvailabilityOverviewTable extends Component {
  get columns() {
    return this.props.data?.columns || [];
  }

  get lines() {
    return this.props.data?.lines || [];
  }
}

BomAvailabilityOverviewTable.template =
  "mrp_bom_availability_planner.BomAvailabilityOverviewTable";
BomAvailabilityOverviewTable.components = { BomAvailabilityOverviewLine };
BomAvailabilityOverviewTable.props = {
  data: Object,
};
