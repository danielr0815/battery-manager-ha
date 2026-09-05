"""Tests for the realized (measured) surplus accounting (F-REALIZED-SURPLUS,
docs/F-REALIZED-SURPLUS.md).

Covers the counter delta machinery (baseline learning, jump filter,
unavailable-relearn), the corrected export (export meter minus the external
loads fed through the measuring node, clamped at 0), the prevented-export
composition (energy-entity deltas of ALL loads + the runtime x power estimate
for loads without a counter), the local-midnight rollover, the store
restore across restarts, the `realized` data block, the sensor set with its
config gates and the options-flow unwiring.

Time discipline (hard-won, do not relax): the entry SETUP runs inside the
pinned window too — the setup-time refresh stamps the reading timestamps, and
a later refresh pinned BEFORE real boot time would measure negative elapsed
hours and trip the jump filter on every delta. The SOC varies slightly on
every refresh: a constant SOC over many pinned cycles trips the house-SOC
stale watchdog (UpdateFailed), and `_update_realized_surplus` only runs on
successful cycles. Energy deltas stay physically plausible against the jump
cap (400 W load, 5-min window -> cap 100 Wh; the export meter falls back to
the absolute REALIZED_JUMP_MAX_WH = 2000 Wh cap).

Patterns mirror test_feedin.py (`_refresh_at` pinning dt_util.now around the
refresh, `_cancel_debounce`, `_listeners_setup = False` so only the explicit
refreshes run).
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigSubentryData
from homeassistant.const import UnitOfEnergy
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from test_config_flow import _no_change_options_payload

from custom_components.battery_manager.const import (
    CONF_EXPORT_METER_ENTITY,
    CONF_FEEDIN_BATTERY_POWER_ENTITY,
    CONF_FEEDIN_DEADLINE_HOUR,
    CONF_FEEDIN_ENABLED,
    CONF_FEEDIN_MAX_W,
    CONF_FEEDIN_MIN_SOC,
    CONF_FEEDIN_SETPOINT_ENTITY,
    CONF_LOAD_ENERGY_ENTITY,
    CONF_LOAD_IN_HOUSE,
    CONF_LOAD_POWER_W,
    CONF_PV_FORECAST_DAY_AFTER,
    CONF_PV_FORECAST_TODAY,
    CONF_PV_FORECAST_TOMORROW,
    CONF_SOC_ENTITY,
    DOMAIN,
    ENTITY_EARLY_FEED_IN_REALIZED_TODAY,
    ENTITY_LOST_SURPLUS_REALIZED_TODAY,
    ENTITY_LOST_SURPLUS_TODAY,
    ENTITY_LOST_SURPLUS_TOMORROW,
    ENTITY_PREVENTED_EXPORT_REALIZED_TODAY,
    ENTITY_PREVENTED_EXPORT_TODAY,
    ENTITY_PREVENTED_EXPORT_TOMORROW,
    ENTITY_TRUE_EXPORT_ENERGY,
    SUBENTRY_TYPE_LOAD,
)

# Every energy counter carries its unit: the delta is scaled by the entity's
# OWN unit_of_measurement, so tests must not lean on the unknown-unit fallback
# (test_export_counter_in_wh_is_scaled_by_its_own_unit covers the other unit).
KWH = {"unit_of_measurement": "kWh", "device_class": "energy"}

SOC = "sensor.test_soc"
EXPORT = "sensor.test_export_meter"
LOAD_A_ENERGY = "sensor.test_load_a_energy"
LOAD_B_ENERGY = "sensor.test_load_b_energy"
SETPOINT = "input_number.acpowersetpoint"
BATT_POWER = "sensor.victron_system_battery_power"

BASE_DATA = {
    CONF_SOC_ENTITY: SOC,
    CONF_PV_FORECAST_TODAY: "sensor.pv_today",
    CONF_PV_FORECAST_TOMORROW: "sensor.pv_tomorrow",
    CONF_PV_FORECAST_DAY_AFTER: "sensor.pv_day_after",
}

FEEDIN_DATA = {
    CONF_FEEDIN_ENABLED: True,
    CONF_FEEDIN_SETPOINT_ENTITY: SETPOINT,
    CONF_FEEDIN_BATTERY_POWER_ENTITY: BATT_POWER,
    CONF_FEEDIN_MAX_W: 1000.0,
    CONF_FEEDIN_MIN_SOC: 30.0,
    CONF_FEEDIN_DEADLINE_HOUR: 9,
}

# A sunny summer late morning (local); refreshes step forward from here.
T0 = dt_util.as_local(datetime(2026, 7, 19, 10, 0, tzinfo=UTC))
FIVE_MIN = timedelta(minutes=5)

REALIZED_SENSOR_KEYS = (
    ENTITY_LOST_SURPLUS_REALIZED_TODAY,
    ENTITY_LOST_SURPLUS_TODAY,
    ENTITY_LOST_SURPLUS_TOMORROW,
    ENTITY_PREVENTED_EXPORT_REALIZED_TODAY,
    ENTITY_PREVENTED_EXPORT_TODAY,
    ENTITY_PREVENTED_EXPORT_TOMORROW,
    ENTITY_TRUE_EXPORT_ENERGY,
)

LOAD_A = "Load A"
LOAD_B = "Load B"
LOAD_C = "Load C"


@pytest.fixture(autouse=True)
async def _unload_entries_after_test(hass):
    """Unload whatever the test set up (lingering-timer guard, same reason
    as in test_config_flow.py / test_feedin.py)."""
    yield
    await hass.async_block_till_done()
    for entry in hass.config_entries.async_entries(DOMAIN):
        await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def _cancel_debounce(coordinator):
    """Drop a pending debounced refresh so every pass in a test is the
    explicit one (a stray refresh would stamp real-time state)."""
    if coordinator._debounce_task:
        coordinator._debounce_task.cancel()
        coordinator._debounce_task = None


def _set_input_states(hass, *, soc="55", pv=("10.0", "12.0", "8.0")):
    hass.states.async_set(
        SOC, soc, {"unit_of_measurement": "%", "device_class": "battery"}
    )
    for entity_id, value in zip(
        ("sensor.pv_today", "sensor.pv_tomorrow", "sensor.pv_day_after"),
        pv,
        strict=True,
    ):
        hass.states.async_set(entity_id, value, {"unit_of_measurement": "kWh"})


def _register_setpoint_service(hass):
    """Fake input_number.set_value like a real input_number (the feed-in
    executor reads the value back) — pattern: test_feedin.py."""

    async def set_value(call):
        hass.states.async_set(call.data["entity_id"], str(float(call.data["value"])))

    hass.services.async_register("input_number", "set_value", set_value)


async def _setup(
    hass,
    *,
    meter=True,
    feedin=False,
    loads=(),
    at=T0,
    pv=("10.0", "12.0", "8.0"),
    export_state="5000.0",
    hass_storage=None,
    store_payload=None,
):
    """Config entry (optionally with the export meter / feed-in / load
    subentries) set up INSIDE the pinned window: the setup-time refresh
    already learns the counter baselines and stamps their timestamps, so it
    must not see the real clock. `loads` are (title, data) pairs; `meter`/
    `feedin` toggle the config sections; `store_payload` is injected into the
    runtime store before setup (restart-restore scenarios)."""
    _set_input_states(hass, pv=pv)
    if meter:
        hass.states.async_set(
            EXPORT,
            export_state,
            {
                "unit_of_measurement": "kWh",
                "device_class": "energy",
                "state_class": "total_increasing",
            },
        )
    if feedin:
        hass.states.async_set(SETPOINT, "0")
        hass.states.async_set(BATT_POWER, "0")
        _register_setpoint_service(hass)
    data = {**BASE_DATA}
    if meter:
        data[CONF_EXPORT_METER_ENTITY] = EXPORT
    if feedin:
        data.update(FEEDIN_DATA)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=data,
        title="Battery Manager",
        version=2,
        subentries_data=[
            ConfigSubentryData(
                data=load_data,
                subentry_type=SUBENTRY_TYPE_LOAD,
                title=title,
                unique_id=None,
            )
            for title, load_data in loads
        ],
    )
    entry.add_to_hass(hass)
    if store_payload is not None:
        key = f"{DOMAIN}.{entry.entry_id}"
        hass_storage[key] = {
            "version": 1,
            "minor_version": 1,
            "key": key,
            "data": store_payload,
        }
    with patch.object(dt_util, "now", return_value=at):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    coordinator = hass.data[DOMAIN][entry.entry_id]
    # The tests drive every refresh explicitly (pinned in time); the live
    # tracked-entity listener would otherwise schedule debounced refreshes on
    # every counter change, racing the pinned passes (pattern: test_feedin).
    # The event WIRING is asserted via _tracked_entities() instead.
    coordinator._listeners_setup = False
    _cancel_debounce(coordinator)
    assert coordinator.last_update_success
    return coordinator, entry


async def _refresh_at(hass, coordinator, moment, soc):
    """One full coordinator refresh pinned to `moment`, with the SOC nudged
    to a NEW value first — a constant SOC across pinned cycles trips the
    house-SOC stale watchdog and the realized accounting (end of the
    successful cycle) never runs."""
    hass.states.async_set(
        SOC, str(soc), {"unit_of_measurement": "%", "device_class": "battery"}
    )
    with patch.object(dt_util, "now", return_value=moment):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    _cancel_debounce(coordinator)
    assert coordinator.last_update_success


def _subentry_id(entry, title):
    return next(sid for sid, sub in entry.subentries.items() if sub.title == title)


def _realized_sensor_eid(registry, entry, key):
    return registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")


# ---------------------------------------------------------------------------
# Baseline learning + corrected export (decisions 3/4)
# ---------------------------------------------------------------------------


async def test_first_refresh_learns_baseline_without_deltas(hass):
    """The setup-time refresh records the counter readings as the baseline:
    no delta is booked on first sight, all day counters stay 0."""
    coordinator, _entry = await _setup(
        hass,
        loads=[
            (
                LOAD_A,
                {CONF_LOAD_ENERGY_ENTITY: LOAD_A_ENERGY, CONF_LOAD_POWER_W: 400.0},
            )
        ],
    )
    hass.states.async_set(LOAD_A_ENERGY, "10.0", {"unit_of_measurement": "kWh"})
    # The load counter appears AFTER setup: the next refresh learns it as a
    # fresh baseline (still no delta), the export meter was learned at setup.
    await _refresh_at(hass, coordinator, T0 + FIVE_MIN, 55.1)

    realized = coordinator._realized
    assert realized["lost_wh"] == 0.0
    assert realized["prevented_wh"] == 0.0
    assert realized["true_export_total_wh"] == 0.0
    assert realized["date"] == T0.date().isoformat()
    assert realized["last_readings"] == {EXPORT: 5000.0, LOAD_A_ENERGY: 10.0}
    # The export meter and the per-load energy counter wake the coordinator
    # (event-driven booking, same pattern as the feed-in trim).
    assert EXPORT in coordinator._tracked_entities()
    assert LOAD_A_ENERGY in coordinator._tracked_entities()
    assert coordinator.data["realized"]["lost_surplus_realized_kwh"] == 0.0


async def test_corrected_export_subtracts_external_load(hass):
    """Export meter +500 Wh while the EXTERNAL load (fed through the export
    measuring node) really consumed +20 Wh: the corrected export books 480 Wh
    into the day counter AND the monotone true-export total; the load's 20 Wh
    land in the prevented counter."""
    coordinator, _entry = await _setup(
        hass,
        loads=[
            (
                LOAD_B,
                {
                    CONF_LOAD_ENERGY_ENTITY: LOAD_B_ENERGY,
                    CONF_LOAD_IN_HOUSE: False,
                    CONF_LOAD_POWER_W: 400.0,
                },
            )
        ],
    )
    hass.states.async_set(LOAD_B_ENERGY, "10.0", {"unit_of_measurement": "kWh"})
    await _refresh_at(hass, coordinator, T0 + FIVE_MIN, 55.1)  # baseline

    hass.states.async_set(EXPORT, "5000.5", KWH)
    hass.states.async_set(LOAD_B_ENERGY, "10.02", KWH)
    await _refresh_at(hass, coordinator, T0 + 2 * FIVE_MIN, 55.2)

    realized = coordinator._realized
    assert realized["lost_wh"] == pytest.approx(480.0)
    assert realized["true_export_total_wh"] == pytest.approx(480.0)
    assert realized["prevented_wh"] == pytest.approx(20.0)
    # Persisted in exactly the store shape (decision 8).
    stored = coordinator._persistent_payload()["realized"]
    assert stored["lost_wh"] == pytest.approx(480.0)
    assert stored["true_export_total_wh"] == pytest.approx(480.0)
    assert stored["last_readings"] == {EXPORT: 5000.5, LOAD_B_ENERGY: 10.02}


async def test_negative_corrected_delta_clamps_at_zero(hass):
    """Metering race (load counter advanced, export meter not yet): external
    delta > export delta clamps the corrected export at 0 instead of booking
    a negative lost surplus."""
    coordinator, _entry = await _setup(
        hass,
        loads=[
            (
                LOAD_B,
                {
                    CONF_LOAD_ENERGY_ENTITY: LOAD_B_ENERGY,
                    CONF_LOAD_IN_HOUSE: False,
                    CONF_LOAD_POWER_W: 400.0,
                },
            )
        ],
    )
    hass.states.async_set(LOAD_B_ENERGY, "10.0", {"unit_of_measurement": "kWh"})
    await _refresh_at(hass, coordinator, T0 + FIVE_MIN, 55.1)  # baseline

    hass.states.async_set(EXPORT, "5000.01", KWH)  # +10 Wh
    hass.states.async_set(LOAD_B_ENERGY, "10.05", KWH)  # +50 Wh
    await _refresh_at(hass, coordinator, T0 + 2 * FIVE_MIN, 55.2)

    realized = coordinator._realized
    assert realized["lost_wh"] == 0.0
    assert realized["true_export_total_wh"] == 0.0
    # The load's real consumption still counts as prevented export.
    assert realized["prevented_wh"] == pytest.approx(50.0)
    # The 40 Wh the export delta could not absorb are CARRIED FORWARD, not
    # discarded (review 2026-08-03).
    assert realized["external_debt_wh"] == pytest.approx(40.0)


async def test_external_correction_debt_is_applied_on_later_cycles(hass):
    """A coarse load counter (Fritz!Powerline: ~0.1 kWh chunks) delivers its
    whole chunk in one cycle while the export meter trickles. Clamping the
    correction per cycle discarded nearly all of it and the metering artefact
    survived; the remainder now waits as a debt and is applied to the
    following export deltas until it is worked off."""
    coordinator, _entry = await _setup(
        hass,
        loads=[
            (
                LOAD_B,
                {
                    CONF_LOAD_ENERGY_ENTITY: LOAD_B_ENERGY,
                    CONF_LOAD_IN_HOUSE: False,
                    CONF_LOAD_POWER_W: 400.0,
                },
            )
        ],
    )
    hass.states.async_set(LOAD_B_ENERGY, "10.0", {"unit_of_measurement": "kWh"})
    await _refresh_at(hass, coordinator, T0 + FIVE_MIN, 55.1)  # baseline

    # The load's 100 Wh chunk lands while the export meter advanced only 8 Wh.
    hass.states.async_set(EXPORT, "5000.008", KWH)
    hass.states.async_set(LOAD_B_ENERGY, "10.1", KWH)
    await _refresh_at(hass, coordinator, T0 + 2 * FIVE_MIN, 55.2)
    assert coordinator._realized["lost_wh"] == 0.0
    assert coordinator._realized["external_debt_wh"] == pytest.approx(92.0)

    # Next cycle: export advances 60 Wh, all of it still the load's draw.
    hass.states.async_set(EXPORT, "5000.068", KWH)
    await _refresh_at(hass, coordinator, T0 + 3 * FIVE_MIN, 55.3)
    assert coordinator._realized["lost_wh"] == 0.0
    assert coordinator._realized["external_debt_wh"] == pytest.approx(32.0)

    # And once the export outruns the remaining debt, the surplus is booked.
    hass.states.async_set(EXPORT, "5000.168", KWH)
    await _refresh_at(hass, coordinator, T0 + 4 * FIVE_MIN, 55.4)
    assert coordinator._realized["lost_wh"] == pytest.approx(68.0)
    assert coordinator._realized["external_debt_wh"] == 0.0
    # Total booked + debt == export - external consumption, exactly.
    assert coordinator._realized["true_export_total_wh"] == pytest.approx(68.0)


async def test_external_delta_without_export_movement_becomes_debt(hass):
    """A load chunk arriving on a cycle where the export meter did not change
    at all used to be thrown away entirely (`export_delta is None`)."""
    coordinator, _entry = await _setup(
        hass,
        loads=[
            (
                LOAD_B,
                {
                    CONF_LOAD_ENERGY_ENTITY: LOAD_B_ENERGY,
                    CONF_LOAD_IN_HOUSE: False,
                    CONF_LOAD_POWER_W: 400.0,
                },
            )
        ],
    )
    hass.states.async_set(LOAD_B_ENERGY, "10.0", {"unit_of_measurement": "kWh"})
    await _refresh_at(hass, coordinator, T0 + FIVE_MIN, 55.1)  # baseline

    hass.states.async_set(LOAD_B_ENERGY, "10.05", KWH)  # export meter unchanged
    await _refresh_at(hass, coordinator, T0 + 2 * FIVE_MIN, 55.2)
    assert coordinator._realized["external_debt_wh"] == pytest.approx(50.0)

    hass.states.async_set(EXPORT, "5000.08", KWH)  # +80 Wh, 50 of them the load
    await _refresh_at(hass, coordinator, T0 + 3 * FIVE_MIN, 55.3)
    assert coordinator._realized["lost_wh"] == pytest.approx(30.0)
    assert coordinator._realized["external_debt_wh"] == 0.0


async def test_export_delta_after_a_cycle_gap_is_booked_not_dropped(hass):
    """The export meter used to be passed no cap power, so its jump cap was
    the flat 2 kWh absolute fallback: a legitimate accrual over a cycle gap
    (planner failure, HA restart) was discarded whole — and since the baseline
    advances anyway, that energy was lost forever."""
    coordinator, _entry = await _setup(hass)
    await _refresh_at(hass, coordinator, T0 + FIVE_MIN, 55.1)  # baseline

    # 25 minutes without a successful cycle, exporting hard: +2.4 kWh in one
    # delta — above the absolute fallback, far below 3 x PV peak x elapsed.
    hass.states.async_set(EXPORT, "5002.4", KWH)
    await _refresh_at(hass, coordinator, T0 + FIVE_MIN + timedelta(minutes=25), 55.6)
    assert coordinator._realized["lost_wh"] == pytest.approx(2400.0)
    assert coordinator._realized["true_export_total_wh"] == pytest.approx(2400.0)


async def test_export_counter_in_wh_is_scaled_by_its_own_unit(hass):
    """The options-flow selector filters by device class only, so a Wh-unit
    counter is a valid pick. Assuming kWh mis-scaled it by 1000x and the jump
    filter then swallowed every delta — all realized sensors stuck at 0."""
    coordinator, _entry = await _setup(hass)
    hass.states.async_set(EXPORT, "5000000", {"unit_of_measurement": "Wh"})
    await _refresh_at(hass, coordinator, T0 + FIVE_MIN, 55.1)  # baseline

    hass.states.async_set(EXPORT, "5000480", {"unit_of_measurement": "Wh"})
    await _refresh_at(hass, coordinator, T0 + 2 * FIVE_MIN, 55.2)
    assert coordinator._realized["lost_wh"] == pytest.approx(480.0)


# ---------------------------------------------------------------------------
# Jump filter (decision 7 / R-Spec): implausible, negative, unavailable
# ---------------------------------------------------------------------------


async def test_jump_filter_drops_implausible_negative_and_relearns(hass):
    """The jump filter drops deltas above the plausibility cap (3.0 x 400 W x
    5 min = 100 Wh here) and negative deltas — but the reading ALWAYS advances
    so one bad jump does not poison later deltas; an unavailable dropout
    re-learns the baseline instead of dumping the outage gap as one delta."""
    coordinator, _entry = await _setup(
        hass,
        loads=[
            (
                LOAD_A,
                {CONF_LOAD_ENERGY_ENTITY: LOAD_A_ENERGY, CONF_LOAD_POWER_W: 400.0},
            )
        ],
    )
    hass.states.async_set(LOAD_A_ENERGY, "10.0", {"unit_of_measurement": "kWh"})
    await _refresh_at(hass, coordinator, T0 + FIVE_MIN, 55.1)  # baseline
    readings = coordinator._realized["last_readings"]

    # Implausible jump: +150 Wh in 5 min > 100 Wh cap -> dropped, reading
    # and timestamp advance anyway.
    hass.states.async_set(LOAD_A_ENERGY, "10.15", KWH)
    await _refresh_at(hass, coordinator, T0 + 2 * FIVE_MIN, 55.2)
    assert coordinator._realized["prevented_wh"] == 0.0
    assert readings[LOAD_A_ENERGY] == 10.15

    # Plausible step from the NEW baseline: +50 Wh <= cap -> counted.
    hass.states.async_set(LOAD_A_ENERGY, "10.20", KWH)
    await _refresh_at(hass, coordinator, T0 + 3 * FIVE_MIN, 55.3)
    assert coordinator._realized["prevented_wh"] == pytest.approx(50.0)

    # Negative delta (counter reset / telemetry glitch): dropped, the reading
    # still advances to the reset value.
    hass.states.async_set(LOAD_A_ENERGY, "10.10", KWH)
    await _refresh_at(hass, coordinator, T0 + 4 * FIVE_MIN, 55.4)
    assert coordinator._realized["prevented_wh"] == pytest.approx(50.0)
    assert readings[LOAD_A_ENERGY] == 10.10

    # Back up: +50 Wh from the reset baseline -> counted.
    hass.states.async_set(LOAD_A_ENERGY, "10.15", KWH)
    await _refresh_at(hass, coordinator, T0 + 5 * FIVE_MIN, 55.5)
    assert coordinator._realized["prevented_wh"] == pytest.approx(100.0)

    # Unavailable: nothing booked, the re-learn flag arms.
    hass.states.async_set(LOAD_A_ENERGY, "unavailable", KWH)
    await _refresh_at(hass, coordinator, T0 + 6 * FIVE_MIN, 55.6)
    assert coordinator._realized["prevented_wh"] == pytest.approx(100.0)
    assert LOAD_A_ENERGY in coordinator._realized_relearn

    # First valid state after the dropout: fresh baseline — the gap to 12.0
    # is NOT dumped as one delta.
    hass.states.async_set(LOAD_A_ENERGY, "12.0", KWH)
    await _refresh_at(hass, coordinator, T0 + 7 * FIVE_MIN, 55.7)
    assert coordinator._realized["prevented_wh"] == pytest.approx(100.0)
    assert readings[LOAD_A_ENERGY] == 12.0
    assert LOAD_A_ENERGY not in coordinator._realized_relearn

    # ...and normal counting resumes from the re-learned baseline.
    hass.states.async_set(LOAD_A_ENERGY, "12.05", KWH)
    await _refresh_at(hass, coordinator, T0 + 8 * FIVE_MIN, 55.8)
    assert coordinator._realized["prevented_wh"] == pytest.approx(150.0)


# ---------------------------------------------------------------------------
# Prevented export (decision 5): ALL loads count; runtime x power fallback
# ---------------------------------------------------------------------------


async def test_prevented_sums_energy_deltas_and_runtime_estimate(hass):
    """Prevented realized = the real energy of every load with an energy
    counter (in-house AND external); a load without a counter is estimated
    from the runtime-counter delta x planning power (learned wins over
    nominal). No PV in this test: the planner never activates the loads, so
    the runtime counter only moves via the test's own injection."""
    coordinator, entry = await _setup(
        hass,
        pv=("0.0", "0.0", "0.0"),
        loads=[
            (
                LOAD_A,
                {CONF_LOAD_ENERGY_ENTITY: LOAD_A_ENERGY, CONF_LOAD_POWER_W: 400.0},
            ),
            (
                LOAD_B,
                {
                    CONF_LOAD_ENERGY_ENTITY: LOAD_B_ENERGY,
                    CONF_LOAD_IN_HOUSE: False,
                    CONF_LOAD_POWER_W: 400.0,
                },
            ),
            (LOAD_C, {CONF_LOAD_POWER_W: 400.0}),  # no energy counter
        ],
    )
    hass.states.async_set(LOAD_A_ENERGY, "10.0", {"unit_of_measurement": "kWh"})
    hass.states.async_set(LOAD_B_ENERGY, "20.0", {"unit_of_measurement": "kWh"})
    await _refresh_at(hass, coordinator, T0 + FIVE_MIN, 55.1)  # baselines

    # In-house +30 Wh, external +20 Wh: BOTH count as prevented; with the
    # export meter unmoved there is no export delta to correct.
    hass.states.async_set(LOAD_A_ENERGY, "10.03", KWH)
    hass.states.async_set(LOAD_B_ENERGY, "20.02", KWH)
    await _refresh_at(hass, coordinator, T0 + 2 * FIVE_MIN, 55.2)
    assert coordinator._realized["prevented_wh"] == pytest.approx(50.0)
    assert coordinator._realized["lost_wh"] == 0.0

    # Load C without a counter: 30 min of runtime x 400 W nominal = 200 Wh.
    # The baseline checkpoint was armed on the refresh above.
    c_id = _subentry_id(entry, LOAD_C)
    coordinator._load_runtime_seconds[c_id] = 1800.0
    await _refresh_at(hass, coordinator, T0 + 3 * FIVE_MIN, 55.3)
    assert coordinator._realized["prevented_wh"] == pytest.approx(250.0)

    # The learned planning power wins over the nominal: another 30 min at the
    # learned 500 W -> +250 Wh.
    coordinator._load_learned_power_w[c_id] = 500.0
    coordinator._load_runtime_seconds[c_id] = 3600.0
    await _refresh_at(hass, coordinator, T0 + 4 * FIVE_MIN, 55.4)
    assert coordinator._realized["prevented_wh"] == pytest.approx(500.0)


