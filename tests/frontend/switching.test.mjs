import { test } from "node:test";
import assert from "node:assert/strict";
import { switchingLanes, switchingDetails } from "../../frontend/switching.js";
import { localize } from "../../frontend/translations.js";
const t0 = Date.parse("2026-09-28T08:00:00Z"),
  t1 = t0 + 3600000;
const hass = { language: "de", config: { time_zone: "Europe/Berlin" } };
const t = (key) => localize(hass, key);
const interval = (
  start,
  end,
  inverter_on,
  dc24_on = false,
  dc48_on = false,
) => ({
  start: new Date(t0 + start * 60000).toISOString(),
  end: new Date(t0 + end * 60000).toISOString(),
  inverter_on,
  dc24_on,
  dc48_on,
});
test("switch lanes clip to horizon and merge each device independently", () => {
  const a = {
    switching_schedule: [
      interval(-10, 15, true),
      interval(15, 30, false, true),
      interval(30, 70, false, true, true),
    ],
  };
  assert.equal(switchingLanes(a, t0, t1, false, t).length, 1);
  const lanes = switchingLanes(a, t0, t1, true, t);
  assert.deepEqual(
    lanes.map((l) => l.schedule.map((b) => [b.start - t0, b.end - t0, b.on])),
    [
      [
        [0, 900000, true],
        [900000, 3600000, false],
      ],
      [
        [0, 900000, false],
        [900000, 3600000, true],
      ],
      [
        [0, 1800000, false],
        [1800000, 3600000, true],
      ],
    ],
  );
  const html = switchingDetails(lanes, hass, true, t);
  assert.match(html, /10:15/);
  assert.match(html, /11:00/);
  assert.match(html, /24-V-Netzteil/);
  assert.match(html, /<b>aus<\/b>/);
  assert.doesNotMatch(html, /09:50|11:10/);
});
test("missing or malformed switching telemetry stays unknown, never inferred from SOC", () => {
  for (const switching_schedule of [
    undefined,
    [
      null,
      {},
      { start: "bad", end: "bad", inverter_on: true },
      interval(30, 20, true),
      interval(0, 60, "false"),
    ],
  ]) {
    const lanes = switchingLanes(
      { switching_schedule, forecast: [{ soc: 95, dc24: true }] },
      t0,
      t1,
      false,
      t,
    );
    assert.equal(lanes[0].schedule.length, 0);
    assert.match(
      switchingDetails(lanes, hass, false, t),
      /Keine Schaltprognose/,
    );
  }
});
test("switch timing retains more than 100 short phases and does not merge through gaps", () => {
  const switching_schedule = Array.from({ length: 1200 }, (_, i) =>
    interval(i * 5, (i + 1) * 5, i % 2 === 0),
  );
  const lanes = switchingLanes(
    { switching_schedule },
    t0,
    t0 + 96 * 3600000,
    false,
    t,
  );
  assert.equal(lanes[0].schedule.length, 1152);
  const gap = switchingLanes(
    { switching_schedule: [interval(0, 15, true), interval(30, 60, true)] },
    t0,
    t1,
    false,
    t,
  );
  assert.equal(gap[0].schedule.length, 2);
});
test("repeated local DST hours carry distinct offsets in the exact-time list", () => {
  const start = Date.parse("2026-10-25T02:00:00+02:00");
  const lanes = switchingLanes(
    {
      switching_schedule: [
        {
          start: "2026-10-25T02:00:00+02:00",
          end: "2026-10-25T02:00:00+01:00",
          inverter_on: true,
        },
      ],
    },
    start,
    start + 3600000,
    false,
    t,
  );
  const html = switchingDetails(lanes, hass, false, t);
  assert.match(html, /MESZ/);
  assert.match(html, /MEZ/);
});
