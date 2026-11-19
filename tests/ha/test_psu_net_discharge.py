"""Terminal discharge qualification uses original, mutually covering HA reports."""

from types import SimpleNamespace

import pytest
from homeassistant.util import dt as dt_util

from custom_components.battery_manager import net_discharge
from custom_components.battery_manager.voltage_gate import VoltageGateRuntime

_PSU24 = "switch.psu24"
_PSU48 = "switch.psu48"
_DCDC = "switch.dcdc"
_LIMIT = "number.inverter_limit"
_BLOCK = "switch.inverter_block"
_SOC = "sensor.battery_soc"
_POWER = "sensor.battery_power"
_VOLTAGE = "sensor.battery_voltage"
_PV = "sensor.real_solar_power"
_AC_IN = "sensor.converter_input"
_AC_OUT = "sensor.converter_output"
_GRID = "sensor.grid_available"
_PCC = "sensor.site_grid_power"
_CONFIG = {
    "support_dc24_switch_entity": _PSU24,
    "support_dc48_switch_entity": _PSU48,
    "dcdc_switch_entity": _DCDC,
    "inverter_discharge_limit_entity": _LIMIT,
    "soc_entity": _SOC,
    "feedin_battery_power_entity": _POWER,
    "battery_voltage_entity": _VOLTAGE,
    "psu48_output_voltage_v": 49.56,
    "psu48_on_voltage_v": 49.6,
    "battery_capacity_wh": 5000,
}
_SWITCHES = {_PSU24: "on", _PSU48: "on", _DCDC: "off", _LIMIT: "0.0", _BLOCK: "on"}
_UNITS = {
    _POWER: "W",
    _VOLTAGE: "V",
    _SOC: "%",
    _PV: "W",
    _AC_IN: "W",
    _AC_OUT: "W",
    _PCC: "W",
}
_NIGHT = "2026-11-18T00:00:00Z"


@pytest.fixture
async def runtime(hass, freezer):
    freezer.move_to(_NIGHT)
    hass.config.latitude = 48
    hass.config.longitude = 12
    owner = SimpleNamespace(
        hass=hass,
        raw_config=dict(_CONFIG),
        live_ac=SimpleNamespace(envelope=None),
        _schedule_replan=lambda: None,
        _save_persistent_state=lambda: None,
    )
    for entity, value in _SWITCHES.items():
        hass.states.async_set(entity, value)
    gate = VoltageGateRuntime(owner)
    gate.ready = True
    gate.start_observing()
    yield gate
    gate.stop_observing()


def _set(hass, entity, value, *, timestamp=None, **attributes):
    if entity in _UNITS:
        attributes.setdefault("unit_of_measurement", _UNITS[entity])
    hass.states.async_set(entity, str(value), attributes, timestamp=timestamp)


async def _report(
    hass,
    freezer,
    *,
    seconds=30,
    watts=-4,
    soc=69,
    voltage=49.5,
    skip=(),
    **extras,
):
    freezer.tick(seconds)
    for entity, value in {
        _POWER: watts,
        _VOLTAGE: voltage,
        _SOC: soc,
        **extras,
    }.items():
        if entity not in skip:
            _set(hass, entity, value)
    await hass.async_block_till_done()


async def _episode(hass, freezer, *, n=61, **kwargs):
    for _ in range(n):
        await _report(hass, freezer, **kwargs)


def _diagnostic(runtime):
    return runtime.net_discharge_diagnostic(dt_util.utcnow())


def _reason(runtime):
    return _diagnostic(runtime)["reason"]


async def test_historical_topology_needs_no_optional_meter_or_feedback(
    runtime, hass, freezer
):
    assert _reason(runtime) == "net_telemetry_unavailable"
    await _episode(hass, freezer, n=60)
    assert _diagnostic(runtime) == {
        "reason": "net_episode_too_short",
        "observed_seconds": 1770,
    }
    await _report(hass, freezer, seconds=0)
    assert _diagnostic(runtime)["observed_seconds"] == 1770
    # The battery report advances, but the common horizon has yet to move.
    await _report(hass, freezer, skip=(_SOC, _VOLTAGE))
    assert _diagnostic(runtime)["observed_seconds"] == 1770
    await _report(hass, freezer, seconds=0, skip=(_POWER,))
    proof = _diagnostic(runtime)
    assert proof["observed_seconds"] == 1800
    assert proof["net_discharge_w"] == 4
    assert (
        dt_util.parse_datetime("2026-11-18T04:00:00Z").timestamp()
        < proof["valid_until"]
        < dt_util.parse_datetime("2026-11-18T06:00:00Z").timestamp()
    )
    freezer.tick(30)
    assert _diagnostic(runtime) == proof
    freezer.tick(1)
    assert _reason(runtime) == "net_telemetry_unavailable"


async def test_watts_are_time_weighted_with_no_credit_for_tiny_positive_samples(
    runtime, hass, freezer
):
    # 1728 seconds at 1 W. Then 100 seconds at 9 W using the upper of the
    # interval endpoints. Small positive readings never subtract watt-seconds.
    await _episode(hass, freezer, n=60, seconds=29, watts=-1)
    await _report(hass, freezer, seconds=17, watts=1)
    for seconds, watts in [(10, -9), (25, -9), (25, -9), (25, -9), (15, 1)]:
        await _report(hass, freezer, seconds=seconds, watts=watts)
    proof = _diagnostic(runtime)
    assert proof["observed_seconds"] == 1828
    assert proof["net_discharge_w"] == pytest.approx((1728 + 100 * 9) / 1828)
    await _report(hass, freezer, seconds=10, watts=-9)
    assert _diagnostic(runtime)["net_discharge_w"] == 9  # Fresh upper guard.
    await _report(hass, freezer, seconds=10, watts=-20)
    assert _diagnostic(runtime) == {
        "reason": "net_episode_too_short",
        "observed_seconds": 0,
    }
    await _episode(hass, freezer, n=60, watts=-20)
    assert _diagnostic(runtime)["net_discharge_w"] == 20
    await _report(hass, freezer, seconds=10, watts=1.1)
    assert _reason(runtime) == "net_charging_detected"


