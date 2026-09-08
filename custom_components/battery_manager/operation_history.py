"""Bounded event journal and interval-aligned daily observations, without HA."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import zlib
from collections import Counter
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from .core.replay import decode, recording, replay

SCHEMA = 1
MAX_EVENTS = 50_000
MAX_BYTES = 32 * 1024 * 1024
RETENTION_DAYS = 7
REPORT_DAYS = 30
MAX_SAMPLE_S = 300
MAX_PLAN_BYTES = 8 * 1024 * 1024


def unpack_plan(blob: str) -> dict:
    """Decode bounded JSON; archive inputs cannot execute code or inflate forever."""
    decoder = zlib.decompressobj()
    data = decoder.decompress(base64.b64decode(blob, validate=True), MAX_PLAN_BYTES + 1)
    if len(data) > MAX_PLAN_BYTES or not decoder.eof:
        raise ValueError("Invalid or oversized plan recording")
    return json.loads(data)


def measured_value(sample: dict | None, at: datetime, *, power: bool = True):
    if not sample:
        return None
    try:
        age = (at - datetime.fromisoformat(sample["reported_at"])).total_seconds()
        value = float(sample["state"])
    except ValueError, TypeError, KeyError:
        return None
    if not math.isfinite(value) or not 0 <= age <= MAX_SAMPLE_S:
        return None
    if not power:
        return value if 0 <= value <= 100 else None
    factor = {"W": 1, "kW": 1000}.get(sample.get("unit"))
    return value * factor if factor is not None and value >= 0 else None


def expected_interval(
    record: dict | None, start: datetime, end: datetime, decoded=None
) -> dict:
    """Integrate the actual booked intervals, including late cascade sources."""
    if record is None:
        return {}
    inputs, result, config = decoded or tuple(
        decode(record[key]) for key in ("inputs", "result", "config")
    )
    expected: dict[str, float] = {}
    load_map = {load.load_id: load for load in config.loads}
    state_map = {state.load_id: state for state in inputs.load_states}
    cascade_loads = {
        member.load_id for cascade in config.cascades for member in cascade.members
    }
    cascade_loads.update(cascade.terminal_load_id for cascade in config.cascades)

    def add(key, value):
        expected[key] = expected.get(key, 0.0) + value

    def overlap(a, b):
        return max(0.0, (min(end, b) - max(start, a)).total_seconds() / 3600)

    for i, slot in enumerate(inputs.slots):
        hours = overlap(slot.start, slot.start + timedelta(hours=slot.duration))
        if not hours:
            continue
        add("coverage_h", hours)
        flow = result.trajectory.flows[i]
        for name, value in (
            ("pv", slot.pv_wh),
            ("ac", slot.ac_wh),
            ("grid_import", flow.grid_import_wh),
            ("grid_export", flow.grid_export_wh),
        ):
            add(name, value * hours / slot.duration)
        for plan in result.load_plans:
            if plan.load_id in cascade_loads:
                continue
            load = load_map[plan.load_id]
            state = state_map.get(plan.load_id)
            total_h = sum(plan.run_hours)
            power = (
                plan.planned_energy_wh / total_h
                if total_h
                else state.planning_power_w(load)
                if state
                else load.nominal_power_w
            )
            run_h = (
                plan.run_hours[i]
                if plan.run_hours
                else slot.duration * plan.schedule[i]
            )
            used_h = overlap(slot.start, slot.start + timedelta(hours=run_h))
            key = f"load:{plan.load_id}"
            add(key, used_h * power)
            add(key + ":run_h", used_h)
            # Power used for runtime attribution even when no run was booked.
            add(key + ":power_h", power * hours)
        for cascade, plan in zip(config.cascades, result.cascade_plans, strict=True):
            cascade_flow = plan.flows[i]
            terminal = f"load:{cascade.terminal_load_id}"
            terminal_load = load_map[cascade.terminal_load_id]
            terminal_state = state_map.get(cascade.terminal_load_id)
            nominal = (
                terminal_state.planning_power_w(terminal_load)
                if terminal_state
                else terminal_load.nominal_power_w
            )
            add(terminal + ":power_h", nominal * hours)
            add(terminal, 0)
            add(terminal + ":run_h", 0)
            for segment in cascade_flow.segments:
                a = slot.start + timedelta(hours=segment.start_offset_h)
                used = overlap(a, a + timedelta(hours=segment.run_hours))
                if segment.terminal_energy_wh > 0 and segment.run_hours > 0:
                    add(terminal, segment.terminal_energy_wh * used / segment.run_hours)
                    add(terminal + ":run_h", used)
            charge_hours = {
                flow.load_id: flow.charge_hours
                if flow.charge_hours is not None
                else slot.duration
                if flow.own_charge_input_wh > 0
                else 0
                for flow in cascade_flow.member_flows
            }
            own_charge = {}
            for member in cascade_flow.member_flows:
                key = f"load:{member.load_id}"
                run_h = charge_hours[member.load_id]
                power = (
                    member.own_charge_input_wh / run_h
                    if run_h
                    else load_map[member.load_id].nominal_power_w
                )
                used = overlap(slot.start, slot.start + timedelta(hours=run_h))
                own_charge[member.load_id] = power * used
                add(key, power * used)
                add(key + ":run_h", used)
                add(key + ":power_h", power * hours)
            # Core member.input_wh denotes charging, not a physical AC meter.
            # Reconstruct each boundary from all downstream charging, terminal
            # delivery and the output overhead of the actually supplied path.
            ids = [member.load_id for member in cascade.members]
            root_h = max(
                (
                    segment.run_hours
                    for segment in cascade_flow.segments
                    if segment.root_input_on
                ),
                default=0,
            )
            for index, member in enumerate(cascade.members):
                incoming = sum(own_charge[lid] for lid in ids[index:])
                for output_index in range(index, len(ids)):
                    output_h = max(
                        root_h,
                        *(charge_hours[lid] for lid in ids[output_index + 1 :]),
                        0,
                    )
                    used = overlap(slot.start, slot.start + timedelta(hours=output_h))
                    incoming += cascade.members[output_index].output_overhead_w * used
                for segment in cascade_flow.segments:
                    if segment.run_hours <= 0:
                        continue
                    a = slot.start + timedelta(hours=segment.start_offset_h)
                    fraction = (
                        overlap(a, a + timedelta(hours=segment.run_hours))
                        / segment.run_hours
                    )
                    if segment.root_input_on:
                        incoming += segment.terminal_energy_wh * fraction
                    elif (
                        segment.source_load_id in ids
                        and ids.index(segment.source_load_id) < index
                    ):
                        overhead = sum(
                            item.output_overhead_w for item in cascade.members[index:]
                        )
                        service_h = (
                            segment.terminal_energy_wh / nominal if nominal > 0 else 0
                        )
                        incoming += (
                            segment.terminal_energy_wh + overhead * service_h
                        ) * fraction
                add(f"cascade_input:{member.load_id}", incoming)
    return expected


class OperationHistory:
    """Event-sourced daily accounting, with bounded detailed evidence."""

    def __init__(self, timezone: str = "UTC"):
        self.timezone = timezone
        self.events: list[dict] = []
        self.plans: dict[str, str] = {}
        self.daily: dict[str, dict] = {}
        self.sequence = 0
        self.dropped = 0
        self._previous = None
        self._plan_id = None
        self._record = None
        self._decoded = None
        self._bytes = 0
        self._plan_refs = Counter()

    def _day(self, at):
        return at.astimezone(ZoneInfo(self.timezone)).date().isoformat()

    def _report(self, day):
        return self.daily.setdefault(
            day,
            {
                "day": day,
                "metrics": {},
                "loads": {},
                "observed_hours": 0.0,
                "gap_hours": 0.0,
                "switch_requests": 0,
                "state_changes": 0,
                "service_failures": 0,
            },
        )

    def event(self, at: datetime, kind: str, data: dict) -> int:
        self.sequence += 1
        row = {
            "sequence": self.sequence,
            "at": at.isoformat(),
            "kind": kind,
            "plan_id": self._plan_id,
            "data": deepcopy(data),
        }
        self.events.append(row)
        self._plan_refs[self._plan_id] += 1
        self._bytes += len(json.dumps(row, separators=(",", ":"))) + 1
        day = self._report(self._day(at))
        if kind == "command_requested":
            day["switch_requests"] += 1
        if kind == "command_result" and data.get("success") is False:
            day["service_failures"] += 1
        if (
            kind == "state_changed"
            and data.get("old") in ("on", "off")
            and data.get("new") in ("on", "off")
            and data["old"] != data["new"]
        ):
            day["state_changes"] += 1
        self._trim(at)
        return self.sequence

    def _trim(self, now):
        cutoff = now - timedelta(days=RETENTION_DAYS)
        while self.events and (
            len(self.events) > MAX_EVENTS
            or self._bytes > MAX_BYTES
            or datetime.fromisoformat(self.events[0]["at"]) < cutoff
        ):
            old = self.events.pop(0)
            self._bytes -= len(json.dumps(old, separators=(",", ":"))) + 1
            self.dropped += 1
            key = old["plan_id"]
            self._plan_refs[key] -= 1
            self._release_plan(key)
        # A single active recording cannot exceed the overall byte budget.
        if not self.events and self._bytes > MAX_BYTES:
            self._record = self._decoded = self._plan_id = None
            for key in list(self.plans):
                self._release_plan(key)
        report_cutoff = date.fromisoformat(self._day(now)) - timedelta(
            days=REPORT_DAYS - 1
        )
        for day in list(self.daily):
            if date.fromisoformat(day) < report_cutoff:
                del self.daily[day]

    def _release_plan(self, key):
        if not self._plan_refs[key] and key != self._plan_id:
            self._plan_refs.pop(key, None)
            if key in self.plans:
                self._bytes -= len(self.plans.pop(key)) + len(key) + 6

    def activate_plan(self, at, config, inputs, result, version):
        record = recording(config, inputs, result)
        raw = json.dumps(record, separators=(",", ":"), allow_nan=False).encode()
        if len(raw) > MAX_PLAN_BYTES:
            raise ValueError("Plan recording exceeds limit")
        key = hashlib.sha256(raw).hexdigest()
        if key not in self.plans:
            blob = base64.b64encode(zlib.compress(raw)).decode()
            self.plans[key] = blob
            self._bytes += len(blob) + len(key) + 6
        previous_id = self._plan_id
        self._record, self._plan_id = record, key
        self._release_plan(previous_id)
        self._decoded = (inputs, result, config)
        self.event(at, "plan", {"version": version})
        return key

    def sample(self, at: datetime, measurements: dict, actors: dict):
        if self._previous is not None:
            start, previous, previous_actors = self._previous
            if at < start:
                # A backwards clock correction must not integrate time twice.
                self.event(at, "clock_regression", {})
                return
            if at > start:
                self._interval(start, at, previous, previous_actors)
        for key, sample in measurements.items():
            if key == "soc" or key.startswith("soc:"):
                value = measured_value(sample, at, power=False)
                if value is not None:
                    day = self._report(self._day(at))
                    target = (
                        day
                        if key == "soc"
                        else day.setdefault("storage_soc", {}).setdefault(key[4:], {})
                    )
                    target["soc_min_percent"] = min(
                        target.get("soc_min_percent", value), value
                    )
                    target["soc_max_percent"] = max(
                        target.get("soc_max_percent", value), value
                    )
        self._previous = (at, deepcopy(measurements), deepcopy(actors))
        self.event(at, "sample", {"measurements": measurements, "actors": actors})

    def _interval(self, start, end, samples, actors):
        start, end = start.astimezone(UTC), end.astimezone(UTC)
        observed_end = min(end, start + timedelta(seconds=MAX_SAMPLE_S))
        cursor = start
        while cursor < end:
            local = cursor.astimezone(ZoneInfo(self.timezone))
            midnight = (local + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            stop = min(end, midnight.astimezone(UTC))
            day = self._report(local.date().isoformat())
            valid_stop = max(cursor, min(stop, observed_end))
            duration = (valid_stop - cursor).total_seconds() / 3600
            day["observed_hours"] += duration
            day["gap_hours"] += (stop - cursor).total_seconds() / 3600 - duration
            expected = expected_interval(
                self._record, cursor, valid_stop, self._decoded
            )
            covered = expected.get("coverage_h", 0)
            for key, sample in samples.items():
                if key == "soc" or key.startswith("soc:"):
                    continue
                value = measured_value(sample, cursor)
                if value is None or duration <= 0:
                    continue
                fresh_end = min(
                    valid_stop,
                    datetime.fromisoformat(sample["reported_at"])
                    + timedelta(seconds=MAX_SAMPLE_S),
                )
                measured_h = (fresh_end - cursor).total_seconds() / 3600
                channel_expected = (
                    expected
                    if fresh_end == valid_stop
                    else expected_interval(
                        self._record, cursor, fresh_end, self._decoded
                    )
                )
                if (
                    measured_h <= 0
                    or abs(channel_expected.get("coverage_h", 0) - measured_h) > 1e-8
                    or key not in channel_expected
                ):
                    continue
                metric = day["metrics"].setdefault(
                    key, {"actual_wh": 0.0, "planned_wh": 0.0, "coverage_hours": 0.0}
                )
                metric["actual_wh"] += value * measured_h
                metric["planned_wh"] += channel_expected[key]
                metric["coverage_hours"] += measured_h
                metric["error_wh"] = metric["actual_wh"] - metric["planned_wh"]
                if key.startswith("load:") and actors.get(key) in (True, False):
                    load = day["loads"].setdefault(key[5:], {})
                    actual_h = measured_h if actors[key] else 0.0
                    power = channel_expected.get(key + ":power_h", 0) / measured_h
                    load["execution_error_wh"] = (
                        load.get("execution_error_wh", 0)
                        + power * actual_h
                        - channel_expected[key]
                    )
                    load["power_error_wh"] = (
                        load.get("power_error_wh", 0)
                        + value * measured_h
                        - power * actual_h
                    )
                    load["coverage_hours"] = load.get("coverage_hours", 0) + measured_h
            if duration > 0 and abs(covered - duration) < 1e-8:
                for key, on in actors.items():
                    if on not in (True, False) or key + ":run_h" not in expected:
                        continue
                    load = day["loads"].setdefault(key[5:], {})
                    load["actual_run_hours"] = load.get("actual_run_hours", 0) + (
                        duration if on else 0
                    )
                    load["planned_run_hours"] = (
                        load.get("planned_run_hours", 0) + expected[key + ":run_h"]
                    )
                    load["runtime_coverage_hours"] = (
                        load.get("runtime_coverage_hours", 0) + duration
                    )
            cursor = stop

    def break_observation(self, at, reason):
        self._previous = None
        self._record = None
        self._decoded = None
        previous_id = self._plan_id
        self._plan_id = None
        self._release_plan(previous_id)
        self.event(at, "observation_break", {"reason": reason})

    def export(self):
        return deepcopy(
            {
                "schema_version": SCHEMA,
                "timezone": self.timezone,
                "events": self.events,
                "plans": self.plans,
                "daily": self.daily,
                "sequence": self.sequence,
                "dropped_events": self.dropped,
            }
        )

    def restore(self, data):
        if not isinstance(data, dict) or data.get("schema_version") != SCHEMA:
            raise ValueError("Unsupported operation history schema")
        # Validate atomically: a broken store must not poison subsequent recording.
        ZoneInfo(data["timezone"])
        if not isinstance(data["daily"], dict) or not isinstance(data["events"], list):
            raise ValueError("Invalid reports or events")
        previous_sequence = 0
        for row in data["events"]:
            at = datetime.fromisoformat(row["at"])
            if at.tzinfo is None or row["sequence"] <= previous_sequence:
                raise ValueError("Invalid event order or timestamp")
            if not isinstance(row["data"], dict) or not isinstance(row["kind"], str):
                raise ValueError("Invalid event payload")
            if row["plan_id"] is not None and row["plan_id"] not in data["plans"]:
                raise ValueError("Missing referenced plan")
            previous_sequence = row["sequence"]
        if int(data["sequence"]) < previous_sequence or int(data["dropped_events"]) < 0:
            raise ValueError("Invalid archive counters")
        for day, report in data["daily"].items():
            date.fromisoformat(day)
            if not isinstance(report, dict) or report.get("day") != day:
                raise ValueError("Invalid daily report")
            for key in (
                "observed_hours",
                "gap_hours",
                "switch_requests",
                "state_changes",
                "service_failures",
            ):
                value = report.get(key)
                if (
                    not isinstance(value, (int, float))
                    or not math.isfinite(value)
                    or value < 0
                ):
                    raise ValueError("Invalid daily counter")
            for section in ("metrics", "loads", "storage_soc"):
                entries = report.get(section, {})
                if not isinstance(entries, dict):
                    raise ValueError("Invalid daily measurements")
                for values in entries.values():
                    if not isinstance(values, dict) or any(
                        not isinstance(value, (int, float)) or not math.isfinite(value)
                        for value in values.values()
                    ):
                        raise ValueError("Invalid daily measurement values")
        json.dumps(data, allow_nan=False)
        for key, blob in data["plans"].items():
            raw = unpack_plan(blob)
            if (
                hashlib.sha256(
                    json.dumps(raw, separators=(",", ":"), allow_nan=False).encode()
                ).hexdigest()
                != key
            ):
                raise ValueError("Plan checksum mismatch")
        self.events = deepcopy(data["events"])
        self.plans = dict(data["plans"])
        self.daily = deepcopy(data["daily"])
        self.sequence = int(data["sequence"])
        self.dropped = int(data["dropped_events"])
        self._bytes = sum(
            len(json.dumps(row, separators=(",", ":"))) + 1 for row in self.events
        ) + sum(len(key) + len(blob) + 6 for key, blob in self.plans.items())
        self._previous = self._record = self._plan_id = self._decoded = None
        self._plan_refs = Counter(row["plan_id"] for row in self.events)


def replay_history(data: dict, *, check_plans: bool = True) -> dict:
    """Replay observations in order; never silently promise a complete truncated day."""
    history = OperationHistory(data["timezone"])
    if data.get("schema_version") != SCHEMA:
        raise ValueError("Unsupported operation history schema")
    history.restore(data)  # Same checksum and structure contract as HA persistence.
    history = OperationHistory(data["timezone"])
    exact = {}
    records = {key: unpack_plan(blob) for key, blob in data["plans"].items()}
    if check_plans:
        exact = {key: replay(record)[1] for key, record in records.items()}
    for row in data["events"]:
        at = datetime.fromisoformat(row["at"])
        if row["kind"] == "plan":
            history._record = records[row["plan_id"]]
            history._plan_id = row["plan_id"]
            history.event(at, "plan", row["data"])
        elif row["kind"] == "sample":
            history._record = records.get(row["plan_id"])
            history._plan_id = row["plan_id"]
            history.sample(at, row["data"]["measurements"], row["data"]["actors"])
        elif row["kind"] == "observation_break":
            history.break_observation(at, row["data"]["reason"])
        else:
            history.event(at, row["kind"], row["data"])
    return {
        "schema_version": SCHEMA,
        "daily": history.daily,
        "exact_plans": exact,
        "complete_event_history": data.get("dropped_events", 0) == 0,
        "daily_matches": history.daily == data["daily"],
    }
