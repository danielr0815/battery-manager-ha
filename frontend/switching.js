import { esc } from "./shared.js";
import { dateTimeFormat } from "./time.js";

// Separate from the hourly SOC samples: a five-minute source change must
// survive chart downsampling and another device switching in the same hour.
export function switchingLanes(attributes, t0, t1, showSupplies, t) {
  const source = Array.isArray(attributes.switching_schedule)
    ? attributes.switching_schedule.slice(0, 4096)
    : [];
  return [
    ["inverter_on", "inverter_lane", "INV", "#ab78e8"],
    ...(showSupplies
      ? [
          ["dc24_on", "psu24_lane", "24 V", "#e6a23c"],
          ["dc48_on", "psu48_lane", "48 V", "#26a69a"],
        ]
      : []),
  ].map(([key, name, shortName, color]) => {
    const schedule = [];
    const intervals = source
      .filter((b) => b && typeof b[key] === "boolean")
      .map((b) => ({
        start: Math.max(t0, Date.parse(b.start)),
        end: Math.min(t1, Date.parse(b.end)),
        on: b[key],
      }))
      .filter(
        (b) =>
          Number.isFinite(b.start) && Number.isFinite(b.end) && b.end > b.start,
      )
      .sort((a, b) => a.start - b.start);
    for (const interval of intervals) {
      const last = schedule.at(-1);
      if (last?.end === interval.start && last.on === interval.on)
        last.end = interval.end;
      else schedule.push(interval);
    }
    return {
      key,
      name: t(name),
      shortName,
      color,
      schedule,
      kind: "switching",
    };
  });
}

export function switchingDetails(lanes, hass, showSupplies, t) {
  const fmt = dateTimeFormat(hass, {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    timeZoneName: "short",
  });
  return `<div class="switching-controls">
    <span>${esc(t(lanes.some((lane) => lane.schedule.length) ? "switching_hint" : "switching_unavailable"))}</span>
    <label><input id="show-power-supplies" data-focus-key="show-power-supplies" type="checkbox" ${showSupplies ? "checked" : ""}>${esc(t("field_show_power_supplies"))}</label>
  </div>
  <details class="switching-details" data-view-key="switching-times">
    <summary>${esc(t("switching_times"))}</summary>
    ${lanes
      .map(
        (
          lane,
        ) => `<section><strong><span class="switching-dot" style="background:${lane.color}"></span>${esc(lane.name)}</strong>
      ${lane.schedule.length ? `<ul>${lane.schedule.map((b) => `<li>${esc(fmt.format(b.start))} – ${esc(fmt.format(b.end))}: <b>${esc(t(b.on ? "card_on" : "card_off"))}</b></li>`).join("")}</ul>` : `<p>${esc(t("switching_unavailable"))}</p>`}
    </section>`,
      )
      .join("")}
  </details>`;
}
