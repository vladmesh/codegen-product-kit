"""Package protocol v1 manifest validation."""

from __future__ import annotations

import ast
from pathlib import Path
import re
import sys
from typing import Any, Literal

from jsonschema import Draft202012Validator, SchemaError
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
import yaml

from framework.spec.manifests import _validate_declaration_schema, empty_declaration_schema

PACKAGE_PROTOCOL_VERSION = 1


class PackageManifestError(ValueError):
    """Base class for an invalid package manifest."""


class UnknownPackageManifestFieldError(PackageManifestError):
    """The manifest contains a field outside protocol v1."""


class MissingPackageIdentityError(PackageManifestError):
    """The manifest omits its name or distribution version."""


class MalformedPackagePrefixError(PackageManifestError):
    """The package HTTP prefix is not an absolute, unambiguous path."""


class UnimplementedDeploymentModeError(PackageManifestError):
    """The manifest declares a delivery form no generated product implements."""


class HttpDeclaration(BaseModel):
    """HTTP routes contributed by a package."""

    prefix: str
    model_config = {"extra": "forbid"}

    @field_validator("prefix")
    @classmethod
    def validate_prefix(cls, prefix: str) -> str:
        if (
            not prefix.startswith("/")
            or prefix == "/"
            or prefix.endswith("/")
            or "//" in prefix
            or "{" in prefix
            or "}" in prefix
        ):
            raise ValueError(
                "must start with '/', name a non-root path, and have no trailing slash"
            )
        return prefix


class DatabaseDeclaration(BaseModel):
    """Package-owned database schema and Alembic revision resource."""

    schema_name: str = Field(alias="schema")
    migrations: str
    model_config = {"extra": "forbid", "populate_by_name": True}

    @field_validator("schema_name")
    @classmethod
    def validate_schema_name(cls, schema: str) -> str:
        if schema == "public" or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", schema) is None:
            raise ValueError("must be a non-public PostgreSQL identifier")
        return schema

    @field_validator("migrations")
    @classmethod
    def validate_migrations(cls, declaration: str) -> str:
        module, separator, relative = declaration.partition(":")
        if not separator or not module or not relative or ".." in Path(relative).parts:
            raise ValueError("must use a non-traversing module:path resource")
        return declaration


class PackageEventMessage(BaseModel):
    """Language-neutral message schema for one package event."""

    model: str
    schema_data: dict[str, Any] = Field(alias="schema")
    model_config = {"extra": "forbid", "populate_by_name": True}

    @field_validator("model")
    @classmethod
    def validate_model_name(cls, name: str) -> str:
        if not name.isidentifier():
            raise ValueError("must be a Python-compatible generated model name")
        return name

    @field_validator("schema_data")
    @classmethod
    def validate_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as error:
            raise ValueError(f"must be a valid Draft 2020-12 schema: {error.message}") from error
        if schema.get("type") != "object":
            raise ValueError("must describe an object message")
        return schema


class EventsDeclaration(BaseModel):
    """Package event names and the message contract bound to each name."""

    publishes: list[str] = Field(default_factory=list)
    consumes: list[str] = Field(default_factory=list)
    messages: dict[str, PackageEventMessage] = Field(default_factory=dict)
    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_messages_cover_events(self) -> EventsDeclaration:
        declared = self.publishes + self.consumes
        if len(set(self.publishes)) != len(self.publishes):
            raise ValueError("events.publishes must not repeat an event")
        if len(set(self.consumes)) != len(self.consumes):
            raise ValueError("events.consumes must not repeat an event")
        missing = sorted(set(declared) - set(self.messages))
        extra = sorted(set(self.messages) - set(declared))
        if missing:
            raise ValueError(f"events.messages is missing declared event {missing[0]!r}")
        if extra:
            raise ValueError(f"events.messages describes undeclared event {extra[0]!r}")
        return self


class EnvironmentDeclaration(BaseModel):
    """One environment variable required by the package."""

    name: str
    required: bool = True
    model_config = {"extra": "forbid"}

    @field_validator("name")
    @classmethod
    def validate_name(cls, name: str) -> str:
        if re.fullmatch(r"[A-Z_][A-Z0-9_]*", name) is None:
            raise ValueError("must be an uppercase environment variable name")
        return name


class ResourceDeclaration(BaseModel):
    """One package-owned resource included in its distribution."""

    name: str
    path: str
    model_config = {"extra": "forbid"}

    @field_validator("name")
    @classmethod
    def validate_name(cls, name: str) -> str:
        if not name:
            raise ValueError("must not be empty")
        return name

    @field_validator("path")
    @classmethod
    def validate_path(cls, path: str) -> str:
        candidate = Path(path)
        if candidate.is_absolute() or not path or ".." in candidate.parts:
            raise ValueError("must be a non-traversing distribution-relative path")
        return path


