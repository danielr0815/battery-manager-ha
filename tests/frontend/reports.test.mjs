import { test } from "node:test";
import assert from "node:assert/strict";
import {
  inverterControlReport,
  operationReport,
  sourceHealthReport,
  stateNotice,
  reserveReport,
  inputControlReport,
  storageReport,
  rechargeReport,
} from "../../frontend/reports.js";
import { selectionTime } from "../../frontend/time.js";
const hass = { language: "en", config: { time_zone: "Europe/Berlin" } };

test("inverter permission distinguishes the captured plan, command and physical feedback", () => {
  const attributes = {
    reserve: { mode: "active", inverter_limit_w: 0 },
    live_ac: { limit_w: 2300, confirmed: true, reason: "measured_ac_demand" },
    plan_metadata: {
      captured_at: "2026-10-02T08:00:00Z",
      activated_at: "2026-10-02T08:00:10Z",
    },
    inverter_control: {
      requested_limit_w: 2300,
      observed_limit_w: 0,
      confirmed: false,
      requested_at: "2026-10-02T08:01:00Z",
      observed_at: "2026-10-02T07:55:00Z",
    },
  };
  const html = inverterControlReport(hass, attributes, "50");
  assert.match(html, /Inverter permission in the plan<\/dt><dd>0 W/);
  assert.match(html, /Requested inverter limit<\/dt><dd>2,300 W/);
  assert.match(html, /Reported device limit<\/dt><dd>0 W/);
  assert.match(html, /Device confirmation pending/);
  assert.match(html, /10:00:00/);
  assert.match(html, /10:01:00/);
  assert.match(html, /09:55:00/);
  assert.match(html, /Measured AC demand/);
  assert.match(html, /not measured inverter power/);
  const stale = inverterControlReport(hass, attributes, "unavailable");
  assert.match(stale, /Last known inverter/);
});

test("old diagnostics cannot manufacture a confirmed device limit from live permission", () => {
  const html = inverterControlReport(hass, {
    reserve: { mode: "active", inverter_limit_w: 0 },
    live_ac: { limit_w: 2300, confirmed: true },
  });
  assert.match(html, /Requested inverter limit<\/dt><dd>— W/);
  assert.match(html, /Reported device limit<\/dt><dd>— W/);
  assert.doesNotMatch(html, /Confirmed<\/dd>/);
});

test("an empty archive preserves its error and zero measurement coverage remains unknown", () => {
  assert.match(
    operationReport(hass, { days: [], last_error: "corrupt" }),
    /data-history-error/,
  );
  assert.match(
    operationReport(hass, { days: [], last_error: "corrupt" }),
    /No recorded daily reports/,
  );
  assert.match(
    operationReport(hass, {
      days: [
        {
          day: "2026-10-02",
          metrics: {
            grid_import: {
              planned_wh: 0,
              actual_wh: 0,
              error_wh: 0,
              coverage_hours: 0,
            },
          },
        },
      ],
    }),
    /Grid import<\/th><td>—<\/td><td>—<\/td><td>—<\/td><td>0 h/,
  );
});

test("source roles retain unavailable, stale and unconfigured distinctions without inferring freshness", () => {
  const html = sourceHealthReport(hass, [
    {
      role: "ac",
      entity_id: "sensor.house",
      status: "stale",
      value: 0,
      unit: "W",
      reported_at: "2026-10-02T07:00:00Z",
      fallback: "static",
    },
    { role: "pv", entity_id: null, status: "not_configured" },
    { role: "market", entity_id: "sensor.missing", status: "not_found" },
    { role: "<img>", status: "invalid", error: "<script>" },
  ]);
  assert.match(html, /Stale publication/);
  assert.match(html, /0 W/);
  assert.match(html, /Not configured/);
  assert.match(html, /Entity not found/);
  assert.match(html, /&lt;img&gt;/);
  assert.doesNotMatch(html, /<img>|<script>|>null</);
});

