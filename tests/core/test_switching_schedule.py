"""The dashboard receives actual simulation decisions, including sub-hour edges."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from core.model import (
    BatteryParams,
    ConverterParams,
    HourSlot,
    PlanInputs,
    ReserveParams,
    SupportParams,
    SystemConfig,
)
from core.optimize import plan
from core.replay import recording, replay
from core.simulate import simulate
from core.simulation_steps import switching_schedule


def config():
    return SystemConfig(
        battery=BatteryParams(1000, 5, 95, 1, 1),
        inverter=ConverterParams(1000, 1, 0),
        charger=ConverterParams(1000, 1, 0),
        support=SupportParams(
            configured=True,
            coordinated=True,
            native48_base_w=0,
            dcdc_eta=1,
            psu24_eta=1,
            psu48_eta=1,
        ),
    )


@pytest.mark.parametrize("zone", [None, UTC, ZoneInfo("Europe/Berlin")])
def test_partial_slot_cutoff_retains_both_states_and_exact_end(zone):
    now = datetime(2026, 9, 28, 10, 17, tzinfo=zone)
    slot = HourSlot(0, now, 43 / 60, 10, 0, 86, 0)
    inputs = PlanInputs(now, 26, (slot,))
    flow = simulate(config(), inputs, 20).flows[0]
    # 60 Wh available above 20%, consumed in 30 minutes at 120 W.
    assert flow.inverter_output_wh == pytest.approx(60)
    assert not flow.inverter_on  # the hourly aggregate cannot describe this!
    on, off = flow.switching_schedule
    assert on.inverter_on and not off.inverter_on
    assert on.start == now
    assert on.end == off.start == now + timedelta(minutes=30)
    assert off.end == now + timedelta(minutes=43)
    assert not any(b.support_dc24 or b.support_dc48 for b in (on, off))


def test_supply_changes_remain_distinct_from_inverter_and_energy_output():
    now = datetime(2026, 9, 28)
    c = config()
    c = replace(
        c, support=replace(c.support, native48_base_w=60, psu48_bus_voltage_v=52)
    )
    slots = tuple(
        HourSlot(i, now + timedelta(hours=i), 1, i, 0, 0, 120) for i in range(3)
    )
    result = simulate(c, PlanInputs(now, 15, slots), 20)
    schedule = switching_schedule(slots, result.flows)
    assert schedule[0].start == now
    assert schedule[-1].end == now + timedelta(hours=3)
    assert all(a.end == b.start for a, b in zip(schedule, schedule[1:], strict=False))
    assert not any(b.inverter_on for b in schedule)
    assert [(b.support_dc24, b.support_dc48) for b in schedule] == [
        (False, False),
        (True, False),
        (True, True),
    ]
    # A requested 48 V source can deliver no current due to the voltage gate.
    # The schedule describes activation, never inferred from output Wh.
    assert all(f.psu48_delivered_wh == 0 for f in result.flows)


def test_reserve_retains_late_preparation_in_same_hour():
    now = datetime(2026, 9, 28)
    c = replace(config(), reserve=ReserveParams(True, 1))
    slots = (
        HourSlot(0, now, 1, 0, 0, 600, 0),
        HourSlot(1, now + timedelta(hours=1), 1, 1, 300, 0, 0),
    )
    flow = simulate(c, PlanInputs(now, 95, slots), 20).flows[0]
    assert flow.inverter_output_wh == pytest.approx(300)
    off, on = flow.switching_schedule
    assert not off.inverter_on and on.inverter_on
    assert off.end == on.start == now + timedelta(minutes=30)


@pytest.mark.parametrize(
    "day, hour", [(datetime(2026, 3, 29), 1), (datetime(2026, 10, 25), 2)]
)
def test_switching_intervals_follow_elapsed_time_across_dst(day, hour):
    now = day.replace(hour=hour, tzinfo=ZoneInfo("Europe/Berlin"))
    slots = tuple(
        HourSlot(
            i,
            (now.astimezone(UTC) + timedelta(hours=i)).astimezone(now.tzinfo),
            1,
            0,
            0,
            0,
            0,
        )
        for i in range(2)
    )
    # Simulate each ZoneInfo slot independently: PlanInputs deliberately
    # requires fixed-offset starts to order repeated local hours in a horizon.
    flows = tuple(
        simulate(config(), PlanInputs(slot.start, 50, (slot,)), 20).flows[0]
        for slot in slots
    )
    schedule = switching_schedule(slots, flows)
    assert len(schedule) == 1
    assert schedule[0].start.isoformat() == now.isoformat()
    assert schedule[0].end.astimezone(UTC) - now.astimezone(UTC) == timedelta(hours=2)


def test_legacy_hourly_plan_keeps_gaps_instead_of_filling_unknown_time():
    now = datetime(2026, 9, 28)
    slots = (
        HourSlot(0, now, 0.5, 0, 0, 0, 0),
        HourSlot(1, now + timedelta(hours=1), 1, 1, 0, 0, 0),
    )
    result = simulate(SystemConfig(), PlanInputs(now, 50, slots), 20)
    assert all(not f.switching_schedule for f in result.flows)
    schedule = switching_schedule(slots, result.flows)
    assert len(schedule) == 2
    assert schedule[0].end == now + timedelta(minutes=30)
    assert schedule[1].start == now + timedelta(hours=1)


def test_replay_checks_new_edges_but_reads_records_without_timing():
    now = datetime(2026, 9, 28)
    inputs = PlanInputs(now, 26, (HourSlot(0, now, 1, 0, 0, 120, 0),))
    result = plan(config(), inputs)
    record = recording(config(), inputs, result)
    assert replay(record) == (result, True)
    flows = record["result"]["fields"]["trajectory"]["fields"]["flows"]["tuple"]
    timing = flows[0]["fields"]["switching_schedule"]
    timing["tuple"][0]["fields"]["inverter_on"] = not timing["tuple"][0]["fields"][
        "inverter_on"
    ]
    assert not replay(record)[1]
    for flow in flows:
        flow["fields"].pop("switching_schedule")
    assert replay(record) == (result, True)
    flows[0]["fields"]["grid_import_wh"] += 1
    assert not replay(record)[1]
