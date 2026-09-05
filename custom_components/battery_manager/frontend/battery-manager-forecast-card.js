/**
 * Battery Manager Forecast, Consumption + Cascade Cards
 *
 * Bundled with the battery_manager integration and registered as a Lovelace
 * resource automatically — no HACS frontend download needed. This module
 * registers three card types, all reading `sensor.…_soc_forecast`:
 *
 *   battery-manager-forecast-card
 *                                 the planned SOC trajectory with the full
 *                                 plan context (loads, appliances, feed-in)
 *   battery-manager-consumption-card
 *                                 the planned CONSUMPTION per slot, split by
 *                                 voltage level (230 V AC / 48 V / 24 V) with
 *                                 the planned surplus loads as their own
 *                                 layer (attribute `consumption_forecast`,
 *                                 backend >= v0.25.5)
 *   battery-manager-cascade-card  the internal Root/charge/discharge/output/
 *                                 terminal timeline of every storage cascade
 *
 * The forecast card renders from these sensor attributes:
 *
 *   forecast                    [{t, soc, feedin}, ...]
 *                                 planned SOC curve; feedin is the planned
 *                                 early grid feed-in power in W per slot
 *   soc_threshold_percent       optimal inverter threshold T*
 *   battery_min/max_soc_percent hard SOC limits
 *   inverter_min_soc_percent    inverter cut-off
 *   soc_buffer_percent          planning buffer above the minimum
 *   grid_import_kwh             expected grid import over the horizon
 *   lost_surplus_kwh            surplus that will still be lost/exported
 *   loads                       [{name, active, planned_energy_kwh,
 *                                 today_kwh, tomorrow_kwh,
 *                                 schedule: [{start, end}]}]
 *   appliances                  detected running appliances (washer, …):
 *                                 [{name, active, schedule: [{start, end,
 *                                 wh}]}] — one block now -> run end
 *   cascades                    storage cascades: the SOC chart shows only
 *                                 their Root-boundary energy; the dedicated
 *                                 cascade card renders internal activity
 *
 * Vanilla web component (no build step, no external dependencies); theming
 * via Home Assistant CSS variables inside an <ha-card>.
 *
 * The entity is user-configurable, so every attribute read is validated:
 * numbers via num(), arrays via Array.isArray(), and collection sizes are
 * capped (MAX_*). Accessibility: the SVG is a labelled img, focusable, and
 * the arrow keys step through the forecast slots; a visually hidden text
 * summary carries the key figures for screen readers.
 */

// Read the version from this module's own `?v=` cache-bust param (set from
// manifest.json when the resource is registered) so it never drifts.
const CARD_VERSION =
  new URL(import.meta.url).searchParams.get("v") || "dev";
const CARD_TYPE = "battery-manager-forecast-card";
const CASCADE_CARD_TYPE = "battery-manager-cascade-card";
const DOCS_URL = "https://github.com/danielr0815/battery-manager-ha";

// Lane palette as theme-overridable custom properties. The fallbacks were
// picked for >= 3:1 contrast against both #fff and #111/#1c1c1c card
// backgrounds (WCAG AA for non-text, verified numerically 2026-07) — this
// is why orange/purple deviate from the original Material-600 set. Color
// is never the only channel: the legend pairs every dot with a text label.
const LOAD_COLORS = [
  "var(--bmpc-load-1-color, #43a047)", // green
  "var(--bmpc-load-2-color, #ef6c00)", // orange
  "var(--bmpc-load-3-color, #039be5)", // light blue
  "var(--bmpc-load-4-color, #ba68c8)", // purple
  "var(--bmpc-load-5-color, #e53935)", // red
  "var(--bmpc-load-6-color, #00897b)", // teal
];
// Early grid feed-in lane (F-FEEDIN): pink-600, >= 3:1 on light and dark
// card backgrounds and distinct from every load lane color.
const FEEDIN_COLOR = "var(--bmpc-feedin-color, #d81b60)";
const CASCADE_ROOT_COLOR = "var(--bmpc-cascade-root-color, #1976d2)";
const CASCADE_CHARGE_COLOR = "var(--bmpc-cascade-charge-color, #43a047)";
const CASCADE_DISCHARGE_COLOR = "var(--bmpc-cascade-discharge-color, #ef6c00)";
const CASCADE_OUTPUT_COLOR = "var(--bmpc-cascade-output-color, #8e24aa)";
const CASCADE_TERMINAL_COLOR = "var(--bmpc-cascade-terminal-color, #00897b)";
const CASCADE_SOC_COLORS = [
  "var(--bmpc-cascade-soc-1-color, #039be5)",
  "var(--bmpc-cascade-soc-2-color, #ef6c00)",
  "var(--bmpc-cascade-soc-3-color, #8e24aa)",
  "var(--bmpc-cascade-soc-4-color, #43a047)",
];

// Defensive caps: attributes are user-controlled input, and a broken or
// hostile payload must not freeze the UI with megabytes of SVG.
const MAX_POINTS = 1000; // forecast samples kept (stride-downsampled)
const MAX_LANES = 12; // load + cascade + appliance + feed-in lanes below the plot
const MAX_BLOCKS = 100; // schedule blocks rendered per lane

const STRINGS = {
  en: {
    fault_unknown: "Cascade fault",
    fault_invalid_topology: "Invalid storage chain configuration",
    fault_safe_off_failed: "Safety shutdown failed",
    fault_restart_aux_reconciliation_failed: "Storage supply could not be restored after restart",
    fault_restart_wake_reconciliation_failed: "Storage wake-up could not be restored after restart",
    fault_exclusive_actor_changed_externally: "An exclusively controlled switch was changed externally",
    fault_root_transition_failed: "Switching to input supply failed",
    fault_wake_failed_after_retry: "Storage wake-up failed after retry",
    fault_source_power_proof_failed: "Storage output power could not be confirmed",
    fault_handover_failed_at_target: "Source change at the discharge target failed",
    fault_terminal_test_restore_failed: "Previous switch states could not be restored after the terminal test",
    fault_terminal_test_recovery_failed: "Interrupted terminal test could not be recovered",
    card_forecast: "Battery Manager Forecast",
    card_consumption: "Battery Manager Consumption",
    card_cascade: "Battery Manager Cascades",
    desc_forecast: "SOC forecast, inverter threshold and planned surplus loads.",
    desc_consumption: "Consumption forecast by voltage level and planned surplus loads.",
    desc_cascade: "Storage SOC, energy flows, planned sequence and cascade status.",
    field_entity: "Forecast sensor",
    field_title: "Title",
    field_hours: "Forecast horizon (hours)",
    invalid_config: "Invalid configuration",
    invalid_entity: "\"entity\" must be an entity id string",
    invalid_hours: "\"hours\" must be a finite number",
    cascade_phase_root: "input supply",
    cascade_phase_waking: "waking storage",
    cascade_phase_waking_members: "waking storage chain",
    cascade_phase_testing_terminal: "testing terminal load",
    cascade_phase_unknown: "unknown status",
    now: "now",
    threshold: "threshold",
    import: "grid import",
    lost: "lost surplus",
    prevented: "prevented export",
    loads: "Surplus loads",
    today_tomorrow: "(kWh · today/tomorrow)",
    nothing_planned: "nothing planned",
    active: "active",
    feedin_lane: "early feed-in",
    feedin: "planned feed-in",
    realized: "measured",
    feedin_realized: "early feed-in",
    no_entity: "No entity configured. Pick the Battery Manager SOC forecast sensor.",
    not_found: "Entity not found:",
    no_data: "Waiting for the first planning run …",
    min_reserve: "reserve",
    render_error: "The forecast chart could not be rendered:",
    chart_label: "SOC forecast",
    sr_min: "minimum",
    sr_max: "maximum",
    kbd_hint: "Use the arrow keys to step through the forecast.",
    // Consumption card
    chart_label_consumption: "Consumption forecast",
    level_ac: "230 V AC",
    level_dc48: "48 V DC",
    level_dc24: "24 V DC",
    planned_loads: "planned loads",
    cascade: "Cascade",
    charging: "charging",
    root: "Root",
    aux: "Aux",
    cascade_chart_label: "Cascade schedule",
    cascade_no_data: "No cascade activity planned in this horizon.",
    cascade_root_input: "Root → cascade",
    discharging: "discharging",
    output: "AC output",
    terminal_load: "terminal load",
    on: "ON",
    stored: "stored",
    source: "source",
    soc: "SOC",
    total: "total",
    cascade_phase_idle: "waiting",
    cascade_phase_proving: "checking source",
    cascade_phase_running: "discharging storage",
    cascade_phase_recovering: "recharge pending",
    cascade_phase_complete: "cycle complete",
    cascade_phase_fault: "fault",
    cascade_phase_hands_off: "manual control",
    cascade_plan: "Plan",
    cascade_from_storage: "from storage",
    cascade_via_root: "from PV / Root",
    cascade_root_today_tomorrow: "Root today/tomorrow",
    cascade_used_today: "used today",
    cascade_discharge_target: "discharge target",
    static_hint: "dimmed bars = static fallback profile",
    no_consumption:
      "No consumption forecast on this sensor — needs Battery Manager v0.25.5+.",
  },
  de: {
    fault_unknown: "Kaskadenstörung",
    fault_invalid_topology: "Ungültige Konfiguration der Speicherkette",
    fault_safe_off_failed: "Sicherheitsabschaltung fehlgeschlagen",
    fault_restart_aux_reconciliation_failed: "Speicherversorgung konnte nach Neustart nicht abgeglichen werden",
    fault_restart_wake_reconciliation_failed: "Speicheraktivierung konnte nach Neustart nicht abgeglichen werden",
    fault_exclusive_actor_changed_externally: "Ein exklusiv gesteuerter Schalter wurde extern geändert",
    fault_root_transition_failed: "Umschaltung auf Eingangsversorgung fehlgeschlagen",
    fault_wake_failed_after_retry: "Speicheraktivierung auch nach Wiederholung fehlgeschlagen",
    fault_source_power_proof_failed: "Ausgangsleistung des Speichers konnte nicht bestätigt werden",
    fault_handover_failed_at_target: "Quellenwechsel beim Entladeziel fehlgeschlagen",
    fault_terminal_test_restore_failed: "Vorherige Schalterzustände konnten nach dem Endlasttest nicht wiederhergestellt werden",
    fault_terminal_test_recovery_failed: "Unterbrochener Endlasttest konnte nicht wiederhergestellt werden",
    card_forecast: "Battery Manager Prognose",
    card_consumption: "Battery Manager Verbrauch",
    card_cascade: "Battery Manager Kaskaden",
    desc_forecast: "SOC-Prognose, Wechselrichterschwelle und geplante Überschusslasten.",
    desc_consumption: "Verbrauchsprognose nach Spannungsebene und geplante Überschusslasten.",
    desc_cascade: "Speicher-SOC, Energieflüsse, geplanter Ablauf und Kaskadenstatus.",
    field_entity: "Prognosesensor",
    field_title: "Titel",
    field_hours: "Prognosezeitraum (Stunden)",
    invalid_config: "Ungültige Konfiguration",
    invalid_entity: "\"entity\" muss eine Entitäts-ID als Text sein",
    invalid_hours: "\"hours\" muss eine endliche Zahl sein",
    cascade_phase_root: "Versorgung über Eingang",
    cascade_phase_waking: "weckt Speicher",
    cascade_phase_waking_members: "weckt Speicherkette",
    cascade_phase_testing_terminal: "prüft Endlast",
    cascade_phase_unknown: "Status unbekannt",
    now: "jetzt",
    threshold: "Schwelle",
    import: "Netzimport",
    lost: "verlorener Überschuss",
    prevented: "verhinderter Export",
    loads: "Überschusslasten",
    today_tomorrow: "(kWh · heute/morgen)",
    nothing_planned: "nichts geplant",
    active: "aktiv",
    feedin_lane: "vorzeitige Einspeisung",
    feedin: "geplante Einspeisung",
    realized: "Ist",
    feedin_realized: "frühe Einspeisung",
    no_entity:
      "Keine Entität konfiguriert. Wähle den SOC-Prognose-Sensor des Battery Managers.",
    not_found: "Entität nicht gefunden:",
    no_data: "Warte auf den ersten Planungslauf …",
    min_reserve: "Reserve",
    render_error: "Das Prognosediagramm konnte nicht dargestellt werden:",
    chart_label: "SOC-Prognose",
    sr_min: "Minimum",
    sr_max: "Maximum",
    kbd_hint: "Mit den Pfeiltasten durch die Prognose gehen.",
    // Verbrauchs-Card
    chart_label_consumption: "Verbrauchsprognose",
    level_ac: "230 V AC",
    level_dc48: "48 V DC",
    level_dc24: "24 V DC",
    planned_loads: "geplante Lasten",
    cascade: "Kaskade",
    charging: "laden",
    root: "Eingang",
    aux: "Speicher",
    cascade_chart_label: "Kaskaden-Zeitplan",
    cascade_no_data: "In diesem Zeitraum ist keine Kaskadenaktivität geplant.",
    cascade_root_input: "Eingang → Kaskade",
    discharging: "entladen",
    output: "AC-Ausgang",
    terminal_load: "Endlast",
    on: "AN",
    stored: "gespeichert",
    source: "Quelle",
    soc: "SOC",
    total: "Summe",
    cascade_phase_idle: "wartet",
    cascade_phase_proving: "prüft Speicher",
    cascade_phase_running: "entlädt Speicher",
    cascade_phase_recovering: "Wiederaufladung ausstehend",
    cascade_phase_complete: "Zyklus abgeschlossen",
    cascade_phase_fault: "Störung",
    cascade_phase_hands_off: "manuelle Steuerung",
    cascade_plan: "Plan",
    cascade_from_storage: "aus Speichern",
    cascade_via_root: "aus PV / Eingang",
    cascade_root_today_tomorrow: "Eingang heute/morgen",
    cascade_used_today: "heute genutzt",
    cascade_discharge_target: "Entladeziel",
    static_hint: "abgedunkelte Balken = statisches Fallback-Profil",
    no_consumption:
      "Keine Verbrauchsprognose im Sensor — benötigt Battery Manager v0.25.5+.",
  },
};

