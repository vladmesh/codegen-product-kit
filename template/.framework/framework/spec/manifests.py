"""Typed, fail-closed service manifests for core product settings."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, Field, field_validator


class ServiceManifest(BaseModel):
    """A service-owned declaration of settings users may control.

    Settings are independent values, so the supported JSON Schema subset is an
    object with named properties and no cross-property ``required`` constraint.
    References are deliberately excluded: a generated product must not resolve
    schemas from the network or another undeclared source at runtime.
    """

    version: Literal[1]
    settings_schema: dict[str, Any] = Field(alias="settings_schema")

    model_config = {"extra": "forbid", "populate_by_name": True}

    @field_validator("settings_schema")
    @classmethod
    def validate_settings_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
            msg = "settings_schema.$schema must be the Draft 2020-12 meta-schema URL"
            raise ValueError(msg)
        if schema.get("type") != "object":
            msg = "settings_schema.type must be 'object'"
            raise ValueError(msg)
        if schema.get("additionalProperties") is not False:
            msg = "settings_schema.additionalProperties must be false"
            raise ValueError(msg)
        if "required" in schema:
            msg = "settings_schema.required is unsupported; settings are written independently"
            raise ValueError(msg)

        properties = schema.get("properties")
        if not isinstance(properties, Mapping):
            msg = "settings_schema.properties must be an object"
            raise ValueError(msg)
        if any(not isinstance(key, str) or not key for key in properties):
            msg = "settings_schema.properties keys must be non-empty strings"
            raise ValueError(msg)
        if any(not isinstance(value, Mapping) for value in properties.values()):
            msg = "settings_schema.properties values must be object schemas"
            raise ValueError(msg)
        if _contains_ref(schema):
            msg = "settings_schema does not support $ref"
            raise ValueError(msg)

        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            raise ValueError(f"invalid settings_schema: {error.message}") from error
        return schema


def _contains_ref(value: object) -> bool:
    if isinstance(value, Mapping):
        return "$ref" in value or any(_contains_ref(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_ref(item) for item in value)
    return False


def parse_service_manifest(data: dict[str, Any]) -> ServiceManifest:
    """Validate raw YAML data as a versioned service manifest."""

    return ServiceManifest.model_validate(data)
