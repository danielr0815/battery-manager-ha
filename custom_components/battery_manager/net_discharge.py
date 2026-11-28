"""Observe battery terminal discharge in sustained, confirmed support mode."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import cache, lru_cache, partial
from math import ceil, cos, floor, radians
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
    CONF_BATTERY_VOLTAGE_ENTITY,
    CONF_DC_BALANCE_IN,
    CONF_DC_BALANCE_OUT,
    CONF_FEEDIN_BATTERY_POWER_ENTITY,
    CONF_FEEDIN_SETPOINT_ENTITY,
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
    _BALANCE_KEYS,
    _GRID_SOURCES,
    _MAX_AGE,
    _ON_ACTORS,
    _ON_REQUIRED_ACTORS,
    _OTHER_POWER_ROLES,
    _load_meter_entities,
    _unconfirmed,
    actor_generation,
)
from .source_health import source_health

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from .coordinator import BatteryManagerCoordinator

_PV_POWER = "operation_pv_power_entity"
_AC_GRID, *_AC_PORTS = LIVE_AC_POWER_KEYS
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
_OPTIONAL_INPUTS = (_PV_POWER, CONF_RESERVE_GRID_ENTITY)
_GRID_SHARED_INPUTS = (*BATTERY_COORDINATE_KEYS, CONF_RESERVE_GRID_ENTITY)
# Night without a measured PV channel: even twilight is excluded.
_NIGHT_DEPRESSION = 18.0
# Protect the transition itself against small differences between Astral's
# crossing and elevation calculations. Later, require independent darkness
# again. At polar latitudes an isolated dawn can lack a corresponding dusk.
_DAWN_GUARD_SECONDS = 300
# Victron's integer-watt reports can move one count around zero. A wider
# variation would mix distinct load conditions into the same projection.
_ZERO_NOISE_W = 1.0
# Brief 4 W draw was observed at an otherwise idle battery terminal. Require
# the idle endpoint within one ordinary reporting interval. Bound the total
# uncertainty of the adjacent intervals to a minute per minimum window.
_MAX_GRID_PULSE_W = 4.0
_MAX_GRID_PULSE_SECONDS = 30
_MAX_GRID_PULSE_COVERAGE_SECONDS = 60
_MAX_STABLE_SPREAD_W = 10.0
_MAX_PENDING_INTERVALS = 4096
_MAX_SKY_CHECKS = 2048
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
    observed_from: float
    observed_until: float
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
        all_entities = tuple(entity for entity, _ in actors) + tuple(
            entity for _, entity in inputs
        )
        # AC residual-load meters need distinct identities from the battery
        # terminals and feedback. Their readings belong to the AC controller.
        other_roles = {
            key: entity.lower()
            for key in _OTHER_POWER_ROLES
            if key not in (CONF_FEEDIN_BATTERY_POWER_ENTITY, *_OPTIONAL_INPUTS)
            and (entity := cfg.get(key))
        }
        load_entities = _load_meter_entities(owner)
        other_entities = set(other_roles.values()) | load_entities
        balance_entities = {
            key: {entity.lower() for entity in (cfg.get(key) or ())}
            for key in _BALANCE_KEYS
        }
        for key, entities in balance_entities.items():
            # A PV measurement can also supply the AC generation balance.
            if key == CONF_AC_BALANCE_IN and pv_entity is not None:
                entities = entities - {pv_entity}
            other_entities.update(entities)
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


def _grid_bindings(cfg: dict[str, Any]) -> tuple[str | None, str | None]:
    power = cfg.get(_GRID_POWER)
    setpoint = cfg.get(_GRID_SETPOINT)
    # The signed site-grid channel is already assigned to the same boundary.
    # The effective regulation target always needs an explicit binding.
    if not power and setpoint:
        power = cfg.get(_AC_GRID)
    return power.lower() if power else None, setpoint.lower() if setpoint else None


def _grid_topology(
    cfg: dict[str, Any],
    actors: tuple[tuple[str, str], ...],
    inputs: tuple[tuple[str, str], ...],
    other_roles: dict[str, str],
    load_entities: set[str],
    balance_entities: dict[str, set[str]],
) -> _GridTopology | str | None:
    power, setpoint = _grid_bindings(cfg)
    if not power and not setpoint:
        return None
    if not power:
        return "net_grid_power_unavailable"
    if not setpoint:
        return "net_grid_setpoint_unavailable"
    reserved = (
        {entity for entity, _ in actors}
        | {entity for _, entity in inputs}
        | load_entities
        | balance_entities[CONF_DC_BALANCE_IN]
        | balance_entities[CONF_DC_BALANCE_OUT]
    )
    if feedin_setpoint := cfg.get(CONF_FEEDIN_SETPOINT_ENTITY):
        reserved.add(feedin_setpoint.lower())
    # A converter port may also be the site meter on a single-feed system.
    # If the configured site boundary is elsewhere, the port's import can
    # coincide with site export and cannot establish grid-import evidence.
    port_meters = [other_roles.get(key) for key in _AC_PORTS]
    site_meter = other_roles.get(_AC_GRID)
    if power in port_meters and (
        port_meters.count(power) > 1 or site_meter not in (None, power)
    ):
        return "net_grid_topology_unavailable"
    # The same physical AC grid point can supply other import/balance views.
    # A setting cannot double as any physical power measurement.
    if (
        power == setpoint
        or power in reserved
        or power in balance_entities[CONF_AC_BALANCE_IN]
        and power in balance_entities[CONF_AC_BALANCE_OUT]
        or any(
            entity == power
            and key not in (*LIVE_AC_POWER_KEYS, "operation_import_power_entity")
            for key, entity in other_roles.items()
        )
        or setpoint in reserved
        or setpoint in other_roles.values()
        or any(setpoint in entities for entities in balance_entities.values())
    ):
        return "net_grid_topology_unavailable"
    return _GridTopology(
        power,
        setpoint,
        tuple((key, entity) for key, entity in inputs if key in _GRID_SHARED_INPUTS),
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


def _grid_source_watts(key: str, state: State) -> float | None:
    reading = _reading(key, state, state.last_reported.timestamp())
    return reading.value if reading is not None else None


def grid_measurement_health(
    hass: HomeAssistant, cfg: dict[str, Any], now: datetime
) -> list[dict[str, Any]]:
    """Use the expectation's effective binding, units and publication limits."""
    power, setpoint = _grid_bindings(cfg)
    power_role = _GRID_POWER.removesuffix("_entity")
    setpoint_role = _GRID_SETPOINT.removesuffix("_entity")
    return source_health(
        hass,
        {power_role: power, setpoint_role: setpoint},
        now,
        kinds={power_role: "power", setpoint_role: "power"},
        max_ages_s={power_role: _MAX_AGE, setpoint_role: _GRID_SETPOINT_MAX_AGE},
        fallbacks={power_role: "live_ac_grid"}
        if power and not cfg.get(_GRID_POWER)
        else None,
        boundaries={
            power_role: "site_grid_import_positive",
            setpoint_role: "effective_grid_import_setpoint",
        },
        normalizers={
            power_role: partial(_grid_source_watts, _GRID_POWER),
            setpoint_role: partial(_grid_source_watts, _GRID_SETPOINT),
        },
    )


