"""Tests for generated typed environment contract fragments."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

from jsonschema import Draft202012Validator
import pytest
import yaml

from tests.copier.conftest import VENV_COPIER

REPO_ROOT = Path(__file__).parent.parent.parent
SCHEMA = json.loads((REPO_ROOT / "tests/fixtures/env-contract.schema.json").read_text())
VALIDATOR = Draft202012Validator(SCHEMA)


def env_keys(path: Path) -> set[str]:
    """Return documented variables, excluding comments and blank lines."""
    return {
        line.partition("=")[0]
        for line in path.read_text().splitlines()
        if line and not line.startswith("#")
    }


def env_values(path: Path) -> dict[str, str]:
    """Return non-commented environment values keyed by variable name."""
    return {
        key: value
        for line in path.read_text().splitlines()
        if line and not line.startswith("#")
        for key, _, value in [line.partition("=")]
    }


def fragments(project: Path) -> list[tuple[Path, dict[str, object]]]:
    """Load every generated environment contract fragment."""
    return [
        (path, yaml.safe_load(path.read_text()))
        for path in sorted(project.rglob("env.contract.yaml"))
    ]


@pytest.mark.parametrize(
    "fixture_name",
    [
        "project_backend",
        "project_standalone",
        "project_notifications",
        "project_frontend",
        "project_backend_tg_bot",
        "project_fullstack",
    ],
)
def test_env_contract_fragments_match_vendored_schema(
    request: pytest.FixtureRequest, fixture_name: str
) -> None:
    """Every generated fragment validates without reaching the codegen repository."""
    project = request.getfixturevalue(fixture_name)

    for path, fragment in fragments(project):
        errors = sorted(VALIDATOR.iter_errors(fragment), key=str)
        assert not errors, f"{path.relative_to(project)}: {errors[0].message}"


@pytest.mark.parametrize(
    "fixture_name",
    [
        "project_backend",
        "project_standalone",
        "project_notifications",
        "project_frontend",
        "project_backend_tg_bot",
        "project_fullstack",
    ],
)
def test_env_contract_covers_env_example(request: pytest.FixtureRequest, fixture_name: str) -> None:
    """All documented generated-project variables have one typed declaration."""
    project = request.getfixturevalue(fixture_name)
    declared: dict[str, dict[str, object]] = {}

    for path, fragment in fragments(project):
        for key, entry in fragment["entries"].items():
            assert key not in declared, f"{key} is declared twice, including {path}"
            declared[key] = entry

    assert env_keys(project / ".env.example") <= set(declared)


def test_backend_contract_classifies_infrastructure_values(project_backend: Path) -> None:
    """Baseline types distinguish allocations, generated credentials and local ports."""
    entries = {
        key: entry
        for _, fragment in fragments(project_backend)
        for key, entry in fragment["entries"].items()
    }

    assert entries["BACKEND_PORT"]["source"] == "allocation"
    assert entries["BACKEND_PORT"]["service"] == "backend"
    assert entries["POSTGRES_HOST_PORT"] == {
        "source": "literal",
        "environments": ["local"],
        "required": True,
        "value": 5432,
    }
    assert entries["REDIS_HOST_PORT"] == {
        "source": "literal",
        "environments": ["local"],
        "required": True,
        "value": 6379,
    }
    for key in (
        "APP_SECRET_KEY",
        "POSTGRES_PASSWORD",
        "USERS_GRANT_CAPABILITY",
        "SETTINGS_WRITE_CAPABILITY",
    ):
        assert entries[key]["source"] == "generated_secret"
    assert entries["REDIS_URL"] == {
        "source": "literal",
        "environments": ["local", "production"],
        "consumers": ["backend"],
        "required": True,
        "value": "redis://redis:6379",
    }
    assert entries["APP_ENV"]["source"] == "derived"
    assert entries["BACKEND_IMAGE"]["source"] == "derived"


def test_required_local_contract_values_are_present_for_documented_backend_startup(
    project_backend_tg_bot: Path,
) -> None:
    """A copied .env.example supplies every required local contract value."""
    entries = {
        key: entry
        for _, fragment in fragments(project_backend_tg_bot)
        for key, entry in fragment["entries"].items()
    }
    local_required = {
        key
        for key, entry in entries.items()
        if entry["required"] and "local" in entry["environments"]
    }
    documented = env_values(project_backend_tg_bot / ".env.example")

    assert local_required <= set(documented)
    assert all(documented[key] for key in local_required)
    assert documented["USERS_GRANT_CAPABILITY"] == "local-grant-capability-not-for-production"
    assert documented["SETTINGS_WRITE_CAPABILITY"] == "local-settings-capability-not-for-production"


@pytest.mark.parametrize(
    "fixture_name",
    [
        "project_backend",
        "project_standalone",
        "project_notifications",
        "project_frontend",
        "project_backend_tg_bot",
        "project_fullstack",
    ],
)
def test_env_contract_gate_accepts_baseline_fragments(
    request: pytest.FixtureRequest, fixture_name: str
) -> None:
    """Each generated module combination passes the vendored offline gate."""
    project = request.getfixturevalue(fixture_name)
    artifact = project / "artifacts" / "env-contract-test.json"

    result = subprocess.run(
        [
            str(VENV_COPIER.parent / "python"),
            "-m",
            "framework.contracts.env_usage",
            "--root",
            ".",
            "--artifact",
            "artifacts/env-contract-test.json",
            "--commit-sha",
            "test-sha",
        ],
        cwd=project,
        env={**os.environ, "PYTHONPATH": ".framework"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(artifact.read_text())["commit_sha"] == "test-sha"


def test_env_contract_gate_rejects_undeclared_key(project_backend: Path) -> None:
    """A static environment read without a declaration makes the gate fail."""
    source = project_backend / "undeclared_env.py"
    source.write_text('import os\nos.getenv("UNDECLARED_ENV_KEY")\n')

    result = subprocess.run(
        [
            str(VENV_COPIER.parent / "python"),
            "-m",
            "framework.contracts.env_usage",
            "--root",
            ".",
            "--artifact",
            "artifacts/env-contract-test.json",
            "--commit-sha",
            "test-sha",
        ],
        cwd=project_backend,
        env={**os.environ, "PYTHONPATH": ".framework"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 1
    assert "undeclared environment key UNDECLARED_ENV_KEY" in result.stderr


def test_ci_publishes_commit_bound_environment_contract(project_backend: Path) -> None:
    """The generated CI uses the vendored offline gate and uploads its output."""
    workflow = (project_backend / ".github" / "workflows" / "ci.yml").read_text()

    assert "-m framework.contracts.env_usage" in workflow
    assert '--commit-sha "${{ github.sha }}"' in workflow
    assert "actions/upload-artifact@v4" in workflow
    assert "env-contract-${{ github.sha }}" in workflow
    assert (project_backend / ".framework" / "framework" / "contracts" / "env_usage.py").exists()
