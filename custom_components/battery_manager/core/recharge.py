"""Bounded additional discharge, certified against fixed solar day anchors.

Legacy short recharge windows remain authoritative for the ordinary market pass.
Only this separate proof may borrow over a short window; its DC certificates
remain mandatory during subsequent AC and optional-load placement.
"""

import json
from collections.abc import Callable, Sequence
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from .dc_service import preserves_dc_service
from .joint_dispatch import MAX_JOINT_VALIDATIONS, ValidationBudget, proof_failure
from .model import (
    ConsumptionUncertaintySnapshot,
    HourFlows,
    RechargeCertificate,
    RechargeSummary,
    SystemConfig,
    Trajectory,
)
from .planning_control import check_cancelled
from .reserve_energy import ENERGY_EPSILON_WH
from .reserve_sources import SourceStep, grid_dc, phase_locks, pv_recharge, source_state

MAX_RECHARGE_PROPOSALS = 4096
BLOCK_HOURS = 0.5


def step_end(step: SourceStep) -> datetime:
    start = step.slot.start
    if start.tzinfo is None:
        return start + timedelta(hours=step.slot.duration)
    return (start.astimezone(UTC) + timedelta(hours=step.slot.duration)).astimezone(
        start.tzinfo
    )


def fixed_anchors(steps: Sequence[SourceStep]) -> tuple[int, ...]:
    """Last net SOLAR charging endpoint of each reference local date."""
    days: dict[object, int] = {}
    for index, step in enumerate(steps):
        if pv_recharge(step.flow):
            days[step.slot.start.date()] = index
    return tuple(days.values())


def uncertainty_wh(
    snapshot: ConsumptionUncertaintySnapshot,
    start: datetime,
    end: datetime,
    config: SystemConfig,
) -> float | None:
    """Integrate once over P50; unknown AC OR DC coverage fails closed."""
    cursor, finish = start.timestamp(), end.timestamp()
    result = 0.0
    for part in snapshot.intervals:
        left, right = (
            max(cursor, part.start.timestamp()),
            min(finish, part.end.timestamp()),
        )
        if right <= left:
            continue
        if left > cursor + 1e-6:
            return None
        share = (right - left) / (part.end.timestamp() - part.start.timestamp())
        result += share * (
            part.ac_p80_minus_p50_wh
            / (config.battery.eta_discharge * config.inverter.eta)
            + part.dc_p80_minus_p50_wh
            / (config.battery.eta_discharge * config.support.dcdc_eta)
        )
        cursor = right
        if cursor >= finish - 1e-6:
            return result
    return result if cursor >= finish - 1e-6 else None


def certified_windows_failure(
    config: SystemConfig,
    before: Sequence[SourceStep],
    after: Sequence[SourceStep],
    certificates: Sequence[RechargeCertificate],
) -> str | None:
    """Only named physical debits may exceed the original short-window ledger."""
    from .reserve_sources import net_draw, source_windows

    debit_at: dict[float, float] = {}
    for certificate in certificates:
        for at, amount in certificate.step_debits:
            debit_at[at.timestamp()] = debit_at.get(at.timestamp(), 0.0) + amount
    for window in source_windows(before):
        allowance = sum(
            debit_at.get(before[i].slot.start.timestamp(), 0.0) for i in window
        )
        if (
            sum(net_draw(after[i].flow) - net_draw(before[i].flow) for i in window)
            > allowance + ENERGY_EPSILON_WH
        ):
            return "uncertified_window_draw"
        endpoint = step_end(before[window.stop - 1]).timestamp()
        outstanding = sum(
            amount
            for certificate in certificates
            if certificate.anchor.timestamp() > endpoint
            for at, amount in certificate.step_debits
            if at.timestamp() < endpoint
        )
        if config.battery.energy_wh(after[window.stop - 1].flow.soc_end_percent) < (
            config.battery.energy_wh(before[window.stop - 1].flow.soc_end_percent)
            - outstanding
            - ENERGY_EPSILON_WH
        ):
            return "uncertified_window_energy"
    return None


