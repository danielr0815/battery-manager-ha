"""Exclusive physical actors across loads, cascades and house supply.

Supply protection wins over a legacy optional-load collision. Ambiguous loads
cannot start; their pure load actors may only be released by reconciliation.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .const import (
    CONF_CASCADE_MEMBER_IDS,
    CONF_CASCADE_TERMINAL_LOAD_ID,
    CONF_DCDC_SWITCH,
    CONF_FEEDIN_SETPOINT_ENTITY,
    CONF_INVERTER_BLOCK_SWITCH,
    CONF_INVERTER_LIMIT_ENTITY,
    CONF_LOAD_CHARGE_ENABLE,
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_OUTPUT_SWITCH,
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_SWITCH,
    SUBENTRY_TYPE_CASCADE,
    SUBENTRY_TYPE_LOAD,
)

SUPPLY_OWNER = "supply"
COLLISION_OWNER = "collision_recovery"
SUPPLY_ACTOR_KEYS = (
    CONF_SUPPORT_DC24_SWITCH,
    CONF_SUPPORT_DC48_SWITCH,
    CONF_DCDC_SWITCH,
    CONF_INVERTER_BLOCK_SWITCH,
    CONF_INVERTER_LIMIT_ENTITY,
)
LOAD_ACTOR_KEYS = (
    CONF_LOAD_CONTROL_SWITCH,
    CONF_LOAD_CHARGE_ENABLE,
    CONF_LOAD_OUTPUT_SWITCH,
)


def actor_claims(
    config: Mapping[str, Any], subentries: Mapping[str, Any]
) -> dict[str, frozenset[str]]:
    claims: dict[str, set[str]] = {}
    memberships: dict[str, set[str]] = {}
    for cid, sub in subentries.items():
        if sub.subentry_type == SUBENTRY_TYPE_CASCADE:
            for lid in (
                *sub.data.get(CONF_CASCADE_MEMBER_IDS, ()),
                sub.data.get(CONF_CASCADE_TERMINAL_LOAD_ID),
            ):
                if lid:
                    memberships.setdefault(lid, set()).add(cid)
    for lid, sub in subentries.items():
        if sub.subentry_type != SUBENTRY_TYPE_LOAD:
            continue
        owners = memberships.get(lid, {lid})
        prefix = "cascade:" if lid in memberships else "load:"
        for key in LOAD_ACTOR_KEYS:
            if actor := sub.data.get(key):
                claims.setdefault(actor, set()).update(
                    prefix + owner for owner in owners
                )
    for key in SUPPLY_ACTOR_KEYS:
        if actor := config.get(key):
            claims.setdefault(actor, set()).add(SUPPLY_OWNER)
    if actor := config.get(CONF_FEEDIN_SETPOINT_ENTITY):
        claims.setdefault(actor, set()).add("feedin")
    return {actor: frozenset(owners) for actor, owners in claims.items()}


def actor_conflicts(claims: Mapping[str, frozenset[str]]) -> dict[str, tuple[str, ...]]:
    return {
        actor: tuple(sorted(owners))
        for actor, owners in claims.items()
        if len(owners) > 1
    }


def blocked_load_ids(
    claims: Mapping[str, frozenset[str]], subentries: Mapping[str, Any]
) -> frozenset[str]:
    blocked = {owner for owners in actor_conflicts(claims).values() for owner in owners}
    ids = {
        owner.removeprefix("load:") for owner in blocked if owner.startswith("load:")
    }
    for cid, sub in subentries.items():
        if "cascade:" + cid in blocked:
            ids.update(sub.data.get(CONF_CASCADE_MEMBER_IDS, ()))
            if terminal := sub.data.get(CONF_CASCADE_TERMINAL_LOAD_ID):
                ids.add(terminal)
    return frozenset(ids)


def command_allowed(
    claims: Mapping[str, frozenset[str]], entity: str, on: bool, owner: str | None
) -> bool:
    owners = claims.get(entity, frozenset())
    blocked = {item for values in actor_conflicts(claims).values() for item in values}
    if SUPPLY_OWNER in owners:
        return owner == SUPPLY_OWNER
    if owner == COLLISION_OWNER:
        return not on and bool(owners.intersection(blocked)) and "feedin" not in owners
    if len(owners) > 1:
        return False
    if owner is not None and owners and owner not in owners:
        return False
    return not (on and owners.intersection(blocked))