// Picker/editor callbacks have no hass argument. Resolve the HA user language
// on access so neither module loading nor a sensor cache freezes the locale.
function uiLanguage(hass) {
  return hass?.language || (typeof document !== "undefined" &&
    document.querySelector("home-assistant")?.hass?.language) ||
    (typeof navigator !== "undefined" && navigator.language) || "en";
}

function localize(hass, key) {
  const lang = uiLanguage(hass).toLowerCase().split(/[-_]/)[0];
  return (STRINGS[lang] || STRINGS.en)[key] || STRINGS.en[key] || key;
}

// Entity names, titles etc. are user-controlled — escape before innerHTML.
function esc(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Attribute values arrive as JSON and may be strings, objects or NaN.
// Accept anything Number() can make finite; treat the rest as absent so
// callers fall back to their default instead of crashing on .toFixed().
function num(value) {
  if (value == null || value === "" || typeof value === "boolean") {
    return undefined;
  }
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}

function isForecastEntity(stateObj) {
  const fc = stateObj?.attributes?.forecast;
  return (
    Array.isArray(fc) &&
    fc.length > 1 &&
    typeof fc[0] === "object" &&
    fc[0] !== null &&
    "soc" in fc[0] &&
    "t" in fc[0]
  );
}

function findForecastEntity(hass, entities) {
  const candidates = (entities || []).filter(
    (id) => id.startsWith("sensor.") && isForecastEntity(hass.states[id])
  );
  // Prefer the battery_manager naming if several sensors expose a forecast
  return (
    candidates.find((id) => id.includes("soc_forecast")) || candidates[0] || ""
  );
}

class BatteryManagerForecastCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = undefined;
    this._hass = undefined;
    this._lastState = undefined;
    this._width = 0;
    this._chartMeta = null;
    this._laneCount = 0; // rendered lanes, feeds getCardSize()
    this._kbIndex = null; // keyboard-driven slot index
    this._shownSlot = null; // dedupes marker/readout DOM writes
    this._rafId = null; // pending pointermove animation frame
    this._pointerEv = null;
    this._resizeObserver = new ResizeObserver(() => {
      const width = this.getBoundingClientRect().width;
      if (width && Math.abs(width - this._width) > 4) {
        this._width = width;
        this._render();
      }
    });
  }

  connectedCallback() {
    this._resizeObserver.observe(this);
  }

  disconnectedCallback() {
    this._resizeObserver.disconnect();
    this._cancelPendingFrame();
  }

  setConfig(config) {
    if (!config || typeof config !== "object") {
      throw new Error(localize(this._hass, "invalid_config"));
    }
    // YAML is free-form: fail loudly with a useful message instead of
    // rendering something subtly broken (Lovelace shows thrown errors).
    if (config.entity != null && typeof config.entity !== "string") {
      throw new Error(`${CARD_TYPE}: ${localize(this._hass, "invalid_entity")}`);
    }
    if (
      config.hours != null &&
      (typeof config.hours !== "number" || !Number.isFinite(config.hours))
    ) {
      throw new Error(`${CARD_TYPE}: ${localize(this._hass, "invalid_hours")}`);
    }
    this._config = {
      hours: 48,
      ...config,
    };
    this._lastState = undefined;
    this._render();
  }

  set hass(hass) {
    const languageChanged = hass.language !== this._hass?.language;
    this._hass = hass;
    const stateObj = this._config?.entity
      ? hass.states[this._config.entity]
      : undefined;
    if (stateObj !== this._lastState || languageChanged) {
      this._lastState = stateObj;
      this._render();
    }
  }

  getCardSize() {
    // One Lovelace unit ≈ 50 px; each schedule lane adds ~11 px below the
    // plot, so the card grows with the lane count set by _renderChart().
    return 4 + Math.ceil(this._laneCount / 3);
  }

  // Instance method by contract (unlike getStubConfig/getConfigForm):
  // the sections layout calls it on the card element.
  getGridOptions() {
    return { rows: 4, columns: 12, min_rows: 3, min_columns: 6 };
  }

  static getStubConfig(hass, entities, entitiesFallback) {
    return {
      entity:
        findForecastEntity(hass, entities) ||
        findForecastEntity(hass, entitiesFallback),
    };
  }

  static getConfigForm() {
    return {
      computeLabel: (schema) => localize(null, `field_${schema.name}`),
      schema: [
        {
          name: "entity",
          required: true,
          selector: { entity: { domain: "sensor" } },
        },
        { name: "title", selector: { text: {} } },
        {
          name: "hours",
          default: 48,
          selector: { number: { min: 6, max: 96, step: 1, mode: "box" } },
        },
      ],
    };
  }

  // ------------------------------------------------------------------
  // Rendering
  // ------------------------------------------------------------------

  _message(text) {
    return `<div class="msg">${text}</div>`;
  }

  _render() {
    // Attribute payloads are user input: a rendering exception must surface
    // as an in-card error (Lovelace convention), not as a silently dead
    // card. The guard lives here so the setConfig/ResizeObserver/hass call
    // sites are all covered.
    try {
      this._renderInner();
    } catch (err) {
      console.error(`[${CARD_TYPE}] render failed:`, err);
      this._renderError(err);
    }
  }

  _renderError(err) {
    if (!this.shadowRoot) {
      return;
    }
    const detail = err instanceof Error ? err.message : String(err);
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { display: block; padding: 12px 16px; }
        .error { color: var(--error-color, #db4437); }
      </style>
      <ha-card>
        <span class="error">${esc(localize(this._hass, "render_error"))} ${esc(
          detail
        )}</span>
      </ha-card>
    `;
  }

  _renderInner() {
    if (!this.shadowRoot || !this._config) {
      return;
    }
    this._laneCount = 0;
    this._kbIndex = null;
    this._shownSlot = null;
    const hass = this._hass;
    const t = (key) => localize(hass, key);

    let body;
    let header = this._config.title;
    const stateObj = this._config.entity
      ? hass?.states?.[this._config.entity]
      : undefined;

    if (!this._config.entity) {
      body = this._message(t("no_entity"));
    } else if (!stateObj) {
      body = this._message(`${t("not_found")} ${esc(this._config.entity)}`);
    } else if (!isForecastEntity(stateObj)) {
      body = this._message(t("no_data"));
    } else {
      header =
        this._config.title ??
        hass.formatEntityName?.(stateObj) ??
        stateObj.attributes.friendly_name ??
        this._config.entity;
      body = this._renderChart(stateObj, t);
    }

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 12px 12px 8px; }
        .header {
          display: flex; flex-wrap: wrap; align-items: baseline;
          justify-content: space-between; gap: 4px 12px; padding: 0 4px 6px;
        }
        .title {
          font-size: 1.1em; font-weight: 500;
          color: var(--primary-text-color);
        }
        .stats {
          font-size: 0.85em; color: var(--secondary-text-color);
        }
        .msg {
          padding: 24px 8px; color: var(--secondary-text-color);
        }
        svg { display: block; }
        svg:focus { outline: none; }
        svg:focus-visible {
          outline: 2px solid var(--primary-color, #03a9f4);
          outline-offset: 2px; border-radius: 2px;
        }
        .visually-hidden {
          position: absolute; width: 1px; height: 1px; margin: -1px;
          padding: 0; overflow: hidden; clip: rect(0 0 0 0);
          clip-path: inset(50%); white-space: nowrap; border: 0;
        }
        .legend {
          display: flex; flex-wrap: wrap; gap: 2px 14px;
          padding: 6px 4px 0; font-size: 0.8em;
          color: var(--secondary-text-color);
        }
        .legend .dot {
          display: inline-block; width: 8px; height: 8px;
          border-radius: 50%; margin-right: 4px;
        }
        .legend .active {
          color: var(--primary-color); font-weight: 500;
        }
        .legend .off {
          font-style: italic; opacity: 0.8;
        }
        .readout {
          font-size: 0.8em; color: var(--secondary-text-color);
          text-align: right; min-height: 1.2em; padding: 2px 4px 0;
        }
        .readout .chip { margin-left: 8px; white-space: nowrap; }
        .readout .dot {
          display: inline-block; width: 8px; height: 8px;
          border-radius: 50%; margin-right: 4px;
        }
      </style>
      <ha-card>
        <div class="header">
          <div class="title">${esc(header ?? "")}</div>
          <div class="stats">${this._statsLine(stateObj, t)}</div>
        </div>
        ${body}
      </ha-card>
    `;
    this._attachChartHandlers();
  }

  _statsLine(stateObj, t) {
    if (!stateObj || !isForecastEntity(stateObj)) {
      return "";
    }
    const a = stateObj.attributes;
    const parts = [];
    const threshold = num(a.soc_threshold_percent);
    if (threshold !== undefined) {
      parts.push(`T* ${Math.round(threshold)} %`);
    }
    // Per-day today/tomorrow figures from the `daily` breakdown (import +
    // lost surplus since v0.9.1, load energy since v0.10.1). Shown as
    // `today/tomorrow` with a single trailing legend so the slash format is
    // explained once (operator request 2026-07-11).
    const daily = Array.isArray(a.daily) ? a.daily : null;
    if (daily && daily.length) {
      const td = (key) => {
        const today = num(daily[0]?.[key]) ?? 0;
        const tomorrow = num(daily[1]?.[key]) ?? 0;
        return `${today.toFixed(1)}/${tomorrow.toFixed(1)}`;
      };
      parts.push(`${t("import")} ${td("grid_import_kwh")}`);
      // F-REALIZED-SURPLUS: with the `realized` block the backend delivers
      // measured-so-far plus combined totals (Ist + rest-of-day forecast).
      // The lost/prevented segments then show the TOTAL per day with the
      // realized share in parentheses and replace the pure-forecast
      // segments below, which would otherwise double-report the same days.
      const realized =
        a.realized && typeof a.realized === "object" ? a.realized : null;
      if (realized) {
        const rtd = (todayKey, tomorrowKey, realizedKey) =>
          `${(num(realized[todayKey]) ?? 0).toFixed(1)}/` +
          `${(num(realized[tomorrowKey]) ?? 0).toFixed(1)} ` +
          `(${t("realized")} ${(num(realized[realizedKey]) ?? 0).toFixed(1)})`;
        parts.push(
          `${t("lost")} ${rtd(
            "lost_surplus_today_kwh",
            "lost_surplus_tomorrow_kwh",
            "lost_surplus_realized_kwh"
          )}`
        );
        parts.push(
          `${t("prevented")} ${rtd(
            "prevented_export_today_kwh",
            "prevented_export_tomorrow_kwh",
            "prevented_export_realized_kwh"
          )}`
        );
        // Measured deliberate early feed-in today (F-FEEDIN); hidden when
        // nothing was fed in yet — a permanent "0.0" would just be noise.
        const feedinRealized = num(realized.early_feed_in_realized_kwh) ?? 0;
        if (feedinRealized > 0) {
          parts.push(
            `${t("feedin_realized")} ${t("realized")} ${feedinRealized.toFixed(1)}`
          );
        }
      } else {
        parts.push(`${t("lost")} ${td("lost_surplus_kwh")}`);
        // F-STRICT-SURPLUS R4: the no-loads counterfactual — the export the
        // day's load runs prevent. Answers "why is a load running although
        // the SOC never reaches max?" right on the card. Only rendered when
        // the backend (>= 0.15.0) delivers the field.
        if (daily[0]?.prevented_export_kwh != null) {
          parts.push(`${t("prevented")} ${td("prevented_export_kwh")}`);
        }
      }
      // F-FEEDIN: planned early feed-in energy per day, same backend-compat
      // pattern — only rendered when the attribute is present.
      if (daily[0]?.planned_feedin_kwh != null) {
        parts.push(`${t("feedin")} ${td("planned_feedin_kwh")}`);
      }
      parts.push(`${t("loads")} ${td("loads_kwh")}`);
      parts.push(t("today_tomorrow"));
      return parts.join(" · ");
    }
    // Fallback for a pre-0.9.1 backend without the per-day breakdown: the
    // horizon totals, exactly as before.
    const gridImport = num(a.grid_import_kwh);
    if (gridImport !== undefined) {
      parts.push(`${t("import")} ${gridImport.toFixed(1)} kWh`);
    }
    const lostSurplus = num(a.lost_surplus_kwh);
    if (lostSurplus !== undefined) {
      parts.push(`${t("lost")} ${lostSurplus.toFixed(1)} kWh`);
    }
    return parts.join(" · ");
  }

  _renderChart(stateObj, t) {
    const a = stateObj.attributes;
    const lang = this._hass?.language || "en";

    let points = a.forecast
      // null coerces to finite 0 (epoch/0 %), so reject it before conversion
      .filter((p) => p && p.t != null && p.soc != null)
      .map((p) => ({
        time: new Date(p.t).getTime(),
        soc: Number(p.soc),
        feedin: num(p.feedin) ?? 0,
      }))
      .filter((p) => Number.isFinite(p.time) && Number.isFinite(p.soc));
    if (points.length < 2) {
      return this._message(t("no_data"));
    }
    const horizonMs = Number(this._config.hours) * 3600 * 1000;
    if (horizonMs > 0) {
      const cutoff = points[0].time + horizonMs;
      const capped = points.filter((p) => p.time <= cutoff);
      if (capped.length >= 2) {
        points = capped;
      }
    }
    if (points.length > MAX_POINTS) {
      // Cap SVG size against hostile payloads. Stride sampling keeps the
      // first and last point, so horizon and curve shape survive; the
      // backend normally sends well under 400 points and never hits this.
      const stride = Math.ceil(points.length / MAX_POINTS);
      points = points.filter(
        (_, i, arr) => i % stride === 0 || i === arr.length - 1
      );
    }
    const t0 = points[0].time;
    const t1 = points[points.length - 1].time;
    // Identical timestamps would divide by zero in x() — nothing to plot.
    if (t1 <= t0) {
      return this._message(t("no_data"));
    }

    const loads = (Array.isArray(a.loads) ? a.loads : [])
      .filter((load) => load && typeof load === "object")
      .slice(0, MAX_LANES)
      .map((load, i) => ({
        ...load,
        schedule: (Array.isArray(load.schedule) ? load.schedule : [])
          .filter((b) => b && typeof b === "object")
          .slice(0, MAX_BLOCKS),
        color: LOAD_COLORS[i % LOAD_COLORS.length],
        // `kind` tells the hover readout where this lane's per-hour energy
        // comes from: a load carries it on its schedule block (`wh`), the
        // feed-in lane derives it from the point's planned power.
        kind: "load",
      }));
    // Cascade-managed loads do not appear in `loads`: this chart treats the
    // complete chain as one black-box consumer at its Root boundary. Internal
    // charge/discharge/output details belong exclusively to the cascade card.
    const cascades = (Array.isArray(a.cascades) ? a.cascades : [])
      .filter((cascade) => cascade && typeof cascade === "object")
      .slice(0, MAX_LANES)
      .map((cascade, i) => ({
        name: `${t("cascade")} ${cascade.name ?? "?"}`,
        planned_root_energy_kwh: num(cascade.planned_root_energy_kwh) ?? 0,
        today_kwh: num(cascade.today_kwh),
        tomorrow_kwh: num(cascade.tomorrow_kwh),
        schedule: (Array.isArray(cascade.schedule) ? cascade.schedule : [])
          .filter(
            (block) =>
              block &&
              typeof block === "object" &&
              (num(block.root_input_wh) ?? 0) > 0
          )
          .slice(0, MAX_BLOCKS)
          .map((block) => ({
            ...block,
            wh: num(block.root_input_wh) ?? 0,
            label: `${t("root")} ${Math.round(num(block.root_input_wh) ?? 0)} Wh`,
          })),
        kind: "cascade",
        color: LOAD_COLORS[(loads.length + i) % LOAD_COLORS.length],
      }));
    // Detected appliance runs (washer, dishwasher, …): the backend ships one
    // block per run (now -> run end) carrying the run's remaining energy.
    // Colors continue the load cycle so adjacent lanes differ.
    const appliances = (Array.isArray(a.appliances) ? a.appliances : [])
      .filter((ap) => ap && typeof ap === "object")
      .slice(0, MAX_LANES)
      .map((ap, i) => ({
        name: ap.name,
        active: true,
        total_wh: num(ap.schedule?.[0]?.wh),
        schedule: (Array.isArray(ap.schedule) ? ap.schedule : [])
          .filter((b) => b && typeof b === "object")
          .slice(0, MAX_BLOCKS),
        kind: "appliance",
        color:
          LOAD_COLORS[(loads.length + cascades.length + i) % LOAD_COLORS.length],
      }));
    // Early grid feed-in (F-FEEDIN): the backend stores a slot's power on its
    // END point. Keep one block per physical slot so hover can use the exact
    // power × duration energy instead of shifting values by one boundary.
    // Adjacent rectangles still render as one continuous visual period.
    const feedinBlocks = [];
    for (let i = 1; i < points.length; i++) {
      const w = points[i].feedin;
      if (w > 0) {
        const durationH = (points[i].time - points[i - 1].time) / 3600000;
        feedinBlocks.push({
          start: points[i - 1].time,
          end: points[i].time,
          wh: w * durationH,
          label: `${Math.round(w)} W`,
        });
      }
    }
    const feedinLanes = feedinBlocks.length
      ? [
          {
            name: t("feedin_lane"),
            color: FEEDIN_COLOR,
            dailyKey: "planned_feedin_kwh",
            kind: "feedin",
            schedule: feedinBlocks.slice(0, MAX_BLOCKS),
          },
        ]
      : [];
    const lanes = [
      ...loads.filter((l) => l.schedule.length > 0),
      ...cascades.filter((l) => l.schedule.length > 0),
      ...appliances.filter((l) => l.schedule.length > 0),
      ...feedinLanes,
    ].slice(0, MAX_LANES);
    this._laneCount = lanes.length;

    const width = Math.max(this._width || this.clientWidth || 320, 280);
    // Generous right margin: the curve must not run into the card edge
    // and the T* label needs room next to the plot.
    const margin = { top: 8, right: 30, bottom: 16, left: 32 };
    const laneH = 8;
    const laneGap = 3;
    const lanesH = lanes.length ? lanes.length * (laneH + laneGap) + 4 : 0;
    const plotH = 150;
    const height = margin.top + plotH + lanesH + margin.bottom;

    const x = (time) =>
      margin.left +
      ((time - t0) / (t1 - t0)) * (width - margin.left - margin.right);
    const y = (soc) => margin.top + (1 - soc / 100) * plotH;

    const line = "var(--divider-color, #e0e0e0)";
    const text = "var(--secondary-text-color, #727272)";
    const accent = "var(--primary-color, #03a9f4)";
    const warn = "var(--warning-color, #ff9800)";
    const err = "var(--error-color, #db4437)";

    const svg = [];

    // Zones: hard SOC limits and the planning reserve (min + buffer).
    // num() guarantees finite inputs; the clamp keeps a garbage value from
    // producing a negative-height rect (invalid SVG, dropped by browsers).
    const socMin = num(a.battery_min_soc_percent) ?? 0;
    const socMax = num(a.battery_max_soc_percent) ?? 100;
    const buffer = num(a.soc_buffer_percent) ?? 0;
    const invMin = num(a.inverter_min_soc_percent);
    const plotW = width - margin.left - margin.right;
    const reserve = Math.max(0, Math.min(socMin + buffer, 100));
    if (reserve > 0) {
      svg.push(
        `<rect x="${margin.left}" y="${y(reserve)}" width="${plotW}"
          height="${y(0) - y(reserve)}" fill="${err}" opacity="0.07"/>`
      );
    }
    if (socMax < 100 && socMax >= 0) {
      svg.push(
        `<rect x="${margin.left}" y="${y(100)}" width="${plotW}"
          height="${y(socMax) - y(100)}" fill="${text}" opacity="0.07"/>`
      );
    }

    // Horizontal grid + y labels
    for (const pct of [0, 20, 40, 60, 80, 100]) {
      svg.push(
        `<line x1="${margin.left}" y1="${y(pct)}" x2="${width - margin.right}"
          y2="${y(pct)}" stroke="${line}" stroke-width="1"/>`,
        `<text x="${margin.left - 5}" y="${y(pct) + 3}" text-anchor="end"
          font-size="9" fill="${text}">${pct}</text>`
      );
    }

    // Vertical grid: day boundaries (labelled) and 6-hour ticks
    const dayFmt = new Intl.DateTimeFormat(lang, { weekday: "short" });
    const gridBottom = margin.top + plotH + lanesH;
    for (
      let tick = new Date(t0).setMinutes(0, 0, 0) + 3600 * 1000;
      tick <= t1;
      tick += 3600 * 1000
    ) {
      const hour = new Date(tick).getHours();
      if (hour === 0) {
        svg.push(
          `<line x1="${x(tick)}" y1="${margin.top}" x2="${x(tick)}"
            y2="${gridBottom}" stroke="${line}" stroke-width="1.5"/>`,
          `<text x="${x(tick) + 3}" y="${height - 4}" font-size="9"
            fill="${text}">${dayFmt.format(tick)}</text>`
        );
      } else if (hour % 6 === 0) {
        svg.push(
          `<line x1="${x(tick)}" y1="${margin.top}" x2="${x(tick)}"
            y2="${gridBottom}" stroke="${line}" stroke-width="1"
            stroke-dasharray="2 3" opacity="0.7"/>`,
          `<text x="${x(tick)}" y="${height - 4}" font-size="9"
            text-anchor="middle" fill="${text}">${hour}</text>`
        );
      }
    }

    // Inverter cut-off (dotted) and threshold T* (dashed); out-of-range
    // values would land outside the plot, so skip them entirely.
    if (invMin !== undefined && invMin > reserve && invMin <= 100) {
      svg.push(
        `<line x1="${margin.left}" y1="${y(invMin)}"
          x2="${width - margin.right}" y2="${y(invMin)}" stroke="${text}"
          stroke-width="1" stroke-dasharray="1 3"/>`
      );
    }
    const threshold = num(a.soc_threshold_percent);
    const showThreshold =
      threshold !== undefined && threshold >= 0 && threshold <= 100;
    if (showThreshold) {
      svg.push(
        `<line x1="${margin.left}" y1="${y(threshold)}"
          x2="${width - margin.right}" y2="${y(threshold)}" stroke="${warn}"
          stroke-width="1.5" stroke-dasharray="5 3"/>`,
        `<text x="${width - margin.right - 2}" y="${y(threshold) - 3}"
          text-anchor="end" font-size="9" fill="${warn}">T* ${Math.round(
            threshold
          )} %</text>`
      );
    }

    // SOC curve: soft area fill + line
    const coords = points.map((p) => `${x(p.time).toFixed(1)},${y(p.soc).toFixed(1)}`);
    svg.push(
      `<polygon points="${x(points[0].time).toFixed(1)},${y(0)} ${coords.join(
        " "
      )} ${x(points[points.length - 1].time).toFixed(1)},${y(0)}"
        fill="${accent}" opacity="0.12"/>`,
      `<polyline points="${coords.join(" ")}" fill="none" stroke="${accent}"
        stroke-width="2" stroke-linejoin="round"/>`
    );

    // "now" marker: the curve starts at the current SOC
    svg.push(
      `<circle cx="${x(t0)}" cy="${y(points[0].soc)}" r="3.5"
        fill="${accent}"/>`,
      `<text x="${x(t0) + 5}" y="${y(points[0].soc) - 6}" font-size="9"
        fill="${text}">${t("now")} ${Math.round(points[0].soc)} %</text>`
    );

    // Load lanes below the plot
    lanes.forEach((load, i) => {
      const laneY = margin.top + plotH + 4 + i * (laneH + laneGap);
      for (const block of load.schedule) {
        const start = new Date(block.start).getTime();
        const end = new Date(block.end).getTime();
        if (
          !Number.isFinite(start) ||
          !Number.isFinite(end) ||
          end <= t0 ||
          start >= t1 // schedule may extend past the plotted horizon
        ) {
          continue;
        }
        const bx = x(Math.max(start, t0));
        const bw = Math.max(x(Math.min(end, t1)) - bx, 2);
        svg.push(
          `<rect x="${bx.toFixed(1)}" y="${laneY}" width="${bw.toFixed(1)}"
            height="${laneH}" rx="2" fill="${load.color}" opacity="0.85"/>`
        );
        // Per-block power label (feed-in lane); only when the block is wide
        // enough to carry legible text.
        if (block.label && bw >= 34) {
          svg.push(
            `<text x="${(bx + bw / 2).toFixed(1)}" y="${laneY + laneH - 1.5}"
              text-anchor="middle" font-size="7" fill="#fff">${esc(
                block.label
              )}</text>`
          );
        }
      }
    });

    // Hover overlay target (events attached after innerHTML assignment)
    svg.push(
      `<rect id="hover-target" x="${margin.left}" y="${margin.top}"
        width="${plotW}" height="${plotH + lanesH}" fill="transparent"/>`,
      `<g id="hover-marker"></g>`
    );

    this._chartMeta = { points, x, y, margin, plotH, lanesH, t0, t1, lang, lanes };

    const whenFmt = new Intl.DateTimeFormat(lang, {
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
    });

    // Screen-reader summary: the SVG is announced as one labelled image,
    // so the key figures ride along as text — in the aria-label and, with
    // the stats line appended, in a visually hidden block below the chart.
    let minP = points[0];
    let maxP = points[0];
    for (const p of points) {
      if (p.soc < minP.soc) minP = p;
      if (p.soc > maxP.soc) maxP = p;
    }
    const summary =
      `${t("chart_label")}: ${t("now")} ${Math.round(points[0].soc)} %, ` +
      `${t("sr_min")} ${Math.round(minP.soc)} % (${whenFmt.format(
        minP.time
      )}), ` +
      `${t("sr_max")} ${Math.round(maxP.soc)} % (${whenFmt.format(
        maxP.time
      )})` +
      (showThreshold ? `, ${t("threshold")} ${Math.round(threshold)} %` : "") +
      ".";
    const statsText = this._statsLine(stateObj, t);

    const legend = loads
      .map((load) => {
        const planned = num(load.planned_energy_kwh) ?? 0;
        // Per-load heute/morgen split when the backend supplies it (the slash
        // format is explained once by the subtitle's `today_tomorrow` legend);
        // fall back to the horizon total for a pre-0.11.1 backend, or when the
        // load only runs on day 3+ (both today and tomorrow zero).
        const today = load.today_kwh;
        const tomorrow = load.tomorrow_kwh;
        const hasPerDay =
          typeof today === "number" &&
          typeof tomorrow === "number" &&
          (today > 0 || tomorrow > 0);
        const detail = hasPerDay
          ? `${today.toFixed(1)}/${tomorrow.toFixed(1)} kWh`
          : planned
          ? `${planned.toFixed(1)} kWh`
          : t("nothing_planned");
        const planningPower = num(load.planning_power_w);
        const powerDetail =
          planningPower !== undefined ? ` · ${Math.round(planningPower)} W` : "";
        const active = load.active
          ? ` · <span class="active">${t("active")}</span>`
          : "";
        // A load can be scheduled OUTSIDE the plotted window (e.g. a horizon
        // longer than `hours`): it shows planned energy but no lane block, which
        // looks contradictory. Flag it with when the first block runs.
        const sched = load.schedule;
        const firstMs = sched.length ? new Date(sched[0].start).getTime() : NaN;
        const inWindow = sched.some((b) => {
          const s = new Date(b.start).getTime();
          const e = new Date(b.end).getTime();
          return Number.isFinite(s) && Number.isFinite(e) && e > t0 && s < t1;
        });
        const offWindow =
          planned > 0 && sched.length && !inWindow && Number.isFinite(firstMs)
            ? ` · <span class="off">${whenFmt.format(firstMs)}</span>`
            : "";
        return `<span><span class="dot" style="background:${load.color}"></span>${esc(
          load.name ?? "?"
        )} (${detail}${powerDetail})${active}${offWindow}</span>`;
      })
      .join("");

    // The feed-in lane carries the same heute/morgen figure as the load
    // entries, read from the `daily` breakdown (operator ask 2026-08-03).
    // A backend that does not publish the metric yet renders the plain
    // dot+name entry exactly as before.
    const dailyArr = Array.isArray(a.daily) ? a.daily : null;
    const laneDetail = (key) => {
      if (!key || !dailyArr || !dailyArr.length || dailyArr[0]?.[key] == null) {
        return null;
      }
      const today = num(dailyArr[0][key]) ?? 0;
      const tomorrow = num(dailyArr[1]?.[key]) ?? 0;
      return `${today.toFixed(1)}/${tomorrow.toFixed(1)} kWh`;
    };
    const feedinLegend = feedinLanes
      .map((l) => {
        const detail = laneDetail(l.dailyKey);
        return `<span><span class="dot" style="background:${l.color}"></span>${esc(
          l.name
        )}${detail ? ` (${detail})` : ""}</span>`;
      })
      .join("");

    // Appliance runs (operator ask 2026-08-08): dot + name, the run's
    // remaining energy, and an active marker — every published run IS active.
    const applianceLegend = appliances
      .map(
        (ap) =>
          `<span><span class="dot" style="background:${ap.color}"></span>${esc(
            ap.name ?? "?"
          )}${
            ap.total_wh != null
              ? ` (${(ap.total_wh / 1000).toFixed(1)} kWh)`
              : ""
          } · <span class="active">${t("active")}</span></span>`
      )
      .join("");

    const cascadeLegend = cascades
      .map((cascade) => {
        const hasPerDay =
          typeof cascade.today_kwh === "number" &&
          typeof cascade.tomorrow_kwh === "number" &&
          (cascade.today_kwh > 0 || cascade.tomorrow_kwh > 0);
        const root = hasPerDay
          ? `${cascade.today_kwh.toFixed(1)}/${cascade.tomorrow_kwh.toFixed(1)}`
          : cascade.planned_root_energy_kwh.toFixed(2);
        return `<span><span class="dot" style="background:${cascade.color}"></span>${esc(
          cascade.name
        )} (${t("root")} ${root} kWh)</span>`;
      })
      .join("");

    return `
      <svg id="chart" role="img" tabindex="0" aria-label="${esc(summary)}"
        width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
        ${svg.join("\n")}
      </svg>
      <div class="readout" id="readout" aria-live="polite">&nbsp;</div>
      <div class="visually-hidden">${esc(summary)} ${esc(statsText)} ${esc(
        t("kbd_hint")
      )}</div>
      ${
        legend || cascadeLegend || feedinLegend || applianceLegend
          ? `<div class="legend">${legend}${cascadeLegend}${feedinLegend}${applianceLegend}</div>`
          : ""
      }
    `;
  }

  // ------------------------------------------------------------------
  // Hover crosshair + keyboard exploration
  // ------------------------------------------------------------------

  _attachChartHandlers() {
    const target = this.shadowRoot.getElementById("hover-target");
    if (!target || !this._chartMeta) {
      return;
    }
    target.addEventListener("pointermove", (ev) => this._onPointerMove(ev));
    target.addEventListener("pointerleave", () => {
      this._cancelPendingFrame();
      this._clearSlot();
    });
    // The SVG itself is focusable (tabindex), so keyboard users get the
    // same per-slot details via the arrow keys that pointer users get via
    // hover.
    const svg = this.shadowRoot.getElementById("chart");
    if (svg) {
      svg.addEventListener("keydown", (ev) => this._onKeyDown(ev));
    }
  }

  // pointermove fires far more often than is worth repainting for —
  // coalesce to one marker update per animation frame.
  _onPointerMove(ev) {
    this._pointerEv = ev;
    if (this._rafId == null) {
      this._rafId = requestAnimationFrame(() => {
        this._rafId = null;
        const pending = this._pointerEv;
        this._pointerEv = null;
        this._onHover(pending);
      });
    }
  }

  _cancelPendingFrame() {
    if (this._rafId != null) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
    this._pointerEv = null;
  }

  _onKeyDown(ev) {
    const meta = this._chartMeta;
    if (!meta) {
      return;
    }
    const last = meta.points.length - 1;
    let index = this._kbIndex;
    switch (ev.key) {
      case "ArrowLeft":
        index = index == null ? 0 : Math.max(0, index - 1);
        break;
      case "ArrowRight":
        index = index == null ? 0 : Math.min(last, index + 1);
        break;
      case "Home":
        index = 0;
        break;
      case "End":
        index = last;
        break;
      case "Escape":
        this._clearSlot();
        return;
      default:
        return;
    }
    // Keep the page from scrolling while the user walks the curve.
    ev.preventDefault();
    this._showSlot(index);
  }

  _onHover(ev) {
    const meta = this._chartMeta;
    const marker = this.shadowRoot.getElementById("hover-marker");
    if (!meta || !marker) {
      return;
    }
    const svg = marker.ownerSVGElement;
    const rect = svg.getBoundingClientRect();
    // A zero-size SVG (hidden card) would make the px mapping NaN.
    if (!(rect.width > 0)) {
      return;
    }
    const px = ((ev.clientX - rect.left) / rect.width) * svg.viewBox.baseVal.width;
    const time =
      meta.t0 +
      ((px - meta.margin.left) /
        (svg.viewBox.baseVal.width - meta.margin.left - 10)) *
        (meta.t1 - meta.t0);
    // Forecast points are slot boundaries: point 0 starts slot 0, point 1
    // starts slot 1 while also carrying slot 0's ending SOC.  Selecting the
    // last boundary at/before the pointer keeps the whole visible schedule
    // rectangle mapped to its own block; nearest-point snapping lost the
    // details over the right half of every lane block.
    let slotIndex = 0;
    for (let i = 1; i < meta.points.length; i++) {
      if (meta.points[i].time > time) break;
      slotIndex = i;
    }
    this._showSlot(slotIndex);
  }

  _showSlot(index) {
    const meta = this._chartMeta;
    const marker = this.shadowRoot.getElementById("hover-marker");
    const readout = this.shadowRoot.getElementById("readout");
    if (!meta || !marker || !readout) {
      return;
    }
    // Skip redundant DOM writes: most pointer moves stay inside one slot,
    // and each write would re-announce the aria-live readout.
    if (index === this._shownSlot) {
      return;
    }
    this._shownSlot = index;
    this._kbIndex = index;
    const nearest = meta.points[index];
    if (!nearest) {
      return;
    }
    const cx = meta.x(nearest.time);
    marker.innerHTML = `
      <line x1="${cx}" y1="${meta.margin.top}" x2="${cx}"
        y2="${meta.margin.top + meta.plotH + meta.lanesH}"
        stroke="var(--secondary-text-color)" stroke-width="1"
        stroke-dasharray="3 3"/>
      <circle cx="${cx}" cy="${meta.y(nearest.soc)}" r="3"
        fill="var(--primary-color, #03a9f4)"/>`;
    const fmt = new Intl.DateTimeFormat(meta.lang, {
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
    const t = (key) => localize(this._hass, key);
    // Which lanes (surplus loads, appliances, feed-in) cover the shown slot?
    // Blocks are [start, end); the crosshair snaps to `nearest`, so test that
    // point for membership to stay consistent with the marker the user sees.
    const covering = (lane) =>
      (lane.schedule || []).find((b) => {
        const s = new Date(b.start).getTime();
        const e = new Date(b.end).getTime();
        return (
          Number.isFinite(s) &&
          Number.isFinite(e) &&
          nearest.time >= s &&
          nearest.time < e
        );
      });
    const activeLanes = (meta.lanes || []).filter((lane) => covering(lane));
    const when = esc(`${fmt.format(nearest.time)} · ${nearest.soc} %`);
    const chips = activeLanes
      .map((lane) => {
        const block = covering(lane);
        if (lane.kind === "cascade") {
          const wh = num(block?.root_input_wh) ?? num(block?.wh);
          const energy = wh != null ? ` ${Math.round(wh)} Wh` : "";
          return `<span class="chip"><span class="dot" style="background:${lane.color}"></span>${esc(
            lane.name ?? "?"
          )}: ${t("root")}${energy}</span>`;
        }
        // Energy of THIS hour per lane (operator ask 2026-08-03): a load
        // carries it on the covering schedule block. Feed-in blocks do too:
        // their power lives on the slot-ending forecast point, so deriving it
        // from `nearest` would shift every tooltip by one slot. Appliance
        // lanes have no per-slot figure and stay name-only.
        let wh = null;
        if (lane.kind === "feedin") {
          const booked = num(block?.wh);
          if (booked != null && booked > 0) {
            wh = booked;
          }
        } else if (lane.kind !== "appliance") {
          const booked = num(block?.wh);
          if (booked != null && booked > 0) {
            wh = booked;
          }
        }
        const energy = wh != null ? ` ${Math.round(wh)} Wh` : "";
        return `<span class="chip"><span class="dot" style="background:${lane.color}"></span>${esc(
          lane.name ?? "?"
        )}${energy}</span>`;
      })
      .join("");
    readout.innerHTML = chips ? `${when} · ${chips}` : when;
  }

  _clearSlot() {
    this._shownSlot = null;
    const marker = this.shadowRoot?.getElementById("hover-marker");
    const readout = this.shadowRoot?.getElementById("readout");
    if (marker) {
      marker.innerHTML = "";
    }
    if (readout) {
      readout.innerHTML = "&nbsp;";
    }
  }
}

// ---------------------------------------------------------------------------
// Consumption forecast card (v0.25.5, operator request 2026-08-08)
//
// Stacked hourly bars per voltage level (230 V AC / 48 V / 24 V) with the
// planned surplus loads as their own top layer and a total line. Reads the
// `consumption_forecast` attribute of the same SOC-forecast sensor:
// [{t, ac_w, dc48_w, dc24_w, loads_w, src}, ...] — src is the per-path
// origin "L/S" (learned/static); slots with a static fallback render dimmed.
// ---------------------------------------------------------------------------

const CONSUMPTION_CARD_TYPE = "battery-manager-consumption-card";

// Layer palette, theme-overridable like the load colors above (fallbacks
// >= 3:1 contrast on light and dark card backgrounds).
const AC_COLOR = "var(--bmpc-ac-color, #1e88e5)";
const DC48_LAYER_COLOR = "var(--bmpc-dc48-layer-color, #7e57c2)";
const DC24_LAYER_COLOR = "var(--bmpc-dc24-layer-color, #009688)";
const PLANNED_LAYER_COLOR = "var(--bmpc-planned-layer-color, #ef6c00)";

function isConsumptionEntity(stateObj) {
  const cf = stateObj?.attributes?.consumption_forecast;
  return (
    Array.isArray(cf) &&
    cf.length > 1 &&
    typeof cf[0] === "object" &&
    cf[0] !== null &&
    "t" in cf[0] &&
    "ac_w" in cf[0]
  );
}

function findConsumptionEntity(hass, entities) {
  const candidates = (entities || []).filter(
    (id) => id.startsWith("sensor.") && isConsumptionEntity(hass.states[id])
  );
  return (
    candidates.find((id) => id.includes("soc_forecast")) || candidates[0] || ""
  );
}

class BatteryManagerConsumptionCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = undefined;
    this._hass = undefined;
    this._lastState = undefined;
    this._width = 0;
    this._chartMeta = null;
    this._kbIndex = null;
    this._shownSlot = null;
    this._rafId = null;
    this._pointerEv = null;
    this._resizeObserver = new ResizeObserver(() => {
      const width = this.getBoundingClientRect().width;
      if (width && Math.abs(width - this._width) > 4) {
        this._width = width;
        this._render();
      }
    });
  }

  connectedCallback() {
    this._resizeObserver.observe(this);
  }

  disconnectedCallback() {
    this._resizeObserver.disconnect();
    this._cancelPendingFrame();
  }

  setConfig(config) {
    if (!config || typeof config !== "object") {
      throw new Error(localize(this._hass, "invalid_config"));
    }
    if (config.entity != null && typeof config.entity !== "string") {
      throw new Error(
        `${CONSUMPTION_CARD_TYPE}: ${localize(this._hass, "invalid_entity")}`
      );
    }
    if (
      config.hours != null &&
      (typeof config.hours !== "number" || !Number.isFinite(config.hours))
    ) {
      throw new Error(`${CONSUMPTION_CARD_TYPE}: ${localize(this._hass, "invalid_hours")}`);
    }
    this._config = { hours: 48, ...config };
    this._lastState = undefined;
    this._render();
  }

  set hass(hass) {
    const languageChanged = hass.language !== this._hass?.language;
    this._hass = hass;
    const stateObj = this._config?.entity
      ? hass.states[this._config.entity]
      : undefined;
    if (stateObj !== this._lastState || languageChanged) {
      this._lastState = stateObj;
      this._render();
    }
  }

  getCardSize() {
    return 4;
  }

  getGridOptions() {
    return { rows: 4, columns: 12, min_rows: 3, min_columns: 6 };
  }

  static getStubConfig(hass, entities, entitiesFallback) {
    return {
      entity:
        findConsumptionEntity(hass, entities) ||
        findConsumptionEntity(hass, entitiesFallback),
    };
  }

  static getConfigForm() {
    return {
      computeLabel: (schema) => localize(null, `field_${schema.name}`),
      schema: [
        {
          name: "entity",
          required: true,
          selector: { entity: { domain: "sensor" } },
        },
        { name: "title", selector: { text: {} } },
        {
          name: "hours",
          default: 48,
          selector: { number: { min: 6, max: 96, step: 1, mode: "box" } },
        },
      ],
    };
  }

  _message(text) {
    return `<div class="msg">${text}</div>`;
  }

  _render() {
    try {
      this._renderInner();
    } catch (err) {
      console.error(`[${CONSUMPTION_CARD_TYPE}] render failed:`, err);
      this._renderError(err);
    }
  }

  _renderError(err) {
    if (!this.shadowRoot) {
      return;
    }
    const detail = err instanceof Error ? err.message : String(err);
    this.shadowRoot.innerHTML = `
      <style>
        ha-card { display: block; padding: 12px 16px; }
        .error { color: var(--error-color, #db4437); }
      </style>
      <ha-card>
        <span class="error">${esc(localize(this._hass, "render_error"))} ${esc(
          detail
        )}</span>
      </ha-card>
    `;
  }

  _renderInner() {
    if (!this.shadowRoot || !this._config) {
      return;
    }
    this._kbIndex = null;
    this._shownSlot = null;
    this._statsText = "";
    const hass = this._hass;
    const t = (key) => localize(hass, key);

    let body;
    let header = this._config.title;
    const stateObj = this._config.entity
      ? hass?.states?.[this._config.entity]
      : undefined;

    if (!this._config.entity) {
      body = this._message(t("no_entity"));
    } else if (!stateObj) {
      body = this._message(`${t("not_found")} ${esc(this._config.entity)}`);
    } else if (!isConsumptionEntity(stateObj)) {
      body = this._message(t("no_consumption"));
    } else {
      header =
        this._config.title ??
        t("chart_label_consumption");
      body = this._renderChart(stateObj, t);
    }

    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { padding: 12px 12px 8px; }
        .header {
          display: flex; flex-wrap: wrap; align-items: baseline;
          justify-content: space-between; gap: 4px 12px; padding: 0 4px 6px;
        }
        .title {
          font-size: 1.1em; font-weight: 500;
          color: var(--primary-text-color);
        }
        .stats {
          font-size: 0.85em; color: var(--secondary-text-color);
        }
        .msg { padding: 24px 8px; color: var(--secondary-text-color); }
        svg { display: block; }
        svg:focus { outline: none; }
        svg:focus-visible {
          outline: 2px solid var(--primary-color, #03a9f4);
          outline-offset: 2px; border-radius: 2px;
        }
        .visually-hidden {
          position: absolute; width: 1px; height: 1px; margin: -1px;
          padding: 0; overflow: hidden; clip: rect(0 0 0 0);
          clip-path: inset(50%); white-space: nowrap; border: 0;
        }
        .legend {
          display: flex; flex-wrap: wrap; gap: 2px 14px;
          padding: 6px 4px 0; font-size: 0.8em;
          color: var(--secondary-text-color);
        }
        .legend .dot {
          display: inline-block; width: 8px; height: 8px;
          border-radius: 50%; margin-right: 4px;
        }
        .readout {
          font-size: 0.8em; color: var(--secondary-text-color);
          text-align: right; min-height: 1.2em; padding: 2px 4px 0;
        }
        .readout .chip { margin-left: 8px; white-space: nowrap; }
        .readout .dot {
          display: inline-block; width: 8px; height: 8px;
          border-radius: 50%; margin-right: 4px;
        }
      </style>
      <ha-card>
        <div class="header">
          <div class="title">${esc(header ?? "")}</div>
          <div class="stats">${this._statsText || ""}</div>
        </div>
        ${body}
      </ha-card>
    `;
    this._attachChartHandlers();
  }

  _renderChart(stateObj, t) {
    const a = stateObj.attributes;
    const lang = this._hass?.language || "en";

    let points = a.consumption_forecast
      .filter((p) => p && p.t != null)
      .map((p) => ({
        time: new Date(p.t).getTime(),
        ac: num(p.ac_w) ?? 0,
        dc48: num(p.dc48_w) ?? 0,
        dc24: num(p.dc24_w) ?? 0,
        loads: num(p.loads_w) ?? 0,
        learned: typeof p.src === "string" && p.src === "L/L",
      }))
      .filter((p) => Number.isFinite(p.time));
    if (points.length < 2) {
      return this._message(t("no_data"));
    }
    const horizonMs = Number(this._config.hours) * 3600 * 1000;
    if (horizonMs > 0) {
      const cutoff = points[0].time + horizonMs;
      const capped = points.filter((p) => p.time <= cutoff);
      if (capped.length >= 2) {
        points = capped;
      }
    }
    if (points.length > MAX_POINTS) {
      const stride = Math.ceil(points.length / MAX_POINTS);
      points = points.filter(
        (_, i, arr) => i % stride === 0 || i === arr.length - 1
      );
    }
    // Slot durations from the gaps to the next slot start (slot 0 is a
    // partial hour); the last slot reuses the previous duration.
    const durs = points.map((p, i) =>
      i + 1 < points.length
        ? (points[i + 1].time - p.time) / 3600000
        : (p.time - points[i - 1].time) / 3600000
    );
    const t0 = points[0].time;
    const t1 = points[points.length - 1].time + durs[durs.length - 1] * 3600000;
    if (t1 <= t0) {
      return this._message(t("no_data"));
    }

    const width = Math.max(this._width || this.clientWidth || 320, 280);
    const margin = { top: 8, right: 12, bottom: 16, left: 40 };
    const plotH = 120;
    const height = margin.top + plotH + margin.bottom;
    const plotW = width - margin.left - margin.right;

    const totals = points.map((p) => p.ac + p.dc48 + p.dc24 + p.loads);
    // Nice ceiling in 100 W steps so the y labels stay round.
    const yMax = Math.max(100, Math.ceil((Math.max(...totals) * 1.1) / 100) * 100);

    const x = (time) =>
      margin.left + ((time - t0) / (t1 - t0)) * plotW;
    const y = (w) => margin.top + (1 - w / yMax) * plotH;

    const line = "var(--divider-color, #e0e0e0)";
    const text = "var(--secondary-text-color, #727272)";
    const totalColor = "var(--primary-text-color, #212121)";

    const layers = [
      { key: "ac", name: t("level_ac"), color: AC_COLOR },
      { key: "dc48", name: t("level_dc48"), color: DC48_LAYER_COLOR },
      { key: "dc24", name: t("level_dc24"), color: DC24_LAYER_COLOR },
      { key: "loads", name: t("planned_loads"), color: PLANNED_LAYER_COLOR },
    ];

    const svg = [];

    // Horizontal grid + y labels (W)
    for (const frac of [0, 0.25, 0.5, 0.75, 1]) {
      const w = Math.round(yMax * frac);
      svg.push(
        `<line x1="${margin.left}" y1="${y(w)}" x2="${width - margin.right}"
          y2="${y(w)}" stroke="${line}" stroke-width="1"/>`,
        `<text x="${margin.left - 5}" y="${y(w) + 3}" text-anchor="end"
          font-size="9" fill="${text}">${w}</text>`
      );
    }
    svg.push(
      `<text x="${margin.left - 5}" y="${margin.top - 2}" text-anchor="end"
        font-size="8" fill="${text}">W</text>`
    );

    // Vertical grid: day boundaries (labelled) and 6-hour ticks
    const dayFmt = new Intl.DateTimeFormat(lang, { weekday: "short" });
    for (
      let tick = new Date(t0).setMinutes(0, 0, 0) + 3600 * 1000;
      tick <= t1;
      tick += 3600 * 1000
    ) {
      const hour = new Date(tick).getHours();
      if (hour === 0) {
        svg.push(
          `<line x1="${x(tick)}" y1="${margin.top}" x2="${x(tick)}"
            y2="${margin.top + plotH}" stroke="${line}" stroke-width="1.5"/>`,
          `<text x="${x(tick) + 3}" y="${height - 4}" font-size="9"
            fill="${text}">${dayFmt.format(tick)}</text>`
        );
      } else if (hour % 6 === 0) {
        svg.push(
          `<line x1="${x(tick)}" y1="${margin.top}" x2="${x(tick)}"
            y2="${margin.top + plotH}" stroke="${line}" stroke-width="1"
            stroke-dasharray="2 3" opacity="0.7"/>`,
          `<text x="${x(tick)}" y="${height - 4}" font-size="9"
            text-anchor="middle" fill="${text}">${hour}</text>`
        );
      }
    }

    // Stacked bars; slots on the static fallback profile render dimmed.
    const barMeta = [];
    points.forEach((p, i) => {
      const x0 = x(p.time);
      const bw = Math.max(x(p.time + durs[i] * 3600000) - x0 - 1, 1);
      barMeta.push({ cx: x0 + bw / 2 });
      const opacity = p.learned ? 0.88 : 0.35;
      svg.push(`<g opacity="${opacity}">`);
      let cum = 0;
      for (const layer of layers) {
        const v = p[layer.key];
        if (v > 0.05) {
          svg.push(
            `<rect x="${x0.toFixed(1)}" y="${y(cum + v).toFixed(1)}"
              width="${bw.toFixed(1)}" height="${(y(cum) - y(cum + v)).toFixed(
                1
              )}" fill="${layer.color}"/>`
          );
        }
        cum += v;
      }
      svg.push("</g>");
    });

    // Total line through the bar-top midpoints.
    const totalCoords = points
      .map((p, i) => `${barMeta[i].cx.toFixed(1)},${y(totals[i]).toFixed(1)}`)
      .join(" ");
    svg.push(
      `<polyline points="${totalCoords}" fill="none" stroke="${totalColor}"
        stroke-width="1.5" stroke-linejoin="round"/>`
    );

    // "now" marker at the left edge of slot 0.
    svg.push(
      `<line x1="${x(t0)}" y1="${margin.top}" x2="${x(t0)}"
        y2="${margin.top + plotH}" stroke="${text}" stroke-width="1"
        stroke-dasharray="3 3"/>`,
      `<text x="${x(t0) + 3}" y="${margin.top + 8}" font-size="9"
        fill="${text}">${t("now")} ${Math.round(totals[0])} W</text>`
    );

    // Hover overlay target (events attached after innerHTML assignment)
    svg.push(
      `<rect id="hover-target" x="${margin.left}" y="${margin.top}"
        width="${plotW}" height="${plotH}" fill="transparent"/>`,
      `<g id="hover-marker"></g>`
    );

    this._chartMeta = { points, durs, totals, barMeta, x, margin, plotH, t0, t1, lang };

    // Per-day kWh sums (today / tomorrow) per layer, from W x slot hours.
    const dayKey = (time) => new Date(time).toDateString();
    const day0 = dayKey(t0);
    let day1 = null;
    const sums = {};
    points.forEach((p, i) => {
      const key = dayKey(p.time);
      if (key !== day0 && day1 === null) {
        day1 = key;
      }
      const bucket = key === day0 ? "today" : key === day1 ? "tomorrow" : null;
      if (!bucket) {
        return;
      }
      for (const layer of layers) {
        sums[`${layer.key}_${bucket}`] =
          (sums[`${layer.key}_${bucket}`] || 0) + p[layer.key] * durs[i] / 1000;
      }
      sums[`total_${bucket}`] =
        (sums[`total_${bucket}`] || 0) + totals[i] * durs[i] / 1000;
    });
    const perDay = (key) =>
      `${(sums[`${key}_today`] || 0).toFixed(1)}/${(
        sums[`${key}_tomorrow`] || 0
      ).toFixed(1)}`;
    this._statsText = `${t("total")} ${perDay("total")} kWh ${t(
      "today_tomorrow"
    )}`;

    const anyStatic = points.some((p) => !p.learned);
    const legend =
      layers
        .map(
          (layer) =>
            `<span><span class="dot" style="background:${layer.color}"></span>${esc(
              layer.name
            )} (${perDay(layer.key)})</span>`
        )
        .join("") +
      `<span><span class="dot" style="background:${totalColor}"></span>${esc(
        t("total")
      )} (${perDay("total")}) kWh</span>` +
      (anyStatic ? `<span class="off">${esc(t("static_hint"))}</span>` : "");

    // Screen-reader summary (same pattern as the SOC chart).
    let maxP = points[0];
    let maxTotal = totals[0];
    const whenFmt = new Intl.DateTimeFormat(lang, {
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
    points.forEach((p, i) => {
      if (totals[i] > maxTotal) {
        maxTotal = totals[i];
        maxP = p;
      }
    });
    const summary =
      `${t("chart_label_consumption")}: ${t("now")} ${Math.round(
        totals[0]
      )} W, ` +
      `${t("sr_max")} ${Math.round(maxTotal)} W (${whenFmt.format(
        maxP.time
      )}).`;

    return `
      <svg id="chart" role="img" tabindex="0" aria-label="${esc(summary)}"
        width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
        ${svg.join("\n")}
      </svg>
      <div class="readout" id="readout" aria-live="polite">&nbsp;</div>
      <div class="visually-hidden">${esc(summary)} ${esc(
        this._statsText
      )} ${esc(t("kbd_hint"))}</div>
      <div class="legend">${legend}</div>
    `;
  }

  // ------------------------------------------------------------------
  // Hover crosshair + keyboard exploration (same pattern as the SOC chart)
  // ------------------------------------------------------------------

  _attachChartHandlers() {
    const target = this.shadowRoot.getElementById("hover-target");
    if (!target || !this._chartMeta) {
      return;
    }
    target.addEventListener("pointermove", (ev) => this._onPointerMove(ev));
    target.addEventListener("pointerleave", () => {
      this._cancelPendingFrame();
      this._clearSlot();
    });
    const svg = this.shadowRoot.getElementById("chart");
    if (svg) {
      svg.addEventListener("keydown", (ev) => this._onKeyDown(ev));
    }
  }

  _onPointerMove(ev) {
    this._pointerEv = ev;
    if (this._rafId == null) {
      this._rafId = requestAnimationFrame(() => {
        this._rafId = null;
        const pending = this._pointerEv;
        this._pointerEv = null;
        this._onHover(pending);
      });
    }
  }

  _cancelPendingFrame() {
    if (this._rafId != null) {
      cancelAnimationFrame(this._rafId);
      this._rafId = null;
    }
    this._pointerEv = null;
  }

  _onKeyDown(ev) {
    const meta = this._chartMeta;
    if (!meta) {
      return;
    }
    const last = meta.points.length - 1;
    let index = this._kbIndex;
    switch (ev.key) {
      case "ArrowLeft":
        index = index == null ? 0 : Math.max(0, index - 1);
        break;
      case "ArrowRight":
        index = index == null ? 0 : Math.min(last, index + 1);
        break;
      case "Home":
        index = 0;
        break;
      case "End":
        index = last;
        break;
      case "Escape":
        this._clearSlot();
        return;
      default:
        return;
    }
    ev.preventDefault();
    this._showSlot(index);
  }

  _onHover(ev) {
    const meta = this._chartMeta;
    const marker = this.shadowRoot.getElementById("hover-marker");
    if (!meta || !marker) {
      return;
    }
    const svg = marker.ownerSVGElement;
    const rect = svg.getBoundingClientRect();
    if (!(rect.width > 0)) {
      return;
    }
    const px = ((ev.clientX - rect.left) / rect.width) * svg.viewBox.baseVal.width;
    const time =
      meta.t0 +
      ((px - meta.margin.left) /
        (svg.viewBox.baseVal.width - meta.margin.left - 10)) *
        (meta.t1 - meta.t0);
    let nearest = 0;
    for (let i = 1; i < meta.points.length; i++) {
      if (
        Math.abs(meta.points[i].time - time) <
        Math.abs(meta.points[nearest].time - time)
      ) {
        nearest = i;
      }
    }
    this._showSlot(nearest);
  }

  _showSlot(index) {
    const meta = this._chartMeta;
    const marker = this.shadowRoot.getElementById("hover-marker");
    const readout = this.shadowRoot.getElementById("readout");
    if (!meta || !marker || !readout) {
      return;
    }
    if (index === this._shownSlot) {
      return;
    }
    this._shownSlot = index;
    this._kbIndex = index;
    const p = meta.points[index];
    if (!p) {
      return;
    }
    const cx = meta.barMeta[index].cx;
    marker.innerHTML = `
      <line x1="${cx}" y1="${meta.margin.top}" x2="${cx}"
        y2="${meta.margin.top + meta.plotH}"
        stroke="var(--secondary-text-color)" stroke-width="1"
        stroke-dasharray="3 3"/>`;
    const fmt = new Intl.DateTimeFormat(meta.lang, {
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
    const t = (key) => localize(this._hass, key);
    const chips = [
      [t("level_ac"), AC_COLOR, p.ac],
      [t("level_dc48"), DC48_LAYER_COLOR, p.dc48],
      [t("level_dc24"), DC24_LAYER_COLOR, p.dc24],
      [t("planned_loads"), PLANNED_LAYER_COLOR, p.loads],
      [t("total"), "var(--primary-text-color, #212121)", meta.totals[index]],
    ]
      .filter(([, , v]) => v > 0.05)
      .map(
        ([name, color, v]) =>
          `<span class="chip"><span class="dot" style="background:${color}"></span>${esc(
            name
          )} ${Math.round(v)} W</span>`
      )
      .join("");
    const when = esc(`${fmt.format(p.time)}`);
    readout.innerHTML = chips ? `${when} · ${chips}` : when;
  }

  _clearSlot() {
    this._shownSlot = null;
    const marker = this.shadowRoot?.getElementById("hover-marker");
    const readout = this.shadowRoot?.getElementById("readout");
    if (marker) {
      marker.innerHTML = "";
    }
    if (readout) {
      readout.innerHTML = "&nbsp;";
    }
  }
}

if (!customElements.get(CARD_TYPE)) {
  customElements.define(CARD_TYPE, BatteryManagerForecastCard);

  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CARD_TYPE,
    get name() { return localize(null, "card_forecast"); },
    get description() { return localize(null, "desc_forecast"); },
    preview: true,
    documentationURL: DOCS_URL,
    // HA 2026.6+ entity-first card picker: suggest this card whenever the
    // user selects a sensor that carries a Battery Manager forecast curve.
    getEntitySuggestion: (hass, entityId) => {
      if (
        entityId.startsWith("sensor.") &&
        isForecastEntity(hass.states[entityId])
      ) {
        return { config: { type: `custom:${CARD_TYPE}`, entity: entityId } };
      }
      return null;
    },
  });

  console.info(
    `%c BATTERY-MANAGER-FORECAST-CARD %c v${CARD_VERSION} `,
    "background: #43a047; color: white; font-weight: 600;",
    "background: #eee; color: #333;"
  );
}

