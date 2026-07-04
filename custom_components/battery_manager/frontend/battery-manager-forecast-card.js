/**
 * Battery Manager Forecast Card
 *
 * Bundled with the battery_manager integration and registered as a Lovelace
 * resource automatically — no HACS frontend download needed. Renders the
 * planned SOC trajectory of `sensor.…_soc_forecast` together with the full
 * plan context carried in the sensor's attributes:
 *
 *   forecast                    [{t, soc}, ...] planned SOC curve
 *   soc_threshold_percent       optimal inverter threshold T*
 *   battery_min/max_soc_percent hard SOC limits
 *   inverter_min_soc_percent    inverter cut-off
 *   soc_buffer_percent          planning buffer above the minimum
 *   grid_import_kwh             expected grid import over the horizon
 *   lost_surplus_kwh            surplus that will still be lost/exported
 *   loads                       [{name, active, planned_energy_kwh,
 *                                 schedule: [{start, end}]}]
 *
 * Vanilla web component (no build step, no external dependencies); theming
 * via Home Assistant CSS variables inside an <ha-card>.
 */

const CARD_VERSION = "0.5.0";
const CARD_TYPE = "battery-manager-forecast-card";
const DOCS_URL = "https://github.com/danielr0815/battery-manager-ha";

const LOAD_COLORS = [
  "#43a047", // green
  "#fb8c00", // orange
  "#039be5", // light blue
  "#8e24aa", // purple
  "#e53935", // red
  "#00897b", // teal
];

