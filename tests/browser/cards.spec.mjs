import { test, expect } from "@playwright/test";
const attributes = {
  soc_threshold_percent: 20,
  forecast: [
    { t: "2026-09-26T10:00:00+02:00", soc: 40, feedin_w: 0 },
    { t: "2026-09-26T11:00:00+02:00", soc: 60, feedin_w: 100 },
    { t: "2026-09-26T12:00:00+02:00", soc: 50, feedin_w: 200 },
  ],
  consumption_forecast: [
    {
      t: "2026-09-26T10:00:00+02:00",
      ac_w: 100,
      dc48_w: 20,
      dc24_w: 30,
      loads_w: 0,
    },
    {
      t: "2026-09-26T11:00:00+02:00",
      ac_w: 200,
      dc48_w: 20,
      dc24_w: 30,
      loads_w: 0,
    },
  ],
  loads: [],
  cascades: [],
};
test.beforeEach(async ({ page }) => {
  await page.goto("/tests/browser/harness.html");
  await page.waitForFunction(() => window.ready);
});
for (const kind of ["forecast", "consumption"])
  test(`${kind}: HA zone is used in accessible labels and keyboard hover`, async ({
    page,
  }) => {
    await page.evaluate(
      ([kind, attrs]) => window.mount(kind, attrs),
      [kind, attributes],
    );
    const chart = page.locator("svg#chart");
    await expect(chart).toBeVisible();
    await expect(chart).toHaveAttribute("aria-label", /11:00|10:00/);
    await chart.focus();
    await chart.press("Home");
    await expect(page.locator("#readout")).toContainText("10:00");
    await chart.press("ArrowRight");
    await expect(page.locator("#readout")).toContainText("11:00");
  });
for (const kind of ["cascade", "loads"])
  test(`${kind}: empty data renders through the asynchronous HA frame`, async ({
    page,
  }) => {
    await page.evaluate(
      ([kind, attrs]) => window.mount(kind, attrs),
      [kind, attributes],
    );
    await expect(page.locator("ha-card")).toBeVisible();
    await expect(page.locator(".wrap")).toContainText(
      kind === "cascade" ? "No cascades" : "No loads",
    );
  });
test("refresh retains open report, keyboard focus and scroll in real shadow DOM", async ({
  page,
}) => {
  const daily = Array.from({ length: 20 }, (_, i) => ({
    day: `2026-09-${String(26 - i).padStart(2, "0")}`,
    coverage_h: 24,
    loads: {},
    actual: {},
    expected: {},
  }));
  await page.evaluate((attrs) => window.mount("cascade", attrs), {
    ...attributes,
    operation_report: { days: daily },
  });
  const disclosure = page.locator("details").first();
  await disclosure.locator("summary").first().click();
  await expect(disclosure).toHaveAttribute("open", "");
  await page.evaluate(() => {
    document.querySelector("#host").scrollTop = 120;
  });
  const before = await page.locator("#host").evaluate((node) => node.scrollTop);
  await page.evaluate(() => {
    window.card.hass = {
      ...window.card._hass,
      states: {
        "sensor.plan": {
          attributes: { ...window.card._hass.states["sensor.plan"].attributes },
        },
      },
    };
  });
  await expect(disclosure).toHaveAttribute("open", "");
  await expect(disclosure.locator("summary").first()).toBeFocused();
  await expect
    .poll(() => page.locator("#host").evaluate((node) => node.scrollTop))
    .toBe(before);
});

test("feed-in hover reports the selected partial slot energy at exact boundaries", async ({
  page,
}) => {
  const forecast = [
    { t: "2026-09-26T10:30:00+02:00", soc: 50, feedin: 0 },
    { t: "2026-09-26T11:00:00+02:00", soc: 55, feedin: 100 },
    { t: "2026-09-26T12:00:00+02:00", soc: 60, feedin: 200 },
  ];
  await page.evaluate((attrs) => window.mount("forecast", attrs), {
    ...attributes,
    forecast,
  });
  const chart = page.locator("svg#chart");
  await chart.focus();
  await chart.press("Home");
  await expect(page.locator("#readout")).toContainText("50 Wh");
  await chart.press("ArrowRight");
  await expect(page.locator("#readout")).toContainText("200 Wh");
});

