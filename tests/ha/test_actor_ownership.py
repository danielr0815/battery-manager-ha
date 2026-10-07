"""Exclusive actors: configuration boundaries and legacy collision recovery."""

from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.battery_manager import const as c
from custom_components.battery_manager.actor_ownership import (
    COLLISION_OWNER,
    SUPPLY_OWNER,
    actor_claims,
    actor_conflicts,
    blocked_load_ids,
    command_allowed,
)
from custom_components.battery_manager.config_flow import (
    SurplusLoadSubentryFlow as Flow,
)
from custom_components.battery_manager.coordinator import BatteryManagerCoordinator


def load(actor, **data):
    return SimpleNamespace(
        subentry_type=c.SUBENTRY_TYPE_LOAD,
        data={c.CONF_LOAD_CONTROL_SWITCH: actor, **data},
    )


def test_claims_block_complete_loads_and_cascades_but_preserve_supply():
    subs = {
        "a": load("switch.shared", **{c.CONF_LOAD_CHARGE_ENABLE: "switch.gate"}),
        "b": load("switch.shared"),
        "leaf": load("switch.leaf"),
        "chain": SimpleNamespace(
            subentry_type=c.SUBENTRY_TYPE_CASCADE,
            data={
                c.CONF_CASCADE_MEMBER_IDS: ["a"],
                c.CONF_CASCADE_TERMINAL_LOAD_ID: "leaf",
            },
        ),
        "valid": load("switch.valid"),
        "appliance": SimpleNamespace(subentry_type=c.SUBENTRY_TYPE_APPLIANCE, data={}),
    }
    claims = actor_claims(
        {
            c.CONF_SUPPORT_DC24_SWITCH: "switch.shared",
            c.CONF_FEEDIN_SETPOINT_ENTITY: "number.export",
        },
        subs,
    )
    assert blocked_load_ids(claims, subs) == {"a", "b", "leaf"}
    assert set(actor_conflicts(claims)) == {"switch.shared"}
    assert command_allowed(claims, "switch.shared", True, SUPPLY_OWNER)
    assert not command_allowed(claims, "switch.shared", False, COLLISION_OWNER)
    assert not command_allowed(claims, "switch.gate", True, "cascade:chain")
    assert command_allowed(claims, "switch.gate", False, COLLISION_OWNER)
    assert not command_allowed(claims, "switch.valid", False, "load:b")
    assert command_allowed(claims, "switch.valid", True, "load:valid")
    assert not command_allowed(claims, "number.export", False, COLLISION_OWNER)
    assert command_allowed(claims, "switch.unknown", False, None)
    pure = actor_claims({}, {"a": load("switch.shared"), "b": load("switch.shared")})
    assert not command_allowed(pure, "switch.shared", True, "load:a")
    assert command_allowed(pure, "switch.shared", False, COLLISION_OWNER)


@pytest.mark.parametrize("supply", [False, True])
async def test_legacy_collisions_recover_pure_loads_without_touching_supply(
    hass, supply
):
    from types import MappingProxyType

    from homeassistant.config_entries import ConfigSubentry

    entry = MockConfigEntry(
        domain=c.DOMAIN,
        data={c.CONF_SUPPORT_DC24_SWITCH: "switch.shared"} if supply else {},
    )
    entry.add_to_hass(hass)
    for sid in ("a", "b"):
        sub = ConfigSubentry(
            subentry_type=c.SUBENTRY_TYPE_LOAD,
            title=sid,
            unique_id=None,
            data=MappingProxyType({c.CONF_LOAD_CONTROL_SWITCH: "switch.shared"}),
            subentry_id=sid,
        )
        hass.config_entries.async_add_subentry(entry, sub)
    coordinator = BatteryManagerCoordinator(hass, entry)
    calls = []

    async def service(call):
        calls.append(call.data["entity_id"])
        hass.states.async_set("switch.shared", "off")

    hass.services.async_register("homeassistant", "turn_off", service)
    hass.states.async_set("switch.shared", "on")
    await coordinator._reconcile_actor_ownership()
    assert calls == ([] if supply else ["switch.shared"])
    assert (
        ir.async_get(hass).async_get_issue(c.DOMAIN, "actor_conflict_" + entry.entry_id)
        is not None
    )
    assert not await coordinator._switch_entity(
        "switch.shared", True, actor_owner="load:a"
    )
    assert not await coordinator._set_number_value(
        "switch.shared", 1, actor_owner="load:a"
    )
    assert actor_conflicts(actor_claims(coordinator.raw_config, entry.subentries))
    await coordinator.async_cancel_actuation_tasks()


