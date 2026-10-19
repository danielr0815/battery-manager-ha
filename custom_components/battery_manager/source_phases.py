"""Confirmed PSU phases shared by rolling plans, diagnostics and restoration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, TypedDict

from homeassistant.util import dt as dt_util

from .const import CONF_DCDC_SWITCH, CONF_SUPPORT_DC24_SWITCH, CONF_SUPPORT_DC48_SWITCH

if TYPE_CHECKING:
    from .coordinator import BatteryManagerCoordinator

# October 18: market replanning reversed confirmed sources after 2:19/2:22 min.
# This binds economic placement, not protection/PV or a device retry timeout.
ECONOMIC_PHASE_SECONDS = 30 * 60
_SOURCES = (("dc24", CONF_SUPPORT_DC24_SWITCH), ("dc48", CONF_SUPPORT_DC48_SWITCH))


class _PhasePayload(TypedDict):
    enabled: bool
    confirmed_at: str
    binding: list[str | None]


class _StorePayload(TypedDict):
    schema_version: int
    sources: dict[str, _PhasePayload]


class _PhaseDiagnostic(TypedDict):
    confirmed_at: str
    enabled: bool
    economic_until: str | None


@dataclass(frozen=True)
class _Phase:
    enabled: bool
    confirmed_at: datetime
    binding: tuple[str | None, str | None]


class SourcePhases:
    def __init__(self, owner: BatteryManagerCoordinator) -> None:
        self.owner = owner
        self._phases: dict[str, _Phase] = {}
        self._pending: dict[str, bool] = {}

    def _binding(self, key: str, conf: str) -> tuple[str | None, str | None]:
        cfg = self.owner.raw_config
        return cfg.get(conf), cfg.get(CONF_DCDC_SWITCH) if key == "dc24" else None

    def requested(self, entity: str, target: bool) -> None:
        if self.owner._entity_tristate(entity) is not target:
            diag = self.owner._coordinated_support_diag
            key = next(
                (
                    key
                    for key, conf in _SOURCES
                    if self.owner.raw_config.get(conf) == entity
                ),
                None,
            )
            # Voltage/PV can release hardware while its economic request stays
            # ON. Do not turn that release into a 30-minute ban on recovery.
            self._pending[entity] = bool(
                key is not None
                and not self.owner._support_manual[key]
                and not diag.get("pv_priority")
                and diag.get("requested", {}).get(key, target) is target
            )
        else:
            self._pending.pop(entity, None)

    def confirmed(self, entity: str, target: bool, now: datetime) -> bool:
        if entity not in self._pending:
            return False
        economic = self._pending.pop(entity)
        for key, conf in _SOURCES:
            if self.owner.raw_config.get(conf) == entity:
                if not economic:
                    self._phases.pop(key, None)
                    self.owner._save_persistent_state()
                    return True
                self._phases[key] = _Phase(
                    target, dt_util.as_utc(now), self._binding(key, conf)
                )
                self.owner._save_persistent_state()
                return True
        return False

    def deadlines(self, now: datetime) -> tuple[float, float]:
        result = []
        for key, conf in _SOURCES:
            phase = self._phases.get(key)
            entity = self.owner.raw_config.get(conf)
            valid = (
                phase is not None
                and entity is not None
                and phase.binding == self._binding(key, conf)
                and not self.owner._support_manual[key]
                and self.owner._entity_tristate(entity) is phase.enabled
                and self.owner._support_state[key] is phase.enabled
            )
            until = (
                phase.confirmed_at + timedelta(seconds=ECONOMIC_PHASE_SECONDS)
                if phase is not None
                else now
            )
            result.append(until.timestamp() if valid and now < until else 0.0)
        return result[0], result[1]

    def export(self) -> _StorePayload:
        return {
            "schema_version": 1,
            "sources": {
                key: {
                    "enabled": phase.enabled,
                    "confirmed_at": phase.confirmed_at.isoformat(),
                    "binding": list(phase.binding),
                }
                for key, phase in self._phases.items()
            },
        }

    def restore(self, value: object, now: datetime) -> None:
        self._phases.clear()
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            return
        sources = value.get("sources")
        if not isinstance(sources, dict):
            return
        for key, conf in _SOURCES:
            source = sources.get(key)
            if not isinstance(source, dict):
                continue
            raw = source.get("confirmed_at")
            at = dt_util.parse_datetime(raw) if isinstance(raw, str) else None
            if (
                at is None
                or at.tzinfo is None
                or not isinstance(source.get("enabled"), bool)
                or source.get("binding") != list(self._binding(key, conf))
                or not 0 <= (now - at).total_seconds() < ECONOMIC_PHASE_SECONDS
            ):
                continue
            self._phases[key] = _Phase(
                source["enabled"], dt_util.as_utc(at), self._binding(key, conf)
            )

    def diagnostic(self, now: datetime) -> dict[str, _PhaseDiagnostic]:
        deadlines = self.deadlines(now)
        return {
            key: {
                "confirmed_at": phase.confirmed_at.isoformat(),
                "enabled": phase.enabled,
                "economic_until": datetime.fromtimestamp(until, dt_util.UTC).isoformat()
                if until
                else None,
            }
            for (key, _), until in zip(_SOURCES, deadlines, strict=True)
            if (phase := self._phases.get(key)) is not None
        }
