"""Direct tests for debug_utils.format_hourly_details_table.

The export service tests pin the file-writing path; here the rendering
itself is pinned: the empty case, the datetime/bool cell formatting, and
the str() fallback for values the column format cannot handle.
"""

from custom_components.battery_manager.debug_utils import format_hourly_details_table


def test_empty_details_render_placeholder():
    assert format_hourly_details_table([]) == "\nNo hourly details available"


def test_full_row_renders_all_columns():
    table = format_hourly_details_table(
        [
            {
                "hour": 10,
                "datetime": "2026-07-30T10:00:00+00:00",
                "duration_minutes": 60,
                "initial_soc_percent": 55.0,
                "final_soc_percent": 58.25,
                "pv_production_wh": 1234.4,
                "ac_consumption_wh": 100.0,
                "dc_consumption_wh": 50.0,
                "surplus_load_wh": 0.0,
                "grid_import_wh": 0.0,
                "grid_export_wh": 600.0,
                "battery_charge_wh": 500.0,
                "battery_discharge_wh": 0.0,
                "inverter_enabled": True,
                "support_dc24": False,
                "support_dc48": False,
                "profile_sources": "LS",
            }
        ]
    )
    # Header carries every column label.
    for header in (
        "Std",
        "Zeit",
        "SOC in %",
        "SOC out %",
        "PV Wh",
        "WR",
        "24V",
        "Prof",
    ):
        assert header in table
    # The datetime is cut to MM-DD HH:MM, bools render on/-, floats rounded.
    assert "07-30 10:00" in table
    assert "58.2" in table  # final SOC, one decimal
    assert "1234" in table  # PV Wh, no decimals
    assert "on" in table  # inverter_enabled True
    # Frame closes around a single data row (3 separator lines).
    assert sum(1 for line in table.splitlines() if line.startswith("+")) == 3


def test_missing_and_unformattable_cells_fall_back_gracefully():
    """Missing keys render empty; a value the column format rejects (e.g. a
    string under a numeric format) falls back to str() instead of raising."""
    table = format_hourly_details_table(
        [
            {"hour": "n/a", "datetime": 12345},  # non-str datetime: rendered as-is
            {},  # all cells missing
        ]
    )
    assert "n/a" in table
    assert "12345" in table
