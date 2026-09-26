/** Calendar labels always use the configured HA zone, including accessibility. */
export function dateTimeFormat(hass, options) {
  return new Intl.DateTimeFormat(hass?.language || "en", {
    timeZone: hass?.config?.time_zone || "UTC",
    ...options,
  });
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
