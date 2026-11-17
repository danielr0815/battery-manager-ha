"""Present PSU evidence is bound to confirmed actor/topology generations."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from custom_components.battery_manager.core.voltage_gate import present_delivery_w
from custom_components.battery_manager.psu_observation import PsuObservationCapture


def state(value, at, unit=None, *, changed=None):
    return SimpleNamespace(
        state=str(value),
        attributes={"unit_of_measurement": unit},
        last_reported=datetime.fromtimestamp(at, UTC),
        last_changed=datetime.fromtimestamp(
            changed if changed is not None else at, UTC
        ),
    )


@pytest.fixture
def rig():
    states = {
        "switch.source": state("on", 100),
        "sensor.output": state(40, 100, "W"),
        "sensor.input": state(50, 100, "W"),
    }
    owner = SimpleNamespace(
        raw_config={
            "support_dc48_switch_entity": "switch.source",
            "support_dc48_output_power_entity": "sensor.output",
            "support_dc48_power_entity": "sensor.input",
            "psu48_output_voltage_v": 50,
            "psu48_max_current_a": 2,
            "psu48_efficiency": 0.8,
        },
        hass=SimpleNamespace(states=SimpleNamespace(get=states.get)),
    )
    return owner, states, PsuObservationCapture()


def capture(rig, at):
    owner, _, adapter = rig
    return adapter.capture(owner, datetime.fromtimestamp(at, UTC))[1]


def test_point_delivery_does_not_invent_overlapping_interval_efficiency(rig):
    snapshot = capture(rig, 100)
    assert (
        present_delivery_w(
            snapshot, 100, snapshot.source_generation, snapshot.topology_fingerprint
        )
        == 40
    )
    diag = rig[2].diagnostic()[1]
    assert diag["ac_input_w"] == 50 and diag["dc_output_w"] == 40
    assert diag["efficiency"] is None
    assert diag["evidence_scope"] == "present_only"
    assert snapshot.dc_output.interval_start == snapshot.dc_output.interval_end == 100


def test_fresh_pre_off_on_point_cannot_survive_source_generation(rig):
    old = capture(rig, 100)
    _, states, adapter = rig
    states["switch.source"] = state("on", 120, changed=119)
    new = capture(rig, 120)
    assert new.source_generation == old.source_generation + 1
    assert new.dc_output is None
    assert adapter.diagnostic()[1]["channel_reasons"]["dc_output"] == (
        "previous_source_generation"
    )
    states["sensor.output"] = state(42, 121, "W")
    assert capture(rig, 121).quality == "measured"


def test_topology_change_requires_new_meter_publication(rig):
    old = capture(rig, 100)
    rig[0].raw_config["psu48_output_voltage_v"] = 49
    new = capture(rig, 101)
    assert new.topology_fingerprint != old.topology_fingerprint
    assert new.source_generation > old.source_generation
    assert new.dc_output is None


@pytest.mark.parametrize("actor", ["off", "unknown", "unavailable"])
def test_service_request_or_unknown_actor_is_not_confirmation(rig, actor):
    rig[1]["switch.source"] = state(actor, 100)
    snapshot = capture(rig, 100)
    assert not snapshot.confirmed_on and snapshot.quality == "unconfirmed"
    assert snapshot.dc_output is None


@pytest.mark.parametrize(
    "value,unit,quality",
    [
        (-1, "W", "incompatible"),
        (float("nan"), "W", "incompatible"),
        (float("inf"), "W", "incompatible"),
        (40, "kWh", "incompatible"),
        (101, "W", "incompatible"),
        (0.04, "kW", "measured"),
        (0, "W", "measured"),
    ],
)
def test_units_finite_values_sign_and_hardware_limits(rig, value, unit, quality):
    rig[1]["sensor.output"] = state(value, 100, unit)
    snapshot = capture(rig, 100)
    assert snapshot.quality == quality
    if quality == "measured":
        assert snapshot.dc_output.power_w == value * (1000 if unit == "kW" else 1)


def test_stale_output_and_last_positive_state_after_off_are_not_delivery(rig):
    capture(rig, 100)
    assert capture(rig, 131).quality == "stale"
    rig[1]["switch.source"] = state("off", 132)
    assert capture(rig, 132).dc_output is None


def test_soc_coordinate_is_not_electrical_meter_provenance(rig):
    old = capture(rig, 100)
    rig[0].raw_config.update(soc_entity="sensor.other_soc", battery_capacity_wh=9000)
    new = capture(rig, 101)
    assert old.source_generation == new.source_generation
    assert old.topology_fingerprint == new.topology_fingerprint
    assert new.quality == "measured"


def test_both_psu_sources_are_captured_independently(rig):
    owner, states, adapter = rig
    owner.raw_config.update(
        support_dc24_switch_entity="switch.rail",
        support_dc24_output_power_entity="sensor.rail",
        psu24_output_voltage_v=24,
        psu24_max_current_a=5,
    )
    states["switch.rail"] = state("on", 100)
    states["sensor.rail"] = state(60, 100, "W")
    rail, native = adapter.capture(owner, datetime.fromtimestamp(100, UTC))
    assert (rail.source_id, native.source_id) == ("dc24", "dc48")
    assert rail.dc_output.power_w == 60 and native.dc_output.power_w == 40


def test_stale_ac_is_not_fabricated_but_fresh_dc_stays_independently_valid(rig):
    capture(rig, 100)
    rig[1]["sensor.output"] = state(40, 131, "W")
    snapshot = capture(rig, 131)
    assert snapshot.quality == "measured" and snapshot.ac_input is None
    diag = rig[2].diagnostic()[1]
    assert diag["present_delivery_w"] == 40
    assert diag["efficiency"] is None
    assert diag["channel_reasons"]["ac_input"] == "stale"
