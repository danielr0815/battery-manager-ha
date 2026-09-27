"""Reachable battery budgets for reserve preparation, without support latches.

This kernel describes one fixed physical battery/DC branch. Automatic PSU
decisions stay in the forward simulation: copying a reference run's early
protection switches would turn later, better-preserved DC consumption into grid
consumption. Unknown future PSU output therefore never becomes storage credit.

The inverse is piecewise algebra, not a search over simulated trajectories.
In particular, AC cannot help reach an end energy below its own discharge floor.
"""

from dataclasses import dataclass

from .model import HourSlot, SystemConfig

ENERGY_EPSILON_WH = 1e-6
FLOW_EPSILON_WH = 1e-9


def dc_loads(config: SystemConfig, slot: HourSlot) -> tuple[float, float]:
    """Return native bus and rail demand before choosing their physical sources."""
    support = config.support
    native_base = min(slot.dc_wh, support.native48_base_w * slot.duration)
    remaining = slot.dc_wh - native_base
    rail = remaining * support.dc24_share
    return native_base + (remaining - rail), rail


def dc_discharge(
    energy: float, floor: float, bus_load: float, eta: float
) -> tuple[float, float]:
    """Stored energy drawn and uncovered bus demand; shared with step_hour."""
    needed = bus_load / eta
    drawn = min(needed, max(0.0, energy - floor))
    return drawn, (needed - drawn) * eta


def charger_dc_demand(
    shortfall: float, limit: float, standby: float, eta: float
) -> tuple[float, float]:
    """DC delivered and AC needed under the charger's single input rating."""
    served = min(shortfall, max(0.0, limit - standby) * eta)
    demand = served / eta
    if served > FLOW_EPSILON_WH:
        demand += standby
    return served, demand


def pv_storage_charge(
    energy: float,
    maximum: float,
    available_ac: float,
    charger_limit: float,
    standby: float,
    charger_eta: float,
    battery_eta: float,
    served_dc: float,
) -> tuple[float, float]:
    """AC intake and stored PV; converter overhead is paid only once.

    PV pays converter overhead without a phantom grid import. Required intake
    includes that overhead, so charging reaches the ceiling instead of approaching
    it asymptotically. Preserve the established arithmetic order: golden energy must not
    drift merely because reserve planning reuses the same physical primitive.
    """
    headroom = max(0.0, maximum - energy)
    standby = 0.0 if served_dc > FLOW_EPSILON_WH else standby
    needed = (
        headroom / (battery_eta * charger_eta) + standby
        if headroom > FLOW_EPSILON_WH
        else 0.0
    )
    intake = min(available_ac, charger_limit, needed)
    stored = (
        max(0.0, intake - standby) * charger_eta * battery_eta
        if intake > FLOW_EPSILON_WH
        else 0.0
    )
    return intake, stored


