"""Golden-plan snapshots that lock planner behaviour across the F-N3
two-bus refactor (docs/DC_TOPOLOGY.md phase 1).

The two-bus model is introduced behaviour-neutral: with the neutral
defaults (efficiencies 1.0, uncapped currents, gate open, dc24 share
100 %) every plan must stay byte-identical to the pre-F-N3 behaviour.
`golden_topology.json` is the frozen baseline; regenerate it ONLY for an
intentional, reviewed behaviour change (scratchpad/gen_golden.py).
"""

import json
from datetime import datetime
from pathlib import Path

from core.model import (
    LoadProfile,
    PVParams,
    SupportParams,
    SurplusLoad,
    SurplusLoadState,
    SystemConfig,
)
from core.optimize import plan
from core.series import build_slots

GOLDEN = json.loads((Path(__file__).parent / "golden_topology.json").read_text())

FOSSIBOT = SurplusLoad(
    load_id="f1",
    name="F1",
    nominal_power_w=300.0,
    energy_limited=True,
    capacity_wh=2000.0,
)
DEHUMID = SurplusLoad(load_id="d1", name="D1", nominal_power_w=400.0)
ES = (
    SurplusLoadState(load_id="f1", soc_percent=0.0),
    SurplusLoadState(load_id="d1"),
)


def _digest(result):
    return {
        "threshold": result.threshold_percent,
        "inverter_on": result.inverter_on,
        "import_kwh": round(result.grid_import_kwh, 6),
        "export_kwh": round(result.grid_export_kwh, 6),
        "min_soc": round(result.min_soc_percent, 4),
        "max_soc": round(result.max_soc_percent, 4),
        "hours_to_max": result.hours_to_max_soc,
        "dc24_now": result.support_dc24_now,
        "dc48_now": result.support_dc48_now,
        "soc_curve": [round(f.soc_end_percent, 3) for f in result.trajectory.flows],
        "loads": {
            lp.load_id: [round(lp.planned_energy_wh, 3), list(lp.schedule)]
            for lp in result.load_plans
        },
    }


def _run(config, now, soc, fc, states=()):
    inputs = build_slots(config, now, soc, fc, load_states=tuple(states))
    return json.loads(json.dumps(_digest(plan(config, inputs))))


def _scenarios():
    base = SystemConfig()
    loads_cfg = SystemConfig(loads=(FOSSIBOT, DEHUMID))
    sup = SystemConfig(support=SupportParams(configured=True))
    sup_dc = SystemConfig(
        support=SupportParams(configured=True),
        dc_profile=LoadProfile(base_w=150.0, variable_w=0.0),
    )
    short_peak = SystemConfig(
        pv=PVParams(
            peak_power_w=3200.0,
            morning_start_hour=11,
            morning_end_hour=13,
            afternoon_end_hour=14,
            morning_ratio=0.7,
        ),
        loads=(FOSSIBOT,),
    )
    return {
        "s1_evening_sunny": (
            base,
            datetime(2026, 7, 3, 20, 0),
            80.0,
            [0.0, 14.0, 12.0],
            (),
        ),
        "s2_cloudy_reserve": (
            base,
            datetime(2026, 7, 3, 20, 0),
            60.0,
            [0.0, 1.5, 2.0],
            (),
        ),
        "s3_loads_night": (
            loads_cfg,
            datetime(2026, 7, 3, 21, 0),
            84.0,
            [0.0, 13.0, 11.0],
            ES,
        ),
        "s3_low_soc_5am": (
            loads_cfg,
            datetime(2026, 7, 5, 5, 0),
            50.0,
            [14.0, 12.0, 13.0],
            ES,
        ),
        "s4_midday_full": (
            loads_cfg,
            datetime(2026, 7, 4, 11, 0),
            93.0,
            [10.0, 12.0, 11.0],
            ES,
        ),
        "support_dc24_escalate": (
            sup_dc,
            datetime(2026, 7, 3, 22, 0),
            8.0,
            [0.0, 0.0, 0.0],
            (),
        ),
        "support_dc48_escalate": (
            sup,
            datetime(2026, 7, 3, 22, 0),
            7.0,
            [0.0, 0.0, 0.0],
            (),
        ),
        "support_none_healthy": (
            sup,
            datetime(2026, 7, 3, 20, 0),
            80.0,
            [0.0, 14.0, 12.0],
            (),
        ),
        "forced_dc24": (
            SystemConfig(support=SupportParams(configured=True, dc24_forced_on=True)),
            datetime(2026, 7, 3, 22, 0),
            50.0,
            [0.0, 2.0, 2.0],
            (),
        ),
        "forced_dc48": (
            SystemConfig(support=SupportParams(configured=True, dc48_forced_on=True)),
            datetime(2026, 7, 3, 22, 0),
            50.0,
            [0.0, 2.0, 2.0],
            (),
        ),
        "short_peak_preempt": (
            short_peak,
            datetime(2026, 7, 4, 20, 0),
            90.0,
            [0.0, 8.0],
            (SurplusLoadState(load_id="f1", soc_percent=0.0),),
        ),
    }


def test_golden_scenarios_unchanged():
    scenarios = _scenarios()
    assert set(scenarios) == set(GOLDEN), "scenario set drifted from the golden file"
    for name, args in scenarios.items():
        assert _run(*args) == GOLDEN[name], f"behaviour changed for scenario {name!r}"
