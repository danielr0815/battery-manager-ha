"""Data model for the Battery Manager core (frozen dataclasses only)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class BatteryParams:
    """48 V battery parameters."""

    capacity_wh: float = 5000.0
    soc_min_percent: float = 5.0
    soc_max_percent: float = 95.0
    eta_charge: float = 0.97
    eta_discharge: float = 0.97

    def energy_wh(self, soc_percent: float) -> float:
        return soc_percent / 100.0 * self.capacity_wh

    def soc_percent(self, energy_wh: float) -> float:
        return max(0.0, min(100.0, energy_wh / self.capacity_wh * 100.0))


@dataclass(frozen=True)
class ConverterParams:
    """AC->DC charger or DC->AC inverter parameters."""

    max_power_w: float = 2300.0
    eta: float = 0.92
    standby_power_w: float = 0.0


@dataclass(frozen=True)
class PVParams:
    """Distribution of daily PV forecasts onto hours (v1: two-window model)."""

    peak_power_w: float = 3200.0
    morning_start_hour: int = 7
    morning_end_hour: int = 13
    afternoon_end_hour: int = 18
    morning_ratio: float = 0.8


@dataclass(frozen=True)
class LoadProfile:
    """Static base + windowed variable load profile."""

    base_w: float = 50.0
    variable_w: float = 25.0
    variable_start_hour: int = 6
    variable_end_hour: int = 22

    def power_w(self, hour_of_day: int) -> float:
        power = self.base_w
        start, end = self.variable_start_hour, self.variable_end_hour
        if start < end:
            in_window = start <= hour_of_day < end
        elif start > end:
            # Night-spanning window (e.g. 20 -> 6): wrap around midnight so the
            # variable load is applied to the intended hours instead of none.
            in_window = hour_of_day >= start or hour_of_day < end
        else:
            in_window = False  # start == end: empty window
        if in_window:
            power += self.variable_w
        return power


@dataclass(frozen=True)
class SurplusLoad:
    """A switchable load meant to consume PV surplus (Fossibot, dehumidifier).

    Loads earlier in SystemConfig.loads have higher priority when surplus is
    scarce; with enough surplus they run in parallel.
    """

    load_id: str
    name: str
    nominal_power_w: float
    battery_tolerance: float = 0.15  # allowed battery share of the load's power
    min_runtime_min: int = 30
    energy_limited: bool = False  # True: needs energy until "full" (powerstation)
    capacity_wh: float = 0.0  # storage size if energy_limited
    target_soc_percent: float = 100.0


@dataclass(frozen=True)
class SurplusLoadState:
    """Runtime state of a surplus load, read from HA entities each cycle."""

    load_id: str
    available: bool = True  # False: unplugged/unavailable -> never scheduled
    soc_percent: float | None = None  # for energy_limited loads
    measured_power_w: float | None = None  # smoothed feedback power

    def remaining_energy_wh(self, load: SurplusLoad) -> float | None:
        """Energy still absorbable, or None if unlimited."""
        if not load.energy_limited:
            return None
        soc = self.soc_percent if self.soc_percent is not None else 0.0
        remaining = (load.target_soc_percent - soc) / 100.0 * load.capacity_wh
        return max(0.0, remaining)

    def planning_power_w(self, load: SurplusLoad) -> float:
        if self.measured_power_w is not None and self.measured_power_w > 0:
            return self.measured_power_w
        return load.nominal_power_w


@dataclass(frozen=True)
class Appliance:
    """Household appliance (washer, dishwasher) with a known run profile."""

    appliance_id: str
    name: str
    run_energy_wh: float
    run_duration_h: float
    opportunistic_start: bool = False  # expose "may start on surplus" advisor


@dataclass(frozen=True)
class ApplianceRun:
    """A detected running appliance: remaining consumption to add to AC load."""

    appliance_id: str
    remaining_energy_wh: float
    remaining_hours: float


@dataclass(frozen=True)
class SupportParams:
    """Emergency grid-support paths for the DC rails (docs/ALGORITHM.md D-A9).

    The two-bus model (docs/DC_TOPOLOGY.md, F-N3) splits the DC load into a
    24 V rail share (fed by the DC/DC converter from the battery, or by a
    grid 24 V PSU) and a native 48 V bus share, with per-device efficiency
    and power caps. All F-N3 fields carry NEUTRAL defaults — 100 % rail
    share, unit efficiencies, uncapped currents, gate always open — so the
    model reproduces the legacy single-bus behaviour bit-for-bit until the
    operator enters real device values (phased rollout).
    """

    configured: bool = False
    dc48_power_w: float = 60.0  # fixed-power PSU feeding the 48 V battery bus
    # Manual override (F-N2): the operator switched a PSU on externally —
    # the simulation must treat that path as permanently active over the
    # whole horizon (winter operation), while the executor keeps hands off.
    dc24_forced_on: bool = False
    dc48_forced_on: bool = False

    # --- F-N3 two-bus parameters (docs/DC_TOPOLOGY.md) ---
    # A FIXED native-48 V base load (W) carved off the DC load BEFORE the rail
    # split — for a roughly constant load wired directly to the 48 V bus, which
    # a percentage share cannot represent (it would scale with the total DC
    # load). 0 = none. Applied per slot as native48_base_w * duration, capped at
    # the slot's DC load.
    native48_base_w: float = 0.0
    # Fraction of the REMAINING DC load (after the fixed 48 V base) that sits on
    # the 24 V rail (rest = native 48 V bus load). 1.0 = today's behaviour.
    dc24_share: float = 1.0
    # DC/DC converter (battery 48 V -> 24 V rail): efficiency, rail-side
    # power cap (V_out x I_max, None = uncapped), output voltage.
    dcdc_eta: float = 1.0
    dcdc_max_power_w: float | None = None
    dcdc_output_voltage_v: float = 24.0
    # Grid-fed 24 V support PSU (replaces the DC/DC): efficiency, rail-side
    # cap, output voltage. When both sources are on, the higher output
    # voltage wins (operator rule, phase 2+); phase 1 selects by schedule.
    psu24_eta: float = 1.0
    psu24_max_power_w: float | None = None
    psu24_output_voltage_v: float = 24.0
    # 48 V support PSU: efficiency and rail-/bus-side cap wired for phase 3+;
    # phase 1 still injects the flat `dc48_power_w`. `gate_soc_percent` is the
    # voltage gate's SOC proxy — None = always open (neutral).
    psu48_eta: float = 1.0
    psu48_max_power_w: float | None = None
    psu48_output_voltage_v: float = 49.56
    gate_soc_percent: float | None = None


@dataclass(frozen=True)
class ControlParams:
    """Planner tuning (decisions D-A1..D-A4 in docs/ALGORITHM.md).

    `soc_buffer_percent` is the PLANNING buffer (threshold search floor,
    load-allocation floor, appliance advisor) — it may be set dynamically
    per run from the learned forecast uncertainty (D-C8).

    The grid-support escalation (D-A9) uses four ABSOLUTE battery-SOC
    thresholds, deliberately independent of the planning buffer so a
    dynamically widened planning buffer never moves the grid PSUs. The sane
    ordering (low to high) is:
      soc_min < dc48_activate < dc48_recovery <= dc24_activate < dc24_recovery
    Each stage is a hysteresis loop: it switches ON below its activate SOC and
    OFF again at/above its recovery SOC. A wider gap between activate and
    recovery latches a PSU on longer, so an SOC parked near a threshold holds
    steadily on grid instead of chattering across it each cycle. Defaults
    (10 / 11 / 5.5 / 10) reproduce the legacy hard-coded behaviour at the
    default battery config (soc_min 5 %, buffer 5 %).
    """

    inverter_min_soc_percent: float = 20.0
    soc_buffer_percent: float = 5.0
    support_dc24_activate_soc: float = 10.0
    support_dc24_recovery_soc: float = 11.0
    support_dc48_activate_soc: float = 5.5
    support_dc48_recovery_soc: float = 10.0
    hysteresis_percent: float = 1.0
    threshold_inertia_percent: float = 2.0
    export_tiebreak: float = 0.05
    min_switch_interval_s: int = 60


@dataclass(frozen=True)
class SystemConfig:
    """Complete static system description."""

    battery: BatteryParams = field(default_factory=BatteryParams)
    charger: ConverterParams = field(
        default_factory=lambda: ConverterParams(eta=0.92, standby_power_w=10.0)
    )
    inverter: ConverterParams = field(
        default_factory=lambda: ConverterParams(eta=0.95, standby_power_w=15.0)
    )
    pv: PVParams = field(default_factory=PVParams)
    ac_profile: LoadProfile = field(
        default_factory=lambda: LoadProfile(50.0, 75.0, 6, 20)
    )
    dc_profile: LoadProfile = field(
        default_factory=lambda: LoadProfile(50.0, 25.0, 6, 22)
    )
    control: ControlParams = field(default_factory=ControlParams)
    support: SupportParams = field(default_factory=SupportParams)
    loads: tuple[SurplusLoad, ...] = ()
    appliances: tuple[Appliance, ...] = ()


@dataclass(frozen=True)
class HourSlot:
    """One simulation interval (first slot may be a partial hour)."""

    index: int
    start: datetime
    duration: float  # fraction of an hour, (0, 1]
    hour_of_day: int
    pv_wh: float
    ac_wh: float  # profile + appliance remainder, WITHOUT surplus loads
    dc_wh: float


@dataclass(frozen=True)
class PlanInputs:
    """Everything a planning run needs, assembled by series.build_slots()."""

    now: datetime
    start_soc_percent: float
    slots: tuple[HourSlot, ...]
    load_states: tuple[SurplusLoadState, ...] = ()
    appliance_runs: tuple[ApplianceRun, ...] = ()


@dataclass(frozen=True)
class HourFlows:
    """Energy flows of one simulated slot."""

    soc_start_percent: float
    soc_end_percent: float
    grid_import_wh: float
    grid_export_wh: float
    battery_charge_wh: float
    battery_discharge_wh: float
    inverter_on: bool
    inverter_output_wh: float
    extra_ac_wh: float  # surplus loads scheduled in this slot
    support_dc24: bool
    support_dc48: bool
    # F-N3 two-bus diagnostics (docs/DC_TOPOLOGY.md); 0 under neutral defaults.
    psu48_delivered_wh: float = 0.0  # PSU energy actually put on the 48 V bus
    psu24_delivered_wh: float = 0.0  # rail energy served from the grid PSU
    dcdc_input_wh: float = 0.0  # bus energy the DC/DC drew to feed the rail
    dcdc_loss_wh: float = 0.0
    unserved_dc_wh: float = 0.0  # rail demand above the active source's cap
    gate_open: bool = False  # 48 V PSU voltage gate open this slot


@dataclass(frozen=True)
class Trajectory:
    """Result of simulating one policy over all slots."""

    flows: tuple[HourFlows, ...]
    total_import_wh: float
    total_export_wh: float
    end_soc_percent: float

    @property
    def min_soc_percent(self) -> float:
        if not self.flows:
            return self.end_soc_percent
        return min(f.soc_end_percent for f in self.flows)

    @property
    def max_soc_percent(self) -> float:
        if not self.flows:
            return self.end_soc_percent
        return max(f.soc_end_percent for f in self.flows)


@dataclass(frozen=True)
class LoadPlan:
    """Planned activation for one surplus load."""

    load_id: str
    schedule: tuple[bool, ...]  # per slot
    planned_energy_wh: float
    # One entry per activation decision, for transparency/diagnostics:
    # (start slot, covered slot count, pass number, booked energy Wh).
    # Pass 1 = direct surplus, pass 2 = preemptive ("zielbasiert").
    allocations: tuple[tuple[int, int, int, float], ...] = ()

    @property
    def active_now(self) -> bool:
        return bool(self.schedule) and self.schedule[0]


@dataclass(frozen=True)
class PlanResult:
    """Complete output of one planning run (single consistent trajectory)."""

    threshold_percent: float
    inverter_on: bool  # raw policy for slot 0 (hysteresis applied by caller)
    trajectory: Trajectory
    load_plans: tuple[LoadPlan, ...]
    appliance_windows: dict[str, bool]
    support_dc24_now: bool
    support_dc48_now: bool
    grid_import_kwh: float
    grid_export_kwh: float
    lost_surplus_kwh: float  # export remaining after load allocation
    min_soc_percent: float
    max_soc_percent: float
    hours_to_max_soc: int