@pytest.mark.parametrize(
    ("entity", "invalid", "returned"),
    [
        (_PSU24, "off", "on"),
        (_PSU48, "off", "on"),
        (_DCDC, "on", "off"),
        (_LIMIT, "1", "0"),
        (_BLOCK, "off", "on"),
    ],
)
async def test_actor_flank_between_samples_breaks_continuity(
    runtime, hass, freezer, entity, invalid, returned
):
    runtime.owner.raw_config["inverter_block_switch_entity"] = _BLOCK
    runtime.start_observing()
    await _episode(hass, freezer)
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    _set(hass, entity, invalid)
    assert _reason(runtime) == "net_mode_unconfirmed"
    _set(hass, entity, returned)
    await hass.async_block_till_done()
    assert _reason(runtime) == "net_telemetry_unavailable"
    await _episode(hass, freezer, n=60)
    assert _reason(runtime) == "net_episode_too_short"
    await _report(hass, freezer)
    assert _diagnostic(runtime)["net_discharge_w"] == 4


async def test_assumed_or_restored_state_and_gap_in_any_required_channel_disqualify(
    runtime, hass, freezer
):
    _set(hass, _LIMIT, 0, assumed_state=True)
    await _episode(hass, freezer)
    assert _reason(runtime) == "net_mode_unconfirmed"
    _set(hass, _LIMIT, 0)
    await _episode(hass, freezer, watts=0)
    assert _diagnostic(runtime)["net_discharge_w"] == 0
    _set(hass, _VOLTAGE, 49.5, restored=True)
    await hass.async_block_till_done()
    assert _reason(runtime) == "net_telemetry_unavailable"
    await _episode(hass, freezer)
    assert _diagnostic(runtime)["net_discharge_w"] == 4
    await _report(hass, freezer, seconds=10, skip=(_VOLTAGE,))
    await _report(hass, freezer, seconds=21)
    assert _diagnostic(runtime)["observed_seconds"] == 0  # 31 s voltage gap.


async def test_voltage_gate_and_soc_coordinate_are_independent_of_net_power(
    runtime, hass, freezer
):
    await _episode(hass, freezer, watts=0, voltage=49.57)
    assert _reason(runtime) == "net_voltage_above_threshold"
    await _episode(hass, freezer, watts=0, voltage=49.56)
    assert _diagnostic(runtime)["net_discharge_w"] == 0
    await _report(hass, freezer, soc=89, watts=0)
    assert runtime.monitor.phase != "tracking"
    assert _reason(runtime) == "net_coordinate_unstable"


async def test_daylight_veto_and_pv_override_with_ac_source_guards(
    runtime, hass, freezer
):
    freezer.move_to("2026-11-18T11:00:00Z")
    await _episode(hass, freezer)
    assert _reason(runtime) == "net_daylight"
    cfg = runtime.owner.raw_config
    cfg["operation_pv_power_entity"] = _PV
    cfg["live_ac_input_power_entity"] = _AC_IN
    cfg["live_ac_output_power_entity"] = _AC_OUT
    assert _reason(runtime) == "net_telemetry_unavailable"

    inputs = {_PV: 0, _AC_IN: 100, _AC_OUT: 100}
    await _episode(hass, freezer, **inputs)
    assert _diagnostic(runtime) == {"observed_seconds": 1800, "net_discharge_w": 4}
    freezer.tick(5)
    _set(hass, _PV, 20)
    _set(hass, _PV, 0)
    await hass.async_block_till_done()
    assert _reason(runtime) == "net_episode_too_short"
    await _report(hass, freezer, **(inputs | {_PV: 1}))
    assert _reason(runtime) == "net_pv_detected"
    await _report(hass, freezer, **(inputs | {_AC_IN: 101}))
    assert _reason(runtime) == "net_ac_flow_incompatible"
    await _report(hass, freezer, **(inputs | {_AC_IN: -10, _AC_OUT: -10}))
    assert _reason(runtime) == "net_ac_flow_incompatible"
    await _report(hass, freezer, **(inputs | {_AC_IN: "unknown"}))
    assert _reason(runtime) == "net_telemetry_unavailable"


async def test_ac_input_export_alone_conflicts_with_blocked_inverter(
    runtime, hass, freezer
):
    cfg = runtime.owner.raw_config
    cfg.update(
        operation_pv_power_entity=_PV,
        live_ac_input_power_entity=_AC_IN,
        live_ac_output_power_entity=_AC_OUT,
    )
    runtime.start_observing()
    inputs = {_PV: 0, _AC_IN: 0, _AC_OUT: 0}
    await _episode(hass, freezer, **inputs)
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    await _report(hass, freezer, **(inputs | {_AC_IN: -25}))
    assert _reason(runtime) == "net_ac_flow_incompatible"
    await _report(hass, freezer, **inputs)
    assert _reason(runtime) == "net_episode_too_short"


@pytest.mark.parametrize(
    ("ac_input", "ac_output", "qualifies"),
    [
        (0, 25, False),
        (25, 0, False),
        (20, 25, False),
        (25, 25, True),
        (0, 0, True),
        (None, 25, False),
        (25, None, False),
        (None, -25, False),
        (-25, None, False),
        (None, 0, True),
        (0, None, True),
        (0, 1e-15, False),
        (0, -1e-15, False),
        (None, None, True),
    ],
)
async def test_ac_port_power_must_prove_passthrough(
    runtime, hass, freezer, ac_input, ac_output, qualifies
):
    cfg = runtime.owner.raw_config
    cfg["operation_pv_power_entity"] = _PV
    signals = {_PV: 0}
    for key, entity, reading in (
        ("live_ac_input_power_entity", _AC_IN, ac_input),
        ("live_ac_output_power_entity", _AC_OUT, ac_output),
    ):
        if reading is not None:
            cfg[key] = entity
            signals[entity] = reading
    runtime.start_observing()
    await _episode(hass, freezer, **signals)
    if qualifies:
        assert _diagnostic(runtime)["observed_seconds"] == 1800
    else:
        assert _reason(runtime) == "net_ac_flow_incompatible"


