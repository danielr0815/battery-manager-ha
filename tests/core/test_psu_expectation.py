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
    project_soc_with_net_battery_discharge,
    project_soc_with_psu48,
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


def scenario(count=3, now=NOW, *, duration=1, planned_psu_w=0):
    slots = []
    flows = []
    soc = 69.0
    for i in range(count):
        start = (now.astimezone(UTC) + timedelta(hours=i * duration)).astimezone(
            now.tzinfo
        )
        end = (start.astimezone(UTC) + timedelta(hours=duration)).astimezone(now.tzinfo)
        slot = HourSlot(
            i, start, duration, start.hour, 0, 100 * duration, 65 * duration
        )
        discharge = (35 - planned_psu_w) * duration / CONFIG.battery.eta_discharge
        next_soc = soc - CONFIG.battery.soc_percent(discharge)
        flow = HourFlows(
            soc,
            next_soc,
            100 * duration,
            0,
            0,
            discharge,
            False,
            0,
            0,
            True,
            True,
            switching_schedule=(SwitchingInterval(start, end, False, True, True),),
            psu24_delivered_wh=30 * duration,
            psu48_delivered_wh=planned_psu_w * duration,
        )
        slots.append(slot)
        flows.append(flow)
        soc = next_soc
    return (
        PlanInputs(now, 69, tuple(slots)),
        Trajectory(tuple(flows), count * 100 * duration, 0, soc),
    )


def test_native_discharge_can_be_avoided_only_for_the_observed_horizon():
    inputs, trajectory = scenario()
    assert expected_soc_with_psu48(
        CONFIG, inputs, trajectory, 35, 7200
    ) == pytest.approx((69, 69))
    assert trajectory.end_soc_percent == pytest.approx(66.8350515)
    small = expected_soc_with_psu48(CONFIG, inputs, trajectory, 10, 3600)
    assert small == pytest.approx((69 - 25 / 0.97 / 50,))
    assert expected_soc_with_psu48(CONFIG, inputs, trajectory, 35, 3599) == ()


def test_explanation_distinguishes_plan_mismatch_from_insufficient_observation():
    inputs, trajectory = scenario()
    assert project_soc_with_psu48(CONFIG, inputs, trajectory, 35, 1800) == (
        (),
        "observation_shorter_than_plan_interval",
    )
    assert project_soc_with_psu48(CONFIG, inputs, trajectory, 35, 3600) == (
        pytest.approx((69,)),
        None,
    )
    first, *rest = trajectory.flows
    (interval,) = first.switching_schedule
    mismatch = replace(
        trajectory,
        flows=(
            replace(first, switching_schedule=(replace(interval, inverter_on=True),)),
            *rest,
        ),
    )
    assert project_soc_with_psu48(CONFIG, inputs, mismatch, 35, 1800) == (
        (),
        "no_matching_plan_window",
    )


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


def test_net_battery_discharge_uses_terminal_energy_and_keeps_direct_credit():
    inputs, trajectory = scenario(planned_psu_w=20)
    projected, reason = project_soc_with_net_battery_discharge(
        CONFIG, inputs, trajectory, 8, 7200
    )
    assert reason is None
    assert projected == pytest.approx((69 - 8 / 0.97 / 50, 69 - 16 / 0.97 / 50))
    assert projected == pytest.approx(
        expected_soc_with_psu48(CONFIG, inputs, trajectory, 27, 7200)
    )
    assert projected != expected_soc_with_psu48(CONFIG, inputs, trajectory, 35, 7200)
    assert trajectory.flows[1].soc_end_percent == pytest.approx(69 - 30 / 0.97 / 50)


def test_net_battery_discharge_can_match_or_fall_below_the_accepted_soc():
    inputs, trajectory = scenario(planned_psu_w=20)
    assert project_soc_with_net_battery_discharge(
        CONFIG, inputs, trajectory, 15, 3600
    ) == (pytest.approx((trajectory.flows[0].soc_end_percent,)), None)
    assert project_soc_with_net_battery_discharge(
        CONFIG, inputs, trajectory, 16, 3600
    ) == (pytest.approx((69 - 16 / 0.97 / 50,)), None)
    assert project_soc_with_net_battery_discharge(
        CONFIG, inputs, trajectory, 36, 3600
    ) == ((), "net_discharge_exceeds_native_load")