# ---------------------------------------------------------------------------
# Midnight rollover (decision 8)
# ---------------------------------------------------------------------------


async def test_midnight_rollover_resets_day_counters_keeps_total(hass):
    """At local midnight the day counters restart at 0 and the last readings
    carry over as the new day's baseline (no artificial jump), while the
    true-export total keeps accumulating monotonically."""
    coordinator, _entry = await _setup(hass)
    day = T0.date()
    tzinfo = T0.tzinfo
    # Pre-/post-midnight refreshes stay after the pinned setup time
    # regardless of the host timezone.
    pre_midnight = datetime.combine(day, datetime.min.time(), tzinfo=tzinfo).replace(
        hour=23, minute=55
    )
    post_midnight = datetime.combine(
        day + timedelta(days=1), datetime.min.time(), tzinfo=tzinfo
    ).replace(hour=0, minute=5)

    hass.states.async_set(EXPORT, "5000.1", KWH)  # +100 Wh today
    await _refresh_at(hass, coordinator, pre_midnight, 55.1)
    assert coordinator._realized["lost_wh"] == pytest.approx(100.0)
    assert coordinator._realized["true_export_total_wh"] == pytest.approx(100.0)

    hass.states.async_set(EXPORT, "5000.2", KWH)  # +100 Wh on the new day
    await _refresh_at(hass, coordinator, post_midnight, 55.2)
    realized = coordinator._realized
    # Exactly the new day's 100 Wh — not 200 (no carry-over) and not
    # 100 + the whole counter (the reading carried over as the baseline).
    assert realized["lost_wh"] == pytest.approx(100.0)
    assert realized["true_export_total_wh"] == pytest.approx(200.0)
    assert realized["last_readings"][EXPORT] == 5000.2
    assert realized["date"] == (day + timedelta(days=1)).isoformat()
    assert coordinator.data["realized"]["date"] == (day + timedelta(days=1)).isoformat()
    assert coordinator.data["realized"]["lost_surplus_realized_kwh"] == 0.1