def _bind_ac_ports(runtime, **overrides):
    runtime.owner.raw_config.update(
        operation_pv_power_entity=_PV,
        live_ac_input_power_entity=_AC_IN,
        live_ac_output_power_entity=_AC_OUT,
        **overrides,
    )
    runtime.start_observing()


@pytest.mark.parametrize(("first", "second"), [(_AC_IN, _AC_OUT), (_AC_OUT, _AC_IN)])
async def test_equal_ac_ports_change_together_without_breaking_coverage(
    runtime, hass, freezer, first, second
):
    _bind_ac_ports(runtime)
    await _report(hass, freezer, **{_PV: 0, first: 25, second: 25})
    for index in range(60):
        watts = 26 - index % 2
        freezer.tick(30)
        _set(hass, first, watts)
        assert _reason(runtime) == "net_publication_unconfirmed"
        await _report(hass, freezer, seconds=0, **{_PV: 0, second: watts})
    assert _diagnostic(runtime)["observed_seconds"] == 1800


@pytest.mark.parametrize("proven_at_same_time", [False, True])
async def test_unresolved_or_proven_ac_mismatch_breaks_previous_coverage(
    runtime, hass, freezer, proven_at_same_time
):
    _bind_ac_ports(runtime)
    await _episode(hass, freezer, **{_PV: 0, _AC_IN: 25, _AC_OUT: 25})
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    freezer.tick(5)
    _set(hass, _AC_IN, 26)
    assert _reason(runtime) == "net_publication_unconfirmed"
    if proven_at_same_time:
        _set(hass, _AC_OUT, 25)
        assert _reason(runtime) == "net_ac_flow_incompatible"
        # Correction on the same clock tick cannot erase the observed mismatch.
        await _report(hass, freezer, seconds=0, **{_PV: 0, _AC_OUT: 26})
    else:
        # Even the counterpart's very next event cannot undo an elapsed gap.
        await _report(hass, freezer, seconds=1, **{_PV: 0, _AC_OUT: 26})
    assert _reason(runtime) == "net_episode_too_short"


async def test_unresolved_ac_pair_cannot_be_used_on_later_read(runtime, hass, freezer):
    _bind_ac_ports(runtime)
    await _episode(hass, freezer, **{_PV: 0, _AC_IN: 25, _AC_OUT: 25})
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    freezer.tick(5)
    _set(hass, _AC_IN, 26)
    assert _reason(runtime) == "net_publication_unconfirmed"
    freezer.tick(1)
    assert _reason(runtime) == "net_publication_unconfirmed"
    await _report(hass, freezer, seconds=0, **{_PV: 0, _AC_OUT: 26})
    assert _reason(runtime) == "net_episode_too_short"


@pytest.mark.parametrize("reports_since_previous_pair", [5, 30])
@pytest.mark.parametrize("diagnostic_between_deliveries", [False, True])
async def test_late_delivery_at_same_original_time_preserves_ac_coverage(
    runtime, hass, freezer, reports_since_previous_pair, diagnostic_between_deliveries
):
    _bind_ac_ports(runtime)
    await _episode(hass, freezer, n=121, **{_PV: 0, _AC_IN: 25, _AC_OUT: 25})
    assert _diagnostic(runtime)["observed_seconds"] == 3600
    freezer.tick(reports_since_previous_pair)
    original = dt_util.utcnow().timestamp()
    _set(hass, _AC_IN, 26)
    for _ in range(2):
        freezer.tick(0.001)
        if diagnostic_between_deliveries:
            assert _reason(runtime) == "net_publication_unconfirmed"
    for entity, value in {
        _AC_OUT: 26,
        _PV: 0,
        _POWER: -4,
        _VOLTAGE: 49.5,
        _SOC: 69,
    }.items():
        _set(hass, entity, value, timestamp=original)
    await hass.async_block_till_done()
    assert (
        _diagnostic(runtime)["observed_seconds"] == 3600 + reports_since_previous_pair
    )


async def test_expired_ac_pair_does_not_restore_previous_episode(
    runtime, hass, freezer
):
    _bind_ac_ports(runtime)
    await _episode(hass, freezer, **{_PV: 0, _AC_IN: 25, _AC_OUT: 25})
    freezer.tick(5)
    original = dt_util.utcnow().timestamp()
    _set(hass, _AC_IN, 26)
    freezer.tick(30)
    assert _reason(runtime) == "net_publication_unconfirmed"
    freezer.tick(0.001)
    assert _reason(runtime) == "net_telemetry_unavailable"
    _set(hass, _AC_OUT, 26, timestamp=original)
    await _report(hass, freezer, seconds=0, **{_PV: 0, _AC_IN: 26, _AC_OUT: 26})
    assert _diagnostic(runtime)["observed_seconds"] == 0


async def test_ac_port_comparison_accepts_only_watt_unit_rounding(
    runtime, hass, freezer
):
    _bind_ac_ports(runtime)
    for _ in range(61):
        freezer.tick(30)
        _set(hass, _AC_IN, 1.001, unit_of_measurement="kW")
        await _report(hass, freezer, seconds=0, **{_PV: 0, _AC_OUT: 1001})
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    _set(hass, _AC_IN, 1001, unit_of_measurement="W")
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    freezer.tick(5)
    _set(hass, _AC_IN, 1.001, unit_of_measurement="kW")
    await _report(hass, freezer, seconds=0, **{_PV: 0, _AC_OUT: 1001.0000001})
    assert _reason(runtime) == "net_ac_flow_incompatible"


