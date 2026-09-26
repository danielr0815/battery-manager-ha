import { dateTimeFormat, nextHour, localHour } from "./time.js";
import { forecast_card_style_0, forecast_card_style_1 } from "./styles.js";
import { localize } from "./translations.js";
import {
  CARD_TYPE,
  findForecastEntity,
  esc,
  isForecastEntity,
  num,
  MAX_POINTS,
  MAX_LANES,
  MAX_BLOCKS,
  LOAD_COLORS,
  FEEDIN_COLOR,
  DOCS_URL,
  CARD_VERSION,
} from "./shared.js";
import { replaceCardHTML } from "./dom.js";
import {
  reserveReport,
  operationReport,
  feedinDecisions,
  executionLines,
} from "./reports.js";

export class BatteryManagerForecastCard extends HTMLElement {
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
      throw new Error(
        `${CARD_TYPE}: ${localize(this._hass, "invalid_entity")}`,
      );
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
    replaceCardHTML(
      this,
      `
      ${forecast_card_style_0}
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

    replaceCardHTML(
      this,
      `
      ${forecast_card_style_1}
      <ha-card>
        <div class="header">
          <div class="title">${esc(header ?? "")}</div>
          <div class="stats">${this._statsLine(stateObj, t)}</div>
        </div>
        ${body}
        ${reserveReport(this._hass, stateObj?.attributes?.reserve)}${operationReport(this._hass, stateObj?.attributes?.operation_report)}${feedinDecisions(this._hass, stateObj?.attributes?.feedin_decisions)}
      </ha-card>
    `,
    );
    this._attachChartHandlers();
  }

  _statsLine(stateObj, t) {
    if (!stateObj || !isForecastEntity(stateObj)) {
      return "";
    }
    const a = stateObj.attributes;
    const parts = [];
    const threshold = num(a.soc_threshold_percent);
    if (threshold !== undefined && a.reserve?.mode !== "active") {
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
            "lost_surplus_realized_kwh",
          )}`,
        );
        parts.push(
          `${t("prevented")} ${rtd(
            "prevented_export_today_kwh",
            "prevented_export_tomorrow_kwh",
            "prevented_export_realized_kwh",
          )}`,
        );
        // Measured deliberate early feed-in today (F-FEEDIN); hidden when
        // nothing was fed in yet — a permanent "0.0" would just be noise.
        const feedinRealized = num(realized.early_feed_in_realized_kwh) ?? 0;
        if (feedinRealized > 0) {
          parts.push(
            `${t("feedin_realized")} ${t("realized")} ${feedinRealized.toFixed(1)}`,
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
        (_, i, arr) => i % stride === 0 || i === arr.length - 1,
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
              (num(block.root_input_wh) ?? 0) > 0,
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
          LOAD_COLORS[
            (loads.length + cascades.length + i) % LOAD_COLORS.length
          ],
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
          height="${y(0) - y(reserve)}" fill="${err}" opacity="0.07"/>`,
      );
    }
    if (socMax < 100 && socMax >= 0) {
      svg.push(
        `<rect x="${margin.left}" y="${y(100)}" width="${plotW}"
          height="${y(socMax) - y(100)}" fill="${text}" opacity="0.07"/>`,
      );
    }

    // Horizontal grid + y labels
    for (const pct of [0, 20, 40, 60, 80, 100]) {
      svg.push(
        `<line x1="${margin.left}" y1="${y(pct)}" x2="${width - margin.right}"
          y2="${y(pct)}" stroke="${line}" stroke-width="1"/>`,
        `<text x="${margin.left - 5}" y="${y(pct) + 3}" text-anchor="end"
          font-size="9" fill="${text}">${pct}</text>`,
      );
    }

    // Vertical grid: day boundaries (labelled) and 6-hour ticks
    const dayFmt = dateTimeFormat(this._hass, { weekday: "short" });
    const gridBottom = margin.top + plotH + lanesH;
    for (let tick = nextHour(this._hass, t0); tick <= t1; tick += 3600 * 1000) {
      const hour = localHour(this._hass, tick);
      if (hour === 0) {
        svg.push(
          `<line x1="${x(tick)}" y1="${margin.top}" x2="${x(tick)}"
            y2="${gridBottom}" stroke="${line}" stroke-width="1.5"/>`,
          `<text x="${x(tick) + 3}" y="${height - 4}" font-size="9"
            fill="${text}">${dayFmt.format(tick)}</text>`,
        );
      } else if (hour % 6 === 0) {
        svg.push(
          `<line x1="${x(tick)}" y1="${margin.top}" x2="${x(tick)}"
            y2="${gridBottom}" stroke="${line}" stroke-width="1"
            stroke-dasharray="2 3" opacity="0.7"/>`,
          `<text x="${x(tick)}" y="${height - 4}" font-size="9"
            text-anchor="middle" fill="${text}">${hour}</text>`,
        );
      }
    }

    // Inverter cut-off (dotted) and threshold T* (dashed); out-of-range
    // values would land outside the plot, so skip them entirely.
    if (invMin !== undefined && invMin > reserve && invMin <= 100) {
      svg.push(
        `<line x1="${margin.left}" y1="${y(invMin)}"
          x2="${width - margin.right}" y2="${y(invMin)}" stroke="${text}"
          stroke-width="1" stroke-dasharray="1 3"/>`,
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
            threshold,
          )} %</text>`,
      );
    }