@pytest.mark.parametrize("reconfigure", [False, True])
@pytest.mark.parametrize("supply", [False, True])
def test_load_flow_rejects_conflicting_actor_before_any_write(reconfigure, supply):
    existing = SimpleNamespace(
        subentry_id="edited", subentry_type=c.SUBENTRY_TYPE_LOAD, data={}
    )
    entry = SimpleNamespace(
        data={c.CONF_SUPPORT_DC24_SWITCH: "switch.shared"} if supply else {},
        options={},
        subentries={"edited": existing, "other": load("switch.shared")}
        if not supply
        else {"edited": existing},
    )
    flow = SimpleNamespace(
        _STORAGE_KEYS=Flow._STORAGE_KEYS,
        _STORAGE_ENTITY_KEYS=Flow._STORAGE_ENTITY_KEYS,
        _existing={},
        _basic={
            c.CONF_LOAD_NAME: "Test",
            c.CONF_LOAD_PRIORITY: 1,
            c.CONF_LOAD_ENERGY_LIMITED: False,
            c.CONF_LOAD_CONTROL_SWITCH: "switch.shared",
        },
        _is_reconfigure=reconfigure,
        _get_entry=lambda: entry,
        _get_reconfigure_subentry=lambda: existing,
        _renumber_siblings=Mock(),
        async_update_and_abort=Mock(),
        async_create_entry=Mock(),
        _basic_schema=lambda _: None,
        _storage_schema=lambda _: None,
        async_show_form=lambda **kw: kw,
    )
    result = Flow._finish(flow, {})
    assert result["errors"]["base"] == "actor_in_use"
    flow._renumber_siblings.assert_not_called()
    flow.async_update_and_abort.assert_not_called()
    flow.async_create_entry.assert_not_called()


@pytest.mark.parametrize("other", ["supply", "load"])
def test_cascade_cannot_claim_actor_from_supply_or_normal_load(other):
    from custom_components.battery_manager.config_flow import CascadeSubentryFlow

    root = load(
        "switch.root",
        **{
            c.CONF_LOAD_ENERGY_LIMITED: True,
            c.CONF_LOAD_SOC_ENTITY: "sensor.root_soc",
            c.CONF_LOAD_CHARGE_ENABLE: "switch.gate",
            c.CONF_LOAD_OUTPUT_SWITCH: "switch.output",
            c.CONF_LOAD_OUTPUT_POWER_ENTITY: "sensor.output",
            c.CONF_LOAD_DISCHARGE_FLOOR_SOC: 20,
            c.CONF_LOAD_RECOVERY_SOC: 50,
            c.CONF_LOAD_TARGET_SOC: 90,
        },
    )
    subs = {"root": root, "leaf": load("switch.leaf")}
    config = {}
    if other == "load":
        subs["other"] = load("switch.output")
    else:
        config[c.CONF_DCDC_SWITCH] = "switch.output"
    entry = SimpleNamespace(subentries=subs, data=config, options={})
    data = {
        c.CONF_LOAD_NAME: "Chain",
        c.CONF_CASCADE_MEMBER_IDS: ["root"],
        c.CONF_CASCADE_TERMINAL_LOAD_ID: "leaf",
    }
    assert CascadeSubentryFlow._validate_entry(entry, data, None) == "actor_in_use"


@pytest.mark.parametrize("confirmed", [False, True])
async def test_collision_recovery_confirms_gates_before_inputs(hass, confirmed):
    from homeassistant.config_entries import ConfigSubentryData

    entry = MockConfigEntry(
        domain=c.DOMAIN,
        subentries_data=[
            ConfigSubentryData(
                subentry_type=c.SUBENTRY_TYPE_LOAD,
                title=sid,
                unique_id=None,
                data={
                    c.CONF_LOAD_CONTROL_SWITCH: "switch.shared",
                    c.CONF_LOAD_CHARGE_ENABLE: "switch.gate",
                },
            )
            for sid in ("a", "b")
        ],
    )
    entry.add_to_hass(hass)
    coordinator = BatteryManagerCoordinator(hass, entry)
    for actor in ("switch.gate", "switch.shared"):
        hass.states.async_set(actor, "on")
    calls = []

    async def service(call):
        actor = call.data["entity_id"]
        calls.append(actor)
        if confirmed:
            hass.states.async_set(actor, "off")

    hass.services.async_register("homeassistant", "turn_off", service)
    await coordinator._reconcile_actor_ownership()
    assert calls == (["switch.gate", "switch.shared"] if confirmed else ["switch.gate"])
    if not confirmed:
        assert hass.states.get("switch.shared").state == "on"
    await coordinator.async_cancel_actuation_tasks()
