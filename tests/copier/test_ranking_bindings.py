"""tg-channels and ranking bound together: generated /top in RU and EN, drift and lint."""

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
def ranking_product(request, tmp_path_factory):
    source_product = request.getfixturevalue("bound_product")
    product = tmp_path_factory.mktemp("ranking-product") / "product"
    shutil.copytree(source_product, product, symlinks=True)
    result = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(product / "services/backend/.venv/bin/python"),
            str(ROOT / "packages/codegen-kit-tg-channels"),
            str(ROOT / "packages/codegen-kit-ranking"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    (product / "services/tg_bot/bindings/reminders.yaml").unlink()
    manifest = product / "services/backend/manifest.yaml"
    data = yaml.safe_load(manifest.read_text())
    data["packages"].extend(["tg-channels", "ranking"])
    manifest.write_text(yaml.safe_dump(data, sort_keys=False))
    assert bind_package("tg-channels", product) == "bound"
    assert bind_package("ranking", product) == "bound"
    return product


@pytest.mark.parametrize("locale", ["ru", "en"])
def test_generated_top_command_renders_ranked_posts(ranking_product, locale):
    result = subprocess.run(
        [
            str(ranking_product / "services/tg_bot/.venv/bin/python"),
            str(ROOT / "packages/codegen-kit-ranking/tests/binding_scenarios.py"),
            locale,
        ],
        cwd=ranking_product,
        env=os.environ | {"PYTHONPATH": f"{ranking_product}:{ranking_product / 'shared'}"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"ranking {locale}: /top, help, items and failures passed" in result.stdout


def test_bound_ranking_product_drift_and_lint(ranking_product, tmp_path):
    check_product_lint(
        SimpleNamespace(getfixturevalue=lambda fixture: ranking_product),
        "ranking_product",
        tmp_path,
    )


@pytest.mark.slow
def test_bound_ranking_product_typecheck(ranking_product):
    check_product_typecheck(
        SimpleNamespace(getfixturevalue=lambda fixture: ranking_product), "ranking_product"
    )