# ---------------------------------------------------------------------------
# Restart restore (decision 8): same-day counters, stale-date, flush+reload
# ---------------------------------------------------------------------------


def _stored_realized(date_iso, **overrides):
    payload = {
        "date": date_iso,
        "lost_wh": 480.0,
        "prevented_wh": 20.0,
        "feedin_wh": 0.0,
        "true_export_total_wh": 1480.0,
        "last_readings": {EXPORT: 5000.5},
    }
    payload.update(overrides)
    return {"realized": payload}


async def test_restart_same_day_restores_counters_and_readings(hass, hass_storage):
    """A restart on the SAME local day restores the day counters, the
    monotone total and the last readings: the next delta books against the
    restored reading (a restored reading has no timestamp, so the absolute
    2000 Wh jump cap applies). The persisted feed-in integral re-arms the
    executor's delivered-Wh cursor."""
    coordinator, _entry = await _setup(
        hass,
        export_state="5000.6",  # +100 Wh while HA was down
        hass_storage=hass_storage,
        store_payload=_stored_realized(T0.date().isoformat(), feedin_wh=250.0),
    )
    realized = coordinator._realized
    assert realized["lost_wh"] == pytest.approx(580.0)
    assert realized["prevented_wh"] == pytest.approx(20.0)
    assert realized["true_export_total_wh"] == pytest.approx(1580.0)
    assert realized["last_readings"][EXPORT] == 5000.6
    # The feed-in integral is restored from the same-day feedin_wh.
    assert coordinator._feedin_delivered is not None
    assert coordinator._feedin_delivered[1] == 250.0
    with patch.object(dt_util, "now", return_value=T0 + FIVE_MIN):
        assert coordinator.feedin_delivered_today_kwh() == 0.25


