"""Physical interval accounting shared by recorder and offline evaluation."""

from datetime import datetime, timedelta

from .replay import decode


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
