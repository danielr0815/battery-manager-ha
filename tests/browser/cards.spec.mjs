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