@pytest.mark.parametrize(
    "balance_role", ["ac_balance_in_entities", "ac_balance_out_entities"]
)
async def test_ac_port_sensors_can_participate_in_ac_balance(
    runtime, hass, freezer, balance_role
):
    _bind_ac_ports(runtime, **{balance_role: [_AC_IN, _AC_OUT]})
    await _episode(hass, freezer, **{_PV: 0, _AC_IN: 25, _AC_OUT: 25})
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    runtime.owner.raw_config["dc_balance_in_entities"] = [_AC_OUT]
    assert _reason(runtime) == "net_topology_unavailable"


@pytest.mark.parametrize("duplicate_port", [_AC_IN, _AC_OUT])
async def test_ac_port_cannot_be_both_inflow_and_outflow(
    runtime, hass, freezer, duplicate_port
):
    _bind_ac_ports(
        runtime, ac_balance_in_entities=[_AC_OUT], ac_balance_out_entities=[_AC_IN]
    )
    await _episode(hass, freezer, **{_PV: 0, _AC_IN: 25, _AC_OUT: 25})
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    opposing_key = (
        "ac_balance_in_entities"
        if duplicate_port == _AC_IN
        else "ac_balance_out_entities"
    )
    runtime.owner.raw_config[opposing_key].append(duplicate_port.upper())
    assert _reason(runtime) == "net_topology_unavailable"


@pytest.mark.parametrize(
    ("entity", "connected", "interruption", "attributes", "rejection"),
    [
        (_GRID, "AC_INPUT_1", "DISCONNECTED", {}, "net_mode_unconfirmed"),
        (_GRID, "AC_INPUT_1", "NOT_CONNECTED", {}, "net_mode_unconfirmed"),
        (_GRID, "AC_INPUT_1", "unknown", {}, "net_mode_unconfirmed"),
        (_GRID, "AC_INPUT_1", "AC_INPUT_1", {"restored": True}, "net_mode_unconfirmed"),
        (
            _GRID,
            "AC_INPUT_1",
            "AC_INPUT_1",
            {"assumed_state": True},
            "net_mode_unconfirmed",
        ),
        (_GRID, "AC_INPUT_1", "aC_iNpUt_2", {}, "net_episode_too_short"),
        ("binary_sensor.grid", "on", "off", {}, "net_mode_unconfirmed"),
    ],
)
async def test_grid_feedback_requires_new_evidence_after_interruption_or_source_switch(
    runtime, hass, freezer, entity, connected, interruption, attributes, rejection
):
    runtime.owner.raw_config["reserve_grid_available_entity"] = entity
    runtime.start_observing()
    await _episode(hass, freezer, **{entity: connected})
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    freezer.tick(5)
    _set(hass, entity, interruption, **attributes)
    await hass.async_block_till_done()
    assert _reason(runtime) == rejection
    await _report(hass, freezer, seconds=0, **{entity: connected})
    assert _reason(runtime) == "net_episode_too_short"
    await _episode(hass, freezer, n=60, **{entity: connected})
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    await _report(hass, freezer, seconds=30)
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    await _report(hass, freezer, seconds=1)
    assert _reason(runtime) == "net_mode_unconfirmed"
    await _report(hass, freezer, seconds=0, **{entity: connected})
    assert _diagnostic(runtime)["observed_seconds"] == 0


async def test_grid_disconnect_and_rebound_between_battery_reports_resets_immediately(
    runtime, hass, freezer
):
    runtime.owner.raw_config["reserve_grid_available_entity"] = _GRID
    runtime.start_observing()
    await _episode(hass, freezer, **{_GRID: "AC_INPUT_1"})
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    freezer.tick(5)
    _set(hass, _GRID, "DISCONNECTED")
    _set(hass, _GRID, "AC_INPUT_1")
    await _report(hass, freezer, seconds=5, **{_GRID: "AC_INPUT_1"})
    assert _diagnostic(runtime)["observed_seconds"] == 5


async def test_equivalent_kw_values_and_unconfirmed_queued_publication(
    runtime, hass, freezer, monkeypatch
):
    for _ in range(61):
        freezer.tick(30)
        _set(hass, _POWER, -0.004, unit_of_measurement="kW")
        await _report(hass, freezer, seconds=0, skip=(_POWER,))
    assert _diagnostic(runtime)["net_discharge_w"] == 4
    freezer.tick(5)
    # Withhold the original publication while the HA state has already moved.
    queued = []
    with monkeypatch.context() as patch:
        patch.setattr(runtime._net_discharge, "publication", queued.append)
        _set(hass, _POWER, -0.006, unit_of_measurement="kW")
        await hass.async_block_till_done()
        assert _reason(runtime) == "net_publication_unconfirmed"
    runtime._net_discharge.publication(queued.pop())
    assert _diagnostic(runtime)["net_discharge_w"] == 6
    for value in (float("nan"), float("inf"), -5000):
        freezer.tick(1)
        _set(hass, _POWER, value)
        await hass.async_block_till_done()
        assert _reason(runtime) == "net_telemetry_unavailable"


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("dcdc_switch_entity", None),
        ("battery_voltage_entity", None),
        ("operation_pv_power_entity", _POWER.upper()),
        ("operation_house_power_entity", _POWER),
        ("inverter_block_switch_entity", _PSU48),
        ("reserve_grid_available_entity", _PSU48),
        ("dc_load_entity", _POWER.upper()),
        ("ac_load_entity", _POWER),
        ("dc_balance_out_entities", [_POWER]),
        ("ac_balance_in_entities", [_POWER]),
        ("psu48_output_voltage_v", 61),
    ],
)
async def test_binding_and_aliased_channels_do_not_reuse_observation(
    runtime, hass, freezer, key, value
):
    await _episode(hass, freezer)
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    config = runtime.owner.raw_config
    previous = config.get(key)
    config[key] = value
    assert _reason(runtime) == "net_topology_unavailable"
    config[key] = previous
    runtime.start_observing()
    await _report(hass, freezer)
    assert _diagnostic(runtime)["observed_seconds"] == 0


