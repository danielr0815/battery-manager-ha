"""Reachability uses the same energy outcomes as the existing physical simulator."""

from dataclasses import replace
from datetime import datetime
from random import Random

import pytest
from core.model import (
    BatteryParams,
    ConverterParams,
    HourSlot,
    SupportParams,
    SystemConfig,
)
from core.reserve_energy import BatteryStep
from core.simulate import step_hour


def cases():
    random = Random(94802)
    for _ in range(240):
        config = SystemConfig(
            battery=BatteryParams(
                1000, 5, 95, random.uniform(0.8, 1), random.uniform(0.8, 1)
            ),
            charger=ConverterParams(
                random.choice([0, 10, 100, 1000]),
                random.uniform(0.8, 1),
                random.choice([0, 2, 20]),
            ),
            inverter=ConverterParams(
                random.choice([0, 100, 1000]),
                random.uniform(0.8, 1),
                random.choice([0, 2, 20]),
            ),
            support=SupportParams(
                configured=True,
                coordinated=True,
                native48_base_w=random.choice([0, 20, 200]),
                dc24_share=random.choice([0, 0.6, 1]),
                dcdc_max_power_w=random.choice([None, 100, 300]),
                dcdc_eta=random.uniform(0.8, 1),
            ),
        )
        duration = random.choice([1 / 12, 0.25, 1])
        slot = HourSlot(
            0,
            datetime(2026, 9, 27),
            duration,
            0,
            random.uniform(0, 1200) * duration,
            random.uniform(0, 500) * duration,
            random.uniform(0, 300) * duration,
        )
        extra = random.uniform(0, 100) * duration
        upper = random.choice([1, 1.2, 1.5])
        feedin = random.choice([0, 10, 300]) * duration
        yield config, slot, extra, upper, feedin


def test_energy_projection_matches_physical_flows_across_converter_limits():
    for config, slot, extra, upper, feedin in cases():
        budget = BatteryStep.build(config, slot, extra, upper, feedin)
        for energy in (50, 51, 70, 199, 200, 201, 400, 949, 950):
            for ac in (False, True):
                threshold = 20 if ac and budget.ac else 100
                expected = step_hour(
                    config,
                    energy / 10,
                    slot,
                    threshold,
                    extra,
                    pv_scale=upper,
                    feedin_wh=feedin,
                )
                end, export = budget.project(energy, ac=ac)
                assert end == pytest.approx(expected.soc_end_percent * 10, abs=1e-7)
                assert export == pytest.approx(expected.grid_export_wh, abs=1e-7)


def test_inverse_preserves_attainable_export_and_latest_energy():
    for config, slot, extra, upper, feedin in cases():
        budget = BatteryStep.build(config, slot, extra, upper, feedin)
        for reference in (50, 80, 200, 400, 800):
            end, export = budget.project(reference)
            following = min(950, end + 25)
            ceiling = budget.incoming_ceiling(following, export)
            actual, spill = budget.project(ceiling)
            assert ceiling >= reference - 1e-5
            assert actual <= following + 1e-5
            assert spill <= export + 1e-5
            if ceiling + 0.01 < 950:
                later, later_export = budget.project(ceiling + 0.01)
                assert later > following + 1e-5 or later_export > export + 1e-5


def test_only_useful_ac_demand_can_create_headroom_despite_inverter_standby():
    config = SystemConfig(inverter=ConverterParams(1000, 0.9, 100))
    slot = HourSlot(0, datetime(2026, 9, 27), 1, 0, 50, 10, 0)
    budget = BatteryStep.build(config, slot, 0, 1)
    assert budget.ac == 0
    assert budget.project(1000) == budget.project(1000, ac=False)


def test_forced_sources_close_ac_window_and_only_verified_24v_removes_rail_load():
    config = SystemConfig(
        support=SupportParams(
            configured=True,
            coordinated=True,
            dc24_forced_on=True,
            psu24_max_power_w=100,
        )
    )
    slot = HourSlot(0, datetime(2026, 9, 27), 1, 0, 0, 100, 60)
    supplied = BatteryStep.build(config, slot, 0, 1)
    assert supplied.dc == supplied.ac == 0
    undersized = BatteryStep.build(
        replace(config, support=replace(config.support, psu24_max_power_w=10)),
        slot,
        0,
        1,
    )
    assert undersized.dc > 0
    assert undersized.ac > 0
    unknown48 = BatteryStep.build(
        replace(
            config,
            support=replace(config.support, dc24_forced_on=False, dc48_forced_on=True),
        ),
        slot,
        0,
        1,
    )
    assert unknown48.ac == 0
    assert unknown48.project(1000)[0] < 1000


def test_inverted_legacy_soc_limits_use_the_same_physical_ceiling():
    config = SystemConfig(battery=BatteryParams(1000, 30, 20, 1, 1))
    slot = HourSlot(0, datetime(2026, 9, 27), 1, 0, 100, 0, 10)
    budget = BatteryStep.build(config, slot, 0, 1)
    assert budget.maximum == budget.floor == 300
    end, export = budget.project(300)
    physical = step_hour(config, 30, slot, 100)
    assert end == pytest.approx(physical.soc_end_percent * 10)
    assert export == pytest.approx(physical.grid_export_wh)
