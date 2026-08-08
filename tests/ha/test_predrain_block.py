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


# ---------------------------------------------------------------------------
# F-PREDRAIN-BLOCK log damping (7-day live audit 31.07.–07.08.2026): the raw
# (load, start, end) change-gate ratcheted with clock and forecast — 4,196
# INFO lines in 7 days. Now: material changes only (start >= 30 min, energy
# >= 200 Wh, new block), rate-limited to 1/h per block (DEBUG otherwise).
# ---------------------------------------------------------------------------


def _log_inputs(minute_offset=0, n_slots=6, day=19):
    slots = tuple(
        HourSlot(
            index=i,
            start=datetime(2026, 7, day, 4 + i, minute_offset),
            duration=1.0,
            hour_of_day=4 + i,
            pv_wh=0.0,
            ac_wh=0.0,
            dc_wh=0.0,
        )
        for i in range(n_slots)
    )
    return SimpleNamespace(slots=slots)


def _block_result_wh(sub_id, wh, start_idx=0, count=2, n_slots=6):
    return SimpleNamespace(
        load_plans=(
            LoadPlan(
                load_id=sub_id,
                schedule=(True,) * n_slots,
                planned_energy_wh=wh,
                allocations=((start_idx, count, 3, wh),),
                run_hours=(1.0,) * n_slots,
            ),
        ),
    )


def _predrain_info_lines(caplog, level):
    return [
        r
        for r in caplog.records
        if "F-PREDRAIN-BLOCK" in r.message and r.levelno == level
    ]


async def test_predrain_log_once_per_stable_booking(hass, caplog):
    """An unchanged booking logs exactly ONE INFO line, not one per cycle
    (spec project-knowledge 05 §5.6: change-gated, no per-cycle spam)."""
    import logging

    entry, coordinator = await _setup(hass, [], [])
    sub_id = next(iter(entry.subentries))
    config = coordinator.build_system_config()
    inputs = _log_inputs()
    result = _block_result_wh(sub_id, 300.0)
    with caplog.at_level(logging.INFO):
        caplog.clear()
        for _ in range(20):
            coordinator._log_night_predrain(result, inputs, config)
    lines = _predrain_info_lines(caplog, logging.INFO)
    assert len(lines) == 1
    assert "Load One" in lines[0].message


async def test_predrain_log_ignores_subthreshold_ratchet(hass, caplog):
    """The audit ratchet itself: start minute and booked Wh jittering below
    the thresholds EVERY cycle (clock/forecast noise) must not re-log —
    1,753 lines on 08-04 came from exactly this."""
    import logging

    entry, coordinator = await _setup(hass, [], [])
    sub_id = next(iter(entry.subentries))
    config = coordinator.build_system_config()
    with caplog.at_level(logging.INFO):
        caplog.clear()
        for cycle in range(20):
            # Alternate 10 min / 50 Wh around the logged booking: every step
            # is below the 30 min / 200 Wh materiality thresholds.
            inputs = _log_inputs(minute_offset=10 if cycle % 2 else 0)
            result = _block_result_wh(sub_id, 350.0 if cycle % 2 else 300.0)
            coordinator._log_night_predrain(result, inputs, config)
    assert len(_predrain_info_lines(caplog, logging.INFO)) == 1


async def test_predrain_log_material_changes_log(hass, caplog):
    """A start shift >= 30 min, an energy change >= 200 Wh and a block on a
    NEW day are each material and log a fresh INFO line."""
    import logging
    from unittest.mock import patch

    entry, coordinator = await _setup(hass, [], [])
    sub_id = next(iter(entry.subentries))
    config = coordinator.build_system_config()
    t0 = datetime(2026, 7, 19, 2, 0, tzinfo=UTC)

    def _log(moment, inputs, result):
        with patch.object(dt_util, "utcnow", return_value=moment):
            coordinator._log_night_predrain(result, inputs, config)

    with caplog.at_level(logging.INFO):
        caplog.clear()
        _log(t0, _log_inputs(), _block_result_wh(sub_id, 300.0))
        assert len(_predrain_info_lines(caplog, logging.INFO)) == 1

        # Start shifted 45 min (past the rate window): material -> new line.
        _log(
            t0 + timedelta(minutes=61),
            _log_inputs(minute_offset=45),
            _block_result_wh(sub_id, 300.0),
        )
        assert len(_predrain_info_lines(caplog, logging.INFO)) == 2

        # Energy +250 Wh at the same start (again past the window): new line.
        _log(
            t0 + timedelta(minutes=122),
            _log_inputs(minute_offset=45),
            _block_result_wh(sub_id, 550.0),
        )
        assert len(_predrain_info_lines(caplog, logging.INFO)) == 3

        # A block on a new day is a new booking: logs even inside the window.
        _log(
            t0 + timedelta(minutes=123),
            _log_inputs(day=20),
            _block_result_wh(sub_id, 550.0),
        )
        lines = _predrain_info_lines(caplog, logging.INFO)
        assert len(lines) == 4
        assert "2026-07-20" in lines[-1].message


async def test_predrain_log_rate_limit_bounds_oscillation(hass, caplog):
    """A forecast oscillating ACROSS the material threshold every cycle is
    bounded to one INFO line per block per hour; suppressed changes go to
    DEBUG and surface once the window passes."""
    import logging
    from unittest.mock import patch

    entry, coordinator = await _setup(hass, [], [])
    sub_id = next(iter(entry.subentries))
    config = coordinator.build_system_config()
    t0 = datetime(2026, 7, 19, 2, 0, tzinfo=UTC)

    with caplog.at_level(logging.DEBUG):
        caplog.clear()
        with patch.object(dt_util, "utcnow", return_value=t0):
            coordinator._log_night_predrain(
                _block_result_wh(sub_id, 300.0), _log_inputs(), config
            )
        # Oscillate 04:00 <-> 04:45 (45 min apart: material) every minute.
        for cycle in range(1, 7):
            offset = 45 if cycle % 2 else 0
            with patch.object(
                dt_util, "utcnow", return_value=t0 + timedelta(minutes=cycle)
            ):
                coordinator._log_night_predrain(
                    _block_result_wh(sub_id, 300.0),
                    _log_inputs(minute_offset=offset),
                    config,
                )
        # One initial INFO only; the oscillation was rate-limited to DEBUG.
        assert len(_predrain_info_lines(caplog, logging.INFO)) == 1
        assert len(_predrain_info_lines(caplog, logging.DEBUG)) >= 1
        # Once the rate window passes the pending material change logs.
        with patch.object(dt_util, "utcnow", return_value=t0 + timedelta(minutes=61)):
            coordinator._log_night_predrain(
                _block_result_wh(sub_id, 300.0),
                _log_inputs(minute_offset=45),
                config,
            )
        lines = _predrain_info_lines(caplog, logging.INFO)
        assert len(lines) == 2
        assert "04:45" in lines[-1].message
