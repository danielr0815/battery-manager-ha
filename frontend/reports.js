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
const reportNumber = (hass, value, options = { maximumFractionDigits: 1 }) =>
  typeof value === "number" && Number.isFinite(value)
    ? new Intl.NumberFormat(hass?.language || "en", options).format(value)
    : "—";

function gridGuardMeasurement(hass, snapshot, freshLabel) {
  const t = (key) => localize(hass, key);
  const watts = (value) =>
    reportNumber(hass, value, { maximumSignificantDigits: 21 });
  const statusKey =
    snapshot.status === "fresh"
      ? freshLabel
      : `load_grid_guard_${snapshot.status}`;
  const status = t(statusKey);
  const reading = watts(snapshot.measured_w ?? snapshot.grid_import_w);
  const details = [
    status === statusKey ? t("load_grid_guard_unknown") : status,
    reading === "—" ? "" : `${reading} W`,
    typeof snapshot.entity_id === "string" ? `(${snapshot.entity_id})` : "",
  ].filter(Boolean);
  const age = reportNumber(hass, snapshot.age_seconds);
  const maxAge = reportNumber(hass, snapshot.max_age_seconds);
  // A reading just past the freshness boundary must not display as at it.
  const ageLabel =
    Number.isFinite(snapshot.max_age_seconds) &&
    age === maxAge &&
    snapshot.age_seconds > snapshot.max_age_seconds
      ? `> ${maxAge}`
      : age;
  const threshold = watts(snapshot.threshold_w);
  return [
    details.join(" "),
    age === "—" ? "" : `${t("load_grid_guard_age")} ${ageLabel} s`,
    threshold === "—" ? "" : `${t("load_grid_guard_cutoff")} ${threshold} W`,
  ]
    .filter(Boolean)
    .join(" · ");
}

export function loadGridGuardReport(hass, state) {
  const guard = state?.attributes?.load_grid_guard;
  if (
    typeof guard?.active !== "boolean" ||
    ["unknown", "unavailable"].includes(state.state)
  )
    return "";
  const t = (key) => localize(hass, key);
  const current = gridGuardMeasurement(hass, guard, "load_grid_guard_fresh");
  const last = guard.last_action;
  let stop = "";
  if (last?.action === "stop_requested") {
    const decided = reportTime(hass, last.decided_at);
    const requested = reportTime(hass, last.requested_at);
    const parts = requested === "—" ? [] : [requested];
    if (decided !== "—" && decided !== requested)
      parts.push(`${t("load_grid_guard_decided_at")} ${decided}`);
    parts.push(
      gridGuardMeasurement(hass, last, "load_grid_guard_decision_reading"),
    );
    stop = `<div class="forecast-note" data-load-grid-last-action><strong>${esc(t("load_grid_guard_last_stop"))}</strong> · ${esc(parts.join(" · "))}</div>`;
  }
  return `<div class="forecast-note" data-load-grid-guard><strong>${esc(t(guard.active ? "load_grid_guard_active" : "load_grid_guard_inactive"))}</strong> · ${esc(current)}</div>${stop}`;
}

