"""The real catalog package runs through generated bilingual commands and delivery."""

import os
from pathlib import Path
import shutil
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from framework.cli import bind_package
from tests.copier.test_bindings import (  # noqa: F401
    bound_product,
    test_bound_product_generation_passes_its_own_drift_and_lint as check_product_lint,
)
from tests.copier.test_bindings_v2 import (
    test_v2_bound_product_passes_make_typecheck as check_product_typecheck,
)

ROOT = Path(__file__).parents[2]


@pytest.fixture(scope="module")
def channel_product(request, tmp_path_factory):
    source_product = request.getfixturevalue("bound_product")
    product = tmp_path_factory.mktemp("channel-product") / "product"
    shutil.copytree(source_product, product, symlinks=True)
    result = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(product / "services/backend/.venv/bin/python"),
            str(ROOT / "packages/codegen-kit-tg-channels"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    (product / "services/tg_bot/bindings/reminders.yaml").unlink()
    manifest = product / "services/backend/manifest.yaml"
    data = yaml.safe_load(manifest.read_text())
    data["packages"].append("tg-channels")
    manifest.write_text(yaml.safe_dump(data, sort_keys=False))
    assert bind_package("tg-channels", product) == "bound"
    return product


def test_platform_entries_and_installed_binding_resources(channel_product):
    contract = yaml.safe_load(
        (channel_product / "services/backend/packages/env.contract.yaml").read_text()
    )
    # Compare to the package data, rather than teaching core/template a concrete service.
    source = yaml.safe_load(
        (ROOT / "packages/codegen-kit-tg-channels/codegen_kit_tg_channels/package.yaml").read_text()
    )
    assert bind_package("tg-channels", channel_product) == "unchanged"
    requirements = {item["name"]: item for item in source["environment"]}
    entries = contract["entries"]
    for name in ("PLATFORM_KEY", "PLATFORM_BASE_URL"):
        entry = entries[name]
        declared = requirements[name]["source"]
        assert entry["source"] == declared["kind"]
        assert entry["required"] is True
        for key, value in declared.items():
            if key != "kind":
                assert entry[key] == value
    assert entries["PLATFORM_KEY"]["sensitive"] is True
    assert entries["PLATFORM_BASE_URL"]["sensitive"] is False
    result = subprocess.run(
        [
            str(channel_product / "services/backend/.venv/bin/python"),
            "-I",
            "-c",
            "from importlib.metadata import distribution; "
            "print(distribution('codegen-kit-tg-channels').locate_file('codegen_kit_tg_channels'))",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    installed = Path(result.stdout.strip())
    source_root = ROOT / "packages/codegen-kit-tg-channels/codegen_kit_tg_channels"
    for relative in (
        "package.yaml",
        "bindings/default.yaml",
        "contracts/openapi.json",
        "migrations/env.py",
        "migrations/versions/0001_channels.py",
        "migrations/versions/0002_subscription_time.py",
    ):
        assert (installed / relative).read_bytes() == (source_root / relative).read_bytes()


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_generated_channel_commands_and_recipient_delivery(channel_product, locale):
    product = channel_product
    result = subprocess.run(
        [
            str(product / "services/tg_bot/.venv/bin/python"),
            str(ROOT / "packages/codegen-kit-tg-channels/tests/binding_scenarios.py"),
            locale,
        ],
        cwd=product,
        env=os.environ | {"PYTHONPATH": f"{product}:{product / 'shared'}"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"channels {locale}: commands, errors, callbacks and delivery passed" in result.stdout


def test_bound_channel_product_drift_and_lint(channel_product, tmp_path):
    check_product_lint(
        SimpleNamespace(getfixturevalue=lambda fixture: channel_product),
        "channel_product",
        tmp_path,
    )


@pytest.mark.slow
def test_bound_channel_product_typecheck(channel_product):
    check_product_typecheck(
        SimpleNamespace(getfixturevalue=lambda fixture: channel_product), "channel_product"
    )
