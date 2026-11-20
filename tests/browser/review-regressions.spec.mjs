import { test, expect } from "@playwright/test";
const attributes = {
  soc_threshold_percent: 20,
  plan_metadata: { captured_at: "2026-09-26T08:00:00Z" },
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

test("the operating forecast stays flat to the measured endpoint and labels the safety curve", async ({
  page,
}) => {
  const at = "2026-09-26T10:30:00+02:00";
  await mount(page, "forecast", {
    ...attributes,
    forecast: attributes.forecast.map((point, i) => ({
      ...point,
      soc: [68, 67.65, 66.95][i],
      safety_soc: [68, 67.3, 66.6][i],
    })),
    soc_expectation: {
      operating_forecast: true,
      status: "available",
      basis: "battery_net",
      net_discharge_w: 0,
      observed_seconds: 1800,
      end_at: at,
      end_soc: 68,
      stop_reason: "supply_evidence_ends",
    },
  });
  const safety = page.locator('[data-soc-curve="safety"]');
  const operating = page.locator('[data-soc-curve="expected"]');
  await expect(safety).toHaveAttribute("stroke-dasharray", "5 3");
  expect(await operating.getAttribute("stroke-dasharray")).toBeNull();
  const op = (await operating.getAttribute("points"))
    .split(" ")
    .map((p) => p.split(",").map(Number));
  expect(op).toHaveLength(4);
  expect(op[0][1]).toBe(op[1][1]);
  expect(op[2][1]).toBeGreaterThan(op[1][1]);
  await expect(page.locator("[data-soc-curve-explanation]")).toContainText(
    "10:30:00",
  );
  const chart = page.locator("#chart");
  await chart.focus();
  await chart.press("Home");
  await chart.press("ArrowRight");
  await chart.press("ArrowRight");
  await expect(page.locator("#readout")).toContainText(
    "Expected operating SOC 67.65 %",
  );
  await expect(page.locator("#readout")).toContainText(
    "Planning and safety curve 67.3 %",
  );
});

test("short grid-import expectation has its own sub-hour endpoint", async ({
  page,
}) => {
  const at = "2026-09-26T10:30:00+02:00";
  const attrs = {
    ...attributes,
    soc_expectation: {
      basis: "battery_net",
      evidence_mode: "grid_import",
      status: "available",
      observed_seconds: 1800,
      net_discharge_w: 0,
      grid_import_w: 288,
      grid_setpoint_w: 0,
      grid_margin_w: 50,
      points: [
        { t: attributes.forecast[0].t, expected_soc: 40 },
        { t: at, expected_soc: 40 },
      ],
    },
  };
  await mount(page, "forecast", attrs);
  const safety = page.locator('[data-soc-curve="safety"]');
  const expected = page.locator('[data-soc-curve="expected"]');
  const coords = async (el) =>
    (await el.getAttribute("points"))
      .split(" ")
      .map((p) => p.split(",").map(Number));
  const plan = await coords(safety);
  const observed = await coords(expected);
  expect(plan).toHaveLength(3);
  expect(observed).toHaveLength(2);
  expect(observed[1][0]).toBeCloseTo((plan[0][0] + plan[1][0]) / 2, 0);
  expect(observed[1][1]).toBe(observed[0][1]);
  const explanation = page.locator("[data-soc-curve-explanation]");
  await expect(explanation).toContainText("Measured grid import 288 W");
  await expect(explanation).toContainText("Effective grid setpoint 0 W");
  await expect(explanation).toContainText("10:30:00");
  const chart = page.locator("#chart");
  await chart.focus();
  await chart.press("Home");
  await chart.press("ArrowRight");
  const readout = page.locator("#readout");
  await expect(readout).toContainText(
    "Short-term SOC at sustained grid import 40 %",
  );
  await expect(readout).not.toContainText("safety");
  await chart.press("ArrowRight");
  await expect(readout).not.toContainText("Short-term SOC");
  await refresh(page, {
    ...attrs,
    soc_expectation: {
      ...attrs.soc_expectation,
      status: "unqualified",
      reason: "net_grid_margin_not_met",
    },
  });
  await expect(expected).toHaveCount(0);
  await expect(explanation).toContainText("too close");
});

test("normal SOC adjustment stays informative and preserves its open diagnostic on refresh", async ({
  page,
}) => {
  const attrs = {
    ...attributes,
    reserve: { mode: "active", upper_pv_factor: 1.2 },
    gate_calibration: {
      adaptive: {
        source: "configured_fallback",
        sample_count: 1518,
        bands: [],
        coordinate: { phase: "reconciling", level: "info" },
        qualification: {
          query_current_a: 0.706,
          current_covered: false,
          roles: [
            {
              role: "delivery",
              reason: "conflicting_coordinates",
              near_count: 12,
              span_seconds: 1800,
              spread_percent: 9,
            },
          ],
        },
      },
    },
  };
  await mount(page, "forecast", attrs);
  const reserve = page.locator('details[data-view-key="reserve-policy"]');
  await reserve.locator(":scope > summary").click();
  const diagnostic = page.locator('details[data-view-key="soc-coordinate"]');
  await diagnostic.locator(":scope > summary").click();
  await expect(diagnostic).toContainText("SOC coordinate is being adjusted");
  await expect(diagnostic).toContainText(
    "Older and newer SOC observations disagree",
  );
  await expect(diagnostic).toContainText("Conservative SOC forecast");
  await expect(diagnostic.locator('[role="alert"]')).toHaveCount(0);
  const updated = structuredClone(attrs);
  updated.gate_calibration.adaptive.coordinate.phase = "settling";
  await refresh(page, updated);
  await expect(diagnostic).toHaveAttribute("open", "");
  await expect(diagnostic).toContainText(
    "Collecting stable observations after adjustment",
  );
});
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
          "sensor.plan": {
            ...old.states["sensor.plan"],
            state: "unavailable",
            // HA's unavailable publication drops custom attributes entirely.
            attributes: { friendly_name: "Plan", unit_of_measurement: "%" },
          },
        },
      };
    });
    await expect(page.locator("#chart")).toBeVisible();
    await expect(page.getByRole("status")).toContainText("last known plan");
    await expect(page.getByRole("status")).toContainText("Last plan captured");
    await page.evaluate(
      (attrs) => {
        const old = window.card._hass;
        window.card.hass = {
          ...old,
          states: { "sensor.plan": { state: "50", attributes: attrs } },
        };
      },
      {
        ...attributes,
        forecast: attributes.forecast.map((p) => ({ ...p, soc: 80 })),
        consumption_forecast: attributes.consumption_forecast.map((p) => ({
          ...p,
          ac_w: 800,
        })),
      },
    );
    await expect(page.locator("[data-state-warning]")).toHaveCount(0);
    await page.locator("#chart").focus();
    await page.locator("#chart").press("Home");
    await expect(page.locator("#readout")).toContainText(
      kind === "forecast" ? "80 %" : "800 W",
    );
  });
  test(`${kind}: no cached plan is fabricated on startup or an entity change`, async ({
    page,
  }) => {
    await mount(page, kind);
    await page.evaluate(() => {
      const old = window.card._hass;
      window.card.hass = {
        ...old,
        states: {
          ...old.states,
          "sensor.other": { state: "unavailable", attributes: {} },
        },
      };
      window.card.setConfig({ entity: "sensor.other" });
    });
    await expect(page.locator("#chart")).toHaveCount(0);
    await expect(page.getByRole("status")).toContainText("No last known plan");
    await page.reload();
    await page.waitForFunction(() => window.ready);
    await mount(page, kind, {});
    await page.evaluate(() => {
      window.card.hass = {
        ...window.card._hass,
        states: {
          "sensor.plan": { state: "unavailable", attributes: {} },
        },
      };
    });
    await expect(page.locator("#chart")).toHaveCount(0);
    await expect(page.getByRole("status")).toContainText("No last known plan");
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

for (const kind of ["cascade", "loads"]) {
  test(`${kind}: stripped HA publications preserve the received schedule`, async ({
    page,
  }) => {
    const attrs = {
      ...attributes,
      loads: [load("retained-load")],
      cascades: [
        {
          ...load("retained-cascade"),
          cascade_id: "retained-cascade",
          members: [],
        },
      ],
    };
    await mount(page, kind, attrs);
    await expect(page.locator(".cascade")).toBeVisible();
    await page.evaluate(() => {
      window.card.hass = {
        ...window.card._hass,
        states: {
          "sensor.plan": {
            state: "unavailable",
            attributes: { friendly_name: "Plan" },
          },
        },
      };
    });
    await expect(page.locator(".cascade")).toBeVisible();
    await expect(page.getByRole("status")).toContainText("last known plan");
    await expect(page.getByRole("status")).toContainText("Last plan captured");
  });
}

test("grid guard reports whether it has usable net connection measurements", async ({
  page,
}) => {
  await mount(page, "forecast");
  const guard = page.locator("[data-load-grid-guard]");
  const publish = (load_grid_guard) =>
    refresh(page, { ...attributes, load_grid_guard });
  const baseline = {
    active: false,
    status: "unconfigured",
    grid_import_w: null,
    measured_w: null,
    age_seconds: null,
    threshold_w: 50,
    entity_id: null,
  };
  await expect(guard).toHaveCount(0);
  await publish(baseline);
  await expect(guard).toContainText("Grid import guard inactive");
  await expect(guard).toContainText("No grid meter configured");
  await expect(guard).toContainText("Cutoff above 50 W");

  const configured = { ...baseline, entity_id: "sensor.net_grid_power" };
  await publish({ ...configured, status: "unavailable" });
  await expect(guard).toContainText("Grid reading unavailable");
  await expect(guard).toContainText("sensor.net_grid_power");
  await expect(guard).not.toContainText("0 W ·");
  await publish({ ...configured, status: "invalid", age_seconds: 2 });
  await expect(guard).toContainText("Invalid grid reading");
  await expect(guard).toContainText("Age at check 2 s");
  await publish({
    ...configured,
    status: "stale",
    measured_w: 2000,
    age_seconds: 301,
  });
  await expect(guard).toContainText("Grid import guard inactive");
  await expect(guard).toContainText("Stale grid reading 2,000 W");
  await expect(guard).toContainText("Age at check 301 s");

  for (const [watts, active] of [
    [25, false],
    [50, false],
    [50.01, true],
  ]) {
    await publish({
      ...configured,
      status: "fresh",
      active,
      grid_import_w: watts,
      measured_w: watts,
      age_seconds: 0,
    });
    await expect(guard).toContainText(
      `Grid import guard ${active ? "active" : "inactive"}`,
    );
    await expect(guard).toContainText(`Fresh grid reading ${watts} W`);
    await expect(guard).toContainText("Age at check 0 s");
  }

  await page.evaluate(() => {
    window.card.hass = { ...window.card._hass, language: "de" };
  });
  await expect(guard).toContainText("Netzbezugsschutz aktiv");
  await expect(guard).toContainText("Aktueller Netzleistungswert 50,01 W");
  await expect(guard).toContainText("Schaltschwelle über 50 W");
  await page.evaluate(() => {
    const hass = window.card._hass;
    window.card.hass = {
      ...hass,
      states: { "sensor.plan": { state: "unavailable", attributes: {} } },
    };
  });
  await expect(page.locator("#chart")).toBeVisible();
  await expect(guard).toHaveCount(0);
});

for (const [language, message] of [
  [
    "en",
    {
      age: "Age at check",
      fresh: "Fresh grid reading",
      stale: "Stale grid reading",
    },
  ],
  [
    "de",
    {
      age: "Alter bei Prüfung",
      fresh: "Aktueller Netzleistungswert",
      stale: "Veralteter Netzleistungswert",
    },
  ],
]) {
  test(`grid guard shows the 30-second freshness boundary (${language})`, async ({
    page,
  }) => {
    const guard = {
      active: false,
      status: "fresh",
      grid_import_w: 20,
      measured_w: 20,
      age_seconds: 30,
      max_age_seconds: 30,
      threshold_w: 50,
    };
    await mount(page, "forecast", { ...attributes, load_grid_guard: guard });
    await page.evaluate((language) => {
      window.card.hass = { ...window.card._hass, language };
    }, language);
    const report = page.locator("[data-load-grid-guard]");
    await expect(report).toContainText(message.fresh);
    await expect(report).toContainText(`${message.age} 30 s`);
    await refresh(page, {
      ...attributes,
      load_grid_guard: {
        ...guard,
        status: "stale",
        grid_import_w: null,
        age_seconds: 30.001,
      },
    });
    await expect(report).toContainText(message.stale);
    await expect(report).toContainText(`${message.age} > 30 s`);
  });
}

for (const [language, copy] of [
  [
    "en",
    {
      inactive: "Grid import guard inactive",
      current: "Fresh grid reading 0 W",
      lastStop: "Last stop request by grid import guard",
      decisionAt: "Decision at",
      previous: "Grid reading at decision 2,000 W",
      age: "Age at check 2 s",
      short:
        "The DC readings do not cover the whole first matching plan interval.",
      limit:
        "A positive, finite DC power limit is required to validate the readings. Check the configured output voltage and maximum current.",
      topology:
        "The configured measurement cannot isolate the 48 V power supply’s DC output.",
    },
  ],
  [
    "de",
    {
      inactive: "Netzbezugsschutz inaktiv",
      current: "Aktueller Netzleistungswert 0 W",
      lastStop: "Letzte Abschaltanforderung des Netzbezugsschutzes",
      decisionAt: "Entscheidung um",
      previous: "Netzleistungswert bei Entscheidung 2.000 W",
      age: "Alter bei Prüfung 2 s",
      short:
        "Die DC-Messwerte decken das erste passende Planintervall nur teilweise ab.",
      limit:
        "Zur Plausibilitätsprüfung ist eine positive, endliche DC-Leistungsgrenze erforderlich. Ausgangsspannung und Maximalstrom prüfen.",
      topology:
        "Die konfigurierte Messung kann die DC-Abgabe des 48-V-Netzteils nicht eindeutig erfassen.",
    },
  ],
]) {
  test(`grid guard separates the stop request from the current reading (${language})`, async ({
    page,
  }) => {
    const reading = {
      active: false,
      status: "fresh",
      grid_import_w: 0,
      measured_w: 0,
      age_seconds: 0,
      entity_id: "sensor.net_grid_power",
      threshold_w: 50,
      max_age_seconds: 30,
    };
    const decision = {
      ...reading,
      action: "stop_requested",
      path: "plan",
      active: true,
      decided_at: "2026-09-26T08:00:02Z",
      measured_at: "2026-09-26T08:00:00Z",
      measured_w: 2000,
      age_seconds: 2,
      load_ids: ["load-a"],
    };
    await mount(page, "forecast", {
      ...attributes,
      load_grid_guard: {
        ...reading,
        last_action: {
          ...decision,
          requested_at: "2026-09-26T08:00:07Z",
        },
      },
    });
    await page.evaluate((language) => {
      window.card.hass = { ...window.card._hass, language };
    }, language);
    const latest = page.locator("[data-load-grid-last-action]");
    const current = page.locator("[data-load-grid-guard]");
    await expect(current).toContainText(copy.inactive);
    await expect(current).toContainText(copy.current);
    await expect(current).not.toContainText(copy.previous);
    await expect(latest).toContainText(
      new RegExp(`${copy.lastStop}[^·]*·[^·]*10:00:07`),
    );
    await expect(latest).toContainText(copy.previous);
    await expect(latest).toContainText(copy.age);
    await expect(latest).toContainText(
      new RegExp(`${copy.decisionAt}[^·]*10:00:02`),
    );
    // The earlier diagnostic payload carried only the decision timestamp.
    await refresh(page, {
      ...attributes,
      load_grid_guard: { ...reading, last_action: decision },
    });
    await expect(latest).toContainText(
      new RegExp(`${copy.lastStop}\\s*·\\s*${copy.decisionAt}[^·]*10:00:02`),
    );
    await refresh(page, { ...attributes, load_grid_guard: reading });
    await expect(latest).toHaveCount(0);
  });

  test(`PSU explanation distinguishes insufficient projection and plausibility evidence (${language})`, async ({
    page,
  }) => {
    await mount(page, "forecast", {
      ...attributes,
      soc_expectation: {
        status: "no_window",
        reason: "observation_shorter_than_plan_interval",
        delivery_w: 35,
        observed_seconds: 1800,
      },
    });
    await page.evaluate((language) => {
      window.card.hass = { ...window.card._hass, language };
    }, language);
    const explanation = page.locator("[data-soc-curve-explanation]");
    await expect(explanation).toContainText(copy.short);
    await expect(explanation).toContainText("35 W");
    await expect(explanation).toContainText("30 min");
    await expect(page.locator('[data-soc-curve="expected"]')).toHaveCount(0);
    await refresh(page, {
      ...attributes,
      soc_expectation: {
        status: "unqualified",
        reason: "missing_dc_power_limit",
        relay_confirmed_on: true,
      },
    });
    await expect(explanation).toContainText(copy.limit);
    await expect(explanation).not.toContainText(copy.topology);
    await refresh(page, {
      ...attributes,
      soc_expectation: {
        status: "unqualified",
        reason: "invalid_dc_topology",
        relay_confirmed_on: true,
      },
    });
    await expect(explanation).toContainText(copy.topology);
    await expect(explanation).not.toContainText(copy.limit);
  });
}

test("measured PSU expectation ends separately from the safety curve", async ({
  page,
}) => {
  const attrs = {
    ...attributes,
    forecast: attributes.forecast.map((point, index) =>
      index < 2 ? { ...point, expected_soc: [40, 65][index] } : point,
    ),
    soc_expectation: { status: "available", delivery_w: 35 },
  };
  await mount(page, "forecast", attrs);
  const card = page.locator("battery-manager-forecast-card");
  const safety = card.locator('[data-soc-curve="safety"]');
  const expected = card.locator('[data-soc-curve="expected"]');
  const explanation = card.locator("[data-soc-curve-explanation]");
  await expect(card.locator("[data-soc-curve-legend]")).toContainText(
    "Planning and safety curve",
  );
  await expect(expected).toHaveAttribute("stroke-dasharray", "5 3");
  await expect(explanation).toContainText(
    "Minimum measured 48 V DC output 35 W",
  );
  await expect(explanation).toContainText(
    "The solid curve governs planning and safety decisions.",
  );
  await expect(explanation).not.toContainText("curves overlap");
  expect((await safety.getAttribute("points")).split(" ")).toHaveLength(3);
  expect((await expected.getAttribute("points")).split(" ")).toHaveLength(2);
  await card.locator("#chart").focus();
  await page.keyboard.press("Home");
  await page.keyboard.press("ArrowRight");
  await expect(card.locator("#readout")).toContainText(
    "With continued 48 V delivery 65 %",
  );
  await page.keyboard.press("End");
  await expect(card.locator("#readout")).not.toContainText(
    "With continued 48 V delivery",
  );
  await refresh(page, {
    ...attributes,
    soc_expectation: { status: "unqualified" },
  });
  await expect(expected).toHaveCount(0);
  await expect(explanation).toContainText(
    "30 minutes of continuous 48 V DC output readings",
  );
  await refresh(page, {
    ...attributes,
    soc_expectation: {
      status: "unqualified",
      reason: "missing_dc_meter",
      relay_confirmed_on: true,
    },
  });
  await expect(expected).toHaveCount(0);
  await expect(explanation).toContainText("Relay ON is confirmed");
  await expect(explanation).toContainText(
    "No 48 V DC output meter is configured.",
  );
  await expect(explanation).toContainText(
    "The solid curve governs planning and safety decisions.",
  );
  await refresh(page, {
    ...attributes,
    soc_expectation: {
      status: "unqualified",
      reason: "episode_too_short",
      relay_confirmed_on: true,
      observed_seconds: 1799.9,
    },
  });
  await expect(expected).toHaveCount(0);
  await expect(explanation).toContainText("Measurement span 29 min 59 s");
  await expect(explanation).toContainText("Fewer than 30 minutes");
  await refresh(page, {
    ...attributes,
    soc_expectation: {
      status: "no_window",
      reason: "no_matching_plan_window",
      delivery_w: 35,
      observed_seconds: 1800,
    },
  });
  await expect(expected).toHaveCount(0);
  await expect(explanation).toContainText(
    "Minimum measured 48 V DC output 35 W",
  );
  await expect(explanation).toContainText("Measurement span 30 min");
  await expect(explanation).toContainText(
    "no matching period in the current switching schedule",
  );
  await refresh(page, attributes);
  await expect(card.locator("[data-soc-curve-legend]")).toHaveCount(0);
  await expect(safety).toHaveCount(1);
});

for (const [language, copy] of [
  [
    "en",
    {
      netCurve: "With continued net battery discharge",
      readout: "With continued net battery discharge 40 %",
      netPower: "Net battery discharge from terminal readings 0 W",
      span: "Measurement span 75 min",
      until: "Comparable night window ends at",
      overlap: "The curves overlap",
      reasons: {
        no_matching_plan_window: "no interval with comparable native load",
        observation_shorter_than_plan_interval:
          "measured net discharge span is shorter",
        projection_unsupported: "cannot support this net discharge scenario",
        net_discharge_exceeds_native_load:
          "exceeds the planned native 48 V load",
        net_storage_limit: "would exceed the configured battery SOC limits",
        net_window_ends_before_interval:
          "night window ends before the first matching plan interval",
        net_episode_too_short:
          "Fewer than 30 minutes of stable net discharge readings",
        net_publication_unconfirmed:
          "measurements for the current plan publication are not yet confirmed",
        net_mode_unconfirmed: "inverter or configured grid source",
        net_ac_flow_incompatible:
          "The AC readings do not confirm grid pass-through for this period.",
        net_plan_outside_observation:
          "plan starts outside the period with a confirmed supply mode",
      },
      safety: "The solid curve governs planning and safety decisions.",
      direct: "Minimum measured 48 V DC output 0 W",
    },
  ],
  [
    "de",
    {
      netCurve: "Bei anhaltender Netto-Entladung",
      readout: "Bei anhaltender Netto-Entladung 40 %",
      netPower: "Netto-Entladung aus Messwerten an den Batterieklemmen 0 W",
      span: "Messspanne 75 min",
      until: "Ende des vergleichbaren Nachtfensters um",
      overlap: "Die Kurven liegen im gemeinsam dargestellten Zeitraum",
      reasons: {
        no_matching_plan_window:
          "kein Intervall mit vergleichbarer nativer Last",
        observation_shorter_than_plan_interval:
          "Messspanne der Netto-Entladung ist kürzer",
        projection_unsupported:
          "reicht für dieses Netto-Entladungsszenario nicht aus",
        net_discharge_exceeds_native_load:
          "übersteigt die geplante native 48-V-Last",
        net_storage_limit: "außerhalb der eingestellten Batteriegrenzen",
        net_window_ends_before_interval:
          "Nachtfenster endet vor dem Ende des ersten passenden Planintervalls",
        net_episode_too_short:
          "weniger als 30 Minuten stabiler Messwerte zur Netto-Entladung",
        net_publication_unconfirmed:
          "Messwerte zum aktuellen veröffentlichten Plan sind noch nicht bestätigt",
        net_mode_unconfirmed:
          "Wechselrichter oder die konfigurierte Netzquellen-Rückmeldung",
        net_ac_flow_incompatible:
          "Die AC-Messwerte bestätigen für diesen Zeitraum keine reine Netzdurchleitung.",
        net_plan_outside_observation:
          "Plan beginnt außerhalb des Zeitraums mit bestätigtem Versorgungsmodus",
      },
      safety:
        "Für Planung und Schutzentscheidungen gilt die durchgezogene Kurve.",
      direct: "Kleinste gemessene 48-V-DC-Abgabe 0 W",
    },
  ],
]) {
  test(`Net battery discharge labels, overlap and reasons (${language})`, async ({
    page,
  }) => {
    const attrs = {
      ...attributes,
      forecast: [
        { ...attributes.forecast[0], expected_soc: 40 },
        { ...attributes.forecast[1], soc: 39, expected_soc: 40 },
        { ...attributes.forecast[2], soc: 38 },
      ],
      soc_expectation: {
        status: "available",
        basis: "battery_net",
        net_discharge_w: 0,
        observed_seconds: 4500,
        valid_until: Date.parse(attributes.forecast[2].t) / 1000,
      },
    };
    await mount(page, "forecast", attrs);
    await page.evaluate((language) => {
      window.card.hass = { ...window.card._hass, language };
    }, language);
    const chart = page.locator("#chart");
    const legend = page.locator("[data-soc-curve-legend]");
    const explanation = page.locator("[data-soc-curve-explanation]");
    const safety = page.locator('[data-soc-curve="safety"]');
    const expected = page.locator('[data-soc-curve="expected"]');
    const readout = page.locator("#readout");
    await expect(legend).toContainText(copy.netCurve);
    await expect(explanation).toContainText(copy.netPower);
    await expect(explanation).toContainText(copy.span);
    await expect(explanation).toContainText(copy.until);
    await expect(explanation).toContainText("12:00:00");
    await expect(explanation).toContainText(copy.safety);
    await expect(explanation).not.toContainText(copy.direct);
    await expect(explanation).not.toContainText(copy.overlap);
    await expect(expected).toHaveAttribute("stroke-dasharray", "5 3");
    expect((await expected.getAttribute("points")).split(" ")).toHaveLength(2);
    expect((await safety.getAttribute("points")).split(" ")).toHaveLength(3);
    const { x, y } = await pointAt(page, attributes.forecast[1].t);
    await page.mouse.move(x, y);
    await expect(readout).toContainText(copy.readout);
    await chart.focus();
    await chart.press("Home");
    await chart.press("ArrowRight");
    await expect(readout).toContainText(copy.readout);
    await chart.press("End");
    await expect(readout).not.toContainText(copy.netCurve);

    await refresh(page, {
      ...attrs,
      forecast: [
        attrs.forecast[0],
        { ...attrs.forecast[1], soc: 40 },
        attrs.forecast[2],
      ],
    });
    await expect(expected).toHaveCount(1);
    await expect(explanation).toContainText(copy.overlap);

    // If the plan expects charging, a measured 0 W continuation lies below it.
    await refresh(page, {
      ...attrs,
      forecast: [
        attrs.forecast[0],
        { ...attrs.forecast[1], soc: 41 },
        attrs.forecast[2],
      ],
    });
    await expect(expected).toHaveCount(1);
    await expect(explanation).not.toContainText(copy.overlap);

    for (const [reason, message] of Object.entries(copy.reasons)) {
      const status = [
        "net_episode_too_short",
        "net_publication_unconfirmed",
        "net_mode_unconfirmed",
        "net_ac_flow_incompatible",
      ].includes(reason)
        ? "unqualified"
        : "no_window";
      await refresh(page, {
        ...attrs,
        soc_expectation: {
          basis: "battery_net",
          status,
          reason,
          observed_seconds: 1500,
        },
      });
      await expect(expected).toHaveCount(0);
      await expect(safety).toHaveCount(1);
      await expect(explanation).toContainText(message);
      await expect(explanation).toContainText("25 min");
      await expect(explanation).toContainText(copy.safety);
      await expect(legend).not.toContainText(copy.netCurve);
    }

    await refresh(page, {
      ...attributes,
      soc_expectation: {
        status: "unqualified",
        reason: "no_positive_delivery",
        delivery_w: 0,
      },
    });
    await expect(explanation).toContainText(copy.direct);
    await expect(explanation).not.toContainText(copy.netPower);
    await refresh(page, attributes);
    await expect(legend).toHaveCount(0);
    await expect(safety).toHaveCount(1);
  });
}

for (const [language, message] of [
  [
    "en",
    "The curves overlap within the chart’s resolution over the shared displayed period.",
  ],
  [
    "de",
    "Die Kurven liegen im gemeinsam dargestellten Zeitraum bis auf die Anzeigenauflösung übereinander.",
  ],
]) {
  test(`SOC overlap uses the common displayed forecast window and subpixel tolerance (${language})`, async ({
    page,
  }) => {
    const attrs = {
      ...attributes,
      forecast: [
        { ...attributes.forecast[0], expected_soc: 40 },
        { ...attributes.forecast[1], expected_soc: 60.1 },
        { ...attributes.forecast[2], expected_soc: 55 },
      ],
      soc_expectation: {
        status: "available",
        delivery_w: 35,
        observed_seconds: 1800,
        relay_confirmed_on: true,
      },
    };
    await mount(page, "forecast", attrs);
    await page.evaluate((language) => {
      window.card.hass = { ...window.card._hass, language };
    }, language);
    const explanation = page.locator("[data-soc-curve-explanation]");
    await expect(explanation).not.toContainText(message);
    await page.evaluate(() => {
      window.card.setConfig({ entity: "sensor.plan", hours: 1 });
    });
    await expect(explanation).toContainText(message);
    await expect(explanation).toContainText(
      language === "de"
        ? "Kleinste gemessene 48-V-DC-Abgabe 35 W"
        : "Minimum measured 48 V DC output 35 W",
    );
    await expect(explanation).toContainText(
      language === "de" ? "Messspanne 30 min" : "Measurement span 30 min",
    );
    await expect(explanation).not.toContainText(
      language === "de" ? "Relais EIN ist bestätigt" : "Relay ON is confirmed",
    );

    await page.evaluate(() => {
      window.card.setConfig({ entity: "sensor.plan", hours: 48 });
    });
    await refresh(page, {
      ...attrs,
      forecast: [attrs.forecast[0], attrs.forecast[1], attributes.forecast[2]],
    });
    await expect(explanation).toContainText(message);
    await expect(page.locator('[data-soc-curve="expected"]')).toHaveCount(1);
    await refresh(page, {
      ...attrs,
      forecast: [
        attrs.forecast[0],
        { ...attributes.forecast[1], expected_soc: 61 },
        attributes.forecast[2],
      ],
    });
    await expect(explanation).not.toContainText(message);
  });
}
