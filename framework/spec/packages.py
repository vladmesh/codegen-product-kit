"""Package protocol v1 manifest validation."""

from __future__ import annotations

import ast
from pathlib import Path
import sys
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, field_validator
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
    """Package-owned database declarations, enforced by the next card."""

    schema_name: str = Field(alias="schema")
    migrations: str
    model_config = {"extra": "forbid", "populate_by_name": True}


class EventsDeclaration(BaseModel):
    """Package event names, merged by the next card."""

    publishes: list[str] = Field(default_factory=list)
    consumes: list[str] = Field(default_factory=list)
    model_config = {"extra": "forbid"}


class EnvironmentDeclaration(BaseModel):
    """One environment variable required by the package."""

    name: str
    required: bool = True
    model_config = {"extra": "forbid"}


class ResourceDeclaration(BaseModel):
    """One package-owned resource included in its distribution."""

    name: str
    path: str
    model_config = {"extra": "forbid"}


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
    environment: list[EnvironmentDeclaration] = Field(default_factory=list)
    resources: list[ResourceDeclaration] = Field(default_factory=list)

    model_config = {"extra": "forbid"}

    @field_validator("settings_schema")
    @classmethod
    def validate_settings_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        return _validate_declaration_schema(schema, "settings_schema")

    @field_validator("jobs_schema")
    @classmethod
    def validate_jobs_schema(cls, schema: dict[str, Any]) -> dict[str, Any]:
        return _validate_declaration_schema(schema, "jobs_schema")


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


def lint_package_imports(source_root: Path, manifest: PackageManifest) -> list[str]:
    """Return imports outside stdlib, the public core API, and declared dependencies."""

    own_modules = {path.name for path in source_root.iterdir() if path.is_dir()}
    own_modules.update(path.stem for path in source_root.glob("*.py"))
    allowed = set(sys.stdlib_module_names) | {
        "codegen_kit",
        *manifest.package_dependencies,
        *own_modules,
    }
    violations: list[str] = []
    for source in sorted(source_root.rglob("*.py")):
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
                    relative = source.relative_to(source_root)
                    violations.append(f"{relative}:{node.lineno}: forbidden import {name!r}")
    return violations
