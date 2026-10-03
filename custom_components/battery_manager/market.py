"""Optional EPEX adapter: published intervals only, never the current scalar."""

from datetime import datetime, timedelta
from math import isfinite
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .core.market import peak_weights
from .core.model import MarketPrice
from .source_health import source_health

CONF_MARKET_ENABLED = "market_price_enabled"
CONF_MARKET_ENTITY = "market_price_entity"
MAX_PRICE_INTERVALS = 400  # two DST-long days of quarter-hours plus margin


def _automatic_series(hass: HomeAssistant, now: datetime) -> dict[str, tuple]:
    return {
        state.entity_id: _read_series(state, now)
        for state in hass.states.async_all("sensor")
        if "epex_spot" in state.entity_id
        and state.attributes.get("unit_of_measurement") == "EUR/MWh"
    }


def price_entity(
    hass: HomeAssistant, config: dict[str, Any], now: datetime | None = None
) -> str | None:
    """Use explicit wiring, or an unambiguous EPEX wholesale series."""
    if not config.get(CONF_MARKET_ENABLED, True):
        return None
    if config.get(CONF_MARKET_ENTITY):
        return str(config[CONF_MARKET_ENTITY])
    candidates = [
        entity
        for entity, (_, status) in _automatic_series(hass, now or dt_util.now()).items()
        if status == "available"
    ]
    return candidates[0] if len(candidates) == 1 else None


def read_prices(
    hass: HomeAssistant, config: dict[str, Any], now: datetime
) -> tuple[tuple[MarketPrice, ...], dict[str, Any]]:
    """Normalize legacy ct/kWh and current EUR/kWh / EUR/MWh schemas.

    Invalid/overlapping series fail closed to the energy-only planner; gaps are
    retained as unknown. Offset-free timestamps cannot identify DST folds.
    """
    automatic = (
        _automatic_series(hass, now)
        if config.get(CONF_MARKET_ENABLED, True) and not config.get(CONF_MARKET_ENTITY)
        else {}
    )
    candidates = [
        entity for entity, (_, status) in automatic.items() if status == "available"
    ]
    entity = (
        str(config[CONF_MARKET_ENTITY])
        if config.get(CONF_MARKET_ENTITY) and config.get(CONF_MARKET_ENABLED, True)
        else candidates[0]
        if len(candidates) == 1
        else None
    )
    diag: dict[str, Any] = {
        "enabled": bool(config.get(CONF_MARKET_ENABLED, True)),
        "entity_id": entity,
        "status": "unavailable",
        "preferred_intervals": [],
        "automatic_candidates": candidates,
        "rejected_sources": [
            {"entity_id": candidate, "status": status}
            for candidate, (_, status) in automatic.items()
            if status != "available"
        ],
    }
    result, status = (
        automatic[entity]
        if entity in automatic
        else _read_series(hass.states.get(entity) if entity else None, now)
    )
    if not entity and automatic:
        statuses = {status for _, status in automatic.values()}
        status = (
            "ambiguous"
            if len(candidates) > 1
            else "invalid"
            if "invalid" in statuses
            else "expired"
            if "expired" in statuses
            else "unavailable"
        )
    diag["status"] = status
    health = source_health(hass, {"market": entity}, now)[0]
    health.update(
        status=status,
        fallback="energy_only" if not result else None,
        boundary="wholesale_spot_price",
        error=status if status != "available" else None,
    )
    diag["source"] = health
    if not result:
        return (), diag
    diag.update(
        coverage_start=result[0].start.isoformat(),
        coverage_end=result[-1].end.isoformat(),
    )
    health.update(
        coverage_start=diag["coverage_start"], coverage_end=diag["coverage_end"]
    )
    diag["preferred_intervals"] = [
        {
            "start": price.start.isoformat(),
            "end": price.end.isoformat(),
            "eur_per_mwh": price.eur_per_mwh,
        }
        for price, weight in zip(result, peak_weights(result), strict=True)
        if weight > 1 and price.end.timestamp() > now.timestamp()
    ]
    return result, diag


def _read_series(state, now: datetime) -> tuple[tuple[MarketPrice, ...], str]:
    """Validate a complete candidate before it participates in discovery."""
    if state is None or state.state in ("unknown", "unavailable"):
        return (), "unavailable"
    data = state.attributes.get("data")
    if not isinstance(data, list) or not data or len(data) > MAX_PRICE_INTERVALS:
        return (), "unavailable"
    prices = []
    try:
        for row in data:
            start = dt_util.parse_datetime(row["start_time"])
            end = dt_util.parse_datetime(row["end_time"])
            if (
                start is None
                or end is None
                or start.tzinfo is None
                or end.tzinfo is None
            ):
                raise ValueError("Missing timezone")
            if "price_eur_per_mwh" in row:
                value = float(row["price_eur_per_mwh"])
            elif "price_ct_per_kwh" in row:
                value = float(row["price_ct_per_kwh"]) * 10
            else:
                value = float(row["price_per_kwh"]) * 1000
            if not isfinite(value) or end.timestamp() - start.timestamp() > 3600:
                raise ValueError("Invalid price or duration")
            prices.append(
                MarketPrice(dt_util.as_local(start), dt_util.as_local(end), value)
            )
        prices.sort(key=lambda price: price.start.timestamp())
        if any(
            a.end.timestamp() > b.start.timestamp()
            for a, b in zip(prices, prices[1:], strict=False)
        ):
            raise ValueError("Overlapping prices")
    except KeyError, TypeError, ValueError, OverflowError:
        return (), "invalid"
    # A retained yesterday series is not a zero-price forecast for tomorrow.
    if (
        prices[-1].end.timestamp() <= now.timestamp()
        or prices[0].start.timestamp() > (now + timedelta(days=3)).timestamp()
    ):
        return (), "expired"
    return tuple(prices), "available"
