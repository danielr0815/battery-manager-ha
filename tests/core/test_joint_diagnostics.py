"""Candidate observations preserve stages, exact spans, and bounded output."""

from dataclasses import replace
from datetime import timedelta

from core.joint_diagnostics import JointDiagnostics
from core.reserve_sources import SourceStep
from core.simulate import simulate
from test_joint_dispatch import forecast, plant


def case(count=4):
    config = plant()
    inputs = forecast([(0, 100, 100)] * count, [100] * count)
    flows = simulate(config, inputs, 20).flows
    return tuple(
        SourceStep(s, f, (), 5) for s, f in zip(inputs.slots, flows, strict=True)
    )


def test_exact_partial_receiver_outcomes_separate_local_and_full_checks():
    steps = case()
    recorder = JointDiagnostics(steps, (100, 100, 100, None))
    recorder.record((1,), "dc_only", "local", "extra_import")
    recorder.record((1,), "dc_only", "proof", "reserve_floor")
    recorder.record((1,), "dc_only", "proved")
    rows = recorder.summary()
    row = rows[0]
    assert row.start == steps[1].slot.start
    assert row.end == steps[2].slot.start
    assert row.attempted == 3
    assert row.local_rejections == (("extra_import", 1),)
    assert row.proof_rejections == (("reserve_floor", 1),)
    assert row.proved_improvements == 1
    assert not row.not_searched
    assert any(r.variant == "ac_dc" and r.not_searched for r in rows)
    assert all(r.start != steps[3].slot.start for r in rows)


def test_bounded_diagnostics_preserve_proved_partial_spans_over_seed_rows():
    steps = case(70)
    recorder = JointDiagnostics(steps, tuple(range(70)))
    assert recorder.truncated
    for i in range(48):
        recorder.record((i,), "dc_only", "local", "energy_budget")
    recorder.record((69,), "paired_dc", "proved")
    recorder.record((0,), "ac_dc", "local", "extra_import")
    rows = recorder.summary()
    assert len(rows) == 48
    assert rows[0].start == steps[69].slot.start
    assert rows[0].proved_improvements == 1
    assert not any(
        r.variant == "ac_dc" and r.start == steps[0].slot.start for r in rows
    )


def test_paired_receiver_spans_exclude_donors_unknown_prices_and_time_gaps():
    steps = list(case(5))
    steps[2] = replace(
        steps[2],
        slot=replace(steps[2].slot, start=steps[2].slot.start + timedelta(minutes=5)),
    )
    recorder = JointDiagnostics(steps, (100, 100, 100, None, 100))
    proposal = tuple(
        replace(s.flow, grid_import_wh=s.flow.grid_import_wh - 1) for s in steps
    )
    recorder.paired(proposal, "proved")
    proved = [r for r in recorder.summary() if r.proved_improvements]
    assert len(proved) == 3
    assert {(r.start, r.end) for r in proved} == {
        (steps[0].slot.start, steps[1].slot.start + timedelta(hours=1)),
        (steps[2].slot.start, steps[2].slot.start + timedelta(hours=1)),
        (steps[4].slot.start, steps[4].slot.start + timedelta(hours=1)),
    }
    recorder.paired(tuple(s.flow for s in steps), "proof", "extra_import")
    assert all(not r.proof_rejections for r in recorder.summary())


def test_dispatch_records_validated_ac_alternative_and_separate_unsearched_pair():
    config = plant()
    inputs = forecast([(0, 0, 100), (0, 100, 0), (100, 0, 0)], [0, 300, 0])
    summary = simulate(config, inputs, 20).reserve_decision.joint
    proved = [r for r in summary.target_diagnostics if r.proved_improvements]
    assert proved
    assert any(r.variant == "ac_dc" and r.eur_per_mwh == 300 for r in proved)
    assert all(
        r.attempted
        == sum(c for _, c in r.local_rejections)
        + sum(c for _, c in r.proof_rejections)
        + r.proved_improvements
        for r in summary.target_diagnostics
    )


def test_converter_loss_is_a_local_candidate_rejection_without_full_proof_claim():
    config = plant()
    config = replace(
        config,
        battery=replace(config.battery, eta_discharge=0.97),
        inverter=replace(config.inverter, eta=0.95, standby_power_w=15),
        support=replace(config.support, psu24_eta=0.89, dcdc_eta=0.93),
    )
    inputs = forecast([(0, 0, 100), (0, 100, 0), (100, 0, 0)], [0, 10000, 0])
    summary = simulate(config, inputs, 20).reserve_decision.joint
    rejected = [
        r
        for r in summary.target_diagnostics
        if dict(r.local_rejections).get("extra_import")
    ]
    assert rejected
    assert all(r.variant == "ac_dc" for r in rejected)
    assert all("extra_import" not in dict(r.proof_rejections) for r in rejected)
    assert all(
        not r.local_rejections for r in summary.target_diagnostics if r.not_searched
    )


def test_diagnostic_interval_end_uses_elapsed_time_across_autumn_fold():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    steps = list(case(1))
    start = datetime(2026, 10, 25, 2, 55, tzinfo=ZoneInfo("Europe/Berlin"), fold=0)
    steps[0] = replace(
        steps[0], slot=replace(steps[0].slot, start=start, duration=1 / 12)
    )
    recorder = JointDiagnostics(steps, (100,))
    recorder.record((0,), "dc_only", "proved")
    row = recorder.summary()[0]
    assert row.start.isoformat() == "2026-10-25T02:55:00+02:00"
    assert row.end.isoformat() == "2026-10-25T02:00:00+01:00"
    assert row.end.timestamp() - row.start.timestamp() == 300
