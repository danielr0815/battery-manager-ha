"""Bounded observations of joint candidates, independent from search decisions."""

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, timedelta

from .model import HourFlows, JointTargetDiagnostic
from .reserve_energy import ENERGY_EPSILON_WH
from .reserve_sources import SourceStep

MAX_TARGET_DIAGNOSTICS = 48


class JointDiagnostics:
    """Keep proved partial receivers ahead of unsearched whole-family labels."""

    def __init__(self, steps: Sequence[SourceStep], prices: Sequence[float | None]):
        self.steps = steps
        self.prices = prices
        self.rows: dict[tuple[tuple[int, ...], str], JointTargetDiagnostic] = {}
        self.truncated = False
        groups: list[tuple[int, ...]] = []
        for i, price in enumerate(prices):
            if price is None:
                continue
            if (
                groups
                and groups[-1][-1] + 1 == i
                and prices[groups[-1][0]] == price
                and abs(
                    steps[i].slot.start.timestamp()
                    - steps[i - 1].slot.start.timestamp()
                    - steps[i - 1].slot.duration * 3600
                )
                < 1e-6
            ):
                groups[-1] += (i,)
            else:
                groups.append((i,))
        groups.sort(key=lambda g: (-(prices[g[0]] or 0.0), g[0]))
        # Leave room for exact partial blocks; seed labels have no outcomes.
        self.truncated = len(groups) > 8
        for group in groups[:8]:
            for variant in ("dc_only", "ac_dc", "paired_dc"):
                self.rows[(group, variant)] = self._row(group, variant)

    def _row(self, target: tuple[int, ...], variant: str) -> JointTargetDiagnostic:
        price = self.prices[target[0]]
        assert price is not None
        return JointTargetDiagnostic(
            self.steps[target[0]].slot.start,
            (
                self.steps[target[-1]].slot.start.astimezone(UTC)
                + timedelta(hours=self.steps[target[-1]].slot.duration)
            ).astimezone(self.steps[target[-1]].slot.start.tzinfo),
            price,
            variant,
        )

    @staticmethod
    def _priority(row: JointTargetDiagnostic) -> tuple[int, float, float, str]:
        return (
            2 if row.proved_improvements else 1 if row.attempted else 0,
            row.eur_per_mwh,
            -row.start.timestamp(),
            row.variant,
        )

    def record(
        self, target: tuple[int, ...], variant: str, stage: str, reason: str = ""
    ) -> None:
        key = (target, variant)
        row = self.rows.get(key, self._row(target, variant))
        row = replace(row, attempted=row.attempted + 1, not_searched=False)
        if stage == "proved":
            updated = replace(row, proved_improvements=row.proved_improvements + 1)
        else:
            counts = dict(
                row.local_rejections if stage == "local" else row.proof_rejections
            )
            counts[reason] = counts.get(reason, 0) + 1
            updated = (
                replace(row, local_rejections=tuple(sorted(counts.items())))
                if stage == "local"
                else replace(row, proof_rejections=tuple(sorted(counts.items())))
            )
        if key not in self.rows and len(self.rows) >= MAX_TARGET_DIAGNOSTICS:
            self.truncated = True
            lowest = min(self.rows, key=lambda k: self._priority(self.rows[k]))
            if self._priority(updated) <= self._priority(self.rows[lowest]):
                return
            del self.rows[lowest]
        self.rows[key] = updated

    def paired(
        self, proposal: Sequence[HourFlows], stage: str, reason: str = ""
    ) -> None:
        """Attribute a whole paired proof only to its exact receiver spans."""
        groups: list[tuple[int, ...]] = []
        for i, (step, flow) in enumerate(zip(self.steps, proposal, strict=True)):
            if (
                self.prices[i] is None
                or step.flow.grid_import_wh - flow.grid_import_wh <= ENERGY_EPSILON_WH
            ):
                continue
            if (
                groups
                and groups[-1][-1] + 1 == i
                and self.prices[groups[-1][0]] == self.prices[i]
                and abs(
                    step.slot.start.timestamp()
                    - self.steps[i - 1].slot.start.timestamp()
                    - self.steps[i - 1].slot.duration * 3600
                )
                < 1e-6
            ):
                groups[-1] += (i,)
            else:
                groups.append((i,))
        for group in groups:
            self.record(group, "paired_dc", stage, reason)

    def summary(self) -> tuple[JointTargetDiagnostic, ...]:
        return tuple(sorted(self.rows.values(), key=self._priority, reverse=True))
