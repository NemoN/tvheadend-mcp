"""Validate against the schema returned by the connected Tvheadend build."""

import math
import re
from typing import Any


def uuid_hex(value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{32}", value):
        raise ValueError("Expected a 32-character Tvheadend UUID copied from a previous result")
    return value.lower()


def properties(schema: dict[str, Any]) -> dict[str, dict[str, Any]]:
    fields = schema.get("params", schema.get("props", []))
    return {p["id"]: p for p in fields if isinstance(p, dict) and "id" in p}


def values(node: dict[str, Any]) -> dict[str, Any]:
    return {key: prop["value"] for key, prop in properties(node).items() if "value" in prop}


def validate_fields(
    schema: dict[str, Any], fields: dict[str, Any], *, creating: bool = False
) -> None:
    props = properties(schema)
    if not props:
        raise ValueError("Tvheadend returned no field metadata; refusing an unchecked update")
    if not fields:
        raise ValueError("At least one setting is required")
    for key, value in fields.items():
        if key not in props:
            raise ValueError(f"Unknown field {key!r}; inspect the node or class schema first")
        prop = props[key]
        if prop.get("rdonly") or (prop.get("wronce") and not creating):
            raise ValueError(f"Field {key!r} is read-only")
        # 'nosave' is NOT read-only: e.g. the writable satconf selector has this flag.
        if prop.get("list"):
            if not isinstance(value, list):
                raise ValueError(f"Field {key!r} requires a JSON array (replaces the whole list)")
            items = value
        else:
            items = [value]
        for item in items:
            _validate_scalar(key, prop, item)


def _validate_scalar(key: str, prop: dict[str, Any], value: Any) -> None:
    kind = prop.get("type")
    valid = value is not None
    if prop.get("intsplit"):
        valid = isinstance(value, (int, float, str)) and bool(
            re.fullmatch(r"-?\d+(?:\.\d+)?", str(value))
        )
    elif kind == "bool":
        valid = isinstance(value, bool) or (type(value) is int and value in (0, 1))
    elif kind in {"int", "u16", "u32", "s64", "time", "dynint"}:
        valid = type(value) is int
        if valid and kind in {"u16", "u32"}:
            valid = 0 <= value < 2 ** (16 if kind == "u16" else 32)
    elif kind in {"dbl", "double"}:
        valid = type(value) in (int, float) and math.isfinite(value)
    elif kind in {"str", "perm"}:
        valid = isinstance(value, str)
    elif kind == "langstr":
        valid = isinstance(value, (str, dict))
    if not valid:
        raise ValueError(f"Invalid value for {key!r} (Tvheadend type {kind})")
    enum = prop.get("enum")
    if isinstance(enum, list) and enum:
        choices = [e["key"] for e in enum if isinstance(e, dict) and "key" in e]
        if choices and value not in choices and str(value) not in {str(c) for c in choices}:
            raise ValueError(f"Invalid option for {key!r}; allowed values: {choices}")
