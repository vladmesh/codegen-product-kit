"""Unified spec loader with validation.

Loads all YAML specs and validates them using Pydantic models.
Provides clear error messages for spec violations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import ValidationError
import yaml

from framework.spec.events import EventSpec, EventsSpec, event_identifier
from framework.spec.manifests import ServiceManifest, parse_service_manifest
from framework.spec.models import ModelsSpec
from framework.spec.operations import DomainSpec, unwrap_list
from framework.spec.package_resolution import (
    ActivePackage,
    PackageResolutionError,
    resolve_active_packages,
)


class SpecValidationError(Exception):
    """Raised when spec validation fails."""

    def __init__(self, message: str, file_path: str | None = None) -> None:
        self.file_path = file_path
        self.message = message
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        if self.file_path:
            return f"{self.file_path}: {self.message}"
        return self.message


@dataclass
class AllSpecs:
    """Container for all loaded and validated specs."""

    models: ModelsSpec
    events: EventsSpec
    domains: dict[str, DomainSpec] = field(default_factory=dict)
    manifests: dict[str, ServiceManifest] = field(default_factory=dict)
    packages: list[ActivePackage] = field(default_factory=list)
    package_models: dict[str, dict[str, Any]] = field(default_factory=dict)
    settings_schemas: dict[str, object] = field(default_factory=dict)
    settings_schema_sources: dict[str, str] = field(default_factory=dict)
    job_schemas: dict[str, object] = field(default_factory=dict)
    job_schema_sources: dict[str, str] = field(default_factory=dict)


def load_yaml_file(file_path: Path) -> dict[str, Any]:
    """Load and parse a YAML file."""
    if not file_path.exists():
        raise SpecValidationError("File not found", str(file_path))

    try:
        with file_path.open() as f:
            data = yaml.safe_load(f)
            return data or {}
    except yaml.YAMLError as e:
        raise SpecValidationError(f"Invalid YAML syntax: {e}", str(file_path)) from e


def format_pydantic_error(error: ValidationError, context: str = "") -> str:
    """Format Pydantic validation error for human readability."""
    messages = []
    for err in error.errors():
        loc = ".".join(str(x) for x in err["loc"])
        msg = err["msg"]
        if context:
            messages.append(f"{context}.{loc}: {msg}")
        else:
            messages.append(f"{loc}: {msg}")
    return "\n".join(messages)


def load_models(models_file: Path) -> ModelsSpec:
    """Load and validate models.yaml."""
    data = load_yaml_file(models_file)

    try:
        return ModelsSpec.from_yaml(data)
    except ValidationError as e:
        raise SpecValidationError(format_pydantic_error(e, "models"), str(models_file)) from e
    except ValueError as e:
        raise SpecValidationError(str(e), str(models_file)) from e


def load_events(events_file: Path) -> EventsSpec:
    """Load and validate events.yaml."""
    if not events_file.exists():
        return EventsSpec(events=[])

    data = load_yaml_file(events_file)

    try:
        return EventsSpec.from_yaml(data)
    except ValidationError as e:
        raise SpecValidationError(format_pydantic_error(e, "events"), str(events_file)) from e
    except ValueError as e:
        raise SpecValidationError(str(e), str(events_file)) from e


def load_domain(domain_file: Path) -> DomainSpec:
    """Load and validate a domain spec."""
    data = load_yaml_file(domain_file)

    domain_name = domain_file.stem
    try:
        return DomainSpec.from_yaml(domain_name, data)
    except ValidationError as e:
        raise SpecValidationError(format_pydantic_error(e, "domain"), str(domain_file)) from e
    except ValueError as e:
        raise SpecValidationError(str(e), str(domain_file)) from e


def load_manifest(manifest_file: Path) -> ServiceManifest:
    """Load a service manifest without treating it as a domain spec."""
    data = load_yaml_file(manifest_file)
    try:
        return parse_service_manifest(data)
    except ValidationError as e:
        raise SpecValidationError(format_pydantic_error(e, "manifest"), str(manifest_file)) from e
    except ValueError as e:
        raise SpecValidationError(str(e), str(manifest_file)) from e


def extract_base_model(model_ref: str) -> str:
    """Extract base model name from a model reference.

    Handles:
        - "User" -> "User"
        - "list[User]" -> "User"
        - "List[User]" -> "User"
    """
    return unwrap_list(model_ref)[1]


def validate_model_references(
    models: ModelsSpec,
    domains: dict[str, DomainSpec],
    events: EventsSpec,
    package_models: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    """Validate that all model references exist.

    Returns list of error messages.
    """
    errors = []
    known_models = models.get_model_names() | set(package_models or {})

    # Check domains
    for domain_key, domain in domains.items():
        for op in domain.operations:
            if op.input_model:
                base_input = extract_base_model(op.input_model)
                if base_input not in known_models:
                    errors.append(
                        f"Domain '{domain_key}', operation '{op.name}': "
                        f"Unknown input model '{op.input_model}'"
                    )
            if op.output_model:
                base_output = extract_base_model(op.output_model)
                if base_output not in known_models:
                    errors.append(
                        f"Domain '{domain_key}', operation '{op.name}': "
                        f"Unknown output model '{op.output_model}'"
                    )

    # Check events
    for event in events.events:
        if event.message and event.message not in known_models:
            errors.append(f"Event '{event.name}': Unknown message model '{event.message}'")

    return errors


def _load_service_specs(services_dir: Path) -> dict[str, DomainSpec]:
    """Load domain specs from all services."""
    domains: dict[str, DomainSpec] = {}

    if not services_dir.exists():
        return domains

    for service_dir in services_dir.iterdir():
        if not service_dir.is_dir():
            continue

        spec_dir = service_dir / "spec"
        if not spec_dir.exists():
            continue

        service_name = service_dir.name

        # Legacy manifests are intentionally ignored.
        for spec_file in spec_dir.glob("*.yaml"):
            if spec_file.stem == "manifest":
                continue
            domain_key = f"{service_name}/{spec_file.stem}"
            domain = load_domain(spec_file)
            domain.service_name = service_name
            domains[domain_key] = domain

    return domains


def _load_service_manifests(services_dir: Path) -> dict[str, ServiceManifest]:
    """Load explicit service manifests from their dedicated top-level path."""
    manifests: dict[str, ServiceManifest] = {}
    if not services_dir.exists():
        return manifests

    for service_dir in services_dir.iterdir():
        if not service_dir.is_dir():
            continue
        manifest_file = service_dir / "manifest.yaml"
        if manifest_file.exists():
            manifests[service_dir.name] = load_manifest(manifest_file)
    return manifests


def _validate_unique_declarations(
    manifests: dict[str, ServiceManifest], field: str, label: str
) -> list[str]:
    """Reject a declared name claimed by more than one service manifest."""
    owners: dict[str, str] = {}
    errors: list[str] = []
    for service_name, manifest in manifests.items():
        for key in getattr(manifest, field)["properties"]:
            if key in owners:
                errors.append(
                    f"{label} '{key}' is declared by both '{owners[key]}' and '{service_name}'"
                )
            else:
                owners[key] = service_name
    return errors


def validate_manifest_settings(manifests: dict[str, ServiceManifest]) -> list[str]:
    """Reject settings keys declared by more than one service."""
    return _validate_unique_declarations(manifests, "settings_schema", "Setting")


def validate_manifest_jobs(manifests: dict[str, ServiceManifest]) -> list[str]:
    """Reject fireable job names declared by more than one service."""
    return _validate_unique_declarations(manifests, "jobs_schema", "Job")


def _package_prefix(name: str) -> str:
    """Return the stable identifier prefix used for package-owned names."""

    return name.replace("-", "_")


def _claim_name(
    label: str,
    name: str,
    owner: str,
    owners: dict[str, str],
    errors: list[str],
) -> bool:
    """Record one exclusive name or report both declarers."""

    previous = owners.get(name)
    if previous is not None:
        errors.append(f"{label} {name!r} is declared by both {previous!r} and {owner!r}")
        return False
    owners[name] = owner
    return True


def _merge_package_events(
    specs: AllSpecs,
    package: ActivePackage,
    event_owners: dict[str, str],
    event_identifier_owners: dict[str, str],
    event_bindings: dict[str, tuple[str, dict[str, Any] | None, str]],
    model_owners: dict[str, str],
    errors: list[str],
) -> None:
    """Merge one package's declarations; consumed names remain references."""

    owner = f"package:{package.name}"
    for event_name in package.manifest.events.publishes:
        message = package.manifest.events.messages[event_name]
        claimed = _claim_name("Event", event_name, owner, event_owners, errors)
        identifier_owner = f"{owner} event {event_name!r}"
        _claim_name(
            "Event identifier",
            event_identifier(event_name),
            identifier_owner,
            event_identifier_owners,
            errors,
        )
        if claimed:
            specs.events.events.append(
                EventSpec(name=event_name, message=message.model, publish=True)
            )
            event_bindings[event_name] = (message.model, message.schema_data, owner)
        previous_model = model_owners.get(message.model)
        if previous_model is not None and previous_model != owner:
            errors.append(
                f"Message schema {message.model!r} is declared by both "
                f"{previous_model!r} and {owner!r}"
            )
        elif previous_model is None:
            model_owners[message.model] = owner
            specs.package_models[message.model] = message.schema_data
        elif specs.package_models[message.model] != message.schema_data:
            errors.append(
                f"Message schema {message.model!r} has conflicting declarations in {owner!r}"
            )


