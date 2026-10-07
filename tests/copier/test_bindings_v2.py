"""Synthetic bilingual package runs in a real Copier product."""

import os
from pathlib import Path
import subprocess
from types import SimpleNamespace

import pytest
import yaml

from framework.cli import bind_package
from tests.copier.test_bindings import (  # noqa: F401
    bound_product,
    bound_product_v2,
    bound_product_v2_only,
)

ROOT = Path(__file__).parents[2]


@pytest.mark.parametrize("locale", ["ru", "en"])
@pytest.mark.parametrize("fixture", ["bound_product_v2", "bound_product_v2_only"])
def test_v2_generated_handlers_and_event_delivery(request, fixture, locale):
    product = request.getfixturevalue(fixture)
    manifest = yaml.safe_load((product / "services/tg_bot/manifest.yaml").read_text())
    assert manifest["settings_schema"]["properties"]["language"] == {
        "type": "string",
        "enum": ["ru", "en"],
    }
    assert bind_package("binding-notes", product) == "unchanged"
    result = subprocess.run(
        [
            str(product / "services/tg_bot/.venv/bin/python"),
            str(ROOT / "tests/copier/binding_v2_scenarios.py"),
            locale,
        ],
        cwd=product,
        env=os.environ | {"PYTHONPATH": f"{product}:{product / 'shared'}"},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        f"v2 {locale}: commands, callbacks, settings, date display and relay passed"
        in result.stdout
    )


@pytest.mark.slow
@pytest.mark.parametrize("fixture", ["bound_product_v2", "bound_product_v2_only"])
def test_v2_bound_product_passes_make_typecheck(request, fixture):
    """CI only: retain the product's exact service loop and inspect hidden loop failures."""
    product = request.getfixturevalue(fixture)
    result = subprocess.run(
        ["make", "typecheck"],
        cwd=product,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert ">> Typechecking backend" in result.stdout and ">> Typechecking tg_bot" in result.stdout
    assert "error:" not in result.stdout + result.stderr


@pytest.mark.parametrize("fixture", ["bound_product_v2", "bound_product_v2_only"])
def test_v2_typecheck_dispatch_uses_selected_product(tmp_path, monkeypatch, fixture):
    """Exercise CI test routing without invoking make or creating service environments."""
    products = {name: tmp_path / name for name in ("bound_product_v2", "bound_product_v2_only")}
    for product in products.values():
        product.mkdir()
    selected = []
    invoked = []

    def resolve(name):
        selected.append(name)
        return products[name]

    def run(argv, *, cwd, capture_output, text):
        assert argv == ["make", "typecheck"]
        assert cwd == products[fixture] and cwd.is_dir()
        assert capture_output and text
        invoked.append(cwd)
        return subprocess.CompletedProcess(
            argv, 0, stdout=">> Typechecking backend\n>> Typechecking tg_bot\n", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", run)
    test_v2_bound_product_passes_make_typecheck(SimpleNamespace(getfixturevalue=resolve), fixture)
    assert selected == [fixture]
    assert invoked == [products[fixture]]


@pytest.mark.parametrize(
    "bad", ["reserved", "cross-command", "language-schema", "core", "date-zone"]
)
def test_v2_product_refusals_do_not_mutate_outputs(request, bad):
    from framework.bindings import BindingError
    from framework.generate import generate_all
    from tests.copier.test_bindings import _snapshot

    product = request.getfixturevalue("bound_product_v2")
    binding_file = product / "services/tg_bot/bindings/binding-notes.yaml"
    manifest_file = product / "services/tg_bot/manifest.yaml"
    core_file = product / "codegen_kit/packages.py"
    originals = {path: path.read_bytes() for path in (binding_file, manifest_file, core_file)}
    data = yaml.safe_load(binding_file.read_text())
    try:
        if bad == "reserved":
            data["commands"][0]["command"] = "start"
        elif bad == "cross-command":
            data["commands"][0]["command"] = "remind"
        elif bad == "language-schema":
            manifest = yaml.safe_load(manifest_file.read_text())
            manifest["settings_schema"]["properties"]["language"]["enum"] = ["en"]
            manifest_file.write_text(yaml.safe_dump(manifest, sort_keys=False))
        elif bad == "core":
            core_file.write_text(
                core_file.read_text().replace('CORE_VERSION = "2.4.0"', 'CORE_VERSION = "2.3.0"')
            )
        else:
            data["timezone"] = {
                "key": "different_zone",
                "scope": "product",
                "required": True,
                "format": "x-iana-tz",
            }
        binding_file.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False))
        before = _snapshot(product)
        with pytest.raises(BindingError):
            generate_all(product)
        assert _snapshot(product) == before
    finally:
        for path, content in originals.items():
            path.write_bytes(content)
