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
    # Defensive: a hand-edited min > max SOC would give a negative headroom
    # band and mis-plan; keep ceil >= floor so the SOC math stays monotonic
    # (the config flow also validates this — cross-field validation sweep).
    ceil_wh = max(floor_wh, ceil_wh)

    grid_import = 0.0
    grid_export = 0.0
    battery_charge = 0.0
    battery_discharge = 0.0
    inverter_output = 0.0

    inverter_on = soc_percent > threshold_percent
    support = config.support

    # --- AC balance computed early ---
    # Needed before the 48 V gate: during a NET-CHARGING slot the charger/PV
    # lifts the 48 V bus above the PSU's output voltage, so the real Meanwell
    # self-gates OFF and delivers nothing (docs/DC_TOPOLOGY.md §4, Jury-Gap #1).
    # It is also the single balance against which the residual DC bus load is
    # settled, so a same-slot PV surplus covers it instead of a phantom import.
    ac_total = slot.ac_wh + extra_ac_wh
    if inverter_on:
        ac_total += config.inverter.standby_power_w * slot.duration
    balance = slot.pv_wh - ac_total
    net_charging = balance > _EPS

    # --- DC load split across the two buses (F-N3, docs/DC_TOPOLOGY.md) ---
    # `dc24_share` of the DC load sits on the 24 V rail; the rest is native
    # 48 V bus load. Neutral default share=1.0 => whole load on the rail.
    rail_wh = slot.dc_wh * support.dc24_share
    native48_wh = slot.dc_wh - rail_wh
    psu24_delivered_wh = 0.0
    dcdc_input_wh = 0.0
    dcdc_loss_wh = 0.0
    unserved_dc_wh = 0.0

    if dc24_from_grid and support.configured:
        # 24 V PSU feeds the rail from the grid; the DC/DC is off.
        cap_wh = (
            support.psu24_max_power_w * slot.duration
            if support.psu24_max_power_w is not None
            else rail_wh
        )
        served = min(rail_wh, cap_wh)
        grid_import += served / support.psu24_eta
        psu24_delivered_wh = served
        unserved_dc_wh = rail_wh - served
        bus_draw24_wh = 0.0
    else:
        # DC/DC converter draws the rail energy from the 48 V bus.
        cap_wh = (
            support.dcdc_max_power_w * slot.duration
            if support.dcdc_max_power_w is not None
            else rail_wh
        )
        served = min(rail_wh, cap_wh)
        bus_draw24_wh = served / support.dcdc_eta
        dcdc_input_wh = bus_draw24_wh
        dcdc_loss_wh = bus_draw24_wh - served
        unserved_dc_wh = rail_wh - served

    # Total consumption on the 48 V bus this slot: native 48 V load + the
    # energy the DC/DC draws to feed the 24 V rail.
    bus_load = native48_wh + bus_draw24_wh

    # --- 48 V support PSU (F-N3 direct-offset model, docs/DC_TOPOLOGY.md §4) ---
    # The PSU is a 48 V source: it first covers concurrent bus load DIRECTLY
    # (no battery round-trip), then the remainder charges the battery through
    # the charge efficiency. Grid billing follows the energy actually
    # DELIVERED (a closed gate or a full battery bills ~0), divided by the
    # PSU efficiency. Voltage gate (R1): an SOC proxy for the PSU's
    # output-voltage threshold; gate_soc None = always open.
    gate_soc = support.gate_soc_percent
    gate_open = (
        dc48_support
        and support.configured
        and (gate_soc is None or soc_percent < gate_soc)
        and not net_charging
    )
    psu48_delivered_wh = 0.0
    if gate_open:
        potential = support.dc48_power_w * slot.duration
        if support.psu48_max_power_w is not None:
            potential = min(potential, support.psu48_max_power_w * slot.duration)
        # (a) offset concurrent bus load 1:1 on the 48 V bus (no battery).
        direct = min(potential, bus_load)
        bus_load -= direct
        # (b) the remainder charges the battery (bus -> stored via eta_charge).
        remainder = potential - direct
        headroom = max(0.0, ceil_wh - energy)
        # Edge taper: never charge the battery past the gate threshold within
        # a single slot, so one slot cannot overshoot gate_soc (the real PSU
        # would self-gate as the bus voltage crosses its output).
        if gate_soc is not None:
            headroom = max(0.0, min(headroom, battery.energy_wh(gate_soc) - energy))
        absorbed = min(remainder * battery.eta_charge, headroom)
        energy += absorbed
        battery_charge += absorbed
        psu48_delivered_wh = direct + absorbed / battery.eta_charge
        grid_import += psu48_delivered_wh / support.psu48_eta

    # --- Remaining 48 V bus load drains the battery. Any shortfall (store at
    # floor) is NOT imported here but carried to the AC settlement, so a
    # same-slot PV surplus covers it via the charger instead of importing grid
    # while PV is simultaneously stored/exported (energy conservation). ---
    shortfall_dc = 0.0
    if bus_load > _EPS:
        needed_from_store = bus_load / battery.eta_discharge
        available = max(0.0, energy - floor_wh)
        used = min(needed_from_store, available)
        energy -= used
        battery_discharge += used
        shortfall_dc = (needed_from_store - used) * battery.eta_discharge

    # --- AC balance settlement ---
    # The residual DC bus shortfall is served by the charger (AC->DC), fed from
    # PV surplus first and only then from the grid — never grid-imported while
    # the same slot exports/stores PV.
    dc_ac_demand = shortfall_dc / config.charger.eta

    if balance >= 0:
        # (a) cover the DC shortfall from PV surplus via the charger.
        if dc_ac_demand > _EPS:
            pv_for_dc = min(balance, dc_ac_demand)
            balance -= pv_for_dc
            dc_ac_demand -= pv_for_dc
        # (b) charge the battery through the charger, export the rest.
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
        # (c) DC shortfall PV could not cover imports via the charger.
        if dc_ac_demand > _EPS:
            grid_import += dc_ac_demand
    else:
        # No PV surplus: the DC shortfall imports via the charger.
        grid_import += dc_ac_demand
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
        support_dc24=dc24_from_grid and support.configured,
        support_dc48=dc48_support and support.configured,
        psu48_delivered_wh=psu48_delivered_wh,
        psu24_delivered_wh=psu24_delivered_wh,
        dcdc_input_wh=dcdc_input_wh,
        dcdc_loss_wh=dcdc_loss_wh,
        unserved_dc_wh=unserved_dc_wh,
        gate_open=gate_open,
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