def _validate_package_event_consumers(
    packages: list[ActivePackage],
    event_bindings: dict[str, tuple[str, dict[str, Any] | None, str]],
    errors: list[str],
) -> None:
    """Bind consumed event references to one existing published declaration."""

    for package in packages:
        consumer = f"package:{package.name}"
        for event_name in package.manifest.events.consumes:
            message = package.manifest.events.messages[event_name]
            binding = event_bindings.get(event_name)
            if binding is None:
                errors.append(
                    f"{consumer!r} consumes event {event_name!r}, but no active package or "
                    "shared/spec/events.yaml declaration publishes it"
                )
                continue
            declared_model, declared_schema, declarer = binding
            referenced_schema = message.schema_data
            comparable_schema = dict(declared_schema or {})
            if comparable_schema.get("title") == declared_model:
                comparable_schema.pop("title")
            if declared_model != message.model or comparable_schema != referenced_schema:
                errors.append(
                    f"Event {event_name!r} consumed by {consumer!r} binds message schema "
                    f"{message.model!r}, conflicting with {declared_model!r} declared by "
                    f"{declarer!r}"
                )


def _merge_package_names(
    specs: AllSpecs,
    package: ActivePackage,
    setting_owners: dict[str, str],
    job_owners: dict[str, str],
    database_owners: dict[str, str],
    environment_owners: dict[str, str],
    capability_owners: dict[str, str],
    errors: list[str],
) -> None:
    """Merge one package's non-event declarations into shared owner registries."""

    owner = f"package:{package.name}"
    prefix = _package_prefix(package.name)
    database = package.manifest.database
    if database is not None:
        _claim_name("Database schema", database.schema_name, owner, database_owners, errors)
    for requirement in package.manifest.requires:
        if requirement not in capability_owners:
            errors.append(f"{owner!r} requires unprovided capability {requirement!r}")
    for requirement in package.manifest.environment:
        _claim_name(
            "Environment variable",
            requirement.name,
            owner,
            environment_owners,
            errors,
        )
    for label, schema, owners in (
        ("Setting", package.manifest.settings_schema, setting_owners),
        ("Job", package.manifest.jobs_schema, job_owners),
    ):
        for local_name in schema["properties"]:
            name = f"{prefix}.{local_name}"
            if not _claim_name(label, name, owner, owners, errors):
                continue
            if label == "Setting":
                specs.settings_schemas[name] = schema["properties"][local_name]
                specs.settings_schema_sources[name] = owner
            else:
                specs.job_schemas[name] = schema["properties"][local_name]
                specs.job_schema_sources[name] = owner


