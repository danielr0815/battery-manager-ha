"""Display-only SOC scenarios for the confirmed 48 V support topology."""

from collections.abc import Callable, Iterator
from math import isfinite

from .model import HourFlows, HourSlot, PlanInputs, SystemConfig, Trajectory
from .reserve_energy import ENERGY_EPSILON_WH, dc_loads
from .simulation_steps import switching_schedule
from .uncertainty import effective_uncertainty

MIN_OBSERVED_SECONDS = 1800.0
MAX_PROJECTION_SECONDS = 48 * 3600.0
# Leave room for regulation around the grid setpoint. Daytime AC loads and
# solar production can change quickly, so reuse a short observed window only.
GRID_IMPORT_MARGIN_W = 50.0
_MAX_GRID_IMPORT_PROJECTION_SECONDS = 3600.0
_TIME_EPSILON_SECONDS = 1e-5  # datetime rounding in substep/slot conversion


def expected_soc_with_psu48(
    config: SystemConfig,
    inputs: PlanInputs,
    trajectory: Trajectory,
    delivery_w: float,
    observed_seconds: float,
) -> tuple[float, ...]:
    """Return the projected SOC points for callers that need only the curve."""
    points, _ = project_soc_with_psu48(
        config, inputs, trajectory, delivery_w, observed_seconds
    )
    return points


def project_soc_with_psu48(
    config: SystemConfig,
    inputs: PlanInputs,
    trajectory: Trajectory,
    delivery_w: float,
    observed_seconds: float,
) -> tuple[tuple[float, ...], str | None]:
    """Project avoided discharge and explain why an empty curve cannot start.

    The scenario assumes uninterrupted delivery in the same supply mode. It
    credits only native 48 V load already drawn from storage in that plan.
    This separate calculation cannot issue a load, source or charging decision.
    """
    support = config.support
    limit_w = support.psu48_max_power_w
    if delivery_w == 0:
        return (), "no_positive_delivery"
    if not (
        support.adaptive_voltage_gate
        and support.coordinated
        and limit_w is not None
        and isfinite(delivery_w)
        and 0 < delivery_w <= limit_w
        and isfinite(observed_seconds)
        and observed_seconds >= MIN_OBSERVED_SECONDS
    ):
        return (), "projection_unsupported"

    battery = config.battery
    additional_wh = 0.0

    def project_point(slot: HourSlot, flow: HourFlows) -> float:
        nonlocal additional_wh
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
        # Keep the accumulated difference within the accepted path's headroom.
        headroom_wh = battery.energy_wh(
            max(0.0, battery.soc_max_percent - flow.soc_end_percent)
        )
        additional_wh = min(headroom_wh, additional_wh)
        return flow.soc_end_percent + battery.soc_percent(additional_wh)

    return _project_soc(inputs, trajectory, observed_seconds, project_point)


