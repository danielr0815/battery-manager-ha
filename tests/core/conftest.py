"""Core tests import the `core` package directly (no Home Assistant needed).

This keeps the core suite runnable on platforms without HA support (Windows):
    python -m pytest tests/core -p no:homeassistant
"""

import sys
from pathlib import Path

COMPONENT_DIR = (
    Path(__file__).resolve().parents[2] / "custom_components" / "battery_manager"
)
if str(COMPONENT_DIR) not in sys.path:
    sys.path.insert(0, str(COMPONENT_DIR))