test("market fallback includes source and coverage while unavailable forecasts are marked stale", () => {
  const html = reserveReport(hass, {
    mode: "active",
    market: {
      enabled: true,
      status: "expired",
      entity_id: "sensor.prices",
      coverage_end: "2026-10-02T07:00:00Z",
    },
  });
  assert.match(html, /sensor.prices/);
  assert.match(html, /Coverage expired/);
  assert.match(stateNotice(hass, { state: "unavailable" }), /role="status"/);
  assert.equal(stateNotice(hass, { state: "50" }), "");
  assert.match(
    stateNotice(hass, { state: "unknown", attributes: {} }),
    /No last known plan/,
  );
});

test("repeated DST readouts disclose distinct offsets while ordinary hours stay concise", () => {
  const times = [
    Date.parse("2026-10-25T02:00:00+02:00"),
    Date.parse("2026-10-25T02:00:00+01:00"),
  ];
  assert.match(selectionTime(hass, times[0], times), /GMT\+2/);
  assert.match(selectionTime(hass, times[1], times), /GMT\+1/);
  assert.doesNotMatch(selectionTime(hass, times[0], [times[0]]), /GMT/);
});

test("DC reserve reports PV and peak decisions with finite discharge budgets", () => {
  for (const [reason, text] of [
    ["dc_pv_supply", /PV surplus supplies the DC consumers/],
    ["dc_market_supply", /Already permitted DC battery energy/],
    ["dc_reserve_holding", /no battery discharge budget/],
  ]) {
    const html = reserveReport(hass, {
      mode: "active",
      decision_reason: reason,
      dc_budget_wh: 120,
      dc_shifted_wh: 40,
    });
    assert.match(html, text);
    assert.match(html, /Available DC discharge budget/);
    assert.match(html, /120 Wh/);
    assert.match(html, /40 Wh/);
    const german = reserveReport(
      { ...hass, language: "de" },
      {
        mode: "active",
        decision_reason: reason,
        dc_budget_wh: 120,
        dc_shifted_wh: 40,
      },
    );
    assert.match(german, /DC-Entnahmebudget/);
    assert.doesNotMatch(german, /Kein Entscheidungsgrund verfügbar/);
  }
});

test("measured PV source requests distinguish confirmed and pending transitions", () => {
  for (const [reason, text] of [
    ["settled", /PV supply confirmed/],
    ["pv_permission_expired", /PV source transition pending/],
  ]) {
    const html = reserveReport(
      hass,
      { mode: "active", decision_reason: "dc_reserve_holding" },
      { pv_priority: true, reason },
    );
    assert.match(html, text);
    assert.match(html, /no battery discharge budget/);
  }
});

test("48 V report distinguishes gated OFF, protective readiness and learned SOC", () => {
  const reserve = { mode: "active" };
  const html = reserveReport(
    { ...hass, language: "de" },
    reserve,
    { voltage_gate: { reason: "above_off_voltage", voltage_v: 49.89 } },
    {
      adaptive: {
        source: "learned",
        sample_count: 7,
        bands: [
          {
            current_min_a: 0.25,
            current_max_a: 1,
            on_soc: 71,
            spread_percent: 2,
          },
        ],
      },
    },
  );
  assert.match(html, /Aus: Spannung oberhalb der Ausschaltgrenze/);
  assert.match(html, /71 %/);
  assert.match(html, /Streuung: 2 %/);
  const fallback = reserveReport(
    hass,
    reserve,
    { voltage_gate: { reason: "voltage_unavailable", voltage_v: null } },
    { adaptive: { source: "configured_fallback", sample_count: 0, bands: [] } },
  );
  assert.match(fallback, /Protective readiness/);
  assert.match(fallback, /no assured power/);
  assert.doesNotMatch(fallback, /NaN|undefined/);
});