async def test_measured_pv_may_also_be_ac_balance_inflow(runtime, hass, freezer):
    cfg = runtime.owner.raw_config
    cfg.update(operation_pv_power_entity=_PV, ac_balance_in_entities=[_PV.upper()])
    runtime.start_observing()
    await _episode(hass, freezer, **{_PV: 0})
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    cfg["dc_load_entity"] = _PV
    assert _reason(runtime) == "net_topology_unavailable"
    cfg.pop("dc_load_entity")
    cfg["ac_balance_in_entities"].append(_POWER)
    assert _reason(runtime) == "net_topology_unavailable"
    cfg["ac_balance_in_entities"].remove(_POWER)
    cfg["ac_balance_out_entities"] = [_PV]
    assert _reason(runtime) == "net_topology_unavailable"


async def test_direct_meter_takes_priority_and_restart_requires_new_measurements(
    runtime, hass, freezer
):
    await _episode(hass, freezer)
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    runtime.owner.raw_config["support_dc48_output_power_entity"] = "sensor.psu_watts"
    assert _reason(runtime) == "net_direct_meter_configured"
    runtime.owner.raw_config.pop("support_dc48_output_power_entity")
    runtime.restore({})
    runtime.start_observing()
    assert _reason(runtime) == "net_telemetry_unavailable"
    await _episode(hass, freezer, n=60)
    assert _reason(runtime) == "net_episode_too_short"


async def test_site_change_invalidates_even_without_rebinding_config(
    runtime, hass, freezer
):
    await _episode(hass, freezer)
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    hass.config.longitude = 180
    assert _reason(runtime) == "net_telemetry_unavailable"
    await _report(hass, freezer)
    assert _reason(runtime) == "net_daylight"


async def test_astronomical_dawn_is_the_limit_of_night_evidence(runtime, hass, freezer):
    # Rebind at a polar site to exercise the finite search without a dawn.
    hass.config.latitude = 90
    runtime.start_observing()
    await _episode(hass, freezer)
    proof = _diagnostic(runtime)
    assert proof["valid_until"] - dt_util.utcnow().timestamp() == 48 * 3600
    hass.config.latitude = 48
    freezer.move_to("2026-11-18T04:00:00Z")
    runtime.start_observing()
    await _episode(hass, freezer)
    proof = _diagnostic(runtime)
    dawn_at = dt_util.utc_from_timestamp(proof["valid_until"])
    while (dawn_at - dt_util.utcnow()).total_seconds() > 15:
        await _report(hass, freezer, seconds=15)
    assert _diagnostic(runtime)["valid_until"] == proof["valid_until"]
    await _report(hass, freezer, seconds=16)
    assert _reason(runtime) == "net_daylight"


async def test_dawn_at_elevation_cannot_extend_night_into_the_next_day(
    runtime, hass, freezer
):
    hass.config.elevation = 600
    freezer.move_to("2026-11-18T02:59:30Z")
    runtime.start_observing()
    await _episode(hass, freezer, n=177, watts=0)
    proof = _diagnostic(runtime)
    assert (
        dt_util.utc_from_timestamp(proof["valid_until"]).strftime("%Y-%m-%dT%H:%M:%S")
        == "2026-11-18T04:28:47"
    )
    await _episode(hass, freezer, n=3, watts=0)
    assert dt_util.utcnow().isoformat() == "2026-11-18T04:29:30+00:00"
    assert _reason(runtime) == "net_daylight"


@pytest.mark.parametrize(
    ("latitude", "start"),
    [(65, "2026-09-02T10:59:30Z"), (90, "2026-01-28T07:24:30Z")],
)
async def test_near_polar_latitude_daylight_without_recent_twilight(
    runtime, hass, freezer, latitude, start
):
    hass.config.latitude = latitude
    hass.config.elevation = 0
    freezer.move_to(start)
    for entity, value in _SWITCHES.items():
        hass.states.async_remove(entity)
        _set(hass, entity, value)
    runtime.start_observing()
    await _episode(hass, freezer, n=121, watts=0)
    assert _reason(runtime) == "net_daylight"


def test_missing_dawn_stops_at_first_uncertified_interval(monkeypatch):
    site = (65, 180, 600)
    now = dt_util.parse_datetime("2026-04-05T12:00:00Z")
    at = now.timestamp()
    # Exercise an absent event even if date-line normalization finds one
    # for this location. The physical angle still limits the horizon.
    monkeypatch.setattr(net_discharge, "_dawn_events", lambda *_: ())
    event_bound = net_discharge._night_until(site, now)
    assert event_bound == at + 48 * 3600  # Astral has no matching dawn.
    through = net_discharge._certified_dark_until(site, at, event_bound)
    assert at + 5 * 60 <= through <= at + 9 * 60


def test_date_line_longitudes_use_the_same_site_and_dawn(runtime, hass):
    hass.config.latitude = 65
    hass.config.elevation = 600
    hass.config.longitude = -180
    west = net_discharge._Topology.from_owner(runtime.owner)
    hass.config.longitude = 180
    east = net_discharge._Topology.from_owner(runtime.owner)
    assert east == west
    assert isinstance(east, net_discharge._Topology)
    assert net_discharge._night_until(
        east.site, dt_util.parse_datetime("2026-04-05T12:00:00Z")
    ) == pytest.approx(
        dt_util.parse_datetime("2026-04-05T12:04:14.467Z").timestamp(), abs=0.001
    )


