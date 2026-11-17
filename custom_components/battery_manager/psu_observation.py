"""Capture present PSU meter evidence without promising future grid charging."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from math import isfinite
from typing import TYPE_CHECKING, Any, Literal

from .core.model import PsuDeliverySnapshot, PsuPowerObservation
from .core.voltage_gate import observed_psu_efficiency, present_delivery_w

if TYPE_CHECKING:
    from .coordinator import BatteryManagerCoordinator

_MAX_AGE = 30
Source = Literal["dc24", "dc48"]


@dataclass
class _Source:
    signature: tuple[str, str | None, float | None]
    generation: int
    since: float


class PsuObservationCapture:
    """Each actor/topology generation needs its own meter publication.

    A HA power state is a point observation, not an interval average. We do
    not create overlapping AC/DC intervals merely because both points are fresh.
    These electrical observations deliberately do not depend on BMS SOC epochs.
    """

    def __init__(self) -> None:
        self._sources: dict[Source, _Source] = {}
        self._snapshots: tuple[PsuDeliverySnapshot, ...] = ()
        self._channel_reasons: dict[Source, dict[str, str]] = {}

    def capture(
        self, owner: BatteryManagerCoordinator, now: datetime
    ) -> tuple[PsuDeliverySnapshot, ...]:
        captured_at = now.timestamp()
        result = []
        sources: tuple[Source, ...] = ("dc24", "dc48")
        for source_id in sources:
            source = source_id
            cfg = owner.raw_config
            switch = cfg.get(f"support_{source}_switch_entity")
            state = owner.hass.states.get(switch) if switch else None
            actor = state.state if state is not None else None
            changed = state.last_changed.timestamp() if state is not None else None
            volts = float(cfg.get(f"psu{source[2:]}_output_voltage_v", 0))
            amps = float(cfg.get(f"psu{source[2:]}_max_current_a", 0))
            eta = float(cfg.get(f"psu{source[2:]}_efficiency", 1))
            fingerprint = hashlib.sha256(
                json.dumps(
                    [
                        source,
                        switch,
                        cfg.get(f"support_{source}_power_entity"),
                        cfg.get(f"support_{source}_output_power_entity"),
                        cfg.get("dcdc_switch_entity"),
                        volts,
                        amps,
                        eta,
                    ]
                ).encode()
            ).hexdigest()
            signature = fingerprint, actor, changed
            previous = self._sources.get(source_id)
            if previous is None or previous.signature != signature:
                previous = _Source(
                    signature,
                    previous.generation + 1 if previous else 0,
                    captured_at,
                )
                self._sources[source_id] = previous
            confirmed = actor == "on" and changed is not None and changed <= captured_at
            since = max(previous.since, changed or 0.0) if confirmed else None
            channels: dict[str, PsuPowerObservation | None] = {}
            reasons: dict[str, str] = {}
            roles: tuple[tuple[Literal["ac_input", "dc_output"], str], ...] = (
                ("ac_input", "power"),
                ("dc_output", "output_power"),
            )
            for role, suffix in roles:
                entity = cfg.get(f"support_{source}_{suffix}_entity")
                meter = owner.hass.states.get(entity) if entity else None
                reason = "missing"
                observation = None
                if meter is not None:
                    published = meter.last_reported.timestamp()
                    try:
                        value = float(meter.state)
                    except TypeError, ValueError:
                        value = float("nan")
                    unit = meter.attributes.get("unit_of_measurement")
                    watts = value * (1000 if unit == "kW" else 1)
                    limit = volts * amps if volts > 0 and amps > 0 else None
                    if role == "ac_input" and limit is not None and 0 < eta <= 1:
                        limit /= eta
                    if (
                        not isfinite(watts)
                        or not 0 <= watts <= 100000
                        or unit not in ("W", "kW")
                    ):
                        reason = "incompatible"
                    elif limit is not None and watts > limit:
                        reason = "hardware_limit"
                    elif not 0 <= captured_at - published <= _MAX_AGE:
                        reason = "stale"
                    elif since is None:
                        reason = "unconfirmed"
                    elif published < since:
                        reason = "previous_source_generation"
                    else:
                        reason = "measured"
                        observation = PsuPowerObservation(
                            source_id=str(entity),
                            role=role,
                            power_w=watts,
                            published_at=published,
                            interval_start=published,
                            interval_end=published,
                            source_generation=previous.generation,
                            topology_fingerprint=fingerprint,
                        )
                channels[role] = observation
                reasons[role] = reason
            quality: Literal[
                "unknown", "measured", "stale", "unconfirmed", "incompatible"
            ] = (
                "unconfirmed"
                if not confirmed
                else "measured"
                if channels["dc_output"] is not None
                else "stale"
                if reasons["dc_output"] == "stale"
                else "incompatible"
                if reasons["dc_output"] in ("hardware_limit", "incompatible")
                else "unknown"
            )
            result.append(
                PsuDeliverySnapshot(
                    captured_at=captured_at,
                    source_generation=previous.generation,
                    topology_fingerprint=fingerprint,
                    confirmed_on=confirmed,
                    confirmed_on_since=since,
                    dc_output=channels["dc_output"],
                    ac_input=channels["ac_input"],
                    quality=quality,
                    source_id=source_id,
                )
            )
            self._channel_reasons[source_id] = reasons
        self._snapshots = tuple(result)
        return self._snapshots

    def diagnostic(self) -> list[dict[str, Any]]:
        return [
            {
                "source_id": snapshot.source_id,
                "quality": snapshot.quality,
                "confirmed_on": snapshot.confirmed_on,
                "source_generation": snapshot.source_generation,
                "captured_at": snapshot.captured_at,
                "dc_output_w": snapshot.dc_output.power_w
                if snapshot.dc_output
                else None,
                "ac_input_w": snapshot.ac_input.power_w if snapshot.ac_input else None,
                "dc_published_at": snapshot.dc_output.published_at
                if snapshot.dc_output
                else None,
                "ac_published_at": snapshot.ac_input.published_at
                if snapshot.ac_input
                else None,
                "present_delivery_w": present_delivery_w(
                    snapshot,
                    snapshot.captured_at,
                    snapshot.source_generation,
                    snapshot.topology_fingerprint,
                ),
                "efficiency": observed_psu_efficiency(snapshot),
                "evidence_scope": "present_only",
                "channel_reasons": self._channel_reasons[snapshot.source_id],
            }
            for snapshot in self._snapshots
        ]

    def private_diagnostic(self) -> list[dict[str, Any]]:
        return [asdict(snapshot) for snapshot in self._snapshots]
