"""Measured demand may move useful AC preparation, never consume DC reserve."""

from datetime import UTC, datetime, timedelta

import pytest
from core.live_ac import (
    LIVE_AC_INTERVAL_S,
    LIVE_AC_OFF_DELAY_S,
    LIVE_AC_PLAN_MAX_AGE_S,
    LIVE_AC_SAMPLE_MAX_AGE_S,
    LiveACState,
    live_ac_decision,
)
from core.simulate import simulate
from test_reserve_priority import plant, series

NOW = datetime(2026, 9, 29, 9, tzinfo=UTC)


def decide(state=None, seconds=0, demand=1500, energy=200, blocked=None):
    return live_ac_decision(
        state or LiveACState(),
        NOW + timedelta(seconds=seconds),
        demand,
        energy,
        2300,
        0.9,
        blocked,
    )


def test_fast_release_then_ten_minutes_continuous_low_demand():
    active = decide()
    assert active.limit_w == 2300
    low = decide(active.state, 1, 0)
    assert low.reason == "off_delay"
    held = decide(low.state, 600, 0)
    assert held.limit_w == 2300
    expired = decide(held.state, 601, 0)
    assert expired.limit_w == 0
    assert not expired.state.active


def test_new_heating_phase_resets_off_timer_and_deadband_does_not_start():
    assert decide(demand=75).limit_w == 0
    low = decide(decide().state, 10, 0)
    reheat = decide(low.state, 300, 100)
    assert reheat.state.low_since is None
    low = decide(reheat.state, 310, 0)
    deadband = decide(low.state, 900, 75)
    assert deadband.state.active and deadband.state.low_since is None
    assert decide(deadband.state, 1000, 0).limit_w > 0


@pytest.mark.parametrize(
    "energy,demand,blocked,reason",
    [
        (0, 2000, None, "reserve_budget"),
        (0.1, 2000, None, "reserve_budget"),
        (200, None, None, "measurement_unavailable"),
        (200, 2000, "dc_supply", "dc_supply"),
    ],
)
def test_protection_immediately_overrides_hold(energy, demand, blocked, reason):
    stopped = decide(decide().state, 1, demand, energy, blocked)
    assert stopped.limit_w == 0
    assert stopped.reason == reason
    assert stopped.state == LiveACState()


def test_limit_preserves_energy_until_stale_measurement_is_detected():
    limited = decide(energy=10)
    assert limited.limit_w == 0
    assert limited.reason == "reserve_budget"
    required_wh = 2300 / 0.9 * 35 / 3600
    assert decide(energy=required_wh - 0.001).limit_w == 0
    released = decide(energy=required_wh + 0.001)
    assert released.limit_w == 2300
    assert released.limit_w / 0.9 * 35 / 3600 <= required_wh + 0.001
    assert LIVE_AC_SAMPLE_MAX_AGE_S == 30
    assert LIVE_AC_INTERVAL_S == 5
    assert LIVE_AC_PLAN_MAX_AGE_S == 300
    assert LIVE_AC_OFF_DELAY_S == 600


def test_live_floor_exposes_energy_reserved_for_later_ac_opportunity():
    result = simulate(plant(), series([(0, 100, 0), (0, 600, 0), (300, 0, 0)]), 20)
    assert result.flows[0].inverter_output_wh == 0
    assert result.flows[1].inverter_output_wh == pytest.approx(150)
    assert result.reserve_decision.live_ac_floor_percent == pytest.approx(65)


def test_dc_consumption_already_creates_space_so_live_ac_gets_no_budget():
    result = simulate(plant(), series([(0, 100, 100), (0, 600, 200), (300, 0, 0)]), 20)
    assert result.reserve_decision.live_ac_floor_percent >= 80


def test_future_dc_energy_cannot_be_borrowed_for_a_live_appliance():
    result = simulate(plant(), series([(0, 600, 0), (0, 0, 600), (900, 0, 0)]), 20)
    # Of 800 Wh stored, 600 Wh are needed for DC, plus the battery's 50 Wh floor.
    assert result.reserve_decision.live_ac_floor_percent >= 65
