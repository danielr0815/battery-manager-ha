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

// Defensive caps: attributes are user-controlled input, and a broken or
// hostile payload must not freeze the UI with megabytes of SVG.
const MAX_POINTS = 1000; // forecast samples kept (stride-downsampled)
const MAX_LANES = 12; // load + cascade + appliance + feed-in lanes below the plot
const MAX_BLOCKS = 100; // schedule blocks rendered per lane

const STRINGS = {
  en: {
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
    static_hint: "dimmed bars = static fallback profile",
    no_consumption:
      "No consumption forecast on this sensor — needs Battery Manager v0.25.5+.",
  },
  de: {
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
    root: "Root",
    aux: "Aux",
    cascade_chart_label: "Kaskaden-Zeitplan",
    cascade_no_data: "In diesem Zeitraum ist keine Kaskadenaktivität geplant.",
    cascade_root_input: "Root → Kaskade",
    discharging: "entladen",
    output: "AC-Ausgang",
    terminal_load: "Endlast",
    on: "AN",
    stored: "gespeichert",
    source: "Quelle",
    soc: "SOC",
    total: "Summe",
    static_hint: "abgedunkelte Balken = statisches Fallback-Profil",
    no_consumption:
      "Keine Verbrauchsprognose im Sensor — benötigt Battery Manager v0.25.5+.",
  },
};

