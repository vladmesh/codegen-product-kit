"""A generated product installs runtime packages built from its own source with its own kit."""

from __future__ import annotations

import json
from pathlib import Path
import runpy
import shutil
import tomllib

import pytest
import yaml

from tests.copier.test_two_product_package import _commit_baseline, _run

KIT_ROOT = Path(__file__).parents[2]
SYNTHETIC = KIT_ROOT / "tests/fixtures/synthetic_package"
CHANNELS = KIT_ROOT / "packages/codegen-kit-tg-channels"
GENERATED = (
    "codegen_kit/_active_packages.py",
    "services/backend/packages/env.contract.yaml",
    "services/backend/src/generated/",
    "services/tg_bot/src/generated/",
    "shared/shared/generated/",
)


def _entry_points(product: Path) -> list[str]:
    listed = _run(
        [
            str(product / "services/backend/.venv/bin/python"),
            "-c",
            "import json; from importlib import metadata; "
            "print(json.dumps(sorted(f'{ep.name}={ep.value}' "
            "for ep in metadata.entry_points(group='codegen_kit.packages'))))",
        ],
        product,
    )
    return json.loads(listed.stdout)


@pytest.mark.slow
def test_product_source_packages_install_bind_and_restore_from_committed_wheels(
    project_backend_tg_bot: Path, tmp_path: Path
) -> None:
    """CI only: real uv build, uv add/sync and generation through `kit add --package-source`."""
    product = tmp_path / "product"
    shutil.copytree(
        project_backend_tg_bot,
        product,
        ignore=shutil.ignore_patterns(".venv", "artifacts", "__pycache__"),
    )
    for source, target in ((SYNTHETIC, "synthetic"), (CHANNELS, "channels")):
        shutil.copytree(
            source, product / "packages" / target, ignore=shutil.ignore_patterns("__pycache__")
        )
    _run(["uv", "sync", "--frozen"], product)
    for service in ("backend", "tg_bot"):
        _run(["uv", "sync", "--project", f"services/{service}", "--frozen"], product)
    kit = str(product / ".venv/bin/kit")

    added = _run([kit, "add", "synthetic", "--package-source", "packages/synthetic"], product)
    assert added.stdout.strip().endswith(
        "kit: installed synthetic 2.0.0 (package source packages/synthetic)"
    )
    _run([kit, "add", "tg-channels", "--package-source", "packages/channels"], product)
    _run([kit, "add", "synthetic", "--package-source", "packages/synthetic"], product)
    _run([kit, "bind", "tg-channels", "--default"], product)

    manifest = yaml.safe_load((product / "services/backend/manifest.yaml").read_text())
    assert manifest["packages"] == ["synthetic", "tg-channels"]
    project = tomllib.loads((product / "services/backend/pyproject.toml").read_text())
    assert (
        project["tool"]["deptry"]["per_rule_ignores"]["DEP002"].count(
            "codegen-kit-synthetic-package"
        )
        == 1
    )
    assert sorted(path.name for path in (product / "services/backend/packages").glob("*.whl")) == [
        "codegen_kit_synthetic_package-2.0.0-py3-none-any.whl",
        "codegen_kit_tg_channels-0.2.0-py3-none-any.whl",
    ]
    settings = runpy.run_path(str(product / "services/backend/src/generated/settings_schemas.py"))
    assert settings["SETTINGS_SCHEMAS"]["synthetic.enabled"] == {"type": "boolean"}
    assert (product / "services/tg_bot/bindings/tg-channels.yaml").is_file()
    active = runpy.run_path(str(product / "codegen_kit/_active_packages.py"))
    assert [(item["name"], item["version"]) for item in active["ACTIVE_PACKAGES"]] == [
        ("synthetic", "2.0.0"),
        ("tg-channels", "0.2.0"),
    ]
    expected = [
        "synthetic=synthetic_package:package",
        "tg-channels=codegen_kit_tg_channels:package",
    ]
    assert _entry_points(product) == expected

    # A fresh checkout restores the backend from the committed wheels and lock alone.
    _commit_baseline(product)
    shutil.rmtree(product / "services/backend/.venv")
    for target in ("synthetic", "channels"):
        shutil.rmtree(product / "packages" / target)
    _run(["uv", "sync", "--project", "services/backend", "--frozen"], product)
    assert _entry_points(product) == expected
    _run(["make", "generate-from-spec"], product)
    _run(["git", "diff", "--exit-code", "--", *GENERATED], product)