    // SOC curve: soft area fill + line
    const coords = points.map(
      (p) => `${x(p.time).toFixed(1)},${y(p.soc).toFixed(1)}`,
    );
    svg.push(
      `<polygon points="${x(points[0].time).toFixed(1)},${y(0)} ${coords.join(
        " ",
      )} ${x(points[points.length - 1].time).toFixed(1)},${y(0)}"
        fill="${accent}" opacity="0.12"/>`,
      `<polyline points="${coords.join(" ")}" fill="none" stroke="${accent}"
        stroke-width="2" stroke-linejoin="round"/>`,
    );

    // "now" marker: the curve starts at the current SOC
    svg.push(
      `<circle cx="${x(t0)}" cy="${y(points[0].soc)}" r="3.5"
        fill="${accent}"/>`,
      `<text x="${x(t0) + 5}" y="${y(points[0].soc) - 6}" font-size="9"
        fill="${text}">${t("now")} ${Math.round(points[0].soc)} %</text>`,
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
            height="${laneH}" rx="2" fill="${load.color}" opacity="0.85"/>`,
        );
        // Per-block power label (feed-in lane); only when the block is wide
        // enough to carry legible text.
        if (block.label && bw >= 34) {
          svg.push(
            `<text x="${(bx + bw / 2).toFixed(1)}" y="${laneY + laneH - 1.5}"
              text-anchor="middle" font-size="7" fill="#fff">${esc(
                block.label,
              )}</text>`,
          );
        }
      }
    });

    // Hover overlay target (events attached after innerHTML assignment)
    svg.push(
      `<rect id="hover-target" x="${margin.left}" y="${margin.top}"
        width="${plotW}" height="${plotH + lanesH}" fill="transparent"/>`,
      `<g id="hover-marker"></g>`,
    );

    this._chartMeta = {
      points,
      x,
      y,
      margin,
      plotH,
      lanesH,
      t0,
      t1,
      lang,
      lanes,
    };

    const whenFmt = dateTimeFormat(this._hass, {
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
        minP.time,
      )}), ` +
      `${t("sr_max")} ${Math.round(maxP.soc)} % (${whenFmt.format(
        maxP.time,
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
          planningPower !== undefined
            ? ` · ${Math.round(planningPower)} W`
            : "";
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
        const wait = load.feedin_waiting_for_confirmation
          ? ` · ${esc(t("feedin_wait"))}`
          : "";
        const releaseTime = load.not_before
          ? new Date(load.not_before).getTime()
          : NaN;
        const release = Number.isFinite(releaseTime)
          ? ` · ${esc(t("runtime_release"))}: ${esc(whenFmt.format(releaseTime))}`
          : "";
        const rejected = Array.isArray(load.rejected_candidates)
          ? load.rejected_candidates[0]
          : null;
        const rejectedTime = rejected
          ? new Date(rejected.start).getTime()
          : NaN;
        const rejection =
          rejected && Number.isFinite(rejectedTime)
            ? ` · ${esc(t("candidate_rejected"))} ${esc(whenFmt.format(rejectedTime))}: ${esc(t(rejected.reason))}`
            : "";
        return `<span><span class="dot" style="background:${load.color}"></span>${esc(
          load.name ?? "?",
        )} (${detail}${powerDetail})${active}${offWindow}${wait}${release}${rejection}${executionLines(
          this._hass,
          load.execution,
        )
          .map((text) => ` · ${text}`)
          .join("")}</span>`;
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
          l.name,
        )}${detail ? ` (${detail})` : ""}</span>`;
      })
      .join("");

    // Appliance runs (operator ask 2026-08-08): dot + name, the run's
    // remaining energy, and an active marker — every published run IS active.
    const applianceLegend = appliances
      .map(
        (ap) =>
          `<span><span class="dot" style="background:${ap.color}"></span>${esc(
            ap.name ?? "?",
          )}${
            ap.total_wh != null
              ? ` (${(ap.total_wh / 1000).toFixed(1)} kWh)`
              : ""
          } · <span class="active">${t("active")}</span></span>`,
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
          cascade.name,
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
        t("kbd_hint"),
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
    const px =
      ((ev.clientX - rect.left) / rect.width) * svg.viewBox.baseVal.width;
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
    const fmt = dateTimeFormat(this._hass, {
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
            lane.name ?? "?",
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
          lane.name ?? "?",
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

if (!customElements.get(CARD_TYPE)) {
  customElements.define(CARD_TYPE, BatteryManagerForecastCard);

  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CARD_TYPE,
    get name() {
      return localize(null, "card_forecast");
    },
    get description() {
      return localize(null, "desc_forecast");
    },
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
    "background: #eee; color: #333;",
  );
}
