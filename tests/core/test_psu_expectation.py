"""An independent display projection credits only supported native DC demand."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from core.model import (
    HourFlows,
    HourSlot,
    PlanInputs,
    SupportParams,
    SwitchingInterval,
    SystemConfig,
    Trajectory,
)
from core.psu_expectation import (
    expected_soc_with_psu48,
)

NOW = datetime(2026, 1, 2, tzinfo=UTC)
CONFIG = SystemConfig(
    support=SupportParams(
        configured=True,
        coordinated=True,
        adaptive_voltage_gate=True,
        native48_base_w=35,
        psu48_max_power_w=57,
    )
)


def scenario(count=3, now=NOW):
    slots = []
    flows = []
    soc = 69.0
    for i in range(count):
        start = (now.astimezone(UTC) + timedelta(hours=i)).astimezone(now.tzinfo)
        end = (start.astimezone(UTC) + timedelta(hours=1)).astimezone(now.tzinfo)
        slot = HourSlot(i, start, 1, start.hour, 0, 100, 65)
        discharge = 35 / CONFIG.battery.eta_discharge
        next_soc = soc - CONFIG.battery.soc_percent(discharge)
        flow = HourFlows(
            soc,
            next_soc,
            100,
            0,
            0,
            discharge,
            False,
            0,
            0,
            True,
            True,
            switching_schedule=(SwitchingInterval(start, end, False, True, True),),
            psu24_delivered_wh=30,
        )
        slots.append(slot)
        flows.append(flow)
        soc = next_soc
    return PlanInputs(now, 69, tuple(slots)), Trajectory(tuple(flows), 300, 0, soc)


def test_native_discharge_can_be_avoided_only_for_the_observed_horizon():
    inputs, trajectory = scenario()
    assert expected_soc_with_psu48(
        CONFIG, inputs, trajectory, 35, 7200
    ) == pytest.approx((69, 69))
    assert trajectory.end_soc_percent == pytest.approx(66.8350515)
    small = expected_soc_with_psu48(CONFIG, inputs, trajectory, 10, 3600)
    assert small == pytest.approx((69 - 25 / 0.97 / 50,))
    assert expected_soc_with_psu48(CONFIG, inputs, trajectory, 35, 3599) == ()


@pytest.mark.parametrize(
    "w, seconds",
    [(0, 3600), (58, 3600), (float("nan"), 3600), (35, 1799), (35, float("inf"))],
)
def test_missing_or_implausible_proof_cannot_draw_a_curve(w, seconds):
    inputs, trajectory = scenario()
    assert expected_soc_with_psu48(CONFIG, inputs, trajectory, w, seconds) == ()


@pytest.mark.parametrize(
    "setting", ["adaptive_voltage_gate", "coordinated", "psu48_max_power_w"]
)
def test_legacy_or_unbounded_supply_is_not_eligible(setting):
    inputs, trajectory = scenario()
    value = None if setting == "psu48_max_power_w" else False
    cfg = replace(CONFIG, support=replace(CONFIG.support, **{setting: value}))
    assert expected_soc_with_psu48(cfg, inputs, trajectory, 35, 3600) == ()


@pytest.mark.parametrize(
    "mode",
    [
        "inverter",
        "dc24",
        "dc48",
        "dcdc",
        "unserved",
        "gap",
        "mixed",
        "short",
        "unknown",
    ],
)
def test_projection_ends_at_first_supply_or_coverage_break(mode):
    inputs, trajectory = scenario()
    first, affected, last = trajectory.flows
    (interval,) = affected.switching_schedule
    midpoint = interval.start + timedelta(minutes=30)
    changes = {
        "inverter": {"switching_schedule": (replace(interval, inverter_on=True),)},
        "dc24": {"switching_schedule": (replace(interval, support_dc24=False),)},
        "dc48": {"switching_schedule": (replace(interval, support_dc48=False),)},
        "dcdc": {"dcdc_input_wh": 1},
        "unserved": {"unserved_dc_wh": 1},
        "gap": {},
        "mixed": {
            "switching_schedule": (
                replace(interval, end=midpoint),
                replace(interval, start=midpoint, support_dc48=False),
            )
        },
        "short": {"switching_schedule": (replace(interval, end=midpoint),)},
        "unknown": {"switching_schedule": ()},
    }
    if mode == "gap":
        first_slot, affected_slot, last_slot = inputs.slots
        inputs = replace(
            inputs,
            slots=(first_slot, replace(affected_slot, start=midpoint), last_slot),
        )
    trajectory = replace(
        trajectory, flows=(first, replace(affected, **changes[mode]), last)
    )
    assert expected_soc_with_psu48(
        CONFIG, inputs, trajectory, 35, 10800
    ) == pytest.approx((69,))


def test_no_double_credit_or_extra_charging_and_physical_ceiling():
    inputs, trajectory = scenario()
    first, second, third = trajectory.flows
    assert expected_soc_with_psu48(
        CONFIG,
        inputs,
        replace(
            trajectory, flows=(replace(first, psu48_delivered_wh=25), second, third)
        ),
        35,
        3600,
    ) == pytest.approx((first.soc_end_percent + 10 / 0.97 / 50,))
    first = replace(first, battery_discharge_wh=3)
    second = replace(
        second, battery_discharge_wh=0, battery_charge_wh=100, soc_end_percent=94.98
    )
    third = replace(third, battery_discharge_wh=0, soc_end_percent=94.97)
    trajectory = replace(trajectory, flows=(first, second, third))
    assert expected_soc_with_psu48(
        CONFIG, inputs, trajectory, 35, 10800
    ) == pytest.approx((first.soc_end_percent + 0.06, 95, 94.99))


def test_projection_counts_real_hours_through_dst_and_caps_at_two_days():
    now = datetime(2026, 10, 25, 1, tzinfo=ZoneInfo("Europe/Berlin"))
    inputs, trajectory = scenario(49, now)
    actual = expected_soc_with_psu48(CONFIG, inputs, trajectory, 35, 49 * 3600)
    assert actual == pytest.approx((69,) * 48)