test("joint market diagnostics show all economic hours in descending actual-price order", () => {
  const intervals = Array.from({ length: 7 }, (_, index) => ({
    start: `2026-10-02T${String(index + 8).padStart(2, "0")}:00:00Z`,
    end: `2026-10-02T${String(index + 9).padStart(2, "0")}:00:00Z`,
    eur_per_mwh: index * 10 - 20,
    avoided_import_wh: 25,
    allocated_battery_wh: 35,
    avoided_total_grid_import_wh: 30,
  }));
  const reserve = {
    mode: "active",
    market: {
      enabled: true,
      status: "available",
      joint: {
        joint_status: "accepted",
        joint_budget_wh: 410,
        unavoidable_dc_draw_wh: 125,
        joint_shifted_wh: 175,
        economic_intervals: intervals,
        economic_daily_hours: { "2026-10-02": 7 },
        reference_grid_import_wh: 800,
        actual_grid_import_wh: 800,
        reference_grid_export_wh: 0,
        actual_grid_export_wh: 0,
        reference_battery_draw_wh: 535,
        actual_battery_draw_wh: 535,
        reference_end_soc_percent: 48,
        actual_end_soc_percent: 48,
      },
    },
  };
  const html = reserveReport(hass, reserve);
  assert.match(html, /Shared AC\/DC discharge budget/);
  assert.match(html, /Selected hours · 2026-10-02<\/dt><dd>7 h/);
  assert.match(html, /Total battery discharge budget<\/dt><dd>410 Wh/);
  assert.match(html, /Unavoidable DC battery draw<\/dt><dd>125 Wh/);
  assert.match(html, /Allocated battery energy/);
  assert.match(html, /Battery draw<\/th><td>535 Wh<\/td><td>535 Wh/);
  assert.match(html, /Grid export<\/th><td>0 Wh<\/td><td>0 Wh/);
  const table = html.split('data-scroll-key="joint-intervals"')[1];
  assert.equal((table.match(/25 Wh/g) || []).length, 7);
  assert.ok(table.indexOf("<td>40</td>") < table.indexOf("<td>-20</td>"));
  assert.match(table, /EUR\/MWh/);
  const german = reserveReport({ ...hass, language: "de" }, reserve);
  assert.match(german, /Gemeinsames AC\/DC-Entnahmebudget/);
  assert.match(german, /Ausgewählte Stunden · 2026-10-02<\/dt><dd>7 h/);
});

test("joint diagnostics preserve unknown values and distinguish bounded comparisons from impossibility", () => {
  const html = reserveReport(hass, {
    mode: "active",
    market: {
      joint: {
        joint_status: "search_limit",
        joint_rejection_reasons: [
          "extra_import",
          "source_unavailable",
          "phase",
          "construction_limit",
          "unsearched_alternatives",
          "<img>",
        ],
        economic_intervals: [
          null,
          { start: "invalid", end: "invalid", eur_per_mwh: 999 },
          { start: "2026-10-02T08:00:00Z", end: "2026-10-02T07:00:00Z" },
          {
            start: "2026-10-02T08:00:00Z",
            end: "2026-10-02T09:00:00Z",
            eur_per_mwh: null,
          },
        ],
        economic_daily_hours: { "<script>": 7, "2026-10-02": null },
      },
    },
  });
  assert.match(html, /better allocation may still exist/);
  assert.match(html, /proposal construction limit/);
  assert.match(html, /Other combinations remain unsearched/);
  assert.match(html, /rejection reasons describe individual proposals/);
  assert.match(html, /Would increase total grid import/);
  assert.match(html, /Required supply source is unavailable/);
  assert.match(html, /fixed supply phase prevents/);
  assert.match(html, /Total battery discharge budget<\/dt><dd>— Wh/);
  assert.match(html, /Grid import<\/th><td>— Wh<\/td><td>— Wh/);
  assert.match(html, /Selected hours · 2026-10-02<\/dt><dd>— h/);
  assert.match(html, /<td>—<\/td><td>— Wh/);
  assert.doesNotMatch(html, /<img>|<script>|NaN|undefined/);
  for (const joint of [undefined, null]) {
    const legacy = reserveReport(hass, {
      mode: "active",
      market: { enabled: true, status: "available", joint },
    });
    assert.doesNotMatch(legacy, /data-joint-market|Reference plan/);
    assert.match(legacy, /Preferred market windows/);
  }
});