def project_soc_with_net_battery_discharge(
    config: SystemConfig,
    inputs: PlanInputs,
    trajectory: Trajectory,
    net_battery_discharge_w: float,
    observed_seconds: float,
    *,
    valid_until: float | None = None,
) -> tuple[tuple[float, ...], str | None]:
    """Continue observed battery-terminal discharge for comparable intervals.

    The caller supplies a conservative power from a stable discharge phase in
    the same confirmed topology. The measurement quantifies residual discharge
    at the battery terminals. It cannot identify any individual source output.
    A supplied UTC timestamp bounds the projection to the comparable night.
    """
    support = config.support
    if not (
        support.adaptive_voltage_gate
        and support.coordinated
        and support.native48_base_w > 0
        and isfinite(net_battery_discharge_w)
        and net_battery_discharge_w >= 0
        and isfinite(observed_seconds)
        and observed_seconds >= MIN_OBSERVED_SECONDS
        and (valid_until is None or isfinite(valid_until))
    ):
        return (), "projection_unsupported"

    battery = config.battery
    floor_wh = battery.energy_wh(battery.soc_min_percent)
    ceiling_wh = battery.energy_wh(battery.soc_max_percent)
    projected_wh = battery.energy_wh(inputs.start_soc_percent)

    def project_point(slot: HourSlot, flow: HourFlows) -> float | str:
        nonlocal projected_wh
        native_wh, _ = dc_loads(config, slot)
        if (
            max(slot.pv_wh, slot.pv_p90_wh or 0.0) > ENERGY_EPSILON_WH
            or abs(flow.battery_charge_wh - flow.psu48_battery_charge_wh)
            > ENERGY_EPSILON_WH
            or flow.inverter_output_wh > ENERGY_EPSILON_WH
            # The stable period is transferable only to the fixed native load.
            # AC and 24 V demand in this topology use their own grid supplies.
            or abs(native_wh - support.native48_base_w * slot.duration)
            > ENERGY_EPSILON_WH
        ):
            return "no_matching_plan_window"
        observed_wh = net_battery_discharge_w * slot.duration
        if observed_wh > native_wh + ENERGY_EPSILON_WH:
            return "net_discharge_exceeds_native_load"
        limit_w = support.psu48_max_power_w
        if (
            limit_w is not None
            and native_wh - observed_wh > limit_w * slot.duration + ENERGY_EPSILON_WH
        ):
            return "no_matching_plan_window"
        # The model can alternate charging and discharging within one slot.
        # Both modelled flows are replaced by the terminal measurement, so
        # the indirect curve cannot inherit the model's PSU battery charging.
        next_wh = projected_wh - observed_wh / battery.eta_discharge
        if (
            next_wh < floor_wh - ENERGY_EPSILON_WH
            or projected_wh > ceiling_wh + ENERGY_EPSILON_WH
        ):
            return "net_storage_limit"
        projected_wh = next_wh
        return battery.soc_percent(max(floor_wh, min(ceiling_wh, projected_wh)))

    return _project_soc(
        inputs,
        trajectory,
        observed_seconds,
        project_point,
        valid_until=valid_until,
    )


def _project_soc(
    inputs: PlanInputs,
    trajectory: Trajectory,
    observed_seconds: float,
    project_point: Callable[[HourSlot, HourFlows], float | str],
    *,
    valid_until: float | None = None,
) -> tuple[tuple[float, ...], str | None]:
    horizon = min(observed_seconds, MAX_PROJECTION_SECONDS)
    at = inputs.now.timestamp()
    until = at + horizon
    horizon_reason = "observation_shorter_than_plan_interval"
    if valid_until is not None and valid_until < until:
        until = valid_until
        horizon_reason = "net_window_ends_before_interval"
    points = []
    reason = "no_matching_plan_window"
    for slot, flow, end in _matching_plan_slots(inputs, trajectory):
        if end > until + _TIME_EPSILON_SECONDS:
            reason = horizon_reason
            break

        point = project_point(slot, flow)
        if isinstance(point, str):
            reason = point
            break
        points.append(point)
    return tuple(points), None if points else reason


