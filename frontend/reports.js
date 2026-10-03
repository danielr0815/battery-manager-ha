import { localize } from "./translations.js";
import { esc } from "./shared.js";

const reportTime = (hass, value) => {
  const at = typeof value === "string" ? Date.parse(value) : NaN;
  return Number.isFinite(at)
    ? new Intl.DateTimeFormat(hass?.language || "en", {
        timeZone: hass?.config?.time_zone || "UTC",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        timeZoneName: "short",
      }).format(at)
    : "—";
};
const reportNumber = (hass, value) =>
  typeof value === "number" && Number.isFinite(value)
    ? new Intl.NumberFormat(hass?.language || "en", {
        maximumFractionDigits: 1,
      }).format(value)
    : "—";

export function stateNotice(hass, state) {
  if (!state || !["unknown", "unavailable"].includes(state.state)) return "";
  const hasPlan = [
    "forecast",
    "consumption_forecast",
    "loads",
    "cascades",
  ].some(
    (key) =>
      Array.isArray(state.attributes?.[key]) &&
      state.attributes[key].length > 0,
  );
  const captured = state.attributes?.plan_metadata?.captured_at;
  const time =
    hasPlan && captured
      ? ` ${localize(hass, "forecast_last_plan")}: ${reportTime(hass, captured)}`
      : "";
  return `<p class="warning" role="status" data-state-warning="${esc(state.state)}" style="padding:12px;color:var(--warning-color,#b26a00)">${esc(localize(hass, hasPlan ? "forecast_stale" : "forecast_unavailable") + time)}</p>`;
}

export function inverterControlReport(hass, attributes, state) {
  const a = attributes || {};
  if (
    !a.inverter_control &&
    !a.live_ac &&
    !["active", "shadow"].includes(a.reserve?.mode)
  )
    return "";
  const t = (key) => localize(hass, key);
  const control = a.inverter_control || {},
    live = a.live_ac || {},
    plan = a.plan_metadata || {};
  const stale = ["unknown", "unavailable"].includes(state);
  const rows = [
    [
      t("report_inverter_limit_planned"),
      `${reportNumber(hass, a.reserve?.inverter_limit_w)} W`,
    ],
    [t("report_plan_captured"), reportTime(hass, plan.captured_at)],
    [t("report_plan_activated"), reportTime(hass, plan.activated_at)],
    [
      t("report_inverter_requested"),
      `${reportNumber(hass, control.requested_limit_w)} W`,
    ],
    [t("report_command_time"), reportTime(hass, control.requested_at)],
    [
      t("report_inverter_observed"),
      `${reportNumber(hass, control.observed_limit_w)} W`,
    ],
    [t("report_feedback_time"), reportTime(hass, control.observed_at)],
    [
      t("report_confirmation"),
      t(
        control.confirmed === true
          ? "report_confirmed"
          : control.confirmed === false
            ? "confirmation_pending"
            : "report_reserve_decision_unknown",
      ),
    ],
  ];
  const reason = control.reason || live.reason;
  if (reason) {
    const key = `live_ac_reason_${reason}`,
      translated = t(key);
    rows.push([
      t("report_live_reason"),
      translated === key ? t("report_reserve_decision_unknown") : translated,
    ]);
  }
  return `<details data-view-key="inverter-control" style="padding:12px"><summary>${esc(t(stale ? "report_inverter_last_known" : "report_inverter_control"))}</summary><p>${esc(t("report_permission_not_power"))}</p><dl>${rows.map(([key, value]) => `<dt>${esc(key)}</dt><dd>${esc(value)}</dd>`).join("")}</dl></details>`;
}

