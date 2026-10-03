import { esc, num, DOCS_URL } from "./shared.js";
import { dateTimeFormat } from "./time.js";
import { replaceCardHTML } from "./dom.js";
import { applianceText } from "./appliances-translations.js";
import { localize } from "./translations.js";

export const APPLIANCES_CARD_TYPE = "battery-manager-appliances-card";
const EDITOR_TYPE = `${APPLIANCES_CARD_TYPE}-editor`;
const WS_TYPE = "battery_manager/appliances";
// Bound rendering of diagnostic collections without creating any inferred data.
const MAX_ITEMS = 100;
const list = (value) =>
  Array.isArray(value)
    ? value
        .filter((item) => item && typeof item === "object")
        .slice(0, MAX_ITEMS)
    : [];
const STYLE = `<style>
:host{display:block;color:var(--primary-text-color)}ha-card{overflow:hidden}.wrap{padding:16px}h2{font-size:20px;margin:0 0 16px}h3{font-size:17px;margin:0}h4{font-size:14px;margin:12px 0 6px}.appliance{border-top:1px solid var(--divider-color,#ddd);padding:16px 0}.appliance:first-of-type{border-top:0}.heading{display:flex;align-items:baseline;justify-content:space-between;gap:12px}.status{font-size:13px;border:1px solid var(--divider-color,#ddd);border-radius:12px;padding:2px 9px}.muted,.source{color:var(--secondary-text-color);font-size:13px}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(155px,1fr));gap:12px;margin:14px 0}.metric{display:flex;flex-direction:column;gap:4px}.metric span{font-size:13px;color:var(--secondary-text-color)}.metric strong{font-size:16px;font-weight:500}details{margin:8px 0}summary{cursor:pointer;padding:9px 0;font-weight:500}button,select,input{font:inherit;color:inherit}button{cursor:pointer;border:1px solid var(--divider-color,#aaa);border-radius:5px;padding:6px 10px;background:var(--card-background-color,#fff)}.entity{border:0;background:none;padding:5px 0;color:var(--primary-color,#1976d2);text-align:left;overflow-wrap:anywhere}button:focus-visible,summary:focus-visible,select:focus-visible,input:focus-visible{outline:2px solid var(--primary-color,#1976d2);outline-offset:3px}.table-scroll{overflow:auto;max-height:360px}table{border-collapse:collapse;width:100%;font-size:13px}th,td{text-align:left;padding:8px;vertical-align:top;border-bottom:1px solid var(--divider-color,#ddd)}th{font-weight:600}td{min-width:90px}p{line-height:1.45;overflow-wrap:anywhere}.warning{color:var(--error-color,#b3261e)}.profile{padding:4px 0 12px}.editor{display:grid;gap:16px;padding:12px}.editor label{display:grid;gap:6px}.editor input,.editor select{width:100%;box-sizing:border-box;padding:8px;background:var(--card-background-color,#fff);border:1px solid var(--divider-color,#888);border-radius:4px}.editor select[multiple]{min-height:120px}.editor small{color:var(--secondary-text-color)}
</style>`;

function configValue(config) {
  if (
    !config ||
    typeof config !== "object" ||
    (config.entry_id != null && typeof config.entry_id !== "string") ||
    (config.title != null && typeof config.title !== "string") ||
    (config.appliance_ids != null &&
      (!Array.isArray(config.appliance_ids) ||
        config.appliance_ids.some((id) => typeof id !== "string")))
  ) {
    throw new Error(`${APPLIANCES_CARD_TYPE}: invalid configuration`);
  }
  const result = { ...config, entry_id: config.entry_id || "" };
  if (config.appliance_ids?.length)
    result.appliance_ids = [...new Set(config.appliance_ids)];
  else delete result.appliance_ids;
  return result;
}

export class BatteryManagerAppliancesCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._connected = false;
    this._generation = 0;
    this._inFlight = false;
    this._pending = false;
    this._scheduled = false;
    this._payload = null;
    this._error = false;
  }
  connectedCallback() {
    this._connected = true;
    this._refresh();
  }
  disconnectedCallback() {
    this._connected = false;
    this._generation++;
    this._pending = false;
  }
  setConfig(config) {
    this._config = configValue(config);
    this._generation++;
    this._payload = null;
    this._error = false;
    this._signal = undefined;
    this._render();
    this._refresh();
  }
  set hass(hass) {
    const presentationChanged =
      hass.language !== this._hass?.language ||
      hass.config?.time_zone !== this._hass?.config?.time_zone;
    this._hass = hass;
    const ids = this._config?.appliance_ids;
    const signal = JSON.stringify(
      Object.entries(hass.states || {})
        .filter(
          ([, state]) =>
            state?.attributes?.entry_id === this._config?.entry_id &&
            state.attributes.appliance_id &&
            (!ids || ids.includes(state.attributes.appliance_id)),
        )
        .map(([id, state]) => [id, state.attributes.revision, state.state])
        .sort(([a], [b]) => a.localeCompare(b)),
    );
    if (signal !== this._signal) {
      this._signal = signal;
      // A backend reload can repeat an old revision signal (A → B → A).
      // Invalidate by publication identity, rather than numerical revision.
      this._generation++;
      this._refresh();
    }
    if (presentationChanged) this._render();
  }
  getCardSize() {
    return 4;
  }
  getGridOptions() {
    return { rows: 4, columns: 12, min_rows: 2, min_columns: 6 };
  }
  static getConfigElement() {
    return document.createElement(EDITOR_TYPE);
  }
  static getStubConfig(hass) {
    const state = Object.values(hass?.states || {}).find(
      (item) => item.attributes?.appliance_id && item.attributes?.entry_id,
    );
    return { entry_id: state?.attributes.entry_id || "" };
  }
  _refresh() {
    if (!this._connected || !this._hass?.callWS || !this._config?.entry_id)
      return;
    if (this._inFlight) {
      this._pending = true;
      return;
    }
    if (this._scheduled) return;
    this._scheduled = true;
    // HA may publish several compact status entities in one turn. One request
    // captures that publication; an in-flight request gets at most one successor.
    Promise.resolve().then(() => {
      this._scheduled = false;
      if (this._connected && this._config?.entry_id) return this._fetch();
    });
  }
  async _fetch() {
    this._inFlight = true;
    this._pending = false;
    const generation = this._generation;
    const signal = this._signal;
    const request = { type: WS_TYPE, entry_id: this._config.entry_id };
    if (this._config.appliance_ids)
      request.appliance_ids = this._config.appliance_ids;
    try {
      const value = await this._hass.callWS(request);
      if (
        !this._connected ||
        generation !== this._generation ||
        signal !== this._signal
      )
        return;
      if (
        value?.entry_id !== request.entry_id ||
        !Array.isArray(value.appliances) ||
        num(value.revision) === undefined
      )
        throw new Error("Invalid appliance response");
      // Revisions restart when an integration entry reloads. Request identity
      // and the captured sensor signal, rather than numerical ordering across
      // process lifetimes, decide whether a response still belongs here.
      this._payload = value;
      this._error = false;
      this._render();
    } catch (_err) {
      if (
        this._connected &&
        generation === this._generation &&
        signal === this._signal
      ) {
        this._error = true;
        this._render();
      }
    } finally {
      this._inFlight = false;
      if (this._pending) this._refresh();
    }
  }
  _t(key) {
    return applianceText(this._hass, key);
  }
  _number(value, unit = "", digits = 1) {
    const number = num(value);
    return number === undefined
      ? "—"
      : `${new Intl.NumberFormat(this._hass?.language || "en", { maximumFractionDigits: digits }).format(number)}${unit ? ` ${unit}` : ""}`;
  }
  _time(value) {
    if (typeof value !== "string" || !value) return "—";
    const timestamp = Date.parse(value);
    return Number.isFinite(timestamp)
      ? dateTimeFormat(this._hass, {
          dateStyle: "short",
          timeStyle: "short",
        }).format(timestamp)
      : "—";
  }
  _metric(label, value, source) {
    return `<div class="metric"><span>${esc(this._t(label))}</span><strong>${esc(value)}</strong>${source ? `<small class="source">${esc(this._t(source))}</small>` : ""}</div>`;
  }
  _reasons(value) {
    return Array.isArray(value) && value.length
      ? `<ul>${value
          .slice(0, MAX_ITEMS)
          .map((reason) => `<li>${esc(this._t(reason))}</li>`)
          .join("")}</ul>`
      : "";
  }
  _details(id, label, body) {
    return `<details data-view-key="${esc(id)}"><summary>${esc(this._t(label))}</summary>${body}</details>`;
  }
  _profile(profile, device = false) {
    const count = num(profile.count) ?? 0;
    const statisticalLabel =
      count === 1 ? "single" : count > 1 ? "median" : "no_data";
    const range = (low, high, unit, factor = 1) =>
      num(low) !== undefined && num(high) !== undefined
        ? `${this._number(num(low) * factor, unit)} – ${this._number(num(high) * factor, unit)}`
        : "—";
    return `<div class="profile"><h4>${esc(device ? this._t("device_profile") : profile.program || this._t("unassigned"))}${profile.selected ? ` · ${esc(this._t("selected_profile"))}` : ""}</h4>
      <p class="muted">${esc(this._t(statisticalLabel))} · ${esc(this._t("samples"))}: ${esc(this._number(count, "", 0))}</p>
      <div class="metrics">${this._metric("energy", this._number(profile.energy_wh, "Wh"))}${device ? "" : this._metric("duration", num(profile.duration_h) === undefined ? "—" : this._number(num(profile.duration_h) * 60, "min"))}</div>
      <p class="muted">${esc(this._t("observed_range"))} · ${esc(this._t("energy"))}: ${esc(range(profile.energy_min_wh, profile.energy_max_wh, "Wh"))}${device ? "" : ` · ${esc(this._t("duration"))}: ${esc(range(profile.duration_min_h, profile.duration_max_h, "min", 60))}`}</p>
      <p class="muted">${esc(this._t("last_learned"))}: ${esc(this._time(profile.last_learned_at))}</p></div>`;
  }
  _history(appliance) {
    const rows = list(appliance.learning?.history);
    if (!rows.length)
      return `<p class="muted">${esc(this._t("no_history"))}</p>`;
    return `<div class="table-scroll" data-scroll-key="history-${esc(appliance.id)}"><table><thead><tr>${["program", "started_at", "ended_at", "duration", "energy", "learning"].map((key) => `<th scope="col">${esc(this._t(key))}</th>`).join("")}</tr></thead><tbody>${rows.map((row) => `<tr><td>${esc(row.program || this._t("unassigned"))}</td><td>${esc(this._time(row.started_at))}</td><td>${esc(this._time(row.ended_at))}</td><td>${esc(num(row.duration_h) === undefined ? "—" : this._number(num(row.duration_h) * 60, "min"))}</td><td>${esc(this._number(row.energy_wh, "Wh"))}<br><span class="muted">${esc(this._t(row.measurement_source || "none"))}</span></td><td>${esc(this._t(row.accepted ? "accepted" : "rejected"))}<br>${esc(this._t(row.complete ? "complete" : "incomplete"))}${this._reasons(row.reasons)}${this._reasons(row.warnings)}</td></tr>`).join("")}</tbody></table></div>`;
  }
  _sources(appliance) {
    const rows = list(appliance.sources);
    if (!rows.length)
      return `<p class="muted">${esc(this._t("no_sources"))}</p>`;
    return `<div class="table-scroll" data-scroll-key="sources-${esc(appliance.id)}"><table><thead><tr>${["source", "available", "last_reported"].map((key) => `<th scope="col">${esc(this._t(key))}</th>`).join("")}</tr></thead><tbody>${rows
      .map((row) => {
        const configured =
          typeof row.entity_id === "string" && row.entity_id.length > 0;
        const entity = configured
          ? `<button class="entity" data-entity-id="${esc(row.entity_id)}" data-focus-key="entity-${esc(appliance.id)}-${esc(row.kind)}" title="${esc(this._t("open_entity"))}">${esc(row.entity_id)}</button>`
          : esc(this._t("not_configured"));
        const status = !configured
          ? this._t("not_configured")
          : row.status
            ? localize(this._hass, `source_status_${row.status}`)
            : this._t(row.available ? "available" : "unavailable");
        const value =
          row.value != null
            ? `${typeof row.value === "number" ? this._number(row.value, row.unit || "") : row.value}`
            : (row.state ?? "—");
        return `<tr><td>${esc(this._t(row.kind || "unknown"))}<br>${entity}</td><td>${esc(status)}<br>${esc(value)}</td><td>${esc(this._time(row.reported_at ?? row.last_reported))}</td></tr>`;
      })
      .join("")}</tbody></table></div>`;
  }
  _appliance(appliance) {
    const observation = appliance.observation || {};
    const planning = appliance.planning || {};
    const learning = appliance.learning || {};
    const recommendation = appliance.recommendation || {};
    const profiles = list(learning.profiles);
    const status = [
      "idle",
      "running",
      "paused",
      "finished",
      "error",
      "unknown",
    ].includes(appliance.status)
      ? appliance.status
      : "unknown";
    const profileBody = `${learning.device_profile && typeof learning.device_profile === "object" ? this._profile(learning.device_profile, true) : ""}${profiles.map((profile) => this._profile(profile)).join("")}`;
    return `<section class="appliance" data-view-key="appliance-${esc(appliance.id)}"><div class="heading"><h3>${esc(appliance.name || appliance.id)}</h3><span class="status">${esc(this._t(status === "error" ? "device_error" : status))}</span></div>
      <p>${esc(this._t(appliance.program_source === "selected" ? "selected" : appliance.program_source === "active" ? "active" : "program"))}: ${esc(appliance.program || "—")}</p>
      <div class="metrics">${this._metric("power", this._number(observation.power_w, "W"))}${this._metric("remaining", this._number(observation.remaining_minutes, "min"), observation.remaining_source)}${this._metric("expected_end", this._time(observation.expected_end))}${this._metric("cycle_energy", this._number(observation.cycle_energy_wh, "Wh"), observation.energy_source)}</div>
      ${observation.started_at ? `<p class="muted">${esc(this._t("started_at"))}: ${esc(this._time(observation.started_at))} · ${esc(this._t(observation.complete ? "complete" : "incomplete"))}</p>` : ""}
      <p>${esc(this._t(recommendation.allowed === true ? "recommendation_yes" : recommendation.allowed === false ? "recommendation_no" : "recommendation_unknown"))}</p>${this._reasons(recommendation.reasons)}
      ${this._details(`planning-${appliance.id}`, "planning", `<div class="metrics">${this._metric("planned_energy", this._number(planning.energy_wh, "Wh"), planning.energy_source)}${this._metric("planned_duration", this._number(planning.duration_minutes, "min"), planning.duration_source)}</div>`)}
      ${this._details(`profiles-${appliance.id}`, "profiles", `<p class="muted">${esc(this._t("learning"))}: ${esc(this._t(learning.status || "unknown"))} · ${esc(this._t("samples"))}: ${esc(this._number(learning.sample_count, "", 0))} · ${esc(this._t("last_learned"))}: ${esc(this._time(learning.last_learned_at))}</p>${this._reasons(learning.reasons)}${this._reasons(learning.warnings)}${profileBody || `<p class="muted">${esc(this._t("no_profiles"))}</p>`}<p class="muted">${esc(this._t("profile_note"))}</p>`)}
      ${this._details(`history-${appliance.id}`, "history", this._history(appliance))}
      ${this._details(`sources-${appliance.id}`, "sources", this._sources(appliance))}</section>`;
  }
  _render() {
    if (!this._config) return;
    const appliances = list(this._payload?.appliances).filter(
      (item) =>
        typeof item.id === "string" &&
        (!this._config.appliance_ids ||
          this._config.appliance_ids.includes(item.id)),
    );
    let body = appliances
      .map((appliance) => this._appliance(appliance))
      .join("");
    if (!body)
      body = `<p class="muted">${esc(this._t(!this._config.entry_id ? "choose" : this._payload ? "empty" : this._error ? "no_data" : "loading"))}</p>`;
    replaceCardHTML(
      this,
      `${STYLE}<ha-card><div class="wrap"><h2>${esc(this._config.title ?? this._t("title"))}</h2>${this._error ? `<p class="warning" role="status">${esc(this._t("error"))}${this._payload ? ` ${esc(this._t("stale"))}` : ""}</p><button data-focus-key="retry" id="retry">${esc(this._t("retry"))}</button>` : ""}${body}</div></ha-card>`,
    );
    this.shadowRoot
      .getElementById("retry")
      ?.addEventListener("click", () => this._refresh());
    for (const button of this.shadowRoot.querySelectorAll("[data-entity-id]"))
      button.addEventListener("click", () =>
        this.dispatchEvent(
          new CustomEvent("hass-more-info", {
            detail: { entityId: button.dataset.entityId },
            bubbles: true,
            composed: true,
          }),
        ),
      );
  }
}