export function socExpectationExplanation(hass, expectation, shown, overlap) {
  const t = (key) => localize(hass, key);
  const isNet = expectation.basis === "battery_net";
  const prefix = isNet ? "soc_expected_net" : "soc_expected";
  const power = reportNumber(
    hass,
    isNet ? expectation.net_discharge_w : expectation.delivery_w,
    {
      maximumFractionDigits: 2,
    },
  );
  const reasonCode = isNet
    ? String(expectation.reason).replace(/^net_/, "")
    : expectation.reason;
  const reasonKey = `${prefix}_reason_${reasonCode}`;
  const reason = t(reasonKey);
  const until =
    isNet && Number.isFinite(expectation.valid_until)
      ? reportTime(hass, new Date(expectation.valid_until * 1000).toJSON())
      : "—";
  const span = expectation.observed_seconds;
  const duration = [];
  if (Number.isFinite(span) && span >= 0) {
    // Do not round an almost-qualified episode up to 30 minutes.
    const minutes = Math.floor(span / 60);
    const seconds = Math.floor(span % 60);
    if (minutes) duration.push(`${reportNumber(hass, minutes)} min`);
    if (seconds || !minutes) duration.push(`${reportNumber(hass, seconds)} s`);
  }
  const parts = [
    power === "—" ? "" : `${t(`${prefix}_measured`)} ${power} W.`,
    duration.length ? `${t("soc_expected_span")} ${duration.join(" ")}.` : "",
    until === "—" ? "" : `${t("soc_expected_net_until")} ${until}.`,
    !isNet &&
    expectation.status === "unqualified" &&
    expectation.relay_confirmed_on
      ? t("soc_expected_relay_on")
      : "",
  ];
  if (isNet && !shown) parts.push(t("soc_expected_net_purpose"));
  if (shown) {
    parts.push(t(`${prefix}_basis`));
    if (overlap) parts.push(t("soc_expected_overlap"));
  } else if (reason !== reasonKey) {
    parts.push(reason);
  } else if (expectation.status === "unqualified") {
    parts.push(t(`${prefix}_unqualified`));
  } else if (expectation.status === "available") {
    parts.push(t("soc_expected_outside_display"));
  } else {
    parts.push(t(`${prefix}_no_window`));
  }
  parts.push(t("soc_safety_decisions"));
  return parts.filter(Boolean).join(" ");
}

