"""F-ADAPTIVE-48V-GATE: live authority, learned future and honest power."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from core.model import (
    HourSlot,
    PlanInputs,
    ReserveParams,
    SupportParams,
    SystemConfig,
    VoltageGateBand,
)
from core.replay import decode, encode
from core.simulate import simulate, step_hour
from core.voltage_gate import forecast_gate, gate_band, voltage_request

NOW = datetime(2026, 10, 14, tzinfo=UTC)
AT = NOW.timestamp()


def config(**kw):
    args = dict(
        configured=True,
        coordinated=True,
        adaptive_voltage_gate=True,
        native48_base_w=35,
        psu48_bus_voltage_v=50,
        psu48_live_until=AT + 300,
        psu48_max_power_w=57,
        psu48_gate_bands=(VoltageGateBand(0.25, 1, 75, 75, 80, 1, 7, AT + 86400),),
    )
    args.update(kw)
    return SystemConfig(support=SupportParams(**args))


def slot(offset=0):
    return HourSlot(0, NOW + timedelta(hours=offset), 1, 0, 0, 100, 60)


@pytest.mark.parametrize(
    ("voltage", "active", "expected", "reason"),
    [
        (50, True, False, "above_off_voltage"),
        (49.5, False, True, "below_on_voltage"),
        (49.7, False, False, "hysteresis_hold"),
        (49.7, True, True, "hysteresis_hold"),
        (None, False, True, "voltage_unavailable"),
    ],
)
def test_voltage_hysteresis(voltage, active, expected, reason):
    assert voltage_request(voltage, active, 49.56, 49.8) == (expected, reason)


def test_high_voltage_disables_idle_psu_but_retains_need_and_dc24():
    flow = step_hour(config(), 82, slot(), 100, dc24_from_grid=True, dc48_support=True)
    assert flow.support_dc24 and flow.support_dc48_requested
    assert not flow.support_dc48 and flow.psu48_delivered_wh == 0
    assert flow.battery_discharge_wh == pytest.approx(35 / 0.97)
    assert not flow.inverter_on


def test_future_threshold_uses_current_soc_scale_not_present_voltage():
    flow = step_hour(config(), 74, slot(1), 100, dc24_from_grid=True, dc48_support=True)
    assert flow.support_dc48 and flow.psu48_delivered_wh == 35
    assert flow.battery_discharge_wh == 0
    assert flow.battery_charge_wh == 0  # learning a gate cannot prove charging current
    stress = step_hour(
        config(), 74, slot(1), 100, dc24_from_grid=True, dc48_support=True, pv_scale=0.8
    )
    assert stress.psu48_delivered_wh == 0 and stress.battery_discharge_wh > 0
    high = step_hour(config(), 81, slot(1), 100, dc24_from_grid=True, dc48_support=True)
    assert not high.support_dc48


def test_forecast_hysteresis_fallback_and_no_load_extrapolation():
    s = config().support
    assert forecast_gate(s, 78, 35, AT + 3600, True) == (True, None)
    assert forecast_gate(s, 81, 35, AT + 3600, True) == (False, None)
    assert forecast_gate(s, 74, 200, AT + 3600, False) == (False, None)
    assert forecast_gate(s, 30, 200, AT + 3600, False) == (True, None)
    assert gate_band(s, 0.7, AT + 86401) is None
    missing = replace(s, gate_soc_percent=None, psu48_gate_bands=())
    assert forecast_gate(missing, 30, 35, AT + 3600, False) == (False, None)
    assert forecast_gate(
        replace(
            s, psu48_gate_bands=(VoltageGateBand(0.25, 1, valid_until=AT + 86400),)
        ),
        30,
        35,
        AT + 3600,
        False,
    ) == (True, None)


@pytest.mark.parametrize("soc,manual", [(5, False), (82, True)])
def test_protection_and_manual_keep_readiness_without_credit(soc, manual):
    flow = step_hour(
        config(dc48_forced_on=manual),
        soc,
        slot(),
        100,
        dc24_from_grid=True,
        dc48_support=True,
    )
    assert flow.support_dc48 and flow.psu48_delivered_wh == 0


def test_low_current_measurement_does_not_survive_entire_horizon():
    c = config(psu48_bus_voltage_v=49, psu48_gate_bands=())
    assert (
        step_hour(
            c, 82, slot(), 100, dc24_from_grid=True, dc48_support=True
        ).psu48_delivered_wh
        > 0
    )
    assert (
        step_hour(
            c, 82, slot(1), 100, dc24_from_grid=True, dc48_support=True
        ).psu48_delivered_wh
        == 0
    )


@pytest.mark.parametrize("reserve", [False, True])
def test_full_simulation_keeps_actual_timeline_and_replayable_learning(reserve):
    c = replace(config(), reserve=ReserveParams(enabled=reserve))
    i = PlanInputs(NOW, 82, (slot(), slot(1)))
    result = simulate(c, i, 100)
    assert not any(f.psu48_delivered_wh for f in result.flows)
    assert decode(encode(c)) == c
    if reserve:
        assert result.flows[0].support_dc48_requested
        assert not result.flows[0].support_dc48_start


@pytest.mark.parametrize(
    "kw",
    [
        dict(current_min_a=1, current_max_a=0),
        dict(current_min_a=0, current_max_a=1, on_soc=101),
        dict(current_min_a=0, current_max_a=1, samples=-1),
        dict(current_min_a=0, current_max_a=1, valid_until=float("nan")),
    ],
)
def test_invalid_learned_contract(kw):
    with pytest.raises(ValueError):
        VoltageGateBand(**kw)


@pytest.mark.parametrize(
    "kw",
    [
        dict(psu48_on_voltage_v=50, psu48_off_voltage_v=49),
        dict(psu48_gate_bands=[]),
        dict(psu48_gate_bands=(1,)),
    ],
)
def test_invalid_support_gate_contract(kw):
    with pytest.raises(ValueError):
        config(**kw)


def test_no_bus_demand_does_not_request_an_economic_grid_charge():
    c = config(native48_base_w=0, psu48_bus_voltage_v=49)
    flow = step_hour(c, 82, slot(), 100, dc24_from_grid=True, dc48_support=True)
    assert not flow.support_dc48_requested and not flow.support_dc48
    assert flow.battery_charge_wh == 0
