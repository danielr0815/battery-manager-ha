import { localize } from "./translations.js";
import { esc } from "./shared.js";

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
    [t("report_inverter_limit_now"), `${fmt(reserve.inverter_limit_w)} W`],
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
  if (!report || !Array.isArray(report.days) || !report.days.length) return "";
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
  const days = report.days
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
          return `<tr><th scope="row">${esc(name)}</th><td>${fmt(m?.planned_wh == null ? null : m.planned_wh / 1000)}</td><td>${fmt(m?.actual_wh == null ? null : m.actual_wh / 1000)}</td><td>${fmt(m?.error_wh == null ? null : m.error_wh / 1000)}</td><td>${fmt(m?.coverage_hours)} h</td></tr>`;
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
    ${report.last_error ? `<p>${text("report_a_recording_error_occurred")}</p>` : ""}${content}</details>`;
}