test("DST fallback uses HA wall time through both occurrences of 02:00", async ({
  page,
}) => {
  await page.evaluate((attrs) => window.mount("forecast", attrs), {
    ...attributes,
    forecast: [
      { t: "2026-10-25T01:00:00+02:00", soc: 40 },
      { t: "2026-10-25T02:00:00+02:00", soc: 50 },
      { t: "2026-10-25T02:00:00+01:00", soc: 60 },
      { t: "2026-10-25T03:00:00+01:00", soc: 70 },
    ],
  });
  const chart = page.locator("svg#chart");
  await chart.focus();
  await chart.press("Home");
  await expect(page.locator("#readout")).toContainText("01:00");
  await chart.press("ArrowRight");
  await expect(page.locator("#readout")).toContainText("02:00");
  await chart.press("ArrowRight");
  await expect(page.locator("#readout")).toContainText("02:00");
  await expect(page.locator("#readout")).toContainText("60");
  await chart.press("ArrowRight");
  await expect(page.locator("#readout")).toContainText("03:00");
});

test("active reserve labels the technical inverter floor without displaying a legacy target", async ({
  page,
}) => {
  await page.evaluate((attrs) => window.mount("forecast", attrs), {
    ...attributes,
    soc_threshold_percent: 73,
    inverter_min_soc_percent: 20,
    battery_min_soc_percent: 5,
    soc_buffer_percent: 10,
    reserve: {
      mode: "active",
      decision_reason: "pv_headroom_preparation",
      preparation_horizon_end: "2026-09-27T22:00:00Z",
      headroom_wh: 600,
      unavoidable_export_wh: 250,
      inverter_limit_w: 125,
      actual_soc_percent: 70,
      hold_soc_percent: 88,
    },
  });
  const chart = page.locator("#chart");
  await expect(page.locator("ha-card")).not.toContainText("T*");
  await expect(chart).toHaveAttribute(
    "aria-label",
    /Inverter lower limit 20 %/,
  );
  await expect(chart).not.toHaveAttribute("aria-label", /threshold/);
  await expect(page.locator('[data-marker="inverter-floor-label"]')).toHaveText(
    "Inverter lower limit 20 %",
  );
  await chart.focus();
  await chart.press("Home");
  await expect(page.locator("#readout")).toContainText(
    "Inverter lower limit 20 %",
  );
  await expect(page.locator("#readout")).not.toContainText("T*");
  const report = page.locator('[data-view-key="reserve-policy"]');
  await report.locator("summary").click();
  await expect(report).toContainText("full available forecast horizon");
  await expect(report).toContainText("600 Wh");
  await expect(report).toContainText("250 Wh");
  await expect(report).toContainText("125 W");
  await expect(report).not.toContainText("88");
  await page.evaluate(() => {
    window.card.hass = { ...window.card._hass, language: "de" };
  });
  await expect(page.locator('[data-marker="inverter-floor-label"]')).toHaveText(
    "Inverter-Untergrenze 20 %",
  );
  await expect(report).toContainText("gesamten verfügbaren Prognosehorizont");
  await expect(report).toContainText("28.09.2026, 00:00");
  await expect(report).toContainText("kein Reserve- oder Entladeziel");
  await expect(report).not.toContainText("Halteziel");
});

