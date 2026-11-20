"""Qualification of the conditional 48 V ON delivery estimate."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from homeassistant.util import dt as dt_util

from custom_components.battery_manager import psu_observation
from custom_components.battery_manager.voltage_gate import VoltageGateRuntime

_OUT = "sensor.psu_output"
_IN = "sensor.psu_input"
_PSU24 = "switch.psu24"
_PSU48 = "switch.psu48"
_DCDC = "switch.dcdc"
_LIMIT = "number.inverter_limit"
_BLOCK = "switch.inverter_block"
_GRID = "sensor.reserve_source"


@pytest.fixture
async def delivery(hass, freezer):
    freezer.move_to("2026-10-14T18:00:00Z")
    owner = SimpleNamespace(
        hass=hass,
        raw_config={
            "support_dc24_switch_entity": _PSU24,
            "support_dc48_switch_entity": _PSU48,
            "dcdc_switch_entity": _DCDC,
            "inverter_discharge_limit_entity": _LIMIT,
            "inverter_block_switch_entity": _BLOCK,
            "support_dc48_power_entity": _IN,
            "support_dc48_output_power_entity": _OUT,
            "psu48_output_voltage_v": 50,
            "psu48_max_current_a": 2,
            "psu48_efficiency": 0.8,
        },
    )
    for entity, value in (
        (_PSU24, "on"),
        (_PSU48, "on"),
        (_DCDC, "off"),
        (_LIMIT, "0.0"),
        (_BLOCK, "on"),
    ):
        hass.states.async_set(entity, value)
    hass.states.async_set(_OUT, "90", {"unit_of_measurement": "W"})
    await hass.async_block_till_done()
    runtime = VoltageGateRuntime(owner)
    runtime.start_observing()
    yield runtime
    runtime.stop_observing()


async def _report(
    hass, freezer, watts=40, seconds=30, *, unit="W", entity=_OUT, grid=None
):
    freezer.tick(seconds)
    if grid is not None:
        hass.states.async_set(_GRID, grid)
    hass.states.async_set(entity, str(watts), {"unit_of_measurement": unit})
    await hass.async_block_till_done()


async def _episode(hass, freezer, *, reports=61, watts=40):
    for _ in range(reports):
        await _report(hass, freezer, watts=watts)


def _estimate(runtime, *, now=None):
    now = now or dt_util.utcnow()
    runtime.psu.capture(runtime.owner, now)
    return runtime.psu.expected48(now)


async def test_elapsed_time_and_distinct_reports_including_unchanged_values(
    delivery, hass, freezer
):
    assert _estimate(delivery) is None
    await _episode(hass, freezer, reports=60)
    assert _estimate(delivery) is None  # 59 gaps, including no startup cache.
    await _report(hass, freezer, seconds=0)
    assert _estimate(delivery) is None  # Same publication time adds no duration.
    await _report(hass, freezer, watts=0.035, unit="kW")
    assert _estimate(delivery) == (35, 1800)
    now = dt_util.utcnow()
    assert delivery.psu.expected48_diagnostic(now) == {
        "status": "qualified",
        "minimum_w": 35,
        "observed_seconds": 1800,
    }
    assert delivery.psu.expectation_explanation(delivery.owner, now) == {
        "relay_confirmed_on": True,
        "observed_from": now.timestamp() - 1800,
        "observed_until": now.timestamp(),
        "observed_seconds": 1800,
    }
    assert delivery.psu.expected48(now + timedelta(seconds=1)) is None
    await _report(hass, freezer, watts=0)
    await _report(hass, freezer, watts=90)
    assert _estimate(delivery) == (0, 1860)


async def test_fresh_capture_and_meter_gap_with_positive_reading(
    delivery, hass, freezer
):
    await _episode(hass, freezer)
    assert _estimate(delivery) == (40, 1800)
    freezer.tick(30)
    assert _estimate(delivery) == (40, 1800)
    freezer.tick(1)
    assert _estimate(delivery) is None
    await _report(hass, freezer, seconds=0)
    assert _estimate(delivery) is None
    await _episode(hass, freezer, reports=60)
    assert _estimate(delivery) == (40, 1800)
    await _report(hass, freezer, seconds=31)
    assert _estimate(delivery) is None


async def test_full_dc_delivery_proof_is_independent_of_bms_coordinate(
    delivery, hass, freezer
):
    await _episode(hass, freezer)
    assert _estimate(delivery) == (40, 1800)
    first_at = dt_util.utcnow().timestamp() - 1800
    delivery.monitor.epoch += 1
    delivery.monitor.generation += 1
    delivery.monitor.phase = "reconciling"
    await _report(hass, freezer, watts=41)
    assert _estimate(delivery) == (40, 1830)
    proof = delivery.psu.expectation_explanation(delivery.owner, dt_util.utcnow())
    assert proof["observed_from"] == first_at
    assert proof["observed_until"] == dt_util.utcnow().timestamp()


async def test_direct_dc_delivery_uses_configured_ac_feedback(delivery, hass, freezer):
    delivery.owner.raw_config["reserve_grid_available_entity"] = _GRID
    delivery.start_observing()
    await _episode(hass, freezer)
    assert _estimate(delivery) is None
    for _ in range(62):
        await _report(hass, freezer, grid="AC_INPUT_1")
    assert _estimate(delivery) == (40, 1800)
    await _report(hass, freezer, seconds=1, grid="AC_INPUT_2")
    assert _estimate(delivery) is None
    for _ in range(61):
        await _report(hass, freezer, grid="AC_INPUT_2")
    assert _estimate(delivery) == (40, 1800)
    await _report(hass, freezer, seconds=15)
    assert _estimate(delivery) == (40, 1815)
    # At this DC publication the grid state is fresh again. The original
    # grid reports still have an uncovered interval.
    await _report(hass, freezer, seconds=16, grid="AC_INPUT_2")
    assert _estimate(delivery) is None


@pytest.mark.parametrize(
    ("entity", "interruption", "rebound"),
    [
        (_PSU24, "off", "on"),
        (_PSU48, "off", "on"),
        (_DCDC, "on", "off"),
        (_DCDC, "unavailable", "off"),
        (_LIMIT, "3000", "0.0"),
        (_LIMIT, "unavailable", "0"),
        (_BLOCK, "off", "on"),
    ],
)
async def test_confirmed_topology_rebound_breaks_continuity(
    delivery, hass, freezer, entity, interruption, rebound
):
    await _episode(hass, freezer)
    assert _estimate(delivery) == (40, 1800)
    freezer.tick(5)
    hass.states.async_set(entity, interruption)
    await _report(hass, freezer, seconds=0)
    assert _estimate(delivery) is None
    hass.states.async_set(entity, rebound)
    await _report(hass, freezer, seconds=0)
    assert _estimate(delivery) is None
    await _episode(hass, freezer, reports=60)
    assert _estimate(delivery) is None
    await _report(hass, freezer)
    assert _estimate(delivery) == (40, 1800)


async def test_switch_flank_between_captures_breaks_credit_even_at_same_time(
    delivery, hass, freezer
):
    await _episode(hass, freezer)
    assert _estimate(delivery) == (40, 1800)
    hass.states.async_set(_PSU48, "off")
    hass.states.async_set(_PSU48, "on")
    await hass.async_block_till_done()
    assert _estimate(delivery) is None
    await _report(hass, freezer)
    assert _estimate(delivery) is None


@pytest.mark.parametrize("attribute", ["assumed_state", "restored"])
async def test_unconfirmed_actor_state_cannot_confirm_or_bridge_an_episode(
    delivery, hass, freezer, attribute
):
    await _episode(hass, freezer)
    assert _estimate(delivery) == (40, 1800)
    hass.states.async_set(_PSU48, "on", {attribute: True})
    hass.states.async_set(_PSU48, "on")
    await _report(hass, freezer)
    assert _estimate(delivery) is None

    hass.states.async_set(_PSU48, "on", {attribute: True})
    await _episode(hass, freezer)
    assert _estimate(delivery) is None


@pytest.mark.parametrize("attribute", ["assumed_state", "restored"])
async def test_unconfirmed_output_cannot_bridge_an_episode(
    delivery, hass, freezer, attribute
):
    await _episode(hass, freezer)
    assert _estimate(delivery) == (40, 1800)
    freezer.tick(5)
    hass.states.async_set(_OUT, "40", {"unit_of_measurement": "W", attribute: True})
    await _report(hass, freezer, seconds=5)
    assert _estimate(delivery) is None


@pytest.mark.parametrize(
    ("role", "entity"),
    [
        ("dcdc_switch_entity", None),
        ("support_dc24_switch_entity", _PSU48.upper()),
        ("inverter_block_switch_entity", _PSU48),
        ("support_dc24_power_entity", _OUT),
        ("support_dc48_power_entity", _OUT),
        ("support_dc24_output_power_entity", _OUT),
        ("feedin_battery_power_entity", _OUT.upper()),
        ("operation_pv_power_entity", _OUT),
        ("operation_import_power_entity", _OUT),
        ("operation_export_power_entity", _OUT),
        ("live_ac_grid_power_entity", _OUT),
        ("soc_expectation_grid_power_entity", _OUT.upper()),
        ("soc_expectation_grid_setpoint_entity", _OUT),
        ("export_meter_entity", _OUT),
        ("ac_load_entity", _OUT),
        ("dc_load_entity", _OUT.upper()),
        ("ac_balance_in_entities", [_OUT.upper()]),
        ("dc_balance_out_entities", [_OUT]),
    ],
)
async def test_ambiguous_or_incomplete_topology_must_requalify_after_rebinding(
    delivery, hass, freezer, role, entity
):
    await _episode(hass, freezer)
    assert _estimate(delivery) == (40, 1800)

    config = delivery.owner.raw_config
    valid_entity = config.get(role)
    config[role] = entity
    now = dt_util.utcnow()
    assert _estimate(delivery, now=now) is None
    assert (
        delivery.psu.expectation_explanation(delivery.owner, now)["reason"]
        == "invalid_dc_topology"
    )
    delivery.start_observing()
    await _episode(hass, freezer)
    now = dt_util.utcnow()
    snapshot = delivery.psu.capture(delivery.owner, now)[1]
    assert snapshot.dc_output is not None
    assert snapshot.dc_output.power_w == 40  # Present measurement still usable.
    assert delivery.psu.expected48(now) is None

    config[role] = valid_entity
    delivery.start_observing()
    await _episode(hass, freezer, reports=60)
    assert _estimate(delivery) is None
    await _report(hass, freezer)
    assert _estimate(delivery) == (40, 1800)


@pytest.mark.parametrize(
    "role",
    [
        "power_entity",
        "energy_entity",
        "input_power_entity",
        "output_power_entity",
        "detection_entity",
    ],
)
async def test_subentry_meter_overlap_invalidates_delivery_before_rebinding(
    delivery, hass, freezer, role
):
    await _episode(hass, freezer)
    assert _estimate(delivery) == (40, 1800)

    owner = delivery.owner
    owner.entry = SimpleNamespace(
        subentries={"secondary": SimpleNamespace(data={role: _OUT.upper()})}
    )
    # Existing subscriptions must discard the evidence even before the
    # config update has reinitialized the observation adapters.
    await _report(hass, freezer, seconds=5)
    now = dt_util.utcnow()
    assert _estimate(delivery, now=now) is None
    assert delivery.psu.expectation_explanation(owner, now)["reason"] == (
        "invalid_dc_topology"
    )

    owner.entry.subentries.clear()
    await _episode(hass, freezer, reports=60)
    assert _estimate(delivery) is None
    await _report(hass, freezer)
    assert _estimate(delivery) == (40, 1800)


async def test_output_meter_can_also_measure_dc_bus_inflow(delivery, hass, freezer):
    await _episode(hass, freezer)
    assert _estimate(delivery) == (40, 1800)
    delivery.owner.raw_config["dc_balance_in_entities"] = [_OUT]
    delivery.start_observing()
    await _report(hass, freezer)
    assert _estimate(delivery) == (40, 1830)


@pytest.mark.parametrize(
    ("volts", "amps"),
    [(50, 0), (0, 2), (50, float("inf")), (float("nan"), 2), (1e308, 2)],
)
async def test_positive_finite_hardware_limit_required(
    delivery, hass, freezer, volts, amps
):
    delivery.owner.raw_config.update(
        psu48_output_voltage_v=volts, psu48_max_current_a=amps
    )
    delivery.start_observing()
    await _episode(hass, freezer)
    assert _estimate(delivery) is None


async def test_stop_restart_and_reconfigure_never_reuse_observed_credit(
    delivery, hass, freezer
):
    await _episode(hass, freezer)
    delivery.start_observing()  # Repeated listener initialization is idempotent.
    assert _estimate(delivery) == (40, 1800)
    delivery.stop_observing()
    await _episode(hass, freezer)
    assert _estimate(delivery) is None
    delivery.start_observing()
    assert _estimate(delivery) is None
    await _episode(hass, freezer)
    assert _estimate(delivery) == (40, 1800)
    delivery.owner.raw_config["psu48_max_current_a"] = 3
    assert _estimate(delivery) is None
    await _report(hass, freezer)
    delivery.start_observing()
    await _episode(hass, freezer)
    assert _estimate(delivery) == (40, 1800)


async def test_callbacks_from_old_subscriptions_cannot_invalidate_replacement(
    delivery, hass, freezer, monkeypatch
):
    callbacks = []
    track = psu_observation.async_track_state_change_event

    def capture_callback(hass, entities, action):
        callbacks.append(action)
        return track(hass, entities, action)

    monkeypatch.setattr(
        psu_observation, "async_track_state_change_event", capture_callback
    )
    delivery.stop_observing()
    delivery.start_observing()
    actor, meter = callbacks
    delivery.stop_observing()
    delivery.start_observing()
    await _episode(hass, freezer)
    assert _estimate(delivery) == (40, 1800)
    removed = SimpleNamespace(
        data={"old_state": hass.states.get(_PSU48), "new_state": None},
        time_fired=dt_util.utcnow(),
        event_type="state_changed",
    )
    actor(removed)
    meter(removed)
    assert _estimate(delivery) == (40, 1800)


async def test_no_optional_measurement_or_missing_actor_has_no_credit(
    delivery, hass, freezer
):
    delivery.owner.raw_config.pop("support_dc48_output_power_entity")
    delivery.start_observing()
    for _ in range(61):
        await _report(hass, freezer, entity=_IN)
    assert _estimate(delivery) is None
    assert delivery.psu.expected48_diagnostic(dt_util.utcnow()) == {
        "status": "unqualified",
        "minimum_w": None,
        "observed_seconds": None,
    }
    delivery.owner.raw_config["support_dc48_output_power_entity"] = _OUT
    hass.states.async_remove(_PSU24)
    delivery.start_observing()
    await _episode(hass, freezer)
    assert _estimate(delivery) is None


@pytest.mark.parametrize(
    ("watts", "unit"),
    [("unavailable", "W"), (40, "kWh"), (101, "W")],
)
async def test_invalid_output_breaks_evidence(delivery, hass, freezer, watts, unit):
    await _episode(hass, freezer)
    assert _estimate(delivery) == (40, 1800)
    await _report(hass, freezer, watts=watts, unit=unit)
    await _report(hass, freezer)
    assert _estimate(delivery) is None


@pytest.mark.parametrize("seconds", [-1, 1])
async def test_late_and_future_publications_within_meter_cadence_break_evidence(
    delivery, hass, freezer, seconds
):
    await _episode(hass, freezer)
    assert _estimate(delivery) == (40, 1800)
    hass.states.async_set_internal(
        _OUT,
        "60",
        {"unit_of_measurement": "W"},
        False,
        None,
        None,
        (dt_util.utcnow() + timedelta(seconds=seconds)).timestamp(),
    )
    await hass.async_block_till_done()
    await _report(hass, freezer)
    assert _estimate(delivery) is None


async def test_projection_duration_is_bounded_at_48_hours(delivery, hass, freezer):
    await _episode(hass, freezer, reports=5763)
    assert _estimate(delivery) == (40, 48 * 3600)
