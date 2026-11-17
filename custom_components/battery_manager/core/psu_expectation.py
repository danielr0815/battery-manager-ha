"""Display-only SOC scenario for sustained, measured 48 V PSU delivery."""

from math import isfinite

from .model import PlanInputs, SystemConfig, Trajectory
from .simulation_steps import switching_schedule

MIN_OBSERVED_SECONDS = 1800.0
MAX_PROJECTION_SECONDS = 48 * 3600.0
_TIME_EPSILON_SECONDS = 1e-5  # datetime rounding in substep/slot conversion


def expected_soc_with_psu48(
    config: SystemConfig,
    inputs: PlanInputs,
    trajectory: Trajectory,
    delivery_w: float,
    observed_seconds: float,
) -> tuple[float, ...]:
    """Project avoided discharge along the accepted schedule, up to each slot end.

    The scenario assumes uninterrupted delivery in the same supply mode. It
    credits only native 48 V load already drawn from storage in that plan.
    This separate calculation cannot issue a load, source or charging decision.
    """
    support = config.support
    limit_w = support.psu48_max_power_w
    if not (
        support.adaptive_voltage_gate
        and support.coordinated
        and limit_w is not None
        and isfinite(delivery_w)
        and 0 < delivery_w <= limit_w
        and isfinite(observed_seconds)
        and observed_seconds >= MIN_OBSERVED_SECONDS
    ):
        return ()

    battery = config.battery
    horizon = min(observed_seconds, MAX_PROJECTION_SECONDS)
    at = inputs.now.timestamp()
    until = at + horizon
    additional_wh = 0.0
    points = []
    for slot, flow in zip(inputs.slots, trajectory.flows, strict=True):
        start = slot.start.timestamp()
        end = start + slot.duration * 3600
        if (
            abs(start - at) > _TIME_EPSILON_SECONDS
            or end > until + _TIME_EPSILON_SECONDS
            or not flow.switching_schedule
        ):
            break
        intervals = switching_schedule((slot,), (flow,))
        if len(intervals) != 1:
            break
        (interval,) = intervals
        if (
            interval.start.timestamp() != start
            or abs(interval.end.timestamp() - end) > _TIME_EPSILON_SECONDS
            or interval.inverter_on
            or not interval.support_dc24
            or not interval.support_dc48
            or flow.dcdc_input_wh > 0
            or flow.unserved_dc_wh > 0
        ):
            break

        native_wh = min(slot.dc_wh, support.native48_base_w * slot.duration)
        # Subtract delivery already credited by the planner even where the
        # exact usage split is unknown. Only replace existing discharge.
        offset_wh = max(
            0.0,
            min(native_wh, delivery_w * slot.duration) - flow.psu48_delivered_wh,
        )
        additional_wh += min(
            flow.battery_discharge_wh, offset_wh / battery.eta_discharge
        )
        # Maintain the difference from the accepted SOC path, consuming that
        # headroom if the plan charges to the battery's physical ceiling.
        headroom_wh = battery.energy_wh(
            max(0.0, battery.soc_max_percent - flow.soc_end_percent)
        )
        additional_wh = min(headroom_wh, additional_wh)
        points.append(flow.soc_end_percent + battery.soc_percent(additional_wh))
        at = end
    return tuple(points)
