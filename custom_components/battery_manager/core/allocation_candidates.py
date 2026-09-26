"""Construct physical load commitments before any economic gate is evaluated."""

from dataclasses import dataclass

from .model import PlanInputs
from .planning_rules import _add_path_overheads, _seamless_spill, _spread_energy


@dataclass(frozen=True)
class AllocationCandidate:
    trial: list[float]
    covered: list[tuple[int, float]]
    commit_h: float
    seamless: bool


@dataclass(frozen=True)
class AllocationContext:
    """Current accepted bookings; candidate construction never mutates them."""

    inputs: PlanInputs
    extra: list[float]
    schedules: dict[str, list[bool]]
    run_h: dict[str, list[float]]
    path_overheads: tuple[tuple[tuple[str, ...], float], ...]

    def candidate(
        self, load_id: str, index: int, commit_h: float, power_w: float
    ) -> AllocationCandidate | None:
        """Trim a seamless raster-edge continuation; reject real double booking."""
        trial, covered = _spread_energy(
            self.extra, self.inputs.slots, index, power_w, commit_h
        )
        seamless = False
        if any(self.schedules[load_id][j] for j, _ in covered):
            trimmed = _seamless_spill(
                covered,
                self.inputs.slots,
                index,
                power_w,
                self.extra,
                self.schedules[load_id],
            )
            if trimmed is None:
                return None
            trial, covered = trimmed
            commit_h = self.inputs.slots[index].duration
            seamless = True
        _add_path_overheads(trial, load_id, covered, self.run_h, self.path_overheads)
        return AllocationCandidate(trial, covered, commit_h, seamless)
