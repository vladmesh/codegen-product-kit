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
    publishes: bool = True,
    consumes: bool = False,
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
                "publishes": [event] if publishes else [],
                "consumes": [event] if consumes else [],
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


def test_normalized_event_identifier_collision_names_both_declarations(tmp_path: Path) -> None:
    package = _package(tmp_path, "weather", event="order.placed", model="PackageOrderPlaced")
    specs = _specs(tmp_path, [package])
    specs.events.events.append(EventSpec(name="order_placed", message="CoreMessage", publish=True))

    errors = _validate_and_merge_packages(specs)

    assert any(
        "Event identifier 'order_placed'" in error
        and "shared/spec/events.yaml event 'order_placed'" in error
        and "package:weather event 'order.placed'" in error
        for error in errors
    )


def test_package_consumes_existing_product_event_as_reference(tmp_path: Path) -> None:
    consumer = _package(
        tmp_path,
        "observer",
        event="core.ready",
        model="CoreMessage",
        publishes=False,
        consumes=True,
    )
    specs = _specs(tmp_path, [consumer])
    specs.events.events.append(EventSpec(name="core.ready", message="CoreMessage", publish=True))

    errors = _validate_and_merge_packages(specs)

    assert errors == []
    assert [event.name for event in specs.events.events] == ["core.ready"]
    assert "CoreMessage" not in specs.package_models


def test_package_consumes_event_published_by_another_package(tmp_path: Path) -> None:
    publisher = _package(tmp_path, "publisher", event="orders.created", model="OrderCreated")
    consumer = _package(
        tmp_path,
        "consumer",
        event="orders.created",
        model="OrderCreated",
        publishes=False,
        consumes=True,
    )
    specs = _specs(tmp_path, [consumer, publisher])

    errors = _validate_and_merge_packages(specs)

    assert errors == []
    assert [event.name for event in specs.events.events] == ["orders.created"]


def test_package_consumer_refuses_conflicting_message_binding(tmp_path: Path) -> None:
    publisher = _package(tmp_path, "publisher", event="orders.created", model="OrderCreated")
    consumer = _package(
        tmp_path,
        "consumer",
        event="orders.created",
        model="OtherOrderCreated",
        publishes=False,
        consumes=True,
    )

    errors = _validate_and_merge_packages(_specs(tmp_path, [publisher, consumer]))

    assert errors == [
        "Event 'orders.created' consumed by 'package:consumer' binds message schema "
        "'OtherOrderCreated', conflicting with 'OrderCreated' declared by 'package:publisher'"
    ]


def test_package_consumer_refuses_changed_schema_for_same_model(tmp_path: Path) -> None:
    publisher = _package(tmp_path, "publisher", event="orders.created", model="OrderCreated")
    consumer = _package(
        tmp_path,
        "consumer",
        event="orders.created",
        model="OrderCreated",
        publishes=False,
        consumes=True,
    )
    consumer.manifest.events.messages["orders.created"].schema_data["properties"] = {
        "sequence": {"type": "integer"}
    }

    errors = _validate_and_merge_packages(_specs(tmp_path, [publisher, consumer]))

    assert errors == [
        "Event 'orders.created' consumed by 'package:consumer' binds message schema "
        "'OrderCreated', conflicting with 'OrderCreated' declared by 'package:publisher'"
    ]


def test_package_consumer_refuses_event_without_publisher(tmp_path: Path) -> None:
    consumer = _package(
        tmp_path,
        "consumer",
        event="orders.missing",
        model="MissingOrder",
        publishes=False,
        consumes=True,
    )

    errors = _validate_and_merge_packages(_specs(tmp_path, [consumer]))

    assert errors == [
        "'package:consumer' consumes event 'orders.missing', but no active package or "
        "shared/spec/events.yaml declaration publishes it"
    ]