@dataclass(frozen=True)
class BatteryStep:
    """Energy-domain equivalent of the fixed-source portion of ``step_hour``.

    All energy fields use Wh. ``floor``, ``maximum``, ``inverter_floor``,
    ``dc`` and ``ac`` are stored battery energy; ``balance``, charger limits,
    standby and feed-in are on the AC side. Efficiencies are unitless ratios.
    """

    floor: float
    maximum: float
    inverter_floor: float
    dc: float
    balance: float
    charger_limit: float
    charger_standby: float
    battery_eta: float
    charger_eta: float
    discharge_eta: float
    ac: float
    feedin: float

    @property
    def charge_eta(self) -> float:
        return self.charger_eta * self.battery_eta

    @classmethod
    def build(
        cls,
        config: SystemConfig,
        slot: HourSlot,
        extra: float,
        upper: float,
        feedin: float = 0.0,
    ) -> BatteryStep:
        """Prepare coefficients once; only useful AC demand opens an AC window."""
        battery, support = config.battery, config.support
        native, rail = dc_loads(config, slot)
        rail_available = support.dc24_available and (
            support.psu24_max_power_w is None
            or rail <= support.psu24_max_power_w * slot.duration
        )
        forced24 = support.dc24_forced_on and rail_available
        forced48 = support.dc48_forced_on and support.dc48_available
        served = (
            min(rail, support.dcdc_max_power_w * slot.duration)
            if support.dcdc_max_power_w is not None
            else rail
        )
        dc = native + (0.0 if forced24 else served / support.dcdc_eta)
        pv = min(slot.pv_wh * upper, config.pv.peak_power_w * slot.duration)
        balance = pv - slot.ac_wh - extra
        useful_ac = balance < -ENERGY_EPSILON_WH and not (forced24 or forced48)
        ac = (
            min(
                -balance + config.inverter.standby_power_w * slot.duration,
                config.inverter.max_power_w * slot.duration,
            )
            / (config.inverter.eta * battery.eta_discharge)
            if useful_ac
            else 0.0
        )
        return cls(
            battery.energy_wh(battery.soc_min_percent),
            max(
                battery.energy_wh(battery.soc_min_percent),
                battery.energy_wh(battery.soc_max_percent),
            ),
            battery.energy_wh(
                max(battery.soc_min_percent, config.control.inverter_min_soc_percent)
            ),
            dc / battery.eta_discharge,
            balance,
            config.charger.max_power_w * slot.duration,
            min(config.charger.max_power_w, config.charger.standby_power_w)
            * slot.duration,
            battery.eta_charge,
            config.charger.eta,
            battery.eta_discharge,
            ac,
            feedin,
        )

    def project(self, energy: float, *, ac: bool = True) -> tuple[float, float]:
        """Return end energy and export under maximal useful AC, or DC alone."""
        used, shortfall = dc_discharge(
            energy, self.floor, self.dc * self.discharge_eta, self.discharge_eta
        )
        after_dc = energy - used
        if self.balance < 0:
            return (
                after_dc
                - min(self.ac if ac else 0.0, max(0.0, after_dc - self.inverter_floor)),
                0.0,
            )
        served, dc_ac = charger_dc_demand(
            shortfall, self.charger_limit, self.charger_standby, self.charger_eta
        )
        dc_from_pv = min(self.balance, dc_ac)
        remainder = self.balance - dc_from_pv
        export = min(self.feedin, remainder)
        remainder -= export
        intake, stored = pv_storage_charge(
            after_dc,
            self.maximum,
            remainder,
            max(0.0, self.charger_limit - dc_ac),
            self.charger_standby,
            self.charger_eta,
            self.battery_eta,
            served,
        )
        return after_dc + stored, export + remainder - intake

    def incoming_ceiling(self, following: float, export: float) -> float:
        """Largest input meeting the next ceiling and the attainable export budget.

        The two PV branches are separated where the battery can supply the full
        DC load. Below that point PV first serves the DC shortfall, through the
        same charger power cap; above it PV can charge the remaining headroom.
        """
        if self.balance < 0:
            ac = self.ac if following >= self.inverter_floor - ENERGY_EPSILON_WH else 0
            return min(self.maximum, following + self.dc + ac)

        intake = min(max(0.0, self.balance - self.feedin), self.charger_limit)
        capture = max(0.0, self.balance - export)
        boundary = self.floor + self.dc
        # Full DC service from storage: ordinary capped charging.
        high: float | None = self.maximum
        if following < self.maximum - ENERGY_EPSILON_WH:
            charge = max(0.0, intake - self.charger_standby) * self.charge_eta
            high = min(self.maximum, following + self.dc - charge)
        if capture > ENERGY_EPSILON_WH:
            if capture > intake + ENERGY_EPSILON_WH:
                high = None  # only direct PV/DC service can satisfy it
            else:
                headroom = max(
                    ENERGY_EPSILON_WH,
                    (capture - self.charger_standby) * self.charge_eta,
                )
                high = min(
                    high if high is not None else self.maximum,
                    self.maximum + self.dc - headroom,
                )
        if high is not None and high >= boundary - ENERGY_EPSILON_WH:
            return min(self.maximum, high)

        # A storage shortfall is served directly from PV. No fabricated battery
        # recharge or DC energy beyond the single charger's power budget.
        needed_dc_ac = 0.0
        if following < self.maximum - ENERGY_EPSILON_WH:
            needed_dc_ac = intake - (following - self.floor) / self.charge_eta
        capacity_ac = (self.maximum - self.floor) / self.charge_eta
        needed_dc_ac = max(
            needed_dc_ac,
            capture - capacity_ac if intake >= capture - ENERGY_EPSILON_WH else capture,
        )
        shortfall = max(0.0, needed_dc_ac - self.charger_standby) * self.charger_eta
        return max(
            self.floor,
            min(
                self.maximum,
                boundary - ENERGY_EPSILON_WH,
                boundary - shortfall / self.discharge_eta,
            ),
        )
