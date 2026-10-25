"""`kit add --package-source`: a non-catalog package built from the product's own source."""

from __future__ import annotations

from pathlib import Path
import shutil
import tomllib
from typing import Any
import zipfile

import pytest
import yaml

from framework import cli
from framework.bindings import BindingError
from framework.package_source import PackageSourceError, PackageWheelMismatchError
from framework.preflight import PreflightIncompatibleError
from framework.spec.package_resolution import ENTRY_POINT_GROUP

ROOT = Path(__file__).parents[2]
FIXTURE = ROOT / "tests/fixtures/synthetic_package"
MODULE = "synthetic_package"
SOURCE = "packages/synthetic"
DISTRIBUTION = "codegen-kit-synthetic-package"


def _product(root: Path) -> Path:
    backend = root / "services/backend"
    backend.mkdir(parents=True)
    (backend / "pyproject.toml").write_text(
        "[project]\nname = 'backend'\n\n[tool.deptry.per_rule_ignores]\nDEP002 = [\"uvicorn\"]\n"
    )
    (backend / "manifest.yaml").write_text(
        "version: 1\nsettings_schema:\n"
        "  $schema: https://json-schema.org/draft/2020-12/schema\n"
        "  type: object\n  properties: {}\n  additionalProperties: false\n"
        "packages: []\n"
    )
    (root / "codegen_kit").mkdir()
    (root / "codegen_kit/packages.py").write_text('CORE_VERSION = "2.6.0"\n')
    (root / ".copier-answers.yml").write_text("_commit: v0.11.0\n")
    shutil.copytree(FIXTURE, root / SOURCE, ignore=shutil.ignore_patterns("__pycache__"))
    return root


