"""Observe battery terminal discharge in sustained, confirmed support mode."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import cache, lru_cache
from math import ceil, cos, floor, isclose, radians
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from astral import Observer
from astral.sun import adjust_to_horizon, dawn, elevation, refraction_at_zenith
from homeassistant.const import EVENT_STATE_REPORTED
from homeassistant.core import Event, State, callback
from homeassistant.helpers.event import (
    async_track_state_change_event,
    async_track_state_report_event,
)
from homeassistant.util import dt as dt_util

from .const import (
    CONF_AC_BALANCE_IN,
    CONF_AC_BALANCE_OUT,
    CONF_AC_LOAD_ENTITY,
    CONF_BATTERY_VOLTAGE_ENTITY,
    CONF_DC_BALANCE_IN,
    CONF_DC_BALANCE_OUT,
    CONF_DC_LOAD_ENTITY,
    CONF_FEEDIN_BATTERY_POWER_ENTITY,
    CONF_FEEDIN_SETPOINT_ENTITY,
    CONF_LOAD_INPUT_POWER_ENTITY,
    CONF_LOAD_OUTPUT_POWER_ENTITY,
    CONF_LOAD_POWER_ENTITY,
    CONF_PSU48_OUTPUT_VOLTAGE_V,
    CONF_RESERVE_GRID_ENTITY,
    CONF_SOC_ENTITY,
    CONF_SOC_EXPECTATION_GRID_POWER_ENTITY,
    CONF_SOC_EXPECTATION_GRID_SETPOINT_ENTITY,
    CONF_SUPPORT_DC48_OUTPUT_POWER_ENTITY,
    LIVE_AC_POWER_KEYS,
)
from .core.psu_expectation import (
    GRID_IMPORT_MARGIN_W,
    MAX_PROJECTION_SECONDS,
    MIN_OBSERVED_SECONDS,
)
from .psu_observation import (
    _MAX_AGE,
    _ON_ACTORS,
    _ON_REQUIRED_ACTORS,
    _OTHER_POWER_ROLES,
    _unconfirmed,
    actor_generation,
)

if TYPE_CHECKING:
    from .coordinator import BatteryManagerCoordinator
    from .soc_consistency import SocConsistencyMonitor

_PV_POWER = "operation_pv_power_entity"
_AC_GRID, _AC_INPUT, _AC_OUTPUT = LIVE_AC_POWER_KEYS
_GRID_POWER = CONF_SOC_EXPECTATION_GRID_POWER_ENTITY
_GRID_SETPOINT = CONF_SOC_EXPECTATION_GRID_SETPOINT_ENTITY
# The Victron number reports about every 30 s (30.001180 s observed).
# Allow one missed report and jitter without relaxing measured telemetry.
_GRID_SETPOINT_MAX_AGE = 65
BATTERY_COORDINATE_KEYS = (
    CONF_SOC_ENTITY,
    CONF_FEEDIN_BATTERY_POWER_ENTITY,
    CONF_BATTERY_VOLTAGE_ENTITY,
)
_OPTIONAL_INPUTS = (_PV_POWER, _AC_INPUT, _AC_OUTPUT, CONF_RESERVE_GRID_ENTITY)
_GRID_SHARED_INPUTS = (*BATTERY_COORDINATE_KEYS, CONF_RESERVE_GRID_ENTITY)
_GRID_SOURCES = {"on": 1, "ac_input_1": 2, "ac_input_2": 3}
_BALANCE_KEYS = (
    CONF_AC_BALANCE_IN,
    CONF_AC_BALANCE_OUT,
    CONF_DC_BALANCE_IN,
    CONF_DC_BALANCE_OUT,
)
# Night without a measured PV channel: even twilight is excluded.
_NIGHT_DEPRESSION = 18.0
# Protect the transition itself against small differences between Astral's
# crossing and elevation calculations. Later, require independent darkness
# again. At polar latitudes an isolated dawn can lack a corresponding dusk.
_DAWN_GUARD_SECONDS = 300
# Victron's integer-watt reports can move one count around zero. A wider
# variation would mix distinct load conditions into the same projection.
_ZERO_NOISE_W = 1.0
_MAX_STABLE_SPREAD_W = 10.0
_MAX_PENDING_INTERVALS = 4096
_MAX_SKY_CHECKS = 2048
# Relative floating-point roundoff from comparing reported W with kW × 1000.
# No absolute allowance: a zero reading cannot conceal nonzero power.
_AC_ROUNDOFF_REL = 1e-15
_Site = tuple[float, float, float]


@lru_cache(maxsize=16)
def _dawn_events(site: _Site, day: date) -> tuple[datetime, ...]:
    observer = Observer(*site)
    events = []
    # Astral evaluates at the supplied UTC date and can return an event on
    # a neighboring date for longitudes near the date line. Include enough
    # days to find both an immediately elapsed dawn and one within the horizon.
    for days in range(-2, 4):
        try:
            events.append(
                dawn(
                    observer,
                    date=day + timedelta(days=days),
                    depression=_NIGHT_DEPRESSION,
                    tzinfo=UTC,
                )
            )
        except ValueError:
            continue  # No crossing on this date at this latitude.
    return tuple(sorted(events))


def _night_until(site: _Site, now: datetime) -> float | None:
    """Always prove current darkness independently of scheduled twilight."""
    now = now.astimezone(UTC)
    at = now.timestamp()
    if _dark_margin(site, floor(at)) <= _solar_speed(site):
        return None
    events = _dawn_events(site, now.date())
    if any(0 <= (now - when).total_seconds() <= _DAWN_GUARD_SECONDS for when in events):
        return None
    limit = at + MAX_PROJECTION_SECONDS
    next_dawn = next((when for when in events if when > now), None)
    return min(limit, next_dawn.timestamp()) if next_dawn is not None else limit


@lru_cache(maxsize=4096)
def _dark_margin(site: _Site, second: int) -> float:
    latitude, _, altitude = site
    depression = 90 + _NIGHT_DEPRESSION + adjust_to_horizon(altitude)
    threshold = 90 - depression - refraction_at_zenith(depression)
    # Astral clips its elevation model at ±89.8°. Moving the observer by
    # that angular difference can raise the actual elevation by the same amount.
    threshold -= max(0.0, abs(latitude) - 89.8)
    return threshold - elevation(
        Observer(*site), dt_util.utc_from_timestamp(second), with_refraction=False
    )


def _solar_speed(site: _Site) -> float:
    # Earth rotates under the observer at less than 16°/h, projected onto
    # the observer's parallel. The extra 0.1°/h bounds the Sun's own drift.
    return (16 * cos(radians(min(abs(site[0]), 89.8))) + 0.1) / 3600


def _certified_dark_until(site: _Site, start: float, limit: float) -> float:
    """Stop at the first interval whose darkness cannot be established."""
    speed = _solar_speed(site)
    # Astral's elevation uses whole seconds. Certify the enclosing interval.
    first, last = floor(start), ceil(limit)
    if (
        min(_dark_margin(site, first), _dark_margin(site, last))
        > speed * (last - first) / 2
    ):
        return limit

    @cache
    def margin(second: int) -> float:
        return _dark_margin(site, second)

    def covered(left: int, right: int) -> int:
        if margin.cache_info().currsize >= _MAX_SKY_CHECKS:
            return left
        extent = right - left
        # Any time in the interval is at most half its length from
        # an endpoint. Subdivide only where that bound cannot prove darkness.
        if min(margin(left), margin(right)) > speed * extent / 2:
            return right
        if extent <= 1:
            return left
        middle = (left + right) // 2
        through = covered(left, middle)
        return covered(middle, right) if through == middle else through

    return min(limit, max(start, covered(first, last)))


class NetDischargeDiagnostic(TypedDict, total=False):
    reason: str
    observed_seconds: float
    net_discharge_w: float
    valid_until: float
    evidence_mode: Literal["grid_import"]
    grid_import_w: float
    grid_setpoint_w: float
    grid_margin_w: float


@dataclass(frozen=True)
class _GridTopology:
    power: str
    setpoint: str
    shared_inputs: tuple[tuple[str, str], ...]
    power_input: str | None


@dataclass(frozen=True)
class _Topology:
    actors: tuple[tuple[str, str], ...]
    inputs: tuple[tuple[str, str], ...]
    threshold_v: float
    site: _Site | None
    grid: _GridTopology | str | None = field(default=None, compare=False)

    @classmethod
    def from_owner(cls, owner: BatteryManagerCoordinator) -> _Topology | str:
        cfg = owner.raw_config
        if cfg.get(CONF_SUPPORT_DC48_OUTPUT_POWER_ENTITY):
            return "net_direct_meter_configured"
        if not all(
            cfg.get(key) for key in (*_ON_REQUIRED_ACTORS, *BATTERY_COORDINATE_KEYS)
        ):
            return "net_topology_unavailable"
        actors = tuple(
            (entity.lower(), expected)
            for key, expected in _ON_ACTORS.items()
            if (entity := cfg.get(key))
        )
        inputs = tuple(
            (key, entity.lower())
            for key in (*BATTERY_COORDINATE_KEYS, *_OPTIONAL_INPUTS)
            if (entity := cfg.get(key))
        )
        pv_entity = next((entity for key, entity in inputs if key == _PV_POWER), None)
        ac_ports = {entity for key, entity in inputs if key in (_AC_INPUT, _AC_OUTPUT)}
        all_entities = tuple(entity for entity, _ in actors) + tuple(
            entity for _, entity in inputs
        )
        other_roles = {
            key: entity.lower()
            for key in _OTHER_POWER_ROLES
            if key not in (CONF_FEEDIN_BATTERY_POWER_ENTITY, *_OPTIONAL_INPUTS)
            and (entity := cfg.get(key))
        }
        load_entities = {
            entity.lower()
            for key in (CONF_AC_LOAD_ENTITY, CONF_DC_LOAD_ENTITY)
            if (entity := cfg.get(key))
        }
        if entry := getattr(owner, "entry", None):
            # Subentries use the same power binding for both appliance and
            # surplus loads. The other two roles are cascade device ports.
            load_entities.update(
                entity.lower()
                for subentry in entry.subentries.values()
                for key in (
                    CONF_LOAD_POWER_ENTITY,
                    CONF_LOAD_INPUT_POWER_ENTITY,
                    CONF_LOAD_OUTPUT_POWER_ENTITY,
                )
                if (entity := subentry.data.get(key))
            )
        other_entities = set(other_roles.values()) | load_entities
        balance_entities = {
            key: {entity.lower() for entity in (cfg.get(key) or ())}
            for key in _BALANCE_KEYS
        }
        if ac_ports.intersection(
            balance_entities[CONF_AC_BALANCE_IN], balance_entities[CONF_AC_BALANCE_OUT]
        ):
            return "net_topology_unavailable"
        for key, entities in balance_entities.items():
            # AC port measurements can be terms at either side of an AC
            # consumption boundary. A positive PV measurement can contribute
            # as AC generation. The other measurement roles are independent.
            compatible = (
                ac_ports if key in (CONF_AC_BALANCE_IN, CONF_AC_BALANCE_OUT) else set()
            )
            if key == CONF_AC_BALANCE_IN and pv_entity is not None:
                compatible = compatible | {pv_entity}
            other_entities.update(entities - compatible)
        if len(set(all_entities)) != len(all_entities) or other_entities.intersection(
            all_entities
        ):
            return "net_topology_unavailable"
        threshold = float(cfg.get(CONF_PSU48_OUTPUT_VOLTAGE_V, 0))
        if not 40 <= threshold <= 60:
            return "net_topology_unavailable"
        site = None
        if not cfg.get(_PV_POWER):
            ha_config = owner.hass.config
            latitude, longitude = ha_config.latitude, ha_config.longitude
            if not (-90 <= latitude <= 90 and -180 <= longitude <= 180):
                return "net_topology_unavailable"
            # The same meridian must select the same Astral date and events.
            site = (
                latitude,
                -180.0 if longitude == 180 else longitude,
                float(ha_config.elevation),
            )
        grid = _grid_topology(
            cfg, actors, inputs, other_roles, load_entities, balance_entities
        )
        return cls(actors, inputs, threshold, site, grid)


def _grid_topology(
    cfg: dict[str, Any],
    actors: tuple[tuple[str, str], ...],
    inputs: tuple[tuple[str, str], ...],
    other_roles: dict[str, str],
    load_entities: set[str],
    balance_entities: dict[str, set[str]],
) -> _GridTopology | str | None:
    power = cfg.get(_GRID_POWER)
    setpoint = cfg.get(_GRID_SETPOINT)
    if not power and not setpoint:
        return None
    if not power:
        return "net_grid_power_unavailable"
    if not setpoint:
        return "net_grid_setpoint_unavailable"
    power, setpoint = power.lower(), setpoint.lower()
    reserved = (
        {entity for entity, _ in actors}
        | {entity for key, entity in inputs if key not in (_AC_INPUT, _AC_OUTPUT)}
        | load_entities
        | balance_entities[CONF_DC_BALANCE_IN]
        | balance_entities[CONF_DC_BALANCE_OUT]
    )
    if feedin_setpoint := cfg.get(CONF_FEEDIN_SETPOINT_ENTITY):
        reserved.add(feedin_setpoint.lower())
    # The same physical AC grid point can supply other import/balance views.
    # A setting cannot double as any physical power measurement.
    if (
        power == setpoint
        or power in reserved
        or power in balance_entities[CONF_AC_BALANCE_IN]
        and power in balance_entities[CONF_AC_BALANCE_OUT]
        or any(
            entity == power and key not in (_AC_GRID, "operation_import_power_entity")
            for key, entity in other_roles.items()
        )
        or setpoint in reserved
        or setpoint in other_roles.values()
        or any(setpoint in entities for entities in balance_entities.values())
        or any(setpoint == entity for _, entity in inputs)
    ):
        return "net_grid_topology_unavailable"
    ports = tuple(
        (key, entity) for key, entity in inputs if key in (_AC_INPUT, _AC_OUTPUT)
    )
    return _GridTopology(
        power,
        setpoint,
        tuple((key, entity) for key, entity in inputs if key in _GRID_SHARED_INPUTS)
        + ports,
        next((key for key, entity in ports if entity == power), None),
    )


@dataclass(frozen=True)
class _Reading:
    at: float
    value: float


def _reading(key: str, state: State | None, at: float) -> _Reading | None:
    if state is None or _unconfirmed(state):
        return None
    if key == CONF_RESERVE_GRID_ENTITY:
        source = _GRID_SOURCES.get(state.state.lower())
        return _Reading(at, source) if source is not None else None
    try:
        value = float(state.state)
    except ValueError:
        return None
    unit = state.attributes.get("unit_of_measurement")
    if key == CONF_SOC_ENTITY:
        valid = unit == "%" and 0 <= value <= 100
    elif key == CONF_BATTERY_VOLTAGE_ENTITY:
        valid = unit == "V" and 40 <= value <= 60
    else:
        value *= 1000 if unit == "kW" else 1
        valid = (
            unit in ("W", "kW")
            or (key == _GRID_SETPOINT and unit is None and state.domain == "number")
        ) and abs(value) <= 100_000
    return _Reading(at, value) if valid else None


@dataclass
class _Interval:
    start: float
    end: float
    watts: float


@dataclass
class _Episode:
    generation: tuple[float, ...]
    coordinate: tuple[int, int]
    first_at: float
    last_at: float
    power_at: float
    last_w: float
    minimum_w: float
    maximum_w: float
    covered_ws: float = 0.0
    pending: deque[_Interval] = field(default_factory=deque)

    def advance(
        self, at: float, covered_until: float, power_at: float, watts: float
    ) -> None:
        self.last_at = at
        self.minimum_w = min(self.minimum_w, watts)
        self.maximum_w = max(self.maximum_w, watts)
        if power_at > self.power_at:
            # Use the larger adjacent draw. Sensor cadence or a late
            # downward edge cannot turn these intervals into savings.
            self.pending.append(
                _Interval(self.power_at, power_at, max(self.last_w, watts))
            )
            self.power_at = power_at
        self.last_w = watts
        # A battery endpoint alone does not prove that other inputs
        # remained eligible through it. Commit only common coverage.
        while self.pending and self.pending[0].start < covered_until:
            interval = self.pending[0]
            end = min(interval.end, covered_until)
            self.covered_ws += (end - interval.start) * interval.watts
            interval.start = end
            if end == interval.end:
                self.pending.popleft()


@dataclass
class _GridEpisode:
    generation: tuple[float, ...]
    coordinate: tuple[int, int]
    first_at: float
    last_at: float
    maximum_w: float


class NetDischargeCapture:
    """Bound what the battery supplied without inferring source contribution.

    All channels must publish original observations in the episode. Fresh
    state-machine snapshots are also required when an estimate is published.
    """

    def __init__(
        self, owner: BatteryManagerCoordinator, monitor: SocConsistencyMonitor
    ) -> None:
        self._owner = owner
        self._monitor = monitor
        self._topology: _Topology | str = "net_topology_unavailable"
        self._unsub: list[Callable[[], None]] = []
        self._inputs: dict[str, str] = {}
        self._readings: dict[str, _Reading] = {}
        self._not_before = 0.0
        self._episode: _Episode | None = None
        self._pending_ac_at: float | None = None
        self._grid_topology: _GridTopology | str | None = None
        self._grid_unsub: list[Callable[[], None]] = []
        self._grid_not_before = 0.0
        self._grid_readings: dict[str, _Reading] = {}
        self._grid_episode: _GridEpisode | None = None
        self._grid_pending_ac_at: float | None = None

    def start(self) -> None:
        topology = _Topology.from_owner(self._owner)
        if topology == self._topology:
            if isinstance(topology, _Topology):
                self._bind_grid(topology.grid)
            return
        self.stop()
        self._topology = topology
        if isinstance(topology, str):
            return

        self._inputs = {entity: key for key, entity in topology.inputs}
        self._not_before = dt_util.utcnow().timestamp()
        actors = tuple(entity for entity, _ in topology.actors)
        additional_inputs = tuple(
            entity for key, entity in topology.inputs if key in _OPTIONAL_INPUTS
        )

        @callback
        def actor_changed(event: Event[Any]) -> None:
            if topology is self._topology:
                before = event.data["old_state"]
                after = event.data["new_state"]
                if (
                    before is None
                    or after is None
                    or before.last_changed != after.last_changed
                    or before.state != after.state
                    or _unconfirmed(before) != _unconfirmed(after)
                ):
                    self._not_before = max(
                        self._not_before, event.time_fired.timestamp()
                    )
                    self._episode = None
                    self._pending_ac_at = None
                    self._readings.clear()
                    self._grid_episode = None
                    self._grid_pending_ac_at = None

        @callback
        def input_published(event: Event[Any]) -> None:
            if topology is self._topology:
                self.publication(event)

        hass = self._owner.hass
        self._unsub = [async_track_state_change_event(hass, actors, actor_changed)]
        if additional_inputs:
            self._unsub.extend(
                [
                    async_track_state_change_event(
                        hass, additional_inputs, input_published
                    ),
                    async_track_state_report_event(
                        hass, additional_inputs, input_published
                    ),
                ]
            )
        self._bind_grid(topology.grid)

    def _bind_grid(self, grid: _GridTopology | str | None) -> None:
        if grid == self._grid_topology:
            return
        for unsubscribe in self._grid_unsub:
            unsubscribe()
        self._grid_unsub.clear()
        self._grid_readings.clear()
        self._grid_episode = None
        self._grid_pending_ac_at = None
        self._grid_topology = grid
        if not isinstance(grid, _GridTopology):
            return
        self._grid_not_before = dt_util.utcnow().timestamp()

        @callback
        def grid_published(event: Event[Any]) -> None:
            if grid is self._grid_topology and isinstance(self._topology, _Topology):
                key = (
                    _GRID_POWER
                    if event.data["entity_id"] == grid.power
                    else _GRID_SETPOINT
                )
                self._grid_publication(self._topology, grid, key, event)

        hass = self._owner.hass
        # When the grid point also has a port role, one publication supplies
        # both roles. Processing it twice could split a staged port pair.
        entities = (
            (grid.power, grid.setpoint)
            if grid.power_input is None
            else (grid.setpoint,)
        )
        self._grid_unsub = [
            async_track_state_change_event(hass, entities, grid_published),
            async_track_state_report_event(hass, entities, grid_published),
        ]

    def stop(self) -> None:
        self._bind_grid(None)
        for unsubscribe in self._unsub:
            unsubscribe()
        self._unsub.clear()
        self._inputs.clear()
        self._readings.clear()
        self._episode = None
        self._pending_ac_at = None
        self._topology = "net_topology_unavailable"

    def _ac_flow_reason(self, at: float, *, allow_reverse: bool = False) -> str | None:
        inlet = self._readings.get(_AC_INPUT)
        outlet = self._readings.get(_AC_OUTPUT)
        if inlet is None or outlet is None:
            measured = inlet or outlet
            # A lone nonzero reading cannot prove pass-through.
            if measured is not None and measured.value != 0:
                return "net_ac_flow_incompatible"
            return None
        if not allow_reverse and min(inlet.value, outlet.value) < 0:
            return "net_ac_flow_incompatible"
        if isclose(inlet.value, outlet.value, rel_tol=_AC_ROUNDOFF_REL):
            return None
        if inlet.at != outlet.at and at == max(inlet.at, outlet.at):
            # Each sensor dispatches independently. Wait for the other
            # original report at this timestamp without crediting an interval.
            return "net_publication_unconfirmed"
        return "net_ac_flow_incompatible"

    def _actor_generation(self, topology: _Topology) -> tuple[float, ...] | str:
        if self._monitor.phase != "tracking":
            return "net_coordinate_unstable"
        generation = actor_generation(self._owner, topology.actors)
        if generation is None:
            return "net_mode_unconfirmed"
        return generation

    def _input_values(
        self,
        inputs: tuple[tuple[str, str], ...],
        sample_at: float,
        earliest: float,
        *,
        check_live: bool,
    ) -> tuple[dict[str, float], bool] | str:
        values: dict[str, float] = {}
        unpublished = False
        for key, entity in inputs:
            reading = self._readings.get(key)
            if (
                reading is None
                or reading.at <= earliest
                or not 0 <= sample_at - reading.at <= _MAX_AGE
            ):
                return (
                    "net_mode_unconfirmed"
                    if key == CONF_RESERVE_GRID_ENTITY
                    else "net_telemetry_unavailable"
                )
            if check_live:
                unpublished |= reading != self._live_reading(key, entity)
            values[key] = reading.value
        return values, unpublished

    def _live_reading(self, key: str, entity: str) -> _Reading | None:
        state = self._owner.hass.states.get(entity)
        return (
            _reading(key, state, state.last_reported.timestamp())
            if state is not None
            else None
        )

    @staticmethod
    def _battery_reason(topology: _Topology, values: dict[str, float]) -> str | None:
        voltage = values[CONF_BATTERY_VOLTAGE_ENTITY]
        power = values[CONF_FEEDIN_BATTERY_POWER_ENTITY]
        if abs(power / voltage) >= 100:
            return "net_telemetry_unavailable"
        if voltage > topology.threshold_v:
            return "net_voltage_above_threshold"
        if power > _ZERO_NOISE_W:
            return "net_charging_detected"
        return None

    def _conditions(
        self, topology: _Topology, now: datetime, *, check_live: bool = False
    ) -> tuple[tuple[float, ...], float, float | None] | str:
        generation = self._actor_generation(topology)
        if isinstance(generation, str):
            return generation
        at = now.timestamp()
        sample_at = at
        if check_live and self._pending_ac_at is not None:
            if at - self._pending_ac_at > _MAX_AGE:
                return "net_telemetry_unavailable"
            # The callback still has time to deliver the counterpart with the
            # original timestamp. Wall time cannot invalidate the staged
            # sample's continuity while that publication is still eligible.
            sample_at = self._pending_ac_at
        sample = self._input_values(
            topology.inputs,
            sample_at,
            max(self._not_before, *generation),
            check_live=check_live,
        )
        if isinstance(sample, str):
            return sample
        values, unpublished = sample
        if reason := self._battery_reason(topology, values):
            return reason
        night_until = None
        if topology.site is None:
            if values[_PV_POWER] != 0:
                return "net_pv_detected"
        else:
            night_until = _night_until(topology.site, now)
            if night_until is None:
                return "net_daylight"
        if unpublished:
            return "net_publication_unconfirmed"
        if ac_reason := self._ac_flow_reason(sample_at):
            return ac_reason
        return (
            generation,
            max(0.0, -values[CONF_FEEDIN_BATTERY_POWER_ENTITY]),
            night_until,
        )

    @staticmethod
    def _event_reading(key: str, event: Event[Any]) -> _Reading | None:
        state = event.data["new_state"]
        now = event.time_fired
        stamp = (
            event.data["last_reported"]
            if event.event_type == EVENT_STATE_REPORTED
            else state.last_updated
            if state is not None
            else None
        )
        max_age = _GRID_SETPOINT_MAX_AGE if key == _GRID_SETPOINT else _MAX_AGE
        if stamp != now or not 0 <= (dt_util.utcnow() - now).total_seconds() <= max_age:
            return None
        return _reading(key, state, now.timestamp())

    @callback
    def publication(self, event: Event[Any]) -> None:
        topology = self._topology
        key = self._inputs.get(event.data["entity_id"])
        if isinstance(topology, str) or key is None:
            return
        now = event.time_fired
        at = now.timestamp()
        if self._pending_ac_at is not None and at != self._pending_ac_at:
            self._episode = None  # A different timestamp cannot repair the pair.
        self._pending_ac_at = None
        grid = self._grid_topology
        used_by_grid = (
            isinstance(grid, _GridTopology)
            and (key, event.data["entity_id"]) in grid.shared_inputs
        )
        reading = self._event_reading(key, event)
        previous = self._readings.get(key)
        if reading is None or (previous is not None and at < previous.at):
            self._readings.pop(key, None)
            self._episode = None
            if used_by_grid:
                self._grid_episode = None
            return
        if previous is not None:
            changed = reading.value != previous.value
            if key in (_AC_INPUT, _AC_OUTPUT):
                changed = not isclose(
                    reading.value, previous.value, rel_tol=_AC_ROUNDOFF_REL
                )
            if at - previous.at > _MAX_AGE or (
                changed and (at == previous.at or key == CONF_RESERVE_GRID_ENTITY)
            ):
                self._episode = None
                if used_by_grid:
                    self._grid_episode = None
        self._readings[key] = reading
        if used_by_grid:
            assert isinstance(grid, _GridTopology)
            self._advance_grid(topology, grid, at)

        conditions = self._conditions(topology, now)
        if conditions == "net_publication_unconfirmed":
            self._pending_ac_at = at
            return
        if isinstance(conditions, str):
            self._episode = None
            return
        generation, watts, _ = conditions
        coordinate = self._monitor.epoch, self._monitor.generation
        episode = self._episode
        if (
            episode is None
            or episode.generation != generation
            or episode.coordinate != coordinate
            or not 0 <= at - episode.last_at <= _MAX_AGE
            or max(episode.maximum_w, watts) - min(episode.minimum_w, watts)
            > _MAX_STABLE_SPREAD_W
            or len(episode.pending) >= _MAX_PENDING_INTERVALS
            or (
                topology.site is not None
                and at > episode.last_at
                and _certified_dark_until(topology.site, episode.last_at, at) < at
            )
        ):
            self._episode = _Episode(
                generation, coordinate, at, at, at, watts, watts, watts
            )
        else:
            episode.advance(
                at,
                self._covered_until(),
                self._readings[CONF_FEEDIN_BATTERY_POWER_ENTITY].at,
                watts,
            )

    def _covered_until(self) -> float:
        return min(reading.at for reading in self._readings.values())

    def _grid_conditions(
        self,
        topology: _Topology,
        grid: _GridTopology,
        at: float,
        *,
        check_live: bool = False,
    ) -> tuple[tuple[float, ...], float, _Reading, _Reading] | str:
        generation = self._actor_generation(topology)
        if isinstance(generation, str):
            return generation
        sample_at = at
        if check_live and self._grid_pending_ac_at is not None:
            if at - self._grid_pending_ac_at > _MAX_AGE:
                return "net_telemetry_unavailable"
            sample_at = self._grid_pending_ac_at
        earliest = max(self._not_before, self._grid_not_before, *generation)
        sample = self._input_values(
            grid.shared_inputs, sample_at, earliest, check_live=check_live
        )
        if isinstance(sample, str):
            return sample
        values, unpublished = sample
        if reason := self._battery_reason(topology, values):
            return reason
        power = values[CONF_FEEDIN_BATTERY_POWER_ENTITY]
        if power < -_ZERO_NOISE_W:
            return "net_battery_not_idle"
        measured = (
            self._grid_readings.get(_GRID_POWER)
            if grid.power_input is None
            else self._readings.get(grid.power_input)
        )
        setpoint = self._grid_readings.get(_GRID_SETPOINT)
        if (
            measured is None
            or measured.at <= earliest
            or not 0 <= sample_at - measured.at <= _MAX_AGE
        ):
            return "net_grid_power_unavailable"
        if (
            setpoint is None
            or setpoint.at <= earliest
            or not 0 <= sample_at - setpoint.at <= _GRID_SETPOINT_MAX_AGE
        ):
            return "net_grid_setpoint_unavailable"
        if check_live and (
            unpublished
            or (
                grid.power_input is None
                and measured != self._live_reading(_GRID_POWER, grid.power)
            )
            or setpoint != self._live_reading(_GRID_SETPOINT, grid.setpoint)
        ):
            return "net_publication_unconfirmed"
        if measured.value <= 0:
            return "net_grid_not_importing"
        if measured.value <= max(0.0, setpoint.value) + GRID_IMPORT_MARGIN_W:
            return "net_grid_margin_not_met"
        if _AC_INPUT in values and _AC_OUTPUT in values:
            # Equal reverse readings across the converter can be AC PV
            # pass-through. The grid role must still prove positive import.
            if reason := self._ac_flow_reason(sample_at, allow_reverse=True):
                return reason
        elif min(values.get(_AC_INPUT, 0.0), values.get(_AC_OUTPUT, 0.0)) < 0:
            # One dedicated port cannot establish where reverse power goes.
            return "net_ac_flow_incompatible"
        return generation, max(0.0, -power), measured, setpoint

    def _grid_publication(
        self, topology: _Topology, grid: _GridTopology, key: str, event: Event[Any]
    ) -> None:
        at = event.time_fired.timestamp()
        reading = self._event_reading(key, event)
        previous = self._grid_readings.get(key)
        if reading is None or (previous is not None and at < previous.at):
            self._grid_readings.pop(key, None)
            self._grid_episode = None
            return
        max_age = _GRID_SETPOINT_MAX_AGE if key == _GRID_SETPOINT else _MAX_AGE
        if previous is not None and (
            at - previous.at > max_age
            or (key == _GRID_SETPOINT and previous.value != reading.value)
        ):
            self._grid_episode = None
        self._grid_readings[key] = reading
        self._advance_grid(topology, grid, at)

    def _advance_grid(
        self, topology: _Topology, grid: _GridTopology, at: float
    ) -> None:
        if self._grid_pending_ac_at is not None and at != self._grid_pending_ac_at:
            self._grid_episode = None
        self._grid_pending_ac_at = None
        conditions = self._grid_conditions(topology, grid, at)
        if conditions == "net_publication_unconfirmed":
            self._grid_pending_ac_at = at
            return
        if isinstance(conditions, str):
            self._grid_episode = None
            return
        generation, watts, _, _ = conditions
        coordinate = self._monitor.epoch, self._monitor.generation
        episode = self._grid_episode
        if (
            episode is None
            or episode.generation != generation
            or episode.coordinate != coordinate
            or not 0 <= at - episode.last_at <= _MAX_AGE
        ):
            self._grid_episode = _GridEpisode(generation, coordinate, at, at, watts)
        else:
            episode.last_at = at
            episode.maximum_w = max(episode.maximum_w, watts)

    def _grid_diagnostic(
        self, topology: _Topology, grid: _GridTopology, now: datetime
    ) -> NetDischargeDiagnostic:
        conditions = self._grid_conditions(
            topology, grid, now.timestamp(), check_live=True
        )
        if isinstance(conditions, str):
            if conditions != "net_publication_unconfirmed":
                self._grid_episode = None
                self._grid_pending_ac_at = None
            return {"reason": conditions}
        generation, _, measured, setpoint = conditions
        episode = self._grid_episode
        if (
            episode is None
            or episode.generation != generation
            or episode.coordinate != (self._monitor.epoch, self._monitor.generation)
        ):
            self._grid_episode = None
            return {"reason": "net_episode_too_short", "observed_seconds": 0.0}
        covered_until = min(
            measured.at,
            setpoint.at,
            *(self._readings[key].at for key, _ in grid.shared_inputs),
        )
        observed = max(0.0, covered_until - episode.first_at)
        if observed < MIN_OBSERVED_SECONDS:
            return {"reason": "net_episode_too_short", "observed_seconds": observed}
        return {
            "observed_seconds": observed,
            "net_discharge_w": episode.maximum_w,
            "evidence_mode": "grid_import",
            "grid_import_w": measured.value,
            "grid_setpoint_w": setpoint.value,
            "grid_margin_w": GRID_IMPORT_MARGIN_W,
        }

    def diagnostic(
        self, now: datetime, *, grid_only: bool = False
    ) -> NetDischargeDiagnostic:
        topology = _Topology.from_owner(self._owner)
        if topology != self._topology:
            # Rebind even if the planner is first to notice a settings change.
            self.start()
        if isinstance(topology, str):
            return {"reason": topology}
        self._bind_grid(topology.grid)
        grid = topology.grid
        if grid_only:
            if isinstance(grid, _GridTopology):
                return self._grid_diagnostic(topology, grid, now)
            return {"reason": grid or "net_grid_topology_unavailable"}
        result = self._night_diagnostic(topology, now)
        if grid is None or "net_discharge_w" in result:
            return result
        if isinstance(grid, _GridTopology):
            grid_result = self._grid_diagnostic(topology, grid, now)
        else:
            grid_result = {"reason": grid}
        if "net_discharge_w" in grid_result:
            return grid_result
        if result["reason"] in (
            "net_daylight",
            "net_pv_detected",
            "net_ac_flow_incompatible",
            "net_telemetry_unavailable",
        ):
            return grid_result
        return result

    def _night_diagnostic(
        self, topology: _Topology, now: datetime
    ) -> NetDischargeDiagnostic:
        conditions = self._conditions(topology, now, check_live=True)
        if isinstance(conditions, str):
            # A queued event still needs to prove the new reading. State
            # machine, actors and coordinate may also invalidate between plans.
            if conditions != "net_publication_unconfirmed":
                self._episode = None
                self._pending_ac_at = None
            return {"reason": conditions}
        generation, watts, night_until = conditions
        episode = self._episode
        at = now.timestamp()
        if (
            episode is None
            or episode.generation != generation
            or episode.coordinate != (self._monitor.epoch, self._monitor.generation)
            or (
                topology.site is not None
                and at > episode.last_at
                and _certified_dark_until(topology.site, episode.last_at, at) < at
            )
        ):
            self._episode = None
            return {"reason": "net_episode_too_short", "observed_seconds": 0.0}
        # Cut at the oldest latest publication. Additional reports on one
        # input cannot advance the covered endpoint of another input.
        observed = max(0.0, self._covered_until() - episode.first_at)
        if observed < MIN_OBSERVED_SECONDS:
            return {"reason": "net_episode_too_short", "observed_seconds": observed}
        result: NetDischargeDiagnostic = {
            "observed_seconds": observed,
            "net_discharge_w": max(watts, episode.covered_ws / observed),
        }
        if night_until is not None:
            assert topology.site is not None
            result["valid_until"] = _certified_dark_until(
                topology.site, at, night_until
            )
        return result
