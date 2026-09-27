"""Versioned, local-only planner recordings; no HA imports or executable input."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from datetime import date, datetime
from typing import Any

from . import model
from .model import PlanInputs, PlanResult, SystemConfig
from .optimize import plan

SCHEMA_VERSION = 1
# Only our frozen model types can be constructed. Never import a type named
# by a downloaded recording or use pickle/eval to reconstruct a plan.
_TYPES = {
    name: value
    for name, value in vars(model).items()
    if isinstance(value, type) and is_dataclass(value)
}


def encode(value: Any) -> Any:
    """Preserve tuple, date, datetime and mapping keys through ordinary JSON."""
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "type": type(value).__name__,
            "fields": {
                field.name: encode(getattr(value, field.name))
                for field in fields(value)
            },
        }
    if isinstance(value, datetime):
        return {"datetime": value.isoformat()}
    if isinstance(value, date):
        return {"date": value.isoformat()}
    if isinstance(value, tuple):
        return {"tuple": [encode(item) for item in value]}
    if isinstance(value, Mapping):
        return {"mapping": [[encode(key), encode(item)] for key, item in value.items()]}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise ValueError(f"Unsupported recording value: {type(value).__name__}")


def decode(value: Any) -> Any:
    """Decode only the schema's explicit, non-executable type vocabulary."""
    if not isinstance(value, dict):
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        raise ValueError("Invalid recording scalar")
    if set(value) == {"type", "fields"} and value["type"] in _TYPES:
        return _TYPES[value["type"]](
            **{key: decode(item) for key, item in value["fields"].items()}
        )
    if set(value) == {"datetime"}:
        return datetime.fromisoformat(value["datetime"])
    if set(value) == {"date"}:
        return date.fromisoformat(value["date"])
    if set(value) == {"tuple"}:
        return tuple(decode(item) for item in value["tuple"])
    if set(value) == {"mapping"}:
        return {decode(key): decode(item) for key, item in value["mapping"]}
    raise ValueError("Unknown recording structure or model type")


def recording(
    config: SystemConfig, inputs: PlanInputs, result: PlanResult
) -> dict[str, Any]:
    """Capture effective inputs together with their own result, not a later config."""
    return {
        "schema_version": SCHEMA_VERSION,
        "config": encode(config),
        "inputs": encode(inputs),
        "result": encode(result),
    }


def replay(record: dict[str, Any]) -> tuple[PlanResult, bool]:
    """Recompute a recording and compare every result field, including timing."""
    if record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported planner recording schema")
    config, inputs = decode(record["config"]), decode(record["inputs"])
    if not isinstance(config, SystemConfig) or not isinstance(inputs, PlanInputs):
        raise ValueError("Recording must contain SystemConfig and PlanInputs")
    result = plan(config, inputs)
    expected = decode(record["result"])
    compared = result
    if (
        isinstance(expected, PlanResult)
        and "appliance_advisories" not in record["result"]["fields"]
    ):
        # Old recordings never captured these explanations. Compare every
        # previously recorded field, while new recordings check reasons too.
        compared = replace(result, appliance_advisories={})
    if (
        isinstance(expected, PlanResult)
        and "reserve_decision" not in record["result"]["fields"]["trajectory"]["fields"]
    ):
        # A historical recording has no explanatory reserve snapshot to
        # compare. Its physical flows still participate in the comparison.
        compared = replace(
            compared,
            trajectory=replace(compared.trajectory, reserve_decision=None),
        )
    return result, encode(compared) == encode(expected)
