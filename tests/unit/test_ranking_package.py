"""Run the ranking package's offline tests in the ordinary kit unit lane."""

from pathlib import Path
import subprocess
import sys


def test_ranking_package_contracts_and_behaviour() -> None:
    root = Path(__file__).parents[2]
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pytest", "-q", "packages/codegen-kit-ranking/tests"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
