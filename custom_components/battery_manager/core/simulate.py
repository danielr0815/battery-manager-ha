"""Pure energy-flow simulation (docs/ALGORITHM.md §1, topology REQUIREMENTS.md §1.1).

Topology: PV feeds the AC side. The battery charges exclusively through the
AC->DC charger and discharges through the DC->AC inverter. DC loads hang off
the battery. Emergency support paths (D-A9) can shift DC loads to the grid.
"""

from __future__ import annotations

from .model import HourFlows, HourSlot, PlanInputs, SystemConfig, Trajectory

_EPS = 1e-9


def step_hour(
    config: SystemConfig,
    soc_percent: float,
    slot: HourSlot,
    threshold_percent: float,
    extra_ac_wh: float = 0.0,
    dc24_from_grid: bool = False,
    dc48_support: bool = False,
) -> HourFlows:
    """Simulate one slot; returns all flows. Never mutates anything."""
    battery = config.battery
    energy = battery.energy_wh(soc_percent)
    floor_wh = battery.energy_wh(battery.soc_min_percent)
    ceil_wh = battery.energy_wh(battery.soc_max_percent)

    grid_import = 0.0
    grid_export = 0.0
    battery_charge = 0.0
    battery_discharge = 0.0
    inverter_output = 0.0

    inverter_on = soc_percent > threshold_percent

    # --- 48 V support PSU: fixed DC power onto the battery bus, from grid ---
    if dc48_support and config.support.configured:
        psu_wh = config.support.dc48_power_w * slot.duration
        absorbed = min(psu_wh, max(0.0, ceil_wh - energy))
        energy += absorbed
        battery_charge += absorbed
        grid_import += psu_wh  # PSU conversion losses neglected (documented)

    # --- DC loads: 24 V rail ---
    if dc24_from_grid and config.support.configured:
        # 24 V PSU replaces the DC/DC converter: DC load served from grid.
        grid_import += slot.dc_wh
    elif slot.dc_wh > 0:
        needed_from_store = slot.dc_wh / battery.eta_discharge
        available = max(0.0, energy - floor_wh)
        used = min(needed_from_store, available)
        energy -= used
        battery_discharge += used
        shortfall_dc = (needed_from_store - used) * battery.eta_discharge
        if shortfall_dc > _EPS:
            # Forced charger operation: grid keeps the DC rail alive.
            grid_import += shortfall_dc / config.charger.eta

    # --- AC balance ---
    ac_total = slot.ac_wh + extra_ac_wh
    if inverter_on:
        ac_total += config.inverter.standby_power_w * slot.duration

    balance = slot.pv_wh - ac_total

    if balance >= 0:
        # Surplus: charge battery through the charger, export the rest.
        headroom = max(0.0, ceil_wh - energy)
        max_charger_ac = config.charger.max_power_w * slot.duration
        needed_ac = headroom / (battery.eta_charge * config.charger.eta)
        charger_ac = min(balance, max_charger_ac, needed_ac)
        if charger_ac > _EPS:
            stored = charger_ac * config.charger.eta * battery.eta_charge
            energy += stored
            battery_charge += stored
            standby = config.charger.standby_power_w * slot.duration
            balance -= charger_ac + standby
        grid_export += max(0.0, balance)
        if balance < 0:  # charger standby pushed balance negative
            grid_import += -balance
    else:
        deficit = -balance
        if inverter_on:
            inv_floor_wh = battery.energy_wh(
                max(battery.soc_min_percent, config.control.inverter_min_soc_percent)
            )
            available_store = max(0.0, energy - inv_floor_wh)
            available_ac = available_store * battery.eta_discharge * config.inverter.eta
            max_inv_ac = config.inverter.max_power_w * slot.duration
            ac_out = min(deficit, max_inv_ac, available_ac)
            if ac_out > _EPS:
                drawn = ac_out / (battery.eta_discharge * config.inverter.eta)
                energy -= drawn
                battery_discharge += drawn
                inverter_output += ac_out
                deficit -= ac_out
        grid_import += deficit

    end_soc = config.battery.soc_percent(energy)
    return HourFlows(
        soc_start_percent=soc_percent,
        soc_end_percent=end_soc,
        grid_import_wh=grid_import,
        grid_export_wh=grid_export,
        battery_charge_wh=battery_charge,
        battery_discharge_wh=battery_discharge,
        inverter_on=inverter_on,
        inverter_output_wh=inverter_output,
        extra_ac_wh=extra_ac_wh,
        support_dc24=dc24_from_grid and config.support.configured,
        support_dc48=dc48_support and config.support.configured,
    )


def simulate(
    config: SystemConfig,
    inputs: PlanInputs,
    threshold_percent: float,
    extra_ac_wh: tuple[float, ...] | None = None,
    dc24_schedule: tuple[bool, ...] | None = None,
    dc48_schedule: tuple[bool, ...] | None = None,
) -> Trajectory:
    """Simulate the whole horizon under the policy `inverter on <=> SOC > threshold`."""
    soc = inputs.start_soc_percent
    flows: list[HourFlows] = []
    total_import = 0.0
    total_export = 0.0

    for i, slot in enumerate(inputs.slots):
        flow = step_hour(
            config,
            soc,
            slot,
            threshold_percent,
            extra_ac_wh=extra_ac_wh[i] if extra_ac_wh else 0.0,
            dc24_from_grid=bool(dc24_schedule[i]) if dc24_schedule else False,
            dc48_support=bool(dc48_schedule[i]) if dc48_schedule else False,
        )
        flows.append(flow)
        soc = flow.soc_end_percent
        total_import += flow.grid_import_wh
        total_export += flow.grid_export_wh

    return Trajectory(
        flows=tuple(flows),
        total_import_wh=total_import,
        total_export_wh=total_export,
        end_soc_percent=soc,
    )
