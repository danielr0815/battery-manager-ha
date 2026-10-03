import { test } from "node:test";
import assert from "node:assert/strict";
import { PlanDisplay } from "../../frontend/plan-state.js";
const valid = (s) =>
  Array.isArray(s?.attributes?.forecast) && s.attributes.forecast.length > 0;
const available = { state: "50", attributes: { forecast: [1, 2] } };

test("stripped unavailable/unknown publications retain only the same entity's last plan", () => {
  const cache = new PlanDisplay();
  assert.equal(cache.read("sensor.one", available, valid), available);
  for (const state of ["unavailable", "unknown"]) {
    const publication = { state, attributes: { friendly_name: "Plan" } };
    const shown = cache.read("sensor.one", publication, valid);
    assert.equal(shown.state, state);
    assert.deepEqual(shown.attributes.forecast, [1, 2]);
    assert.deepEqual(publication.attributes, { friendly_name: "Plan" });
  }
  const next = { state: "80", attributes: { forecast: [8, 9] } };
  cache.read("sensor.one", next, valid);
  assert.deepEqual(
    cache.read("sensor.one", { state: "unavailable" }, valid).attributes
      .forecast,
    [8, 9],
  );
  const other = { state: "unavailable", attributes: {} };
  assert.equal(cache.read("sensor.two", other, valid), other);
});

test("missing entities, empty valid publications and initial outages never invent a plan", () => {
  const cache = new PlanDisplay();
  const unavailable = { state: "unavailable", attributes: {} };
  assert.equal(cache.read("sensor.one", unavailable, valid), unavailable);
  cache.read("sensor.one", available, valid);
  assert.equal(cache.read("sensor.one", undefined, valid), undefined);
  assert.equal(cache.read("sensor.one", unavailable, valid), unavailable);
  cache.read("sensor.one", available, valid);
  const empty = { state: "50", attributes: { forecast: [] } };
  assert.equal(cache.read("sensor.one", empty, valid), empty);
  assert.equal(cache.read("sensor.one", unavailable, valid), unavailable);
  const retainedAttributes = { ...available, state: "unavailable" };
  assert.equal(
    cache.read("sensor.one", retainedAttributes, valid),
    retainedAttributes,
  );
});