def anchor_failure(
    config: SystemConfig,
    reference: Trajectory,
    trial: Trajectory,
    before: Sequence[SourceStep],
    after: Sequence[SourceStep],
    start: int,
    anchor: int,
    uncertainty: ConsumptionUncertaintySnapshot,
    certificates: Sequence[RechargeCertificate] = (),
) -> str | None:
    """Sequential nominal and stressed simulations use this identical contract."""
    uncertainty_reserve = uncertainty_wh(
        uncertainty, before[start].slot.start, step_end(before[anchor]), config
    )
    if uncertainty_reserve is None:
        return "consumption_coverage"
    physical_failure = proof_failure(
        config, reference, trial, before, after, check_windows=False
    )
    if physical_failure is not None:
        return physical_failure
    if (
        after[anchor].flow.soc_end_percent
        < before[anchor].flow.soc_end_percent - ENERGY_EPSILON_WH
    ):
        return "anchor_energy"
    if trial.end_soc_percent < reference.end_soc_percent - ENERGY_EPSILON_WH:
        return "end_energy"
    window_failure = certified_windows_failure(config, before, after, certificates)
    if window_failure is not None:
        return window_failure
    for i, (old, new) in enumerate(zip(before, after, strict=True)):
        flow = new.flow
        if (
            flow.soc_end_percent
            < min(config.battery.soc_min_percent, old.flow.soc_end_percent)
            - ENERGY_EPSILON_WH
        ):
            return "soc_floor"
        if start <= i <= anchor:
            # P50 has not physically consumed realised forecast error. Keep
            # the start-to-anchor allowance before AND after every step; an
            # elapsed interval cannot lose its reserve without booking its error.
            reserve = uncertainty_reserve
            # Both margins protect the same forecast error: preserve the larger,
            # never lower the existing learned buffer or debit P80 twice.
            floor = config.battery.energy_wh(config.battery.soc_min_percent) + max(
                reserve, config.battery.energy_wh(config.control.soc_buffer_percent)
            )
            if (
                config.battery.energy_wh(flow.soc_start_percent)
                < min(floor, config.battery.energy_wh(old.flow.soc_start_percent))
                - ENERGY_EPSILON_WH
            ):
                return "consumption_reserve"
            if (
                config.battery.energy_wh(flow.soc_end_percent)
                < min(floor, config.battery.energy_wh(old.flow.soc_end_percent))
                - ENERGY_EPSILON_WH
            ):
                return "consumption_reserve"
    return None


def preserves_certificates(
    steps: Sequence[SourceStep], certificates: Sequence[RechargeCertificate]
) -> bool:
    for certificate in certificates:
        for step in steps:
            if (
                certificate.start.timestamp()
                <= step.slot.start.timestamp()
                < certificate.end.timestamp()
            ):
                if certificate.kind == "dc24" and (
                    step.flow.support_dc24 or step.flow.support_dc48
                ):
                    return False
                if certificate.kind == "ac" and not step.flow.inverter_limit_w:
                    return False
    return True


# The callback owns the shared full-validation counter, including both references.
Evaluate = Callable[
    [tuple[HourFlows, ...], bool], tuple[Trajectory, tuple[SourceStep, ...]]
]