test("physical rejection reasons explain the protected energy and supply boundaries in both languages", () => {
  const reasons = [
    "dc_service",
    "extra_psu_charge",
    "source_inverter_loop",
    "reserve_floor",
    "energy_budget",
    "window_end_energy",
    "extra_export",
    "unknown_price",
    "uncertainty_dc_service",
    "uncertainty_import",
    "uncertainty_reserve",
  ];
  for (const language of ["en", "de"]) {
    const html = reserveReport(
      { ...hass, language },
      {
        mode: "active",
        market: {
          joint: {
            joint_status: "no_improvement",
            joint_rejection_reasons: reasons,
          },
        },
      },
    );
    const explanations = html
      .split(
        language === "en"
          ? "Reasons alternatives were rejected"
          : "Gründe für verworfene Alternativen",
      )[1]
      .split("</dd>")[0];
    assert.doesNotMatch(
      explanations,
      /No decision reason available|Kein Entscheidungsgrund verfügbar|report_joint_reason_/,
    );
  }
});

test("already allocated economic energy remains visible with zero reference change", () => {
  const html = reserveReport(hass, {
    mode: "active",
    market: {
      joint: {
        joint_status: "no_improvement",
        unavoidable_dc_draw_wh: null,
        economic_daily_hours: { "2026-10-02": 6 },
        economic_intervals: [
          {
            start: "2026-10-02T08:00:00Z",
            end: "2026-10-02T14:00:00Z",
            eur_per_mwh: 90,
            allocated_battery_wh: 80,
            avoided_total_grid_import_wh: 70,
            avoided_import_wh: 0,
          },
        ],
      },
    },
  });
  assert.match(html, /Unavoidable DC battery draw<\/dt><dd>— Wh/);
  assert.match(html, /Selected hours · 2026-10-02<\/dt><dd>6 h/);
  assert.match(html, /<td>90<\/td><td>80 Wh<\/td><td>70 Wh<\/td><td>0 Wh/);
});

test("joint target observations separate screening, proof and unsearched exact spans", () => {
  const joint = {
    joint_status: "accepted",
    target_diagnostics_truncated: true,
    target_diagnostics: [
      {
        start: "2026-10-19T07:20:00Z",
        end: "2026-10-19T08:00:00Z",
        eur_per_mwh: 300,
        variant: "paired_dc",
        attempted: 2,
        local_rejections: {},
        proof_rejections: { extra_import: 1 },
        proved_improvements: 1,
        not_searched: false,
      },
      {
        start: "2026-10-19T07:00:00Z",
        end: "2026-10-19T08:00:00Z",
        variant: "ac_dc",
        attempted: 0,
        not_searched: true,
      },
      { start: "bad", end: "bad" },
    ],
  };
  const reserve = {
    mode: "active",
    market: { enabled: true, status: "available", joint },
  };
  const html = reserveReport(hass, reserve);
  assert.match(html, /DC in a paired exchange/);
  assert.match(html, /09:20/);
  assert.match(html, /Rejected in local screening/);
  assert.match(html, /Rejected after complete check/);
  assert.match(html, /Validated improvements/);
  assert.match(html, /No recorded candidate/);
  assert.match(html, /does not establish global optimality/);
  assert.match(html, /further intervals or outcomes are omitted/);
  const german = reserveReport({ ...hass, language: "de" }, reserve);
  assert.match(german, /DC im gekoppelten Tausch/);
  assert.match(german, /Nach vollständiger Prüfung abgelehnt/);
  assert.doesNotMatch(
    reserveReport(hass, {
      ...reserve,
      market: { ...reserve.market, joint: { joint_status: "accepted" } },
    }),
    /data-joint-targets/,
  );
});

