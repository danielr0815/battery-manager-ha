"""F-PREDRAIN-BLOCK stability gate (coordinator side, docs/F-PREDRAIN-BLOCK.md R7).

A continuous load's pass-3 pre-drain block is only ACTUATED after
PREDRAIN_BLOCK_STABLE_PLANS consecutive identical plans AND
PREDRAIN_BLOCK_STABLE_MINUTES of wall-clock stability — the 2026-08-01
flicker incident produced a pointless 19-min battery run when a marginal
block flipped in and out of the plan within minutes. The gate covers only
OFF->ON: pass-1 (direct surplus) actuation is unaffected, and a RUNNING
load is never cut by the gate — the plan itself retracts the recommendation
when the block disappears (dwell rules apply).
"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from homeassistant.util import dt as dt_util
from test_stale_failsafe import (
    ENABLE,
    PLUG,
    _cancel_debounce,
    _refresh_at,
    _set_soc,
    _setup,
)

from custom_components.battery_manager.const import (
    PREDRAIN_BLOCK_STABLE_MINUTES,
    PREDRAIN_BLOCK_STABLE_PLANS,
)
from custom_components.battery_manager.core.model import HourSlot, LoadPlan

# 04:00 local — pre-dawn, so a big clip day makes the block cover slot 0.
EARLY = dt_util.as_local(datetime(2026, 7, 19, 2, 0, tzinfo=UTC))
MIDDAY = dt_util.as_local(datetime(2026, 7, 19, 11, 0, tzinfo=UTC))


def _prime_block_scenario(hass, coordinator, entry, pinned=EARLY):
    """Drive the coordinator into a clean state where the plan books a
    pass-3 block covering slot 0: high SOC (deep drain allowed) and a huge
    clip day (a long block is needed). Clears the stability evidence so the
    test owns the plan count."""
    sub_id = next(iter(entry.subentries))
    _set_soc(hass, coordinator, "90")
    hass.states.async_set("sensor.pv_today", "18.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set(PLUG, "off")
    hass.states.async_set(ENABLE, "off")
    coordinator._load_charging_active[sub_id] = False
    coordinator._predrain_block_evidence.clear()
    coordinator._predrain_block_stable.clear()
    # Every state set above armed a debounced refresh; cancel them so the
    # test's explicit refreshes are the only plans counted.
    _cancel_debounce(coordinator)
    return sub_id


async def test_block_actuation_waits_for_stability(hass):
    """A pass-3 block covering slot 0 does not switch on at the first plan —
    only after PREDRAIN_BLOCK_STABLE_PLANS identical plans AND the
    PREDRAIN_BLOCK_STABLE_MINUTES time floor."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    entry, coordinator = await _setup(hass, calls, notifications, pinned=EARLY)
    sub_id = _prime_block_scenario(hass, coordinator, entry)
    calls.clear()

    t0 = EARLY + timedelta(minutes=2)
    await _refresh_at(hass, coordinator, t0)
    evidence = coordinator._predrain_block_evidence.get(sub_id)
    assert evidence is not None and evidence[1] == 1, (
        "premise: the plan must book a pass-3 block"
    )
    assert ("turn_on", PLUG) not in calls  # gate: 1 plan < 3

    await _refresh_at(hass, coordinator, t0 + timedelta(minutes=5))
    evidence = coordinator._predrain_block_evidence[sub_id]
    assert evidence[1] == 2
    assert ("turn_on", PLUG) not in calls  # plans ok, time floor not met

    await _refresh_at(hass, coordinator, t0 + timedelta(minutes=10))
    evidence = coordinator._predrain_block_evidence[sub_id]
    assert evidence[1] >= PREDRAIN_BLOCK_STABLE_PLANS
    assert sub_id in coordinator._predrain_block_stable
    assert ("turn_on", PLUG) in calls
    assert hass.states.get(PLUG).state == "on"


