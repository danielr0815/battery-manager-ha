"""SOC reconciliation is informative evidence, never an absolute energy gauge."""

from dataclasses import replace
from itertools import permutations

import pytest

from custom_components.battery_manager.soc_consistency import (
    MAX_MEASUREMENT_AGE_SECONDS,
    MIN_COVERED_SECONDS,
    MIN_SOC_PUBLICATIONS,
    SETTLING_SECONDS,
    WINDOW_SECONDS,
    SocConsistencyMonitor,
    SocObservation,
)


def observation(at, soc=55, power=0):
    return SocObservation(at, soc, power, 50, at, at, at)


def feed(monitor, sample, **kwargs):
    return monitor.observe(sample, power_trace_complete=True, **kwargs)


def test_production_time_and_publication_contract():
    assert (WINDOW_SECONDS, MIN_COVERED_SECONDS, MIN_SOC_PUBLICATIONS) == (300, 60, 3)
    assert (MAX_MEASUREMENT_AGE_SECONDS, SETTLING_SECONDS) == (30, 600)


def test_one_percent_staircase_is_one_informative_episode():
    monitor = SocConsistencyMonitor()
    for tick in range(0, 401, 10):
        feed(
            monitor,
            observation(tick, 55 + min(9, tick // 40), 600),
            capacity_wh=5000,
            eta_charge=0.97,
            eta_discharge=0.97,
        )
    assert monitor.epoch == 1
    assert len(monitor.events) == 1
    event = monitor.events[0]
    assert event["kind"] == "soc_coordinate_adjustment"
    assert event["level"] == "info"
    assert event["detector"] == "rolling_energy"
    assert event["soc_publications"] >= 3
    assert event["covered_seconds"] >= 60


@pytest.mark.parametrize("power,direction", [(3000, 1), (-3000, -1)])
def test_real_energy_and_quantization_do_not_create_epochs(power, direction):
    monitor = SocConsistencyMonitor()
    for tick in range(0, 401, 10):
        feed(
            monitor,
            observation(tick, 55 + direction * (tick // 40), power),
            capacity_wh=5000,
            eta_charge=0.97,
            eta_discharge=0.97,
        )
    assert monitor.epoch == 0
    assert not monitor.events
    assert monitor.diagnostic()["residual"]["qualified"]


def test_energy_uses_actual_publication_change_and_separate_efficiencies():
    monitor = SocConsistencyMonitor()
    feed(
        monitor,
        observation(100, power=360),
        capacity_wh=5000,
        eta_charge=0.9,
        eta_discharge=0.8,
    )
    feed(
        monitor,
        replace(observation(120, power=-180), power_published_at=110),
        capacity_wh=5000,
        eta_charge=0.9,
        eta_discharge=0.8,
    )
    residual = monitor.diagnostic()["residual"]
    assert residual["charge_terminal_wh"] == pytest.approx(1)
    assert residual["discharge_terminal_wh"] == pytest.approx(0.5)
    assert residual["energy_equivalent_soc_delta_percent"] == pytest.approx(
        (1 * 0.9 - 0.5 / 0.8) / 5000 * 100
    )


def test_fast_path_does_not_ignore_configured_discharge_loss():
    monitor = SocConsistencyMonitor()
    feed(monitor, observation(100, soc=55, power=-4990), capacity_wh=5000)
    feed(
        monitor,
        observation(130, soc=51, power=-4990),
        capacity_wh=5000,
        eta_discharge=0.2,
    )
    assert monitor.epoch == 0  # Terminal loss makes this movement plausible.


def test_old_power_hold_cannot_bridge_a_hidden_stale_interval():
    monitor = SocConsistencyMonitor()
    feed(monitor, replace(observation(100), power_published_at=80), capacity_wh=5000)
    feed(monitor, observation(120, soc=64), capacity_wh=5000)
    assert monitor.epoch == 0
    assert monitor.diagnostic()["residual"]["covered_seconds"] == 0


def test_duplicate_soc_publications_do_not_qualify_rolling_evidence():
    monitor = SocConsistencyMonitor()
    for tick in range(0, 31, 10):
        feed(monitor, replace(observation(tick), soc_published_at=0), capacity_wh=5000)
    assert monitor.diagnostic()["residual"]["soc_publications"] == 1
    assert not monitor.diagnostic()["residual"]["qualified"]
    assert not feed(monitor, observation(20, 64), capacity_wh=5000)
    assert monitor.epoch == 0


@pytest.mark.parametrize(
    "bad",
    [
        None,
        replace(observation(100), soc=float("nan")),
        replace(observation(100), power_w=float("inf")),
        replace(observation(100), soc=101),
        replace(observation(100), voltage_v=0),
        replace(observation(100), power_w=5000),
        replace(observation(100), soc_published_at=69.9),
        replace(observation(100), voltage_published_at=101),
    ],
)
def test_invalid_or_missing_evidence_never_accumulates_gap_energy(bad):
    monitor = SocConsistencyMonitor()
    feed(monitor, observation(90), capacity_wh=5000)
    feed(monitor, bad, capacity_wh=5000)
    feed(monitor, observation(110, soc=64), capacity_wh=5000)
    assert monitor.epoch == 0
    assert monitor.diagnostic()["residual"]["covered_seconds"] == 0


def test_fast_jump_and_rolling_share_episode_even_during_settling():
    monitor = SocConsistencyMonitor()
    feed(monitor, observation(100), capacity_wh=5000)
    feed(monitor, observation(110, 64), capacity_wh=5000)
    assert monitor.epoch == 1 and monitor.phase == "reconciling"
    for tick in range(120, 181, 10):
        feed(monitor, observation(tick, 64), capacity_wh=5000)
    assert monitor.phase == "settling"
    feed(monitor, observation(190, 73), capacity_wh=5000)
    assert monitor.epoch == 1 and monitor.phase == "reconciling"
    assert len(monitor.events) == 1
    for tick in range(200, 861, 10):
        feed(monitor, observation(tick, 73), capacity_wh=5000)
    assert monitor.phase == "tracking"
    assert monitor.epoch == 1
    feed(monitor, observation(870, 82), capacity_wh=5000)
    assert monitor.epoch == 2 and len(monitor.events) == 2


def test_settling_counts_covered_time_not_elapsed_wall_time():
    monitor = SocConsistencyMonitor()
    feed(monitor, observation(100), capacity_wh=5000)
    feed(monitor, observation(110, 64), capacity_wh=5000)
    for tick in range(120, 181, 10):
        feed(monitor, observation(tick, 64), capacity_wh=5000)
    assert monitor.phase == "settling"
    feed(monitor, observation(900, 64), capacity_wh=5000)
    assert monitor.phase == "reconciling"
    assert monitor.diagnostic()["settled_covered_seconds"] == 0
    assert monitor.epoch == 1


def test_fingerprint_and_different_source_timestamps_reset_coverage():
    monitor = SocConsistencyMonitor()
    monitor.bind("one")
    feed(monitor, observation(100), capacity_wh=5000)
    monitor.bind("two")
    feed(monitor, observation(110, 64), capacity_wh=5000)
    assert monitor.epoch == 1  # Explicit configuration coordinate change.
    feed(
        monitor, replace(observation(120, 73), power_published_at=109), capacity_wh=5000
    )
    assert monitor.epoch == 1


def test_window_and_events_stay_bounded():
    monitor = SocConsistencyMonitor()
    for tick in range(0, 2000, 10):
        feed(monitor, observation(tick, 55), capacity_wh=5000)
    assert monitor.diagnostic()["residual"]["covered_seconds"] <= WINDOW_SECONDS


def test_fresh_polled_endpoints_do_not_claim_complete_power_trace():
    monitor = SocConsistencyMonitor()
    # Unseen intervening peaks could entirely finance these SOC steps. Merely
    # fresh zero-valued endpoints cannot certify a measured0Wh window.
    for tick in range(0, 301, 5):
        monitor.observe(observation(tick, 55 + tick // 75, 0), capacity_wh=5000)
    assert not monitor.events
    assert monitor.reason == "power_trace_incomplete"
    assert monitor.diagnostic()["residual"] is None


@pytest.mark.parametrize("phase", ["reconciling", "settling"])
def test_restart_keeps_episode_but_never_restores_coverage_or_settling(phase):
    monitor = SocConsistencyMonitor()
    feed(monitor, observation(100), capacity_wh=5000)
    feed(monitor, observation(110, 64), capacity_wh=5000)
    if phase == "settling":
        for tick in range(120, 181, 10):
            feed(monitor, observation(tick, 64), capacity_wh=5000)
    assert monitor.phase == phase
    payload = monitor.export()
    restored = SocConsistencyMonitor()
    restored.restore(payload)
    assert restored.phase == "reconciling"
    assert restored.epoch == monitor.epoch
    assert len(restored.events) == 1
    feed(restored, observation(2000, 73), capacity_wh=5000)
    assert restored.epoch == monitor.epoch
    assert restored.diagnostic()["residual"]["covered_seconds"] == 0
    assert restored.diagnostic()["settled_covered_seconds"] == 0


def test_migration_and_corrupt_coordinate_provenance_are_distinct():
    legacy = SocConsistencyMonitor()
    legacy.restore(None, initial_epoch=3, fingerprint="legacy")
    assert legacy.epoch == 3 and legacy.phase == "tracking"
    assert not legacy.events and not legacy.has_reconciled
    broken = SocConsistencyMonitor()
    broken.restore({"version": 999}, initial_epoch=3)
    assert broken.phase == "reconciling" and broken.has_reconciled


def test_efficiency_change_warms_new_window_without_fabricating_energy():
    monitor = SocConsistencyMonitor()
    feed(monitor, observation(100, 55, 600), capacity_wh=5000)
    feed(monitor, observation(110, 64, 600), capacity_wh=5000, eta_charge=0.8)
    assert monitor.epoch == 0
    assert monitor.diagnostic()["residual"]["covered_seconds"] == 0


def test_restored_event_metadata_is_bounded_informative_and_finite():
    import json

    monitor = SocConsistencyMonitor()
    payload = {
        "version": 1,
        "fingerprint": "synthetic",
        "epoch": 1,
        "generation": 2,
        "phase": "tracking",
        "has_reconciled": True,
        "events": [
            {
                "kind": "soc_coordinate_adjustment",
                "epoch": 1,
                "at": 100,
                "level": "error",
                "detector": "x" * 10000,
                "residual_percent": {"unexpected": "nested"},
                "charge_terminal_wh": float("nan"),
            }
        ]
        * 20,
    }
    monitor.restore(payload)
    assert len(monitor.events) == 8
    assert all(event["level"] == "info" for event in monitor.events)
    assert all(event["detector"] == "unknown" for event in monitor.events)
    assert len(json.dumps(monitor.diagnostic(), allow_nan=False)) < 2048


@pytest.mark.parametrize("order", list(permutations(("soc", "power", "voltage"))))
def test_same_time_channel_updates_count_settling_coverage_once(order):
    monitor = SocConsistencyMonitor()
    monitor.phase = "reconciling"
    monitor.epoch = 1
    monitor.has_reconciled = True
    published = {key: 0.0 for key in order}
    for tick in range(0, 661, 5):
        for channel in order:
            published[channel] = float(tick)
            feed(
                monitor,
                SocObservation(
                    tick,
                    60,
                    0,
                    50,
                    published["soc"],
                    published["power"],
                    published["voltage"],
                ),
                capacity_wh=5000,
            )
            if tick < 660:
                assert monitor.phase != "tracking"
                assert monitor.diagnostic()["settled_covered_seconds"] <= max(
                    0, tick - 60
                )
        if tick == 655:
            assert monitor.diagnostic()["settled_covered_seconds"] == 595
    assert monitor.phase == "tracking"
    assert monitor.diagnostic()["settled_covered_seconds"] == 600
    assert monitor.epoch == 1