def test_net_measurement_replaces_both_planned_flows_in_archived_night_slot():
    # 2026-11-18 05:32:01 night plan: PSU alternation happened within one
    # contiguous isolated interval, so the aggregate contains both flows.
    now = datetime.fromisoformat("2026-11-18T05:32:01.301593+01:00")
    duration = 0.46630511305555555
    inputs, trajectory = scenario(count=1, now=now, duration=duration)
    (flow,) = trajectory.flows
    mixed = replace(
        flow,
        battery_discharge_wh=10.811696519186714,
        battery_charge_wh=3.5426825,
        psu48_battery_charge_wh=3.5426825,
        psu48_delivered_wh=9.485583333333333,
        soc_end_percent=68.85461971961625,
    )
    trajectory = replace(
        trajectory, flows=(mixed,), end_soc_percent=mixed.soc_end_percent
    )
    assert project_soc_with_net_battery_discharge(
        CONFIG, inputs, trajectory, 0, 7200
    ) == (
        pytest.approx((69,)),
        None,
    )
    assert project_soc_with_net_battery_discharge(
        CONFIG, inputs, trajectory, 18, 7200
    ) == (
        pytest.approx((69 - 18 * duration / 0.97 / 50,)),
        None,
    )


def test_net_measurement_excludes_pure_planned_psu_charging():
    inputs, trajectory = scenario(count=1)
    (flow,) = trajectory.flows
    charging = replace(
        flow,
        battery_discharge_wh=0,
        battery_charge_wh=5,
        psu48_battery_charge_wh=5,
        psu48_delivered_wh=35 + 5 / 0.97,
        soc_end_percent=69.1,
    )
    trajectory = replace(trajectory, flows=(charging,), end_soc_percent=69.1)
    assert project_soc_with_net_battery_discharge(
        CONFIG, inputs, trajectory, 0, 3600
    ) == (
        pytest.approx((69,)),
        None,
    )
    assert project_soc_with_net_battery_discharge(
        CONFIG, inputs, trajectory, 5, 3600
    ) == (
        pytest.approx((69 - 5 / 0.97 / 50,)),
        None,
    )


def test_net_measurement_stops_before_crossing_storage_bounds():
    inputs, trajectory = scenario()
    config = replace(
        CONFIG, battery=replace(CONFIG.battery, capacity_wh=100, eta_discharge=1)
    )
    for start, power, points in [(6, 1, (5,)), (95, 0, (95, 95, 95))]:
        assert project_soc_with_net_battery_discharge(
            config, replace(inputs, start_soc_percent=start), trajectory, power, 10800
        ) == (pytest.approx(points), None)
    for start, power in [(6, 2), (96, 0)]:
        assert project_soc_with_net_battery_discharge(
            config, replace(inputs, start_soc_percent=start), trajectory, power, 10800
        ) == ((), "net_storage_limit")


def test_zero_net_discharge_credits_full_planned_intervals_only():
    inputs, trajectory = scenario(duration=0.5)
    assert project_soc_with_net_battery_discharge(
        CONFIG, inputs, trajectory, 0, 3599
    ) == (pytest.approx((69,)), None)
    assert project_soc_with_net_battery_discharge(
        CONFIG, inputs, trajectory, 0, 3600
    ) == (pytest.approx((69, 69)), None)
    inputs, trajectory = scenario()
    assert project_soc_with_net_battery_discharge(
        CONFIG, inputs, trajectory, 0, 1800
    ) == ((), "observation_shorter_than_plan_interval")


def test_net_battery_discharge_caps_proven_duration_at_two_days():
    inputs, trajectory = scenario(count=49)
    assert project_soc_with_net_battery_discharge(
        CONFIG, inputs, trajectory, 0, 49 * 3600
    ) == (pytest.approx((69,) * 48), None)