for (const mode of ["off", "shadow"])
  test(`reserve ${mode}: legacy threshold remains visible`, async ({
    page,
  }) => {
    await page.evaluate((attrs) => window.mount("forecast", attrs), {
      ...attributes,
      soc_threshold_percent: 73,
      inverter_min_soc_percent: 20,
      reserve: { mode },
    });
    await expect(page.locator("svg")).toContainText("T* 73 %");
    await expect(page.locator("#chart")).toHaveAttribute(
      "aria-label",
      /threshold 73 %/,
    );
  });

for (const [timestamp, date] of [
  ["2026-03-29T22:00:00Z", "30.03.2026"],
  ["2026-10-25T23:00:00Z", "26.10.2026"],
])
  test(`reserve horizon remains HA midnight around ${date}`, async ({
    page,
  }) => {
    await page.evaluate(
      (attrs) => {
        window.mount("forecast", attrs);
        window.card.hass = { ...window.card._hass, language: "de" };
      },
      {
        ...attributes,
        reserve: {
          mode: "active",
          preparation_horizon_end: timestamp,
          decision_reason: "no_preparation_needed",
        },
      },
    );
    const report = page.locator('[data-view-key="reserve-policy"]');
    await report.locator("summary").click();
    await expect(report).toContainText(`${date}, 00:00`);
  });

const switchingSchedule = [
  {
    start: "2026-09-26T10:00:00+02:00",
    end: "2026-09-26T10:15:00+02:00",
    inverter_on: true,
    dc24_on: false,
    dc48_on: false,
  },
  {
    start: "2026-09-26T10:15:00+02:00",
    end: "2026-09-26T10:35:00+02:00",
    inverter_on: false,
    dc24_on: true,
    dc48_on: false,
  },
  {
    start: "2026-09-26T10:35:00+02:00",
    end: "2026-09-26T12:00:00+02:00",
    inverter_on: false,
    dc24_on: true,
    dc48_on: true,
  },
];
test("forecast shows inverter on/off and visits sub-hour edges without invented SOC", async ({
  page,
}) => {
  await page.evaluate((attrs) => window.mount("forecast", attrs), {
    ...attributes,
    switching_schedule: switchingSchedule,
  });
  await expect(page.locator('[data-switching="inverter_on"]')).toHaveCount(2);
  await expect(page.locator('[data-switching="dc24_on"]')).toHaveCount(0);
  const chart = page.locator("svg#chart");
  await chart.focus();
  await chart.press("Home");
  await expect(page.locator("#readout")).toContainText("Inverter: on");
  await chart.press("ArrowRight");
  await expect(page.locator("#readout")).toContainText("10:15");
  await expect(page.locator("#readout")).toContainText("Inverter: off");
  await expect(page.locator("#readout")).not.toContainText("%");
  await expect(page.locator("#hover-marker circle")).toHaveCount(0);
  await page.locator('[data-view-key="switching-times"] summary').click();
  await expect(page.locator(".switching-details")).toContainText("10:15");
  await expect(page.locator(".switching-details")).toContainText("12:00");
});
test("supply toggle, focus and exact-time disclosure survive HA updates", async ({
  page,
}) => {
  await page.evaluate((attrs) => window.mount("forecast", attrs), {
    ...attributes,
    switching_schedule: switchingSchedule,
  });
  await page.getByLabel("Show 24/48 V power supplies").check();
  await expect(page.locator('[data-switching="dc24_on"]')).toHaveCount(2);
  await expect(page.locator('[data-switching="dc48_on"]')).toHaveCount(2);
  await page.evaluate(() => {
    window.card.hass = {
      ...window.card._hass,
      states: {
        "sensor.plan": {
          attributes: { ...window.card._hass.states["sensor.plan"].attributes },
        },
      },
    };
  });
  await expect(page.getByLabel("Show 24/48 V power supplies")).toBeChecked();
  await expect(page.getByLabel("Show 24/48 V power supplies")).toBeFocused();
  const chart = page.locator("svg#chart");
  await chart.focus();
  await chart.press("Home");
  await chart.press("ArrowRight");
  await chart.press("ArrowRight");
  await expect(page.locator("#readout")).toContainText("10:35");
  await expect(page.locator("#readout")).toContainText("48 V power supply: on");
  // Pointer and keyboard agree immediately before/after the 10:35 edge.
  const box = await chart.boundingBox();
  const view = await chart.evaluate((el) => el.viewBox.baseVal.width);
  for (const [minute, state] of [
    [34, "off"],
    [36, "on"],
  ]) {
    await page.mouse.move(
      box.x + ((32 + ((view - 62) * minute) / 120) / view) * box.width,
      box.y + 40,
    );
    await expect(page.locator("#readout")).toContainText(
      `48 V power supply: ${state}`,
    );
  }
  await page.getByLabel("Show 24/48 V power supplies").uncheck();
  await expect(page.locator('[data-switching="dc24_on"]')).toHaveCount(0);
});
test("power supplies can be enabled in card config and remain readable on a narrow card", async ({
  page,
}) => {
  await page.evaluate(
    (attrs) => {
      window.mount("forecast", attrs);
      document.querySelector("#host").style.width = "340px";
      window.card.setConfig({
        entity: "sensor.plan",
        show_power_supplies: true,
      });
    },
    { ...attributes, switching_schedule: switchingSchedule },
  );
  await expect(page.getByLabel("Show 24/48 V power supplies")).toBeChecked();
  await expect(page.locator('[data-switching="dc48_on"]')).toHaveCount(2);
  await page.locator(".switching-details summary").click();
  await expect(page.locator(".switching-details")).toContainText(
    "24 V power supply",
  );
  expect(
    await page
      .locator("ha-card")
      .evaluate((el) => el.scrollWidth <= el.clientWidth + 1),
  ).toBe(true);
});

