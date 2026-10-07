"""Bounded offline reserve alternatives; these results never grant live permission."""

from dataclasses import dataclass
from math import isfinite

from .dc_service import dc_service_regression_wh
from .model import PlanInputs, SystemConfig, Trajectory
from .planning_control import check_cancelled
from .reserve import reserve_planning_scope, simulate_reserve_variant
from .reserve_energy import ENERGY_EPSILON_WH
from .reserve_sources import grid_dc
from .simulation_steps import SUPPORT_STEP_HOURS
from .uncertainty import effective_uncertainty

MAX_COMPARISON_VARIANTS = 15


@dataclass(frozen=True)
class ReserveMetrics:
    grid_import_wh: float
    grid_export_wh: float
    dc_grid_wh: float
    unserved_dc_wh: float
    ac_output_wh: float
    terminal_stored_wh: float
    terminal_recoverable_ac_wh: float
    min_soc_percent: float
    max_soc_percent: float


@dataclass(frozen=True)
class ReserveComparison:
    scenario: str
    ac_margin_wh: float
    reference: ReserveMetrics
    candidate: ReserveMetrics
    dc_service_regression_wh: float
    dc_grid_increase_wh: float
    dc_transfer_quantum_wh: float
    preserves_dc_priority: bool
    import_reduction_wh: float
    terminal_stored_reduction_wh: float
    terminal_ac_reduction_wh: float
    terminal_adjusted_gain_wh: float


def _metrics(config: SystemConfig, trajectory: Trajectory) -> ReserveMetrics:
    stored = config.battery.energy_wh(trajectory.end_soc_percent)
    return ReserveMetrics(
        trajectory.total_import_wh,
        trajectory.total_export_wh,
        sum(grid_dc(config, f) for f in trajectory.flows),
        sum(f.unserved_dc_wh for f in trajectory.flows),
        sum(f.inverter_output_wh for f in trajectory.flows),
        stored,
        stored * config.battery.eta_discharge * config.inverter.eta,
        trajectory.min_soc_percent,
        trajectory.max_soc_percent,
    )


def compare_reserve_alternatives(
    config: SystemConfig,
    inputs: PlanInputs,
    margins_wh: tuple[float, ...] = (0, 250, 500, 750, 1000),
    *,
    extra_ac_wh: tuple[float, ...] | None = None,
    feedin_wh: tuple[float, ...] | None = None,
) -> tuple[ReserveComparison, ...]:
    """Compare at most 15 probes against scenario-matched DC-only trajectories.

    Terminal valuation credits later recoverable AC once, after conversion.
    It is an explicit horizon-end sensitivity, not a promised future price or
    realised saving. Nominal, lower and upper PV use the same fixed inputs and
    issued load/feed-in schedules; no hindsight scheduling or auto-selection.
    """
    if not (
        config.reserve.enabled
        and config.support.configured
        and config.support.coordinated
    ):
        raise ValueError("Comparison requires coordinated active reserve")
    if (
        not margins_wh
        or len(margins_wh) * 3 > MAX_COMPARISON_VARIANTS
        or len(set(margins_wh)) != len(margins_wh)
        or any(not isfinite(value) or value < 0 for value in margins_wh)
    ):
        raise ValueError("Use 1–5 distinct finite nonnegative AC margins")
    n = len(inputs.slots)
    if any(
        series is not None and len(series) != n for series in (extra_ac_wh, feedin_wh)
    ):
        raise ValueError("Comparison schedules must match the complete input horizon")
    lower, upper, _ = effective_uncertainty(
        inputs, config.control.predrain_pv_confidence, config.reserve.upper_pv_factor
    )
    quantum = (
        max((slot.dc_wh / slot.duration for slot in inputs.slots), default=0.0)
        * SUPPORT_STEP_HOURS
        / min(config.support.psu24_eta, config.support.psu48_eta)
    )
    results = []
    with reserve_planning_scope():
        for name, scale in (("nominal", 1.0), ("pessimistic", lower), ("upper", upper)):
            check_cancelled()
            reference = simulate_reserve_variant(
                config, inputs, extra_ac_wh, scale, feedin_wh, allow_ac=False
            )
            reference_metrics = _metrics(config, reference)
            for margin in margins_wh:
                check_cancelled()
                candidate = simulate_reserve_variant(
                    config, inputs, extra_ac_wh, scale, feedin_wh, ac_margin_wh=margin
                )
                candidate_metrics = _metrics(config, candidate)
                regression = dc_service_regression_wh(reference, candidate)
                grid_increase = (
                    candidate_metrics.dc_grid_wh - reference_metrics.dc_grid_wh
                )
                import_reduction = (
                    reference_metrics.grid_import_wh - candidate_metrics.grid_import_wh
                )
                stored_reduction = (
                    reference_metrics.terminal_stored_wh
                    - candidate_metrics.terminal_stored_wh
                )
                ac_reduction = (
                    reference_metrics.terminal_recoverable_ac_wh
                    - candidate_metrics.terminal_recoverable_ac_wh
                )
                results.append(
                    ReserveComparison(
                        name,
                        margin,
                        reference_metrics,
                        candidate_metrics,
                        regression,
                        grid_increase,
                        quantum,
                        regression <= ENERGY_EPSILON_WH
                        and grid_increase <= quantum + ENERGY_EPSILON_WH,
                        import_reduction,
                        stored_reduction,
                        ac_reduction,
                        import_reduction - ac_reduction,
                    )
                )
    return tuple(results)
