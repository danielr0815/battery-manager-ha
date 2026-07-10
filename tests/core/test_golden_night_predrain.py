"""Golden snapshot for the F-PREDRAIN two-buffer pre-drain (docs/F-PREDRAIN.md,
WP2).

This scenario runs with the RECOMMENDED live parameters (import_trade_ratio
0.10, predrain_pv_confidence 0.5, upper_pv_reserve 1.2) — deliberately NON-
neutral, so it lives in its own golden file: the behaviour-neutral
`golden_topology.json` must stay frozen under the neutral dataclass defaults.

Regenerate ONLY for an intentional, reviewed behaviour change, then review the
diff (a good change touches only what you expect):

    python tests/core/test_golden_night_predrain.py
"""

import json
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path

# Match tests/core/conftest.py so the module also runs standalone (regeneration).
_COMPONENT = (
    Path(__file__).resolve().parents[2] / "custom_components" / "battery_manager"
)
if str(_COMPONENT) not in sys.path:
    sys.path.insert(0, str(_COMPONENT))

from core.model import (  # noqa: E402
    ControlParams,
    SurplusLoad,
    SurplusLoadState,
    SystemConfig,
)
from core.optimize import plan  # noqa: E402
from core.series import build_slots  # noqa: E402

GOLDEN_PATH = Path(__file__).parent / "golden_night_predrain.json"

DEHUMID = SurplusLoad(
    load_id="dehumidifier",
    name="Entfeuchter",
    nominal_power_w=400.0,
    battery_tolerance=0.15,
    min_runtime_min=30,
)
FOSSIBOT = SurplusLoad(
    load_id="fossibot_b",
    name="Fossibot F2400-B",
    nominal_power_w=300.0,
    energy_limited=True,
    capacity_wh=2000.0,
    target_soc_percent=90.0,
)


def _config():
    control = replace(
        ControlParams(),
        import_trade_ratio=0.1,
        predrain_pv_confidence=0.5,
        upper_pv_reserve=1.2,
    )
    return SystemConfig(control=control, loads=(FOSSIBOT, DEHUMID))


def _scenario():
    # Evening before ONE strong sunny day, high SOC, a Fossibot 872 Wh short of
    # its 90 % target. The dehumidifier pre-drains a pre-dawn block (F2 trade +
    # F3 alpha protection); the Fossibot stays in daylight (L5).
    return (
        _config(),
        datetime(2026, 7, 3, 21, 0),
        90.0,
        [0.0, 15.0],
        (
            SurplusLoadState(load_id="fossibot_b", soc_percent=46.4),
            SurplusLoadState(load_id="dehumidifier"),
        ),
    )


def _digest(result):
    return {
        "threshold": result.threshold_percent,
        "inverter_on": result.inverter_on,
        "import_kwh": round(result.grid_import_kwh, 6),
        "export_kwh": round(result.grid_export_kwh, 6),
        "lost_surplus_kwh": round(result.lost_surplus_kwh, 6),
        "import_trade_used_wh": round(result.import_trade_used_wh, 4),
        "stressed_min_soc": (
            None
            if result.stressed_min_soc_percent is None
            else round(result.stressed_min_soc_percent, 4)
        ),
        "pv_window_ends": result.pv_window_ends,
        "min_soc": round(result.min_soc_percent, 4),
        "max_soc": round(result.max_soc_percent, 4),
        "hours_to_max": result.hours_to_max_soc,
        "soc_curve": [round(f.soc_end_percent, 3) for f in result.trajectory.flows],
        "loads": {
            lp.load_id: [round(lp.planned_energy_wh, 3), list(lp.schedule)]
            for lp in result.load_plans
        },
    }


def _run():
    config, now, soc, fc, states = _scenario()
    inputs = build_slots(config, now, soc, fc, load_states=states)
    return json.loads(json.dumps(_digest(plan(config, inputs))))


def test_night_predrain_golden_unchanged():
    golden = json.loads(GOLDEN_PATH.read_text())
    assert _run() == golden, "F-PREDRAIN plan behaviour changed for s_night_predrain"


if __name__ == "__main__":
    GOLDEN_PATH.write_text(
        json.dumps(_run(), indent=1, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Wrote {GOLDEN_PATH}")
