"""SOC forecast projection of a voltage-controlled 48 V support source."""

from .model import PsuDeliverySnapshot, SupportParams, VoltageGateBand


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
) -> bool:
    """Project relay permission using voltage or its learned switching points."""
    if at < support.psu48_live_until:
        allowed, _ = voltage_request(
            support.psu48_bus_voltage_v,
            active,
            support.psu48_on_voltage_v,
            support.psu48_off_voltage_v,
        )
        return allowed
    band = gate_band(support, bus_w / support.psu48_output_voltage_v, at)
    on_soc = band.on_soc if band is not None else None
    if on_soc is None:
        on_soc = support.gate_soc_percent
    allowed = active or (on_soc is not None and soc <= on_soc)
    if active and band is not None and band.off_soc is not None and soc >= band.off_soc:
        allowed = False
    return allowed


# Matches the runtime physical-sample hold; not a forecast validity horizon.
PSU_MEASUREMENT_MAX_AGE_S = 30.0


def present_delivery_w(
    snapshot: PsuDeliverySnapshot,
    at: float,
    source_generation: int,
    topology_fingerprint: str,
) -> float | None:
    """Accept only a fresh DC observation from this confirmed source episode."""
    observation = snapshot.dc_output
    if (
        snapshot.quality != "measured"
        or not snapshot.confirmed_on
        or snapshot.confirmed_on_since is None
        or snapshot.source_generation != source_generation
        or snapshot.topology_fingerprint != topology_fingerprint
        or not 0 <= at - snapshot.captured_at <= PSU_MEASUREMENT_MAX_AGE_S
        or observation is None
        or observation.source_generation != source_generation
        or observation.topology_fingerprint != topology_fingerprint
        or not snapshot.confirmed_on_since <= observation.interval_start
        or not 0 <= at - observation.published_at <= PSU_MEASUREMENT_MAX_AGE_S
        or observation.published_at > snapshot.captured_at
        or not 0 <= at - observation.interval_end <= PSU_MEASUREMENT_MAX_AGE_S
    ):
        return None
    return observation.power_w


def observed_psu_efficiency(snapshot: PsuDeliverySnapshot) -> float | None:
    """An electrical balance requires covered, overlapping compatible channels."""
    dc, ac = snapshot.dc_output, snapshot.ac_input
    if (
        present_delivery_w(
            snapshot,
            snapshot.captured_at,
            snapshot.source_generation,
            snapshot.topology_fingerprint,
        )
        is None
        or dc is None
        or ac is None
        or ac.source_generation != snapshot.source_generation
        or ac.topology_fingerprint != snapshot.topology_fingerprint
        or snapshot.confirmed_on_since is None
        or ac.interval_start < snapshot.confirmed_on_since
        or not 0 <= snapshot.captured_at - ac.published_at <= PSU_MEASUREMENT_MAX_AGE_S
        or not 0 <= snapshot.captured_at - ac.interval_end <= PSU_MEASUREMENT_MAX_AGE_S
        or max(dc.interval_start, ac.interval_start)
        >= min(dc.interval_end, ac.interval_end)
        or ac.power_w <= 0
        or dc.power_w > ac.power_w
    ):
        return None
    return dc.power_w / ac.power_w
