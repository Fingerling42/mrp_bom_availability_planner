/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { formatFloat } from "@web/views/fields/formatters";
import { Component, useState } from "@odoo/owl";

export class BomAvailabilityOverviewLine extends Component {
  setup() {
    this.state = useState({
      isFolded: false,
    });
  }

  get line() {
    return this.props.line;
  }

  get hasChildren() {
    return Boolean(this.line.children?.length);
  }

  get indentStyle() {
    return `padding-left: ${this.line.level * 24}px`;
  }

  get rowClass() {
    return `o_bap_tree_row o_bap_tree_row_${this.line.status}`;
  }

  get statusClass() {
    return `o_bap_tree_status o_bap_tree_status_${this.line.status}`;
  }

  get caretClass() {
    return `fa fa-fw fa-caret-${this.isFolded ? "right" : "down"}`;
  }

  get toggleTitle() {
    return this.isFolded ? _t("Unfold") : _t("Fold");
  }

  get isFolded() {
    return this.state.isFolded;
  }

  onToggle() {
    this.state.isFolded = !this.state.isFolded;
  }

  formatQty(value, precision = 4) {
    return value === false || value === null || value === undefined
      ? "-"
      : formatFloat(value, { digits: [false, precision] });
  }

  formatInteger(value) {
    return this.formatQty(value, 0);
  }
}

BomAvailabilityOverviewLine.template =
  "mrp_bom_availability_planner.BomAvailabilityOverviewLine";
BomAvailabilityOverviewLine.components = { BomAvailabilityOverviewLine };
BomAvailabilityOverviewLine.props = {
  line: Object,
};