export class BatteryManagerAppliancesEditor extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._generation = 0;
    this._entries = [];
    this._devices = [];
    this._connected = false;
  }
  connectedCallback() {
    this._connected = true;
    this._load();
  }
  disconnectedCallback() {
    this._connected = false;
    this._generation++;
    this._loadingKey = undefined;
    this._loadedKey = undefined;
  }
  setConfig(config) {
    this._config = configValue(config);
    this._generation++;
    this._loadingKey = undefined;
    this._loadedKey = undefined;
    this._render();
    this._load();
  }
  set hass(hass) {
    const changed = this._hass?.language !== hass.language;
    this._hass = hass;
    if (changed) this._render();
    this._load();
  }
  async _load() {
    if (!this._connected || !this._config || !this._hass?.callWS) return;
    const key = this._config.entry_id;
    if (key === this._loadedKey || key === this._loadingKey) return;
    const generation = ++this._generation;
    this._loadingKey = key;
    try {
      const [entries, devices] = await Promise.all([
        this._hass.callWS({ type: WS_TYPE }),
        key
          ? this._hass.callWS({ type: WS_TYPE, entry_id: key })
          : Promise.resolve({ appliances: [] }),
      ]);
      if (!this._connected || generation !== this._generation) return;
      this._entries = list(entries?.entries);
      this._devices = list(devices?.appliances);
      this._loadedKey = key;
      this._error = false;
    } catch (_err) {
      if (!this._connected || generation !== this._generation) return;
      this._error = true;
    } finally {
      if (this._connected && generation === this._generation) {
        this._loadingKey = undefined;
        this._render();
      }
    }
  }
  _emit() {
    this.dispatchEvent(
      new CustomEvent("config-changed", {
        detail: {
          config: { type: `custom:${APPLIANCES_CARD_TYPE}`, ...this._config },
        },
        bubbles: true,
        composed: true,
      }),
    );
  }
  _render() {
    if (!this._config) return;
    const t = (key) => applianceText(this._hass, key);
    const entries = [...this._entries];
    if (
      this._config.entry_id &&
      !entries.some((entry) => entry.entry_id === this._config.entry_id)
    )
      entries.push({
        entry_id: this._config.entry_id,
        title: this._config.entry_id,
      });
    const devices = [...this._devices];
    for (const id of this._config.appliance_ids || [])
      if (!devices.some((device) => device.id === id))
        devices.push({ id, name: id });
    replaceCardHTML(
      this,
      `${STYLE}<div class="editor"><label>${esc(t("entry"))}<select id="entry" data-focus-key="entry"><option value="">—</option>${entries.map((entry) => `<option value="${esc(entry.entry_id)}"${entry.entry_id === this._config.entry_id ? " selected" : ""}>${esc(entry.title)}</option>`).join("")}</select></label><label>${esc(t("devices"))}<select id="devices" data-focus-key="devices" multiple>${devices.map((device) => `<option value="${esc(device.id)}"${this._config.appliance_ids?.includes(device.id) ? " selected" : ""}>${esc(device.name)}</option>`).join("")}</select><small>${esc(t("all"))}</small></label><label>${esc(t("card_title"))}<input id="title" data-focus-key="title" value="${esc(this._config.title || "")}"></label>${this._error ? `<p class="warning">${esc(t("error"))}</p><button id="retry" data-focus-key="retry">${esc(t("retry"))}</button>` : ""}</div>`,
    );
    this.shadowRoot
      .getElementById("entry")
      ?.addEventListener("change", (event) => {
        this._config.entry_id = event.target.value;
        this._generation++;
        this._loadingKey = undefined;
        this._loadedKey = undefined;
        this._error = false;
        delete this._config.appliance_ids;
        this._devices = [];
        this._emit();
        this._render();
        this._load();
      });
    this.shadowRoot
      .getElementById("devices")
      ?.addEventListener("change", (event) => {
        const ids = [...event.target.selectedOptions].map(
          (option) => option.value,
        );
        if (ids.length) this._config.appliance_ids = ids;
        else delete this._config.appliance_ids;
        this._emit();
      });
    this.shadowRoot
      .getElementById("title")
      ?.addEventListener("input", (event) => {
        this._config.title = event.target.value;
        this._emit();
      });
    this.shadowRoot
      .getElementById("retry")
      ?.addEventListener("click", () => this._load());
  }
}

if (!customElements.get(APPLIANCES_CARD_TYPE)) {
  customElements.define(APPLIANCES_CARD_TYPE, BatteryManagerAppliancesCard);
  customElements.define(EDITOR_TYPE, BatteryManagerAppliancesEditor);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: APPLIANCES_CARD_TYPE,
    get name() {
      return `Battery Manager · ${applianceText(null, "title")}`;
    },
    get description() {
      return applianceText(null, "description");
    },
    preview: true,
    documentationURL: DOCS_URL,
  });
}
