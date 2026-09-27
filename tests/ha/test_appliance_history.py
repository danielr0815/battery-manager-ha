"""User-visible learning provenance must explain observations without teaching."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.battery_manager.appliance_learning import ApplianceLearning

NOW = datetime(2026, 9, 27, 12, tzinfo=UTC)


def cycle(learner, *, key="washer", program="eco", wh=100, start=NOW):
    learner.observe(key, start, True, 600, 1000, complete_start=True, program=program)
    learner.observe(
        key,
        start + timedelta(minutes=5),
        False,
        0,
        1000 + wh,
        complete_start=False,
    )


def test_profile_statistics_preserve_legacy_unknown_dates_and_device_energy_only():
    learner = ApplianceLearning()
    learner.restore({"washer": [100, 200, 900]})
    learner.restore_programs({"washer": {"eco": [[100, 1], [300, 2]]}})
    view = learner.snapshot("washer")
    assert view["state"] == "learned"
    assert view["device_profile"] == {
        "energy_wh": 200,
        "energy_min_wh": 100,
        "energy_max_wh": 900,
        "count": 3,
        "last_learned_at": None,
    }
    assert view["profiles"] == [
        {
            "program": "eco",
            "energy_wh": 200,
            "energy_min_wh": 100,
            "energy_max_wh": 300,
            "duration_h": 1.5,
            "duration_min_h": 1,
            "duration_max_h": 2,
            "count": 2,
            "last_learned_at": None,
        }
    ]
    assert view["history"] == []
    assert view["measurement"] is None
    assert learner.snapshot("new")["state"] == "no_data"


def test_live_counter_measurement_and_completed_history_are_read_only():
    learner = ApplianceLearning()
    learner.observe("washer", NOW, True, 600, 1000, complete_start=True, program="eco")
    learner.observe(
        "washer", NOW + timedelta(minutes=2), True, 600, 1060, complete_start=False
    )
    live = learner.snapshot("washer")
    assert live["state"] == "measuring"
    assert live["measurement"] == {
        "started_at": NOW.isoformat(),
        "observed_at": (NOW + timedelta(minutes=2)).isoformat(),
        "energy_wh": 60,
        "measurement_source": "energy_counter",
        "complete": True,
        "reasons": [],
        "warnings": [],
    }
    live["measurement"]["reasons"].append("externally_changed")
    learner.observe(
        "washer", NOW + timedelta(minutes=5), False, 0, 1100, complete_start=False
    )
    result = learner.snapshot("washer")
    history = result["history"]
    assert history == [
        {
            "program": "eco",
            "started_at": NOW.isoformat(),
            "ended_at": (NOW + timedelta(minutes=5)).isoformat(),
            "duration_h": 5 / 60,
            "energy_wh": 100,
            "measurement_source": "energy_counter",
            "complete": True,
            "accepted": True,
            "reasons": [],
            "warnings": [],
        }
    ]
    assert result["device_profile"]["last_learned_at"] == history[0]["ended_at"]
    assert result["profiles"][0]["last_learned_at"] == history[0]["ended_at"]
    history[0]["accepted"] = False
    result["profiles"].clear()
    assert learner.snapshot("washer")["history"][0]["accepted"]
    assert learner.energy("washer", 999, "eco") == 100
    assert learner.samples["washer"] == [100]  # Reading adds no samples.


def test_partial_observation_is_explained_without_entering_teachable_cycles():
    learner = ApplianceLearning()
    learner.observe("washer", NOW, True, 600, None, complete_start=False)
    assert learner.active == {}
    assert learner.snapshot("washer")["measurement"]["reasons"] == ["missing_start"]
    learner.observe(
        "washer", NOW + timedelta(minutes=5), False, 0, None, complete_start=False
    )
    history = learner.snapshot("washer")["history"]
    assert history[0]["energy_wh"] == 50
    assert history[0]["measurement_source"] == "integrated_power"
    assert not history[0]["complete"]
    assert not history[0]["accepted"]
    assert learner.samples == {}
    assert learner.snapshot("washer")["state"] == "invalid"


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"minutes": 11}, "measurement_gap"),
        ({"minutes": -1}, "clock_changed"),
        ({"valid": False}, "aborted"),
        ({"energy": None, "power": None}, "missing_measurement"),
        ({"energy": 1000}, "invalid_energy"),
        ({"energy": 12000}, "invalid_energy"),
        ({"valid": False, "invalid_reason": "detection_unknown"}, "detection_unknown"),
    ],
)
def test_rejected_cycles_have_concrete_reasons(change, reason):
    learner = ApplianceLearning()
    learner.observe("washer", NOW, True, None, 1000, complete_start=True)
    learner.observe(
        "washer",
        NOW + timedelta(minutes=change.get("minutes", 5)),
        False,
        change.get("power", 0),
        change.get("energy", 1100),
        complete_start=False,
        valid=change.get("valid", True),
        invalid_reason=change.get("invalid_reason"),
    )
    item = learner.snapshot("washer")["history"][0]
    assert reason in item["reasons"]
    assert not item["accepted"]
    assert learner.samples == {}


def test_program_conflict_and_counter_fallback_remain_distinct():
    learner = ApplianceLearning()
    learner.observe("washer", NOW, True, 600, 1000, complete_start=True, program="eco")
    learner.observe(
        "washer",
        NOW + timedelta(minutes=5),
        True,
        600,
        0,
        complete_start=False,
        program="auto",
    )
    learner.observe(
        "washer", NOW + timedelta(minutes=10), False, 0, 100, complete_start=False
    )
    rejected = learner.snapshot("washer")["history"][0]
    assert rejected["reasons"] == ["program_changed"]
    assert rejected["warnings"] == ["counter_reset"]
    assert rejected["measurement_source"] == "integrated_power"
    assert rejected["energy_wh"] == 100
    assert learner.samples == {}
    learner.observe("washer", NOW, True, 600, 1000, complete_start=True)
    learner.observe(
        "washer", NOW + timedelta(minutes=5), False, 0, 0, complete_start=False
    )
    accepted = learner.snapshot("washer")["history"][-1]
    assert accepted["warnings"] == ["counter_reset"]
    assert accepted["reasons"] == []
    assert accepted["accepted"]
    assert learner.samples["washer"] == [50]


def test_unknown_detection_and_explicit_discard_preserve_invalid_observation():
    learner = ApplianceLearning()
    learner.observe("washer", NOW, True, 600, None, complete_start=True, valid=False)
    learner.observe(
        "washer",
        NOW + timedelta(minutes=2),
        True,
        600,
        None,
        complete_start=False,
        valid=False,
    )
    learner.discard("washer", NOW + timedelta(minutes=15))
    item = learner.snapshot("washer")["history"][0]
    assert item["reasons"] == ["detection_unknown"]
    assert item["energy_wh"] == 20  # No fabricated consumption across missing data.
    assert learner.active == {}
    assert learner.programs == {}
    learner.discard("washer", NOW)
    assert len(learner.snapshot("washer")["history"]) == 1


def test_metadata_roundtrip_keeps_profiles_and_discards_interrupted_measurement():
    original = ApplianceLearning()
    cycle(original)
    original.observe(
        "washer", NOW + timedelta(hours=1), True, 600, None, complete_start=True
    )
    original.observe("partial", NOW, True, None, None, complete_start=False)
    original.observe(
        "washer",
        NOW + timedelta(hours=1, minutes=5),
        True,
        600,
        None,
        complete_start=False,
    )
    payload = original.metadata_payload()
    restored = ApplianceLearning()
    restored.restore(original.samples)
    restored.restore_programs({"washer": {"eco": [[100, 5 / 60]]}})
    restored.restore_metadata(payload, NOW + timedelta(hours=2))
    assert restored.samples == original.samples
    assert restored.program_samples == original.program_samples
    assert restored.active == {}
    assert restored.programs == {}
    view = restored.snapshot("washer")
    assert view["profiles"] == original.snapshot("washer")["profiles"]
    assert len(view["history"]) == 2
    interrupted = view["history"][-1]
    assert interrupted["reasons"] == ["restart"]
    assert interrupted["ended_at"] == (NOW + timedelta(hours=1, minutes=5)).isoformat()
    assert interrupted["energy_wh"] == 50
    assert not interrupted["accepted"]
    assert restored.snapshot("partial")["history"][0]["reasons"] == [
        "missing_start",
        "restart",
    ]
    payload["history"]["washer"][0]["reasons"].append("external")
    assert original.snapshot("washer")["history"][0]["reasons"] == []
    assert restored.snapshot("washer")["history"][0]["reasons"] == []


def test_history_is_bounded_without_truncating_legacy_profile_samples():
    learner = ApplianceLearning()
    for i in range(25):
        cycle(learner, wh=i + 1, start=NOW + timedelta(hours=i))
    view = learner.snapshot("washer")
    assert len(view["history"]) == 20
    assert view["history"][0]["energy_wh"] == 6
    assert view["device_profile"]["count"] == 20
    assert view["profiles"][0]["count"] == 20
    payload = learner.metadata_payload()
    payload["history"]["washer"] *= 2
    restored = ApplianceLearning()
    restored.restore_metadata(payload)
    assert len(restored.snapshot("washer")["history"]) == 20


@pytest.mark.parametrize(
    "data", [None, [], {"version": 2}, {"version": True}, {"version": 1}]
)
def test_missing_metadata_does_not_damage_legacy_learning(data):
    learner = ApplianceLearning()
    learner.restore({"washer": [500]})
    learner.restore_programs({"washer": {"eco": [[500, 2]]}})
    learner.restore_metadata(data)
    assert learner.energy("washer", 999, "eco") == 500
    assert learner.duration("washer", 4, "eco") == 2
    assert learner.snapshot("washer")["device_profile"]["last_learned_at"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"started_at": "garbage"},
        {"ended_at": None},
        {"started_at": "2026-09-27T12:00:00"},
        {"duration_h": -1},
        {"energy_wh": float("nan")},
        {"energy_wh": 10**1000},
        {"energy_wh": True},
        {"program": ""},
        {"program": 1},
        {"measurement_source": "guess"},
        {"accepted": "yes"},
        {"complete": 1},
        {"reasons": "none"},
        {"warnings": [1]},
        {"warnings": ["large"] * 17},
        {"ended_at": (NOW - timedelta(minutes=1)).isoformat()},
        {"accepted": True, "complete": False},
        {"accepted": True, "energy_wh": None},
        {"accepted": True, "energy_wh": 0},
        {"accepted": True, "measurement_source": None},
        {"accepted": True, "reasons": ["aborted"]},
    ],
)
def test_invalid_observation_is_discarded_without_losing_valid_sibling(changes):
    learner = ApplianceLearning()
    cycle(learner)
    payload = learner.metadata_payload()
    valid = payload["history"]["washer"][0]
    payload["history"]["washer"] = [valid, dict(valid, **changes), None]
    restored = ApplianceLearning()
    restored.restore({"washer": [100]})
    restored.restore_metadata(payload)
    assert restored.snapshot("washer")["history"] == [valid]
    assert restored.energy("washer", 999) == 100


def test_malformed_metadata_sections_are_isolated_and_restart_cannot_learn():
    learner = ApplianceLearning()
    cycle(learner)
    payload = learner.metadata_payload()
    valid = deepcopy(payload["history"]["washer"][0])
    payload["history"].update({"": [], "broken": {}, 1: []})
    payload["last_learned_at"].update({"washer": "invalid", "unknown": NOW.isoformat()})
    payload["program_learned_at"].update({"washer": {"eco": None}, "unknown": []})
    payload["interrupted"] = {"": valid, "broken": {}, "washer": valid}
    restored = ApplianceLearning()
    restored.restore({"washer": [100]})
    restored.restore_programs({"washer": {"eco": [[100, 5 / 60]]}})
    restored.restore_metadata(payload)
    assert restored.snapshot("washer")["device_profile"]["last_learned_at"] is None
    assert restored.snapshot("washer")["profiles"][0]["last_learned_at"] is None
    assert restored.snapshot("washer")["history"][-1]["reasons"] == ["restart"]
    assert restored.samples == {"washer": [100]}


def test_out_of_range_program_duration_retains_existing_device_energy_contract():
    learner = ApplianceLearning()
    learner.observe("washer", NOW, True, 600, 1000, complete_start=True, program="eco")
    learner.observe("washer", NOW, False, 0, 1100, complete_start=False)
    result = learner.snapshot("washer")
    assert result["device_profile"]["energy_wh"] == 100
    assert result["profiles"] == []
    assert result["history"][0]["warnings"] == ["invalid_duration"]
    assert result["history"][0]["accepted"]