class DeploymentDeclaration(BaseModel):
    """Delivery forms a package author declares the distribution can support."""

    modes: list[Literal["in_process", "container"]] = Field(default_factory=lambda: ["in_process"])
    model_config = {"extra": "forbid"}

    @field_validator("modes")
    @classmethod
    def validate_modes(cls, modes: list[str]) -> list[str]:
        if not modes:
            raise ValueError("must declare at least one deployment mode")
        if len(set(modes)) != len(modes):
            raise ValueError("must not repeat a deployment mode")
        if "container" in modes:
            raise ValueError("declares 'container', which no generated product implements")
        return modes


class PackageManifest(BaseModel):
    """Fail-closed declaration carried by a package distribution."""

    protocol_version: Literal[1]
    name: str
    version: str
    requires_core: str
    provides: list[str] = Field(default_factory=list)
    requires: list[str] = Field(default_factory=list)
    package_dependencies: list[str] = Field(default_factory=list)
    http: HttpDeclaration
    database: DatabaseDeclaration | None = None
    events: EventsDeclaration = Field(default_factory=EventsDeclaration)
    settings_schema: dict[str, Any] = Field(default_factory=empty_declaration_schema)
    jobs_schema: dict[str, Any] = Field(default_factory=empty_declaration_schema)
    deployment: DeploymentDeclaration = Field(default_factory=DeploymentDeclaration)
    environment: list[EnvironmentDeclaration] = Field(default_factory=list)
    resources: list[ResourceDeclaration] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def validate_named_lists(self) -> PackageManifest:
        for field_name, values in (
            ("provides", self.provides),
            ("requires", self.requires),
            ("environment", [item.name for item in self.environment]),
            ("resources", [item.name for item in self.resources]),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{field_name} must not repeat a name")
        return self

    @field_validator("settings_schema")
    @classmethod
    def validate_settings_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        return _validate_declaration_schema(schema, "settings_schema")

    @field_validator("jobs_schema")
    @classmethod
    def validate_jobs_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        _validate_declaration_schema(schema, "jobs_schema")
        for name, arguments in schema["properties"].items():
            if arguments.get("type") != "object":
                raise ValueError(f"jobs_schema.properties.{name}.type must be 'object'")
            if arguments.get("additionalProperties") is not False:
                raise ValueError(
                    f"jobs_schema.properties.{name}.additionalProperties must be false"
                )
        return schema


def parse_package_manifest(data: object) -> PackageManifest:
    """Validate raw manifest data and expose stable, named structural errors."""

    if not isinstance(data, dict):
        raise PackageManifestError("package.yaml must contain an object")
    known = set(PackageManifest.model_fields)
    unknown = sorted(set(data) - known)
    if unknown:
        raise UnknownPackageManifestFieldError(
            f"package.yaml contains unknown field {unknown[0]!r}"
        )
    deployment = data.get("deployment")
    modes = deployment.get("modes") if isinstance(deployment, dict) else None
    if isinstance(modes, list) and "container" in modes:
        raise UnimplementedDeploymentModeError(
            "package.yaml declares deployment mode 'container', which is refused until a "
            "generated product implements container delivery"
        )
    missing_identity = [field for field in ("name", "version") if not data.get(field)]
    if missing_identity:
        raise MissingPackageIdentityError(
            f"package.yaml is missing required identity field {missing_identity[0]!r}"
        )
    try:
        return PackageManifest.model_validate(data)
    except ValidationError as error:
        if any(item["type"] == "extra_forbidden" for item in error.errors()):
            raise UnknownPackageManifestFieldError(
                "package.yaml contains an unknown nested field"
            ) from error
        if any(item["loc"] == ("http", "prefix") for item in error.errors()):
            raise MalformedPackagePrefixError("package.yaml has malformed HTTP prefix") from error
        raise PackageManifestError(str(error)) from error


def load_package_manifest(path: Path) -> PackageManifest:
    """Load and validate a package.yaml file."""

    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as error:
        raise PackageManifestError(f"cannot read {path}: {error}") from error
    return parse_package_manifest(data)


def lint_package_imports(
    module_path: Path,
    manifest: PackageManifest,
    *,
    module_root: str | None = None,
) -> list[str]:
    """Return imports outside stdlib, the public core API, and declared dependencies."""

    own_module = (module_root or module_path.name).partition(".")[0]
    source_files, display_root = _resolve_source_files(module_path)
    if not source_files:
        return [f"package sources could not be located for module {own_module!r}"]
    allowed = set(sys.stdlib_module_names) | {
        "codegen_kit",
        *manifest.package_dependencies,
        own_module,
    }
    violations: list[str] = []
    for source in source_files:
        tree = ast.parse(source.read_text(), filename=str(source))
        for node in ast.walk(tree):
            imported: list[str] = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported = [node.module]
            for name in imported:
                top_level = name.split(".", 1)[0]
                if top_level not in allowed:
                    relative = source.relative_to(display_root)
                    violations.append(f"{relative}:{node.lineno}: forbidden import {name!r}")
    return violations


def _resolve_source_files(module_path: Path) -> tuple[tuple[Path, ...], Path]:
    """Resolve an entry-point module root to every concrete Python source scanned."""

    if module_path.is_dir():
        return tuple(sorted(module_path.rglob("*.py"))), module_path
    return (), module_path.parent