def _validate_and_merge_packages(
    specs: AllSpecs,
) -> list[str]:
    """Merge package contracts once and return named ownership violations."""

    errors: list[str] = []
    setting_owners: dict[str, str] = {}
    job_owners: dict[str, str] = {}
    specs.settings_schemas = {}
    specs.settings_schema_sources = {}
    specs.job_schemas = {}
    specs.job_schema_sources = {}
    for service, manifest in sorted(specs.manifests.items()):
        for name, schema in sorted(manifest.settings_schema["properties"].items()):
            if _claim_name("Setting", name, service, setting_owners, errors):
                specs.settings_schemas[name] = schema
                specs.settings_schema_sources[name] = service
        for name, schema in sorted(manifest.jobs_schema["properties"].items()):
            if _claim_name("Job", name, service, job_owners, errors):
                specs.job_schemas[name] = schema
                specs.job_schema_sources[name] = service
    event_owners: dict[str, str] = {}
    event_identifier_owners: dict[str, str] = {}
    model_schemas = specs.models.to_json_schema()["definitions"]
    event_bindings: dict[str, tuple[str, dict[str, Any] | None, str]] = {}
    for event in specs.events.events:
        owner = "shared/spec/events.yaml"
        _claim_name("Event", event.name, owner, event_owners, errors)
        _claim_name(
            "Event identifier",
            event_identifier(event.name),
            f"{owner} event {event.name!r}",
            event_identifier_owners,
            errors,
        )
        event_bindings[event.name] = (event.message, model_schemas.get(event.message), owner)
    model_owners = dict.fromkeys(specs.models.get_model_names(), "shared/spec/models.yaml")
    database_owners: dict[str, str] = {}
    capability_owners = {
        capability: service
        for service, manifest in specs.manifests.items()
        for capability in manifest.provides
    }
    environment_owners: dict[str, str] = {}

    for package in specs.packages:
        owner = f"package:{package.name}"
        for capability in package.manifest.provides:
            _claim_name("Capability", capability, owner, capability_owners, errors)

    for package in specs.packages:
        _merge_package_names(
            specs,
            package,
            setting_owners,
            job_owners,
            database_owners,
            environment_owners,
            capability_owners,
            errors,
        )
        _merge_package_events(
            specs,
            package,
            event_owners,
            event_identifier_owners,
            event_bindings,
            model_owners,
            errors,
        )
    _validate_package_event_consumers(specs.packages, event_bindings, errors)
    return errors


