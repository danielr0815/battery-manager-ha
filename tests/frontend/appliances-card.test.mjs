import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import { applianceFixture } from "./appliances-fixture.mjs";
const definitions = new Map();
const context = vm.createContext({
  URL,
  Intl,
  Date,
  console,
  HTMLElement: class {
    attachShadow() {
      this.shadowRoot = {
        innerHTML: "",
        querySelectorAll: () => [],
        getElementById: () => null,
      };
    }
  },
  ResizeObserver: class {
    observe() {}
    disconnect() {}
  },
  customElements: {
    get: (key) => definitions.get(key),
    define: (key, value) => definitions.set(key, value),
  },
  window: {},
});
vm.runInContext(
  readFileSync(
    new URL(
      "../../custom_components/battery_manager/frontend/battery-manager-forecast-card.js",
      import.meta.url,
    ),
    "utf8",
  ).replaceAll("import.meta.url", '"https://example.test/card.js"'),
  context,
);
const Card = definitions.get("battery-manager-appliances-card");
const flush = () => new Promise(setImmediate);
const clone = (value) => JSON.parse(JSON.stringify(value));
const state = (revision, extra = {}) => ({
  "sensor.washer_status": {
    state: "running",
    attributes: { entry_id: "plant", appliance_id: "washer", revision },
  },
  ...extra,
});
function card(callWS, config = {}, language = "en") {
  const value = new Card();
  value.setConfig({ entry_id: "plant", ...config });
  value.hass = {
    language,
    config: { time_zone: "Europe/Berlin" },
    states: state(1),
    callWS,
  };
  value.connectedCallback();
  return value;
}

test("appliance card renders inactive devices, observations and honest profile statistics", async () => {
  const c = card(async () => clone(applianceFixture));
  await flush();
  const html = c.shadowRoot.innerHTML;
  for (const text of [
    "Washing machine",
    "Dryer",
    "420 W",
    "480 Wh",
    "600 Wh",
    "Single observation",
    "Median",
    "No learned profile yet.",
    "Measurement gap",
    "Energy counter reset",
    "10:35",
  ])
    assert.ok(html.includes(text), text);
  assert.ok(!html.includes("<svg"));
  assert.ok(!html.includes("<strong>0 Wh</strong>"));
  assert.ok(html.includes("—"));
});

test("revision publications are coalesced and an obsolete in-flight payload is never shown", async () => {
  const requests = [];
  const resolvers = [];
  const c = card((request) => {
    requests.push(clone(request));
    return new Promise((resolve) => resolvers.push(resolve));
  });
  await flush();
  c.hass = { ...c._hass, states: state(2) };
  c.hass = { ...c._hass, states: state(3) };
  resolvers[0]({
    ...clone(applianceFixture),
    revision: 1,
    appliances: [{ id: "washer", name: "Obsolete response" }],
  });
  await flush();
  assert.equal(requests.length, 2);
  assert.ok(!c.shadowRoot.innerHTML.includes("Obsolete response"));
  resolvers[1]({ ...clone(applianceFixture), revision: 3 });
  await flush();
  assert.ok(c.shadowRoot.innerHTML.includes("Washing machine"));
  c.hass = {
    ...c._hass,
    states: state(3, { "sensor.unrelated": { state: "new" } }),
  };
  await flush();
  assert.equal(requests.length, 2);
});

test("configuration and disconnect invalidate pending responses, reconnect refetches", async () => {
  const requests = [],
    resolvers = [];
  const c = card((request) => {
    requests.push(clone(request));
    return new Promise((resolve) => resolvers.push(resolve));
  });
  await flush();
  c.setConfig({ entry_id: "other", appliance_ids: ["other_device"] });
  resolvers[0](clone(applianceFixture));
  await flush();
  assert.deepEqual(requests[1], {
    type: "battery_manager/appliances",
    entry_id: "other",
    appliance_ids: ["other_device"],
  });
  c.disconnectedCallback();
  resolvers[1]({
    entry_id: "other",
    revision: 1,
    appliances: [{ id: "other_device", name: "Disconnected response" }],
  });
  await flush();
  assert.ok(!c.shadowRoot.innerHTML.includes("Disconnected response"));
  c.connectedCallback();
  await flush();
  assert.equal(requests.length, 3);
  resolvers[2]({ entry_id: "other", revision: 2, appliances: [] });
  await flush();
  assert.ok(c.shadowRoot.innerHTML.includes("No appliances configured"));
});

