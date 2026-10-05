"""Structural agreement of declared actions with a package's real OpenAPI."""

from typing import Any

from framework.spec.packages import PackageManifest


def normalize_schema(value: Any, document: dict[str, Any]) -> Any:
    """Resolve local OpenAPI refs and strip documentation, retaining constraints."""
    if isinstance(value, list):
        return [normalize_schema(item, document) for item in value]
    if not isinstance(value, dict):
        return value
    if "$ref" in value:
        reference = value["$ref"]
        if not reference.startswith("#/"):
            raise ValueError("only local OpenAPI references are supported")
        target = document
        for segment in reference[2:].split("/"):
            target = target[segment.replace("~1", "/").replace("~0", "~")]
        value = {**target, **{key: item for key, item in value.items() if key != "$ref"}}
    result = {
        key: normalize_schema(item, document)
        for key, item in value.items()
        if key not in {"title", "description"}
    }
    if "required" in result:
        result["required"] = sorted(result["required"])
    return result


def operation_input(operation: dict[str, Any], document: dict[str, Any]) -> dict[str, Any]:
    """Flatten the object body and public path/query parameters into an action input."""
    body = operation.get("requestBody")
    result = (
        normalize_schema(body["content"]["application/json"]["schema"], document)
        if body
        else {"type": "object", "properties": {}, "additionalProperties": False}
    )
    required = set(result.get("required", [])) if body is None or body.get("required") else set()
    for parameter in operation.get("parameters", []):
        if parameter["in"] not in {"path", "query"}:
            continue
        name = parameter["name"]
        if name in result["properties"]:
            raise ValueError(f"ambiguous action input {name!r}")
        result["properties"][name] = normalize_schema(parameter["schema"], document)
        if parameter.get("required"):
            required.add(name)
    if required:
        result["required"] = sorted(required)
    return result


def verify_action_openapi(manifest: PackageManifest, document: dict[str, Any]) -> None:
    """Refuse route, input or selected JSON success-output drift."""
    for action in manifest.actions:
        path = manifest.http.prefix + action.operation.path
        method = action.operation.method.lower()
        operation = document.get("paths", {}).get(path, {}).get(method)
        if operation is None:
            raise ValueError(f"{action.name}: missing OpenAPI operation {method} {path}")
        if normalize_schema(action.input, document) != operation_input(operation, document):
            raise ValueError(f"{action.name}: OpenAPI input drift")
        successes = [
            response["content"]["application/json"]["schema"]
            for status, response in operation["responses"].items()
            if status.startswith("2") and "application/json" in response.get("content", {})
        ]
        if not successes or any(
            normalize_schema(action.output, document) != normalize_schema(schema, document)
            for schema in successes
        ):
            raise ValueError(f"{action.name}: OpenAPI output drift")
