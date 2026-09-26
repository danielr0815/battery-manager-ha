"""Runtime storage contract: serialize confirmed state and open obligations."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.util import dt as dt_util

from .const import CONF_INVERTER_LIMIT_ENTITY

if TYPE_CHECKING:
    from .coordinator import BatteryManagerCoordinator


def persistent_payload(self: BatteryManagerCoordinator) -> dict[str, Any]:
    return {
        "load_soc": self._load_soc_cache,
        "plug_owned": self._load_plug_owned,
        "last_load_switch": {
            k: v.isoformat() for k, v in self._last_load_switch.items()
        },
        "load_run_deadline": {
            k: v.isoformat() for k, v in self._load_run_deadline.items()
        },
        "load_bm_enabled": dict(self._load_bm_enabled),
        "cascade_state": self.cascade_manager.persistent_state_snapshot(),
        # Only the accumulated total is persisted; the in-progress tick cursor
        # is deliberately NOT — restoring it would credit the whole restart
        # gap (up to the tick cap) as runtime the device may never have run.
        # After a restart the first tick just re-arms the cursor (losing at
        # most the last sub-cycle partial, which is bounded and unobservable).
        "load_runtime_seconds": dict(self._load_runtime_seconds),
        # F-PLANNER-HONESTY R3 / F-ROBUST-POWER: the learned planning
        # power (write-through of the robust windowed estimate) survives
        # restarts — unlike the live sample buffer (deliberately
        # volatile, see async_load_persistent_state); spike-proof by the
        # estimator's median + warm-up construction.
        "load_learned_power": dict(self._load_learned_power_w),
        # The samples are intentionally volatile. Only the actor-ownership
        # marker survives so setup can force an interrupted grid-powered
        # probe OFF before the normal planner starts.
        "load_power_calibration_active": (
            self._load_power_calibration_id or self._load_power_calibration_restored
        ),
        # F-SUBHOUR H1: persist the appliance run start so a restart mid-run
        # does not re-latch at `now` and re-inject the full run energy.
        "appliance_energy_samples": self._appliance_learning.samples,
        "appliance_program_samples": {
            key: {
                name: [list(sample) for sample in samples]
                for name, samples in programs.items()
            }
            for key, programs in self._appliance_learning.program_samples.items()
        },
        "appliance_started": {
            k: v.isoformat() for k, v in self._appliance_started.items()
        },
        "support_manual": dict(self._support_manual),
        "support_control_mode": "coordinated"
        if self.raw_config.get(CONF_INVERTER_LIMIT_ENTITY)
        else "legacy",
        "support_migration": dict(self._support_migration),
        "support_state": dict(self._support_state),
        "dc48_ctrl_caused_off": self._dc48_ctrl_caused_off,
        # F-L7: the latched power warning survives reloads/restarts so an
        # options save (which reloads the coordinator) does not silently
        # drop a raised warning. Only the bool is persisted; the dwell
        # timer re-arms after a restart (a not-yet-tripped deviation is
        # deliberately not credited across downtime).
        "load_power_warning": dict(self._load_power_warning),
        # V6 (F-TANK): the learned tank samples (median = learned full-tank
        # runtime) and the pending tank-full runtime capture survive
        # restarts, like the power-warning latch. The per-cycle notified set
        # is deliberately volatile (a rare duplicate push after a restart is
        # acceptable; persisting it would over-suppress after a real reset).
        "load_tank_samples": {k: list(v) for k, v in self._load_tank_samples.items()},
        "load_tank_full_min": dict(self._load_tank_full_min),
        # F-EXECUTOR-GUARDS G2 + F4 (7-day live audit 2026-08-02): both
        # watchdogs survive a coordinator reload — a reload that dropped
        # the F4 latch re-opened the exact hole it guarded against (the
        # recommendation duty-cycled a demonstrably unplugged Fossibot B2
        # for 110 min, ~225 Wh misbooked; the precedence freeze ran
        # 174.7 h, so re-building the 6 h evidence from zero is not a
        # shrug). Persisted per load is the reference value(s) the
        # release compares against PLUS the accumulated evidence in
        # SECONDS — not the wall-clock window start: downtime is NOT
        # freeze evidence (while HA is down nothing is observed), so the
        # restored clock resumes from the saved accumulation and the
        # restart gap is never credited (the same rule as the runtime
        # tick cursor). A clean reload flushes on unload
        # (async_flush_persistent_state); an unclean power loss forfeits
        # at most the delayed-save window.
        "load_soc_stale_guard": {
            k: {
                "soc": soc,
                "elapsed_s": max(0.0, (dt_util.utcnow() - since).total_seconds()),
                "latched": k in self._load_soc_stale,
            }
            for k, (soc, since) in self._load_soc_frozen.items()
        },
        "load_freeze_guard": {
            k: {
                "soc": ref[0],
                "power": ref[1],
                "elapsed_s": max(0.0, (dt_util.utcnow() - ref[2]).total_seconds()),
                "rec_seen": ref[3],
                "latched": k in self._load_freeze_stale,
            }
            for k, ref in self._load_freeze_ref.items()
        },
        # D-A8 stage 2: the data-loss clock and the shed latch survive
        # restarts (see async_load_persistent_state for the rationale).
        "stale_since": (
            self._data_stale_since.isoformat()
            if self._data_stale_since is not None
            else None
        ),
        "stale_shed_active": self._stale_shed_active,
        "stale_shed_pending": sorted(self._stale_shed_pending),
        "load_actor_requests": {
            entity: {
                "desired": request.desired,
                "requested_at": request.requested_at.isoformat(),
                "state": request.state,
            }
            for entity, request in self._load_actor_requests.items()
            if request.state != "confirmed"
        },
        # F-FEEDIN: see async_load_persistent_state for the rationale.
        "feedin_switch_on": self._feedin_switch_on,
        "feedin_manual_until": (
            self._feedin_manual_until.isoformat()
            if self._feedin_manual_until is not None
            else None
        ),
        "feedin_last_written_w": self._feedin_last_written_w,
        "feedin_owned_entity": self._feedin_owned_entity,
        # F-REALIZED-SURPLUS: measured day counters + monotone true-export
        # total + last counter readings (decision 8; rationale in
        # async_load_persistent_state).
        "reserve": self._reserve_runtime.export(),
        "operation_history": self.operation_recorder.export(),
        "realized": {
            "date": self._realized["date"],
            "lost_wh": self._realized["lost_wh"],
            "prevented_wh": self._realized["prevented_wh"],
            "feedin_wh": self._realized["feedin_wh"],
            "true_export_total_wh": self._realized["true_export_total_wh"],
            "external_debt_wh": self._realized["external_debt_wh"],
            "last_readings": dict(self._realized["last_readings"]),
        },
    }
