"""Conservative, pure inference from a library's primary result to an action parameter."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from framework.catalog import CatalogFunction

_ANNOTATIONS = {"title", "description", "default", "examples", "$comment", "$schema"}
_SUPPORTED = {
    "type",
    "format",
    "enum",
    "const",
    "items",
    "properties",
    "required",
    "additionalProperties",
    "anyOf",
    "oneOf",
}


def _normalized(schema: dict[str, Any]) -> dict[str, Any]:
    result = {key: value for key, value in schema.items() if key not in _ANNOTATIONS}
    if "properties" in result:
        result["properties"] = {
            key: _normalized(value) for key, value in result["properties"].items()
        }
    if isinstance(result.get("items"), dict):
        result["items"] = _normalized(result["items"])
    for key in ("anyOf", "oneOf"):
        if key in result:
            result[key] = [_normalized(value) for value in result[key]]
    for key in ("required", "type"):
        if isinstance(result.get(key), list):
            result[key] = sorted(result[key])
    if "enum" in result:
        result["enum"] = sorted(result["enum"], key=_literal)
    return result


def non_null_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Unwrap a single result plus null; preserve every non-null union."""

    result = _normalized(schema)
    kind = result.get("type")
    if isinstance(kind, list):
        remaining = [item for item in kind if item != "null"]
        if len(remaining) == 1:
            result["type"] = remaining[0]
    for key in ("anyOf", "oneOf"):
        if key in result:
            remaining = [item for item in result[key] if item.get("type") != "null"]
            if len(remaining) == 1:
                result = {
                    **{name: value for name, value in result.items() if name != key},
                    **remaining[0],
                }
    return result


def _literal(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _semantic(schema: dict[str, Any]) -> bool:
    if any(key in schema for key in ("format", "enum", "const")):
        return True
    if schema.get("type") == "array":
        return _semantic(schema.get("items", {}))
    if schema.get("type") == "object":
        return any(
            _semantic(schema.get("properties", {}).get(name, {}))
            for name in schema.get("required", [])
        )
    return any(_semantic(item) for key in ("anyOf", "oneOf") for item in schema.get(key, []))


def _values(schema: dict[str, Any]) -> set[str] | None:
    values = {_literal(item) for item in schema["enum"]} if "enum" in schema else None
    if "const" in schema:
        constant = {_literal(schema["const"])}
        values = constant if values is None else values & constant
    return values


def _object_matches(source: dict[str, Any], target: dict[str, Any]) -> bool:
    source_properties = source.get("properties", {})
    target_properties = target.get("properties", {})
    if not set(target.get("required", [])) <= set(source.get("required", [])):
        return False
    if target.get("additionalProperties") is False and (
        source.get("additionalProperties") is not False
        or not source_properties.keys() <= target_properties.keys()
    ):
        return False
    if isinstance(source.get("additionalProperties"), dict) or isinstance(
        target.get("additionalProperties"), dict
    ):
        return False
    for name, parameter in target_properties.items():
        if name in source_properties:
            if not _compatible(source_properties[name], parameter):
                return False
        elif name in target.get("required", []) or source.get("additionalProperties") is not False:
            return False
    return True


def _compatible(source: dict[str, Any], target: dict[str, Any]) -> bool:
    # Unmodelled constraints/references never produce a guessed edge.
    union = (
        any(key in source or key in target for key in ("anyOf", "oneOf"))
        or isinstance(source.get("type"), list)
        or isinstance(target.get("type"), list)
    )
    if union:
        return source == target
    if (
        source.get("type") != target.get("type")
        or "type" not in source
        or source.get("format") != target.get("format")
        or (source.keys() | target.keys()) - _SUPPORTED
    ):
        return False
    source_values, target_values = _values(source), _values(target)
    if target_values is not None and (source_values is None or not source_values <= target_values):
        return False
    if source.get("type") == "array":
        return (
            isinstance(source.get("items"), dict)
            and isinstance(target.get("items"), dict)
            and _compatible(source["items"], target["items"])
        )
    if source.get("type") == "object":
        return _object_matches(source, target)
    return True


def _supported(schema: dict[str, Any]) -> bool:
    if schema.keys() - _SUPPORTED - _ANNOTATIONS or isinstance(
        schema.get("additionalProperties"), dict
    ):
        return False
    children = list(schema.get("properties", {}).values())
    children.extend(item for key in ("anyOf", "oneOf") for item in schema.get(key, []))
    if "items" in schema:
        children.append(schema["items"])
    return all(isinstance(child, dict) and _supported(child) for child in children)


def primary_output_matches(function: CatalogFunction, parameter: dict[str, Any]) -> bool:
    """Infer only a semantic edge from ``function.value`` to one action parameter.

    Null at the result boundary means no parsed result, handled by the future binding.
    Secondary fields are never searched. Unsupported schemas return False.
    """

    result = non_null_schema(function.output)
    if result.get("type") != "object" or function.value not in result.get("required", []):
        return False
    primary = result.get("properties", {}).get(function.value)
    if not isinstance(primary, dict) or not _supported(primary) or not _supported(parameter):
        return False
    target = _normalized(parameter)
    return _semantic(target) and _compatible(primary, target)
