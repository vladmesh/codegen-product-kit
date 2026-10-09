"""Package-declared timers enter the generated job contract and nothing else."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
import runpy
import shutil
import sys
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from framework.generate import generate_all
from framework.generators.jobs_manifest import JobsManifestGenerator
from framework.spec.loader import load_specs
from framework.spec.package_resolution import ActivePackage
from framework.spec.packages import load_package_manifest, parse_package_manifest

KIT_ROOT = Path(__file__).parents[2]
CHANNELS = KIT_ROOT / "packages/codegen-kit-tg-channels/codegen_kit_tg_channels"
META = "https://json-schema.org/draft/2020-12/schema"
AT_ONLY = {
    "type": "object",
    "properties": {"at": {"type": "string", "format": "date-time"}},
    "required": ["at"],
    "additionalProperties": False,
}


def _package(tmp_path: Path, name: str, *, timer_seconds: int | None = None) -> ActivePackage:
    manifest = parse_package_manifest(
        {
            "protocol_version": 1,
            "name": name,
            "version": "1.0.0",
            "requires_core": ">=2.1,<3",
            "http": {"prefix": f"/{name}"},
            "jobs_schema": {
                "$schema": META,
                "type": "object",
                "properties": {"refresh": AT_ONLY},
                "additionalProperties": False,
            },
            "timers": []
            if timer_seconds is None
            else [{"job": "refresh", "every_seconds": timer_seconds}],
        }
    )
    return ActivePackage(
        name=name,
        manifest=manifest,
        package_root=tmp_path / name,
        manifest_sha256="0" * 64,
    )


def _generate_jobs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, packages: list[ActivePackage]
) -> dict[str, Any]:
    repo = tmp_path / "repo"
    (repo / "shared/spec").mkdir(parents=True)
    (repo / "shared/spec/models.yaml").write_text(
        "models:\n  CoreMessage:\n    fields:\n      id: string\n"
    )
    (repo / "services/backend/src/generated").mkdir(parents=True)
    names = ", ".join(package.name for package in packages)
    (repo / "services/backend/manifest.yaml").write_text(
        f"version: 1\nsettings_schema: {{'$schema': '{META}', type: object, "
        f"properties: {{}}, additionalProperties: false}}\npackages: [{names}]\n"
    )
    monkeypatch.setattr(
        "framework.spec.loader.resolve_active_packages",
        lambda *_args, **_kwargs: packages,
    )

    generate_all(repo)

    return runpy.run_path(str(repo / "services/backend/src/generated/jobs_schemas.py"))


def test_active_package_timers_are_recorded_next_to_the_job_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    jobs = _generate_jobs(
        tmp_path,
        monkeypatch,
        [_package(tmp_path, "weather-kit", timer_seconds=300), _package(tmp_path, "digest")],
    )

    assert jobs["JOB_TIMERS"] == {"weather_kit.refresh": 300}
    assert jobs["JOB_SCHEMA_SOURCES"] == {
        "digest.refresh": "package:digest",
        "weather_kit.refresh": "package:weather-kit",
    }


@pytest.mark.parametrize("with_packages", [False, True], ids=["no-package", "no-timer"])
def test_a_product_without_declared_timers_generates_an_empty_timer_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, with_packages: bool
) -> None:
    packages = [_package(tmp_path, "weather")] if with_packages else []

    jobs = _generate_jobs(tmp_path, monkeypatch, packages)

    assert jobs["JOB_TIMERS"] == {}
    assert ("weather.refresh" in jobs["JOB_SCHEMAS"]) is with_packages


def test_the_template_ships_the_rendered_empty_timer_contract() -> None:
    shipped = Path(__file__).parents[2] / (
        "template/services/backend/src/generated/jobs_schemas.py"
    )

    assert runpy.run_path(str(shipped))["JOB_TIMERS"] == {}


def _channel_runtime(monkeypatch: pytest.MonkeyPatch) -> Any:
    """The package's own consumer module, with only its transport imports stubbed offline."""
    namespace = ModuleType("codegen_kit_tg_channels")
    namespace.__path__ = [str(CHANNELS)]  # type: ignore[attr-defined]
    for name, module in {
        "codegen_kit_tg_channels": namespace,
        "codegen_kit_tg_channels.polling": SimpleNamespace(Poller=object),
        "faststream": ModuleType("faststream"),
        "faststream.redis": SimpleNamespace(RedisBroker=object, StreamSub=object),
        "faststream.redis.parser": SimpleNamespace(BinaryMessageFormatV1=object()),
        "redis": ModuleType("redis"),
        "redis.exceptions": SimpleNamespace(ResponseError=Exception),
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    monkeypatch.delitem(sys.modules, "codegen_kit_tg_channels.runtime", raising=False)
    return importlib.import_module("codegen_kit_tg_channels.runtime")


def test_channel_consumer_runs_the_tick_the_core_generates_from_its_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (tg-channels 0.1.1): the consumer waited for a name the core never fires.

    The job name comes from the shipped package.yaml through the loader and the generated
    JOB_TIMERS, never from a string written here, and is handed to the package's consumer.
    """
    manifest = load_package_manifest(CHANNELS / "package.yaml")
    package = ActivePackage(
        name=manifest.name,
        manifest=manifest,
        package_root=CHANNELS,
        manifest_sha256="0" * 64,
    )
    # A product core: the template's shared specs declare the job_fired event the package
    # consumes; the backend manifest allowlists the package.
    repo = tmp_path / "product"
    (repo / "shared").mkdir(parents=True)
    shutil.copytree(KIT_ROOT / "template/shared/spec", repo / "shared/spec")
    (repo / "services/backend/src/generated").mkdir(parents=True)
    (repo / "services/backend/manifest.yaml").write_text(
        f"version: 1\nsettings_schema: {{'$schema': '{META}', type: object, "
        f"properties: {{}}, additionalProperties: false}}\npackages: [{manifest.name}]\n"
    )
    monkeypatch.setattr(
        "framework.spec.loader.resolve_active_packages", lambda *_args, **_kwargs: [package]
    )
    JobsManifestGenerator(load_specs(repo), repo).generate()
    jobs = runpy.run_path(str(repo / "services/backend/src/generated/jobs_schemas.py"))
    fired = [
        name
        for name in jobs["JOB_TIMERS"]
        if jobs["JOB_SCHEMA_SOURCES"][name] == f"package:{manifest.name}"
    ]
    assert len(fired) == 1, jobs["JOB_TIMERS"]
    runtime = _channel_runtime(monkeypatch)
    poller = SimpleNamespace(tick=AsyncMock())
    consumer = runtime.ChannelConsumer(poller)

    async def deliver(name: str) -> None:
        await consumer.handle_job(
            {"payload": {"name": name, "arguments": {"at": "2026-10-09T17:00:00Z"}}}
        )

    asyncio.run(deliver(fired[0]))
    poller.tick.assert_awaited_once()
    # A job the core fires for another owner is still not this package's tick.
    asyncio.run(deliver(fired[0].replace(".", "_other.", 1)))
    poller.tick.assert_awaited_once()