test("input report separates energized input, denied permission and stopped charging", () => {
  const html = inputControlReport(hass, {
    input_on: true,
    charge_enable: false,
    charging: false,
    output_on: true,
    output_power_w: 0,
    owned: true,
    reason: "held_passthrough",
    paused_input_power_w: 6,
  });
  assert.match(html, /Input switch<\/dt><dd>On/);
  assert.match(html, /Charging permission<\/dt><dd>Off/);
  assert.match(html, /Reported battery charging<\/dt><dd>Off/);
  assert.match(html, /AC output is on/);
  assert.match(html, /6 W/);
  assert.match(html, /24-hour estimate/);
  const unknown = inputControlReport(hass, {
    input_on: null,
    reason: "unrecognized",
  });
  assert.match(unknown, /Input switch<\/dt><dd>—/);
  assert.match(unknown, /Reported battery charging<\/dt><dd>—/);
  const kept = inputControlReport(hass, {
    input_on: true,
    charge_enable: false,
    output_on: false,
    reason: "held_policy",
  });
  assert.match(kept, /Input retained by keep_on policy/);
  assert.doesNotMatch(kept, /AC output is on/);
  assert.equal(inputControlReport(hass, null), "");
});

test("storage report uses AC bookings and carries stored energy to a local day SOC", () => {
  const html = storageReport(hass, {
    target_soc_percent: 50,
    initial_deficit_wh: 600,
    recovery_planned_wh: 500,
    topup_planned_wh: 100,
    capacity_wh: 2000,
    reasons: ["recovery_target_unmet", "house_supply_priority"],
    day_end_energy_wh: [
      ["2026-10-27", 800],
      ["2026-10-28", 1200],
    ],
  });
  assert.match(html, /Recovery AC energy<\/dt><dd>500 Wh/);
  assert.match(html, /Recovery target remains unmet/);
  assert.match(
    html,
    /House supply and its repayment commitments take priority/,
  );
  assert.doesNotMatch(html, /unreachable|impossible/);
  assert.match(html, /2026-10-27/);
  assert.match(html, /40 %/);
  assert.match(html, /200 Wh/);
  assert.match(html, /60 %/);
  assert.equal(storageReport(hass, null), "");
});

test("recharge report shows fixed anchors and cannot turn missing evidence into zero credit", () => {
  const html = rechargeReport(hass, {
    status: "search_limit",
    validations: 32,
    rejection_reasons: ["anchor_energy", "uncertainty_import", "unrecognized"],
    certificates: [
      { anchor: "2026-10-27T12:00:00Z", kind: "dc24", battery_draw_wh: 137 },
    ],
  });
  assert.match(html, /Search limit reached/);
  assert.match(html, /137 Wh/);
  assert.match(html, /01:00:00 PM/);
  assert.match(html, /does not prove/);
  assert.match(html, /fixed solar deadline/);
  assert.match(html, /increase grid import under the lower PV forecast/);
  assert.match(html, /No complete proof/);
  assert.doesNotMatch(html, /unrecognized/);
  assert.match(
    rechargeReport(hass, {
      status: "manual_feed_in",
      rejection_reasons: ["manual_feed_in"],
    }),
    /Additional battery draw is paused while positive manual feed-in is active/,
  );
  assert.match(
    rechargeReport(hass, { status: "accepted" }),
    /Certified extra DCDC battery draw<\/dt><dd>— Wh/,
  );
  assert.match(
    rechargeReport(hass, {
      certificates: [{ kind: "dc24", battery_draw_wh: null }],
    }),
    /Certified extra DCDC battery draw<\/dt><dd>— Wh/,
  );
  assert.equal(rechargeReport(hass, null), "");
});