async def test_restart_stale_date_starts_day_counters_at_zero(hass, hass_storage):
    """A store from YESTERDAY: the day counters start at 0 (the first refresh
    rolls the date over), but the readings still carry — the export delta
    since shutdown books normally and the monotone total survives."""
    coordinator, _entry = await _setup(
        hass,
        export_state="5000.6",
        hass_storage=hass_storage,
        store_payload=_stored_realized((T0.date() - timedelta(days=1)).isoformat()),
    )
    realized = coordinator._realized
    assert realized["date"] == T0.date().isoformat()
    assert realized["lost_wh"] == pytest.approx(100.0)  # only the new delta
    assert realized["prevented_wh"] == 0.0
    assert realized["true_export_total_wh"] == pytest.approx(1580.0)
    assert realized["last_readings"][EXPORT] == 5000.6
    # No same-day feed-in integral -> no delivered cursor restore.
    assert coordinator._feedin_delivered is None


async def test_restart_via_flush_and_reload_restores_counters(hass):
    """The real write path: accumulated counters flushed via
    async_flush_persistent_state survive an unload/reload cycle on the same
    local day (the unload itself flushes too — belt and braces against the
    10 s delayed save)."""
    coordinator, entry = await _setup(
        hass,
        loads=[
            (
                LOAD_B,
                {
                    CONF_LOAD_ENERGY_ENTITY: LOAD_B_ENERGY,
                    CONF_LOAD_IN_HOUSE: False,
                    CONF_LOAD_POWER_W: 400.0,
                },
            )
        ],
    )
    hass.states.async_set(LOAD_B_ENERGY, "10.0", {"unit_of_measurement": "kWh"})
    await _refresh_at(hass, coordinator, T0 + FIVE_MIN, 55.1)
    hass.states.async_set(EXPORT, "5000.5", KWH)
    hass.states.async_set(LOAD_B_ENERGY, "10.02", KWH)
    await _refresh_at(hass, coordinator, T0 + 2 * FIVE_MIN, 55.2)
    assert coordinator._realized["lost_wh"] == pytest.approx(480.0)

    await coordinator.async_flush_persistent_state()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    with patch.object(dt_util, "now", return_value=T0 + 3 * FIVE_MIN):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    reloaded = hass.data[DOMAIN][entry.entry_id]
    assert reloaded is not coordinator
    reloaded._listeners_setup = False
    _cancel_debounce(reloaded)
    assert reloaded._realized["lost_wh"] == pytest.approx(480.0)
    assert reloaded._realized["prevented_wh"] == pytest.approx(20.0)
    assert reloaded._realized["true_export_total_wh"] == pytest.approx(480.0)
    assert reloaded._realized["last_readings"] == {
        EXPORT: 5000.5,
        LOAD_B_ENERGY: 10.02,
    }


