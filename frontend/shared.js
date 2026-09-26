/**
 * Battery Manager Forecast, Consumption, Cascade + Loads Cards
 *
 * Bundled with the battery_manager integration and registered as a Lovelace
 * resource automatically — no HACS frontend download needed. This module
 * registers four card types, all reading `sensor.…_soc_forecast`:
 *
 *   battery-manager-forecast-card
 *                                 the planned SOC trajectory with the full
 *                                 plan context (loads, appliances, feed-in)
 *   battery-manager-consumption-card
 *                                 the planned CONSUMPTION per slot, split by
 *                                 voltage level (230 V AC / 48 V / 24 V) with
 *                                 the planned surplus loads as their own
 *                                 layer (attribute `consumption_forecast`,
 *                                 backend >= v0.25.5)
 *   battery-manager-loads-card    forecasts for loads outside cascades
 *   battery-manager-cascade-card  the internal Root/charge/discharge/output/
 *                                 terminal timeline of every storage cascade
 *
 * The forecast card renders from these sensor attributes:
 *
 *   forecast                    [{t, soc, feedin}, ...]
 *                                 planned SOC curve; feedin is the planned
 *                                 early grid feed-in power in W per slot
 *   soc_threshold_percent       optimal inverter threshold T*
 *   battery_min/max_soc_percent hard SOC limits
 *   inverter_min_soc_percent    inverter cut-off
 *   soc_buffer_percent          planning buffer above the minimum
 *   grid_import_kwh             expected grid import over the horizon
 *   lost_surplus_kwh            surplus that will still be lost/exported
 *   loads                       [{name, active, planned_energy_kwh,
 *                                 today_kwh, tomorrow_kwh,
 *                                 schedule: [{start, end}]}]
 *   appliances                  detected running appliances (washer, …):
 *                                 [{name, active, schedule: [{start, end,
 *                                 wh}]}] — one block now -> run end
 *   cascades                    storage cascades: the SOC chart shows only
 *                                 their Root-boundary energy; the dedicated
 *                                 cascade card renders internal activity
 *
 * Vanilla web component (no build step, no external dependencies); theming
 * via Home Assistant CSS variables inside an <ha-card>.
 *
 * The entity is user-configurable, so every attribute read is validated:
 * numbers via num(), arrays via Array.isArray(), and collection sizes are
 * capped (MAX_*). Accessibility: the SVG is a labelled img, focusable, and
 * the arrow keys step through the forecast slots; a visually hidden text
 * summary carries the key figures for screen readers.
 */

// Read the version from this module's own `?v=` cache-bust param (set from
// manifest.json when the resource is registered) so it never drifts.
export const CARD_VERSION =
  new URL(import.meta.url).searchParams.get("v") || "dev";

export const CARD_TYPE = "battery-manager-forecast-card";

export const CASCADE_CARD_TYPE = "battery-manager-cascade-card";

export const DOCS_URL = "https://github.com/danielr0815/battery-manager-ha";

// Lane palette as theme-overridable custom properties. The fallbacks were
// picked for >= 3:1 contrast against both #fff and #111/#1c1c1c card
// backgrounds (WCAG AA for non-text, verified numerically 2026-07) — this
// is why orange/purple deviate from the original Material-600 set. Color
// is never the only channel: the legend pairs every dot with a text label.
export const LOAD_COLORS = [
  "var(--bmpc-load-1-color, #43a047)", // green
  "var(--bmpc-load-2-color, #ef6c00)", // orange
  "var(--bmpc-load-3-color, #039be5)", // light blue
  "var(--bmpc-load-4-color, #ba68c8)", // purple
  "var(--bmpc-load-5-color, #e53935)", // red
  "var(--bmpc-load-6-color, #00897b)", // teal
];

// Early grid feed-in lane (F-FEEDIN): pink-600, >= 3:1 on light and dark
// card backgrounds and distinct from every load lane color.
export const FEEDIN_COLOR = "var(--bmpc-feedin-color, #d81b60)";