for (const [language, labels] of [
  [
    "en",
    [
      "provisional · uncertainty unknown",
      "previous estimate · no observations in window",
      "established",
      "standard profile",
    ],
  ],
  [
    "de",
    [
      "vorläufig · Unsicherheit unbekannt",
      "bisherige Schätzung · keine Messungen im Lernfenster",
      "etabliert",
      "Standardprofil",
    ],
  ],
]) {
  test(`consumption explains sparse and retained estimates (${language})`, async ({
    page,
  }) => {
    await page.evaluate(
      ({ attrs, language }) => {
        window.mount("consumption", attrs);
        window.card.hass = { ...window.card._hass, language };
      },
      {
        language,
        attrs: {
          ...attributes,
          consumption_profile: {
            samples: { ac: { weekday: [3, 0, 10, 0] } },
            minimum_samples: { weekday: 10 },
            bin_quality: {
              ac: {
                weekday: ["provisional", "retained", "mature", "fallback"],
              },
            },
          },
        },
      },
    );
    await page.locator("#consumption-learning summary").click();
    const rows = page
      .locator("#consumption-learning table")
      .first()
      .locator("tbody tr");
    for (const [index, label] of labels.entries()) {
      await expect(rows.nth(index)).toContainText(label);
    }
    await expect(rows.nth(0)).toContainText("3 / 10");
    await expect(rows.nth(1)).toContainText("0 / 10");
  });
}

