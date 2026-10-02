"""Optional EPEX adapter: published intervals only, never the current scalar."""

from datetime import datetime, timedelta
from math import isfinite
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .core.market import peak_weights
from .core.model import MarketPrice

CONF_MARKET_ENABLED = "market_price_enabled"
CONF_MARKET_ENTITY = "market_price_entity"
MAX_PRICE_INTERVALS = 400  # two DST-long days of quarter-hours plus margin


def price_entity(hass: HomeAssistant, config: dict[str, Any]) -> str | None:
    """Use explicit wiring, or an unambiguous EPEX wholesale series."""
    if not config.get(CONF_MARKET_ENABLED, True):
        return None
    if config.get(CONF_MARKET_ENTITY):
        return str(config[CONF_MARKET_ENTITY])
    candidates = [
        state.entity_id
        for state in hass.states.async_all("sensor")
        if "epex_spot" in state.entity_id
        and state.attributes.get("unit_of_measurement") == "EUR/MWh"
    ]
    return candidates[0] if len(candidates) == 1 else None


def read_prices(
    hass: HomeAssistant, config: dict[str, Any], now: datetime
) -> tuple[tuple[MarketPrice, ...], dict[str, Any]]:
    """Normalize legacy ct/kWh and current EUR/kWh / EUR/MWh schemas.

    Invalid/overlapping series fail closed to the energy-only planner; gaps are
    retained as unknown. Offset-free timestamps cannot identify DST folds.
    """
    entity = price_entity(hass, config)
    diag: dict[str, Any] = {
        "enabled": bool(config.get(CONF_MARKET_ENABLED, True)),
        "entity_id": entity,
        "status": "unavailable",
        "preferred_intervals": [],
    }
    state = hass.states.get(entity) if entity else None
    if state is None or state.state in ("unknown", "unavailable"):
        return (), diag
    data = state.attributes.get("data")
    if not isinstance(data, list) or not data or len(data) > MAX_PRICE_INTERVALS:
        return (), diag
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
        diag["status"] = "invalid"
        return (), diag
    # A retained yesterday series is not a zero-price forecast for tomorrow.
    if (
        prices[-1].end.timestamp() <= now.timestamp()
        or prices[0].start.timestamp() > (now + timedelta(days=3)).timestamp()
    ):
        diag["status"] = "expired"
        return (), diag
    result = tuple(prices)
    diag.update(
        status="available",
        coverage_start=result[0].start.isoformat(),
        coverage_end=result[-1].end.isoformat(),
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