def test_net_battery_discharge_respects_the_end_of_the_comparable_night():
    inputs, trajectory = scenario(duration=0.25)
    until = NOW.timestamp() + 900
    assert project_soc_with_net_battery_discharge(
        CONFIG, inputs, trajectory, 0, 1800, valid_until=until
    ) == (pytest.approx((69,)), None)
    assert project_soc_with_net_battery_discharge(
        CONFIG, inputs, trajectory, 0, 1800, valid_until=until - 1
    ) == ((), "net_window_ends_before_interval")
    assert project_soc_with_net_battery_discharge(
        CONFIG, inputs, trajectory, 0, 1800, valid_until=float("nan")
    ) == ((), "projection_unsupported")


@pytest.mark.parametrize(
    ("w", "seconds"), [(-1, 3600), (float("nan"), 3600), (0, 1799), (0, float("inf"))]
)
def test_net_battery_discharge_requires_valid_age_and_power(w, seconds):
    inputs, trajectory = scenario()
    assert project_soc_with_net_battery_discharge(
        CONFIG, inputs, trajectory, w, seconds
    ) == ((), "projection_unsupported")


@pytest.mark.parametrize(
    "setting", ["adaptive_voltage_gate", "coordinated", "native48_base_w"]
)
def test_net_battery_discharge_requires_known_isolated_native_load(setting):
    inputs, trajectory = scenario()
    value = 0.0 if setting == "native48_base_w" else False
    cfg = replace(CONFIG, support=replace(CONFIG.support, **{setting: value}))
    assert project_soc_with_net_battery_discharge(cfg, inputs, trajectory, 0, 3600) == (
        (),
        "projection_unsupported",
    )


@pytest.mark.parametrize(
    ("slot_changes", "flow_changes"),
    [
        ({"pv_wh": 1}, {}),
        ({"pv_p90_wh": 1}, {}),
        ({}, {"battery_charge_wh": 1}),
        ({}, {"battery_charge_wh": 2, "psu48_battery_charge_wh": 1}),
        ({}, {"inverter_output_wh": 1}),
        ({"dc_wh": 34}, {}),
        ({}, {"dcdc_input_wh": 1}),
    ],
)
def test_net_battery_discharge_stops_at_first_incompatible_slot(
    slot_changes, flow_changes
):
    inputs, trajectory = scenario()
    first_slot, second_slot, last_slot = inputs.slots
    first, second, last = trajectory.flows
    inputs = replace(
        inputs, slots=(first_slot, replace(second_slot, **slot_changes), last_slot)
    )
    trajectory = replace(
        trajectory, flows=(first, replace(second, **flow_changes), last)
    )
    assert project_soc_with_net_battery_discharge(
        CONFIG, inputs, trajectory, 0, 10800
    ) == (pytest.approx((69,)), None)


def test_net_battery_discharge_rejects_variable_native_load_but_not_grid_load():
    inputs, trajectory = scenario(count=1)
    config = replace(CONFIG, support=replace(CONFIG.support, dc24_share=0.9))
    assert project_soc_with_net_battery_discharge(
        config, inputs, trajectory, 0, 3600
    ) == (
        (),
        "no_matching_plan_window",
    )
    (slot,) = inputs.slots
    (flow,) = trajectory.flows
    inputs = replace(inputs, slots=(replace(slot, ac_wh=800, dc_wh=70),))
    trajectory = replace(trajectory, flows=(replace(flow, psu24_delivered_wh=35),))
    assert project_soc_with_net_battery_discharge(
        CONFIG, inputs, trajectory, 0, 3600
    ) == (
        pytest.approx((69,)),
        None,
    )


def test_net_battery_discharge_respects_known_psu_cap_without_requiring_one():
    inputs, trajectory = scenario(planned_psu_w=20)
    cfg = replace(CONFIG, support=replace(CONFIG.support, psu48_max_power_w=25))
    assert project_soc_with_net_battery_discharge(cfg, inputs, trajectory, 0, 3600) == (
        (),
        "no_matching_plan_window",
    )
    assert project_soc_with_net_battery_discharge(
        cfg, inputs, trajectory, 10, 3600
    ) == (
        pytest.approx((69 - 10 / 0.97 / 50,)),
        None,
    )
    cfg = replace(CONFIG, support=replace(CONFIG.support, psu48_max_power_w=None))
    assert project_soc_with_net_battery_discharge(cfg, inputs, trajectory, 0, 3600) == (
        pytest.approx((69,)),
        None,
    )
