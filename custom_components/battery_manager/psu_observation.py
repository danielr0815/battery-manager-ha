"""Capture present PSU measurements and qualify observed 48 V ON delivery."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from math import isfinite
from typing import TYPE_CHECKING, Any, Literal

from homeassistant.const import ATTR_ASSUMED_STATE, ATTR_RESTORED, EVENT_STATE_REPORTED
from homeassistant.core import Event, State, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_DCDC_SWITCH,
    CONF_FEEDIN_BATTERY_POWER_ENTITY,
    CONF_INVERTER_BLOCK_SWITCH,
    CONF_INVERTER_LIMIT_ENTITY,
    CONF_SUPPORT_DC24_OUTPUT_POWER_ENTITY,
    CONF_SUPPORT_DC24_POWER_ENTITY,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_OUTPUT_POWER_ENTITY,
    CONF_SUPPORT_DC48_POWER_ENTITY,
    CONF_SUPPORT_DC48_SWITCH,
    LIVE_AC_POWER_KEYS,
    OPERATION_POWER_SOURCES,
)
from .core.model import PsuDeliverySnapshot, PsuPowerObservation
from .core.psu_expectation import MAX_PROJECTION_SECONDS, MIN_OBSERVED_SECONDS
from .core.voltage_gate import observed_psu_efficiency, present_delivery_w

if TYPE_CHECKING:
    from .coordinator import BatteryManagerCoordinator

_MAX_AGE = 30
Source = Literal["dc24", "dc48"]
_ON_ACTORS = {
    CONF_SUPPORT_DC24_SWITCH: "on",
    CONF_SUPPORT_DC48_SWITCH: "on",
    CONF_DCDC_SWITCH: "off",
    CONF_INVERTER_LIMIT_ENTITY: "0",
    CONF_INVERTER_BLOCK_SWITCH: "on",
}
_ON_REQUIRED_ACTORS = (
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_SWITCH,
    CONF_DCDC_SWITCH,
    CONF_INVERTER_LIMIT_ENTITY,
)
_OTHER_POWER_ROLES = (
    CONF_SUPPORT_DC24_POWER_ENTITY,
    CONF_SUPPORT_DC24_OUTPUT_POWER_ENTITY,
    CONF_SUPPORT_DC48_POWER_ENTITY,
    CONF_FEEDIN_BATTERY_POWER_ENTITY,
    *LIVE_AC_POWER_KEYS,
    *OPERATION_POWER_SOURCES,
)


@dataclass
class _Source:
    signature: tuple[str, str | None, float | None]
    generation: int
    since: float


@dataclass(frozen=True)
class _OnTopology:
    meter: str
    actors: tuple[tuple[str, str], ...]
    ac_meter: str | None
    volts: float
    amps: float
    efficiency: float

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> _OnTopology | None:
        meter = config.get(CONF_SUPPORT_DC48_OUTPUT_POWER_ENTITY)
        if not meter or not all(config.get(key) for key in _ON_REQUIRED_ACTORS):
            return None
        meter = meter.lower()
        actors = tuple(
            (entity.lower(), expected)
            for key, expected in _ON_ACTORS.items()
            if (entity := config.get(key))
        )
        actor_entities = {entity for entity, _ in actors}
        # An entity shared with another electrical boundary cannot prove
        # independent delivery from this PSU.
        if len(actor_entities) != len(actors) or meter in actor_entities:
            return None
        if any(
            entity.lower() == meter
            for key in _OTHER_POWER_ROLES
            if (entity := config.get(key))
        ):
            return None
        volts = float(config.get("psu48_output_voltage_v", 0))
        amps = float(config.get("psu48_max_current_a", 0))
        limit = volts * amps
        if not (volts > 0 and amps > 0 and limit > 0 and isfinite(limit)):
            return None
        return cls(
            meter,
            actors,
            config.get(CONF_SUPPORT_DC48_POWER_ENTITY),
            volts,
            amps,
            float(config.get("psu48_efficiency", 1)),
        )


@dataclass
class _OnEpisode:
    actor_generation: tuple[float, ...]
    first_at: float
    last_at: float
    last_w: float
    minimum_w: float


def _unconfirmed(state: State) -> bool:
    return bool(
        state.attributes.get(ATTR_ASSUMED_STATE) or state.attributes.get(ATTR_RESTORED)
    )


def _meter_power(state: State, limit: float | None) -> tuple[float, str | None]:
    try:
        value = float(state.state)
    except TypeError, ValueError:
        value = float("nan")
    unit = state.attributes.get("unit_of_measurement")
    watts = value * (1000 if unit == "kW" else 1)
    if not isfinite(watts) or not 0 <= watts <= 100000 or unit not in ("W", "kW"):
        return watts, "incompatible"
    if limit is not None and watts > limit:
        return watts, "hardware_limit"
    return watts, None


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
        self._on_topology: _OnTopology | None = None
        self._on_unsub: list[Callable[[], None]] = []
        self._on_subscribed_at = 0.0
        self._on_episode: _OnEpisode | None = None
        self._on_expected: tuple[float, float, float] | None = None

    def start(self, owner: BatteryManagerCoordinator) -> None:
        topology = _OnTopology.from_config(owner.raw_config)
        if topology == self._on_topology:
            return
        self.stop()
        if topology is None:
            return
        self._on_topology = topology
        self._on_subscribed_at = dt_util.utcnow().timestamp()
        actors = tuple(entity for entity, _ in topology.actors)

        @callback
        def actor_changed(event: Event[Any]) -> None:
            if topology is not self._on_topology:
                return
            before = event.data["old_state"]
            after = event.data["new_state"]
            if (
                before is None
                or after is None
                or before.state != after.state
                or before.last_changed != after.last_changed
                or _unconfirmed(before) != _unconfirmed(after)
            ):
                self._invalidate_on()

        @callback
        def output_published(event: Event[Any]) -> None:
            if topology is self._on_topology:
                self._capture_on_publication(owner, topology, event)

        self._on_unsub = [
            async_track_state_change_event(owner.hass, actors, actor_changed),
            async_track_state_change_event(
                owner.hass, topology.meter, output_published
            ),
            async_track_state_report_event(
                owner.hass, topology.meter, output_published
            ),
        ]

    def stop(self) -> None:
        for unsubscribe in self._on_unsub:
            unsubscribe()
        self._on_unsub.clear()
        self._on_topology = None
        self._invalidate_on()

    def _invalidate_on(self) -> None:
        self._on_episode = None
        self._on_expected = None

    @staticmethod
    def _on_generation(
        owner: BatteryManagerCoordinator, topology: _OnTopology
    ) -> tuple[float, ...] | None:
        generation = []
        for entity, expected in topology.actors:
            state = owner.hass.states.get(entity)
            if state is None or _unconfirmed(state):
                return None
            if expected == "0":
                try:
                    blocked = float(state.state) == 0
                except ValueError:
                    blocked = False
                if not blocked:
                    return None
            elif state.state != expected:
                return None
            generation.append(state.last_changed.timestamp())
        return tuple(generation)

    @staticmethod
    def _on_reading(state: State | None, topology: _OnTopology) -> float | None:
        if state is None or _unconfirmed(state):
            return None
        watts, reason = _meter_power(state, topology.volts * topology.amps)
        return watts if reason is None else None

    def _capture_on_publication(
        self, owner: BatteryManagerCoordinator, topology: _OnTopology, event: Event[Any]
    ) -> None:
        self._on_expected = None
        state = event.data["new_state"]
        at = event.time_fired.timestamp()
        now = dt_util.utcnow().timestamp()
        generation = self._on_generation(owner, topology)
        watts = self._on_reading(state, topology)
        # HA keeps an immutable publication time in a repeated-value event.
        # Its shared State.last_reported may already reflect a later report.
        published = (
            event.data["last_reported"]
            if event.event_type == EVENT_STATE_REPORTED
            else state.last_updated
            if state is not None
            else None
        )
        if (
            topology != _OnTopology.from_config(owner.raw_config)
            or generation is None
            or watts is None
            or published is None
            or published.timestamp() != at
            or not 0 <= now - at <= _MAX_AGE
            or at <= max(self._on_subscribed_at, *generation)
        ):
            self._invalidate_on()
            return
        episode = self._on_episode
        if (
            episode is None
            or episode.actor_generation != generation
            or at - episode.last_at > _MAX_AGE
        ):
            self._on_episode = _OnEpisode(generation, at, at, watts, watts)
        elif at < episode.last_at or (
            at == episode.last_at and watts != episode.last_w
        ):
            self._invalidate_on()
        elif at > episode.last_at:
            episode.last_at = at
            episode.last_w = watts
            episode.minimum_w = min(episode.minimum_w, watts)

    def _validate_on_capture(self, owner: BatteryManagerCoordinator, at: float) -> None:
        self._on_expected = None
        episode = self._on_episode
        topology = self._on_topology
        if episode is None or topology is None:
            return
        meter = owner.hass.states.get(topology.meter)
        watts = self._on_reading(meter, topology)
        published = meter.last_reported.timestamp() if meter is not None else None
        if (
            topology != _OnTopology.from_config(owner.raw_config)
            or episode.actor_generation != self._on_generation(owner, topology)
            or watts is None
            or published is None
            or not 0 <= at - episode.last_at <= _MAX_AGE
            or not episode.last_at <= published <= at
        ):
            self._invalidate_on()
            return
        if published > episode.last_at:
            return  # A pending event must confirm the latest value before use.
        if watts != episode.last_w:
            self._invalidate_on()
            return
        duration = min(episode.last_at - episode.first_at, MAX_PROJECTION_SECONDS)
        if duration >= MIN_OBSERVED_SECONDS:
            self._on_expected = at, episode.minimum_w, duration

    def expected48(self, now: datetime) -> tuple[float, float] | None:
        """Return the lower watts and covered seconds validated for this capture."""
        validated = self._on_expected
        if validated is None or validated[0] != now.timestamp():
            return None
        return validated[1], validated[2]

    def expected48_diagnostic(self, now: datetime) -> dict[str, Any]:
        expected = self.expected48(now)
        return {
            "status": "qualified" if expected is not None else "unqualified",
            "minimum_w": expected[0] if expected is not None else None,
            "observed_seconds": expected[1] if expected is not None else None,
        }

    def capture(
        self, owner: BatteryManagerCoordinator, now: datetime
    ) -> tuple[PsuDeliverySnapshot, ...]:
        captured_at = now.timestamp()
        self._validate_on_capture(owner, captured_at)
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
                    limit = volts * amps if volts > 0 and amps > 0 else None
                    if role == "ac_input" and limit is not None and 0 < eta <= 1:
                        limit /= eta
                    watts, invalid = _meter_power(meter, limit)
                    if invalid is not None:
                        reason = invalid
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
