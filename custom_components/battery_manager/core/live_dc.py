"""F-DC-PV-MARKET: measured PV permission without battery discharge credit."""

from dataclasses import replace
from datetime import datetime

from .model import HourSlot, SystemConfig
from .reserve_energy import ENERGY_EPSILON_WH
from .simulate import step_hour

# Entry needs 10% excess useful PV; continued operation needs full DC coverage.
PV_SOURCE_START_RATIO = 1.1


def pv_covers_dc(
    config: SystemConfig,
    soc: float,
    now: datetime,
    surplus_w: float | None,
    dc_w: float | None,
    active: bool,
) -> bool:
    """Use the same converter limits/losses as planning, including full SOC.

    Positive converter circulation is allowed; net battery depletion, phantom
    PSU current and uncovered loads are not. Manual/protection are checked by
    the single actuator owner, not weakened by this permission.
    """
    if surplus_w is None or dc_w is None or surplus_w <= 0 or dc_w < 0:
        return False
    ratio = 1.0 if active else PV_SOURCE_START_RATIO
    slot = HourSlot(0, now, 1, now.hour, surplus_w / ratio, 0, dc_w)
    natural = step_hour(
        replace(config, support=replace(config.support, gate_soc_percent=None)),
        soc,
        slot,
        100,
        inverter_limit_w=0,
    )
    return (
        natural.battery_charge_wh + ENERGY_EPSILON_WH >= natural.battery_discharge_wh
        and natural.unserved_dc_wh <= ENERGY_EPSILON_WH
        and natural.grid_import_wh <= ENERGY_EPSILON_WH
    )
