"""SOC drift is learned only from fresh comparable natural voltage episodes."""

from dataclasses import replace

import pytest

from custom_components.battery_manager.gate_learning import (
    MAX_AGE_SECONDS,
    SAMPLE_SECONDS,
    GateLearner,
    GateSample,
)


def episode(learner, start, soc=65, voltage=49.56, current=0.7):
    for tick in range(0, 2161, 60):
        learner.observe(
            GateSample(start + tick, soc, voltage, current),
            eligible=True,
            capacity_wh=5000,
        )


def test_repeated_recent_soc_drift_replaces_old_coordinate():
    learner = GateLearner()
    learner.bind("battery-a")
    episode(learner, 10000)
    assert learner.bands(12160, (49.56, 49.56, 49.8))[0].on_soc == 65
    # The same voltage/current now corresponds to +1 SOC per day. Old lifetime
    # extrema would stay at 65; the recent weighted coordinate follows the BMS.
    for day in range(1, 5):
        episode(learner, 10000 + day * 86400, 65 + day)
    bands = learner.bands(10000 + 4 * 86400 + 2160, (49.56, 49.56, 49.8))
    assert bands[0].on_soc >= 68
    assert bands[0].off_soc is None
    assert (
        learner.bands(
            10000 + 4 * 86400 + 2160 + MAX_AGE_SECONDS + 1, (49.56, 49.56, 49.8)
        )
        == ()
    )


def test_load_directions_remain_separate_and_no_voltage_extrapolation():
    learner = GateLearner()
    episode(learner, 10000, 65)
    episode(learner, 14000, 80, current=-0.7)
    bands = learner.bands(17000, (49.56, 49.56, 49.8))
    assert [b.on_soc for b in bands] == [80, 65]
    assert bands[1].current_min_a == pytest.approx(0.55)
    assert bands[1].current_max_a == pytest.approx(0.85)
    assert learner.bands(17000, (50.5, 50.6, 50.8)) == ()


def test_clamped_voltage_and_load_transients_do_not_teach_drift():
    learner = GateLearner()
    episode(learner, 10000)
    before = list(learner.samples)
    for tick in range(0, 1800, 60):
        learner.observe(
            GateSample(13000 + tick, 65 + tick / 3600, 49.56, -0.7),
            eligible=False,
            capacity_wh=5000,
        )
    assert learner.samples == before
    for tick in range(0, 1800, 60):
        learner.observe(
            GateSample(16000 + tick, 65, 49.56, 0.7 if tick % 120 else 4),
            eligible=True,
            capacity_wh=5000,
        )
    assert learner.samples == before
    learner.observe(None, eligible=True, capacity_wh=5000)
    assert learner.reason == "measurement_unavailable"


def test_recalibration_starts_new_epoch_without_mixing_soc_scales():
    learner = GateLearner()
    learner.bind("a")
    episode(learner, 10000)
    old = learner.epoch
    assert learner.observe(
        GateSample(12220, 80, 49.56, 0.7), eligible=True, capacity_wh=5000
    )
    assert learner.epoch == old + 1 and learner.samples == []
    assert learner.reason == "recalibration_suspected"
    episode(learner, 12300, 80)
    assert learner.bands(15000, (49.56, 49.56, 49.8))[0].on_soc == 80


def test_large_energy_change_and_gaps_are_not_recalibration_evidence():
    learner = GateLearner()
    learner.observe(GateSample(100, 65, 50, 80), eligible=True, capacity_wh=5000)
    learner.observe(GateSample(400, 57, 50, 80), eligible=True, capacity_wh=5000)
    assert learner.epoch == 0
    learner.observe(GateSample(5000, 30, 50, 0.7), eligible=True, capacity_wh=5000)
    assert learner.epoch == 0


def test_persistence_binding_and_restart_do_not_restore_stability():
    learner = GateLearner()
    learner.bind("a")
    episode(learner, 10000)
    restored = GateLearner()
    restored.restore(learner.export())
    restored.bind("a")
    assert restored.samples == learner.samples
    assert not restored.observe(
        GateSample(13000, 65, 49.56, 0.7), eligible=True, capacity_wh=5000
    )
    assert restored.reason == "settling"
    restored.bind("b")
    assert not restored.samples and restored.reason == "configuration_changed"


@pytest.mark.parametrize(
    "bad",
    [
        None,
        {},
        {"version": 1},
        {"version": 1, "samples": [{}]},
        {"version": 1, "samples": [{"at": 0, "soc": 101, "voltage": 49, "current": 1}]},
        {
            "version": 1,
            "samples": [{"at": 0, "soc": float("nan"), "voltage": 49, "current": 1}],
        },
        {"version": 1, "samples": [], "fingerprint": None, "epoch": 1},
        {"version": 1, "samples": [], "fingerprint": "x", "epoch": "x"},
    ],
)
def test_corrupt_learning_is_disposable(bad):
    learner = GateLearner()
    learner.restore(bad)
    assert learner.samples == [] and learner.fingerprint == ""


def test_broad_contradiction_does_not_become_precise_threshold():
    learner = GateLearner()
    learner.samples = [
        GateSample(10000 + i * 1000, 60 + i * 5, 49.56, 0.7) for i in range(4)
    ]
    assert learner.bands(14000, (49.56, 49.56, 49.8)) == ()


def test_sample_cadence_and_expiry_are_bounded():
    learner = GateLearner()
    episode(learner, 10000)
    assert all(
        b.at - a.at >= SAMPLE_SECONDS
        for a, b in zip(learner.samples[:-1], learner.samples[1:], strict=True)
    )
    old = list(learner.samples)
    learner.observe(
        replace(old[-1], at=old[-1].at + MAX_AGE_SECONDS + 1),
        eligible=False,
        capacity_wh=5000,
    )
    assert learner.samples == []


def test_out_of_order_callback_cannot_erase_recent_learning():
    learner = GateLearner()
    episode(learner, 10000)
    before = list(learner.samples)
    assert not learner.observe(
        GateSample(11000, 65, 49.56, 0.7), eligible=True, capacity_wh=5000
    )
    assert learner.samples == before


def test_partially_shifted_thresholds_cannot_invert_switching_hysteresis():
    learner = GateLearner()
    learner.samples = [
        GateSample(10000 + i * 1000, soc, voltage, 0.7)
        for i in range(3)
        for soc, voltage in [(75, 49.56), (65, 49.8)]
    ]
    assert learner.bands(13000, (49.56, 49.56, 49.8)) == ()


def test_even_small_charging_and_discharging_currents_do_not_mix():
    learner = GateLearner()
    episode(learner, 10000, 65, current=0.1)
    episode(learner, 14000, 80, current=-0.1)
    bands = learner.bands(17000, (49.56, 49.56, 49.8))
    assert [b.on_soc for b in bands] == [80, 65]