async def test_block_disappearing_retracts_recommendation_with_dwell(hass):
    """Once the block leaves the plan the recommendation is retracted like any
    plan-off: the min_runtime dwell finishes the current run, then the load
    switches off."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    entry, coordinator = await _setup(hass, calls, notifications, pinned=EARLY)
    sub_id = _prime_block_scenario(hass, coordinator, entry)
    calls.clear()

    t0 = EARLY + timedelta(minutes=2)
    for minutes in (0, 5, 10):
        await _refresh_at(hass, coordinator, t0 + timedelta(minutes=minutes))
    assert hass.states.get(PLUG).state == "on"  # stable block actuated

    # The clip day evaporates: no export today -> no block in the plan.
    hass.states.async_set("sensor.pv_today", "0.5", {"unit_of_measurement": "kWh"})
    await _refresh_at(hass, coordinator, t0 + timedelta(minutes=15))
    assert coordinator._predrain_block_evidence.get(sub_id) is None
    assert hass.states.get(PLUG).state == "on"  # min_runtime dwell still holds

    await _refresh_at(hass, coordinator, t0 + timedelta(minutes=45))
    assert hass.states.get(PLUG).state == "off"  # dwell expired, plan-off wins


async def test_pass1_direct_surplus_needs_no_stability(hass):
    """The gate only covers pass-3 blocks: a pass-1 (direct surplus) slot-0
    booking switches on the very first plan."""
    calls: list[tuple[str, str]] = []
    notifications: list[dict] = []
    entry, coordinator = await _setup(hass, calls, notifications, pinned=MIDDAY)
    _prime_block_scenario(hass, coordinator, entry, pinned=MIDDAY)
    calls.clear()

    await _refresh_at(hass, coordinator, MIDDAY + timedelta(minutes=2))
    assert ("turn_on", PLUG) in calls
    assert hass.states.get(PLUG).state == "on"


def _block_result(sub_id, start_idx, count, n_slots):
    return SimpleNamespace(
        load_plans=(
            LoadPlan(
                load_id=sub_id,
                schedule=(True,) * n_slots,
                planned_energy_wh=300.0,
                allocations=((start_idx, count, 3, 300.0),),
                run_hours=(1.0,) * n_slots,
            ),
        ),
    )


def _no_block_result(sub_id, n_slots):
    return SimpleNamespace(
        load_plans=(
            LoadPlan(
                load_id=sub_id,
                schedule=(False,) * n_slots,
                planned_energy_wh=0.0,
            ),
        ),
    )


async def test_evidence_counts_resets_and_enforces_the_time_floor(hass):
    """The evidence counts consecutive identical signatures, resets on any
    change, clears when the block vanishes, and the wall-clock floor holds
    even with enough plans (fast refresh cadences)."""
    entry, coordinator = await _setup(hass, [], [])
    sub_id = next(iter(entry.subentries))
    slots = tuple(
        HourSlot(
            index=i,
            start=datetime(2026, 7, 19, 4 + i, 0),
            duration=1.0,
            hour_of_day=4 + i,
            pv_wh=0.0,
            ac_wh=0.0,
            dc_wh=0.0,
        )
        for i in range(6)
    )
    inputs = SimpleNamespace(slots=slots)
    t0 = datetime(2026, 7, 19, 2, 0, tzinfo=UTC)

    coordinator._update_predrain_block_evidence(
        _block_result(sub_id, 0, 2, 6), inputs, t0
    )
    assert coordinator._predrain_block_stable == set()
    for minutes in (5, 9):
        coordinator._update_predrain_block_evidence(
            _block_result(sub_id, 0, 2, 6), inputs, t0 + timedelta(minutes=minutes)
        )
    assert coordinator._predrain_block_evidence[sub_id][1] == 3
    # 3 plans but only 9 min: the time floor (10 min) is not met yet.
    assert coordinator._predrain_block_stable == set()

    coordinator._update_predrain_block_evidence(
        _block_result(sub_id, 0, 2, 6), inputs, t0 + timedelta(minutes=11)
    )
    assert coordinator._predrain_block_evidence[sub_id][1] >= (
        PREDRAIN_BLOCK_STABLE_PLANS
    )
    assert coordinator._predrain_block_stable == {sub_id}

    # A changed signature restarts the count (and the time floor).
    coordinator._update_predrain_block_evidence(
        _block_result(sub_id, 1, 2, 6), inputs, t0 + timedelta(minutes=12)
    )
    assert coordinator._predrain_block_evidence[sub_id][1] == 1
    assert coordinator._predrain_block_stable == set()

    # The block vanishing clears the evidence entirely.
    coordinator._update_predrain_block_evidence(
        _no_block_result(sub_id, 6), inputs, t0 + timedelta(minutes=13)
    )
    assert coordinator._predrain_block_evidence.get(sub_id) is None
    assert coordinator._predrain_block_stable == set()
    assert PREDRAIN_BLOCK_STABLE_MINUTES >= 1  # documented contract (floor > 0)
