import { test, expect } from "@playwright/test";
const attributes = {
  soc_threshold_percent: 20,
  forecast: [
    { t: "2026-09-26T10:00:00+02:00", soc: 40 },
    { t: "2026-09-26T11:00:00+02:00", soc: 60 },
    { t: "2026-09-26T12:00:00+02:00", soc: 50 },
  ],
  consumption_forecast: [
    { t: "2026-09-26T10:00:00+02:00", ac_w: 100, duration_h: 1 },
    { t: "2026-09-26T11:00:00+02:00", ac_w: 200, duration_h: 1 },
  ],
  loads: [],
  cascades: [],
};
test.beforeEach(async ({ page }) => {
  await page.goto("/tests/browser/harness.html");
  await page.waitForFunction(() => window.ready);
});
async function mount(page, kind, attrs = attributes) {
  await page.evaluate(
    ([kind, attrs]) => window.mount(kind, attrs),
    [kind, attrs],
  );
}
async function refresh(page, attrs) {
  await page.evaluate((attrs) => {
    const previous = window.card._hass;
    window.card.hass = {
      ...previous,
      states: {
        "sensor.plan": {
          ...previous.states["sensor.plan"],
          attributes: attrs || { ...previous.states["sensor.plan"].attributes },
        },
      },
    };
  }, attrs);
}
async function pointAt(page, time) {
  // ResizeObserver can replace the chart before HA publishes its shadow slot.
  // Wait for the current measured width and visible target before using raw
  // coordinates; an old element handle can detach during a resize.
  await page.waitForFunction(() => {
    const card = window.card;
    const target = card.shadowRoot.getElementById("hover-target");
    const rect = target?.getBoundingClientRect();
    return (
      card._width > 0 &&
      Math.abs(card._width - card.getBoundingClientRect().width) <= 4 &&
      rect?.width > 0 &&
      rect?.height > 0
    );
  });
  const point = await page.evaluate((time) => {
    const card = window.card,
      meta = card._chartMeta;
    const svg = card.shadowRoot.getElementById("chart");
    const rect = svg.getBoundingClientRect();
    return {
      x:
        rect.left +
        (meta.x(Date.parse(time)) / svg.viewBox.baseVal.width) * rect.width,
      y:
        rect.top +
        ((meta.margin.top + 20) / svg.viewBox.baseVal.height) * rect.height,
    };
  }, time);
  return point;
}

for (const kind of ["forecast", "consumption"]) {
  test(`${kind}: chart focus and selected time survive a new HA publication`, async ({
    page,
  }) => {
    await mount(page, kind);
    const chart = page.locator("#chart");
    await chart.focus();
    await chart.press("Home");
    await chart.press("ArrowRight");
    await expect(page.locator("#readout")).toContainText("11:00");
    await refresh(page);
    await expect(chart).toBeFocused();
    await expect(page.locator("#readout")).toContainText("11:00");
    await chart.press("ArrowLeft");
    await expect(page.locator("#readout")).toContainText("10:00");
  });
  test(`${kind}: unavailable entities retain old charts with an explicit warning`, async ({
    page,
  }) => {
    await mount(page, kind);
    await page.evaluate(() => {
      const old = window.card._hass;
      window.card.hass = {
        ...old,
        states: {
          "sensor.plan": { ...old.states["sensor.plan"], state: "unavailable" },
        },
      };
    });
    await expect(page.locator("#chart")).toBeVisible();
    await expect(page.getByRole("status")).toContainText("last known plan");
  });
  test(`${kind}: HA timezone updates even if the sensor object is unchanged`, async ({
    page,
  }) => {
    await mount(page, kind);
    await expect(page.locator("#chart")).toHaveAttribute("aria-label", /11:00/);
    await page.evaluate(() => {
      window.card.hass = {
        ...window.card._hass,
        config: { time_zone: "America/New_York" },
      };
    });
    await expect(page.locator("#chart")).toHaveAttribute("aria-label", /05:00/);
    await expect(page.locator("#chart")).not.toHaveAttribute(
      "aria-label",
      /11:00/,
    );
  });
}

test("a removed selected slot has an explicit fallback instead of a fabricated cursor", async ({
  page,
}) => {
  await mount(page, "consumption");
  const chart = page.locator("#chart");
  await chart.focus();
  await chart.press("Home");
  await refresh(page, {
    ...attributes,
    consumption_forecast: [
      ...attributes.consumption_forecast.slice(1),
      { t: "2026-09-26T12:00:00+02:00", ac_w: 300, duration_h: 1 },
    ],
  });
  await expect(chart).toBeFocused();
  await expect(page.locator("#readout")).toContainText("no longer");
});

