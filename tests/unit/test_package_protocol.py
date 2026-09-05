"""Package protocol v1 manifest and import-boundary tests."""

from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from framework.lint import package_imports
from framework.spec.packages import (
    MalformedPackagePrefixError,
    MissingPackageIdentityError,
    UnknownPackageManifestFieldError,
    lint_package_imports,
    load_package_manifest,
    parse_package_manifest,
)

FIXTURE = Path(__file__).parents[1] / "fixtures/synthetic_package/synthetic_package/package.yaml"


def test_synthetic_package_manifest_is_valid() -> None:
    manifest = load_package_manifest(FIXTURE)
    assert manifest.name == "synthetic"
    assert manifest.http.prefix == "/synthetic"


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"surprise": True}, UnknownPackageManifestFieldError),
        ({"http": {"prefix": "/valid", "surprise": True}}, UnknownPackageManifestFieldError),
        ({"name": ""}, MissingPackageIdentityError),
        ({"http": {"prefix": "relative"}}, MalformedPackagePrefixError),
    ],
)
def test_named_manifest_errors(change: dict[str, object], error: type[ValueError]) -> None:
    data = yaml.safe_load(FIXTURE.read_text())
    data.update(change)
    with pytest.raises(error):
        parse_package_manifest(data)


def test_package_import_lint_accepts_declared_dependencies() -> None:
    manifest = load_package_manifest(FIXTURE)
    source_root = FIXTURE.parent
    assert lint_package_imports(source_root, manifest) == []


def test_package_import_lint_rejects_product_internal_import(tmp_path: Path) -> None:
    manifest = load_package_manifest(FIXTURE)
    (tmp_path / "package.py").write_text("from services.backend.src.core import db\n")
    assert lint_package_imports(tmp_path, manifest) == [
        "package.py:1: forbidden import 'services.backend.src.core'"
    ]


def test_package_import_lint_does_not_allow_namesake_subdirectory(tmp_path: Path) -> None:
    manifest = load_package_manifest(FIXTURE)
    source_root = tmp_path / "synthetic_package"
    (source_root / "requests").mkdir(parents=True)
    (source_root / "requests/__init__.py").write_text("")
    (source_root / "module.py").write_text("import requests\n")

    assert lint_package_imports(source_root, manifest) == [
        "module.py:1: forbidden import 'requests'"
    ]


@pytest.mark.parametrize("value", ["", "   ", "/does/not/exist"])
def test_package_import_lint_rejects_invalid_site_packages(value: str) -> None:
    with pytest.raises(ValueError, match="--site-packages"):
        package_imports._distribution_path(value)


@pytest.mark.parametrize(
    ("distribution", "message"),
    [
        (None, "entry point has no distribution metadata"),
        (SimpleNamespace(files=[]), "distribution must contain exactly one package.yaml; found 0"),
    ],
)
def test_installed_package_lint_reports_missing_manifest_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    distribution: object,
    message: str,
) -> None:
    manifest_path = tmp_path / "services/backend/manifest.yaml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("packages: [synthetic]\n")
    entry_point = SimpleNamespace(dist=distribution)
    monkeypatch.setattr(
        package_imports,
        "_package_entry_points",
        lambda _path: {"synthetic": entry_point},
    )

    assert package_imports.lint_installed_packages(tmp_path) == [f"synthetic: {message}"]