def certify(
    config: SystemConfig,
    reference: Trajectory,
    steps: tuple[SourceStep, ...],
    stress_reference: Trajectory,
    stress_steps: tuple[SourceStep, ...],
    uncertainty: ConsumptionUncertaintySnapshot,
    evaluate: Evaluate,
    budget: ValidationBudget,
    stress_required: bool,
) -> tuple[Trajectory, tuple[SourceStep, ...], RechargeSummary]:
    from .replay import encode

    # Persistable identity of the complete physical proof ledger, independent
    # of Python object identities and optional appliance scheduling metadata.
    physical = tuple(
        getattr(config, name)
        for name in (
            "battery",
            "charger",
            "inverter",
            "support",
            "pv",
            "control",
            "reserve",
        )
    )
    encoded = encode(
        (
            physical,
            tuple(step.slot for step in steps),
            reference,
            stress_reference,
            uncertainty,
        )
    )
    reference_id = sha256(
        json.dumps(
            encoded, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    ).hexdigest()
    anchors = fixed_anchors(steps)
    best, best_steps = reference, steps
    best_stressed = stress_reference
    certificates: list[RechargeCertificate] = []
    failures: set[str] = set()
    proposals = 0
    limited = False
    cost = 2 if stress_required else 1
    # Every DC block receives a turn before any new AC permission is considered.
    for kind in ("dc24", "ac"):
        if limited:
            break
        for start in range(len(steps)):
            check_cancelled()
            block: list[int] = []
            duration = 0.0
            for i in range(start, len(steps)):
                if (
                    i > start
                    and abs(
                        steps[i].slot.start.timestamp()
                        - step_end(steps[i - 1]).timestamp()
                    )
                    > 1e-6
                ):
                    break
                if kind == "dc24" and not any(source_state(best_steps[i].flow)):
                    break
                block.append(i)
                duration += steps[i].slot.duration
                # PSU source changes keep their existing half-hour blocks.
                # An inverter permission is one complete physical control
                # quantum, just like the established binary inverter planner.
                if kind == "ac" or duration >= BLOCK_HOURS - 1e-9:
                    break
            if not block:
                continue
            previous_off = (
                start > 0
                and not any(source_state(best_steps[start - 1].flow))
                and abs(
                    step_end(steps[start - 1]).timestamp()
                    - steps[start].slot.start.timestamp()
                )
                < 1e-6
            )
            variants = (
                tuple(tuple(block[:length]) for length in range(1, len(block) + 1))
                if (kind == "dc24" and previous_off)
                else (tuple(block),)
            )
            for variant in variants:
                block = list(variant)
                duration = sum(steps[i].slot.duration for i in block)
                if kind == "dc24" and duration < BLOCK_HOURS - 1e-9:
                    # Extend an already running OFF/DCDC phase at its actual edge.
                    # The complete sequential phase proof below still enforces the
                    # same dwell; a short new fragment is not a short full phase.
                    neighbours = tuple(
                        i
                        for i in (start - 1, block[-1] + 1)
                        if 0 <= i < len(steps)
                        and not any(source_state(best_steps[i].flow))
                        and (
                            abs(
                                step_end(steps[i]).timestamp()
                                - steps[start].slot.start.timestamp()
                            )
                            < 1e-6
                            if i < start
                            else abs(
                                step_end(steps[block[-1]]).timestamp()
                                - steps[i].slot.start.timestamp()
                            )
                            < 1e-6
                        )
                    )
                    if not neighbours:
                        continue
                if kind == "dc24":
                    if not any(any(source_state(best_steps[i].flow)) for i in block):
                        continue
                    if (
                        config.support.dc24_forced_on
                        or config.support.dc48_forced_on
                        or any(
                            any(
                                lock and chosen
                                for lock, chosen in zip(
                                    phase_locks(config, steps[i].slot),
                                    source_state(best_steps[i].flow),
                                    strict=True,
                                )
                            )
                            for i in block
                        )
                    ):
                        continue
                elif any(
                    any(source_state(best_steps[i].flow))
                    or best_steps[i].flow.inverter_limit_w
                    or steps[i].slot.ac_wh <= steps[i].slot.pv_wh
                    for i in block
                ):
                    continue
                later = [
                    anchor
                    for anchor in anchors
                    if anchor >= block[-1]
                    and step_end(steps[anchor]).timestamp()
                    > step_end(steps[block[-1]]).timestamp()
                ]
                if not later:
                    continue
                if (
                    proposals >= MAX_RECHARGE_PROPOSALS
                    or budget.used + cost > MAX_JOINT_VALIDATIONS
                ):
                    limited = True
                    failures.add("search_limit")
                    break
                proposals += 1
                proposed = [step.flow for step in best_steps]
                for i in block:
                    proposed[i] = replace(
                        proposed[i],
                        support_dc24=False,
                        support_dc48=False
                        if kind == "dc24"
                        else proposed[i].support_dc48,
                        inverter_limit_w=config.inverter.max_power_w
                        if kind == "ac"
                        else proposed[i].inverter_limit_w,
                    )
                trial, trial_steps = evaluate(tuple(proposed), False)
                if kind == "dc24" and (
                    any(any(source_state(trial_steps[i].flow)) for i in block)
                    or sum(trial_steps[i].flow.dcdc_input_wh for i in block)
                    <= ENERGY_EPSILON_WH
                ):
                    failures.add("dc24_not_dcdc")
                    continue
                if kind == "ac" and any(
                    not trial_steps[i].flow.inverter_limit_w for i in block
                ):
                    failures.add("ac_not_served")
                    continue
                if not preserves_certificates(trial_steps, certificates):
                    failures.add("certificate_displaced")
                    continue
                stressed, stressed_steps = (
                    evaluate(tuple(proposed), True)
                    if stress_required
                    else (trial, trial_steps)
                )
                if kind == "dc24" and any(
                    any(source_state(stressed_steps[i].flow)) for i in block
                ):
                    failures.add("dc24_stress_protection")
                    continue
                if kind == "ac" and (
                    any(
                        grid_dc(config, new) > grid_dc(config, old) + ENERGY_EPSILON_WH
                        for old, new in zip(best.flows, trial.flows, strict=True)
                    )
                    or any(
                        grid_dc(config, new) > grid_dc(config, old) + ENERGY_EPSILON_WH
                        for old, new in zip(
                            best_stressed.flows, stressed.flows, strict=True
                        )
                    )
                ):
                    failures.add("dc_priority")
                    continue
                # Anchors never move in response to a candidate. Earliest passing
                # anchor must pass BOTH forecasts, rather than separate deadlines.
                accepted_anchor = None
                candidate_debits = tuple(
                    (
                        steps[i].slot.start,
                        max(
                            0.0,
                            trial_steps[i].flow.battery_discharge_wh
                            - trial_steps[i].flow.battery_charge_wh
                            - best_steps[i].flow.battery_discharge_wh
                            + best_steps[i].flow.battery_charge_wh,
                        ),
                    )
                    for i in block
                )
                selected_certificate = None
                for anchor in later:
                    candidate = RechargeCertificate(
                        steps[start].slot.start,
                        step_end(steps[block[-1]]),
                        step_end(steps[anchor]),
                        kind,
                        sum(amount for _, amount in candidate_debits),
                        candidate_debits,
                        reference_id,
                    )
                    combined = (*certificates, candidate)
                    failure = anchor_failure(
                        config,
                        reference,
                        trial,
                        steps,
                        trial_steps,
                        start,
                        anchor,
                        uncertainty,
                        combined,
                    )
                    if failure is None:
                        failure = anchor_failure(
                            config,
                            stress_reference,
                            stressed,
                            stress_steps,
                            stressed_steps,
                            start,
                            anchor,
                            uncertainty,
                            combined,
                        )
                    if failure is None:
                        for old in certificates:
                            old_start = next(
                                i
                                for i, step in enumerate(steps)
                                if step.slot.start.timestamp() == old.start.timestamp()
                            )
                            old_anchor = next(
                                i
                                for i, step in enumerate(steps)
                                if step_end(step).timestamp() == old.anchor.timestamp()
                            )
                            failure = anchor_failure(
                                config,
                                reference,
                                trial,
                                steps,
                                trial_steps,
                                old_start,
                                old_anchor,
                                uncertainty,
                                combined,
                            )
                            if failure is None:
                                failure = anchor_failure(
                                    config,
                                    stress_reference,
                                    stressed,
                                    stress_steps,
                                    stressed_steps,
                                    old_start,
                                    old_anchor,
                                    uncertainty,
                                    combined,
                                )
                            if failure is not None:
                                failure = "existing_certificate_" + failure
                                break
                    if failure is None:
                        accepted_anchor = anchor
                        selected_certificate = candidate
                        break
                    failures.add(failure)
                if accepted_anchor is None:
                    continue
                if (
                    trial.total_import_wh >= best.total_import_wh - ENERGY_EPSILON_WH
                    or (
                        kind == "dc24"
                        and selected_certificate is not None
                        and selected_certificate.battery_draw_wh <= ENERGY_EPSILON_WH
                    )
                ):
                    failures.add("no_improvement")
                    continue
                if not preserves_dc_service(trial, baseline=reference, accepted=best):
                    failures.add("accepted_dc_service")
                    continue
                assert selected_certificate is not None
                certificates.append(selected_certificate)
                best, best_steps = trial, trial_steps
                best_stressed = stressed
            if limited:
                break
    summary = RechargeSummary(
        "search_limit" if limited else "accepted" if certificates else "no_release",
        tuple(step_end(steps[i]) for i in anchors),
        tuple(certificates),
        budget.used,
        tuple(sorted(failures)),
        reference_id,
    )
    return best, best_steps, summary