test("consumption hover selects the containing interval and exposes gaps", async ({
  page,
}) => {
  await mount(page, "consumption");
  let point = await pointAt(page, "2026-09-26T10:45:00+02:00");
  await page.mouse.move(point.x, point.y);
  await expect(page.locator("#readout")).toContainText("100 W");
  await expect(page.locator("#readout")).not.toContainText("200 W");
  await refresh(page, {
    ...attributes,
    consumption_forecast: [
      { t: "2026-09-26T10:00:00+02:00", ac_w: 100, duration_h: 0.5 },
      { t: "2026-09-26T11:00:00+02:00", ac_w: 200, duration_h: 1 },
    ],
  });
  point = await pointAt(page, "2026-09-26T10:45:00+02:00");
  await page.mouse.move(point.x, point.y);
  await expect(page.locator("#readout")).toContainText("No forecast value");
});

test.describe("touch selection", () => {
  test.use({ hasTouch: true, viewport: { width: 390, height: 844 } });
  for (const kind of ["forecast", "consumption"])
    test(`${kind}: a touch tap exposes details and they survive refresh`, async ({
      page,
    }) => {
      await mount(page, kind);
      await page
        .locator("#host")
        .evaluate((host) => (host.style.width = "340px"));
      const point = await pointAt(page, "2026-09-26T10:15:00+02:00");
      await page.touchscreen.tap(point.x, point.y);
      await expect(page.locator("#readout")).toContainText("10:00");
      await refresh(page);
      await expect(page.locator("#readout")).toContainText("10:00");
      await expect(page.locator("#chart")).toHaveCSS("touch-action", "pan-y");
    });
});

const load = (id) => ({
  load_id: id,
  name: id,
  target_soc_percent: 80,
  soc_percent: null,
  planned_energy_kwh: 0.2,
  schedule: [
    { start: "2026-09-26T08:00:00Z", end: "2026-09-26T10:00:00Z", wh: 200 },
  ],
});
test("loads: a focused period follows its load identity through refresh and reordering", async ({
  page,
}) => {
  await mount(page, "loads", {
    ...attributes,
    loads: [load("b1"), load("b2")],
  });
  const button = page
    .locator('button[data-action="period"][data-period="all"]')
    .first();
  await button.click();
  await expect(button).toBeFocused();
  await expect(page.locator("[data-tentative-plan]").first()).toContainText(
    "wake-up",
  );
  await refresh(page, { ...attributes, loads: [load("b2"), load("b1")] });
  await expect(
    page.locator('button[data-action="period"][data-period="all"]').nth(1),
  ).toBeFocused();
  await page.evaluate(
    () =>
      (window.card.hass = {
        ...window.card._hass,
        config: { time_zone: "America/New_York" },
      }),
  );
  await expect(page.locator(".agenda").last()).toContainText("04:00");
});

test("plan zero and confirmed live 2300 W display separately in the card", async ({
  page,
}) => {
  await mount(page, "forecast", {
    ...attributes,
    reserve: { mode: "active", inverter_limit_w: 0 },
    live_ac: { limit_w: 2300, confirmed: true, reason: "measured_ac_demand" },
    inverter_control: {
      requested_limit_w: 2300,
      observed_limit_w: 2300,
      confirmed: true,
      observed_at: "2026-09-26T08:01:00Z",
      requested_at: "2026-09-26T08:00:55Z",
    },
    plan_metadata: {
      captured_at: "2026-09-26T08:00:00Z",
      activated_at: "2026-09-26T08:00:10Z",
    },
  });
  const report = page.locator('[data-view-key="inverter-control"]');
  await report.locator("summary").click();
  await expect(report).toContainText("Inverter permission in the plan");
  await expect(report).toContainText("0 W");
  await expect(report).toContainText("2,300 W");
  await expect(report).toContainText("Confirmed");
  await expect(report).toContainText("Measured AC demand");
});

test("empty history errors and source quality remain accessible without chart data", async ({
  page,
}) => {
  await mount(page, "forecast", {
    ...attributes,
    operation_report: { days: [], last_error: "corrupt" },
    source_health: [
      {
        role: "ac",
        entity_id: "sensor.house",
        status: "stale",
        value: 0,
        unit: "W",
        reported_at: "2026-09-26T07:30:00Z",
      },
    ],
  });
  await page.locator('[data-view-key="operation-report"] summary').click();
  await expect(page.locator("[data-history-error]")).toBeVisible();
  await expect(
    page.locator('[data-view-key="operation-report"]'),
  ).toContainText("No recorded daily reports");
  await page.locator('[data-view-key="source-health"] summary').click();
  await expect(page.locator('[data-view-key="source-health"]')).toContainText(
    "Stale publication",
  );
  await expect(page.locator('[data-view-key="source-health"]')).toContainText(
    "0 W",
  );
});
