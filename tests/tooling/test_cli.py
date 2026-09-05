"""Tests for whole-recipe product package installation."""

from pathlib import Path

import pytest
import yaml

from framework import cli


def _product(root: Path) -> None:
    backend = root / "services/backend"
    backend.mkdir(parents=True)
    (backend / "pyproject.toml").write_text(
        "[project]\nname = 'backend'\n\n[tool.deptry.per_rule_ignores]\nDEP002 = [\"uvicorn\"]\n"
    )
    (backend / "manifest.yaml").write_text("version: 1\npackages: []\n")


def test_add_reminders_runs_the_complete_recipe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _product(tmp_path)
    artifact = tmp_path.parent / "codegen_kit_reminders-0.1.0-py3-none-any.whl"
    artifact.write_bytes(b"wheel")
    commands: list[list[str]] = []
    generated: list[Path] = []
    monkeypatch.setattr(cli, "_run", lambda command, _root: commands.append(command))
    monkeypatch.setattr(cli, "generate_all", lambda root: generated.append(root))

    cli.add_package("reminders", artifact, tmp_path)

    copied = tmp_path / "services/backend/packages" / artifact.name
    assert copied.read_bytes() == b"wheel"
    assert (
        'DEP002 = ["uvicorn", "codegen-kit-reminders"]'
        in (tmp_path / "services/backend/pyproject.toml").read_text()
    )
    assert commands == [
        [
            "uv",
            "add",
            "--project",
            "services/backend",
            "--no-sync",
            f"services/backend/packages/{artifact.name}",
        ],
        ["uv", "sync", "--project", "services/backend", "--frozen"],
    ]
    assert yaml.safe_load((tmp_path / "services/backend/manifest.yaml").read_text())[
        "packages"
    ] == ["reminders"]
    assert generated == [tmp_path]


def test_add_rejects_an_unknown_or_wrong_artifact(tmp_path: Path) -> None:
    _product(tmp_path)
    wrong = tmp_path / "other-1.0-py3-none-any.whl"
    wrong.write_bytes(b"wheel")

    with pytest.raises(ValueError, match="unknown package"):
        cli.add_package("other", wrong, tmp_path)
    with pytest.raises(ValueError, match="not codegen-kit-reminders"):
        cli.add_package("reminders", wrong, tmp_path)