export const CASCADE_ROOT_COLOR = "var(--bmpc-cascade-root-color, #1976d2)";

export const CASCADE_CHARGE_COLOR = "var(--bmpc-cascade-charge-color, #43a047)";

export const CASCADE_DISCHARGE_COLOR =
  "var(--bmpc-cascade-discharge-color, #ef6c00)";

export const CASCADE_OUTPUT_COLOR = "var(--bmpc-cascade-output-color, #8e24aa)";

export const CASCADE_TERMINAL_COLOR =
  "var(--bmpc-cascade-terminal-color, #00897b)";

export const CASCADE_SOC_COLORS = [
  "var(--bmpc-cascade-soc-1-color, #039be5)",
  "var(--bmpc-cascade-soc-2-color, #ef6c00)",
  "var(--bmpc-cascade-soc-3-color, #8e24aa)",
  "var(--bmpc-cascade-soc-4-color, #43a047)",
];

// Defensive caps: attributes are user-controlled input, and a broken or
// hostile payload must not freeze the UI with megabytes of SVG.
export const MAX_POINTS = 1000;
// forecast samples kept (stride-downsampled)
export const MAX_LANES = 12;
// load + cascade + appliance + feed-in lanes below the plot
export const MAX_BLOCKS = 100;
// schedule blocks rendered per lane
export const MAX_CASCADE_POINTS = 10000;

// Entity names, titles etc. are user-controlled — escape before innerHTML.
export function esc(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

// Attribute values arrive as JSON and may be strings, objects or NaN.
// Accept anything Number() can make finite; treat the rest as absent so
// callers fall back to their default instead of crashing on .toFixed().
export function num(value) {
  if (value == null || value === "" || typeof value === "boolean") {
    return undefined;
  }
  const n = Number(value);
  return Number.isFinite(n) ? n : undefined;
}

export function isForecastEntity(stateObj) {
  const fc = stateObj?.attributes?.forecast;
  return (
    Array.isArray(fc) &&
    fc.length > 1 &&
    typeof fc[0] === "object" &&
    fc[0] !== null &&
    "soc" in fc[0] &&
    "t" in fc[0]
  );
}

export function findForecastEntity(hass, entities) {
  const candidates = (entities || []).filter(
    (id) => id.startsWith("sensor.") && isForecastEntity(hass.states[id]),
  );
  // Prefer the battery_manager naming if several sensors expose a forecast
  return (
    candidates.find((id) => id.includes("soc_forecast")) || candidates[0] || ""
  );
}

// ---------------------------------------------------------------------------
// Consumption forecast card (v0.25.5, operator request 2026-08-08)
//
// Stacked hourly bars per voltage level (230 V AC / 48 V / 24 V) with the
// planned surplus loads as their own top layer and a total line. Reads the
// `consumption_forecast` attribute of the same SOC-forecast sensor:
// [{t, ac_w, dc48_w, dc24_w, loads_w, src}, ...] — src is the per-path
// origin "L/S" (learned/static); slots with a static fallback render dimmed.
// ---------------------------------------------------------------------------

export const CONSUMPTION_CARD_TYPE = "battery-manager-consumption-card";

// Layer palette, theme-overridable like the load colors above (fallbacks
// >= 3:1 contrast on light and dark card backgrounds).
export const AC_COLOR = "var(--bmpc-ac-color, #1e88e5)";

export const DC48_LAYER_COLOR = "var(--bmpc-dc48-layer-color, #7e57c2)";

export const DC24_LAYER_COLOR = "var(--bmpc-dc24-layer-color, #009688)";

export const PLANNED_LAYER_COLOR = "var(--bmpc-planned-layer-color, #ef6c00)";

// Standalone loads share the cascade card's time clipping, exact Wh/W charts,
// keyboard navigation and preserved UI state, but have no cascade topology.
export const LOADS_CARD_TYPE = "battery-manager-loads-card";