# ---------------------------------------------------------------------------
# The `realized` data block: exact keys, Ist+forecast composition, gate
# ---------------------------------------------------------------------------


async def test_realized_data_block_shape_and_composition(hass):
    """The data block carries exactly the sensor keys; the today values are
    realized (measured) + the fresh per-day forecast for the rest of the day,
    tomorrow is pure forecast (operator decisions 1/4/5)."""
    coordinator, _entry = await _setup(hass)
    hass.states.async_set(EXPORT, "5000.5", KWH)
    await _refresh_at(hass, coordinator, T0 + FIVE_MIN, 55.1)

    realized = coordinator.data["realized"]
    assert set(realized) == {
        "date",
        "lost_surplus_realized_kwh",
        "lost_surplus_today_kwh",
        "lost_surplus_tomorrow_kwh",
        "prevented_export_realized_kwh",
        "prevented_export_today_kwh",
        "prevented_export_tomorrow_kwh",
        "early_feed_in_realized_kwh",
        "true_export_total_kwh",
    }
    assert realized["date"] == T0.date().isoformat()
    assert realized["lost_surplus_realized_kwh"] == 0.5
    assert realized["prevented_export_realized_kwh"] == 0.0
    assert realized["early_feed_in_realized_kwh"] == 0.0
    assert realized["true_export_total_kwh"] == 0.5

    by_date = {e["date"]: e for e in coordinator.data["daily_surplus"]}
    today_fc = by_date[T0.date().isoformat()]
    tomorrow_fc = by_date[(T0.date() + timedelta(days=1)).isoformat()]
    assert realized["lost_surplus_today_kwh"] == pytest.approx(
        round(0.5 + today_fc["lost_surplus_kwh"], 3), abs=0.001
    )
    assert realized["lost_surplus_tomorrow_kwh"] == pytest.approx(
        tomorrow_fc["lost_surplus_kwh"], abs=0.001
    )
    assert realized["prevented_export_today_kwh"] == pytest.approx(
        today_fc["prevented_export_kwh"], abs=0.001
    )
    assert realized["prevented_export_tomorrow_kwh"] == pytest.approx(
        tomorrow_fc["prevented_export_kwh"], abs=0.001
    )


