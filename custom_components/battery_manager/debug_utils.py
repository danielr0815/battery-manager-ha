"""Utility functions for debugging output."""

from __future__ import annotations

from typing import Any

_COLUMNS = (
    ("hour", "Std", "{:d}"),
    ("datetime", "Zeit", "{}"),
    ("duration_minutes", "Min", "{:d}"),
    ("initial_soc_percent", "SOC in %", "{:.1f}"),
    ("final_soc_percent", "SOC out %", "{:.1f}"),
    ("pv_production_wh", "PV Wh", "{:.0f}"),
    ("ac_consumption_wh", "AC Wh", "{:.0f}"),
    ("dc_consumption_wh", "DC Wh", "{:.0f}"),
    ("surplus_load_wh", "Zusatz Wh", "{:.0f}"),
    ("grid_import_wh", "Import Wh", "{:.0f}"),
    ("grid_export_wh", "Export Wh", "{:.0f}"),
    ("battery_charge_wh", "Laden Wh", "{:.0f}"),
    ("battery_discharge_wh", "Entladen Wh", "{:.0f}"),
    ("inverter_enabled", "WR", "{}"),
    ("support_dc24", "24V", "{}"),
    ("support_dc48", "48V", "{}"),
)


def format_hourly_details_table(hourly_details: list[dict[str, Any]]) -> str:
    """Return hourly plan details formatted as an ASCII table."""
    if not hourly_details:
        return "\nNo hourly details available"

    rows: list[list[str]] = []
    for detail in hourly_details:
        row = []
        for key, _header, fmt in _COLUMNS:
            value = detail.get(key, "")
            if key == "datetime" and isinstance(value, str):
                value = value[5:16].replace("T", " ")  # MM-DD HH:MM
            elif isinstance(value, bool):
                value = "on" if value else "-"
            try:
                row.append(fmt.format(value))
            except (ValueError, TypeError):
                row.append(str(value))
        rows.append(row)

    headers = [header for _key, header, _fmt in _COLUMNS]
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows))
        for i in range(len(headers))
    ]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    lines = [sep]
    lines.append(
        "|" + "|".join(f" {headers[i]:>{widths[i]}} " for i in range(len(headers))) + "|"
    )
    lines.append(sep)
    for row in rows:
        lines.append(
            "|" + "|".join(f" {row[i]:>{widths[i]}} " for i in range(len(row))) + "|"
        )
    lines.append(sep)
    return "\n".join(lines)
