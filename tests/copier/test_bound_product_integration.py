"""CI-only published, bound products run their unchanged integration Make target."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest
import yaml


@pytest.mark.slow
@pytest.mark.parametrize("package", ["reminders", "tg-channels"])
def test_published_bound_product_passes_own_integration(
    project_backend_tg_bot: Path, tmp_path: Path, package: str
) -> None:
    # Never substitute local component sources for these independently published tags.
    assert os.environ.get("CI") == "true", "Published integration proof runs in CI only"
    requirement = os.environ["CODEGEN_TOOLING_REQUIREMENT"]
    assert " @ git+" in requirement
    candidate = requirement.rsplit("@", 1)[1]
    assert re.fullmatch(r"[0-9a-f]{40}", candidate)
    product = tmp_path / "product"
    shutil.copytree(project_backend_tg_bot, product)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONPATH", "VIRTUAL_ENV")
        and not key.startswith(("GIT_", "KIT_CATALOG_", "PLATFORM_"))
    }

    def run(command: list[str]) -> str:
        result = subprocess.run(
            command, cwd=product, env=environment, capture_output=True, text=True
        )
        assert result.returncode == 0, f"{command}\n{result.stdout}{result.stderr}"
        return result.stdout

    run(["make", "setup"])
    python = str(product / ".venv/bin/python")
    tooling = json.loads(
        run(
            [
                python,
                "-I",
                "-c",
                "from importlib.metadata import distribution; "
                "print(distribution('codegen-kit-tooling').read_text('direct_url.json'))",
            ]
        )
    )
    assert tooling["vcs_info"]["commit_id"] == candidate
    versions = {"reminders": "0.5.0", "textparse": "0.1.0", "tg-channels": "0.1.0"}
    components = ["textparse", package] if package == "reminders" else [package]
    for component in components:
        installed = run(
            [python, "-m", "framework.cli", "add", component, "--product-root", str(product)]
        )
        version = versions[component]
        assert f"{component} {version} (packages/{component}/v{version})" in installed
    run(
        [
            python,
            "-m",
            "framework.cli",
            "bind",
            package,
            "--default",
            "--product-root",
            str(product),
        ]
    )
    run(["make", "generate-from-spec"])
    binding = product / f"services/tg_bot/bindings/{package}.yaml"
    assert yaml.safe_load(binding.read_text())["package"] == package
    for name in ("bindings.py", "binding_relay.py"):
        assert (product / "services/tg_bot/src/generated" / name).is_file()
    serialized = "bindings.py" if package == "reminders" else "bindings_v1.py"
    assert package in (product / "services/tg_bot/src/generated" / serialized).read_text()
    active = (product / "codegen_kit/_active_packages.py").read_text()
    assert package in active and versions[package] in active

    env_file = (product / ".env.example").read_text()
    if package == "tg-channels":
        fragment = yaml.safe_load(
            (product / "services/backend/packages/env.contract.yaml").read_text()
        )
        entries = fragment["entries"]
        assert entries["PLATFORM_KEY"]["source"] == "platform_key"
        assert entries["PLATFORM_BASE_URL"]["source"] == "platform_base_url"
        assert entries["PLATFORM_KEY"]["service"] == "tg-reader"
        assert entries["PLATFORM_KEY"]["scopes"] == ["tg-reader:read"]
        assert entries["PLATFORM_KEY"]["quota"] == {
            "channels_max": 50,
            "requests_per_minute": 60,
            "resolve_per_day": 200,
        }
        assert entries["PLATFORM_BASE_URL"]["url"] == "https://platform.vladmesh.dev/tg-reader"
        # Startup validates explicit values but does not call the platform. The unchanged
        # product suite never adds a channel; timer polling has no subscriptions to fetch.
        # The reserved .invalid endpoint cannot reach a real platform even on a long run.
        env_file += "\nPLATFORM_KEY=integration-only-inert-key\n"
        env_file += "PLATFORM_BASE_URL=https://platform.invalid/tg-reader\n"
    (product / ".env").write_text(env_file)
    makefile = (product / "Makefile").read_text()
    assert re.search(r"^test-integration:\n", makefile, re.M)
    assert "--exit-code-from integration-tests" in makefile
    # No replacement pytest command, altered tests or alternate Compose stack.
    run(["make", "test-integration"])
