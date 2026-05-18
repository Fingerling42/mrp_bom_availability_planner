/** @odoo-module **/

import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { Many2XAutocomplete } from "@web/views/fields/relational_utils";
import { Component, onWillStart, useState } from "@odoo/owl";

export class BomAvailabilityOverview extends Component {
  setup() {
    this.orm = useService("orm");
    this.notification = useService("notification");
    this.activeActions = {
      create: false,
      createEdit: false,
      write: false,
    };
    this.locationActiveActions = {
      ...this.activeActions,
      link: true,
      unlink: true,
    };
    this.state = useState({
      isLoading: true,
      isComputing: false,
      setupData: {},
      product: null,
      bom: null,
      locations: [],
      availabilityBasis: "on_hand",
      result: null,
    });

    onWillStart(async () => {
      await this.loadSetupData();
    });
  }

  async loadSetupData() {
    this.state.isLoading = true;
    this.state.setupData = await this.orm.call(
      "mrp.bom.availability.engine",
      "get_client_action_data",
    );
    this.state.locations = (
      this.state.setupData.default_location_ids || []
    ).map((id, index) => ({
      id,
      display_name: this.state.setupData.default_location_names[index],
    }));
    this.state.isLoading = false;
  }

  get title() {
    return this.state.setupData.title || _t("BoM Availability Planner");
  }

  get loadingLabel() {
    return _t("Loading...");
  }

  get productFieldLabel() {
    return _t("Product Variant");
  }

  get bomFieldLabel() {
    return _t("Bill of Materials");
  }

  get locationsFieldLabel() {
    return _t("Locations");
  }

  get availabilityBasisLabel() {
    return _t("Availability Basis");
  }

  get computeLabel() {
    return _t("Compute Availability");
  }

  get clearLabel() {
    return _t("Clear");
  }

  get canProduceLabel() {
    return _t("Can Produce Now");
  }

  get mainBottleneckLabel() {
    return _t("Main Bottleneck");
  }

  get bottleneckAvailabilityLabel() {
    return _t("Bottleneck Availability");
  }

  get productValue() {
    return this.state.product?.display_name || "";
  }

  get bomValue() {
    return this.state.bom?.display_name || "";
  }

  getProductDomain() {
    return [["type", "in", ["product", "consu"]]];
  }

  getBomDomain() {
    const domain = [
      ["type", "in", ["normal", "phantom"]],
      "|",
      ["company_id", "=", false],
      ["company_id", "=", this.state.setupData.company_id],
    ];
    if (this.state.product) {
      domain.push(
        "|",
        ["product_id", "=", this.state.product.id],
        "&",
        ["product_id", "=", false],
        ["product_tmpl_id.product_variant_ids", "=", this.state.product.id],
      );
    }
    return domain;
  }

  getLocationDomain() {
    return [
      ["usage", "=", "internal"],
      "|",
      ["company_id", "=", false],
      ["company_id", "=", this.state.setupData.company_id],
    ];
  }

  async updateProduct(records) {
    this.state.product = this.firstRecord(records);
    this.state.result = null;
    if (!this.state.product) {
      this.state.bom = null;
      return;
    }
    const bom = await this.orm.call(
      "mrp.bom.availability.engine",
      "get_matching_bom_data",
      [this.state.product.id],
    );
    this.state.bom = bom || null;
  }

  updateBom(records) {
    this.state.bom = this.firstRecord(records);
    this.state.result = null;
  }

  updateLocations(records) {
    const selectedLocations = Array.isArray(records) ? records : [];
    const locationById = new Map(
      this.state.locations.map((location) => [location.id, location]),
    );
    for (const record of selectedLocations) {
      locationById.set(record.id, this.normalizeRecord(record));
    }
    this.state.locations = [...locationById.values()];
    this.state.result = null;
  }

  removeLocation(locationId) {
    this.state.locations = this.state.locations.filter(
      (location) => location.id !== locationId,
    );
    this.state.result = null;
  }

  onChangeAvailabilityBasis(ev) {
    this.state.availabilityBasis = ev.target.value;
    this.state.result = null;
  }

  async computeAvailability() {
    if (
      !this.state.product ||
      !this.state.bom ||
      !this.state.locations.length
    ) {
      this.notification.add(
        _t("Select product, BoM, and at least one location."),
        {
          type: "warning",
        },
      );
      return;
    }
    this.state.isComputing = true;
    this.state.result = await this.orm.call(
      "mrp.bom.availability.engine",
      "get_availability_data",
      [
        this.state.product.id,
        this.state.bom.id,
        this.state.locations.map((location) => location.id),
        this.state.availabilityBasis,
      ],
    );
    this.state.isComputing = false;
  }

  clearResult() {
    this.state.result = null;
  }

  firstRecord(records) {
    return records && records.length ? this.normalizeRecord(records[0]) : null;
  }

  normalizeRecord(record) {
    return {
      id: record.id,
      display_name: record.display_name || record.name,
    };
  }
}

BomAvailabilityOverview.template =
  "mrp_bom_availability_planner.BomAvailabilityOverview";
BomAvailabilityOverview.components = { Many2XAutocomplete };

registry
  .category("actions")
  .add("mrp_bom_availability_planner.overview", BomAvailabilityOverview);
