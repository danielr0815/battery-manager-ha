import { test } from "node:test";
import assert from "node:assert/strict";
import {
  inverterControlReport,
  operationReport,
  sourceHealthReport,
  stateNotice,
  reserveReport,
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
