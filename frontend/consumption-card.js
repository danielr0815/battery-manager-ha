import { dateTimeFormat, nextHour } from "./time.js";
import {
  consumption_card_style_0,
  consumption_card_style_1,
} from "./styles.js";
import { localize } from "./translations.js";
import {
  CONSUMPTION_CARD_TYPE,
  esc,
  num,
  AC_COLOR,
  DC48_LAYER_COLOR,
  DC24_LAYER_COLOR,
  PLANNED_LAYER_COLOR,
  DOCS_URL,
} from "./shared.js";
import { replaceCardHTML } from "./dom.js";

export function isConsumptionEntity(stateObj) {
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

export function findConsumptionEntity(hass, entities) {
  const candidates = (entities || []).filter(
    (id) => id.startsWith("sensor.") && isConsumptionEntity(hass.states[id]),
  );
  return (
    candidates.find((id) => id.includes("soc_forecast")) || candidates[0] || ""
  );
}

export class BatteryManagerConsumptionCard extends HTMLElement {
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
        `${CONSUMPTION_CARD_TYPE}: ${localize(this._hass, "invalid_entity")}`,
      );
    }
    if (
      config.hours != null &&
      (typeof config.hours !== "number" || !Number.isFinite(config.hours))
    ) {
      throw new Error(
        `${CONSUMPTION_CARD_TYPE}: ${localize(this._hass, "invalid_hours")}`,
      );
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
    replaceCardHTML(
      this,
      `
      ${consumption_card_style_0}
      <ha-card>
        <span class="error">${esc(localize(this._hass, "render_error"))} ${esc(
          detail,
        )}</span>
      </ha-card>
    `,
    );
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
      header = this._config.title ?? t("chart_label_consumption");
      body = this._renderChart(stateObj, t);
    }

    replaceCardHTML(
      this,
      `
      ${consumption_card_style_1}
      <ha-card>
        <div class="header">
          <div class="title">${esc(header ?? "")}</div>
          <div class="stats">${this._statsText || ""}</div>
        </div>
        ${body}
      </ha-card>
    `,
    );
    this._attachChartHandlers();
  }

  _renderChart(stateObj, t) {
    const a = stateObj.attributes;
    const lang = this._hass?.language || "en";

    let points = a.consumption_forecast
      .filter((p) => p && p.t != null)
      .map((p) => ({
        time: new Date(p.t).getTime(),
        duration: num(p.duration_h),
        ac: num(p.ac_w) ?? 0,
        dc48: num(p.dc48_w) ?? 0,
        dc24: num(p.dc24_w) ?? 0,
        loads: num(p.loads_w) ?? 0,
        learned: typeof p.src === "string" && p.src === "L/L",
      }))
      .filter((p) => Number.isFinite(p.time));
    points.sort((a, b) => a.time - b.time);
    points = points.map((p, i) => ({
      ...p,
      end:
        p.time +
        (p.duration > 0
          ? p.duration * 3600000
          : i + 1 < points.length
            ? points[i + 1].time - p.time
            : 3600000),
    }));
    if (!points.length) return this._message(t("no_data"));
    const horizonMs = Number(this._config.hours) * 3600000;
    const cutoff = horizonMs > 0 ? points[0].time + horizonMs : Infinity;
    points = points
      .filter((p) => p.time < cutoff && p.end > p.time)
      .map((p) => ({ ...p, end: Math.min(p.end, cutoff) }));
    if (!points.length) return this._message(t("no_data"));
    const durs = points.map((p) => (p.end - p.time) / 3600000);
    const t0 = points[0].time;
    const t1 = points[points.length - 1].end;
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
    const yMax = Math.max(
      100,
      Math.ceil((Math.max(...totals) * 1.1) / 100) * 100,
    );

    const x = (time) => margin.left + ((time - t0) / (t1 - t0)) * plotW;
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
          font-size="9" fill="${text}">${w}</text>`,
      );
    }
    svg.push(
      `<text x="${margin.left - 5}" y="${margin.top - 2}" text-anchor="end"
        font-size="8" fill="${text}">W</text>`,
    );

    // Vertical grid: day boundaries (labelled) and 6-hour ticks
    const timeZone = this._hass?.config?.time_zone;
    const dayFmt = dateTimeFormat(this._hass, { weekday: "short", timeZone });
    const hourFmt = new Intl.DateTimeFormat("en-GB", {
      hour: "2-digit",
      hourCycle: "h23",
      timeZone,
    });
    for (let tick = nextHour(this._hass, t0); tick <= t1; tick += 3600 * 1000) {
      const hour = Number(hourFmt.format(tick));
      if (hour === 0) {
        svg.push(
          `<line x1="${x(tick)}" y1="${margin.top}" x2="${x(tick)}"
            y2="${margin.top + plotH}" stroke="${line}" stroke-width="1.5"/>`,
          `<text x="${x(tick) + 3}" y="${height - 4}" font-size="9"
            fill="${text}">${dayFmt.format(tick)}</text>`,
        );
      } else if (hour % 6 === 0) {
        svg.push(
          `<line x1="${x(tick)}" y1="${margin.top}" x2="${x(tick)}"
            y2="${margin.top + plotH}" stroke="${line}" stroke-width="1"
            stroke-dasharray="2 3" opacity="0.7"/>`,
          `<text x="${x(tick)}" y="${height - 4}" font-size="9"
            text-anchor="middle" fill="${text}">${hour}</text>`,
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
                1,
              )}" fill="${layer.color}"/>`,
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
        stroke-width="1.5" stroke-linejoin="round"/>`,
    );

    // "now" marker at the left edge of slot 0.
    svg.push(
      `<line x1="${x(t0)}" y1="${margin.top}" x2="${x(t0)}"
        y2="${margin.top + plotH}" stroke="${text}" stroke-width="1"
        stroke-dasharray="3 3"/>`,
      `<text x="${x(t0) + 3}" y="${margin.top + 8}" font-size="9"
        fill="${text}">${t("now")} ${Math.round(totals[0])} W</text>`,
    );

    // Hover overlay target (events attached after innerHTML assignment)
    svg.push(
      `<rect id="hover-target" x="${margin.left}" y="${margin.top}"
        width="${plotW}" height="${plotH}" fill="transparent"/>`,
      `<g id="hover-marker"></g>`,
    );

    this._chartMeta = {
      points,
      durs,
      totals,
      barMeta,
      x,
      margin,
      plotH,
      t0,
      t1,
      lang,
    };

    // Per-day kWh sums (today / tomorrow) per layer, from W x slot hours.
    const dateFmt = new Intl.DateTimeFormat("en-CA", {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      timeZone,
    });
    const dayKey = (time) => dateFmt.format(time);
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
          (sums[`${layer.key}_${bucket}`] || 0) +
          (p[layer.key] * durs[i]) / 1000;
      }
      sums[`total_${bucket}`] =
        (sums[`total_${bucket}`] || 0) + (totals[i] * durs[i]) / 1000;
    });
    const perDay = (key) =>
      `${(sums[`${key}_today`] || 0).toFixed(1)}/${(
        sums[`${key}_tomorrow`] || 0
      ).toFixed(1)}`;
    this._statsText = `${t("total")} ${perDay("total")} kWh ${t(
      "today_tomorrow",
    )}`;

    const anyStatic = points.some((p) => !p.learned);
    const legend =
      layers
        .map(
          (layer) =>
            `<span><span class="dot" style="background:${layer.color}"></span>${esc(
              layer.name,
            )} (${perDay(layer.key)})</span>`,
        )
        .join("") +
      `<span><span class="dot" style="background:${totalColor}"></span>${esc(
        t("total"),
      )} (${perDay("total")}) kWh</span>` +
      (anyStatic ? `<span class="off">${esc(t("static_hint"))}</span>` : "");

    // Screen-reader summary (same pattern as the SOC chart).
    let maxP = points[0];
    let maxTotal = totals[0];
    const whenFmt = dateTimeFormat(this._hass, {
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
        totals[0],
      )} W, ` +
      `${t("sr_max")} ${Math.round(maxTotal)} W (${whenFmt.format(
        maxP.time,
      )}).`;

    return `
      <svg id="chart" role="img" tabindex="0" aria-label="${esc(summary)}"
        width="${width}" height="${height}" viewBox="0 0 ${width} ${height}">
        ${svg.join("\n")}
      </svg>
      <div class="readout" id="readout" aria-live="polite">&nbsp;</div>
      <div class="visually-hidden">${esc(summary)} ${esc(
        this._statsText,
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
    const px =
      ((ev.clientX - rect.left) / rect.width) * svg.viewBox.baseVal.width;
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
    const fmt = dateTimeFormat(this._hass, {
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
            name,
          )} ${Math.round(v)} W</span>`,
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

if (!customElements.get(CONSUMPTION_CARD_TYPE)) {
  customElements.define(CONSUMPTION_CARD_TYPE, BatteryManagerConsumptionCard);

  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CONSUMPTION_CARD_TYPE,
    get name() {
      return localize(null, "card_consumption");
    },
    get description() {
      return localize(null, "desc_consumption");
    },
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
