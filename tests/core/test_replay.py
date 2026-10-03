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


def test_advisory_recording_roundtrip_checks_reasons_and_reads_older_records():
    """Missing old diagnostics remain compatible; present reasons are audited."""
    from core.model import Appliance, ApplianceAdvisory

    now = datetime(2026, 9, 27, 12)
    config = SystemConfig(appliances=(Appliance("washer", "Washer", 1000, 2, True),))
    inputs = PlanInputs(now, 80, (HourSlot(0, now, 1, 12, 2000, 0, 0),))
    result = plan(config, inputs)
    assert result.appliance_advisories == {
        "washer": ApplianceAdvisory(False, ("forecast_horizon_short",))
    }
    record = json.loads(json.dumps(recording(config, inputs, result), allow_nan=False))
    assert replay(record) == (result, True)

    record["result"]["fields"]["appliance_advisories"] = encode(
        {"washer": ApplianceAdvisory(False, ("extra_grid_import",))}
    )
    assert not replay(record)[1]

    record["result"]["fields"].pop("appliance_advisories")
    assert replay(record) == (result, True)
    # Legacy compatibility excludes only the absent additive field, never an
    # existing decision or energy measurement.
    record["result"]["fields"]["grid_import_kwh"] += 1
    assert not replay(record)[1]


def test_reserve_recording_checks_decision_and_preserves_legacy_energy_comparison():
    from core.model import ReserveParams, SupportParams

    now = datetime(2026, 9, 27, 12, tzinfo=UTC)
    config = SystemConfig(
        reserve=ReserveParams(enabled=True),
        support=SupportParams(configured=True, coordinated=True),
    )
    inputs = PlanInputs(now, 80, (HourSlot(0, now, 1, 12, 0, 100, 50),))
    result = plan(config, inputs)
    assert result.trajectory.reserve_decision is not None
    record = json.loads(json.dumps(recording(config, inputs, result), allow_nan=False))
    assert replay(record) == (result, True)

    trajectory = record["result"]["fields"]["trajectory"]["fields"]
    trajectory["reserve_decision"]["fields"]["reason"] = "pv_headroom_preparation"
    assert not replay(record)[1]
    trajectory.pop("reserve_decision")
    assert replay(record) == (result, True)
    trajectory["total_import_wh"] += 1
    assert not replay(record)[1]


def test_temporal_deficit_evidence_roundtrips_and_legacy_absence_stays_unknown():
    from core.model import BatteryParams, SupportParams

    now = datetime(2026, 10, 2, 12, tzinfo=UTC)
    config = SystemConfig(
        battery=BatteryParams(1000),
        support=SupportParams(
            configured=True,
            coordinated=True,
            dc24_available=False,
            dc48_available=False,
        ),
    )
    inputs = PlanInputs(now, 5, (HourSlot(0, now, 1, 12, 0, 0, 100),))
    result = plan(config, inputs)
    record = json.loads(json.dumps(recording(config, inputs, result)))
    assert replay(record) == (result, True)
    flow = record["result"]["fields"]["trajectory"]["fields"]["flows"]["tuple"][0][
        "fields"
    ]
    assert len(flow["dc_deficit_intervals"]["tuple"]) == 12
    flow["dc_deficit_intervals"]["tuple"][0]["fields"]["unserved_dc_wh"] += 1
    assert not replay(record)[1]
    flow.pop("dc_deficit_intervals")
    assert decode(record["result"]).trajectory.flows[0].dc_deficit_intervals is None
    assert replay(record) == (result, True)
    flow["unserved_dc_wh"] += 1
    assert not replay(record)[1]


def test_market_ranking_reason_is_checked_when_present_but_optional_in_old_records():
    from test_market import plant, priced

    config = plant()
    inputs = priced([(0, 600, 0), (0, 500, 0), (300, 0, 0)], [100, 300, 100])
    result = plan(config, inputs)
    record = recording(config, inputs, result)
    decision = record["result"]["fields"]["trajectory"]["fields"]["reserve_decision"][
        "fields"
    ]
    assert decision["market_ranking_reason"] == "weighted"
    decision["market_ranking_reason"] = "load_priority_incomplete"
    assert not replay(record)[1]
    decision.pop("market_ranking_reason")
    assert replay(record) == (result, True)
