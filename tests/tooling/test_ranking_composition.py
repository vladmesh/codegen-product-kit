"""Ranking composes with tg-channels only through the provided interface it requires."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest

from framework.spec.loader import SpecValidationError, load_specs
from framework.spec.package_resolution import ActivePackage
from framework.spec.packages import load_package_manifest

KIT_ROOT = Path(__file__).parents[2]
PACKAGES = {
    "tg-channels": KIT_ROOT / "packages/codegen-kit-tg-channels/codegen_kit_tg_channels",
    "ranking": KIT_ROOT / "packages/codegen-kit-ranking/codegen_kit_ranking",
}
META = "https://json-schema.org/draft/2020-12/schema"


def _load(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: list[str]):
    packages = []
    for name in names:
        manifest = load_package_manifest(PACKAGES[name] / "package.yaml")
        packages.append(
            ActivePackage(
                name=manifest.name,
                manifest=manifest,
                package_root=PACKAGES[name],
                manifest_sha256="0" * 64,
            )
        )
    repo = tmp_path / "product"
    (repo / "shared").mkdir(parents=True)
    shutil.copytree(KIT_ROOT / "template/shared/spec", repo / "shared/spec")
    (repo / "services/backend/src/generated").mkdir(parents=True)
    (repo / "services/backend/manifest.yaml").write_text(
        f"version: 1\nsettings_schema: {{'$schema': '{META}', type: object, "
        f"properties: {{}}, additionalProperties: false}}\npackages: [{', '.join(names)}]\n"
    )
    monkeypatch.setattr(
        "framework.spec.loader.resolve_active_packages", lambda *_args, **_kwargs: packages
    )
    return load_specs(repo)


def test_ranking_without_tg_channels_is_refused_as_a_missing_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SpecValidationError) as refused:
        _load(tmp_path, monkeypatch, ["ranking"])

    assert "'package:ranking' requires unprovided capability 'tg-channels.posts.v1'" in str(
        refused.value
    )


def test_ranking_after_tg_channels_activates_with_its_seeded_settings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    specs = _load(tmp_path, monkeypatch, ["tg-channels", "ranking"])

    owned = {
        name: specs.settings_schemas[name]["default"]
        for name, source in specs.settings_schema_sources.items()
        if source == "package:ranking"
    }
    assert owned == {
        "ranking.weights": {
            "views": 35,
            "reactions": 25,
            "forwards": 20,
            "freshness": 20,
            "keywords": 0,
        },
        "ranking.keywords": [],
        "ranking.top_count": 10,
    }