test("consumption distinguishes mixed sources and explains excluded hours", async ({
  page,
}) => {
  const attrs = structuredClone(attributes);
  attrs.consumption_forecast[0].src = "S/L";
  attrs.consumption_forecast[0].loads_w = 40;
  attrs.consumption_forecast[1].src = "L/S";
  attrs.consumption_profile = {
    samples: { ac: { weekday: [4] }, dc: { weekday: [60] } },
    minimum_samples: { weekday: 10 },
    ac_valid_since: "2026-08-30T00:00:00+02:00",
    excluded_hours: { "2026-09-28": { ac: { 6: ["support_unresolved"] } } },
    measurement_freshness: {
      ac: {
        configured: true,
        last_usable_at: "2026-10-09T04:00:00Z",
        stale: true,
        yesterday_empty: true,
        recent_valid_hours: 45,
        recent_window_hours: 168,
      },
      dc: {
        configured: true,
        last_usable_at: null,
        stale: false,
        yesterday_empty: true,
      },
    },
  };
  await page.evaluate((attrs) => window.mount("consumption", attrs), attrs);
  await expect(page.locator('[data-learning-warning="ac"]')).toBeVisible();
  await expect(page.locator('[data-learning-warning="ac"]')).toContainText(
    "No recent usable measurements",
  );
  await expect(page.locator('[data-learning-warning="dc"]')).toContainText(
    "No usable measurements yesterday",
  );
  await expect(page.locator('[data-layer="ac"]').first()).toHaveAttribute(
    "opacity",
    "0.35",
  );
  await expect(page.locator('[data-layer="dc24"]').first()).toHaveAttribute(
    "opacity",
    "0.88",
  );
  await expect(page.locator('[data-layer="dc48"]').first()).toHaveAttribute(
    "opacity",
    "0.88",
  );
  await expect(page.locator('[data-layer="loads"]').first()).toHaveAttribute(
    "opacity",
    "0.88",
  );
  await expect(page.locator('[data-layer="ac"]').nth(1)).toHaveAttribute(
    "opacity",
    "0.88",
  );
  await expect(page.locator('[data-layer="dc24"]').nth(1)).toHaveAttribute(
    "opacity",
    "0.35",
  );
  await page.locator("svg#chart").press("Home");
  await expect(page.locator("#readout")).toContainText(
    "AC: fallback · DC: learned",
  );
  await page.locator("#consumption-learning summary").click();
  await expect(page.locator("#consumption-learning")).toContainText("4 / 10");
  await expect(page.locator('[data-learning-freshness="ac"]')).toContainText(
    "06:00",
  );
  await expect(page.locator('[data-learning-freshness="ac"]')).toContainText(
    "45.0 / 168.0 h",
  );
  await expect(page.locator('[data-learning-freshness="dc"]')).toContainText(
    "— / — h",
  );
  await expect(page.locator("#consumption-learning")).toContainText(
    "2026-09-28 6:00",
  );
  await expect(page.locator("#consumption-learning")).toContainText(
    "24 V supply or switch history unresolved",
  );
  await page.locator("#consumption-learning summary").focus();
  await page.evaluate(() => {
    const card = window.card;
    card.hass = {
      ...card.hass,
      ...{
        language: "en",
        config: { time_zone: "Europe/Berlin" },
        states: {
          "sensor.plan": {
            attributes: structuredClone(card._lastState.attributes),
          },
        },
      },
    };
  });
  await expect(page.locator("#consumption-learning")).toHaveAttribute(
    "open",
    "",
  );
  await expect(page.locator("#consumption-learning summary")).toBeFocused();
});

test("DC source reasons and shifted budget remain visible in both languages", async ({
  page,
}) => {
  await page.evaluate((attrs) => window.mount("forecast", attrs), {
    ...attributes,
    reserve: {
      mode: "active",
      decision_reason: "dc_market_supply",
      dc_budget_wh: 120,
      dc_shifted_wh: 40,
    },
  });
  const report = page.locator('[data-view-key="reserve-policy"]');
  await report.locator("summary").click();
  await expect(report).toContainText("Already permitted DC battery energy");
  await expect(report).toContainText("120 Wh");
  await expect(report).toContainText("40 Wh");
  await page.evaluate(() => {
    window.card.hass = { ...window.card._hass, language: "de" };
  });
  await expect(report).toContainText("während einer Marktspitze");
  await expect(report).toContainText("Verfügbares DC-Entnahmebudget");
});

