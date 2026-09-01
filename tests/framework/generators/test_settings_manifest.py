"""Tests for generated runtime settings-schema registries."""

from __future__ import annotations

from pathlib import Path

import pytest

from framework.generate import generate_all


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


def test_manifest_generator_emits_declared_settings(fake_repo: Path) -> None:
    _write_base_specs(fake_repo)
    (fake_repo / "services" / "backend" / "manifest.yaml").write_text(
        "version: 1\nsettings_schema:\n"
        "  $schema: https://json-schema.org/draft/2020-12/schema\n"
        "  type: object\n"
        "  properties:\n    languages:\n      type: array\n      items: {type: string}\n"
        "  additionalProperties: false\n"
    )

    generate_all(fake_repo)

    generated = (fake_repo / "services" / "backend" / "src" / "generated" / "settings_schemas.py")
    assert "SETTINGS_SCHEMAS" in generated.read_text()
    assert '"languages"' in generated.read_text()


def test_manifest_generator_never_accepts_duplicate_keys(fake_repo: Path) -> None:
    _write_base_specs(fake_repo)
    (fake_repo / "services" / "other" / "spec").mkdir(parents=True)
    for service in ("backend", "other"):
        (fake_repo / "services" / service / "manifest.yaml").write_text(
            "version: 1\nsettings_schema:\n"
            "  $schema: https://json-schema.org/draft/2020-12/schema\n"
            "  type: object\n  properties: {languages: {type: array}}\n"
            "  additionalProperties: false\n"
        )

    with pytest.raises(SystemExit):
        generate_all(fake_repo)
