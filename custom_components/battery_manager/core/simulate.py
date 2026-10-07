"""Pure energy-flow simulation (docs/ALGORITHM.md §1, topology REQUIREMENTS.md §1.1).

Topology: PV feeds the AC side. The battery charges exclusively through the
AC->DC charger and discharges through the DC->AC inverter. DC loads hang off
the battery. Emergency support paths (D-A9) can shift DC loads to the grid.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, timedelta
from math import isfinite

from .model import (
    DCDeficitInterval,
    HourFlows,
    HourSlot,
    PlanInputs,
    SystemConfig,
    Trajectory,
)
from .planning_control import check_cancelled
from .reserve_energy import (
    FLOW_EPSILON_WH,
    charger_dc_demand,
    dc_discharge,
    dc_loads,
    pv_storage_charge,
)

_EPS = FLOW_EPSILON_WH


def step_hour(
    config: SystemConfig,
    soc_percent: float,
    slot: HourSlot,
    threshold_percent: float,
    extra_ac_wh: float = 0.0,
    dc24_from_grid: bool = False,
    dc48_support: bool = False,
    pv_scale: float = 1.0,
    feedin_wh: float = 0.0,
    *,
    inverter_limit_w: float | None = None,
) -> HourFlows:
    """Simulate one slot; returns all flows. Never mutates anything."""
    # Reserve changes an operating limit, not the validated plant configuration.
    # Avoid reconstructing SystemConfig for every five-minute candidate step.
    inverter_power_w = config.inverter.max_power_w
    if inverter_limit_w is not None:
        if (
            not isfinite(inverter_limit_w)
            or not 0 <= inverter_limit_w <= inverter_power_w
        ):
            raise ValueError(
                "Inverter limit must be finite and within configured power"
            )
        inverter_power_w = inverter_limit_w
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
    feedin_eff = 0.0

    inverter_on = soc_percent > threshold_percent
    if config.support.coordinated and (dc24_from_grid or dc48_support):
        inverter_on = False
    support = config.support

    # --- AC balance computed early ---
    # Needed before the 48 V gate: during a NET-CHARGING slot the charger/PV
    # lifts the 48 V bus above the PSU's output voltage, so the real Meanwell
    # self-gates OFF and delivers nothing (docs/DC_TOPOLOGY.md §4, Jury-Gap #1).
    # It is also the single balance against which the residual DC bus load is
    # settled, so a same-slot PV surplus covers it instead of a phantom import.
    # F-PREDRAIN F3: the lower-buffer stress gate re-simulates the horizon with
    # a pessimistic PV multiplier. `pv_scale` scales THIS slot's PV inside the
    # loop; the peak cap already applied to the unscaled input at build time.
    # 1.0 leaves the value byte-identical (neutral default — the multiply is
    # skipped so the plan is bit-for-bit). An OPTIMISTIC scale (beta > 1.0) is
    # re-clamped at the physical peak (peak_power_w * duration) so the upper-buffer
    # gate cannot conjure PV a real array could never deliver (FIX-8); scales <=
    # 1.0 keep the legacy path bit-identical (never clamped).
    if pv_scale == 1.0:
        pv_wh = slot.pv_wh
    else:
        pv_wh = slot.pv_wh * pv_scale
        if pv_scale > 1.0:
            pv_wh = min(pv_wh, config.pv.peak_power_w * slot.duration)
    ac_total = slot.ac_wh + extra_ac_wh
    if inverter_on:
        ac_total += config.inverter.standby_power_w * slot.duration
    balance = pv_wh - ac_total
    net_charging = balance > _EPS

    # --- DC load split across the two buses (F-N3, docs/DC_TOPOLOGY.md) ---
    # A FIXED native-48 V base load is carved off first (a constant load wired
    # directly to the 48 V bus, which a percentage cannot represent), then
    # `dc24_share` of the REMAINING load sits on the 24 V rail and the rest is
    # native 48 V bus load. Neutral defaults (base=0, share=1.0) => whole load
    # on the rail, bit-for-bit as before.
    native48_wh, rail_wh = dc_loads(config, slot)
    psu24_delivered_wh = 0.0
    psu24_ac_wh = 0.0
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
        psu24_ac_wh = served / support.psu24_eta
        balance -= psu24_ac_wh
        net_charging = balance > _EPS
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
    if support.coordinated:
        gate_open = (
            gate_open
            and support.psu48_bus_voltage_v is not None
            and support.psu48_bus_voltage_v <= support.psu48_output_voltage_v
        )
    psu48_delivered_wh = 0.0
    psu48_battery_charge_wh = 0.0
    psu48_ac_wh = 0.0
    if gate_open:
        potential = support.dc48_power_w * slot.duration
        if support.coordinated:
            # A current-limited PSU supplies U_bus * I_max, not its nameplate
            # power at every voltage. Unknown bus voltage receives no credit.
            voltage = support.psu48_bus_voltage_v
            potential = (
                min(voltage, support.psu48_output_voltage_v)
                / support.psu48_output_voltage_v
                * support.psu48_max_power_w
                * slot.duration
                if support.psu48_max_power_w is not None
                and voltage is not None
                and voltage <= support.psu48_output_voltage_v
                else 0.0
            )
            if voltage == support.psu48_output_voltage_v:
                # At the regulated voltage estimate direct load coverage only;
                # extra charging current cannot be inferred from voltage.
                potential = min(potential, bus_load)
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
        # Reserve-only detail: old recordings keep their neutral added field.
        psu48_battery_charge_wh = absorbed if config.reserve.enabled else 0.0
        psu48_delivered_wh = direct + absorbed / battery.eta_charge
        psu48_ac_wh = psu48_delivered_wh / support.psu48_eta
        balance -= psu48_ac_wh

    # PV covers house/committed AC first, then actual PSU intake. Counting
    # every PSU Wh as grid energy fabricated simultaneous import and export.
    psu_ac_wh = psu24_ac_wh + psu48_ac_wh
    psu_grid_import_wh = (
        max(0.0, psu_ac_wh - max(0.0, pv_wh - ac_total)) if psu_ac_wh else 0.0
    )

    # --- Remaining 48 V bus load drains the battery. Any shortfall (store at
    # floor) is NOT imported here but carried to the AC settlement, so a
    # same-slot PV surplus covers it via the charger instead of importing grid
    # while PV is simultaneously stored/exported (energy conservation). ---
    shortfall_dc = 0.0
    if bus_load > _EPS:
        used, shortfall_dc = dc_discharge(
            energy, floor_wh, bus_load, battery.eta_discharge
        )
        energy -= used
        battery_discharge += used

    # --- AC balance settlement ---
    # The residual DC bus shortfall is served by the charger (AC->DC), fed from
    # PV surplus first and only then from the grid — never grid-imported while
    # the same slot exports/stores PV.
    max_charger_ac = config.charger.max_power_w * slot.duration
    standby = min(max_charger_ac, config.charger.standby_power_w * slot.duration)
    # One AC-side rating covers DC service, converter overhead and storage.
    # Demand above it remains physically unserved, even with abundant PV.
    served_dc, dc_ac_demand = charger_dc_demand(
        shortfall_dc, max_charger_ac, standby, config.charger.eta
    )
    unserved_dc_wh += shortfall_dc - served_dc
    reserved_charger_ac = dc_ac_demand

    if balance >= 0:
        # (a) cover the DC shortfall from PV surplus via the charger.
        if dc_ac_demand > _EPS:
            pv_for_dc = min(balance, dc_ac_demand)
            balance -= pv_for_dc
            dc_ac_demand -= pv_for_dc
        # (a2) F-FEEDIN: booked early feed-in is served from the surplus AFTER
        # the DC shortfall (an export must never coexist with a same-slot
        # import — review #1) but BEFORE battery charging: the pass-through
        # reroutes surplus that would have been stored into the grid 1:1, on
        # the same conversion path as natural export (no new efficiency). The
        # clamp to the remaining balance is the hard guarantee that feed-in
        # can never force an import; a deficit slot below never serves any
        # (the planner only books surplus slots, requirement 1: the battery
        # is never actively discharged for feed-in).
        if feedin_wh > _EPS:
            feedin_eff = min(feedin_wh, max(0.0, balance))
            balance -= feedin_eff
            grid_export += feedin_eff
        # (b) charge the battery through the charger, export the rest.
        max_charger_ac = max(0.0, max_charger_ac - reserved_charger_ac)
        charger_ac, stored = pv_storage_charge(
            energy,
            ceil_wh,
            balance,
            max_charger_ac,
            standby,
            config.charger.eta,
            battery.eta_charge,
            served_dc,
        )
        if charger_ac > _EPS:
            energy += stored
            battery_charge += stored
            balance -= charger_ac
        grid_export += max(0.0, balance)
        if balance < 0:  # pragma: no cover - balance >= 0 by construction
            grid_import += -balance
        # (c) DC shortfall PV could not cover imports via the charger.
        if dc_ac_demand > _EPS:
            if support.coordinated:
                unserved_dc_wh += min(served_dc, dc_ac_demand * config.charger.eta)
            else:
                grid_import += dc_ac_demand
    else:
        # No PV surplus: the DC shortfall imports via the charger.
        if support.coordinated:
            unserved_dc_wh += served_dc
        else:
            grid_import += dc_ac_demand
        deficit = -balance
        if inverter_on:
            inv_floor_wh = battery.energy_wh(
                max(battery.soc_min_percent, config.control.inverter_min_soc_percent)
            )
            available_store = max(0.0, energy - inv_floor_wh)
            available_ac = available_store * battery.eta_discharge * config.inverter.eta
            max_inv_ac = inverter_power_w * slot.duration
            # Support must not buy an inverter-to-PSU charging loop. Legacy
            # uncoordinated callers may release AC only for their house demand.
            house_deficit = max(0.0, ac_total - pv_wh)
            ac_out = min(deficit, house_deficit, max_inv_ac, available_ac)
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
        psu48_battery_charge_wh=psu48_battery_charge_wh,
        psu_grid_import_wh=psu_grid_import_wh,
        psu48_delivered_wh=psu48_delivered_wh,
        psu24_delivered_wh=psu24_delivered_wh,
        dcdc_input_wh=dcdc_input_wh,
        dcdc_loss_wh=dcdc_loss_wh,
        unserved_dc_wh=unserved_dc_wh,
        dc_deficit_intervals=(
            DCDeficitInterval(
                slot.start,
                (
                    slot.start.astimezone(UTC) + timedelta(hours=slot.duration)
                ).astimezone(slot.start.tzinfo)
                if slot.start.tzinfo
                else slot.start + timedelta(hours=slot.duration),
                unserved_dc_wh,
            ),
        )
        if unserved_dc_wh > 0
        else (),
        gate_open=gate_open,
        feedin_wh=feedin_eff,
    )


def simulate(
    config: SystemConfig,
    inputs: PlanInputs,
    threshold_percent: float,
    extra_ac_wh: tuple[float, ...] | None = None,
    dc24_schedule: tuple[bool, ...] | None = None,
    dc48_schedule: tuple[bool, ...] | None = None,
    pv_scale: float | Sequence[float] = 1.0,
    feedin_wh: tuple[float, ...] | None = None,
) -> Trajectory:
    """Simulate the whole horizon under the policy `inverter on <=> SOC > threshold`.

    `pv_scale` multiplies each slot's PV forecast (F-PREDRAIN F3):
    - a SCALAR applies the same factor to every slot — the planner re-runs the
      horizon pessimistically (alpha < 1.0) or optimistically (beta > 1.0);
    - a SEQUENCE is a per-slot factor, index-aligned with `inputs.slots`, so the
      windowed lower-buffer stress gate (§3.3 v2) can stress only the bet's
      recovery window and leave the rest of the horizon at nominal PV.
    1.0 (scalar) is the neutral default and keeps the result bit-identical to the
    unscaled run.

    `feedin_wh` (F-FEEDIN) is the planner's booked early feed-in per slot,
    served from the slot's PV surplus before battery charging (see step_hour).
    None (the neutral default) feeds in nothing and keeps the result
    bit-identical to a run without the feature.

    The optional per-slot series are index-aligned with `inputs.slots`; a series
    SHORTER than the horizon used to crash with a bare IndexError mid-run
    (code review 2026-07) and now fails up-front with a speaking ValueError.
    """
    check_cancelled()
    n_slots = len(inputs.slots)
    for name, series in (
        ("extra_ac_wh", extra_ac_wh),
        ("dc24_schedule", dc24_schedule),
        ("dc48_schedule", dc48_schedule),
        ("pv_scale", pv_scale if not isinstance(pv_scale, (int, float)) else None),
        ("feedin_wh", feedin_wh),
    ):
        if series is not None and len(series) < n_slots:
            raise ValueError(
                f"simulate: {name} has {len(series)} entries but the horizon "
                f"has {n_slots} slots"
            )

    if (
        config.reserve.enabled
        and config.support.configured
        and config.support.coordinated
    ):
        from .reserve import simulate_reserve

        return simulate_reserve(config, inputs, extra_ac_wh, pv_scale, feedin_wh)

    if config.support.configured and config.support.coordinated:
        from .support import simulate_support

        return simulate_support(
            config, inputs, threshold_percent, extra_ac_wh, pv_scale, feedin_wh
        )

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
            # Inline isinstance (was a `seq_scale` flag) so mypy narrows the
            # union in each branch; same runtime semantics.
            pv_scale=pv_scale[i]
            if not isinstance(pv_scale, (int, float))
            else pv_scale,
            feedin_wh=feedin_wh[i] if feedin_wh else 0.0,
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
