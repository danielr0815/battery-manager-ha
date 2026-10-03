import { BatteryManagerCascadeCard } from "./cascade-card.js";
import {
  num,
  esc,
  CASCADE_ROOT_COLOR,
  LOADS_CARD_TYPE,
  DOCS_URL,
  isForecastEntity,
} from "./shared.js";
import { localize } from "./translations.js";

export class BatteryManagerLoadsCard extends BatteryManagerCascadeCard {
  _cardTitle() {
    return localize(this._hass, "card_battery_manager_loads");
  }
  _emptyText() {
    return localize(this._hass, "card_no_loads_outside_cascades_configured");
  }
  getCardSize() {
    return Math.max(3, this._cascades().length * 8);
  }

  _cascades() {
    const attrs = this._hass?.states?.[this._entityId()]?.attributes || {};
    const managed = new Set(
      (Array.isArray(attrs.cascades) ? attrs.cascades : []).flatMap((c) =>
        c
          ? [
              ...(Array.isArray(c.members) ? c.members : []),
              ...(Array.isArray(c.member_details)
                ? c.member_details.map((m) => m?.load_id)
                : []),
              c.terminal_load_id,
            ]
          : [],
      ),
    );
    return (Array.isArray(attrs.loads) ? attrs.loads : [])
      .filter(
        (load) =>
          load &&
          typeof load === "object" &&
          !load.managed_by_cascade &&
          (!load.load_id || !managed.has(load.load_id)),
      )
      .slice(0, 50)
      .map((load, index) => ({
        ...load,
        cascade_id: load.load_id || `legacy-load-${index}`,
        terminal_load_id: load.load_id,
        member_details: [],
        members: [],
        schedule: (Array.isArray(load.schedule) ? load.schedule : [])
          .filter((b) => b && typeof b === "object")
          .map((b) => ({ ...b, root_input_wh: num(b.wh), activities: [] })),
      }));
  }

  _renderCascade(load, index) {
    const view = this._ui(load, index),
      blocks = this._blocks(load, view.period);
    const source =
      {
        configured: localize(this._hass, "card_configured"),
        learned: localize(this._hass, "card_learned"),
        live: localize(this._hass, "card_from_measurements"),
        saturated: localize(this._hass, "card_power_draw_saturated"),
      }[load.planning_power_source] || "—";
    const metrics = [
      [
        localize(this._hass, "card_today_from_plan_start"),
        `${this._number(num(load.today_kwh))} kWh`,
      ],
      [
        localize(this._hass, "card_tomorrow"),
        `${this._number(num(load.tomorrow_kwh))} kWh`,
      ],
      [
        localize(this._hass, "card_full_plan"),
        `${this._number(num(load.planned_energy_kwh))} kWh`,
      ],
      [
        localize(this._hass, "card_planning_power"),
        `${this._number(num(load.planning_power_w), 0)} W · ${source}`,
      ],
    ];
    const state =
      load.active === true
        ? localize(this._hass, "card_recommendation_on")
        : load.active === false
          ? localize(this._hass, "card_recommendation_off")
          : localize(this._hass, "card_recommendation_unknown");
    return `<section class="cascade"><header class="section-heading"><h2>${esc(load.name || load.load_id || "?")}</h2><span class="badge">${esc(state)}</span></header>
      ${load.available === false ? `<p class="fault">${esc(localize(this._hass, "card_unavailable"))}</p>` : ""}
      ${load.power_warning ? `<p class="fault">${esc(localize(this._hass, "card_power_draw_differs_from_expectation"))}</p>` : ""}
      ${load.soc_stale ? `<p class="fault">${esc(localize(this._hass, "card_stale_telemetry_blocks_execution"))}</p>` : ""}
      ${load.target_soc_percent != null && num(load.soc_percent) === undefined ? `<p class="muted" data-tentative-plan>${esc(localize(this._hass, "card_tentative_wake"))}</p>` : ""}
      <div class="metrics">${metrics.map(([label, value]) => `<div class="metric"><span>${esc(label)}</span><strong>${esc(value)}</strong></div>`).join("")}</div>
      ${load.target_soc_percent != null ? `<p class="muted">${esc(localize(this._hass, "card_state_of_charge_at_plan_start_charge_target"))}: ${this._number(num(load.soc_percent), 1)} % / ${this._number(num(load.target_soc_percent), 1)} %</p>` : ""}
      <p class="muted">${esc(localize(this._hass, "card_robust_power_estimate_while_running"))}: ${this._number(num(load.observed_power_w), 0)} W · ${esc(localize(this._hass, "card_last_learned_power"))}: ${this._number(num(load.learned_power_w), 0)} W</p>
      ${this._decisions(load)}
      <div class="period">${[
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
      <p class="muted">${esc(localize(this._hass, "card_forecast_planned_power_and_cumulative_energy_in_the_selected_period_the_recommendation_is_not_a_measured_switch_state_missing_values"))}</p>
      <div class="chart-grid">${[
        ["power", localize(this._hass, "card_power")],
        ["energy", localize(this._hass, "card_energy")],
      ]
        .map(
          ([mode, label]) =>
            `<article class="terminal"><h3>${esc(label)}</h3>${this._plot(this._series(load, "root", null, view.period, mode), label, CASCADE_ROOT_COLOR, true)}</article>`,
        )
        .join("")}</div>
      <details data-view-key="load-schedule-${esc(load.cascade_id)}"><summary>${esc(localize(this._hass, "card_planned_running_times"))}</summary>
      ${blocks.length ? `<ol class="agenda">${blocks.map((b) => `<li class="event"><time>${esc(this._time(b.start, true))} – ${esc(this._time(b.end, true))}</time><span>${this._energyText(this._energy(b, "root"))}${b.why ? ` · ${esc(localize(this._hass, b.why))}` : ""}</span></li>`).join("")}</ol>` : `<p class="muted">${esc(localize(this._hass, "card_no_running_time_planned_in_the_selected_period"))}</p>`}</details>
    </section>`;
  }
}

if (!customElements.get(LOADS_CARD_TYPE)) {
  customElements.define(LOADS_CARD_TYPE, BatteryManagerLoadsCard);
  window.customCards = window.customCards || [];
  window.customCards.push({
    type: LOADS_CARD_TYPE,
    get name() {
      return localize(null, "loads_picker_name");
    },
    get description() {
      return localize(null, "loads_picker_description");
    },
    preview: true,
    documentationURL: DOCS_URL,
    getEntitySuggestion: (hass, entityId) =>
      entityId.startsWith("sensor.") && isForecastEntity(hass.states[entityId])
        ? { config: { type: `custom:${LOADS_CARD_TYPE}`, entity: entityId } }
        : null,
  });
}
