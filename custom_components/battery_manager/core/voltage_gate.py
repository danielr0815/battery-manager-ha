"""SOC forecast projection of a voltage-controlled 48 V support source."""

from .model import SupportParams, VoltageGateBand


def gate_band(
    support: SupportParams, current_a: float, at: float
) -> VoltageGateBand | None:
    """No extrapolation beyond the learned current range or evidence lifetime."""
    return next(
        (
            band
            for band in support.psu48_gate_bands
            if band.current_min_a <= current_a < band.current_max_a
            and at <= band.valid_until
        ),
        None,
    )


def voltage_request(
    voltage: float | None, active: bool, on_v: float, off_v: float
) -> tuple[bool, str]:
    """A missing measurement retains protective readiness, not power credit."""
    if voltage is None:
        return True, "voltage_unavailable"
    if voltage >= off_v:
        return False, "above_off_voltage"
    if voltage <= on_v:
        return True, "below_on_voltage"
    return active, "hysteresis_hold"


def forecast_gate(
    support: SupportParams, soc: float, bus_w: float, at: float, active: bool
) -> tuple[bool, float | None]:
    """Return switch permission and conservative nominal delivery voltage.

    A learned gate predicts direct load coverage only. It never fabricates
    full current or grid charging from the fact that a threshold was crossed.
    The caller suppresses all unmeasured credit in stress scenarios.
    """
    if at < support.psu48_live_until:
        allowed, _ = voltage_request(
            support.psu48_bus_voltage_v,
            active,
            support.psu48_on_voltage_v,
            support.psu48_off_voltage_v,
        )
        return allowed, support.psu48_bus_voltage_v
    band = gate_band(support, bus_w / support.psu48_output_voltage_v, at)
    on_soc = band.on_soc if band is not None else None
    if on_soc is None:
        on_soc = support.gate_soc_percent
    allowed = active or (on_soc is not None and soc <= on_soc)
    if active and band is not None and band.off_soc is not None and soc >= band.off_soc:
        allowed = False
    voltage = (
        support.psu48_output_voltage_v
        if band is not None
        and band.delivery_soc is not None
        and soc <= band.delivery_soc
        else None
    )
    return allowed, voltage
