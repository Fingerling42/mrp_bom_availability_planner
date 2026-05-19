/** @odoo-module **/

import { Component, useState } from "@odoo/owl";
import { BomAvailabilityOverviewLine } from "./availability_overview_line";

export class BomAvailabilityOverviewTable extends Component {
  setup() {
    this.state = useState({
      folded: {},
    });
  }

  get columns() {
    return this.props.data?.columns || [];
  }

  get lines() {
    return this.props.data?.lines || [];
  }

  isFolded(line) {
    return Boolean(this.state.folded[line.line_id]);
  }

  toggleLine(line) {
    this.state.folded[line.line_id] = !this.isFolded(line);
  }
}

BomAvailabilityOverviewTable.template =
  "mrp_bom_availability_planner.BomAvailabilityOverviewTable";
BomAvailabilityOverviewTable.components = { BomAvailabilityOverviewLine };
BomAvailabilityOverviewTable.props = {
  data: Object,
};