if (!customElements.get(CONSUMPTION_CARD_TYPE)) {
  customElements.define(
    CONSUMPTION_CARD_TYPE,
    BatteryManagerConsumptionCard
  );

  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CONSUMPTION_CARD_TYPE,
    get name() { return localize(null, "card_consumption"); },
    get description() { return localize(null, "desc_consumption"); },
    preview: true,
    documentationURL: DOCS_URL,
    getEntitySuggestion: (hass, entityId) => {
      if (
        entityId.startsWith("sensor.") &&
        isConsumptionEntity(hass.states[entityId])
      ) {
        return {
          config: { type: `custom:${CONSUMPTION_CARD_TYPE}`, entity: entityId },
        };
      }
      return null;
    },
  });
}

class BatteryManagerCascadeCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = undefined;
    this._hass = undefined;
    this._lastState = undefined;
    this._width = 0;
    this._charts = [];
    this._resizeObserver = new ResizeObserver(() => {
      const width = this.getBoundingClientRect().width;
      if (width && Math.abs(width - this._width) > 4) {
        this._width = width;
        this._render();
      }
    });
  }

  connectedCallback() {
    this._resizeObserver.observe(this);
  }

  disconnectedCallback() {
    this._resizeObserver.disconnect();
  }

  setConfig(config) {
    if (!config || typeof config !== "object") {
      throw new Error(localize(this._hass, "invalid_config"));
    }
    if (config.entity != null && typeof config.entity !== "string") {
      throw new Error(
        `${CASCADE_CARD_TYPE}: ${localize(this._hass, "invalid_entity")}`
      );
    }
    if (
      config.hours != null &&
      (typeof config.hours !== "number" || !Number.isFinite(config.hours))
    ) {
      throw new Error(`${CASCADE_CARD_TYPE}: ${localize(this._hass, "invalid_hours")}`);
    }
    this._config = { hours: 48, ...config };
    this._lastState = undefined;
    this._render();
  }

  set hass(value) {
    const languageChanged = value.language !== this._hass?.language;
    this._hass = value;
    const state = value.states[this._entityId()];
    if (state !== this._lastState || languageChanged) {
      this._lastState = state;
      this._render();
    }
  }

  getCardSize() {
    const rows = this._cascades().reduce(
      (sum, cascade) =>
        sum + 2 + 3 * this._memberDetails(cascade).length,
      0
    );
    return Math.max(3, 2 + Math.ceil(rows / 3));
  }

  getGridOptions() {
    return { rows: "auto", columns: 12, min_rows: 4, min_columns: 6 };
  }

  static getStubConfig(hass, entities, entitiesFallback) {
    return {
      entity:
        findForecastEntity(hass, entities) ||
        findForecastEntity(hass, entitiesFallback),
      hours: 48,
    };
  }

  static getConfigForm() {
    return {
      computeLabel: (schema) => localize(null, `field_${schema.name}`),
      schema: [
        {
          name: "entity",
          required: true,
          selector: { entity: { domain: "sensor" } },
        },
        { name: "title", selector: { text: {} } },
        {
          name: "hours",
          default: 48,
          selector: { number: { min: 6, max: 96, step: 1, mode: "box" } },
        },
      ],
    };
  }

  _entityId() {
    if (this._config?.entity) return this._config.entity;
    if (!this._hass) return "";
    // Compatibility for cards created before v0.29.0: those picker entries
    // carried only `type`. Auto-discovery makes them useful immediately while
    // the editor now persists an explicit entity for new cards.
    return findForecastEntity(this._hass, Object.keys(this._hass.states));
  }

  _cascades() {
    const state = this._hass?.states?.[this._entityId()];
    const cascades = state?.attributes?.cascades;
    return Array.isArray(cascades) ? cascades.filter((c) => c && typeof c === "object").slice(0, 20) : [];
  }

  _memberDetails(cascade) {
    const explicit = Array.isArray(cascade?.member_details)
      ? cascade.member_details.filter(
          (item) => item && typeof item === "object" && item.load_id
        )
      : [];
    if (explicit.length) return explicit.slice(0, 12);
    const names = new Map();
    for (const block of Array.isArray(cascade?.schedule)
      ? cascade.schedule
      : []) {
      for (const activity of Array.isArray(block?.activities)
        ? block.activities
        : []) {
        if (
          activity?.load_id &&
          activity.load_id !== cascade?.terminal_load_id &&
          activity.kind !== "terminal"
        ) {
          names.set(activity.load_id, activity.name || activity.load_id);
        }
      }
    }
    for (const id of Array.isArray(cascade?.members) ? cascade.members : []) {
      if (typeof id === "string" && !names.has(id)) names.set(id, id);
    }
    return [...names].slice(0, 12).map(([load_id, name]) => ({
      load_id,
      name,
    }));
  }

  _text(de, en) {
    return uiLanguage(this._hass).toLowerCase().split(/[-_]/)[0] === "de" ? de : en;
  }

  _number(value, digits = 2) {
    return value == null ? "—" : new Intl.NumberFormat(this._hass?.language || "en", {
      minimumFractionDigits: digits, maximumFractionDigits: digits,
    }).format(value);
  }

  _time(time, date = false) {
    return new Intl.DateTimeFormat(this._hass?.language || "en", {
      timeZone: this._hass?.config?.time_zone,
      ...(date ? { weekday: "short", day: "2-digit", month: "2-digit" } : {}),
      hour: "2-digit", minute: "2-digit",
    }).format(time);
  }

  _day(time) {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: this._hass?.config?.time_zone,
      year: "numeric", month: "2-digit", day: "2-digit",
    }).formatToParts(time);
    return ["year", "month", "day"].map((key) => parts.find((p) => p.type === key).value).join("-");
  }

  _timestamp(value) {
    // Backend slots are naive HA-local timestamps; the viewing browser can
    // live in a different timezone. Offset-bearing ISO timestamps stay exact.
    if (typeof value === "string" && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(value)) {
      return this._localTimestamp(Date.parse(`${value}Z`));
    }
    return value == null ? NaN : new Date(value).getTime();
  }

  _dayStart(day) {
    return this._localTimestamp(Date.parse(`${day}T00:00:00Z`));
  }

  _localTimestamp(target) {
    // Calendar boundaries in HA's timezone, including 23/25-hour DST days.
    if (!Number.isFinite(target)) return NaN;
    let value = target;
    for (let i = 0; i < 3; i++) {
      const parts = new Intl.DateTimeFormat("en-CA", {
        timeZone: this._hass?.config?.time_zone, year: "numeric", month: "2-digit",
        day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", fractionalSecondDigits: 3, hourCycle: "h23",
      }).formatToParts(value);
      const p = Object.fromEntries(parts.map((part) => [part.type, part.value]));
      value += target - Date.parse(`${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}.${p.fractionalSecond}Z`);
    }
    return value;
  }

  _window(period = "all") {
    if (period === "all") return [-Infinity, Infinity];
    const today = this._day(Date.now());
    const date = new Date(`${today}T12:00:00Z`);
    if (period === "tomorrow") date.setUTCDate(date.getUTCDate() + 1);
    const start = this._dayStart(date.toISOString().slice(0, 10));
    date.setUTCDate(date.getUTCDate() + 1);
    return [start, this._dayStart(date.toISOString().slice(0, 10))];
  }

  _horizon(cascade, period) {
    const starts = [...(Array.isArray(cascade.schedule) ? cascade.schedule : []).map((b) => this._timestamp(b?.start)),
      ...this._memberDetails(cascade).flatMap((m) => this._points(m).map((p) => p.time))].filter(Number.isFinite);
    const start = starts.length ? Math.min(...starts) : Date.now();
    const [from, until] = this._window(period);
    return [Math.max(from, start), Math.min(until, start + Math.max(6, Math.min(96, this._config.hours)) * 3600000)];
  }

  _blocks(cascade, period = "all") {
    const [from, until] = this._horizon(cascade, period);
    return (Array.isArray(cascade?.schedule) ? cascade.schedule : [])
      .flatMap((block) => {
        const start = this._timestamp(block?.start);
        const end = this._timestamp(block?.end);
        if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) return [];
        const a = Math.max(start, from), b = Math.min(end, until);
        if (b <= a) return [];
        return [{ ...block, start: a, end: b, fraction: (b - a) / (end - start),
          activities: (Array.isArray(block.activities) ? block.activities : []).filter((v) => v && typeof v === "object") }];
      }).sort((a, b) => a.start - b.start).slice(0, MAX_BLOCKS);
  }

  _energy(block, kind, id, field = "energy_wh") {
    if (kind === "root") return num(block.root_input_wh) == null ? null : num(block.root_input_wh) * block.fraction;
    const activities = block.activities.filter((a) => (kind === "aux" ? a.kind === "terminal" && a.source === "aux" : a.kind === kind) && (id == null || a.load_id === id));
    if (activities.some((a) => num(a[field]) == null)) return null;
    return activities.reduce((sum, a) => sum + num(a[field]), 0) * block.fraction;
  }

  _kwh(wh) {
    return wh == null ? null : wh / 1000;
  }

  _total(blocks, kind, id, field) {
    const values = blocks.map((b) => this._energy(b, kind, id, field));
    return values.some((v) => v == null) ? null : values.reduce((sum, v) => sum + v, 0);
  }

  _flowList(blocks, cascade) {
    const flows = new Map();
    const add = (key, label, wh, color) => {
      const previous = flows.get(key);
      flows.set(key, { label, color, wh: wh == null || previous?.wh === null ? null : (previous?.wh || 0) + wh });
    };
    for (const block of blocks) {
      for (const a of block.activities) {
        const name = a.name || a.load_id || "?";
        const wh = num(a.energy_wh) == null ? null : num(a.energy_wh) * block.fraction;
        if (a.kind === "charge") {
          add(`charge:${a.load_id}`, `${localize(this._hass, "root")} → ${name}`, wh, CASCADE_CHARGE_COLOR);
          add(`stored:${a.load_id}`, `${name} · ${this._text("im Akku gespeichert", "stored in battery")}`,
            num(a.stored_energy_wh) == null ? null : num(a.stored_energy_wh) * block.fraction, CASCADE_CHARGE_COLOR);
        } else if (a.kind === "discharge") {
          add(`discharge:${a.load_id}`, `${name} · ${this._text("Akkuentnahme inkl. Verlusten", "battery withdrawal incl. losses")}`, wh, CASCADE_DISCHARGE_COLOR);
        } else if (a.kind === "terminal") {
          const source = a.source === "root" ? localize(this._hass, "root") : a.source_name ||
            this._memberDetails(cascade).find((m) => m.load_id === a.source_load_id)?.name ||
            this._text("Speicher (Quelle unbekannt)", "Storage (unknown source)");
          add(`terminal:${a.source}:${a.source_load_id}`, `${source} → ${name}`, wh, CASCADE_TERMINAL_COLOR);
        }
      }
    }
    const root = this._total(blocks, "root");
    const charge = this._total(blocks, "charge");
    const rootDeliveries = blocks.flatMap((b) => b.activities
      .filter((a) => a.kind === "terminal" && a.source === "root")
      .map((a) => num(a.energy_wh) == null ? null : num(a.energy_wh) * b.fraction));
    const terminalRoot = rootDeliveries.some((wh) => wh == null) ? null : rootDeliveries.reduce((sum, wh) => sum + wh, 0);
    if (root != null && charge != null && terminalRoot != null && root - charge - terminalRoot > 0.5) {
      add("overhead", this._text("Eingang → AC-Eigenbedarf / Rundungsrest", "Root → AC overhead / rounding residual"), root - charge - terminalRoot, CASCADE_OUTPUT_COLOR);
    }
    return [...flows.values()].map((f) => `<li><span class="flow-label"><i style="background:${f.color}"></i>${esc(f.label)}</span><strong>${this._number(f.wh == null ? null : f.wh / 1000)} kWh</strong></li>`).join("");
  }

  _points(member) {
    // Missing forecasts must not become a fabricated constant SOC forecast.
    return (Array.isArray(member?.soc_forecast) ? member.soc_forecast : [])
      .map((p) => ({ time: this._timestamp(p?.t), value: num(p?.soc) }))
      .filter((p) => Number.isFinite(p.time) && p.value != null && p.value >= 0 && p.value <= 100)
      .sort((a, b) => a.time - b.time).slice(0, MAX_POINTS);
  }

  _socAt(points, time) {
    if (!points.length || time < points[0].time || time > points.at(-1).time) return null;
    const next = points.findIndex((p) => p.time >= time);
    if (next === 0 || points[next].time === time) return points[next].value;
    const a = points[next - 1], b = points[next];
    return a.value + (b.value - a.value) * (time - a.time) / (b.time - a.time);
  }

  _series(cascade, kind, id, period, mode) {
    const [from, until] = this._horizon(cascade, period);
    if (until < from) return { points: [], unit: kind === "soc" ? "%" : mode === "energy" ? "kWh" : "W", label: "" };
    if (kind === "soc") {
      const member = this._memberDetails(cascade).find((m) => m.load_id === id);
      const raw = this._points(member);
      const points = raw.filter((p) => p.time >= from && p.time <= until);
      for (const time of [from, until]) {
        const value = this._socAt(raw, time);
        if (Number.isFinite(time) && value != null && !points.some((p) => p.time === time)) points.push({ time, value });
      }
      return { points: points.sort((a, b) => a.time - b.time), unit: "%", label: "SOC", target: num(member?.target_soc_percent) };
    }
    const blocks = this._blocks(cascade, period);
    const points = [];
    let total = 0, previousEnd = null;
    for (const block of blocks) {
      const wh = this._energy(block, kind, id);
      if (wh == null) return { points: [], unit: mode === "energy" ? "kWh" : "W", label: this._text("Keine vollständigen Energiedaten", "Incomplete energy data") };
      if (previousEnd != null && block.start > previousEnd) {
        points.push({ time: previousEnd, value: mode === "energy" ? total / 1000 : 0 },
          { time: block.start, value: mode === "energy" ? total / 1000 : 0 });
      }
      const watts = wh / ((block.end - block.start) / 3600000);
      points.push({ time: block.start, value: mode === "energy" ? total / 1000 : watts });
      total += wh;
      points.push({ time: block.end, value: mode === "energy" ? total / 1000 : watts });
      previousEnd = block.end;
    }
    return { points, blocks, kind, id, mode, unit: mode === "energy" ? "kWh" : "W",
      label: mode === "energy" ? this._text("Energie kumuliert", "Cumulative energy") : this._text("Ø Leistung je Zeitfenster", "Average power per time slot") };
  }

  _valueAt(series, time) {
    if (series.unit === "%") return this._socAt(series.points, time);
    if (!series.points.length || time < series.points[0].time || time > series.points.at(-1).time) return null;
    if (series.mode === "energy") {
      return series.blocks.reduce((sum, b) => sum + this._energy(b, series.kind, series.id) *
        Math.max(0, Math.min(1, (time - b.start) / (b.end - b.start))), 0) / 1000;
    }
    const block = series.blocks.find((b) => time >= b.start && time < b.end);
    return block ? this._energy(block, series.kind, series.id) / ((block.end - block.start) / 3600000) : 0;
  }

  _plot(series, owner, color, compact = false) {
    const index = this._charts.length;
    const points = series.points;
    if (!points.length) return `<p class="muted">${esc(this._text("Keine Prognose für diesen Zeitraum", "No forecast for this period"))}</p>`;
    const t0 = points[0].time, t1 = Math.max(t0 + 1, points.at(-1).time);
    const width = 600, height = compact ? 110 : 210;
    const left = 48, right = 16, top = 18, bottom = height - 28;
    const peak = Math.max(0, ...points.map((p) => p.value));
    const step = series.unit === "W" ? 50 : 0.1;
    const maximum = series.unit === "%" ? 100 : Math.max(step, Math.ceil(peak / step) * step);
    const x = (time) => left + (time - t0) / (t1 - t0) * (width - left - right);
    const y = (value) => bottom - value / maximum * (bottom - top);
    this._charts.push({ ...series, owner, t0, t1, width, left, right, top, bottom, kbIndex: null });
    const ticks = [t0, (t0 + t1) / 2, t1];
    const target = series.target == null ? "" : `<line x1="${left}" x2="${width - right}" y1="${y(series.target)}" y2="${y(series.target)}" class="soc-target"/>`;
    return `<div class="plot"><svg id="chart-${index}" viewBox="0 0 ${width} ${height}" tabindex="0" role="img" aria-label="${esc(`${owner}: ${series.label} · ${this._text("Planung; Pfeiltasten zur Zeitauswahl", "Forecast; arrow keys to select time")}`)}">
      <text x="2" y="${top + 4}" class="axis">${this._number(maximum, series.unit === "kWh" ? 1 : 0)}</text><text x="6" y="${bottom}" class="axis">0</text>
      <line x1="${left}" x2="${width - right}" y1="${bottom}" y2="${bottom}" class="grid"/>${target}
      <polyline points="${points.map((p) => `${x(p.time)},${y(p.value)}`).join(" ")}" fill="none" stroke="${color}" class="forecast-line"/>
      ${ticks.map((time, i) => `<text x="${x(time)}" y="${height - 7}" text-anchor="${i === 0 ? "start" : i === 2 ? "end" : "middle"}" class="axis">${esc(this._time(time))}</text>`).join("")}
      <g id="marker-${index}"></g></svg></div><div id="readout-${index}" class="readout" aria-live="polite">${esc(`${series.label} · ${this._time(t0, true)} – ${this._time(t1, true)} · ${this._text("Planung", "Forecast")}`)}</div>`;
  }

  _showTime(time) {
    this._cursorTime = time;
    this._charts.forEach((chart, index) => {
      const marker = this.shadowRoot.getElementById(`marker-${index}`);
      const readout = this.shadowRoot.getElementById(`readout-${index}`);
      if (!marker || !readout) return;
      const value = this._valueAt(chart, time);
      const px = chart.left + (time - chart.t0) / (chart.t1 - chart.t0) * (chart.width - chart.left - chart.right);
      marker.innerHTML = time < chart.t0 || time > chart.t1 ? "" : `<line x1="${px}" x2="${px}" y1="${chart.top}" y2="${chart.bottom}" class="marker"/>`;
      readout.textContent = `${this._time(time, true)} · ${chart.owner} · ${chart.label}: ${this._number(value, chart.unit === "W" ? 0 : chart.unit === "kWh" ? 3 : 1)} ${chart.unit} · ${this._text("Planung", "Forecast")}`;
    });
  }

  _ui(cascade, index) {
    const key = cascade.cascade_id || `${index}:${cascade.name}`;
    this._views ||= new Map();
    if (!this._views.has(key)) this._views.set(key, { period: "today", detail: null, mode: "power" });
    return this._views.get(key);
  }

  _button(label, index, action, extra = "", selected = false) {
    return `<button type="button" data-cascade="${index}" data-action="${action}" ${extra} aria-pressed="${selected}">${esc(label)}</button>`;
  }

  _groups(blocks) {
    // Adjacent slots with the same source, activities and average powers are
    // one readable phase; retain original slots for exact energy sums.
    const groups = [];
    for (const block of blocks) {
      const hours = (block.end - block.start) / 3600000;
      const signature = JSON.stringify([this._energy(block, "root") / hours, block.activities.map((a) => [a.kind, a.load_id,
        a.source, a.source_load_id, a.sources,
        num(a.energy_wh) == null ? null : Math.round(num(a.energy_wh) * block.fraction / hours * 10),
        num(a.stored_energy_wh) == null ? null : Math.round(num(a.stored_energy_wh) * block.fraction / hours * 10)])]);
      const last = groups.at(-1);
      if (last && last.end === block.start && last.signature === signature &&
          !block.activities.some((a) => a.kind === "transition") && this._day(last.start) === this._day(block.start)) {
        last.end = block.end;
        last.blocks.push(block);
      } else groups.push({ ...block, signature, blocks: [block] });
    }
    return groups;
  }

  _agenda(cascade, index, view) {
    const blocks = this._blocks(cascade, view.period);
    const heading = this._text("Geplanter Ablauf", "Planned sequence");
    return `<div class="section-heading"><h3>${heading}</h3></div>
      ${!blocks.length ? `<p class="muted">${esc(localize(this._hass, "cascade_no_data"))}</p>` : `<ol class="agenda">${this._groups(blocks).map((block) => {
        const outputs = block.activities.filter((a) => a.kind === "output").map((a) => a.name || a.load_id);
        const transitions = block.activities.filter((a) => a.kind === "transition").reduce((sum, a) => sum + (num(a.minutes) || 0) * block.fraction, 0);
        return `<li class="event"><time>${esc(this._time(block.start, true))} – ${esc(this._time(block.end))}</time>
          <div class="event-body"><div class="event-title"><b>${esc(this._text("Energiefluss", "Energy flow"))}</b><span class="badge">${esc(this._text("Planung", "Forecast"))}</span></div>
          <ul class="flows">${this._flowList(block.blocks, cascade)}</ul>
          ${outputs.length ? `<p class="muted">${esc(this._text("AC-Ausgänge aktiv", "AC outputs active"))}: ${esc(outputs.join(" · "))}</p>` : ""}
          ${transitions ? `<p class="muted">${esc(this._text("Quellenwechsel", "Source transition"))}: ${this._number(transitions, 1)} min</p>` : ""}
          </div></li>`;
      }).join("")}</ol>`}`;
  }

  _details(cascade, index, view) {
    if (!view.detail) return "";
    const { kind, id } = view.detail;
    const member = this._memberDetails(cascade).find((m) => m.load_id === id);
    const title = kind === "soc" ? member?.name || id : kind === "root" ? localize(this._hass, "cascade_root_input") :
      kind === "aux" ? this._text("Aus Speichern → Endlast", "From storage → terminal") : cascade.terminal_name || cascade.terminal_load_id || "?";
    const controls = kind === "soc" ? [["soc", "SOC"], ["charge", this._text("Aufnahme", "Charge input")], ["discharge", this._text("Akkuentnahme", "Battery withdrawal")]] : [];
    const metric = kind === "soc" ? view.detail.metric || "soc" : kind;
    const blocks = this._blocks(cascade, view.period);
    return `<section class="details" id="details-${index}" tabindex="-1"><div class="section-heading"><h3>${esc(title)}</h3>${this._button(this._text("Schließen", "Close"), index, "close")}</div>
      <nav>${controls.map(([key, label]) => this._button(label, index, "metric", `data-metric="${key}"`, metric === key)).join("")}
      ${metric !== "soc" ? [["power", this._text("Leistung", "Power")], ["energy", this._text("Energie", "Energy")]].map(([key, label]) => this._button(label, index, "mode", `data-mode="${key}"`, view.mode === key)).join("") : ""}</nav>
      <p class="muted">${esc(this._text("Planung · gestrichelte Kurve. Leistung = Durchschnitt je Zeitfenster; keine Messhistorie.", "Forecast · dashed curve. Power = slot average; no measurement history."))}</p>
      ${this._plot(this._series(cascade, metric, id, view.period, view.mode), title, metric === "soc" ? CASCADE_SOC_COLORS[0] : metric === "charge" ? CASCADE_CHARGE_COLOR : metric === "discharge" ? CASCADE_DISCHARGE_COLOR : CASCADE_ROOT_COLOR)}
      ${metric === "soc" && num(member?.target_soc_percent) != null ? `<p class="muted">${esc(localize(this._hass, "cascade_discharge_target"))}: ${this._number(num(member.target_soc_percent), 0)} %</p>` : ""}
      <h4>${esc(this._text("Quelle → Empfänger · ausgewählter Zeitraum", "Source → recipient · selected period"))}</h4><ul class="flows">${this._flowList(blocks, cascade)}</ul>
      <p class="muted">${esc(this._text("Eingang bezeichnet die Versorgung am Anfang der Kaskade. Pfeile zeigen Quelle und Empfänger entlang der oben dargestellten Kette. AC-Durchleitung ist keine Akkuladung; je AC-Ausgang liegt keine eigene Energiemenge vor. Fehlende Werte: —.", "Root is the cascade input. Arrows show source and recipient along the chain above. AC pass-through is not battery charging; energy per AC output is unavailable. Missing values: —."))}</p></section>`;
  }

  _renderCascade(cascade, index) {
    const view = this._ui(cascade, index);
    const members = this._memberDetails(cascade);
    const blocks = this._blocks(cascade, view.period);
    const phase = cascade.hands_off ? "hands_off" : cascade.fault ? "fault" : cascade.phase || "idle";
    const metrics = [
      [this._text("Aus Speichern · an Endlast", "From storage · to terminal"), this._kwh(this._total(this._blocks(cascade, "all"), "aux")), "aux", "all"],
      [this._text("Eingang heute · Plan", "Root today · forecast"), this._kwh(this._total(this._blocks(cascade, "today"), "root")), "root", "today"],
      [this._text("Eingang morgen · Plan", "Root tomorrow · forecast"), this._kwh(this._total(this._blocks(cascade, "tomorrow"), "root")), "root", "tomorrow"],
    ];
    return `<section class="cascade"><header class="section-heading"><h2>${esc(cascade.name || localize(this._hass, "cascade"))}</h2><span class="badge">${esc(localize(this._hass, STRINGS.en[`cascade_phase_${phase}`] ? `cascade_phase_${phase}` : "cascade_phase_unknown"))}</span></header>
      ${cascade.source_name ? `<p>${esc(localize(this._hass, "source"))}: ${esc(cascade.source_name)}</p>` : ""}
      ${cascade.fault ? `<p class="fault">⚠ ${esc(localize(this._hass, STRINGS.en[`fault_${String(cascade.fault).split(":")[0]}`] ? `fault_${String(cascade.fault).split(":")[0]}` : "fault_unknown"))}${cascade.fault_detail ? ` · ${esc(cascade.fault_detail.entity_id || "")} · ${esc(cascade.fault_detail.observed_state || "?")}` : ""}</p>` : ""}
      <p class="topology">${esc([localize(this._hass, "root"), ...members.map((m) => m.name || m.load_id), cascade.terminal_name || cascade.terminal_load_id || "?"].join(" → "))}</p>
      <div class="metrics">${metrics.map(([label, value, kind, period]) => `<button type="button" data-cascade="${index}" data-action="detail" data-kind="${kind}" data-period="${period}" aria-controls="details-${index}" aria-expanded="${view.detail?.kind === kind && view.period === period}"><span>${esc(label)}</span><strong>${this._number(value)} kWh</strong><small>${esc(this._text("Diagramm öffnen", "Open chart"))} ↗</small></button>`).join("")}</div>
      <p class="muted">${esc(this._text("Heute zeigt den verbleibenden Plan. Aus Speichern bezieht sich auf den gesamten Plan.", "Today shows the remaining plan. From storage refers to the full plan."))}</p>
      ${num(cascade.actual_aux_energy_kwh) != null ? `<p class="muted">${esc(this._text("Ist · heute aus Speichern genutzt", "Actual · used from storage today"))}: ${this._number(num(cascade.actual_aux_energy_kwh))} kWh</p>` : ""}
      <div class="period">${[["today", this._text("Heute", "Today")], ["tomorrow", this._text("Morgen", "Tomorrow")], ["all", this._text("Gesamter Plan", "Full plan")]].map(([key, label]) => this._button(label, index, "period", `data-period="${key}"`, view.period === key)).join("")}</div>
      <div class="members">${members.map((member, mi) => `<article class="member"><div class="section-heading"><h3>${esc(member.name || member.load_id)}</h3><strong>${this._number(num(member.soc_percent), 1)} %</strong></div>
        <p class="muted">${esc(this._text("SOC am Planstart", "SOC at plan start"))} · ${esc(localize(this._hass, "cascade_discharge_target"))} ${this._number(num(member.target_soc_percent), 0)} %</p>
        ${this._plot(this._series(cascade, "soc", member.load_id, view.period), member.name || member.load_id, CASCADE_SOC_COLORS[mi % CASCADE_SOC_COLORS.length], true)}
        <div class="member-energy">${[["charge", this._text("Aufnahme", "Charge input"), "energy_wh"], ["charge", this._text("Im Akku gespeichert", "Stored in battery"), "stored_energy_wh"], ["discharge", this._text("Akkuentnahme", "Battery withdrawal"), "energy_wh"]].map(([kind, label, field]) => { const wh = this._total(blocks, kind, member.load_id, field); return `<span>${label}<b>${this._number(wh == null ? null : wh / 1000)} kWh</b></span>`; }).join("")}</div>
        ${this._button(this._text("SOC & Energie · Details", "SOC & energy · details"), index, "detail", `data-kind="soc" data-id="${esc(member.load_id)}" aria-controls="details-${index}" aria-expanded="${view.detail?.id === member.load_id}"`)}
      </article>`).join("")}</div>
      <article class="terminal"><div class="section-heading"><h3>${esc(cascade.terminal_name || cascade.terminal_load_id || "?")}</h3>${this._button(this._text("Leistung & Energie ↗", "Power & energy ↗"), index, "detail", `data-kind="terminal" aria-controls="details-${index}" aria-expanded="${view.detail?.kind === "terminal"}"`)}</div>
        ${this._plot(this._series(cascade, "terminal", null, view.period, "power"), cascade.terminal_name || cascade.terminal_load_id || "?", CASCADE_TERMINAL_COLOR, true)}</article>
      ${this._details(cascade, index, view)}${this._agenda(cascade, index, view)}</section>`;
  }

  _bindCharts() {
    this._charts.forEach((chart, index) => {
      const svg = this.shadowRoot.getElementById(`chart-${index}`);
      if (!svg) return;
      const move = (event) => {
        const rect = svg.getBoundingClientRect();
        if (!rect.width) return;
        const x = (event.clientX - rect.left) / rect.width * chart.width;
        const fraction = Math.max(0, Math.min(1, (x - chart.left) / (chart.width - chart.left - chart.right)));
        this._showTime(chart.t0 + fraction * (chart.t1 - chart.t0));
      };
      svg.addEventListener("pointermove", move);
      svg.addEventListener("pointerdown", move);
      svg.addEventListener("keydown", (event) => {
        const times = [...new Set(chart.points.map((p) => p.time))];
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const current = chart.kbIndex ?? 0;
        chart.kbIndex = event.key === "Home" ? 0 : event.key === "End" ? times.length - 1 :
          event.key === "ArrowLeft" ? Math.max(0, current - 1) : Math.min(times.length - 1, current + 1);
        this._showTime(times[chart.kbIndex]);
      });
    });
    this.shadowRoot.querySelectorAll("button[data-action]").forEach((button) => button.addEventListener("click", () => {
      const index = Number(button.dataset.cascade), cascade = this._cascades()[index];
      if (!cascade) return;
      const view = this._ui(cascade, index);
      const action = button.dataset.action;
      if (action === "detail") {
        view.mode = "power";
        view.detail = { kind: button.dataset.kind, id: button.dataset.id };
        if (button.dataset.period) view.period = button.dataset.period;
      } else if (action === "close") view.detail = null;
      else if (action === "period") view.period = button.dataset.period;
      else if (action === "mode") view.mode = button.dataset.mode;
      else if (action === "metric" && view.detail) view.detail.metric = button.dataset.metric;
      const dataset = { ...button.dataset };
      this._render();
      if (action === "detail") {
        const panel = this.shadowRoot.getElementById(`details-${index}`);
        panel?.focus({ preventScroll: true });
        panel?.scrollIntoView({ block: "nearest", behavior: "smooth" });
      } else {
        const buttons = [...this.shadowRoot.querySelectorAll("button[data-action]")];
        const next = buttons.find((b) => Object.entries(dataset).every(([key, value]) => b.dataset[key] === value));
        (next || buttons.find((b) => b.dataset.cascade === String(index)))?.focus({ preventScroll: true });
      }
    }));
    if (this._cursorTime != null) this._showTime(this._cursorTime);
  }

  _render() {
    if (!this._config || !this._hass) return;
    this._charts = [];
    const entityId = this._entityId();
    const state = this._hass.states[entityId];
    const cascades = this._cascades();
    const body = !entityId ? esc(localize(this._hass, "no_entity")) : !state ? esc(`${localize(this._hass, "not_found")} ${entityId}`) :
      cascades.length ? cascades.map((c, i) => this._renderCascade(c, i)).join("") : esc(this._text("Keine Kaskaden konfiguriert", "No cascades configured"));
    this.shadowRoot.innerHTML = `<ha-card header="${esc(this._config.title || localize(this._hass, "card_cascade"))}"><style>
      :host{display:block;min-width:0}*{box-sizing:border-box}.wrap{padding:0 16px 16px;color:var(--primary-text-color,#eee);font-size:14px;line-height:1.5;overflow-wrap:anywhere}
      .cascade{border-top:1px solid var(--divider-color,#444);padding-top:16px}.cascade+.cascade{margin-top:24px}h2,h3,h4,p{margin:0}h2{font-size:1.4em}h3{font-size:1.1em}h4{margin-top:16px}.muted,.readout,small{color:var(--secondary-text-color,#aaa);font-size:.86em}.muted{margin:8px 0}.fault{color:var(--error-color,#f66)}
      .section-heading{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:10px}.section-heading>*{min-width:0}.badge{padding:3px 9px;border:1px solid var(--divider-color,#444);border-radius:12px;color:var(--secondary-text-color,#aaa);font-size:.85em}
      button{font:inherit;white-space:normal;overflow-wrap:anywhere;text-align:left;cursor:pointer;border:1px solid var(--divider-color,#444);border-radius:9px;background:var(--secondary-background-color,#222);color:var(--primary-text-color,#eee);padding:9px 12px;min-height:44px;max-width:100%}button:hover,button[aria-pressed=true]{border-color:var(--primary-color,#039be5);background:color-mix(in srgb,var(--primary-color,#039be5) 12%,transparent)}button:focus-visible,svg:focus-visible{outline:2px solid var(--primary-color,#039be5);outline-offset:2px}nav,.period{display:flex;gap:6px;flex-wrap:wrap}.period{margin:16px 0}
      .topology{color:var(--secondary-text-color,#aaa);margin:8px 0 16px}.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(145px,100%),1fr));gap:8px}.metrics button{display:grid;gap:5px}.metrics strong{font-size:1.35em}.metrics span{color:var(--secondary-text-color,#aaa)}.members{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(300px,100%),1fr));gap:12px;margin-top:16px}.member,.terminal,.details{padding:14px;border:1px solid var(--divider-color,#444);border-radius:12px;min-width:0}.terminal{margin-top:12px}.member-energy{display:flex;flex-wrap:wrap;gap:12px;margin:12px 0;font-size:.85em}.member-energy span{flex:1;min-width:85px;color:var(--secondary-text-color,#aaa)}.member-energy b{display:block;color:var(--primary-text-color,#eee)}
      .plot{overflow-x:auto;padding:3px}svg{display:block;width:100%;min-width:300px;height:auto;touch-action:pan-y}.axis{fill:var(--secondary-text-color,#aaa);font:12px sans-serif}.grid{stroke:var(--divider-color,#444)}.forecast-line{stroke-width:2.5;stroke-dasharray:6 3;stroke-linejoin:round}.soc-target{stroke:var(--warning-color,#ffb300);stroke-width:1;stroke-dasharray:3 5}.marker{stroke:var(--primary-text-color,#eee);stroke-width:1;stroke-dasharray:3 3}.readout{min-height:3em;margin-top:4px;white-space:normal;overflow-wrap:anywhere}.details{border-color:var(--primary-color,#039be5);margin:16px 0}.details:focus{outline:none}
      .agenda{list-style:none;padding:0;margin:12px 0}.event{display:grid;grid-template-columns:minmax(110px,150px) minmax(0,1fr);gap:12px;margin-bottom:12px}.event time{font-size:.88em;color:var(--primary-color,#039be5);padding-top:12px}.event-body{border:1px solid var(--divider-color,#444);border-radius:12px;padding:12px}.event-title{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap}.flows{list-style:none;margin:8px 0;padding:0}.flows li{display:flex;justify-content:space-between;gap:8px 16px;flex-wrap:wrap;padding:6px 0;border-bottom:1px solid var(--divider-color,#444)}.flow-label{flex:1;min-width:min(180px,100%)}.flows strong{font-variant-numeric:tabular-nums}.flow-label i{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:8px}
      /* Bound SVG scaling on wide dashboards; keep wrapping readouts outside scrolling. */
      .plot,.readout{width:100%;max-width:600px}.details .plot,.details .readout{max-width:900px}
      @media(max-width:480px){.event{grid-template-columns:1fr;gap:4px}.wrap{padding:0 12px 12px}.member,.terminal,.details{padding:10px}}
    </style><div class="wrap">${body}</div></ha-card>`;
    this._bindCharts();
  }

}

if (!customElements.get(CASCADE_CARD_TYPE)) {
  customElements.define(CASCADE_CARD_TYPE, BatteryManagerCascadeCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CASCADE_CARD_TYPE,
    get name() { return localize(null, "card_cascade"); },
    get description() { return localize(null, "desc_cascade"); },
    preview: true,
    documentationURL: DOCS_URL,
    getEntitySuggestion: (hass, entityId) => {
      if (
        entityId.startsWith("sensor.") &&
        isForecastEntity(hass.states[entityId])
      ) {
        return {
          config: { type: `custom:${CASCADE_CARD_TYPE}`, entity: entityId },
        };
      }
      return null;
    },
  });
}