async def test_old_dawn_without_dusk_does_not_invalidate_a_new_dark_period(
    runtime, hass, freezer
):
    hass.config.latitude = 89.8
    hass.config.longitude = -180
    hass.config.elevation = 600
    freezer.move_to("2026-11-18T20:29:30Z")
    runtime.start_observing()
    await _episode(hass, freezer, watts=0)
    assert _diagnostic(runtime)["valid_until"] == (
        dt_util.utcnow().timestamp() + 48 * 3600
    )


@pytest.mark.parametrize("report_at_end", [False, True])
async def test_twilight_between_two_fresh_observations_breaks_coverage(
    runtime, hass, freezer, monkeypatch, report_at_end
):
    await _episode(hass, freezer)
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    center = dt_util.utcnow().timestamp() + 10
    # Both publications are dark. The smooth, brief excursion between
    # them must still reset the evidence, even with no scheduled crossing.
    monkeypatch.setattr(
        net_discharge, "_dark_margin", lambda _, at: abs(at - center) * 0.001 - 0.004
    )
    if report_at_end:
        await _report(hass, freezer, seconds=20)
    else:
        freezer.tick(20)
    assert _diagnostic(runtime)["observed_seconds"] == 0


async def test_no_unqualified_diagnostic_precedes_readiness(runtime):
    runtime.ready = False
    assert _reason(runtime) == "net_telemetry_unavailable"


_SETPOINT = "number.grid_target"
_GRID_POWER_KEY = "soc_expectation_grid_power_entity"
_GRID_SETPOINT_KEY = "soc_expectation_grid_setpoint_entity"
_DAY_INPUTS = {_PCC: 151, _SETPOINT: 100, _PV: 650}


def _bind_grid_observation(runtime, freezer, **overrides):
    freezer.move_to("2026-11-18T11:00:00Z")
    runtime.owner.raw_config.update(
        {
            "operation_pv_power_entity": _PV,
            _GRID_POWER_KEY: _PCC,
            _GRID_SETPOINT_KEY: _SETPOINT,
        }
        | overrides
    )
    runtime.start_observing()


async def _grid_report(hass, freezer, *, watts=0, **extras):
    await _report(hass, freezer, watts=watts, **(_DAY_INPUTS | extras))


async def test_grid_import_requires_thirty_minutes_of_common_coverage(
    runtime, hass, freezer
):
    _bind_grid_observation(
        runtime,
        freezer,
        live_ac_grid_power_entity=_PCC,
        ac_balance_in_entities=[_PCC.upper()],
        operation_import_power_entity=_PCC,
    )
    await _episode(hass, freezer, n=60, watts=-1, **_DAY_INPUTS)
    assert _diagnostic(runtime) == {
        "reason": "net_episode_too_short",
        "observed_seconds": 1770,
    }
    await _grid_report(hass, freezer, skip=(_PCC,), watts=1)
    assert _diagnostic(runtime)["observed_seconds"] == 1770
    _set(hass, _PCC, 0.151, unit_of_measurement="kW")
    await hass.async_block_till_done()
    assert _diagnostic(runtime) == {
        "evidence_mode": "grid_import",
        "observed_seconds": 1800,
        "net_discharge_w": 1,
        "grid_import_w": 151,
        "grid_setpoint_w": 100,
        "grid_margin_w": 50,
    }
    await _grid_report(hass, freezer, seconds=30, skip=(_PCC,))
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    await _grid_report(hass, freezer, seconds=1, skip=(_PCC,))
    assert _reason(runtime) == "net_grid_power_unavailable"


@pytest.mark.parametrize(
    ("grid_w", "setpoint_w", "watts", "reason"),
    [
        (-1, -100, 0, "net_grid_not_importing"),
        (0, -100, 0, "net_grid_not_importing"),
        (1, -100, 0, "net_grid_margin_not_met"),
        (50, -100, 0, "net_grid_margin_not_met"),
        (149, 100, 0, "net_grid_margin_not_met"),
        (150, 100, 0, "net_grid_margin_not_met"),
        (151, 100, -1.01, "net_battery_not_idle"),
        (151, 100, 1.01, "net_charging_detected"),
    ],
)
async def test_grid_import_thresholds_restart_after_short_interruptions(
    runtime, hass, freezer, grid_w, setpoint_w, watts, reason
):
    _bind_grid_observation(runtime, freezer)
    await _episode(hass, freezer, watts=0, **_DAY_INPUTS)
    assert _diagnostic(runtime)["evidence_mode"] == "grid_import"
    await _grid_report(
        hass, freezer, seconds=5, watts=watts, **{_PCC: grid_w, _SETPOINT: setpoint_w}
    )
    assert _reason(runtime) == reason
    await _grid_report(hass, freezer, seconds=0)
    assert _diagnostic(runtime)["observed_seconds"] == 0


async def test_grid_setpoint_changes_reset_even_above_both_targets(
    runtime, hass, freezer
):
    _bind_grid_observation(runtime, freezer)
    await _episode(hass, freezer, watts=0, **_DAY_INPUTS)
    assert _diagnostic(runtime)["evidence_mode"] == "grid_import"
    freezer.tick(5)
    _set(hass, _SETPOINT, -1)
    _set(hass, _SETPOINT, 100)
    await _grid_report(hass, freezer, seconds=0, skip=(_SETPOINT,))
    assert _diagnostic(runtime)["observed_seconds"] == 0
    await _episode(hass, freezer, n=59, watts=0, **_DAY_INPUTS)
    assert _reason(runtime) == "net_episode_too_short"
    await _grid_report(hass, freezer)
    assert _diagnostic(runtime)["grid_setpoint_w"] == 100
    freezer.tick(5)
    _set(hass, _SETPOINT, 100, restored=True)
    await hass.async_block_till_done()
    assert _reason(runtime) == "net_grid_setpoint_unavailable"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({_GRID_POWER_KEY: None}, "net_grid_power_unavailable"),
        ({_GRID_SETPOINT_KEY: None}, "net_grid_setpoint_unavailable"),
        ({_GRID_POWER_KEY: _PV.upper()}, "net_grid_topology_unavailable"),
        ({_GRID_SETPOINT_KEY: _PCC}, "net_grid_topology_unavailable"),
        ({_GRID_SETPOINT_KEY: _LIMIT}, "net_grid_topology_unavailable"),
        ({"operation_house_power_entity": _PCC}, "net_grid_topology_unavailable"),
        ({"dc_balance_in_entities": [_PCC]}, "net_grid_topology_unavailable"),
        ({"support_dc48_power_entity": _PCC}, "net_grid_topology_unavailable"),
    ],
)
async def test_bad_grid_roles_do_not_delay_independent_pv_zero_path(
    runtime, hass, freezer, overrides, reason
):
    _bind_grid_observation(runtime, freezer, **overrides)
    await _grid_report(hass, freezer)
    assert _reason(runtime) == reason
    await _episode(hass, freezer, n=61, watts=-4, **{_PV: 0})
    assert _diagnostic(runtime) == {"net_discharge_w": 4, "observed_seconds": 1800}


