"""Package contributions enter the same generated product contracts as services."""

from __future__ import annotations

from pathlib import Path

import pytest

from framework.generate import generate_all
from framework.spec.events import EventSpec, EventsSpec
from framework.spec.loader import AllSpecs, _validate_and_merge_packages
from framework.spec.manifests import parse_service_manifest
from framework.spec.models import ModelsSpec
from framework.spec.package_resolution import ActivePackage
from framework.spec.packages import DatabaseDeclaration, parse_package_manifest

META = "https://json-schema.org/draft/2020-12/schema"


def _declarations(properties: dict[str, object]) -> dict[str, object]:
    return {
        "$schema": META,
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
    }


def _package(
    tmp_path: Path,
    name: str,
    *,
    setting: str = "enabled",
    job: str = "refresh",
    event: str = "package_ready",
    model: str = "PackageReady",
) -> ActivePackage:
    manifest = parse_package_manifest(
        {
            "protocol_version": 1,
            "name": name,
            "version": "1.0.0",
            "requires_core": ">=1,<2",
            "environment": [{"name": f"{name.replace('-', '_').upper()}_TOKEN"}],
            "http": {"prefix": f"/{name}"},
            "events": {
                "publishes": [event],
                "messages": {
                    event: {
                        "model": model,
                        "schema": {
                            "type": "object",
                            "properties": {"id": {"type": "string"}},
                            "required": ["id"],
                            "additionalProperties": False,
                        },
                    }
                },
            },
            "settings_schema": _declarations({setting: {"type": "boolean"}}),
            "jobs_schema": _declarations(
                {
                    job: {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    }
                }
            ),
        }
    )
    return ActivePackage(
        name=name,
        manifest=manifest,
        package_root=tmp_path / name,
        manifest_sha256="0" * 64,
    )


def _specs(tmp_path: Path, packages: list[ActivePackage]) -> AllSpecs:
    return AllSpecs(
        models=ModelsSpec.from_yaml({"models": {"CoreMessage": {"fields": {"id": "string"}}}}),
        events=EventsSpec(),
        packages=packages,
    )


def test_package_settings_jobs_events_and_messages_are_generated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    (repo / "shared/spec").mkdir(parents=True)
    (repo / "shared/spec/models.yaml").write_text(
        "models:\n  CoreMessage:\n    fields:\n      id: string\n"
    )
    (repo / "services/backend/src/generated").mkdir(parents=True)
    (repo / "services/backend/manifest.yaml").write_text(
        f"version: 1\nsettings_schema: {{'$schema': '{META}', type: object, "
        "properties: {}, additionalProperties: false}\npackages: [weather]\n"
    )
    package = _package(tmp_path, "weather")
    monkeypatch.setattr(
        "framework.spec.loader.resolve_active_packages",
        lambda *_args, **_kwargs: [package],
    )

    generate_all(repo)

    settings = (repo / "services/backend/src/generated/settings_schemas.py").read_text()
    jobs = (repo / "services/backend/src/generated/jobs_schemas.py").read_text()
    events = (repo / "shared/shared/generated/events.py").read_text()
    schemas = (repo / "shared/shared/generated/schemas.py").read_text()
    active = (repo / "codegen_kit/_active_packages.py").read_text()
    environment = (repo / "services/backend/packages/env.contract.yaml").read_text()
    assert "weather.enabled" in settings
    assert "weather.refresh" in jobs
    assert "publish_package_ready" in events
    assert "class PackageReady" in schemas
    assert '"name": "weather"' in active
    assert "WEATHER_TOKEN" in environment
    assert "owner: packages" in environment


def test_normalized_package_prefix_collisions_name_both_packages(tmp_path: Path) -> None:
    specs = _specs(
        tmp_path,
        [
            _package(tmp_path, "weather-kit", event="first_ready", model="FirstReady"),
            _package(tmp_path, "weather_kit", event="second_ready", model="SecondReady"),
        ],
    )

    errors = _validate_and_merge_packages(specs)

    assert any(
        "weather_kit.enabled" in error
        and "package:weather-kit" in error
        and "package:weather_kit" in error
        for error in errors
    )
    assert any("weather_kit.refresh" in error for error in errors)


def test_packages_cannot_share_a_database_schema(tmp_path: Path) -> None:
    first = _package(tmp_path, "first", event="first_ready", model="FirstReady")
    second = _package(tmp_path, "second", event="second_ready", model="SecondReady")
    first.manifest.database = DatabaseDeclaration(schema="shared", migrations="first:migrations")
    second.manifest.database = DatabaseDeclaration(schema="shared", migrations="second:migrations")

    errors = _validate_and_merge_packages(_specs(tmp_path, [first, second]))

    assert errors == [
        "Database schema 'shared' is declared by both 'package:first' and 'package:second'"
    ]


def test_package_service_and_event_schema_collisions_name_both_sides(tmp_path: Path) -> None:
    package = _package(tmp_path, "weather")
    specs = _specs(tmp_path, [package])
    specs.models = ModelsSpec.from_yaml(
        {
            "models": {
                "CoreMessage": {"fields": {"id": "string"}},
                "PackageReady": {"fields": {"id": "string"}},
            }
        }
    )
    specs.manifests["backend"] = parse_service_manifest(
        {
            "version": 1,
            "settings_schema": _declarations({"weather.enabled": {"type": "boolean"}}),
            "jobs_schema": _declarations(
                {
                    "weather.refresh": {
                        "type": "object",
                        "properties": {},
                        "additionalProperties": False,
                    }
                }
            ),
        }
    )
    specs.events.events.append(
        EventSpec(name="package_ready", message="PackageReady", publish=True)
    )

    errors = _validate_and_merge_packages(specs)

    assert any("Setting 'weather.enabled'" in error and "backend" in error for error in errors)
    assert any("Job 'weather.refresh'" in error and "backend" in error for error in errors)
    assert any(
        "Event 'package_ready'" in error and "shared/spec/events.yaml" in error for error in errors
    )
    assert any("Message schema 'PackageReady'" in error for error in errors)
