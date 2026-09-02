"""Tests for the generated runtime registry of fireable jobs."""

from __future__ import annotations

from pathlib import Path

import pytest

from framework.generate import generate_all

DECLARED_JOB = (
    "version: 1\nsettings_schema:\n"
    "  $schema: https://json-schema.org/draft/2020-12/schema\n"
    "  type: object\n  properties: {}\n  additionalProperties: false\n"
    "jobs_schema:\n"
    "  $schema: https://json-schema.org/draft/2020-12/schema\n"
    "  type: object\n"
    "  properties:\n"
    "    friday_digest:\n"
    "      type: object\n"
    "      properties: {week: {type: integer}}\n"
    "      additionalProperties: false\n"
    "  additionalProperties: false\n"
)


@pytest.fixture
def fake_repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "services").mkdir(parents=True)
    return root


def _write_base_specs(root: Path) -> None:
    (root / "shared" / "spec").mkdir(parents=True)
    (root / "shared" / "spec" / "models.yaml").write_text(
        "models:\n  Example:\n    fields:\n      id: int\n"
    )
    (root / "services" / "backend" / "spec").mkdir(parents=True)
    (root / "services" / "backend" / "src" / "generated").mkdir(parents=True)


def test_jobs_generator_emits_declared_jobs_and_declared_providers(fake_repo: Path) -> None:
    _write_base_specs(fake_repo)
    (fake_repo / "services" / "backend" / "manifest.yaml").write_text(
        DECLARED_JOB + 'provides: ["jobs.fire"]\n'
    )

    generate_all(fake_repo)

    generated = (
        fake_repo / "services" / "backend" / "src" / "generated" / "jobs_schemas.py"
    ).read_text()
    assert '"friday_digest"' in generated
    assert '"jobs.fire"' in generated
    assert "JOB_CAPABILITY_PROVIDERS" in generated


def test_a_manifest_without_jobs_generates_an_empty_registry(fake_repo: Path) -> None:
    _write_base_specs(fake_repo)
    (fake_repo / "services" / "backend" / "manifest.yaml").write_text(
        "version: 1\nsettings_schema:\n"
        "  $schema: https://json-schema.org/draft/2020-12/schema\n"
        "  type: object\n  properties: {}\n  additionalProperties: false\n"
    )

    generate_all(fake_repo)

    generated = (
        fake_repo / "services" / "backend" / "src" / "generated" / "jobs_schemas.py"
    ).read_text()
    assert "JOB_SCHEMAS: dict[str, object] = {}" in generated
    assert "JOB_CAPABILITY_PROVIDERS: dict[str, list[str]] = {}" in generated


def test_jobs_generator_never_accepts_a_name_declared_twice(fake_repo: Path) -> None:
    _write_base_specs(fake_repo)
    (fake_repo / "services" / "other" / "spec").mkdir(parents=True)
    for service in ("backend", "other"):
        (fake_repo / "services" / service / "manifest.yaml").write_text(DECLARED_JOB)

    with pytest.raises(SystemExit):
        generate_all(fake_repo)
