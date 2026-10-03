import { test, expect } from "@playwright/test";
import { applianceFixture } from "../frontend/appliances-fixture.mjs";

async function mount(page, payload = applianceFixture, language = "en") {
  await page.evaluate(
    ({ payload, language }) => {
      window.appliancePayload = payload;
      window.applianceRequests = [];
      window.applianceHass = {
        language,
        config: { time_zone: "Europe/Berlin" },
        states: {
          "sensor.washer_status": {
            state: "running",
            attributes: {
              entry_id: "plant",
              appliance_id: "washer",
              revision: 1,
            },
          },
        },
        callWS: async (request) => {
          window.applianceRequests.push(request);
          return request.entry_id
            ? structuredClone(window.appliancePayload)
            : { entries: [{ entry_id: "plant", title: "My installation" }] };
        },
      };
      const card = document.createElement("battery-manager-appliances-card");
      card.setConfig({ entry_id: "plant" });
      card.hass = window.applianceHass;
      document.querySelector("#host").append(card);
      window.applianceCard = card;
    },
    { payload, language },
  );
  await expect(
    page.locator("battery-manager-appliances-card h3").first(),
  ).toHaveText(payload.appliances[0].name);
}
async function refresh(page) {
  await page.evaluate(() => {
    window.appliancePayload.revision++;
    const old = window.applianceHass.states["sensor.washer_status"];
    window.applianceHass = {
      ...window.applianceHass,
      states: {
        "sensor.washer_status": {
          ...old,
          attributes: {
            ...old.attributes,
            revision: window.appliancePayload.revision,
          },
        },
      },
    };
    window.applianceCard.hass = window.applianceHass;
  });
}

test.beforeEach(async ({ page }) => {
  await page.goto("/tests/browser/harness.html");
  await page.waitForFunction(() => window.ready);
});

test("appliances: HA timezone, inactive devices and measured/planned values remain distinct", async ({
  page,
}) => {
  await mount(page);
  await expect(page.locator(".appliance")).toHaveCount(2);
  const washer = page.locator(".appliance").first();
  await expect(washer).toContainText("10:35");
  await expect(washer).toContainText("Observed cycle energy");
  await expect(washer).toContainText("480 Wh");
  await washer.locator('[data-view-key="planning-washer"] summary').click();
  await expect(
    washer.locator('[data-view-key="planning-washer"]'),
  ).toContainText("600 Wh");
  await expect(washer.locator("svg")).toHaveCount(0);
  await expect(page.locator(".appliance").last()).toContainText("Idle");
  await page.evaluate(() => {
    window.applianceCard.hass = { ...window.applianceHass, language: "de" };
  });
  await expect(washer).toContainText("Beobachtete Laufenergie");
  await expect(page.locator("h2")).toHaveText("Haushaltsgeräte");
});

test("appliances: refresh preserves keyboard disclosure focus and nested history scrolling", async ({
  page,
}) => {
  const payload = structuredClone(applianceFixture);
  payload.appliances[0].learning.history = Array.from(
    { length: 30 },
    (_, i) => ({
      ...payload.appliances[0].learning.history[0],
      program: `Program ${i}`,
    }),
  );
  await mount(page, payload);
  const summary = page.locator('[data-view-key="history-washer"] summary');
  await summary.focus();
  await summary.press("Enter");
  const history = page.locator('[data-view-key="history-washer"]');
  await expect(history).toHaveAttribute("open", "");
  const scroll = history.locator(".table-scroll");
  await scroll.evaluate((node) => {
    node.scrollTop = 240;
  });
  const before = await scroll.evaluate((node) => node.scrollTop);
  await refresh(page);
  await expect
    .poll(() => page.evaluate(() => window.applianceRequests.length))
    .toBe(2);
  await expect(history).toHaveAttribute("open", "");
  await expect(summary).toBeFocused();
  await expect
    .poll(() => scroll.evaluate((node) => node.scrollTop))
    .toBe(before);
});

test("appliances: source keyboard action opens HA more-info and retains focus on refresh", async ({
  page,
}) => {
  await mount(page);
  await page.locator('[data-view-key="sources-washer"] summary').click();
  await page.evaluate(() =>
    document
      .querySelector("#host")
      .addEventListener("hass-more-info", (event) => {
        window.entityOpened = event.detail.entityId;
      }),
  );
  const source = page.getByRole("button", { name: "sensor.washer_power" });
  await source.focus();
  await source.press("Enter");
  await expect
    .poll(() => page.evaluate(() => window.entityOpened))
    .toBe("sensor.washer_power");
  await refresh(page);
  await expect
    .poll(() => page.evaluate(() => window.applianceRequests.length))
    .toBe(2);
  await expect(source).toBeFocused();
});

