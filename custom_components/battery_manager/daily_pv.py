"""Keep rolling HA forecast roles tied to their publication date."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from math import isfinite
from typing import TYPE_CHECKING

from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from .const import MAX_HISTORICAL_FORECAST_AGE_HOURS

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


_Source = tuple[str, ...]
_DAY_SUFFIXES = (("_today",), ("_tomorrow",), ("_day_after_tomorrow", "_day_after"))


def _source(registry: er.EntityRegistry, entity: str, offset: int) -> _Source:
    """Cross roles only for matching provider, installation and forecast series."""
    entry = registry.async_get(entity) if entity else None
    if entry is None:
        return (entity,)
    for suffix in _DAY_SUFFIXES[offset]:
        if entry.unique_id.endswith(suffix):
            family = entry.unique_id.removesuffix(suffix)
            if family and (entry.config_entry_id or entry.device_id):
                return (
                    entry.platform,
                    entry.config_entry_id or "",
                    entry.device_id or "",
                    family,
                )
    return (entry.id,)


class DailyPVCache:
    """Volatile per-source evidence. Reading an old state never renews it."""

    def __init__(self) -> None:
        self._bindings: tuple[str, ...] = ()
        self._readings: dict[tuple[date, _Source], tuple[float, datetime]] = {}

    def read(
        self, hass: HomeAssistant, bindings: tuple[str, ...], now: datetime
    ) -> list[float | None] | None:
        if bindings != self._bindings:
            self._readings.clear()
            self._bindings = bindings
        today = dt_util.as_local(now).date()
        instant = dt_util.as_utc(now)
        cutoff = instant - timedelta(hours=MAX_HISTORICAL_FORECAST_AGE_HOURS)
        self._readings = {
            key: reading
            for key, reading in self._readings.items()
            if key[0] >= today and cutoff <= reading[1] <= instant
        }
        registry = er.async_get(hass)
        sources = []
        for offset, entity in enumerate(bindings):
            source = _source(registry, entity, offset)
            sources.append(source)
            state = hass.states.get(entity) if entity else None
            if state is None or state.attributes.get("restored"):
                continue
            published = state.last_reported
            if not cutoff <= published <= instant:
                continue
            target = dt_util.as_local(published).date() + timedelta(days=offset)
            try:
                value = float(state.state)
            except ValueError:
                continue
            previous = self._readings.get((target, source))
            if (
                isfinite(value)
                and target >= today
                and (previous is None or published >= previous[1])
            ):
                self._readings[target, source] = max(0.0, value), published
        totals = [
            reading[0]
            if (reading := self._readings.get((today + timedelta(days=i), source)))
            else None
            for i, source in enumerate(sources)
        ]
        return totals if totals[0] is not None else None
