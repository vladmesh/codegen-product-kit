"""Package protocol v1 manifest and import-boundary tests."""

from pathlib import Path

import pytest

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
    import yaml

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
