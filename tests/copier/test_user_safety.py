"""Execute logging regressions and typechecks in every supported generated service shape."""

import os
from pathlib import Path
import subprocess

import pytest


@pytest.mark.parametrize("shape", ["backend", "standalone", "backend_tg_bot"])
def test_generated_user_safety_checks(shape: str, request: pytest.FixtureRequest) -> None:
    project: Path = request.getfixturevalue(f"project_{shape}")
    services = ["backend"] if shape == "backend" else ["tg_bot"]
    if shape == "backend_tg_bot":
        services.append("backend")
    for service in services:
        installed = subprocess.run(
            ["uv", "sync", "--project", f"services/{service}", "--frozen"],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert installed.returncode == 0, installed.stdout + installed.stderr
        result = subprocess.run(
            [
                f"services/{service}/.venv/bin/python",
                "-m",
                "pytest",
                f"services/{service}/tests/unit/test_token_logging.py",
                "-q",
            ],
            cwd=project,
            env={**os.environ, "PYTHONPATH": f"{project}:{project / 'shared'}"},
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    typecheck = subprocess.run(
        ["make", "typecheck"],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert typecheck.returncode == 0, typecheck.stdout + typecheck.stderr