const STRINGS = {
  en: {
    now: "now",
    threshold: "threshold",
    import: "grid import",
    lost: "lost surplus",
    loads: "Surplus loads",
    nothing_planned: "nothing planned",
    active: "active",
    no_entity: "No entity configured. Pick the Battery Manager SOC forecast sensor.",
    not_found: "Entity not found:",
    no_data: "Waiting for the first planning run …",
    min_reserve: "reserve",
  },
  de: {
    now: "jetzt",
    threshold: "Schwelle",
    import: "Netzimport",
    lost: "verlorener Überschuss",
    loads: "Überschusslasten",
    nothing_planned: "nichts geplant",
    active: "aktiv",
    no_entity:
      "Keine Entität konfiguriert. Wähle den SOC-Prognose-Sensor des Battery Managers.",
    not_found: "Entität nicht gefunden:",
    no_data: "Warte auf den ersten Planungslauf …",
    min_reserve: "Reserve",
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
    .replace(/"/g, "&quot;");
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
    this._hover = null;
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
      this._hover = null;
      this._render();
    }
  }

  getCardSize() {
    return 4;
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
    if (!this.shadowRoot || !this._config) {
      return;
    }
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
        .readout {
          font-size: 0.8em; color: var(--secondary-text-color);
          text-align: right; min-height: 1.2em; padding: 2px 4px 0;
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
    this._attachHoverHandlers();
  }

  _statsLine(stateObj, t) {
    if (!stateObj || !isForecastEntity(stateObj)) {
      return "";
    }
    const a = stateObj.attributes;
    const parts = [];
    if (a.soc_threshold_percent != null) {
      parts.push(`T* ${Math.round(a.soc_threshold_percent)} %`);
    }
    if (a.grid_import_kwh != null) {
      parts.push(`${t("import")} ${a.grid_import_kwh.toFixed(1)} kWh`);
    }
    if (a.lost_surplus_kwh != null) {
      parts.push(`${t("lost")} ${a.lost_surplus_kwh.toFixed(1)} kWh`);
    }
    return parts.join(" · ");
  }

  _renderChart(stateObj, t) {
    const a = stateObj.attributes;
    const lang = this._hass?.language || "en";

    let points = a.forecast
      // null coerces to finite 0 (epoch/0 %), so reject it before conversion
      .filter((p) => p && p.t != null && p.soc != null)
      .map((p) => ({ time: new Date(p.t).getTime(), soc: Number(p.soc) }))
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

    const loads = (a.loads || []).map((load, i) => ({
      ...load,
      color: LOAD_COLORS[i % LOAD_COLORS.length],
    }));
    const lanes = loads.filter((l) => (l.schedule || []).length > 0);

    const width = Math.max(this._width || this.clientWidth || 320, 280);
    const margin = { top: 8, right: 10, bottom: 16, left: 32 };
    const laneH = 8;
    const laneGap = 3;
    const lanesH = lanes.length ? lanes.length * (laneH + laneGap) + 4 : 0;
    const plotH = 150;
    const height = margin.top + plotH + lanesH + margin.bottom;

    const t0 = points[0].time;
    const t1 = points[points.length - 1].time;
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

    // Zones: hard SOC limits and the planning reserve (min + buffer)
    const socMin = Number(a.battery_min_soc_percent ?? 0);
    const socMax = Number(a.battery_max_soc_percent ?? 100);
    const buffer = Number(a.soc_buffer_percent ?? 0);
    const invMin = Number(a.inverter_min_soc_percent ?? NaN);
    const plotW = width - margin.left - margin.right;
    const reserve = Math.min(socMin + buffer, 100);
    svg.push(
      `<rect x="${margin.left}" y="${y(reserve)}" width="${plotW}"
        height="${y(0) - y(reserve)}" fill="${err}" opacity="0.07"/>`
    );
    if (socMax < 100) {
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

    // Inverter cut-off (dotted) and threshold T* (dashed)
    if (Number.isFinite(invMin) && invMin > reserve) {
      svg.push(
        `<line x1="${margin.left}" y1="${y(invMin)}"
          x2="${width - margin.right}" y2="${y(invMin)}" stroke="${text}"
          stroke-width="1" stroke-dasharray="1 3"/>`
      );
    }
    const threshold = Number(a.soc_threshold_percent ?? NaN);
    if (Number.isFinite(threshold)) {
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
      }
    });

    // Hover overlay target (events attached after innerHTML assignment)
    svg.push(
      `<rect id="hover-target" x="${margin.left}" y="${margin.top}"
        width="${plotW}" height="${plotH + lanesH}" fill="transparent"/>`,
      `<g id="hover-marker"></g>`
    );

    this._chartMeta = { points, x, y, margin, plotH, lanesH, t0, t1, lang };

    const legend = loads
      .map((load) => {
        const planned = Number(load.planned_energy_kwh || 0);
        const detail = planned
          ? `${planned.toFixed(1)} kWh`
          : t("nothing_planned");
        const active = load.active
          ? ` · <span class="active">${t("active")}</span>`
          : "";
        return `<span><span class="dot" style="background:${load.color}"></span>${esc(
          load.name ?? "?"
        )} (${detail})${active}</span>`;
      })
      .join("");

    return `
      <svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
        ${svg.join("\n")}
      </svg>
      <div class="readout" id="readout">&nbsp;</div>
      ${legend ? `<div class="legend">${legend}</div>` : ""}
    `;
  }

  // ------------------------------------------------------------------
  // Hover crosshair
  // ------------------------------------------------------------------

  _attachHoverHandlers() {
    const target = this.shadowRoot.getElementById("hover-target");
    if (!target || !this._chartMeta) {
      return;
    }
    target.addEventListener("pointermove", (ev) => this._onHover(ev));
    target.addEventListener("pointerleave", () => this._onHover(null));
  }

  _onHover(ev) {
    const meta = this._chartMeta;
    const marker = this.shadowRoot.getElementById("hover-marker");
    const readout = this.shadowRoot.getElementById("readout");
    if (!meta || !marker || !readout) {
      return;
    }
    if (!ev) {
      marker.innerHTML = "";
      readout.innerHTML = "&nbsp;";
      return;
    }
    const svg = marker.ownerSVGElement;
    const rect = svg.getBoundingClientRect();
    const px = ((ev.clientX - rect.left) / rect.width) * svg.viewBox.baseVal.width;
    const time =
      meta.t0 +
      ((px - meta.margin.left) /
        (svg.viewBox.baseVal.width - meta.margin.left - 10)) *
        (meta.t1 - meta.t0);
    let nearest = meta.points[0];
    for (const p of meta.points) {
      if (Math.abs(p.time - time) < Math.abs(nearest.time - time)) {
        nearest = p;
      }
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
    readout.textContent = `${fmt.format(nearest.time)} · ${nearest.soc} %`;
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
