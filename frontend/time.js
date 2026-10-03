/** Calendar labels always use the configured HA zone, including accessibility. */
export function dateTimeFormat(hass, options) {
  return new Intl.DateTimeFormat(hass?.language || "en", {
    timeZone: hass?.config?.time_zone || "UTC",
    ...options,
  });
}
/** Add an offset only where two real instants share the same local hour. */
export function selectionTime(hass, time, times) {
  const wall = dateTimeFormat(hass, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  });
  const label = wall.format(time);
  const ambiguous = times.some(
    (other) => other !== time && wall.format(other) === label,
  );
  return dateTimeFormat(hass, {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    ...(ambiguous ? { timeZoneName: "shortOffset" } : {}),
  }).format(time);
}
export function localHour(hass, time) {
  return Number(
    new Intl.DateTimeFormat("en-GB", {
      timeZone: hass?.config?.time_zone || "UTC",
      hour: "2-digit",
      hourCycle: "h23",
    }).format(time),
  );
}
export function nextHour(hass, time) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: hass?.config?.time_zone || "UTC",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(time);
  const part = (key) => Number(parts.find((item) => item.type === key).value);
  return (
    time -
    part("minute") * 60000 -
    part("second") * 1000 -
    (time % 1000) +
    3600000
  );
}
