"""The shipped declaration the kit, the product and the platform read."""

from __future__ import annotations

import asyncio
from pathlib import Path
import tomllib
from typing import Any

import codegen_kit_ranking
from conftest import CONFIRMED_KEYWORDS, CONFIRMED_WEIGHTS, KEYWORD_WEIGHTS, SettingsTable
from jsonschema import Draft202012Validator
import pytest
import yaml

MODULE_ROOT = Path(codegen_kit_ranking.__file__).parent
PROJECT_ROOT = MODULE_ROOT.parent


@pytest.fixture(scope="module")
def manifest() -> dict[str, Any]:
    return yaml.safe_load((MODULE_ROOT / "package.yaml").read_text(encoding="utf-8"))


def _validator(manifest: dict[str, Any], key: str) -> Draft202012Validator:
    return Draft202012Validator(manifest["settings_schema"]["properties"][key])


def test_seeded_settings_declare_the_owner_approved_defaults(manifest: dict[str, Any]) -> None:
    properties = manifest["settings_schema"]["properties"]
    seeds = {(seed["key"], seed["scope"]) for seed in manifest["setting_seeds"]}

    assert seeds == {(name, "product") for name in properties}
    assert {name: schema["default"] for name, schema in properties.items()} == {
        "weights": CONFIRMED_WEIGHTS,
        "keywords": [],
        "top_count": 10,
    }


def test_the_declared_defaults_seed_the_package_and_are_valid(manifest: dict[str, Any]) -> None:
    table = SettingsTable()

    async def write() -> None:
        for name, schema in manifest["settings_schema"]["properties"].items():
            _validator(manifest, name).validate(schema["default"])
            await codegen_kit_ranking.package.seed_setting(
                table, f"ranking.{name}", schema["default"]
            )

    asyncio.run(write())
    assert table.values() == {"weights": CONFIRMED_WEIGHTS, "keywords": [], "top_count": 10}


def test_the_settings_schema_accepts_reconfiguration(manifest: dict[str, Any]) -> None:
    _validator(manifest, "weights").validate(KEYWORD_WEIGHTS)
    _validator(manifest, "keywords").validate(list(CONFIRMED_KEYWORDS))
    _validator(manifest, "keywords").validate(["k" * 100, *(f"k{i}" for i in range(49))])
    for count in (1, 50):
        _validator(manifest, "top_count").validate(count)


@pytest.mark.parametrize(
    ("key", "rejected"),
    [
        ("weights", {**CONFIRMED_WEIGHTS, "shares": 10}),
        ("weights", {**CONFIRMED_WEIGHTS, "views": -1}),
        ("weights", {**CONFIRMED_WEIGHTS, "views": 101}),
        ("weights", {key: value for key, value in CONFIRMED_WEIGHTS.items() if key != "keywords"}),
        ("keywords", ["ИИ", "ИИ"]),
        ("keywords", [""]),
        ("keywords", ["k" * 101]),
        ("keywords", [f"k{i}" for i in range(51)]),
        ("top_count", 0),
        ("top_count", 51),
        ("top_count", 2.5),
    ],
)
def test_the_settings_schema_refuses_values_outside_its_bounds(
    manifest: dict[str, Any], key: str, rejected: Any
) -> None:
    assert not _validator(manifest, key).is_valid(rejected)


def test_the_distribution_declares_the_kit_license_and_one_entry_point(
    manifest: dict[str, Any],
) -> None:
    project = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    table = project["project"]
    entry_points = table["entry-points"]["codegen_kit.packages"]
    kit_license = (PROJECT_ROOT.parents[1] / "LICENSE").read_bytes()

    assert table["license"] == "MIT"
    assert (PROJECT_ROOT / table["license-files"][0]).read_bytes() == kit_license
    assert table["version"] == manifest["version"]
    assert entry_points == {manifest["name"]: "codegen_kit_ranking:package"}


def test_the_lifecycle_hooks_hold_no_resources() -> None:
    async def lifecycle() -> None:
        await codegen_kit_ranking.package.startup(object())
        await codegen_kit_ranking.package.shutdown(object())

    assert asyncio.run(lifecycle()) is None
