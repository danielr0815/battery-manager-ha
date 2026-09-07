"""Round trips cover actual solver decisions, not just dataclass construction."""

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta

import pytest
from core.model import FeedInParams, HourSlot, PlanInputs, SurplusLoad, SystemConfig
from core.optimize import plan
from core.replay import decode, encode, recording, replay


def test_recording_reproduces_complete_plan_and_detects_changed_inputs():
    now = datetime(2026, 9, 7, 9, tzinfo=UTC)
    config = SystemConfig(
        loads=(SurplusLoad("deh", "Dehumidifier", 400, 0, 15, 15, False),),
        feedin=FeedInParams(enabled=True, automatic_enabled=False),
    )
    inputs = PlanInputs(
        now,
        70,
        tuple(
            HourSlot(i, now + timedelta(hours=i), 1, 9 + i, pv, 100, 0)
            for i, pv in enumerate((800, 1600, 1600, 800, 100, 0))
        ),
    )
    result = plan(config, inputs)
    record = json.loads(json.dumps(recording(config, inputs, result), allow_nan=False))
    reproduced, matches = replay(record)
    assert matches
    assert reproduced == result
    record["inputs"] = encode(replace(inputs, start_soc_percent=20))
    assert not replay(record)[1]


def test_schema_preserves_date_keys_and_rejects_executable_or_invalid_types():
    value = {date(2026, 9, 7): (None, True, "text", 3, 2.5)}
    assert decode(json.loads(json.dumps(encode(value)))) == value
    with pytest.raises(ValueError, match="Unsupported recording value"):
        encode(object())
    for invalid in ([1], {"type": "os.system", "fields": {}}, {"unknown": 1}):
        with pytest.raises(ValueError):
            decode(invalid)
    with pytest.raises(ValueError, match="schema"):
        replay({"schema_version": 99})
    with pytest.raises(ValueError, match="SystemConfig"):
        replay({"schema_version": 1, "config": encode(value), "inputs": None})