export function inputControlReport(hass, input) {
  if (!input || typeof input !== "object") return "";
  const t = (key) => localize(hass, key);
  const state = (value) =>
    value === true ? t("input_on") : value === false ? t("input_off") : "—";
  const reasonKey = `input_reason_${input.reason}`;
  const reason = t(reasonKey);
  const rows = [
    [t("input_connection"), state(input.input_on)],
    [t("input_charge_enable"), state(input.charge_enable)],
    [t("input_physical_charging"), state(input.charging)],
    [t("input_output"), state(input.output_on)],
    [t("input_output_power"), `${reportNumber(hass, input.output_power_w)} W`],
    [
      t("input_ownership"),
      input.owned === true
        ? t("input_owned")
        : input.owned === false
          ? t("input_foreign")
          : "—",
    ],
    [t("input_stop_reason"), reason === reasonKey ? "—" : reason],
  ];
  if (
    typeof input.paused_input_power_w === "number" &&
    Number.isFinite(input.paused_input_power_w)
  ) {
    rows.push([
      t("input_paused_power"),
      `${reportNumber(hass, input.paused_input_power_w)} W`,
    ]);
    rows.push([
      t("input_daily_estimate"),
      `${reportNumber(hass, (input.paused_input_power_w * 24) / 1000)} kWh`,
    ]);
  }
  return `<details data-view-key="input-control"><summary>${esc(t("input_control_title"))}</summary><dl data-input-control>${rows.map(([label, value]) => `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join("")}</dl></details>`;
}

export function storageReport(hass, storage) {
  if (!storage || typeof storage !== "object") return "";
  const t = (key) => localize(hass, key);
  const rows = [
    [
      t("storage_minimum_target"),
      `${reportNumber(hass, storage.target_soc_percent)} %`,
    ],
    [
      t("storage_deficit"),
      `${reportNumber(hass, storage.initial_deficit_wh)} Wh`,
    ],
    [
      t("storage_recovery_energy"),
      `${reportNumber(hass, storage.recovery_planned_wh)} Wh`,
    ],
    [
      t("storage_topup_energy"),
      `${reportNumber(hass, storage.topup_planned_wh)} Wh`,
    ],
  ];
  const days = Array.isArray(storage.day_end_energy_wh)
    ? storage.day_end_energy_wh.map(([day, energy]) => [
        day,
        typeof energy === "number" && storage.capacity_wh > 0
          ? (energy / storage.capacity_wh) * 100
          : undefined,
      ])
    : [];
  const reasons = Array.isArray(storage.reasons)
    ? [...new Set(storage.reasons)]
    : [];
  return `<details data-view-key="storage-target"><summary>${esc(t("storage_title"))}</summary><dl>${rows.map(([label, value]) => `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join("")}</dl>${reasons.length ? `<ul>${reasons.map((reason) => `<li>${esc(t(reason) === reason ? t("storage_unproved") : t(reason))}</li>`).join("")}</ul>` : ""}${days.length ? `<table data-storage-days><thead><tr><th>${esc(t("storage_day"))}</th><th>${esc(t("storage_end_soc"))}</th><th>${esc(t("storage_deficit"))}</th></tr></thead><tbody>${days.map((day) => `<tr><th>${esc(day[0])}</th><td>${esc(reportNumber(hass, day[1]))} %</td><td>${esc(reportNumber(hass, storage.target_soc_percent != null && day[1] != null && storage.capacity_wh != null ? (Math.max(0, storage.target_soc_percent - day[1]) / 100) * storage.capacity_wh : undefined))} Wh</td></tr>`).join("")}</tbody></table>` : ""}</details>`;
}

export function rechargeReport(hass, recharge) {
  if (!recharge || typeof recharge !== "object") return "";
  const t = (key) => localize(hass, key);
  const commitments = Array.isArray(recharge.certificates)
    ? recharge.certificates
    : [];
  const total = (kind) => {
    if (!Array.isArray(recharge.certificates)) return undefined;
    const matching = commitments.filter((item) => item.kind === kind);
    if (
      matching.some(
        (item) =>
          typeof item.battery_draw_wh !== "number" ||
          !Number.isFinite(item.battery_draw_wh),
      )
    )
      return undefined;
    return matching.reduce((sum, item) => sum + item.battery_draw_wh, 0);
  };
  const statusKey = `recharge_${recharge.status}`;
  const rejections = Array.isArray(recharge.rejection_reasons)
    ? [...new Set(recharge.rejection_reasons)]
    : [];
  const rejectionText = (reason) => {
    for (const prefix of ["recharge_reason_", "report_joint_reason_"]) {
      const key = `${prefix}${reason}`;
      if (t(key) !== key) return t(key);
    }
    return t("recharge_unproved");
  };
  const rows = [
    [t("recharge_status"), t(statusKey) === statusKey ? "—" : t(statusKey)],
    [t("recharge_validations"), reportNumber(hass, recharge.validations)],
    [t("recharge_dc_energy"), `${reportNumber(hass, total("dc24"))} Wh`],
    [t("recharge_ac_energy"), `${reportNumber(hass, total("ac"))} Wh`],
  ];
  return `<section data-recharge><h4>${esc(t("recharge_title"))}</h4><p>${esc(t("recharge_explanation"))}</p><dl>${rows.map(([label, value]) => `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join("")}</dl>${commitments.length ? `<ol>${commitments.map((item) => `<li>${esc(reportTime(hass, item.anchor))} · ${esc(reportNumber(hass, item.battery_draw_wh))} Wh</li>`).join("")}</ol>` : ""}${rejections.length ? `<details data-recharge-rejections><summary>${esc(t("report_joint_rejections"))}</summary><ul>${rejections.map((reason) => `<li>${esc(rejectionText(reason))}</li>`).join("")}</ul></details>` : ""}</section>`;
}

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

function jointTargetReport(hass, joint, reason) {
  const t = (key) => localize(hass, key);
  const fmt = (value) => reportNumber(hass, value);
  const counts = (values) =>
    Object.entries(values && typeof values === "object" ? values : {})
      .filter(([, count]) => Number.isInteger(count) && count > 0)
      .map(([key, count]) => `${reason(key)} (${count})`)
      .join("; ") || "—";
  const rows = (
    Array.isArray(joint.target_diagnostics) ? joint.target_diagnostics : []
  )
    .filter(
      (row) =>
        row &&
        Number.isFinite(Date.parse(row.start)) &&
        Date.parse(row.end) > Date.parse(row.start),
    )
    .slice(0, 48)
    .map(
      (row) =>
        `<tr><th scope="row">${esc(reportTime(hass, row.start))} – ${esc(reportTime(hass, row.end))}<br>${esc(fmt(row.eur_per_mwh))} EUR/MWh · ${esc(t(`report_joint_variant_${row.variant}`))}</th><td>${row.not_searched ? esc(t("report_joint_not_searched")) : esc(fmt(row.attempted))}</td><td>${esc(counts(row.local_rejections))}</td><td>${esc(counts(row.proof_rejections))}</td><td>${esc(fmt(row.proved_improvements))}</td></tr>`,
    )
    .join("");
  if (!rows) return "";
  return `<h4>${esc(t("report_joint_targets"))}</h4><p>${esc(t("report_joint_targets_note"))}</p>${joint.target_diagnostics_truncated ? `<p>${esc(t("report_joint_truncated"))}</p>` : ""}<div data-scroll-key="joint-targets" style="overflow-x:auto"><table data-joint-targets><thead><tr>${["report_joint_interval", "report_joint_attempted", "report_joint_local_rejected", "report_joint_proof_rejected", "report_joint_proved"].map((key) => `<th scope="col">${esc(t(key))}</th>`).join("")}</tr></thead><tbody>${rows}</tbody></table></div>`;
}

function jointMarketReport(hass, joint) {
  if (!joint || typeof joint !== "object") return "";
  const t = (key) => localize(hass, key);
  const fmt = (value) => reportNumber(hass, value);
  const reason = (value) => {
    const key = `report_joint_reason_${value}`;
    return t(key) === key ? t("report_reserve_decision_unknown") : t(key);
  };
  const rows = [
    [t("report_joint_result"), reason(joint.joint_status)],
    [t("report_joint_budget"), `${fmt(joint.joint_budget_wh)} Wh`],
    [
      t("report_joint_unavoidable_dc"),
      `${fmt(joint.unavoidable_dc_draw_wh)} Wh`,
    ],
    [t("report_joint_shifted"), `${fmt(joint.joint_shifted_wh)} Wh`],
  ];
  if (Array.isArray(joint.joint_rejection_reasons)) {
    const reasons = [...new Set(joint.joint_rejection_reasons)].map(reason);
    if (reasons.length)
      rows.push([t("report_joint_rejections"), reasons.join("; ")]);
  }
  const daily = joint.economic_daily_hours;
  if (daily && typeof daily === "object" && !Array.isArray(daily)) {
    for (const [day, hours] of Object.entries(daily).sort()) {
      if (!/^\d{4}-\d{2}-\d{2}$/.test(day)) continue;
      rows.push([
        `${t("report_joint_daily_hours")} · ${day}`,
        `${fmt(hours)} h`,
      ]);
    }
  }
  const comparisons = [
    ["report_grid_import", "grid_import_wh", "Wh"],
    ["report_grid_export", "grid_export_wh", "Wh"],
    ["report_joint_battery_draw", "battery_draw_wh", "Wh"],
    ["report_joint_end_soc", "end_soc_percent", "%"],
  ]
    .map(
      ([label, key, unit]) =>
        `<tr><th scope="row">${esc(t(label))}</th><td>${esc(fmt(joint[`reference_${key}`]))} ${unit}</td><td>${esc(fmt(joint[`actual_${key}`]))} ${unit}</td></tr>`,
    )
    .join("");
  const intervals = (
    Array.isArray(joint.economic_intervals) ? joint.economic_intervals : []
  )
    .filter(
      (item) =>
        item &&
        Number.isFinite(Date.parse(item.start)) &&
        Number.isFinite(Date.parse(item.end)) &&
        Date.parse(item.end) > Date.parse(item.start),
    )
    .sort((a, b) => {
      const priceA =
        typeof a.eur_per_mwh === "number" && Number.isFinite(a.eur_per_mwh)
          ? a.eur_per_mwh
          : -Infinity;
      const priceB =
        typeof b.eur_per_mwh === "number" && Number.isFinite(b.eur_per_mwh)
          ? b.eur_per_mwh
          : -Infinity;
      return priceB - priceA || Date.parse(a.start) - Date.parse(b.start);
    })
    .map(
      (item) =>
        `<tr><th scope="row">${esc(reportTime(hass, item.start))} – ${esc(reportTime(hass, item.end))}</th><td>${esc(fmt(item.eur_per_mwh))}</td><td>${esc(fmt(item.allocated_battery_wh))} Wh</td><td>${esc(fmt(item.avoided_total_grid_import_wh))} Wh</td><td>${esc(fmt(item.avoided_import_wh))} Wh</td></tr>`,
    )
    .join("");
  return `<section data-joint-market><h4>${esc(t("report_joint_title"))}</h4><p>${esc(t("report_joint_explanation"))}</p><dl>${rows.map(([label, value]) => `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join("")}</dl><div data-scroll-key="joint-comparison" style="overflow-x:auto"><table><thead><tr><th scope="col">${esc(t("report_metric"))}</th><th scope="col">${esc(t("report_joint_reference"))}</th><th scope="col">${esc(t("report_joint_selected"))}</th></tr></thead><tbody>${comparisons}</tbody></table></div><h4>${esc(t("report_joint_intervals"))}</h4>${intervals ? `<div data-scroll-key="joint-intervals" style="overflow-x:auto"><table><thead><tr><th scope="col">${esc(t("report_joint_interval"))}</th><th scope="col">EUR/MWh</th><th scope="col">${esc(t("report_joint_allocated_battery"))}</th><th scope="col">${esc(t("report_joint_avoided_import"))}</th><th scope="col">${esc(t("report_joint_import_change"))}</th></tr></thead><tbody>${intervals}</tbody></table></div>` : `<p>${esc(t("report_joint_no_intervals"))}</p>`}${jointTargetReport(hass, joint, reason)}</section>`;
}

export function socCoordinateReport(hass, learned) {
  if (!learned?.coordinate && !learned?.qualification) return "";
  const t = (key) => localize(hass, key);
  const translated = (prefix, value) => {
    const key = `${prefix}_${value}`;
    const text = t(key);
    return text === key ? t("soc_observation_unknown") : text;
  };
  const coordinate = learned.coordinate;
  const qualification = learned.qualification;
  const rows = [];
  if (coordinate) {
    rows.push([
      t("report_soc_coordinate_state"),
      translated("soc_phase", coordinate.phase),
    ]);
    const eventAt = coordinate.last_event?.at;
    const eventDate = new Date(
      typeof eventAt === "number" ? eventAt * 1000 : NaN,
    );
    if (Number.isFinite(eventDate.getTime()))
      rows.push([
        t("report_soc_coordinate_event"),
        reportTime(hass, eventDate.toISOString()),
      ]);
    if (coordinate.residual) {
      rows.push([
        t("report_soc_reported_change"),
        `${reportNumber(hass, coordinate.residual.observed_soc_delta_percent)} %`,
      ]);
      rows.push([
        t("report_soc_energy_change"),
        `${reportNumber(hass, coordinate.residual.energy_equivalent_soc_delta_percent)} %`,
      ]);
      rows.push([
        t("report_soc_model_residual"),
        `${reportNumber(hass, coordinate.residual.residual_percent)} %`,
      ]);
      rows.push([
        t("report_soc_covered_time"),
        `${reportNumber(hass, Number.isFinite(coordinate.residual.covered_seconds) ? coordinate.residual.covered_seconds / 60 : null)} min`,
      ]);
    }
  }
  if (qualification) {
    rows.push([
      t("report_gate_query"),
      `${reportNumber(hass, qualification.query_current_a)} A`,
    ]);
    for (const role of Array.isArray(qualification.roles)
      ? qualification.roles
      : []) {
      if (!role || typeof role !== "object") continue;
      if (!["on", "delivery", "off"].includes(role.role)) continue;
      rows.push([
        translated("gate_role", role.role),
        `${translated("gate_qualification", role.reason)} · ${reportNumber(hass, role.near_count)} ${t("report_gate_points")} · ${reportNumber(hass, Number.isFinite(role.span_seconds) ? role.span_seconds / 60 : null)} min · ${t("report_gate_spread")}: ${reportNumber(hass, role.spread_percent)} %`,
      ]);
    }
    if (qualification.reason)
      rows.push([
        t("report_gate_query_status"),
        translated("gate_qualification", qualification.reason),
      ]);
  }
  return `<details data-view-key="soc-coordinate"><summary>${esc(t("report_soc_coordinate"))}</summary>
    <p>${esc(t("report_soc_coordinate_explanation"))}</p>
    <dl>${rows.map(([label, value]) => `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join("")}</dl>
    <p data-soc-forecast-basis="conservative">${esc(t("report_soc_conservative_forecast"))}</p></details>`;
}

export function psuObservationReport(hass, observations) {
  if (!Array.isArray(observations) || !observations.length) return "";
  const t = (key) => localize(hass, key);
  const sections = observations
    .filter((item) => item && ["dc24", "dc48"].includes(item.source_id))
    .map((item) => {
      const qualityKey = `psu_quality_${item.quality}`;
      const quality = t(qualityKey);
      const rows = [
        [
          t("report_source_status"),
          quality === qualityKey ? t("soc_observation_unknown") : quality,
        ],
        [
          t("report_psu_confirmed_state"),
          item.confirmed_on === true
            ? t("input_on")
            : item.confirmed_on === false
              ? t("input_off")
              : "—",
        ],
        [t("report_psu_ac_input"), `${reportNumber(hass, item.ac_input_w)} W`],
        [
          t("report_psu_dc_output"),
          `${reportNumber(hass, item.dc_output_w)} W`,
        ],
        [
          t("report_psu_present_delivery"),
          `${reportNumber(hass, item.present_delivery_w)} W`,
        ],
        [
          t("report_psu_observed_efficiency"),
          typeof item.efficiency === "number" &&
          Number.isFinite(item.efficiency) &&
          item.efficiency >= 0 &&
          item.efficiency <= 1
            ? `${reportNumber(hass, item.efficiency * 100)} %`
            : "—",
        ],
      ];
      for (const channel of ["ac_input", "dc_output"]) {
        const reason = item.channel_reasons?.[channel];
        if (!reason) continue;
        const reasonKey = `psu_channel_${reason}`;
        const text = t(reasonKey);
        rows.push([
          channel === "ac_input"
            ? t("report_psu_ac_input_status")
            : t("report_psu_dc_output_status"),
          text === reasonKey ? t("soc_observation_unknown") : text,
        ]);
      }
      return `<section data-psu-observation="${esc(item.source_id)}"><h4>${esc(item.source_id === "dc24" ? t("report_psu_24") : t("report_psu_48"))}</h4>
        <dl>${rows.map(([label, value]) => `<dt>${esc(label)}</dt><dd>${esc(value)}</dd>`).join("")}</dl></section>`;
    });
  if (!sections.length) return "";
  return `<details data-view-key="psu-observations"><summary>${esc(t("report_psu_observations"))}</summary>
    <p>${esc(t("report_psu_present_only"))}</p>${sections.join("")}</details>`;
}

export function reserveReport(hass, reserve, support, calibration) {
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
    [t("report_dc_budget"), `${fmt(reserve.dc_budget_wh)} Wh`],
    [t("report_dc_shifted"), `${fmt(reserve.dc_shifted_wh)} Wh`],
    [
      t("report_incidental_psu_charging"),
      `${fmt(reserve.incidental_grid_charge_wh)} Wh`,
    ],
    [
      t("report_expected_48_v_support"),
      `${fmt(reserve.psu48_delivered_wh)} Wh`,
    ],
  ];
  const gate = support?.voltage_gate;
  const learned = calibration?.adaptive;
  if (gate) {
    rows.push([t("report_gate_48"), t(`gate_${gate.reason}`)]);
    rows.push([t("report_gate_voltage"), `${fmt(gate.voltage_v)} V`]);
  }
  if (learned) {
    rows.push([t("report_gate_learning"), t(`gate_${learned.source}`)]);
    rows.push([t("report_gate_samples"), fmt(learned.sample_count)]);
    for (const band of learned.bands || []) {
      if (band.current_min_a < 0 || band.on_soc == null) continue;
      rows.push([
        `${t("report_gate_soc")} (${fmt(band.current_min_a)}–${fmt(band.current_max_a)} A)`,
        `${fmt(band.on_soc)} % · ${t("report_gate_spread")}: ${fmt(band.spread_percent)} %`,
      ]);
    }
  }
  if (support?.pv_priority) {
    rows.push([
      t("report_live_dc_source"),
      t(
        support.reason === "settled"
          ? "report_live_dc_pv_confirmed"
          : "report_live_dc_pv_pending",
      ),
    ]);
  }
  const market = reserve.market;
  if (market) {
    rows.push([
      t("report_market"),
      t(
        !market.enabled
          ? "report_market_off"
          : market.status === "available"
            ? market.joint
              ? "report_joint_market_available"
              : "report_market_available"
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
    ${jointMarketReport(hass, reserve.market?.joint)}
    ${rechargeReport(hass, reserve.recharge)}
    ${socCoordinateReport(hass, learned)}
    ${psuObservationReport(hass, learned?.psu_observations)}
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