async def test_grid_binding_change_preserves_current_night_evidence(
    runtime, hass, freezer
):
    await _episode(hass, freezer)
    proof = _diagnostic(runtime)
    runtime.owner.raw_config.update(
        {_GRID_POWER_KEY: _PCC, _GRID_SETPOINT_KEY: _SETPOINT}
    )
    assert _diagnostic(runtime) == proof
    runtime.owner.raw_config[_GRID_SETPOINT_KEY] = _PCC
    assert _diagnostic(runtime) == proof


async def test_grid_import_cannot_override_independent_ac_conversion(
    runtime, hass, freezer
):
    _bind_grid_observation(
        runtime,
        freezer,
        live_ac_input_power_entity=_AC_IN,
        live_ac_output_power_entity=_AC_OUT,
    )
    await _episode(
        hass, freezer, watts=0, **(_DAY_INPUTS | {_AC_IN: 100, _AC_OUT: 100})
    )
    assert _diagnostic(runtime)["evidence_mode"] == "grid_import"
    await _grid_report(hass, freezer, **{_AC_IN: 100, _AC_OUT: 50})
    assert _reason(runtime) == "net_ac_flow_incompatible"
    await _grid_report(hass, freezer, **{_AC_IN: -100, _AC_OUT: -50})
    assert _reason(runtime) == "net_ac_flow_incompatible"
    await _grid_report(hass, freezer, **{_AC_IN: 100, _AC_OUT: -100})
    assert _reason(runtime) == "net_ac_flow_incompatible"


@pytest.mark.parametrize(("first", "second"), [(_AC_IN, _AC_OUT), (_AC_OUT, _AC_IN)])
async def test_grid_import_accepts_staggered_publications_of_reverse_passthrough(
    runtime, hass, freezer, first, second
):
    _bind_grid_observation(
        runtime,
        freezer,
        live_ac_input_power_entity=_AC_IN,
        live_ac_output_power_entity=_AC_OUT,
    )
    inputs = _DAY_INPUTS | {_PCC: 300, _SETPOINT: 0, _PV: 0}
    await _episode(hass, freezer, watts=0, **(inputs | {_AC_IN: 100, _AC_OUT: 100}))
    assert _diagnostic(runtime)["observed_seconds"] == 1800
    freezer.tick(5)
    original = dt_util.utcnow().timestamp()
    _set(hass, first, -100)
    await hass.async_block_till_done()
    assert _reason(runtime) == "net_publication_unconfirmed"
    freezer.tick(0.001)
    for entity, value in (
        inputs | {second: -0.1, _POWER: 0, _VOLTAGE: 49.5, _SOC: 69}
    ).items():
        options = {"unit_of_measurement": "kW"} if entity == second else {}
        _set(hass, entity, value, timestamp=original, **options)
    await hass.async_block_till_done()
    proof = _diagnostic(runtime)
    assert proof["evidence_mode"] == "grid_import"
    assert proof["observed_seconds"] == 1805

    # Without the site-grid evidence, the PV-zero path still rejects backflow.
    runtime.owner.raw_config.pop(_GRID_POWER_KEY)
    runtime.owner.raw_config.pop(_GRID_SETPOINT_KEY)
    assert _reason(runtime) == "net_ac_flow_incompatible"


async def test_grid_setpoint_report_jitter_and_missed_report(runtime, hass, freezer):
    _bind_grid_observation(runtime, freezer)
    await _episode(hass, freezer, watts=0, **_DAY_INPUTS)
    assert _diagnostic(runtime)["evidence_mode"] == "grid_import"
    for seconds in (30, 0.001180):
        await _grid_report(hass, freezer, seconds=seconds, skip=(_SETPOINT,))
        assert _diagnostic(runtime)["observed_seconds"] == 1800
    _set(hass, _SETPOINT, 100)
    await hass.async_block_till_done()
    assert _diagnostic(runtime)["observed_seconds"] == pytest.approx(1830.001180)
    for seconds in (30, 1, 29, 5):
        await _grid_report(hass, freezer, seconds=seconds, skip=(_SETPOINT,))
        assert _diagnostic(runtime)["observed_seconds"] == pytest.approx(1830.001180)
    await _grid_report(hass, freezer, seconds=1, skip=(_SETPOINT,))
    assert _reason(runtime) == "net_grid_setpoint_unavailable"
    _set(hass, _SETPOINT, 100)
    await hass.async_block_till_done()
    assert _diagnostic(runtime)["observed_seconds"] == 0