test("joint economic allocation survives refresh and localizes without limiting daily hours", async ({
  page,
}) => {
  await page.evaluate((attrs) => window.mount("forecast", attrs), {
    ...attributes,
    reserve: {
      mode: "active",
      market: {
        enabled: true,
        status: "available",
        joint: {
          joint_status: "search_limit",
          target_diagnostics_truncated: true,
          target_diagnostics: [
            {
              start: "2026-10-02T14:20:00Z",
              end: "2026-10-02T15:00:00Z",
              eur_per_mwh: 100,
              variant: "paired_dc",
              attempted: 1,
              local_rejections: {},
              proof_rejections: {},
              proved_improvements: 1,
              not_searched: false,
            },
          ],
          joint_budget_wh: 900,
          unavoidable_dc_draw_wh: 150,
          economic_daily_hours: { "2026-10-02": 7.5 },
          reference_grid_import_wh: 1234,
          actual_grid_import_wh: 1200,
          economic_intervals: [
            {
              start: "2026-10-02T12:00:00Z",
              end: "2026-10-02T13:00:00Z",
              eur_per_mwh: 5,
              avoided_import_wh: 10,
              allocated_battery_wh: 15,
              avoided_total_grid_import_wh: 12,
            },
            {
              start: "2026-10-02T14:00:00Z",
              end: "2026-10-02T15:00:00Z",
              eur_per_mwh: 100,
              avoided_import_wh: 24,
              allocated_battery_wh: 30,
              avoided_total_grid_import_wh: 25,
            },
          ],
        },
      },
    },
  });
  const report = page.locator('[data-view-key="reserve-policy"]');
  await report.locator("summary").click();
  const joint = report.locator("[data-joint-market]");
  await expect(joint).toContainText("7.5 h");
  await expect(joint.locator("[data-joint-targets]")).toContainText(
    "04:20:00 PM",
  );
  await expect(joint).toContainText("DC in a paired exchange");
  await expect(joint).toContainText(
    "further intervals or outcomes are omitted",
  );
  await expect(joint).toContainText("better allocation may still exist");
  const rows = joint.locator('[data-scroll-key="joint-intervals"] tbody tr');
  await expect(rows).toHaveCount(2);
  await expect(rows.first()).toContainText("100");
  await page.evaluate(() => {
    window.card.hass = { ...window.card._hass, language: "de" };
  });
  await expect(report).toHaveAttribute("open", "");
  await expect(joint).toContainText("7,5 h");
  await expect(joint).toContainText("DC im gekoppelten Tausch");
  await expect(joint).toContainText("Validierte Verbesserungen");
  await expect(joint).toContainText(
    "eine bessere Verteilung kann weiterhin möglich sein",
  );
  await expect(joint).toContainText("1.234 Wh");
  await expect(joint.locator("tbody").first()).toContainText("— Wh");
});

test("loads card distinguishes passthrough hold from charging and retains details on refresh", async ({
  page,
}) => {
  const attrs = {
    ...attributes,
    loads: [
      {
        load_id: "b1",
        name: "B1",
        active: false,
        available: true,
        schedule: [],
        input_control: {
          input_on: true,
          charge_enable: false,
          charging: false,
          output_on: true,
          output_power_w: 0,
          owned: true,
          reason: "held_passthrough",
          paused_input_power_w: 6,
        },
      },
    ],
  };
  await page.evaluate((attrs) => window.mount("loads", attrs), attrs);
  const section = page.locator('[data-view-key="input-control"]');
  await section.locator("summary").click();
  await expect(section).toContainText("AC output is on");
  await expect(section).toContainText("6 W");
  await page.evaluate(() => {
    window.card.hass = {
      ...window.card._hass,
      states: {
        ...window.card._hass.states,
        "sensor.plan": {
          ...window.card._hass.states["sensor.plan"],
          last_updated: "2026-10-27T11:01:00Z",
        },
      },
    };
  });
  await expect(section).toHaveAttribute("open", "");
  await expect(section).toContainText("Reported battery charging");
});