function localize(hass, key) {
  const lang = (hass?.language || "en").split("-")[0];
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
      throw new Error("Invalid configuration");
    }
    // YAML is free-form: fail loudly with a useful message instead of
    // rendering something subtly broken (Lovelace shows thrown errors).
    if (config.entity != null && typeof config.entity !== "string") {
      throw new Error(`${CARD_TYPE}: "entity" must be an entity id string`);
    }
    if (
      config.hours != null &&
      (typeof config.hours !== "number" || !Number.isFinite(config.hours))
    ) {
      throw new Error(`${CARD_TYPE}: "hours" must be a finite number`);
    }
    this._config = {
      hours: 48,
      ...config,
    };
    this._lastState = undefined;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const stateObj = this._config?.entity
      ? hass.states[this._config.entity]
      : undefined;
    if (stateObj !== this._lastState) {
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
    // Early grid feed-in (F-FEEDIN): same slot-ENDING semantics as the
    // support flags, but numeric — a contiguous run of points with
    // feedin > 0 forms one block, labelled with its power in W (single
    // value, or min–max range when the rate varies within the block).
    const feedinBlocks = [];
    let feedinStart = null;
    let feedinMin = Infinity;
    let feedinMax = 0;
    for (let i = 1; i < points.length; i++) {
      const w = points[i].feedin;
      if (w > 0) {
        if (feedinStart === null) feedinStart = points[i - 1].time;
        feedinMin = Math.min(feedinMin, w);
        feedinMax = Math.max(feedinMax, w);
      } else if (feedinStart !== null) {
        feedinBlocks.push({
          start: feedinStart,
          end: points[i - 1].time,
          label:
            feedinMin === feedinMax
              ? `${Math.round(feedinMax)} W`
              : `${Math.round(feedinMin)}–${Math.round(feedinMax)} W`,
        });
        feedinStart = null;
        feedinMin = Infinity;
        feedinMax = 0;
      }
    }
    if (feedinStart !== null) {
      feedinBlocks.push({
        start: feedinStart,
        end: points[points.length - 1].time,
        label:
          feedinMin === feedinMax
            ? `${Math.round(feedinMax)} W`
            : `${Math.round(feedinMin)}–${Math.round(feedinMax)} W`,
      });
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
        const root = cascade.planned_root_energy_kwh.toFixed(2);
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
    // Slot length of the hovered hour: the gap to the previous point. Slot 0
    // is a PARTIAL hour, so the feed-in power must not be read as Wh 1:1.
    const prev = meta.points[index - 1];
    const slotHours = prev ? (nearest.time - prev.time) / 3600000 : 0;
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
        // carries it on the covering schedule block, the feed-in lane derives
        // it from the point's planned power x slot length. Appliance lanes
        // have no per-slot figure and stay name-only.
        let wh = null;
        if (lane.kind === "feedin") {
          const w = num(nearest.feedin) ?? 0;
          if (w > 0 && slotHours > 0) {
            wh = w * slotHours;
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
      throw new Error("Invalid configuration");
    }
    if (config.entity != null && typeof config.entity !== "string") {
      throw new Error(
        `${CONSUMPTION_CARD_TYPE}: "entity" must be an entity id string`
      );
    }
    if (
      config.hours != null &&
      (typeof config.hours !== "number" || !Number.isFinite(config.hours))
    ) {
      throw new Error(`${CONSUMPTION_CARD_TYPE}: "hours" must be a finite number`);
    }
    this._config = { hours: 48, ...config };
    this._lastState = undefined;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    const stateObj = this._config?.entity
      ? hass.states[this._config.entity]
      : undefined;
    if (stateObj !== this._lastState) {
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
    name: "Battery Manager Forecast",
    description:
      "Planned SOC trajectory, inverter threshold and surplus-load schedule" +
      " from the Battery Manager integration.",
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
    name: "Battery Manager Consumption",
    description:
      "Planned consumption per hour, split by voltage level (230 V AC /" +
      " 48 V / 24 V) plus planned surplus loads, from the Battery Manager" +
      " integration.",
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
      throw new Error("Invalid configuration");
    }
    if (config.entity != null && typeof config.entity !== "string") {
      throw new Error(
        `${CASCADE_CARD_TYPE}: "entity" must be an entity id string`
      );
    }
    if (
      config.hours != null &&
      (typeof config.hours !== "number" || !Number.isFinite(config.hours))
    ) {
      throw new Error(`${CASCADE_CARD_TYPE}: "hours" must be a finite number`);
    }
    this._config = { hours: 48, ...config };
    this._lastState = undefined;
    this._render();
  }

  set hass(value) {
    this._hass = value;
    const state = value.states[this._entityId()];
    if (state !== this._lastState) {
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
    return { rows: 6, columns: 12, min_rows: 4, min_columns: 6 };
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
    return Array.isArray(cascades) ? cascades.slice(0, 20) : [];
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

  _detail(activity, fallbackName) {
    const t = (key) => localize(this._hass, key);
    const name = activity?.name || fallbackName || "?";
    const wh = num(activity?.energy_wh);
    const energy = wh == null ? "" : ` ${Math.round(wh)} Wh`;
    const startSoc = num(activity?.soc_start_percent);
    const endSoc = num(activity?.soc_end_percent);
    const soc =
      startSoc == null || endSoc == null
        ? ""
        : ` · ${t("soc")} ${startSoc.toFixed(1)}→${endSoc.toFixed(1)} %`;
    if (activity?.kind === "charge") {
      const stored = num(activity.stored_energy_wh);
      return `${name} · ${t("charging")}${energy}${
        stored == null ? "" : ` · ${t("stored")} ${Math.round(stored)} Wh`
      }${soc}`;
    }
    if (activity?.kind === "discharge") {
      return `${name} · ${t("discharging")}${energy}${soc}`;
    }
    if (activity?.kind === "output") {
      const sources = (Array.isArray(activity.sources)
        ? activity.sources
        : []
      )
        .map((source) => (source === "aux" ? t("aux") : t("root")))
        .join("/");
      return `${name} · ${t("output")} ${t("on")}${
        sources ? ` · ${t("source")} ${sources}` : ""
      }`;
    }
    const source =
      activity?.source === "aux"
        ? `${t("aux")}${activity.source_name ? ` ${activity.source_name}` : ""}`
        : t("root");
    return `${name} · ${t("terminal_load")}${energy} · ${t("source")} ${source}`;
  }

  _chart(cascade, index) {
    const t = (key) => localize(this._hass, key);
    const rawSchedule = (Array.isArray(cascade?.schedule)
      ? cascade.schedule
      : []
    )
      .filter((block) => {
        const start = new Date(block?.start).getTime();
        const end = new Date(block?.end).getTime();
        return Number.isFinite(start) && Number.isFinite(end) && end > start;
      })
      .slice(0, MAX_BLOCKS);
    const starts = rawSchedule.map((block) => new Date(block.start).getTime());
    const hourMs = 3600000;
    const t0 = starts.length
      ? Math.min(...starts)
      : Math.floor(Date.now() / hourMs) * hourMs;
    const t1 = t0 + Math.max(6, Math.min(96, this._config.hours)) * hourMs;
    const schedule = rawSchedule.filter(
      (block) =>
        new Date(block.end).getTime() > t0 &&
        new Date(block.start).getTime() < t1
    );
    const members = this._memberDetails(cascade);
    const activityBlocks = (kind, loadId) =>
      schedule.flatMap((block) =>
        (Array.isArray(block.activities) ? block.activities : [])
          .filter(
            (activity) =>
              activity &&
              typeof activity === "object" &&
              activity.kind === kind &&
              (loadId == null || activity.load_id === loadId)
          )
          .map((activity) => ({
            start: block.start,
            end: block.end,
            activity,
            wh: num(activity.energy_wh),
            detail: this._detail(activity),
          }))
      );
    const rows = [
      {
        label: t("cascade_root_input"),
        color: CASCADE_ROOT_COLOR,
        blocks: schedule
          .filter((block) => (num(block.root_input_wh) ?? 0) > 0)
          .map((block) => {
            const wh = num(block.root_input_wh) ?? 0;
            return {
              start: block.start,
              end: block.end,
              wh,
              detail: `${t("cascade_root_input")} · ${Math.round(wh)} Wh`,
            };
          }),
      },
    ];
    for (const member of members) {
      rows.push(
        {
          label: `${member.name} · ${t("charging")}`,
          color: CASCADE_CHARGE_COLOR,
          blocks: activityBlocks("charge", member.load_id),
        },
        {
          label: `${member.name} · ${t("discharging")}`,
          color: CASCADE_DISCHARGE_COLOR,
          blocks: activityBlocks("discharge", member.load_id),
        },
        {
          label: `${member.name} · ${t("output")}`,
          color: CASCADE_OUTPUT_COLOR,
          blocks: activityBlocks("output", member.load_id),
        }
      );
    }
    const terminalName = cascade?.terminal_name || cascade?.terminal_load_id || "?";
    rows.push({
      label: `${terminalName} · ${t("terminal_load")}`,
      color: CASCADE_TERMINAL_COLOR,
      blocks: activityBlocks("terminal", cascade?.terminal_load_id),
    });

    const width = 900;
    const labelWidth = 205;
    const right = 12;
    const plotWidth = width - labelWidth - right;
    const rowHeight = 25;
    const top = 22;
    const bottom = 24;
    const height = top + rows.length * rowHeight + bottom;
    const x = (time) =>
      labelWidth + ((time - t0) / (t1 - t0)) * plotWidth;
    const svg = [];
    const fmtTick = new Intl.DateTimeFormat(this._hass?.language || "en", {
      weekday: "short",
      hour: "2-digit",
    });
    for (let tick = t0; tick <= t1; tick += 6 * hourMs) {
      const px = x(tick);
      svg.push(
        `<line x1="${px}" y1="${top - 8}" x2="${px}" y2="${height - bottom}" class="grid"/>`,
        `<text x="${px + 3}" y="12" class="axis">${esc(fmtTick.format(tick))}</text>`
      );
    }
    rows.forEach((row, rowIndex) => {
      const y = top + rowIndex * rowHeight;
      svg.push(
        `<text x="4" y="${y + 16}" class="label">${esc(row.label)}</text>`,
        `<line x1="${labelWidth}" y1="${y + rowHeight}" x2="${width - right}" y2="${y + rowHeight}" class="rowline"/>`
      );
      for (const block of row.blocks) {
        const start = Math.max(t0, new Date(block.start).getTime());
        const end = Math.min(t1, new Date(block.end).getTime());
        const bx = x(start);
        const bw = Math.max(2, x(end) - bx);
        const label = block.wh == null ? t("on") : `${Math.round(block.wh)} Wh`;
        svg.push(
          `<rect x="${bx}" y="${y + 5}" width="${bw}" height="15" rx="3" fill="${row.color}"><title>${esc(block.detail)}</title></rect>`,
          bw > 42
            ? `<text x="${bx + 4}" y="${y + 16}" class="block-label">${esc(label)}</text>`
            : ""
        );
      }
    });
    svg.push(`<g id="marker-${index}"></g>`);
    const slots = [...new Set(schedule.map((block) => new Date(block.start).getTime()))]
      .filter((time) => time >= t0 && time < t1)
      .sort((a, b) => a - b);
    this._charts[index] = {
      t0,
      t1,
      labelWidth,
      plotWidth,
      top,
      height,
      bottom,
      rows,
      slots,
      kbIndex: null,
    };
    const hasActivity = rows.some((row) => row.blocks.length);
    return `<svg id="chart-${index}" viewBox="0 0 ${width} ${height}" role="img" tabindex="0" aria-label="${esc(
      `${t("cascade_chart_label")}: ${cascade?.name || "Cascade"}`
    )}">${svg.join("")}</svg><div id="readout-${index}" class="readout" aria-live="polite">${
      hasActivity ? "&nbsp;" : esc(t("cascade_no_data"))
    }</div>`;
  }

  _showSlot(chartIndex, time) {
    const chart = this._charts[chartIndex];
    const marker = this.shadowRoot?.getElementById(`marker-${chartIndex}`);
    const readout = this.shadowRoot?.getElementById(`readout-${chartIndex}`);
    if (!chart || !marker || !readout) return;
    const clamped = Math.max(chart.t0, Math.min(chart.t1, time));
    const px =
      chart.labelWidth +
      ((clamped - chart.t0) / (chart.t1 - chart.t0)) * chart.plotWidth;
    marker.innerHTML = `<line x1="${px}" y1="${chart.top - 8}" x2="${px}" y2="${chart.height - chart.bottom}" class="marker"/>`;
    const details = chart.rows.flatMap((row) =>
      row.blocks
        .filter((block) => {
          const start = new Date(block.start).getTime();
          const end = new Date(block.end).getTime();
          return clamped >= start && clamped < end;
        })
        .map((block) => block.detail)
    );
    const fmt = new Intl.DateTimeFormat(this._hass?.language || "en", {
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
    });
    readout.textContent = `${fmt.format(clamped)}${
      details.length ? ` · ${details.join(" · ")}` : ""
    }`;
  }

  _bindCharts() {
    this._charts.forEach((chart, index) => {
      const svg = this.shadowRoot?.getElementById(`chart-${index}`);
      if (!svg) return;
      svg.addEventListener("pointermove", (event) => {
        const rect = svg.getBoundingClientRect();
        if (!(rect.width > 0)) return;
        const viewX = ((event.clientX - rect.left) / rect.width) * 900;
        const fraction = Math.max(
          0,
          Math.min(1, (viewX - chart.labelWidth) / chart.plotWidth)
        );
        this._showSlot(index, chart.t0 + fraction * (chart.t1 - chart.t0));
      });
      svg.addEventListener("keydown", (event) => {
        if (!chart.slots.length) return;
        let kbIndex = chart.kbIndex;
        if (event.key === "ArrowLeft") {
          kbIndex = kbIndex == null ? 0 : Math.max(0, kbIndex - 1);
        } else if (event.key === "ArrowRight") {
          kbIndex =
            kbIndex == null
              ? 0
              : Math.min(chart.slots.length - 1, kbIndex + 1);
        } else if (event.key === "Home") {
          kbIndex = 0;
        } else if (event.key === "End") {
          kbIndex = chart.slots.length - 1;
        } else {
          return;
        }
        event.preventDefault();
        chart.kbIndex = kbIndex;
        this._showSlot(index, chart.slots[kbIndex]);
      });
    });
  }

  _render() {
    if (!this._config || !this._hass) return;
    const entityId = this._entityId();
    const state = this._hass.states[entityId];
    const t = (key) => localize(this._hass, key);
    let body = "";
    this._charts = [];
    if (!entityId) {
      body = `<div class="message">${esc(t("no_entity"))}</div>`;
    } else if (!state) {
      body = `<div class="message">${esc(t("not_found"))} ${esc(entityId)}</div>`;
    } else {
      body = this._cascades()
        .map((item, index) => {
        const soc = num(item?.aggregate_soc_percent);
        const root = num(item?.planned_root_energy_kwh) ?? 0;
        const aux = num(item?.planned_aux_energy_kwh) ?? 0;
        const actual = num(item?.actual_aux_energy_kwh) ?? 0;
        const fault = item?.fault ? ` · ⚠ ${esc(item.fault)}` : "";
          return `<section><div class="summary"><div><b>${esc(item?.name || "Cascade")}</b>` +
            `<span>${esc(item?.phase || "idle")} · ${esc(item?.source_name || item?.source || "Root")}${fault}</span></div>` +
            `<div class="soc">${soc == null ? "?" : soc.toFixed(1)} %${item?.aggregate_soc_stale ? "*" : ""}</div>` +
            `<div class="energy">Root ${root.toFixed(2)} kWh · Aux ${aux.toFixed(2)} kWh · Ist ${actual.toFixed(2)} kWh</div></div>` +
            `<div class="chart-wrap">${this._chart(item, index)}</div></section>`;
        })
        .join("");
      if (!body) body = `<div class="message">No cascades configured.</div>`;
    }
    this.shadowRoot.innerHTML = `<ha-card header="${esc(
      this._config.title || "Battery Manager Cascades"
    )}"><style>
      .wrap{padding:0 16px 16px}section{padding:12px 0;border-top:1px solid var(--divider-color)}
      .summary{display:grid;grid-template-columns:1fr auto;gap:4px 16px;margin-bottom:8px}
      .summary span,.energy{display:block;color:var(--secondary-text-color);font-size:.85em}
      .energy{grid-column:1/-1}.soc{font-variant-numeric:tabular-nums}
      .chart-wrap{overflow-x:auto}svg{display:block;width:100%;min-width:620px;height:auto;touch-action:none;outline:none}
      svg:focus{outline:2px solid var(--primary-color);outline-offset:2px}
      .grid{stroke:var(--divider-color);stroke-width:1;stroke-dasharray:2 3}.rowline{stroke:var(--divider-color);stroke-width:.7}
      .axis,.label{fill:var(--secondary-text-color);font:12px sans-serif}.label{fill:var(--primary-text-color)}
      .block-label{fill:white;font:10px sans-serif;pointer-events:none}.marker{stroke:var(--primary-text-color);stroke-width:1;stroke-dasharray:3 3}
      .readout{min-height:2.6em;margin:6px 4px 0;color:var(--secondary-text-color);font-size:.82em;line-height:1.3}
      .message{padding:20px 0;color:var(--secondary-text-color)}
    </style><div class="wrap">${body}</div></ha-card>`;
    this._bindCharts();
  }
}

if (!customElements.get(CASCADE_CARD_TYPE)) {
  customElements.define(CASCADE_CARD_TYPE, BatteryManagerCascadeCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CASCADE_CARD_TYPE,
    name: "Battery Manager Cascades",
    description: "Storage SOC, active source, Root/Aux energy, recovery and faults.",
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