export function sourceHealthReport(hass, health) {
  if (!Array.isArray(health) || !health.length) return "";
  const t = (key) => localize(hass, key);
  const rows = health
    .filter((row) => row && typeof row === "object")
    .slice(0, 100)
    .map((row) => {
      const roleKey = `source_role_${row.role}`,
        role = t(roleKey);
      const statusKey = `source_status_${row.status}`,
        status = t(statusKey);
      const value =
        row.value == null
          ? "—"
          : `${typeof row.value === "number" ? reportNumber(hass, row.value) : row.value}${row.unit ? ` ${row.unit}` : ""}`;
      const coverage =
        row.coverage_start || row.coverage_end
          ? `<br>${esc(t("report_source_coverage"))}: ${esc(reportTime(hass, row.coverage_start))} – ${esc(reportTime(hass, row.coverage_end))}`
          : "";
      const fallback = row.fallback
        ? `<br>${esc(t("report_source_fallback"))}: ${esc(row.fallback)}`
        : "";
      const entity =
        typeof row.entity_id === "string" && row.entity_id.length
          ? `<button type="button" data-entity-id="${esc(row.entity_id)}" data-focus-key="source-${esc(row.role)}-${esc(row.entity_id)}" title="${esc(t("report_open_entity"))}">${esc(row.entity_id)}</button>`
          : esc(t("source_status_not_configured"));
      return `<tr><th scope="row">${esc(role === roleKey ? row.role : role)}<br>${entity}</th><td>${esc(status === statusKey ? t("report_reserve_decision_unknown") : status)}${fallback}</td><td>${esc(value)}</td><td>${esc(reportTime(hass, row.reported_at))}${coverage}${row.boundary ? `<br>${esc(row.boundary)}` : ""}</td></tr>`;
    })
    .join("");
  return `<details data-view-key="source-health" style="padding:12px"><summary>${esc(t("report_sources"))}</summary><div data-scroll-key="source-health-table" style="overflow-x:auto"><table><thead><tr>${["report_source", "report_source_status", "report_source_value", "report_source_reported"].map((key) => `<th scope="col">${esc(t(key))}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div></details>`;
}

export function executionLines(hass, execution) {
  if (!execution || typeof execution !== "object") return [];
  const lines = [];
  const t = (key) => localize(hass, key);
  for (const key of ["minimum_run_until", "predrain_not_before", "check_at"]) {
    const at = execution[key] ? Date.parse(execution[key]) : NaN;
    if (Number.isFinite(at))
      lines.push(
        `${esc(t(key))}: ${esc(new Intl.DateTimeFormat(hass.language || "en", { timeZone: hass.config?.time_zone || "UTC", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(at))}`,
      );
  }
  if (execution.phase === "waiting_stability")
    lines.push(
      `${esc(t("waiting_stability"))} · ${esc(t("stable_progress"))}: ${esc(execution.stable_plans)} / ${esc(execution.required_stable_plans)}`,
    );
  if (execution.confirmation_pending)
    lines.push(esc(t("confirmation_pending")));
  if (
    [
      "waking",
      "waking_members",
      "proving",
      "recovering",
      "testing_terminal",
      "restart_reconciliation",
    ].includes(execution.phase)
  )
    lines.push(esc(t(`cascade_phase_${execution.phase}`)));
  return lines;
}

export function feedinDecisions(hass, decisions) {
  if (!Array.isArray(decisions) || !decisions.length) return "";
  let previous;
  const rows = decisions.slice(0, 300).flatMap((item) => {
    if (
      !item ||
      item.reason === previous ||
      !Number.isFinite(Date.parse(item.start))
    )
      return [];
    previous = item.reason;
    const at = new Intl.DateTimeFormat(hass.language || "en", {
      timeZone: hass.config?.time_zone || "UTC",
      weekday: "short",
      hour: "2-digit",
      minute: "2-digit",
    }).format(Date.parse(item.start));
    return [`<li>${esc(at)} · ${esc(localize(hass, item.reason))}</li>`];
  });
  return `<details data-view-key="feedin-decisions" style="padding:12px"><summary>${esc(localize(hass, "feedin_decisions"))}</summary><ul>${rows.join("")}</ul></details>`;
}

export function reserveReport(hass, reserve) {
  if (!reserve || !["shadow", "active"].includes(reserve.mode)) return "";
  const t = (key) => localize(hass, key);
  const fmt = (value) =>
    typeof value === "number" && Number.isFinite(value)
      ? new Intl.NumberFormat(hass?.language || "en", {
          maximumFractionDigits: 1,
        }).format(value)
      : "—";
  const time = (value) => {
    const at = typeof value === "string" ? Date.parse(value) : NaN;
    return Number.isFinite(at)
      ? new Intl.DateTimeFormat(hass?.language || "en", {
          timeZone: hass?.config?.time_zone || "UTC",
          year: "numeric",
          month: "2-digit",
          day: "2-digit",
          hour: "2-digit",
          minute: "2-digit",
          hourCycle: "h23",
          timeZoneName: "short",
        }).format(at)
      : "—";
  };
  const rows = [
    [
      t("report_preparation_horizon_end"),
      time(reserve.preparation_horizon_end),
    ],
    [t("report_actual_soc"), `${fmt(reserve.actual_soc_percent)} %`],
    [t("report_additional_headroom_needed"), `${fmt(reserve.headroom_wh)} Wh`],
    [
      t("report_unavoidable_export"),
      `${fmt(reserve.unavoidable_export_wh)} Wh`,
    ],
    [t("report_preparation_from"), time(reserve.preparation_start)],
    [t("report_inverter_limit_planned"), `${fmt(reserve.inverter_limit_w)} W`],
    [
      t("report_expected_minimum_soc"),
      `${fmt(reserve.expected_min_soc_percent)} %`,
    ],
    [
      t("report_additional_grid_import_for_reserve"),
      `${fmt(reserve.extra_grid_import_wh)} Wh`,
    ],
    [
      t("report_remaining_battery_discharge"),
      `${fmt(reserve.remaining_discharge_wh)} Wh`,
    ],
    [
      t("report_incidental_psu_charging"),
      `${fmt(reserve.incidental_grid_charge_wh)} Wh`,
    ],
    [
      t("report_expected_48_v_support"),
      `${fmt(reserve.psu48_delivered_wh)} Wh`,
    ],
  ];
  const market = reserve.market;
  if (market) {
    rows.push([
      t("report_market"),
      t(
        !market.enabled
          ? "report_market_off"
          : market.status === "available"
            ? "report_market_available"
            : "report_market_fallback",
      ),
    ]);
    if (market.entity_id)
      rows.push([t("report_market_source"), market.entity_id]);
    if (market.coverage_start || market.coverage_end)
      rows.push([
        t("report_source_coverage"),
        `${time(market.coverage_start)} – ${time(market.coverage_end)}`,
      ]);
    if (market.enabled && market.status !== "available") {
      const key = `source_status_${market.status}`;
      rows.push([
        t("report_source_status"),
        t(key) === key ? t("report_reserve_decision_unknown") : t(key),
      ]);
    }
    if (market.status === "available") {
      rows.push([
        t("report_market_avoided_import"),
        `${fmt(market.avoided_grid_import_wh)} Wh`,
      ]);
      const windows = Array.isArray(market.preferred_intervals)
        ? market.preferred_intervals
        : [];
      const ranges = [];
      for (const window of windows) {
        if (
          !window ||
          !Number.isFinite(Date.parse(window.start)) ||
          !Number.isFinite(Date.parse(window.end))
        )
          continue;
        const previous = ranges.at(-1);
        if (previous && Date.parse(previous.end) === Date.parse(window.start))
          previous.end = window.end;
        else ranges.push({ start: window.start, end: window.end });
      }
      rows.push([
        t("report_market_windows"),
        ranges
          .map((window) => `${time(window.start)} – ${time(window.end)}`)
          .join("; ") || "—",
      ]);
    }
  }
  const reason = reserve.decision_reason;
  const reasonKey = `reserve_decision_${reason}`;
  const translatedReason =
    typeof reason === "string" && reason ? t(reasonKey) : reasonKey;
  const reasonText =
    translatedReason === reasonKey
      ? t("report_reserve_decision_unknown")
      : translatedReason;
  return `<details data-view-key="reserve-policy" style="padding:12px"><summary>${esc(t("report_year_round_reserve"))} · ${esc(reserve.mode === "shadow" ? t("report_shadow") : t("report_active"))}</summary>
    <p>${esc(t("report_preparation_today_tomorrow"))}</p>
    <p>${esc(t("report_reserve_energy_policy"))}</p>
    <p>${esc(t("report_forecast_driven_control_without_a_waiting_period"))}</p>
    <p data-reserve-reason="${esc(reason || "unknown")}">${esc(t("report_reserve_decision"))}: ${esc(reasonText)}</p>
    ${reserve.mode === "shadow" ? `<p>${esc(t("report_reserve_shadow_explanation"))}</p>` : ""}
    ${reserve.dc24_transfer_verified === false ? `<p data-reserve-transfer="unverified">${esc(t("report_dc24_transfer_unverified"))}</p>` : ""}
    <dl>${rows.map(([label, value]) => `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join("")}</dl>
    <p>${esc(t("report_forecast_bands_otherwise_uncalibrated_pv_factor"))}: ${fmt(reserve.upper_pv_factor)} · ${esc(t("report_no_targeted_grid_recharge_feed_in_requires_proven_emergency_benefit"))}</p></details>`;
}

export function operationReport(hass, report) {
  if (!report || (!Array.isArray(report.days) && !report.last_error)) return "";
  const text = (key) => localize(hass, key);
  const fmt = (value, digits = 2) =>
    typeof value === "number" && Number.isFinite(value)
      ? new Intl.NumberFormat(hass.language || "en", {
          maximumFractionDigits: digits,
        }).format(value)
      : "—";
  const labels = {
    pv: "PV",
    ac: text("report_house_consumption"),
    grid_import: text("report_grid_import"),
    grid_export: text("report_grid_export"),
  };
  const days = (Array.isArray(report.days) ? report.days : [])
    .filter((day) => day && typeof day === "object")
    .slice(-30)
    .sort((a, b) => String(b.day).localeCompare(String(a.day)));
  const content = days
    .map((day) => {
      const metrics = day.metrics || {};
      const keys = [
        ...new Set([...Object.keys(labels), ...Object.keys(metrics)]),
      ];
      const rows = keys
        .map((key) => {
          const m = metrics[key];
          const name =
            labels[key] ||
            (key.startsWith("cascade_input:")
              ? `${report.load_names?.[key.slice(14)] || key.slice(14)} · ${text("report_ac_input_including_pass_through")}`
              : report.load_names?.[key.slice(5)] || key);
          const missing = m?.coverage_hours === 0;
          return `<tr><th scope="row">${esc(name)}</th><td>${fmt(missing || m?.planned_wh == null ? null : m.planned_wh / 1000)}</td><td>${fmt(missing || m?.actual_wh == null ? null : m.actual_wh / 1000)}</td><td>${fmt(missing || m?.error_wh == null ? null : m.error_wh / 1000)}</td><td>${fmt(m?.coverage_hours)} h</td></tr>`;
        })
        .join("");
      const loads = Object.entries(day.loads || {})
        .map(
          ([id, l]) =>
            `<li>${esc(report.load_names?.[id] || id)}: ${text("report_actor_time_actual_planned")} ${fmt(l.actual_run_hours)} / ${fmt(l.planned_run_hours)} h · ${text("report_runtime_power_component")} ${fmt(l.execution_error_wh)} / ${fmt(l.power_error_wh)} Wh · ${text("report_coverage_actor_time_energy")} ${fmt(l.runtime_coverage_hours)} / ${fmt(l.coverage_hours)} h</li>`,
        )
        .join("");
      const storageSoc = Object.entries(day.storage_soc || {})
        .map(
          ([id, v]) =>
            `<li>${esc(report.load_names?.[id] || id)} · SOC Min/Max: ${fmt(v.soc_min_percent)} / ${fmt(v.soc_max_percent)} %</li>`,
        )
        .join("");
      return `<details data-view-key="operation-day-${esc(day.segment_id || "legacy")}-${esc(day.day)}"><summary>${esc(day.day)}${day.timezone ? ` (${esc(day.timezone)})` : ""} · ${text("report_switch_requests")}: ${fmt(day.switch_requests, 0)} · ${text("report_state_changes")}: ${fmt(day.state_changes, 0)}</summary>
      <div data-scroll-key="operation-table-${esc(day.segment_id || "legacy")}-${esc(day.day)}" style="overflow-x:auto"><table><thead><tr><th>${text("report_metric")}</th><th>${text("report_planned")} kWh</th><th>${text("report_actual")} kWh</th><th>Δ kWh</th><th>${text("report_coverage")}</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p>SOC ${text("report_observed_min_max")}: ${fmt(day.soc_min_percent)} / ${fmt(day.soc_max_percent)} % · ${text("report_observation_gap")}: ${fmt(day.gap_hours)} h · ${text("report_service_failures")}: ${fmt(day.service_failures, 0)}</p>${loads || storageSoc ? `<ul>${loads}${storageSoc}</ul>` : ""}</details>`;
    })
    .join("");
  return `<details data-view-key="operation-report" style="padding:12px"><summary>${text("report_daily_comparison_plan_and_operation")}</summary>
    <p>${text("report_planned_and_actual_values_cover_the_same_measured_intervals_only_means_m")}</p>
    ${report.dropped_events ? `<p>${text("report_older_detailed_events_were_removed_by_the_retention_limit_daily_reports_")}</p>` : ""}
    ${report.last_error ? `<p role="status" data-history-error>${text("report_a_recording_error_occurred")}</p>` : ""}${days.length ? content : `<p>${esc(text("report_history_empty"))}</p>`}</details>`;
}
