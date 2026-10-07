"""Inline action contracts and import-resource declarations shared by tooling."""

import json
from pathlib import PurePosixPath
import re
from typing import Annotated, Any, Literal

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, Field, field_validator, model_validator


def resource_reference(value: str) -> str:
    module, separator, path = value.partition(":")
    if (
        not separator
        or not all(part.isidentifier() for part in module.split("."))
        or not path
        or PurePosixPath(path).is_absolute()
        or any(part in {"", ".", ".."} for part in path.split("/"))
        or "\\" in path
    ):
        raise ValueError("must use a non-traversing module:path resource")
    return value


def inline_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Validate inline JSON Schema without resolving external or alias references."""
    try:
        json.dumps(schema, allow_nan=False)
        Draft202012Validator.check_schema(schema)
    except SchemaError as error:
        raise ValueError(f"invalid action schema: {error.message}") from error
    except (TypeError, ValueError) as error:
        raise ValueError("action schemas must contain JSON values") from error
    _schema_nodes(schema)
    _typed_schema(schema)
    return schema


def _schema_nodes(node: Any) -> None:
    if isinstance(node, dict):
        if "$ref" in node or "$dynamicRef" in node:
            raise ValueError("action schemas must be inline; references are unsupported")
        if "properties" in node and not set(node.get("required", [])) <= node["properties"].keys():
            raise ValueError("schema requires undeclared fields")
        children = [
            value
            for key in ("properties", "$defs", "patternProperties", "dependentSchemas")
            for value in node.get(key, {}).values()
        ]
        children.extend(
            value
            for key in ("anyOf", "oneOf", "allOf", "prefixItems")
            for value in node.get(key, [])
        )
        children.extend(
            node[key]
            for key in ("items", "contains", "not", "if", "then", "else", "additionalProperties")
            if key in node
        )
        for child in children:
            _schema_nodes(child)


def _typed_schema(schema: dict[str, Any]) -> None:
    if not isinstance(schema, dict) or (
        "type" not in schema and not any(key in schema for key in ("anyOf", "oneOf"))
    ):
        raise ValueError("action fields must have inline typed schemas")
    children = list(schema.get("properties", {}).values())
    children.extend(item for key in ("anyOf", "oneOf") for item in schema.get(key, []))
    if schema.get("type") == "array" and "items" not in schema:
        raise ValueError("action array output must have typed items")
    if "items" in schema:
        children.append(schema["items"])
    for child in children:
        _typed_schema(child)


class HttpOperation(BaseModel):
    model_config = {"extra": "forbid"}
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
    path: str

    @field_validator("path")
    @classmethod
    def relative_path(cls, value: str) -> str:
        if value and re.fullmatch(r"(?:/(?:[A-Za-z0-9_-]+|\{[a-z][a-z0-9_]*\}))+", value) is None:
            raise ValueError("operation path must be empty or a relative route suffix")
        return value


class PackageAction(BaseModel):
    model_config = {"extra": "forbid"}
    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    summary: str = Field(min_length=1)
    operation: HttpOperation
    input: dict[str, Any]
    output: dict[str, Any]
    errors: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]*$")]] = Field(default_factory=list)

    @field_validator("input", "output")
    @classmethod
    def schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        return inline_schema(value)

    @model_validator(mode="after")
    def input_object(self) -> "PackageAction":
        if len(self.errors) != len(set(self.errors)):
            raise ValueError("duplicate action error codes")
        if self.input.get("type") != "object" or not isinstance(self.input.get("properties"), dict):
            raise ValueError("action input must have object properties")
        for name in re.findall(r"\{([^}]+)\}", self.operation.path):
            if name not in self.input.get("required", []):
                raise ValueError(f"path parameter {name!r} must be a required input")
        return self