async def test_realized_data_block_absent_without_meter(hass):
    """Backend-compat gate: without an export meter the `realized` key stays
    absent from the coordinator data (the counters are still maintained
    in-memory for the feed-in integral, but nothing is published)."""
    coordinator, _entry = await _setup(hass, meter=False)
    assert "realized" not in coordinator.data
    await _refresh_at(hass, coordinator, T0 + FIVE_MIN, 55.1)
    assert "realized" not in coordinator.data


async def test_soc_forecast_attribute_carries_realized_block(hass):
    """R14: the card reads the block off the `soc_forecast` ATTRIBUTES, not
    off the coordinator data — the sensor has to republish it, else the card's
    Ist branch is dead code."""
    coordinator, entry = await _setup(hass)
    hass.states.async_set(EXPORT, "5000.5", KWH)
    await _refresh_at(hass, coordinator, T0 + FIVE_MIN, 55.1)

    registry = er.async_get(hass)
    eid = _realized_sensor_eid(registry, entry, "soc_forecast")
    attrs = hass.states.get(eid).attributes
    assert attrs["realized"] == coordinator.data["realized"]


async def test_soc_forecast_attribute_omits_realized_without_meter(hass):
    """The key must be ABSENT, not an empty dict: the card branches on the
    attribute's existence and `{}` is truthy in JS — it would render a
    permanent "(Ist 0.0)" instead of the unchanged pure-forecast line."""
    coordinator, entry = await _setup(hass, meter=False)
    await _refresh_at(hass, coordinator, T0 + FIVE_MIN, 55.1)

    registry = er.async_get(hass)
    eid = _realized_sensor_eid(registry, entry, "soc_forecast")
    assert "realized" not in hass.states.get(eid).attributes


