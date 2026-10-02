"""Package-declared timers enter the generated job contract and nothing else."""

from __future__ import annotations

from pathlib import Path
import runpy
from typing import Any

import pytest

from framework.generate import generate_all
from framework.spec.package_resolution import ActivePackage
from framework.spec.packages import parse_package_manifest

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