def _state(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _build_from(project: Path, output: Path, edit=None) -> Path:
    """Stand in for ``uv build``: the wheel carries the source's static metadata as data."""
    table = tomllib.loads((project / "pyproject.toml").read_text())["project"]
    files = {
        f"{MODULE}/package.yaml": (project / MODULE / "package.yaml").read_text(),
        f"{MODULE}-x.dist-info/METADATA": f"Name: {table['name']}\nVersion: {table['version']}\n",
        f"{MODULE}-x.dist-info/entry_points.txt": "[codegen_kit.packages]\n"
        + "".join(
            f"{key} = {value}\n" for key, value in table["entry-points"][ENTRY_POINT_GROUP].items()
        ),
    }
    if edit is not None:
        edit(files)
    output.mkdir(parents=True)
    wheel = output / f"codegen_kit_synthetic_package-{table['version']}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return wheel


@pytest.fixture
def recipe(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Record build, uv and generation; refuse any catalog or tag access."""
    calls: dict[str, Any] = {"build": [], "run": [], "generate": [], "edit_wheel": None}

    def build(project: Path, output: Path) -> Path:
        calls["build"].append(project)
        return _build_from(project, output, calls["edit_wheel"])

    def catalog(*_args: object) -> None:
        raise AssertionError("the package-source route read a catalog")

    monkeypatch.setattr(cli, "build_wheel", build)
    monkeypatch.setattr(cli, "_run", lambda command, _root: calls["run"].append(command))
    monkeypatch.setattr(cli, "generate_all", lambda root: calls["generate"].append(root))
    for reader in ("read_catalog", "fetch_package_source", "bundled_catalog"):
        monkeypatch.setattr(cli, reader, catalog)
    return calls


def _no_install(calls: dict[str, Any]) -> bool:
    return calls["run"] == calls["generate"] == []


def test_source_package_installs_through_the_ordinary_recipe_and_repeats_cleanly(
    tmp_path: Path, recipe: dict[str, Any]
) -> None:
    product = _product(tmp_path / "product")

    metadata = cli.add_source_package("synthetic", product / SOURCE, product)
    cli.add_source_package("synthetic", product / SOURCE, product)

    assert metadata.manifest.version == "2.0.0"
    assert metadata.provenance["route"] == "package_source"
    assert metadata.provenance["path"] == SOURCE
    assert recipe["build"] == [(product / SOURCE).resolve()] * 2
    wheel = "services/backend/packages/codegen_kit_synthetic_package-2.0.0-py3-none-any.whl"
    assert (product / wheel).is_file()
    assert recipe["run"] == 2 * [
        ["uv", "add", "--project", "services/backend", "--no-sync", wheel],
        ["uv", "sync", "--project", "services/backend", "--frozen"],
    ]
    assert recipe["generate"] == [product, product]
    manifest = yaml.safe_load((product / "services/backend/manifest.yaml").read_text())
    assert manifest["packages"] == ["synthetic"]
    project = (product / "services/backend/pyproject.toml").read_text()
    assert f'DEP002 = ["uvicorn", "{DISTRIBUTION}"]' in project
    assert (product / SOURCE / "pyproject.toml").read_bytes() == (
        FIXTURE / "pyproject.toml"
    ).read_bytes()


def test_cli_reports_the_installed_source_version(
    tmp_path: Path,
    recipe: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    product = _product(tmp_path / "product")
    monkeypatch.setenv("KIT_CATALOG_SOURCE", str(tmp_path / "no-catalog"))
    monkeypatch.setattr(
        "sys.argv",
        [
            "kit",
            "add",
            "synthetic",
            "--package-source",
            str(product / SOURCE),
            "--product-root",
            str(product),
        ],
    )
    cli.main()
    assert (
        capsys.readouterr().out
        == "kit: installed synthetic 2.0.0 (package source packages/synthetic)\n"
    )
    assert recipe["generate"] == [product]


def _outside(product: Path, tmp_path: Path) -> Path:
    outside = tmp_path / "elsewhere/synthetic"
    shutil.copytree(product / SOURCE, outside)
    return outside


def _nested(product: Path, _tmp_path: Path) -> Path:
    other = product / "vendor/other-product"
    (other / "packages").mkdir(parents=True)
    (other / ".copier-answers.yml").write_text("_commit: v0.11.0\n")
    shutil.copytree(product / SOURCE, other / "packages/synthetic")
    return other / "packages/synthetic"


def _linked_outside(product: Path, tmp_path: Path) -> Path:
    link = product / "packages/linked"
    link.symlink_to(_outside(product, tmp_path), target_is_directory=True)
    return link


@pytest.mark.parametrize(
    ("select", "message"),
    [
        (_outside, "is not inside product"),
        (_nested, "belongs to another product"),
        (_linked_outside, "is not inside product"),
        (lambda product, _tmp: product, "is not inside product"),
        (lambda product, _tmp: product / "packages/missing", "is not a directory"),
    ],
    ids=["outside", "other-product", "symlink-outside", "product-root", "missing"],
)
def test_source_outside_the_product_is_refused_before_build(
    tmp_path: Path, recipe: dict[str, Any], select, message: str
) -> None:
    product = _product(tmp_path / "product")
    source = select(product, tmp_path)
    before = _state(product)
    with pytest.raises(PackageSourceError, match=message):
        cli.add_source_package("synthetic", source, product)
    assert recipe["build"] == [] and _no_install(recipe)
    assert _state(product) == before


def test_source_symlink_inside_the_product_is_its_target(
    tmp_path: Path, recipe: dict[str, Any]
) -> None:
    product = _product(tmp_path / "product")
    (product / "packages/current").symlink_to(product / SOURCE, target_is_directory=True)
    cli.add_source_package("synthetic", product / "packages/current", product)
    assert recipe["build"] == [(product / SOURCE).resolve()]


def _edit_text(relative: str, old: str, new: str):
    def edit(source: Path) -> None:
        path = source / relative
        assert old in path.read_text()
        path.write_text(path.read_text().replace(old, new))

    return edit


#: Source edits refused before build; each names the rule it breaks.
SOURCE_CASES = {
    "dynamic-version": (
        _edit_text("pyproject.toml", 'version = "2.0.0"', 'dynamic = ["version"]'),
        PackageSourceError,
        "must declare project version statically",
    ),
    "no-entry-point": (
        _edit_text("pyproject.toml", 'synthetic = "synthetic_package:package"\n', ""),
        PackageSourceError,
        "exactly one codegen_kit.packages entry point",
    ),
    "second-entry-point": (
        _edit_text(
            "pyproject.toml",
            'synthetic = "synthetic_package:package"\n',
            'synthetic = "synthetic_package:package"\nextra = "synthetic_package:package"\n',
        ),
        PackageSourceError,
        "exactly one codegen_kit.packages entry point",
    ),
    "other-entry-point": (
        _edit_text("pyproject.toml", "synthetic = ", "other = "),
        PackageSourceError,
        "declares entry point 'other'",
    ),
    "manifest-name": (
        _edit_text(f"{MODULE}/package.yaml", "name: synthetic", "name: other"),
        PreflightIncompatibleError,
        "package_mismatch",
    ),
    "manifest-version": (
        _edit_text(f"{MODULE}/package.yaml", "version: 2.0.0", "version: 2.0.1"),
        PackageSourceError,
        "package.yaml version '2.0.1' differs from pyproject.toml version '2.0.0'",
    ),
    "manifest-invalid": (
        _edit_text(f"{MODULE}/package.yaml", "protocol_version: 1", "protocol_version: 2"),
        PreflightIncompatibleError,
        "package_metadata_invalid",
    ),
    "core-range": (
        _edit_text(f"{MODULE}/package.yaml", 'requires_core: ">=2,<3"', 'requires_core: ">=3"'),
        PreflightIncompatibleError,
        "core_range",
    ),
}


@pytest.mark.parametrize("case", SOURCE_CASES)
def test_source_identity_and_core_refusals_precede_build_and_writes(
    tmp_path: Path, recipe: dict[str, Any], case: str
) -> None:
    edit, error, message = SOURCE_CASES[case]
    product = _product(tmp_path / "product")
    edit(product / SOURCE)
    before = _state(product)
    with pytest.raises(error, match=message):
        cli.add_source_package("synthetic", product / SOURCE, product)
    assert recipe["build"] == [] and _no_install(recipe)
    assert _state(product) == before


def _wheel_entry(files: dict[str, str]) -> None:
    files[f"{MODULE}-x.dist-info/entry_points.txt"] = (
        "[codegen_kit.packages]\nsynthetic = other_module:package\n"
    )


#: Built wheels that differ from the checked source; each is refused before installation.
WHEEL_CASES = {
    "version": (
        lambda files: files.update(
            {f"{MODULE}-x.dist-info/METADATA": f"Name: {DISTRIBUTION}\nVersion: 2.0.1\n"}
        ),
        "is not source version '2.0.0'",
    ),
    "distribution": (
        lambda files: files.update(
            {f"{MODULE}-x.dist-info/METADATA": "Name: codegen-kit-other\nVersion: 2.0.0\n"}
        ),
        "is not source distribution",
    ),
    "entry-point": (_wheel_entry, "the source declares"),
    "manifest": (
        lambda files: files.update(
            {f"{MODULE}/package.yaml": files[f"{MODULE}/package.yaml"] + "# rebuilt\n"}
        ),
        "differs from the source",
    ),
    "no-manifest": (
        lambda files: files.pop(f"{MODULE}/package.yaml"),
        "no synthetic_package/package.yaml in entry point module",
    ),
}


@pytest.mark.parametrize("case", WHEEL_CASES)
def test_built_wheel_identity_mismatch_is_not_installed(
    tmp_path: Path, recipe: dict[str, Any], case: str
) -> None:
    edit, message = WHEEL_CASES[case]
    recipe["edit_wheel"] = edit
    product = _product(tmp_path / "product")
    before = _state(product)
    with pytest.raises(PackageWheelMismatchError, match=message):
        cli.add_source_package("synthetic", product / SOURCE, product)
    assert len(recipe["build"]) == 1 and _no_install(recipe)
    assert _state(product) == before


def test_host_contract_refusal_precedes_build_and_writes(
    tmp_path: Path, recipe: dict[str, Any]
) -> None:
    product = _product(tmp_path / "product")
    (product / "services/backend/manifest.yaml").write_text("- language\n")
    before = _state(product)
    with pytest.raises(BindingError):
        cli.add_source_package("synthetic", product / SOURCE, product)
    assert recipe["build"] == [] and _no_install(recipe)
    assert _state(product) == before


@pytest.mark.parametrize(
    "extra",
    [
        ["--wheel", "artifact.whl"],
        ["--catalog-source", "https://example.invalid/kit.git"],
        ["--catalog-ref", "HEAD"],
    ],
    ids=["wheel", "catalog-source", "catalog-ref"],
)
def test_cli_refuses_mixed_source_and_artifact_or_catalog_input(
    tmp_path: Path,
    recipe: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    extra: list[str],
) -> None:
    product = _product(tmp_path / "product")
    before = _state(product)
    monkeypatch.setattr(
        "sys.argv",
        ["kit", "add", "synthetic", "--package-source", SOURCE, "--product-root", str(product)]
        + extra,
    )
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == 2
    assert f"--package-source cannot be combined with {extra[0]}" in capsys.readouterr().err
    assert recipe["build"] == [] and _no_install(recipe)
    assert _state(product) == before