@dataclass
class _Interval:
    start: float
    end: float
    watts: float


@dataclass
class _Episode:
    generation: tuple[float, ...]
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
class _GridEpisode(_Episode):
    pulse: tuple[float, float] | None = None
    pulse_spans: deque[tuple[float, float]] = field(default_factory=deque)
    pulse_seconds: float = 0.0

    def advance_grid(
        self, at: float, covered_until: float, power_at: float, watts: float
    ) -> bool:
        if power_at > self.power_at:
            if self.pulse is not None:
                start, first_high = self.pulse
                if power_at - first_high > _MAX_GRID_PULSE_SECONDS:
                    return False
                if watts <= _ZERO_NOISE_W:
                    # Include both flanks in the conservative time budget, as
                    # they also contribute at the higher adjacent watt value.
                    self.pulse_seconds += power_at - start
                    self.pulse_spans.append((start, power_at))
                    self.pulse = None
            elif watts > _ZERO_NOISE_W:
                self.pulse = self.power_at, power_at
        since = power_at - MIN_OBSERVED_SECONDS
        while self.pulse_spans and self.pulse_spans[0][1] <= since:
            start, end = self.pulse_spans.popleft()
            self.pulse_seconds -= end - start
        if self.pulse_spans:
            pulse_start = self.pulse_spans[0][0]
            if self.pulse_seconds - max(0.0, since - pulse_start) > (
                _MAX_GRID_PULSE_COVERAGE_SECONDS
            ):
                return False
        self.advance(at, covered_until, power_at, watts)
        return True


