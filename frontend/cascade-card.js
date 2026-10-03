import { PlanDisplay } from "./plan-state.js";
import { cascade_card_style_0 } from "./styles.js";
import { localize, STRINGS } from "./translations.js";
import {
  CASCADE_CARD_TYPE,
  findForecastEntity,
  MAX_CASCADE_POINTS,
  num,
  CASCADE_CHARGE_COLOR,
  CASCADE_DISCHARGE_COLOR,
  CASCADE_TERMINAL_COLOR,
  CASCADE_OUTPUT_COLOR,
  esc,
  CASCADE_SOC_COLORS,
  CASCADE_ROOT_COLOR,
  DOCS_URL,
  isForecastEntity,
} from "./shared.js";
import {
  executionLines,
  operationReport,
  feedinDecisions,
  stateNotice,
  sourceHealthReport,
} from "./reports.js";
import { replaceCardHTML, bindEntityButtons } from "./dom.js";

export class BatteryManagerCascadeCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._config = undefined;
    this._hass = undefined;
    this._lastState = undefined;
    this._planDisplay = new PlanDisplay();
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
        `${CASCADE_CARD_TYPE}: ${localize(this._hass, "invalid_entity")}`,
      );
    }
    if (
      config.hours != null &&
      (typeof config.hours !== "number" || !Number.isFinite(config.hours))
    ) {
      throw new Error(
        `${CASCADE_CARD_TYPE}: ${localize(this._hass, "invalid_hours")}`,
      );
    }
    this._config = { hours: 48, ...config };
    this._lastState = undefined;
    this._render();
  }

  set hass(value) {
    const presentationChanged =
      value.language !== this._hass?.language ||
      value.config?.time_zone !== this._hass?.config?.time_zone;
    this._hass = value;
    const state = value.states[this._entityId()];
    if (state !== this._lastState || presentationChanged) {
      this._lastState = state;
      this._render();
    }
  }

  getCardSize() {
    const rows = this._cascades().reduce(
      (sum, cascade) => sum + 2 + 3 * this._memberDetails(cascade).length,
      0,
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
    return (
      findForecastEntity(this._hass, Object.keys(this._hass.states)) ||
      this._planDisplay.entityId ||
      ""
    );
  }

  _displayState() {
    const entityId = this._entityId();
    return this._planDisplay.read(
      entityId,
      this._hass?.states?.[entityId],
      (state) =>
        Array.isArray(state.attributes?.cascades) ||
        Array.isArray(state.attributes?.loads),
    );
  }

  _cascades() {
    const state = this._displayState();
    const cascades = state?.attributes?.cascades;
    return Array.isArray(cascades)
      ? cascades.filter((c) => c && typeof c === "object").slice(0, 20)
      : [];
  }

  _memberDetails(cascade) {
    const explicit = Array.isArray(cascade?.member_details)
      ? cascade.member_details.filter(
          (item) => item && typeof item === "object" && item.load_id,
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

  _number(value, digits = 2) {
    return value == null
      ? "—"
      : new Intl.NumberFormat(this._hass?.language || "en", {
          minimumFractionDigits: digits,
          maximumFractionDigits: digits,
        }).format(value);
  }

  _time(time, date = false) {
    return new Intl.DateTimeFormat(this._hass?.language || "en", {
      timeZone: this._hass?.config?.time_zone || "UTC",
      ...(date ? { weekday: "short", day: "2-digit", month: "2-digit" } : {}),
      hour: "2-digit",
      minute: "2-digit",
    }).format(time);
  }

  _day(time) {
    const parts = new Intl.DateTimeFormat("en-CA", {
      timeZone: this._hass?.config?.time_zone || "UTC",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
    }).formatToParts(time);
    return ["year", "month", "day"]
      .map((key) => parts.find((p) => p.type === key).value)
      .join("-");
  }

  _timestamp(value) {
    // Backend slots are naive HA-local timestamps; the viewing browser can
    // live in a different timezone. Offset-bearing ISO timestamps stay exact.
    if (
      typeof value === "string" &&
      /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/.test(value)
    ) {
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
        timeZone: this._hass?.config?.time_zone || "UTC",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        fractionalSecondDigits: 3,
        hourCycle: "h23",
      }).formatToParts(value);
      const p = Object.fromEntries(
        parts.map((part) => [part.type, part.value]),
      );
      value +=
        target -
        Date.parse(
          `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}.${p.fractionalSecond}Z`,
        );
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
    const starts = [
      ...(Array.isArray(cascade.schedule) ? cascade.schedule : []).map((b) =>
        this._timestamp(b?.start),
      ),
      ...this._memberDetails(cascade).flatMap((m) =>
        this._points(m).map((p) => p.time),
      ),
    ].filter(Number.isFinite);
    const start = starts.length ? Math.min(...starts) : Date.now();
    const ends = [
      ...(Array.isArray(cascade.schedule) ? cascade.schedule : []).map((b) =>
        this._timestamp(b?.end),
      ),
      ...this._memberDetails(cascade).flatMap((m) =>
        this._points(m).map((p) => p.time),
      ),
    ].filter(Number.isFinite);
    const end = ends.length ? Math.max(...ends) : start;
    const [from, until] = this._window(period);
    return [
      Math.max(from, start),
      Math.min(
        until,
        end,
        start + Math.max(6, Math.min(96, this._config.hours)) * 3600000,
      ),
    ];
  }

  _blocks(cascade, period = "all") {
    const [from, until] = this._horizon(cascade, period);
    const schedule = Array.isArray(cascade?.chart_schedule)
      ? cascade.chart_schedule
      : cascade?.schedule;
    return (Array.isArray(schedule) ? schedule : [])
      .flatMap((block) => {
        const start = this._timestamp(block?.start);
        const end = this._timestamp(block?.end);
        if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start)
          return [];
        const a = Math.max(start, from),
          b = Math.min(end, until);
        if (b <= a) return [];
        return [
          {
            ...block,
            start: a,
            end: b,
            roundingWh: Array.isArray(cascade?.chart_schedule) ? 0.000001 : 0.1,
            fraction: (b - a) / (end - start),
            activities: (Array.isArray(block.activities)
              ? block.activities
              : []
            ).filter((v) => v && typeof v === "object"),
          },
        ];
      })
      .sort((a, b) => a.start - b.start)
      .slice(0, MAX_CASCADE_POINTS);
  }

  _energy(block, kind, id, field = "energy_wh") {
    if (kind === "root")
      return num(block.root_input_wh) == null
        ? null
        : num(block.root_input_wh) * block.fraction;
    const activities = block.activities.filter(
      (a) =>
        (kind === "aux"
          ? a.kind === "terminal" && a.source === "aux"
          : a.kind === kind) &&
        (id == null || a.load_id === id),
    );
    if (activities.some((a) => num(a[field]) == null)) return null;
    return (
      activities.reduce((sum, a) => sum + num(a[field]), 0) * block.fraction
    );
  }

  _kwh(wh) {
    return wh == null ? null : wh / 1000;
  }

  _total(blocks, kind, id, field) {
    const values = blocks.map((b) => this._energy(b, kind, id, field));
    return values.some((v) => v == null)
      ? null
      : values.reduce((sum, v) => sum + v, 0);
  }

  _flowList(blocks, cascade, memberId = null) {
    const flows = new Map();
    const add = (key, label, wh, color) => {
      const previous = flows.get(key);
      flows.set(key, {
        label,
        color,
        wh:
          wh == null || previous?.wh === null ? null : (previous?.wh || 0) + wh,
      });
    };
    for (const block of blocks) {
      for (const a of block.activities) {
        if (memberId && a.load_id !== memberId && a.source_load_id !== memberId)
          continue;
        const name = a.name || a.load_id || "?";
        const wh =
          num(a.energy_wh) == null ? null : num(a.energy_wh) * block.fraction;
        if (a.kind === "charge") {
          add(
            `charge:${a.load_id}`,
            `${localize(this._hass, "root")} → ${name}`,
            wh,
            CASCADE_CHARGE_COLOR,
          );
          add(
            `stored:${a.load_id}`,
            `${name} · ${localize(this._hass, "card_stored_in_battery")}`,
            num(a.stored_energy_wh) == null
              ? null
              : num(a.stored_energy_wh) * block.fraction,
            CASCADE_CHARGE_COLOR,
          );
        } else if (a.kind === "discharge") {
          add(
            `discharge:${a.load_id}`,
            `${name} · ${localize(this._hass, "card_battery_withdrawal_incl_losses")}`,
            wh,
            CASCADE_DISCHARGE_COLOR,
          );
        } else if (a.kind === "terminal") {
          const source =
            a.source === "root"
              ? localize(this._hass, "root")
              : a.source_name ||
                this._memberDetails(cascade).find(
                  (m) => m.load_id === a.source_load_id,
                )?.name ||
                localize(this._hass, "card_storage_unknown_source");
          add(
            `terminal:${a.source}:${a.source_load_id}`,
            `${source} → ${name}`,
            wh,
            CASCADE_TERMINAL_COLOR,
          );
        }
      }
    }
    const root = this._total(blocks, "root");
    const charge = this._total(blocks, "charge");
    const rootDeliveries = blocks.flatMap((b) =>
      b.activities
        .filter((a) => a.kind === "terminal" && a.source === "root")
        .map((a) =>
          num(a.energy_wh) == null ? null : num(a.energy_wh) * b.fraction,
        ),
    );
    const terminalRoot = rootDeliveries.some((wh) => wh == null)
      ? null
      : rootDeliveries.reduce((sum, wh) => sum + wh, 0);
    if (
      !memberId &&
      root != null &&
      charge != null &&
      terminalRoot != null &&
      root - charge - terminalRoot > 0.5
    ) {
      add(
        "overhead",
        localize(this._hass, "card_other_energy_balance_residual"),
        root - charge - terminalRoot,
        CASCADE_OUTPUT_COLOR,
      );
    }
    return [...flows.entries()]
      .map(([key, f]) =>
        key === "overhead"
          ? `<li class="balance-residual"><details data-view-key="balance-${esc(cascade.cascade_id || cascade.terminal_load_id)}-${esc(blocks[0]?.start || "all")}"><summary>${esc(f.label)}: ${this._energyText(f.wh)}</summary>${esc(localize(this._hass, "card_difference_between_root_input_charge_input_and_direct_terminal_supply_includes_modelled_overhead_and_rounding"))}</details></li>`
          : `<li><span class="flow-label"><i style="background:${f.color}"></i>${esc(f.label)}</span><strong>${this._historyButton(this._historyEntity(cascade, key.startsWith("stored:") ? "soc" : key.startsWith("terminal:") ? "terminal" : key.split(":")[0], key.slice(key.indexOf(":") + 1)), this._energyText(f.wh))}</strong></li>`,
      )
      .join("");
  }

  _points(member) {
    // Missing forecasts must not become a fabricated constant SOC forecast.
    return (Array.isArray(member?.soc_forecast) ? member.soc_forecast : [])
      .map((p) => ({ time: this._timestamp(p?.t), value: num(p?.soc) }))
      .filter(
        (p) =>
          Number.isFinite(p.time) &&
          p.value != null &&
          p.value >= 0 &&
          p.value <= 100,
      )
      .sort((a, b) => a.time - b.time)
      .slice(0, MAX_CASCADE_POINTS);
  }

  _socAt(points, time) {
    if (!points.length || time < points[0].time || time > points.at(-1).time)
      return null;
    const next = points.findIndex((p) => p.time >= time);
    if (next === 0 || points[next].time === time) return points[next].value;
    const a = points[next - 1],
      b = points[next];
    return (
      a.value + ((b.value - a.value) * (time - a.time)) / (b.time - a.time)
    );
  }

  _series(cascade, kind, id, period, mode) {
    const focusKey = `chart-${cascade.cascade_id || cascade.terminal_load_id}-${kind}-${id || "root"}-${mode || "power"}`;
    const [from, until] = this._horizon(cascade, period);
    if (until < from)
      return {
        points: [],
        unit: kind === "soc" ? "%" : mode === "energy" ? "kWh" : "W",
        label: "",
      };
    if (kind === "soc") {
      const member = this._memberDetails(cascade).find((m) => m.load_id === id);
      const raw = this._points(member);
      const points = raw.filter((p) => p.time >= from && p.time <= until);
      for (const time of [from, until]) {
        const value = this._socAt(raw, time);
        if (
          Number.isFinite(time) &&
          value != null &&
          !points.some((p) => p.time === time)
        )
          points.push({ time, value });
      }
      return {
        from,
        until,
        focusKey,
        points: points.sort((a, b) => a.time - b.time),
        unit: "%",
        label: localize(this._hass, "card_state_of_charge"),
        historyEntity: this._historyEntity(cascade, kind, id),
        target: num(member?.target_soc_percent),
      };
    }
    const blocks = this._blocks(cascade, period);
    const points = [];
    let total = 0,
      previousEnd = from;
    for (const block of blocks) {
      const wh = this._energy(block, kind, id);
      if (wh == null)
        return {
          points: [],
          unit: mode === "energy" ? "kWh" : "W",
          label: localize(this._hass, "card_incomplete_energy_data"),
        };
      if (previousEnd != null && block.start > previousEnd) {
        points.push(
          { time: previousEnd, value: mode === "energy" ? total / 1000 : 0 },
          { time: block.start, value: mode === "energy" ? total / 1000 : 0 },
        );
      }
      const watts = wh / ((block.end - block.start) / 3600000);
      points.push({
        time: block.start,
        value: mode === "energy" ? total / 1000 : watts,
      });
      total += wh;
      points.push({
        time: block.end,
        value: mode === "energy" ? total / 1000 : watts,
      });
      previousEnd = block.end;
    }
    if (previousEnd < until) {
      points.push(
        { time: previousEnd, value: mode === "energy" ? total / 1000 : 0 },
        { time: until, value: mode === "energy" ? total / 1000 : 0 },
      );
    }
    return {
      from,
      until,
      focusKey,
      points,
      blocks,
      kind,
      id,
      mode,
      historyEntity: this._historyEntity(cascade, kind, id),
      unit: mode === "energy" ? "kWh" : "W",
      label:
        mode === "energy"
          ? localize(this._hass, "card_cumulative_energy")
          : cascade.chart_resolution === "activity"
            ? localize(this._hass, "card_planned_power")
            : localize(this._hass, "card_average_power_per_time_slot"),
    };
  }

  _valueAt(series, time) {
    if (series.unit === "%") return this._socAt(series.points, time);
    if (
      !series.points.length ||
      time < series.points[0].time ||
      time > series.points.at(-1).time
    )
      return null;
    if (series.mode === "energy") {
      return (
        series.blocks.reduce(
          (sum, b) =>
            sum +
            this._energy(b, series.kind, series.id) *
              Math.max(0, Math.min(1, (time - b.start) / (b.end - b.start))),
          0,
        ) / 1000
      );
    }
    const block = series.blocks.find((b) => time >= b.start && time < b.end);
    return block
      ? this._energy(block, series.kind, series.id) /
          ((block.end - block.start) / 3600000)
      : 0;
  }

  _plot(series, owner, color, compact = false) {
    const index = this._charts.length;
    const points = series.points;
    if (!points.length)
      return `<p class="muted">${esc(localize(this._hass, "card_no_forecast_for_this_period"))}</p>`;
    const t0 = series.from ?? points[0].time,
      t1 = Math.max(t0 + 1, series.until ?? points.at(-1).time);
    const multipleDays = this._day(t0) !== this._day(t1 - 1);
    const width = 600,
      height = (compact ? 110 : 210) + (multipleDays ? 14 : 0);
    const left = 48,
      right = 16,
      top = 18,
      bottom = height - (multipleDays ? 42 : 28);
    const peak = Math.max(0, ...points.map((p) => p.value));
    const step = series.unit === "W" ? 50 : 0.1;
    const maximum =
      series.unit === "%" ? 100 : Math.max(step, Math.ceil(peak / step) * step);
    const x = (time) =>
      left + ((time - t0) / (t1 - t0)) * (width - left - right);
    const y = (value) => bottom - (value / maximum) * (bottom - top);
    this._charts.push({
      ...series,
      owner,
      t0,
      t1,
      width,
      left,
      right,
      top,
      bottom,
      kbIndex: null,
    });
    const stepHours =
      t1 - t0 > 36 * 3600000
        ? 12
        : t1 - t0 > 12 * 3600000
          ? 6
          : t1 - t0 > 4 * 3600000
            ? 2
            : 1;
    const ticks = [t0];
    for (let t = Math.ceil(t0 / 3600000) * 3600000; t < t1; t += 3600000) {
      const hour = Number(
        new Intl.DateTimeFormat("en-GB", {
          timeZone: this._hass.config.time_zone || "UTC",
          hour: "2-digit",
          hourCycle: "h23",
        }).format(t),
      );
      if (
        hour % stepHours === 0 &&
        t - ticks.at(-1) > (t1 - t0) * 0.13 &&
        t1 - t > (t1 - t0) * 0.13
      )
        ticks.push(t);
    }
    ticks.push(t1);
    const target =
      series.target == null
        ? ""
        : `<line x1="${left}" x2="${width - right}" y1="${y(series.target)}" y2="${y(series.target)}" class="soc-target"/>`;
    return `<div class="plot"><svg id="chart-${index}" data-focus-key="${esc(`${series.focusKey}-${compact ? "overview" : "detail"}`)}" viewBox="0 0 ${width} ${height}" tabindex="0" role="img" aria-label="${esc(`${owner}: ${series.label} · ${series.historyEntity ? localize(this._hass, "card_enter_open_history") : ""}${localize(this._hass, "card_forecast_arrow_keys_to_select_time")}`)}">
      <text x="2" y="${top + 4}" class="axis">${this._number(maximum, series.unit === "kWh" ? 1 : 0)}</text><text x="6" y="${bottom}" class="axis">0</text>
      <line x1="${left}" x2="${width - right}" y1="${bottom}" y2="${bottom}" class="grid"/>${target}
      <polyline points="${points.map((p) => `${x(p.time)},${y(p.value)}`).join(" ")}" fill="none" stroke="${color}" class="forecast-line"/>
      ${ticks.map((time, i) => `<text x="${x(time)}" y="${height - 7}" text-anchor="${i === 0 ? "start" : i === ticks.length - 1 ? "end" : "middle"}" class="axis">${multipleDays ? `<tspan x="${x(time)}" dy="-14">${esc(new Intl.DateTimeFormat(this._hass?.language || "en", { timeZone: this._hass?.config?.time_zone || "UTC", day: "2-digit", month: "2-digit" }).format(time))}</tspan><tspan x="${x(time)}" dy="14">${esc(this._time(time))}</tspan>` : esc(this._time(time))}</text>`).join("")}
      <g id="marker-${index}"></g></svg></div><div id="readout-${index}" class="readout" aria-live="polite">${esc(`${series.label} · ${this._time(t0, true)} – ${this._time(t1, true)} · ${localize(this._hass, "card_forecast_tab")}`)}</div>`;
  }

  _showTime(time) {
    this._cursorTime = time;
    (this._tracks || []).forEach((track, index) => {
      const marker = this.shadowRoot.getElementById(`activity-marker-${index}`);
      const readout = this.shadowRoot.getElementById(
        `activity-readout-${index}`,
      );
      if (!marker || !readout) return;
      const visible = time >= track.from && time < track.until;
      marker.hidden = !visible;
      if (!visible) {
        readout.textContent = "";
        return;
      }
      marker.style.left = `${(100 * (time - track.from)) / (track.until - track.from)}%`;
      const active = track.intervals.filter(
        (a) => time >= a.start && time < a.end,
      );
      const state = active.some((a) => a.exact)
        ? localize(this._hass, "card_on")
        : active.length
          ? localize(this._hass, "card_switching_state_unknown")
          : localize(this._hass, "card_off");
      readout.textContent = `${this._time(time)} · ${state}`;
    });
    this._charts.forEach((chart, index) => {
      const marker = this.shadowRoot.getElementById(`marker-${index}`);
      const readout = this.shadowRoot.getElementById(`readout-${index}`);
      if (!marker || !readout) return;
      if (time < chart.t0 || time > chart.t1) {
        marker.innerHTML = "";
        readout.textContent = `${chart.label} · ${this._time(chart.t0, true)} – ${this._time(chart.t1, true)} · ${localize(this._hass, "card_forecast_tab")}`;
        return;
      }
      const value = this._valueAt(chart, time);
      if (value == null) {
        marker.innerHTML = "";
        readout.textContent = `${this._time(time, true)} · ${localize(this._hass, "card_no_forecast_value_at_this_time")}`;
        return;
      }
      const px =
        chart.left +
        ((time - chart.t0) / (chart.t1 - chart.t0)) *
          (chart.width - chart.left - chart.right);
      marker.innerHTML =
        time < chart.t0 || time > chart.t1
          ? ""
          : `<line x1="${px}" x2="${px}" y1="${chart.top}" y2="${chart.bottom}" class="marker"/>`;
      readout.textContent = `${this._time(time, true)} · ${chart.label}: ${this._number(value, chart.unit === "W" ? 0 : chart.unit === "kWh" ? 3 : 1)} ${chart.unit} · ${localize(this._hass, "card_forecast_tab")}`;
    });
  }

  _ui(cascade, index) {
    const key = cascade.cascade_id || `${index}:${cascade.name}`;
    this._views ||= new Map();
    if (!this._views.has(key))
      this._views.set(key, { period: "today", detail: null, mode: "power" });
    return this._views.get(key);
  }

  _button(label, index, action, extra = "", selected = false) {
    const identity = this._cascades()[index]?.cascade_id || index;
    return `<button type="button" data-focus-key="${esc(`control-${identity}-${action}-${extra}`)}" data-cascade="${index}" data-action="${action}" ${extra} aria-pressed="${selected}">${esc(label)}</button>`;
  }

  _groups(blocks) {
    // Legacy energies use 0.1 Wh; activity-resolved chart energies use 0.000001 Wh.
    // Compare powers with that rounding uncertainty, not a fixed W tolerance.
    const describe = (b) => {
      const hours = (b.end - b.start) / 3600000;
      const activities = b.activities
        .map((a) => ({
          key: JSON.stringify([
            a.kind,
            a.load_id,
            a.source,
            a.source_load_id,
            [...(Array.isArray(a.sources) ? a.sources : [])].sort(),
          ]),
          values: [a.energy_wh, a.stored_energy_wh].map((v) =>
            num(v) == null ? null : (num(v) * b.fraction) / hours,
          ),
        }))
        .sort((a, b) => a.key.localeCompare(b.key));
      return {
        activities,
        root:
          num(b.root_input_wh) == null ? null : this._energy(b, "root") / hours,
        error: (((b.roundingWh ?? 0.1) / 2) * b.fraction) / hours,
      };
    };
    const groups = [];
    for (const block of blocks) {
      const d = describe(block),
        last = groups.at(-1),
        prior = last?.description;
      const equal = (a, b) =>
        a == null || b == null
          ? a === b
          : Math.abs(a - b) <= d.error + prior.error + 1e-7;
      const same =
        prior &&
        equal(d.root, prior.root) &&
        d.activities.length === prior.activities.length &&
        d.activities.every(
          (a, i) =>
            a.key === prior.activities[i].key &&
            a.values.every((v, j) => equal(v, prior.activities[i].values[j])),
        );
      if (
        last &&
        last.end === block.start &&
        same &&
        ![...block.activities, ...last.activities].some(
          (a) => a.kind === "transition",
        ) &&
        this._day(last.start) === this._day(block.start)
      ) {
        last.end = block.end;
        last.blocks.push(block);
      } else groups.push({ ...block, description: d, blocks: [block] });
    }
    return groups;
  }

  _historyEntity(cascade, kind, id) {
    const member = this._memberDetails(cascade).find((m) => m.load_id === id);
    // An AC output includes pass-through: it is not an own-discharge meter.
    const entity =
      kind === "discharge"
        ? member?.history_entities?.output
        : kind === "root"
          ? cascade.root_history_entity
          : kind === "terminal" || kind === "aux"
            ? cascade.terminal_history_entity
            : member?.history_entities?.[kind];
    return typeof entity === "string" && this._hass.states?.[entity]
      ? entity
      : null;
  }

  _openHistory(entityId) {
    if (!entityId || !this._hass.states?.[entityId]) return;
    this.dispatchEvent(
      new CustomEvent("hass-more-info", {
        detail: { entityId },
        bubbles: true,
        composed: true,
      }),
    );
  }

  _historyButton(entityId, label) {
    return entityId
      ? `<button type="button" class="history" data-history="${esc(entityId)}" title="${esc(`${localize(this._hass, "card_open_home_assistant_history")}: ${this._hass.states?.[entityId]?.attributes?.friendly_name || entityId}`)}">${label}</button>`
      : label;
  }

  _energyText(wh) {
    return wh != null && wh > 0 && wh < 10
      ? `${this._number(wh, 1)} Wh`
      : `${this._number(wh == null ? null : wh / 1000)} kWh`;
  }

  _activityTracks(cascade, id, period) {
    const [from, until] = this._horizon(cascade, period);
    if (until <= from || !Array.isArray(cascade.activity_intervals)) return "";
    const kinds =
      id === cascade.terminal_load_id
        ? [
            [
              "terminal",
              localize(this._hass, "card_running"),
              CASCADE_TERMINAL_COLOR,
            ],
          ]
        : [
            [
              "charge",
              localize(this._hass, "card_charging"),
              CASCADE_CHARGE_COLOR,
            ],
            [
              "discharge",
              localize(this._hass, "card_discharging"),
              CASCADE_DISCHARGE_COLOR,
            ],
            [
              "output",
              localize(this._hass, "card_ac_output"),
              CASCADE_OUTPUT_COLOR,
            ],
          ];
    this._tracks ||= [];
    return `<div class="activity-tracks"><small>${localize(this._hass, "card_planned_activity_bar_on_gap_off")}</small>${kinds
      .map(([kind, label, color]) => {
        const intervals = cascade.activity_intervals
          .filter((a) => a.load_id === id && a.kind === kind)
          .map((a) => ({
            start: Math.max(from, this._timestamp(a.start)),
            end: Math.min(until, this._timestamp(a.end)),
            exact: a.exact === true,
          }))
          .filter(
            (a) =>
              Number.isFinite(a.start) &&
              Number.isFinite(a.end) &&
              a.end > a.start,
          )
          .sort((a, b) => a.start - b.start);
        const merged = [];
        for (const item of intervals) {
          const last = merged.at(-1);
          if (last && last.end >= item.start && last.exact === item.exact)
            last.end = Math.max(last.end, item.end);
          else merged.push({ ...item });
        }
        const index = this._tracks.length;
        this._tracks.push({ from, until, intervals: merged });
        // The axis anchors each sibling tooltip across the available width.
        // A fixed child tooltip is clipped by HA dashboard containing blocks;
        // anchoring to the tiny bar would also overflow at the right edge.
        return `<div class="activity-row"><span>${label} <span id="activity-readout-${index}" aria-live="polite"></span></span><div id="activity-axis-${index}" class="activity-axis" tabindex="0" role="group" aria-label="${esc(`${label}: ${localize(this._hass, "card_forecast_arrow_keys_to_select_time")}`)}"><span id="activity-marker-${index}" class="activity-marker" hidden aria-hidden="true"></span>${merged
          .map((a) => {
            const description = `${label}: ${this._time(a.start, true)} – ${this._time(a.end, true)} · ${a.exact ? localize(this._hass, "card_planned") : localize(this._hass, "card_time_slot_switching_times_unknown")}`;
            return `<span tabindex="0" class="activity-bar ${a.exact ? "" : "estimated"}" style="left:${(100 * (a.start - from)) / (until - from)}%;width:${(100 * (a.end - a.start)) / (until - from)}%;background-color:${color}" aria-label="${esc(description)}"></span><span class="activity-tip" aria-hidden="true">${esc(description)}</span>`;
          })
          .join("")}</div></div>`;
      })
      .join("")}</div>`;
  }

  _phaseTitle(block) {
    const charge = block.activities
      .filter((a) => a.kind === "charge")
      .map((a) => a.name || a.load_id);
    if (charge.length)
      return `${localize(this._hass, "card_charging")}: ${charge.join(" · ")}`;
    const terminal = block.activities.find((a) => a.kind === "terminal");
    if (terminal)
      return `${terminal.name || terminal.load_id} · ${terminal.source === "root" ? localize(this._hass, "card_from_root") : localize(this._hass, "card_from_storage")}`;
    return localize(this._hass, "card_source_change_preparation");
  }

  _agenda(cascade, index, view) {
    const blocks = this._blocks(cascade, view.period);
    const heading = localize(this._hass, "card_planned_sequence");
    return `<div class="section-heading"><h3>${heading}</h3></div>
      ${
        !blocks.length
          ? `<p class="muted">${esc(localize(this._hass, "cascade_no_data"))}</p>`
          : `<ol class="agenda">${this._groups(blocks)
              .map((block, position, groups) => {
                const outputs = block.activities
                  .filter((a) => a.kind === "output")
                  .map((a) => a.name || a.load_id);
                const transitions = block.activities
                  .filter((a) => a.kind === "transition")
                  .reduce(
                    (sum, a) => sum + (num(a.minutes) || 0) * block.fraction,
                    0,
                  );
                const previous = groups[position - 1];
                const gap =
                  previous && previous.end < block.start
                    ? `<li class="event pause"><time>${esc(this._time(previous.end, true))} – ${esc(this._time(block.start))}</time><span>${esc(localize(this._hass, "card_no_activity_in_the_published_plan"))}</span></li>`
                    : "";
                return `${gap}<li class="event"><time>${esc(this._time(block.start, true))} – ${esc(this._time(block.end))}</time>
          <div class="event-body"><div class="event-title"><b>${esc(this._phaseTitle(block))}</b></div>
          <ul class="flows">${this._flowList(block.blocks, cascade)}</ul>
          ${outputs.length ? `<p class="muted">${esc(localize(this._hass, "card_ac_outputs_active"))}: ${esc(outputs.join(" · "))}</p>` : ""}
          ${transitions ? `<p class="muted">${esc(localize(this._hass, "card_source_transition"))}: ${this._number(transitions, 1)} min</p>` : ""}
          </div></li>`;
              })
              .join("")}</ol>`
      }`;
  }

  _details(cascade, index, view) {
    if (!view.detail) return "";
    const { kind, id } = view.detail;
    const member = this._memberDetails(cascade).find((m) => m.load_id === id);
    const title =
      kind === "soc"
        ? member?.name || id
        : kind === "root"
          ? localize(this._hass, "cascade_root_input")
          : kind === "aux"
            ? localize(this._hass, "card_from_storage_terminal")
            : cascade.terminal_name || cascade.terminal_load_id || "?";
    const controls =
      kind === "soc"
        ? [
            ["soc", "SOC"],
            ["charge", localize(this._hass, "card_charge_input")],
            ["discharge", localize(this._hass, "card_battery_withdrawal")],
          ]
        : [];
    const metric = kind === "soc" ? view.detail.metric || "soc" : kind;
    const blocks = this._blocks(cascade, view.period);
    return `<section class="details" id="details-${index}" tabindex="-1"><div class="section-heading"><h3>${esc(title)}</h3>${this._button(localize(this._hass, "card_close"), index, "close")}</div>
      <nav>${controls.map(([key, label]) => this._button(label, index, "metric", `data-metric="${key}"`, metric === key)).join("")}
      ${
        metric !== "soc"
          ? [
              ["power", localize(this._hass, "card_power")],
              ["energy", localize(this._hass, "card_energy")],
            ]
              .map(([key, label]) =>
                this._button(
                  label,
                  index,
                  "mode",
                  `data-mode="${key}"`,
                  view.mode === key,
                ),
              )
              .join("")
          : ""
      }</nav>
      <p class="muted">${esc(cascade.chart_resolution === "activity" ? localize(this._hass, "card_forecast_curves_and_bars_follow_the_same_planned_charging_and_discharging_times_no_measurement_history") : localize(this._hass, "card_forecast_detailed_timing_unavailable_power_and_soc_are_averaged_over_time_slots_no_measurement_history"))}</p>
      ${this._plot(this._series(cascade, metric, id, view.period, view.mode), title, metric === "soc" ? CASCADE_SOC_COLORS[0] : metric === "charge" ? CASCADE_CHARGE_COLOR : metric === "discharge" ? CASCADE_DISCHARGE_COLOR : CASCADE_ROOT_COLOR)}
      ${kind === "soc" ? this._activityTracks(cascade, id, view.period) : kind === "terminal" ? this._activityTracks(cascade, cascade.terminal_load_id, view.period) : ""}
      ${metric === "soc" && num(member?.target_soc_percent) != null ? `<p class="muted">${esc(localize(this._hass, "cascade_discharge_target"))}: ${this._number(num(member.target_soc_percent), 0)} %</p>` : ""}
      <h4>${esc(localize(this._hass, "card_source_recipient_selected_period"))}</h4><ul class="flows">${this._flowList(blocks, cascade, kind === "soc" ? id : null)}</ul>
      <p class="muted">${esc(localize(this._hass, "card_root_is_the_cascade_input_arrows_show_source_and_recipient_along_the_chain_above_ac_pass_through_is_not_battery_charging_energy_per_ac_output_is_unavailable_missing_values"))}</p></section>`;
  }

  _decisions(cascade) {
    const data =
      this._hass?.states?.[this._entityId()]?.attributes?.load_decisions || {};
    const ids = [
      ...this._memberDetails(cascade).map((m) => m.load_id),
      cascade.terminal_load_id,
    ];
    const rows = ids.flatMap((id) => {
      const decision = data[id];
      if (!decision || typeof decision !== "object") return [];
      const messages = [];
      const release = this._timestamp(decision.not_before);
      if (Number.isFinite(release)) {
        const when = new Intl.DateTimeFormat(this._hass.language || "en", {
          timeZone: this._hass.config?.time_zone || "UTC",
          hour: "2-digit",
          minute: "2-digit",
          second: "2-digit",
        }).format(release);
        messages.push(
          `${esc(localize(this._hass, "card_earliest_start_after_minimum_pause"))}: ${esc(when)}`,
        );
      }
      messages.push(...executionLines(this._hass, decision.execution));
      if (decision.waiting_for_confirmation)
        messages.push(esc(localize(this._hass, "feedin_wait")));
      for (const rejection of Array.isArray(decision.rejected_candidates)
        ? decision.rejected_candidates
        : []) {
        const time = this._timestamp(rejection?.start);
        if (!Number.isFinite(time)) continue;
        const when = new Intl.DateTimeFormat(this._hass.language || "en", {
          timeZone: this._hass.config?.time_zone || "UTC",
          weekday: "short",
          hour: "2-digit",
          minute: "2-digit",
        }).format(time);
        messages.push(
          `${esc(when)} · ${esc(localize(this._hass, "candidate_rejected"))}: ${esc(localize(this._hass, rejection.reason))}`,
        );
      }
      return messages.map(
        (message) =>
          `<li><strong>${esc(decision.name || id)}</strong>: ${message}</li>`,
      );
    });
    return rows.length
      ? `<details data-view-key="cascade-decisions-${esc(cascade.cascade_id || cascade.terminal_load_id)}" class="muted"><summary>${esc(localize(this._hass, "card_planning_decisions"))}</summary><ul>${rows.join("")}</ul></details>`
      : "";
  }

  _cardTitle() {
    return localize(this._hass, "card_cascade");
  }

  _emptyText() {
    return localize(this._hass, "card_no_cascades_configured");
  }

  _renderCascade(cascade, index) {
    const view = this._ui(cascade, index);
    const members = this._memberDetails(cascade);
    const blocks = this._blocks(cascade, view.period);
    const phase = cascade.hands_off
      ? "hands_off"
      : cascade.fault
        ? "fault"
        : cascade.phase || "idle";
    const metrics = [
      [
        localize(this._hass, "card_from_storage_to_terminal"),
        this._kwh(this._total(this._blocks(cascade, "all"), "aux")),
        "aux",
        "all",
      ],
      [
        localize(this._hass, "card_root_today_forecast"),
        this._kwh(this._total(this._blocks(cascade, "today"), "root")),
        "root",
        "today",
      ],
      [
        localize(this._hass, "card_root_tomorrow_forecast"),
        this._kwh(this._total(this._blocks(cascade, "tomorrow"), "root")),
        "root",
        "tomorrow",
      ],
    ];
    return `<section class="cascade"><header class="section-heading"><h2>${esc(cascade.name || localize(this._hass, "cascade"))}</h2><span class="badge">${esc(localize(this._hass, STRINGS.en[`cascade_phase_${phase}`] ? `cascade_phase_${phase}` : "cascade_phase_unknown"))}</span></header>
      ${cascade.source_name ? `<p>${esc(localize(this._hass, "source"))}: ${esc(cascade.source_name)}</p>` : ""}
      ${cascade.fault ? `<p class="fault">⚠ ${esc(localize(this._hass, STRINGS.en[`fault_${String(cascade.fault).split(":")[0]}`] ? `fault_${String(cascade.fault).split(":")[0]}` : "fault_unknown"))}${cascade.fault_detail ? ` · ${esc(cascade.fault_detail.entity_id || "")} · ${esc(cascade.fault_detail.observed_state || "?")}` : ""}</p>` : ""}
      <p class="topology">${esc([localize(this._hass, "root"), ...members.map((m) => m.name || m.load_id), cascade.terminal_name || cascade.terminal_load_id || "?"].join(" → "))}</p>
      ${this._decisions(cascade)}
      <h3>${esc(localize(this._hass, "card_plan_overview"))}</h3><div class="metrics">${metrics.map(([label, value, kind, period]) => `<div class="metric"><span>${esc(label)}</span><strong>${this._historyButton(this._historyEntity(cascade, kind), `${this._number(value)} kWh`)}</strong><button type="button" data-cascade="${index}" data-action="detail" data-kind="${kind}" data-period="${period}" aria-controls="details-${index}" aria-expanded="${view.detail?.kind === kind && view.period === period}">${esc(localize(this._hass, "card_open_forecast"))} ↗</button></div>`).join("")}</div>
      <p class="muted">${esc(localize(this._hass, "card_underlined_values_and_chart_clicks_open_measurement_history_output_power_also_includes_pass_through"))}</p>
      <p class="muted">${esc(localize(this._hass, "card_today_shows_the_remaining_plan_from_storage_refers_to_the_full_plan"))}</p>
      ${num(cascade.actual_aux_energy_kwh) != null ? `<p class="muted">${esc(localize(this._hass, "card_actual_used_from_storage_today"))}: ${this._number(num(cascade.actual_aux_energy_kwh))} kWh</p>` : ""}
      <h3>${esc(localize(this._hass, "card_selected_period"))}</h3><div class="period">${[
        ["today", localize(this._hass, "card_today_from_now")],
        ["tomorrow", localize(this._hass, "card_tomorrow")],
        ["all", localize(this._hass, "card_full_plan")],
      ]
        .map(([key, label]) =>
          this._button(
            label,
            index,
            "period",
            `data-period="${key}"`,
            view.period === key,
          ),
        )
        .join("")}</div>
      <div class="chart-grid"><div class="members">${members
        .map(
          (
            member,
            mi,
          ) => `<article class="member"><div class="section-heading"><h3>${esc(member.name || member.load_id)}</h3><strong>${this._historyButton(this._historyEntity(cascade, "soc", member.load_id), `${this._number(num(member.soc_percent), 1)} %`)}</strong></div>
        <p class="muted">${esc(localize(this._hass, "card_state_of_charge_at_plan_start"))} · <span style="color:var(--warning-color,#ffb300)">⋯ ${esc(localize(this._hass, "cascade_discharge_target"))} ${this._number(num(member.target_soc_percent), 0)} %</span></p>
        ${this._plot(this._series(cascade, "soc", member.load_id, view.period), member.name || member.load_id, CASCADE_SOC_COLORS[mi % CASCADE_SOC_COLORS.length], true)}
        ${this._activityTracks(cascade, member.load_id, view.period)}
        <div class="member-energy">${[
          ["charge", localize(this._hass, "card_charge_input"), "energy_wh"],
          [
            "charge",
            localize(this._hass, "card_stored_in_battery_59"),
            "stored_energy_wh",
          ],
          [
            "discharge",
            localize(this._hass, "card_battery_withdrawal"),
            "energy_wh",
          ],
        ]
          .map(([kind, label, field]) => {
            const wh = this._total(blocks, kind, member.load_id, field);
            return `<span>${label}<b>${this._historyButton(this._historyEntity(cascade, field === "stored_energy_wh" ? "soc" : kind, member.load_id), this._energyText(wh))}</b></span>`;
          })
          .join("")}</div>
        ${this._button(localize(this._hass, "card_soc_energy_details"), index, "detail", `data-kind="soc" data-id="${esc(member.load_id)}" aria-controls="details-${index}" aria-expanded="${view.detail?.id === member.load_id}"`)}
      </article>`,
        )
        .join("")}</div>
      <article class="terminal"><div class="section-heading"><h3>${esc(cascade.terminal_name || cascade.terminal_load_id || "?")}</h3>${this._button(localize(this._hass, "card_power_energy"), index, "detail", `data-kind="terminal" aria-controls="details-${index}" aria-expanded="${view.detail?.kind === "terminal"}"`)}</div>
        ${this._plot(this._series(cascade, "terminal", null, view.period, "power"), cascade.terminal_name || cascade.terminal_load_id || "?", CASCADE_TERMINAL_COLOR, true)}${this._activityTracks(cascade, cascade.terminal_load_id, view.period)}</article>
      ${this._details(cascade, index, view)}</div>${this._agenda(cascade, index, view)}</section>`;
  }

  _sizeAxes() {
    // Container queries settle after rendering; preserve readable physical text
    // size instead of shrinking 12px SVG labels together with the viewBox.
    this._charts.forEach((chart, index) => {
      const svg = this.shadowRoot.getElementById(`chart-${index}`);
      const width = svg?.getBoundingClientRect().width;
      if (width)
        svg.querySelectorAll(".axis").forEach((label) => {
          label.style.fontSize = `${(12 * chart.width) / width}px`;
        });
    });
  }

  _bindCharts() {
    (this._tracks || []).forEach((track, index) => {
      const axis = this.shadowRoot.getElementById(`activity-axis-${index}`);
      if (!axis) return;
      const move = (event) => {
        const rect = axis.getBoundingClientRect();
        if (!rect.width) return;
        const fraction = Math.max(
          0,
          Math.min(1, (event.clientX - rect.left) / rect.width),
        );
        this._showTime(track.from + fraction * (track.until - track.from));
      };
      axis.addEventListener("pointermove", move);
      axis.addEventListener("pointerdown", move);
      axis.addEventListener("keydown", (event) => {
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
          return;
        event.preventDefault();
        const times = [
          ...new Set([
            track.from,
            track.until,
            ...track.intervals.flatMap((a) => [a.start, a.end]),
          ]),
        ].sort((a, b) => a - b);
        const current = this._cursorTime ?? track.from;
        const time =
          event.key === "Home"
            ? track.from
            : event.key === "End"
              ? track.until
              : event.key === "ArrowLeft"
                ? (times.findLast((t) => t < current) ?? track.from)
                : (times.find((t) => t > current) ?? track.until);
        this._showTime(time);
      });
    });
    this._charts.forEach((chart, index) => {
      const svg = this.shadowRoot.getElementById(`chart-${index}`);
      if (!svg) return;
      const move = (event) => {
        const rect = svg.getBoundingClientRect();
        if (!rect.width) return;
        const x = ((event.clientX - rect.left) / rect.width) * chart.width;
        const fraction = Math.max(
          0,
          Math.min(
            1,
            (x - chart.left) / (chart.width - chart.left - chart.right),
          ),
        );
        this._showTime(chart.t0 + fraction * (chart.t1 - chart.t0));
      };
      svg.addEventListener("pointermove", move);
      svg.addEventListener("pointerdown", move);
      svg.addEventListener("click", () =>
        this._openHistory(chart.historyEntity),
      );
      svg.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && chart.historyEntity) {
          event.preventDefault();
          this._openHistory(chart.historyEntity);
          return;
        }
        const times = [...new Set(chart.points.map((p) => p.time))];
        if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key))
          return;
        event.preventDefault();
        const current =
          this._cursorTime == null
            ? 0
            : Math.max(
                0,
                times.findLastIndex((time) => time <= this._cursorTime),
              );
        chart.kbIndex =
          event.key === "Home"
            ? 0
            : event.key === "End"
              ? times.length - 1
              : event.key === "ArrowLeft"
                ? Math.max(0, current - 1)
                : Math.min(times.length - 1, current + 1);
        this._showTime(times[chart.kbIndex]);
      });
    });
    this.shadowRoot
      .querySelectorAll("[data-history]")
      .forEach((button) =>
        button.addEventListener("click", () =>
          this._openHistory(button.dataset.history),
        ),
      );
    this.shadowRoot.querySelectorAll("button[data-action]").forEach((button) =>
      button.addEventListener("click", () => {
        const index = Number(button.dataset.cascade),
          cascade = this._cascades()[index];
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
        else if (action === "metric" && view.detail)
          view.detail.metric = button.dataset.metric;
        this._cursorTime = null;
        const dataset = { ...button.dataset };
        this._render();
        if (action === "detail") {
          const panel = this.shadowRoot.getElementById(`details-${index}`);
          panel?.focus({ preventScroll: true });
          panel?.scrollIntoView({ block: "nearest", behavior: "smooth" });
        } else {
          const buttons = [
            ...this.shadowRoot.querySelectorAll("button[data-action]"),
          ];
          const next = buttons.find((b) =>
            Object.entries(dataset).every(
              ([key, value]) => b.dataset[key] === value,
            ),
          );
          (
            next || buttons.find((b) => b.dataset.cascade === String(index))
          )?.focus({ preventScroll: true });
        }
      }),
    );
    if (this._cursorTime != null) this._showTime(this._cursorTime);
  }

  _render() {
    if (!this._config || !this._hass) return;
    this._charts = [];
    this._tracks = [];
    const entityId = this._entityId();
    const state = this._displayState();
    const cascades = this._cascades();
    const body = !entityId
      ? esc(localize(this._hass, "no_entity"))
      : !state
        ? esc(`${localize(this._hass, "not_found")} ${entityId}`)
        : cascades.length
          ? cascades.map((c, i) => this._renderCascade(c, i)).join("")
          : esc(this._emptyText());
    replaceCardHTML(
      this,
      `<ha-card header="${esc(this._config.title || this._cardTitle())}">${cascade_card_style_0}<div class="wrap">${stateNotice(this._hass, state)}${body}${sourceHealthReport(this._hass, state?.attributes?.source_health)}${operationReport(this._hass, state?.attributes?.operation_report)}${feedinDecisions(this._hass, state?.attributes?.feedin_decisions)}</div></ha-card>`,
    );
    this._bindCharts();
    bindEntityButtons(this);
    if (typeof requestAnimationFrame === "function")
      requestAnimationFrame(() => this._sizeAxes());
  }
}

if (!customElements.get(CASCADE_CARD_TYPE)) {
  customElements.define(CASCADE_CARD_TYPE, BatteryManagerCascadeCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: CASCADE_CARD_TYPE,
    get name() {
      return localize(null, "card_cascade");
    },
    get description() {
      return localize(null, "desc_cascade");
    },
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