test("fetch failures preserve last data with an explicit warning and retry recovers", async () => {
  let failed = false;
  const c = card(async () => {
    if (failed) throw new Error("offline");
    return clone(applianceFixture);
  });
  await flush();
  failed = true;
  c.hass = { ...c._hass, states: state(2) };
  await flush();
  assert.ok(c.shadowRoot.innerHTML.includes("last received data"));
  assert.ok(c.shadowRoot.innerHTML.includes("Washing machine"));
  failed = false;
  c._refresh();
  await flush();
  assert.ok(!c.shadowRoot.innerHTML.includes("could not be updated"));
});

test("device filtering, German labels and escaping apply to every diagnostics section", async () => {
  const payload = clone(applianceFixture);
  payload.appliances[0].name = '<img src=x onerror="alert(1)">';
  payload.appliances[0].learning.profiles[0].program =
    "<script>alert(1)</script>";
  const requests = [];
  const c = card(
    async (request) => {
      requests.push(clone(request));
      return payload;
    },
    { appliance_ids: ["washer"] },
    "de",
  );
  await flush();
  assert.deepEqual(requests[0].appliance_ids, ["washer"]);
  assert.ok(!c.shadowRoot.innerHTML.includes("Dryer"));
  for (const text of [
    "Einzelbeobachtung",
    "zusätzlichen Netzbezug",
    "Messlücke",
  ]) {
    assert.ok(c.shadowRoot.innerHTML.includes(text), text);
  }
  assert.ok(c.shadowRoot.innerHTML.includes("&lt;img"));
  assert.ok(c.shadowRoot.innerHTML.includes("&lt;script&gt;"));
  assert.ok(!c.shadowRoot.innerHTML.includes("<script>"));
  assert.throws(() => c.setConfig({ entry_id: 4 }), /configuration/);
  assert.throws(
    () => c.setConfig({ entry_id: "plant", appliance_ids: "washer" }),
    /configuration/,
  );
});

test("the aggregate energy profile never presents an invented duration", async () => {
  const c = card(async () => clone(applianceFixture));
  await flush();
  const html = c._profile({ count: 1, energy_wh: 550, duration_h: 1.5 }, true);
  assert.ok(html.includes("Single observation"));
  assert.ok(html.includes("550 Wh"));
  assert.ok(!html.includes("90 min"));
  assert.ok(!html.includes("Duration"));
});

test("an integration reload may reset revisions without freezing the displayed data", async () => {
  let payload = { ...clone(applianceFixture), revision: 20 };
  const c = card(async () => payload);
  await flush();
  payload = { ...clone(applianceFixture), revision: 1 };
  payload.appliances[0].name = "After integration reload";
  c.hass = { ...c._hass, states: state(0) };
  await flush();
  assert.ok(c.shadowRoot.innerHTML.includes("After integration reload"));
});

test("picker labels follow the HA language and editor is registered separately", () => {
  context.document = { querySelector: () => ({ hass: { language: "de" } }) };
  const picker = context.window.customCards.find(
    (item) => item.type === "battery-manager-appliances-card",
  );
  assert.equal(picker.name, "Battery Manager · Haushaltsgeräte");
  assert.ok(definitions.has("battery-manager-appliances-card-editor"));
  delete context.document;
});

test("an invalid active cycle explains its learning exclusion before any history exists", async () => {
  const payload = clone(applianceFixture);
  payload.appliances[0].learning = {
    status: "invalid",
    sample_count: 0,
    profiles: [],
    history: [],
    reasons: ["missing_start"],
    warnings: ["invalid_duration"],
    device_profile: { count: 1, energy_wh: 550, selected: true },
  };
  const c = card(async () => payload);
  await flush();
  const html = c.shadowRoot.innerHTML;
  assert.ok(html.includes("Cycle start was not observed"));
  assert.ok(html.includes("Invalid observed duration"));
  assert.ok(html.includes("Used for planning"));
  assert.ok(html.includes("No observed cycles yet."));
  assert.ok(
    html.indexOf("Cycle start was not observed") <
      html.indexOf('data-view-key="history-washer"'),
  );
});
