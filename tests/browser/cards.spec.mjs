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
  await expect(report).toContainText("today and tomorrow");
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
  await expect(report).toContainText("heute und morgen");
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