def project_soc_during_grid_import(
    config: SystemConfig,
    inputs: PlanInputs,
    trajectory: Trajectory,
    net_battery_discharge_w: float,
    observed_seconds: float,
    *,
    grid_import_w: float,
    grid_setpoint_w: float,
) -> tuple[tuple[tuple[float, float], ...], str | None]:
    """Continue recent terminal discharge over a short comparable import window.

    Points carry epoch seconds since the observation can end within a slot.
    No observed AC power becomes delivery or battery charge in this scenario.
    """
    support = config.support
    limit_w = support.psu48_max_power_w
    if not (
        support.adaptive_voltage_gate
        and support.coordinated
        and support.native48_base_w > 0
        and isfinite(net_battery_discharge_w)
        and net_battery_discharge_w >= 0
        and isfinite(observed_seconds)
        and observed_seconds >= MIN_OBSERVED_SECONDS
        and isfinite(grid_import_w)
        and isfinite(grid_setpoint_w)
        and grid_import_w > max(0.0, grid_setpoint_w) + GRID_IMPORT_MARGIN_W
    ):
        return (), "projection_unsupported"

    battery = config.battery
    floor_wh = battery.energy_wh(battery.soc_min_percent)
    ceiling_wh = battery.energy_wh(battery.soc_max_percent)
    projected_wh = battery.energy_wh(inputs.start_soc_percent)
    start = inputs.now.timestamp()
    until = start + min(observed_seconds, _MAX_GRID_IMPORT_PROJECTION_SECONDS)
    required_import_w = max(0.0, grid_setpoint_w) + GRID_IMPORT_MARGIN_W
    upper_factor = (
        config.reserve.upper_pv_factor
        if config.reserve.enabled
        else config.control.upper_pv_reserve
    )
    _, upper, _ = effective_uncertainty(
        inputs, config.control.predrain_pv_confidence, upper_factor
    )
    points = []
    reason = "no_matching_plan_window"
    for index, (slot, flow, end) in enumerate(_matching_plan_slots(inputs, trajectory)):
        # Require a deficit even under optimistic PV. Counting scheduled
        # surplus loads or assumed grid delivery to the PSUs could create a
        # matching window based on the very supply that is under observation.
        # Keep any published P90 as a conservative check even where the
        # planner falls back to a scalar, with the planner's physical cap.
        pv_wh = max(
            slot.pv_wh,
            min(
                config.pv.peak_power_w * slot.duration,
                max(slot.pv_wh * upper[index], slot.pv_p90_wh or 0.0),
            ),
        )
        if slot.ac_wh - pv_wh <= required_import_w * slot.duration:
            reason = "net_grid_plan_margin_not_met"
            break
        native_wh, _ = dc_loads(config, slot)
        observed_wh = net_battery_discharge_w * slot.duration
        if (
            abs(flow.battery_charge_wh - flow.psu48_battery_charge_wh)
            > ENERGY_EPSILON_WH
            or flow.inverter_output_wh > ENERGY_EPSILON_WH
            or flow.grid_export_wh > ENERGY_EPSILON_WH
            or abs(native_wh - support.native48_base_w * slot.duration)
            > ENERGY_EPSILON_WH
            or (
                limit_w is not None
                and native_wh - observed_wh
                > limit_w * slot.duration + ENERGY_EPSILON_WH
            )
        ):
            break
        if observed_wh > native_wh + ENERGY_EPSILON_WH:
            reason = "net_discharge_exceeds_native_load"
            break

        end = min(end, until)
        hours = (end - start) / 3600.0
        next_wh = projected_wh - net_battery_discharge_w * hours / battery.eta_discharge
        if (
            next_wh < floor_wh - ENERGY_EPSILON_WH
            or projected_wh > ceiling_wh + ENERGY_EPSILON_WH
        ):
            reason = "net_storage_limit"
            break
        projected_wh = next_wh
        points.append(
            (end, battery.soc_percent(max(floor_wh, min(ceiling_wh, projected_wh))))
        )
        if end >= until - _TIME_EPSILON_SECONDS:
            break
        start = end
    return tuple(points), None if points else reason


def _matching_plan_slots(
    inputs: PlanInputs, trajectory: Trajectory
) -> Iterator[tuple[HourSlot, HourFlows, float]]:
    """Find the contiguous initial slots with the observed isolated topology."""
    at = inputs.now.timestamp()
    for slot, flow in zip(inputs.slots, trajectory.flows, strict=True):
        start = slot.start.timestamp()
        end = start + slot.duration * 3600
        if abs(start - at) > _TIME_EPSILON_SECONDS or not flow.switching_schedule:
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
        yield slot, flow, end
        at = end