def load_specs(repo_root: Path, package_site_packages: Path | None = None) -> AllSpecs:
    """Load and validate all specs from the repository.

    Args:
        repo_root: Path to the repository root

    Returns:
        AllSpecs containing validated models, events, and domains

    Raises:
        SpecValidationError: If any spec is invalid
    """
    services_dir = repo_root / "services"
    manifests = _load_service_manifests(services_dir)
    # 1. Load models (required)
    shared_spec_dir = repo_root / "shared" / "spec"
    models_file = shared_spec_dir / "models.yaml"

    if not models_file.exists():
        empty_specs = AllSpecs(
            models=ModelsSpec(models={}),
            events=EventsSpec(events=[]),
            manifests=manifests,
        )
        manifest_errors = _validate_and_merge_packages(empty_specs)
        if manifest_errors:
            raise SpecValidationError(
                "Package contract merge failed:\n"
                + "\n".join(f"  - {error}" for error in manifest_errors)
            )
        return empty_specs

    models = load_models(models_file)

    # 2. Load events (optional)
    events_file = shared_spec_dir / "events.yaml"
    events = load_events(events_file)

    # 3. Resolve the one active package set from the backend package environment.
    try:
        packages = resolve_active_packages(repo_root, manifests, package_site_packages)
    except (PackageResolutionError, ValueError) as error:
        raise SpecValidationError(f"Package resolution failed: {error}") from error

    merged = AllSpecs(
        models=models,
        events=events,
        manifests=manifests,
        packages=packages,
    )
    package_errors = _validate_and_merge_packages(merged)
    if package_errors:
        raise SpecValidationError(
            "Package contract merge failed:\n"
            + "\n".join(f"  - {error}" for error in package_errors)
        )

    # 4. Load service domains
    domains = _load_service_specs(services_dir)
    merged.domains = domains

    # 4. Explicit manifests are independent of ordinary domain generation.

    # 5. Cross-validate model references
    reference_errors = validate_model_references(models, domains, events, merged.package_models)
    if reference_errors:
        raise SpecValidationError(
            "Model reference validation failed:\n" + "\n".join(f"  - {e}" for e in reference_errors)
        )

    return merged


def validate_specs_cli(repo_root: Path) -> tuple[bool, str]:
    """CLI-friendly validation that returns success status and message.

    Returns:
        (success, message) tuple
    """
    try:
        specs = load_specs(repo_root)
        if not specs.models.models:
            return True, "No specs found. Skipping validation."
        model_count = len(specs.models.models)
        domain_count = len(specs.domains)
        event_count = len(specs.events.events)
        manifest_count = len(specs.manifests)
        return True, (
            f"Spec validation PASSED.\n"
            f"  Models: {model_count}\n"
            f"  Domains: {domain_count}\n"
            f"  Events: {event_count}\n"
            f"  Manifests: {manifest_count}"
        )
    except SpecValidationError as e:
        return False, f"Spec validation FAILED:\n  {e}"