async def test_grid_power_requires_units_and_usable_setpoint_feedback(
    runtime, hass, freezer
):
    setpoint_sensor = "sensor.site_grid_target"
    _bind_grid_observation(runtime, freezer, **{_GRID_SETPOINT_KEY: setpoint_sensor})
    await _grid_report(hass, freezer, skip=(_SETPOINT,), **{setpoint_sensor: 100})
    assert _reason(runtime) == "net_grid_setpoint_unavailable"
    _set(hass, setpoint_sensor, 0.1, unit_of_measurement="kW")
    await hass.async_block_till_done()
    assert _reason(runtime) == "net_episode_too_short"
    _set(hass, _PCC, 151, unit_of_measurement=None)
    await hass.async_block_till_done()
    assert _reason(runtime) == "net_grid_power_unavailable"


async def test_grid_import_aliases_independent_live_ac_channel(runtime, hass, freezer):
    _bind_grid_observation(runtime, freezer, live_ac_input_power_entity=_PCC)
    await _episode(hass, freezer, watts=0, **_DAY_INPUTS)
    assert _diagnostic(runtime)["evidence_mode"] == "grid_import"
    runtime.owner.raw_config["support_dc48_output_power_entity"] = "sensor.support_dc48"
    assert _reason(runtime) == "net_direct_meter_configured"


@pytest.mark.parametrize(
    ("input_entity", "output_entity", "independent_port"),
    [
        (None, _AC_OUT, _AC_OUT),
        (_AC_IN, None, _AC_IN),
    ],
)
async def test_grid_import_cannot_override_reverse_flow_at_independent_port(
    runtime, hass, freezer, input_entity, output_entity, independent_port
):
    _bind_grid_observation(
        runtime,
        freezer,
        live_ac_input_power_entity=input_entity,
        live_ac_output_power_entity=output_entity,
    )
    inputs = {_PCC: 300, _SETPOINT: 0, independent_port: 100}
    await _episode(hass, freezer, watts=0, **(_DAY_INPUTS | inputs))
    assert _diagnostic(runtime)["evidence_mode"] == "grid_import"

    # The site meter and this converter port measure different boundaries.
    # Forward readings can differ, while reverse flow here is a contradiction.
    await _grid_report(hass, freezer, seconds=5, **(inputs | {independent_port: -100}))
    assert _reason(runtime) == "net_ac_flow_incompatible"
    await _grid_report(hass, freezer, seconds=0, **inputs)
    assert _diagnostic(runtime)["observed_seconds"] == 0


@pytest.mark.parametrize(
    ("input_entity", "output_entity", "other_port"),
    [(_PCC, _AC_OUT, _AC_OUT), (_AC_IN, _PCC, _AC_IN)],
)
@pytest.mark.parametrize("pcc_first", [True, False])
async def test_grid_import_preserves_both_configured_port_roles_with_pcc_alias(
    runtime, hass, freezer, input_entity, output_entity, other_port, pcc_first
):
    _bind_grid_observation(
        runtime,
        freezer,
        live_ac_input_power_entity=input_entity,
        live_ac_output_power_entity=output_entity,
    )
    inputs = _DAY_INPUTS | {_PCC: 300, _SETPOINT: 0, other_port: 100}
    await _grid_report(hass, freezer, **inputs)
    assert _reason(runtime) == "net_ac_flow_incompatible"
    await _grid_report(hass, freezer, **(inputs | {other_port: -100}))
    assert _reason(runtime) == "net_ac_flow_incompatible"
    await _grid_report(hass, freezer, **(inputs | {_PCC: -100, other_port: -100}))
    assert _reason(runtime) == "net_grid_not_importing"
    await _episode(hass, freezer, watts=0, **(inputs | {other_port: 300}))
    assert _diagnostic(runtime)["observed_seconds"] == 1800

    first, second = (_PCC, other_port) if pcc_first else (other_port, _PCC)
    freezer.tick(5)
    original = dt_util.utcnow().timestamp()
    _set(hass, first, 320)
    await hass.async_block_till_done()
    assert _reason(runtime) == "net_publication_unconfirmed"
    freezer.tick(0.001)
    for entity, value in (
        inputs | {second: 320, _POWER: 0, _VOLTAGE: 49.5, _SOC: 69}
    ).items():
        if entity != first:
            _set(hass, entity, value, timestamp=original)
    await hass.async_block_till_done()
    proof = _diagnostic(runtime)
    assert proof["evidence_mode"] == "grid_import"
    assert proof["grid_import_w"] == 320
    assert proof["observed_seconds"] == 1805


async def test_grid_evidence_waits_for_original_reports_in_new_actor_generation(
    runtime, hass, freezer
):
    _bind_grid_observation(runtime, freezer)
    await _episode(hass, freezer, watts=0, **_DAY_INPUTS)
    assert _diagnostic(runtime)["evidence_mode"] == "grid_import"
    freezer.tick(5)
    _set(hass, _PSU48, "off")
    assert _reason(runtime) == "net_mode_unconfirmed"
    _set(hass, _PSU48, "on")
    await _grid_report(hass, freezer, seconds=1, skip=(_SETPOINT,))
    assert _reason(runtime) == "net_grid_setpoint_unavailable"
    await _grid_report(hass, freezer, seconds=0)
    assert _diagnostic(runtime)["observed_seconds"] == 0


async def test_grid_fallback_is_available_alongside_pv_zero_evidence(
    runtime, hass, freezer
):
    _bind_grid_observation(runtime, freezer)
    await _episode(hass, freezer, watts=0, **(_DAY_INPUTS | {_PV: 0}))
    assert _diagnostic(runtime) == {"net_discharge_w": 0, "observed_seconds": 1800}
    fallback = runtime._net_discharge.diagnostic(dt_util.utcnow(), grid_only=True)
    assert fallback["evidence_mode"] == "grid_import"
    assert fallback["observed_seconds"] == 1800
    freezer.tick(5)
    _set(hass, _PCC, 150)
    await hass.async_block_till_done()
    fallback = runtime._net_discharge.diagnostic(dt_util.utcnow(), grid_only=True)
    assert fallback == {"reason": "net_grid_margin_not_met"}
    assert _diagnostic(runtime)["observed_seconds"] == 1800