test("appliances: optional unconfigured sources are neutral and have no null entity action", async ({
  page,
}) => {
  const payload = structuredClone(applianceFixture);
  payload.appliances[0].sources.push({
    kind: "total_time",
    entity_id: null,
    state: null,
    available: false,
    status: "not_configured",
  });
  await mount(page, payload);
  await page.locator('[data-view-key="sources-washer"] summary').click();
  await expect(page.locator('[data-view-key="sources-washer"]')).toContainText(
    "Not configured",
  );
  await expect(page.locator('[data-entity-id="null"]')).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "sensor.washer_power" }),
  ).toBeVisible();
});

test("appliances editor discovers entries/devices and emits usable filters and title", async ({
  page,
}) => {
  await mount(page, applianceFixture, "de");
  await page.evaluate(() => {
    window.editorEvents = [];
    const editor = document.createElement(
      "battery-manager-appliances-card-editor",
    );
    editor.setConfig({ entry_id: "" });
    editor.hass = window.applianceHass;
    editor.addEventListener("config-changed", (event) =>
      window.editorEvents.push(event.detail.config),
    );
    document.querySelector("#host").replaceChildren(editor);
  });
  const entry = page.getByLabel("Battery-Manager-Installation");
  await expect(entry.locator("option")).toHaveCount(2);
  await entry.selectOption("plant");
  const devices = page.getByLabel("Haushaltsgeräte");
  await expect(devices.locator("option")).toHaveCount(2);
  await devices.selectOption(["washer"]);
  const title = page.getByLabel("Kartentitel");
  await title.fill("Meine Geräte");
  await expect
    .poll(() => page.evaluate(() => window.editorEvents.at(-1)))
    .toEqual({
      type: "custom:battery-manager-appliances-card",
      entry_id: "plant",
      appliance_ids: ["washer"],
      title: "Meine Geräte",
    });
  await devices.selectOption([]);
  await expect
    .poll(() =>
      page.evaluate(() => window.editorEvents.at(-1).appliance_ids ?? null),
    )
    .toBeNull();
});

test("appliances: untrusted names remain text and failed refresh has an accessible retry", async ({
  page,
}) => {
  const payload = structuredClone(applianceFixture);
  payload.appliances[0].name = '<img src=x onerror="window.injected=true">';
  await mount(page, payload);
  await expect(page.locator(".appliance img")).toHaveCount(0);
  await page.evaluate(() => {
    window.applianceHass.callWS = async () => {
      throw new Error("unreachable");
    };
  });
  await refresh(page);
  await expect(page.getByRole("status")).toContainText("last received data");
  await page.evaluate(() => {
    window.applianceHass.callWS = async () =>
      structuredClone(window.appliancePayload);
  });
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("status")).toHaveCount(0);
});

test("appliances editor ignores a delayed response after returning to a previously selected entry", async ({
  page,
}) => {
  await page.evaluate((payload) => {
    window.delayedDeviceResponses = [];
    const editor = document.createElement(
      "battery-manager-appliances-card-editor",
    );
    editor.setConfig({ entry_id: "plant" });
    editor.hass = {
      language: "en",
      states: {},
      callWS: async (request) => {
        if (!request.entry_id)
          return {
            entries: [
              { entry_id: "plant", title: "First" },
              { entry_id: "other", title: "Second" },
            ],
          };
        if (request.entry_id === "plant") return payload;
        return new Promise((resolve) =>
          window.delayedDeviceResponses.push(resolve),
        );
      },
    };
    document.querySelector("#host").append(editor);
  }, applianceFixture);
  const entry = page.getByLabel("Battery Manager installation");
  const devices = page.getByLabel("Appliances");
  await expect(devices.locator("option")).toHaveCount(2);
  await entry.selectOption("other");
  await expect
    .poll(() => page.evaluate(() => window.delayedDeviceResponses.length))
    .toBe(1);
  await entry.selectOption("plant");
  await expect(devices.locator("option")).toHaveCount(2);
  await page.evaluate(() =>
    window.delayedDeviceResponses[0]({
      entry_id: "other",
      revision: 1,
      appliances: [{ id: "wrong", name: "Wrong installation" }],
    }),
  );
  await expect(devices.locator("option")).toHaveText([
    "Washing machine",
    "Dryer",
  ]);
});
