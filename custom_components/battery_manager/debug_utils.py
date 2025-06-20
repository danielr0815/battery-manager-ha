# Utility functions for debugging output.
from typing import Dict, List


def format_hourly_details_table(
    hourly_details: List[Dict[str, any]], include_color: bool = False
) -> str:
    """Return hourly details formatted as an ASCII table.

    Args:
        hourly_details: List of hourly details dictionaries.
        include_color: Include ANSI color codes.

    Returns:
        String containing the formatted table.
    """
    if not hourly_details:
        return "\nNo hourly details available"

    # ANSI color codes
    if include_color:
        RESET = "\033[0m"
        BOLD = "\033[1m"
        GREEN = "\033[92m"
        RED = "\033[91m"
        BLUE = "\033[94m"
        YELLOW = "\033[93m"
        CYAN = "\033[96m"
        MAGENTA = "\033[95m"
    else:
        RESET = BOLD = GREEN = RED = BLUE = YELLOW = CYAN = MAGENTA = ""

    lines = []
    lines.append("=" * 170)
    lines.append("DETAILED HOURLY CALCULATION TABLE (Internal Algorithm Data)")
    lines.append("=" * 170)

    has_extra = "extra_load" in hourly_details[0]
    header = (
        f"{BOLD}{CYAN}{'Hour':>4}{RESET} | "
        f"{BOLD}{CYAN}{'Time':>5}{RESET} | "
        f"{BOLD}{BLUE}{'SOC%':>4}{RESET} | "
        f"{BOLD}{YELLOW}{'PV_Wh':>6}{RESET} | "
        f"{BOLD}{MAGENTA}{'AC_Wh':>6}{RESET} | "
        f"{BOLD}{MAGENTA}{'DC_Wh':>6}{RESET} | "
        f"{BOLD}{'Import':>6}{RESET} | "
        f"{BOLD}{'Export':>6}{RESET} | "
        f"{BOLD}{'Batt_Wh':>8}{RESET} | "
        f"{BOLD}{'Forced':>7}{RESET} | "
        f"{BOLD}{'Volunt':>7}{RESET} | "
        f"{BOLD}{'Inv_Wh':>7}{RESET} | "
        f"{BOLD}{'Final%':>6}{RESET}"
        + (f" | {BOLD}{'Extra':>5}{RESET}" if has_extra else "")
    )
    lines.append(header)
    lines.append("-" * 170)

    for detail in hourly_details:
        hour = detail["hour"]
        time_str = detail["datetime"][11:16]
        duration_fraction = detail.get("duration_fraction", 1.0)
        soc_initial = detail["initial_soc_percent"]
        soc_final = detail["final_soc_percent"]
        pv_wh = detail["pv_production_wh"]
        ac_wh = detail["ac_consumption_wh"]
        dc_wh = detail["dc_consumption_wh"]
        grid_import = detail.get("grid_import_wh", 0.0)
        grid_export = detail.get("grid_export_wh", 0.0)
        net_battery = detail["net_battery_wh"]
        charger_forced = detail.get("charger_forced_wh", 0.0)
        charger_voluntary = detail.get("charger_voluntary_wh", 0.0)
        inverter_ac = detail["inverter_dc_to_ac_wh"]

        # Color coding for grid flows
        if grid_import > 0:
            import_color = RED
            import_str = f"{grid_import:6.0f}"
        else:
            import_color = RESET
            import_str = f"{grid_import:6.0f}"

        if grid_export > 0:
            export_color = GREEN
            export_str = f"{grid_export:6.0f}"
        else:
            export_color = RESET
            export_str = f"{grid_export:6.0f}"

        if net_battery > 0:
            batt_color = GREEN
            batt_str = f"+{net_battery:7.0f}"
        elif net_battery < 0:
            batt_color = RED
            batt_str = f"{net_battery:8.0f}"
        else:
            batt_color = RESET
            batt_str = f"{net_battery:8.0f}"

        soc_change = soc_final - soc_initial
        if soc_change > 0:
            soc_color = GREEN
        elif soc_change < 0:
            soc_color = RED
        else:
            soc_color = BLUE

        row = (
            f"{CYAN}{hour:4d}{RESET} | "
            f"{CYAN}{time_str:>5}{RESET} | "
            f"{BLUE}{soc_initial:4.1f}{RESET} | "
            f"{YELLOW}{pv_wh:6.0f}{RESET} | "
            f"{MAGENTA}{ac_wh:6.0f}{RESET} | "
            f"{MAGENTA}{dc_wh:6.0f}{RESET} | "
            f"{import_color}{import_str}{RESET} | "
            f"{export_color}{export_str}{RESET} | "
            f"{batt_color}{batt_str}{RESET} | "
            f"{RED if charger_forced > 0 else RESET}{charger_forced:7.0f}{RESET} | "
            f"{GREEN if charger_voluntary > 0 else RESET}{charger_voluntary:7.0f}{RESET} | "
            f"{RESET}{inverter_ac:7.0f}{RESET} | "
            f"{soc_color}{soc_final:6.1f}{RESET}"
            + (
                f" | {GREEN if detail.get('extra_load') else RED}{'ON' if detail.get('extra_load') else 'OFF':>5}{RESET}"
                if has_extra
                else ""
            )
        )
        lines.append(row)

    lines.append("-" * 170)
    lines.append(f"\n{BOLD}Legend:{RESET}")
    lines.append(f"  {GREEN}Green{RESET}: Positive energy flows (charging, export)")
    lines.append(f"  {RED}Red{RESET}: Negative energy flows (discharging, import)")
    lines.append("  Yellow: PV production")
    lines.append(f"  {MAGENTA}Magenta{RESET}: Consumption")
    lines.append(f"  {BLUE}Blue{RESET}: SOC values")
    lines.append(f"  Import: {RED}Grid import{RESET} (+from grid)")
    lines.append(f"  Export: {GREEN}Grid export{RESET} (+to grid)")
    lines.append("  Batt_Wh: Net battery flow (+charge, -discharge)")
    lines.append(f"  Forced: {RED}Forced{RESET} charger energy (DC deficit)")
    lines.append(f"  Volunt: {GREEN}Voluntary{RESET} charger energy (PV surplus)")
    lines.append("=" * 170)

    return "\n".join(lines)