# ---------------------------------------------------------------------------
# Sensors: state classes, values, config gates, stale-registry cleanup
# ---------------------------------------------------------------------------


async def test_realized_sensors_exist_with_state_classes_and_values(hass):
    """Sensor set 1-6 + 8 with the export meter configured: day counters are
    state_class TOTAL (reset at midnight), the corrected true export is
    TOTAL_INCREASING (energy-dashboard compatible); all kWh/energy."""
    coordinator, entry = await _setup(
        hass,
        loads=[
            (
                LOAD_B,
                {
                    CONF_LOAD_ENERGY_ENTITY: LOAD_B_ENERGY,
                    CONF_LOAD_IN_HOUSE: False,
                    CONF_LOAD_POWER_W: 400.0,
                },
            )
        ],
    )
    registry = er.async_get(hass)
    eids = {
        key: _realized_sensor_eid(registry, entry, key) for key in REALIZED_SENSOR_KEYS
    }
    assert all(eids.values()), f"missing sensors: {eids}"
    for key in REALIZED_SENSOR_KEYS:
        attrs = hass.states.get(eids[key]).attributes
        expected = (
            SensorStateClass.TOTAL_INCREASING
            if key == ENTITY_TRUE_EXPORT_ENERGY
            else SensorStateClass.TOTAL
        )
        assert attrs["state_class"] == expected, key
        assert attrs["device_class"] == SensorDeviceClass.ENERGY
        assert attrs["unit_of_measurement"] == UnitOfEnergy.KILO_WATT_HOUR

    hass.states.async_set(LOAD_B_ENERGY, "10.0", {"unit_of_measurement": "kWh"})
    await _refresh_at(hass, coordinator, T0 + FIVE_MIN, 55.1)
    hass.states.async_set(EXPORT, "5000.5", KWH)
    hass.states.async_set(LOAD_B_ENERGY, "10.02", KWH)
    await _refresh_at(hass, coordinator, T0 + 2 * FIVE_MIN, 55.2)

    lost = hass.states.get(eids[ENTITY_LOST_SURPLUS_REALIZED_TODAY])
    assert float(lost.state) == pytest.approx(0.48)
    # Ist + Restprognose: today >= realized.
    today = hass.states.get(eids[ENTITY_LOST_SURPLUS_TODAY])
    assert float(today.state) >= float(lost.state)
    prevented = hass.states.get(eids[ENTITY_PREVENTED_EXPORT_REALIZED_TODAY])
    assert float(prevented.state) == pytest.approx(0.02)
    true_export = hass.states.get(eids[ENTITY_TRUE_EXPORT_ENERGY])
    assert float(true_export.state) == pytest.approx(0.48)


