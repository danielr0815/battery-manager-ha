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
    # AC/DC consumption source: L = learned series, S = static profile
    ("profile_sources", "Prof", "{}"),
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
    return _ascii_table(headers, rows)


def _ascii_table(headers: list[str], rows: list[list[str]]) -> str:
    widths = [
        max(len(headers[i]), *(len(row[i]) for row in rows))
        for i in range(len(headers))
    ]
    sep = "+" + "+".join("-" * (w + 2) for w in widths) + "+"
    lines = [sep]
    lines.append(
        "|"
        + "|".join(f" {headers[i]:>{widths[i]}} " for i in range(len(headers)))
        + "|"
    )
    lines.append(sep)
    for row in rows:
        lines.append(
            "|" + "|".join(f" {row[i]:>{widths[i]}} " for i in range(len(row))) + "|"
        )
    lines.append(sep)
    return "\n".join(lines)


_DAY_TYPE_HEADERS = (
    ("weekday", "Werktag"),
    ("weekend", "Wochenende"),
    ("absence", "Abwesend"),
)


def format_learned_profiles_table(snapshot: dict[str, Any]) -> str:
    """Render the learned consumption profiles as ASCII tables.

    `snapshot` is the learner's export: {profiles, samples, diagnostics,
    computed_at, window_days}. Per path one table with the learned W value
    and the sample count per (day type, hour); '-' = bin invalid (static
    fallback, docs/CONSUMPTION_FORECAST.md D-C6).
    """
    profiles = snapshot.get("profiles") or {}
    samples = snapshot.get("samples") or {}
    diagnostics = snapshot.get("diagnostics") or {}
    lines = [
        "Gelernte Verbrauchsprofile (docs/CONSUMPTION_FORECAST.md)",
        f"Stand: {snapshot.get('computed_at') or '-'}"
        f" | Lernfenster: {snapshot.get('window_days') or '-'} Tage"
        f" | Abdeckung: {diagnostics.get('coverage')}"
        f" | negative Residuen: {diagnostics.get('negative_residuals')}",
    ]
    if diagnostics.get("missing_statistics"):
        lines.append(
            "Ohne Langzeitstatistik: " + ", ".join(diagnostics["missing_statistics"])
        )

    headers = ["Std"]
    for _key, label in _DAY_TYPE_HEADERS:
        headers.extend([f"{label} P50", "P80", "n"])

    validation = snapshot.get("validation") or {}
    for path in ("ac", "dc"):
        bins = profiles.get(path)
        lines.append("")
        lines.append(f"[{path.upper()}-Pfad]")
        entries = validation.get(path) or []
        if entries:
            last = entries[-1]
            lines.append(
                f"Wächter zuletzt ({last.get('day')}): "
                f"Bias {last.get('bias_w')} W, MAE {last.get('mae_w')} W"
            )
        if not bins:
            lines.append("(kein gelerntes Profil — statisches Profil aktiv)")
            continue
        path_samples = samples.get(path) or {}
        rows = []
        for hour in range(24):
            row = [str(hour)]
            for key, _label in _DAY_TYPE_HEADERS:
                by_quantile = bins.get(key) or {}
                counts = path_samples.get(key) or []
                for q_key in ("p50", "p80"):
                    values = (
                        by_quantile.get(q_key)
                        if isinstance(by_quantile, dict)
                        else None
                    ) or []
                    value = values[hour] if hour < len(values) else None
                    row.append(f"{value:.0f}" if value is not None else "-")
                count = counts[hour] if hour < len(counts) else 0
                row.append(str(count))
            rows.append(row)
        lines.append(_ascii_table(headers, rows))
    return "\n".join(lines)