class NetDischargeCapture:
    """Bound what the battery supplied without inferring source contribution.

    All channels must publish original observations in the episode. Fresh
    state-machine snapshots are also required when an estimate is published.
    """

    def __init__(self, owner: BatteryManagerCoordinator) -> None:
        self._owner = owner
        self._topology: _Topology | str = "net_topology_unavailable"
        self._unsub: list[Callable[[], None]] = []
        self._inputs: dict[str, str] = {}
        self._readings: dict[str, _Reading] = {}
        self._not_before = 0.0
        self._episode: _Episode | None = None
        self._grid_topology: _GridTopology | str | None = None
        self._grid_unsub: list[Callable[[], None]] = []
        self._grid_not_before = 0.0
        self._grid_readings: dict[str, _Reading] = {}
        self._grid_episode: _GridEpisode | None = None

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
                    self._readings.clear()
                    self._grid_episode = None

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
        entities = (grid.power, grid.setpoint)
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
        self._topology = "net_topology_unavailable"

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
        generation = actor_generation(self._owner, topology.actors)
        if generation is None:
            return "net_mode_unconfirmed"
        sample = self._input_values(
            topology.inputs,
            now.timestamp(),
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
        if previous is not None and (
            at - previous.at > _MAX_AGE
            or reading.value != previous.value
            and (
                key == CONF_RESERVE_GRID_ENTITY
                or at == previous.at
                and key != CONF_SOC_ENTITY
            )
        ):
            self._episode = None
            if used_by_grid:
                self._grid_episode = None
        self._readings[key] = reading
        if used_by_grid:
            assert isinstance(grid, _GridTopology)
            self._advance_grid(topology, grid, at)

        conditions = self._conditions(topology, now)
        if isinstance(conditions, str):
            self._episode = None
            return
        generation, watts, _ = conditions
        episode = self._episode
        if (
            episode is None
            or episode.generation != generation
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
            self._episode = _Episode(generation, at, at, at, watts, watts, watts)
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
        generation = actor_generation(self._owner, topology.actors)
        if generation is None:
            return "net_mode_unconfirmed"
        earliest = max(self._not_before, self._grid_not_before, *generation)
        sample = self._input_values(
            grid.shared_inputs, at, earliest, check_live=check_live
        )
        if isinstance(sample, str):
            return sample
        values, unpublished = sample
        if reason := self._battery_reason(topology, values):
            return reason
        power = values[CONF_FEEDIN_BATTERY_POWER_ENTITY]
        if power < -_MAX_GRID_PULSE_W:
            return "net_battery_not_idle"
        measured = self._grid_readings.get(_GRID_POWER)
        setpoint = self._grid_readings.get(_GRID_SETPOINT)
        if (
            measured is None
            or measured.at <= earliest
            or not 0 <= at - measured.at <= _MAX_AGE
        ):
            return "net_grid_power_unavailable"
        if (
            setpoint is None
            or setpoint.at <= earliest
            or not 0 <= at - setpoint.at <= _GRID_SETPOINT_MAX_AGE
        ):
            return "net_grid_setpoint_unavailable"
        if check_live and (
            unpublished
            or measured != self._live_reading(_GRID_POWER, grid.power)
            or setpoint != self._live_reading(_GRID_SETPOINT, grid.setpoint)
        ):
            return "net_publication_unconfirmed"
        if measured.value <= 0:
            return "net_grid_not_importing"
        if measured.value <= max(0.0, setpoint.value) + GRID_IMPORT_MARGIN_W:
            return "net_grid_margin_not_met"
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
        conditions = self._grid_conditions(topology, grid, at)
        if isinstance(conditions, str):
            self._grid_episode = None
            return
        generation, watts, measured, setpoint = conditions
        episode = self._grid_episode
        if (
            episode is not None
            and episode.generation == generation
            and 0 <= at - episode.last_at <= _MAX_AGE
            and len(episode.pending) < _MAX_PENDING_INTERVALS
            and len(episode.pulse_spans) < _MAX_PENDING_INTERVALS
            and episode.advance_grid(
                at,
                self._grid_covered_until(grid, measured, setpoint),
                self._readings[CONF_FEEDIN_BATTERY_POWER_ENTITY].at,
                watts,
            )
        ):
            return
        # Elevated readings alone cannot start an otherwise idle episode.
        self._grid_episode = (
            _GridEpisode(generation, at, at, at, watts, watts, watts)
            if watts <= _ZERO_NOISE_W
            else None
        )

    def _grid_covered_until(
        self, grid: _GridTopology, measured: _Reading, setpoint: _Reading
    ) -> float:
        return min(
            measured.at,
            setpoint.at,
            *(self._readings[key].at for key, _ in grid.shared_inputs),
        )

    def _grid_diagnostic(
        self, topology: _Topology, grid: _GridTopology, now: datetime
    ) -> NetDischargeDiagnostic:
        conditions = self._grid_conditions(
            topology, grid, now.timestamp(), check_live=True
        )
        if isinstance(conditions, str):
            if conditions != "net_publication_unconfirmed":
                self._grid_episode = None
            return {"reason": conditions}
        generation, watts, measured, setpoint = conditions
        episode = self._grid_episode
        if watts > _ZERO_NOISE_W:
            if (
                episode is not None
                and episode.pulse is not None
                and now.timestamp() - episode.pulse[1] > _MAX_GRID_PULSE_SECONDS
            ):
                self._grid_episode = None
            return {"reason": "net_battery_not_idle"}
        if episode is None or episode.generation != generation:
            self._grid_episode = None
            return {"reason": "net_episode_too_short", "observed_seconds": 0.0}
        covered_until = self._grid_covered_until(grid, measured, setpoint)
        if episode.pulse_spans and covered_until < episode.pulse_spans[-1][1]:
            return {"reason": "net_publication_unconfirmed"}
        observed = max(0.0, covered_until - episode.first_at)
        if observed < MIN_OBSERVED_SECONDS:
            return {"reason": "net_episode_too_short", "observed_seconds": observed}
        return {
            "observed_seconds": observed,
            "observed_from": episode.first_at,
            "observed_until": covered_until,
            "net_discharge_w": max(watts, episode.covered_ws / observed),
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
            return {"reason": conditions}
        generation, watts, night_until = conditions
        episode = self._episode
        at = now.timestamp()
        if (
            episode is None
            or episode.generation != generation
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
        covered_until = self._covered_until()
        observed = max(0.0, covered_until - episode.first_at)
        if observed < MIN_OBSERVED_SECONDS:
            return {"reason": "net_episode_too_short", "observed_seconds": observed}
        result: NetDischargeDiagnostic = {
            "observed_seconds": observed,
            "observed_from": episode.first_at,
            "observed_until": covered_until,
            "net_discharge_w": max(watts, episode.covered_ws / observed),
        }
        if night_until is not None:
            assert topology.site is not None
            result["valid_until"] = _certified_dark_until(
                topology.site, at, night_until
            )
        return result