async def test_realized_sensors_absent_without_meter(hass):
    """Without an export meter NONE of the realized sensors (1-6 + 8) is
    created — the feature is forecast-only then."""
    coordinator, entry = await _setup(hass, meter=False)
    registry = er.async_get(hass)
    for key in REALIZED_SENSOR_KEYS:
        assert _realized_sensor_eid(registry, entry, key) is None, key
    assert "realized" not in coordinator.data


async def test_early_feed_in_realized_sensor_gate_and_value(hass):
    """Sensor 7 rides on the F-FEEDIN gate ALONE (no export meter needed):
    it reads the executor's delivered-Wh integral directly; the same value is
    bridged into the persisted `realized` store block."""
    coordinator, entry = await _setup(hass, meter=False, feedin=True)
    registry = er.async_get(hass)
    eid = _realized_sensor_eid(registry, entry, ENTITY_EARLY_FEED_IN_REALIZED_TODAY)
    assert eid is not None
    assert hass.states.get(eid).attributes["state_class"] == SensorStateClass.TOTAL
    assert float(hass.states.get(eid).state) == 0.0

    # 250 Wh delivered today (executor integral, pinned day).
    coordinator._feedin_delivered = (T0.date(), 250.0, T0)
    coordinator._feedin_last_written_w = 0.0
    await _refresh_at(hass, coordinator, T0 + FIVE_MIN, 55.1)

    assert float(hass.states.get(eid).state) == pytest.approx(0.25)
    assert coordinator._persistent_payload()["realized"]["feedin_wh"] == 250.0


async def test_early_feed_in_realized_sensor_absent_without_feedin(hass):
    """The feed-in realized sensor is not created when F-FEEDIN is off —
    independent of the export meter (which gates only sensors 1-6 + 8)."""
    _coordinator, entry = await _setup(hass, meter=True, feedin=False)
    registry = er.async_get(hass)
    assert (
        _realized_sensor_eid(registry, entry, ENTITY_EARLY_FEED_IN_REALIZED_TODAY)
        is None
    )
    # ...while the meter-gated sensors exist.
    assert (
        _realized_sensor_eid(registry, entry, ENTITY_LOST_SURPLUS_REALIZED_TODAY)
        is not None
    )


async def test_stale_realized_sensors_removed_when_gates_close(hass):
    """Unwiring via the options flow (meter cleared, feed-in disabled)
    reloads the entry and drops the now-stale realized sensors from the
    registry instead of letting them linger."""
    coordinator, entry = await _setup(hass, meter=True, feedin=True)
    registry = er.async_get(hass)
    for key in (*REALIZED_SENSOR_KEYS, ENTITY_EARLY_FEED_IN_REALIZED_TODAY):
        assert _realized_sensor_eid(registry, entry, key) is not None, key

    result = await hass.config_entries.options.async_init(entry.entry_id)
    payload = _no_change_options_payload(result["data_schema"].schema)
    # Clearing the meter entity (absent = clearable selector field, stored as
    # explicit None) unwires the whole realized sensor set; disabling feed-in
    # drops sensor 7.
    payload["surplus_accounting"] = {}
    payload["early_feed_in"][CONF_FEEDIN_ENABLED] = False
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], payload
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()  # the update listener reloads the entry
    await hass.async_block_till_done()

    for key in (*REALIZED_SENSOR_KEYS, ENTITY_EARLY_FEED_IN_REALIZED_TODAY):
        assert _realized_sensor_eid(registry, entry, key) is None, key
    reloaded = hass.data[DOMAIN][entry.entry_id]
    assert reloaded is not coordinator
    assert "realized" not in reloaded.data
    assert EXPORT not in reloaded._tracked_entities()


async def test_nan_counter_does_not_poison_realized_ledger(hass):
    import math

    hass.states.async_set(LOAD_A_ENERGY, "1.0", KWH)
    coordinator, _entry = await _setup(
        hass,
        loads=(
            (LOAD_A, {CONF_LOAD_POWER_W: 400, CONF_LOAD_ENERGY_ENTITY: LOAD_A_ENERGY}),
        ),
    )
    # Exercise the real ledger with virtual timestamps.
    for index, reading in enumerate(("1.0", "NaN", "1.01", "1.02", "1.03")):
        hass.states.async_set(LOAD_A_ENERGY, reading, KWH)
        coordinator._update_realized_surplus(T0 + FIVE_MIN * index, [])
    assert math.isfinite(coordinator._realized["prevented_wh"])
    assert coordinator._realized["prevented_wh"] == pytest.approx(20.0)
