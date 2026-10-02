"""The package catalog, its ties to package sources, and catalog-resolved `kit add <name>`."""

from __future__ import annotations

from collections.abc import Callable
import os
from pathlib import Path
import shutil
import subprocess
import tomllib
from typing import Any
import zipfile

import pytest
import yaml

from framework import cli
from framework.catalog import (
    CatalogPackage,
    DuplicateCatalogPackageError,
    DuplicateCatalogVersionError,
    IncompatibleCatalogVersionError,
    InvalidCatalogEntryError,
    InvalidCatalogVersionError,
    UnknownPackageError,
    UnsupportedCatalogFormatError,
    bundled_catalog,
    load_catalog,
    package_tag,
    parse_catalog,
)
from framework.generators.package_contract import PackageContractGenerator
from framework.spec.events import EventsSpec
from framework.spec.loader import AllSpecs, load_manifest
from framework.spec.models import ModelsSpec
from framework.spec.package_resolution import ENTRY_POINT_GROUP, resolve_active_packages
from framework.spec.packages import load_package_manifest

KIT_ROOT = Path(__file__).parents[2]
CATALOG = KIT_ROOT / "packages/catalog.yaml"
OFFLINE_BACKEND = KIT_ROOT / "tests/fixtures/offline_wheel_backend/offline_backend.py"
HATCH_BUILD_SYSTEM = '[build-system]\nrequires = ["hatchling"]\nbuild-backend = "hatchling.build"\n'
OFFLINE_BUILD_SYSTEM = (
    '[build-system]\nrequires = []\nbuild-backend = "offline_backend"\nbackend-path = ["_build"]\n'
)
PRODUCT_MANIFEST = """\
version: 1
settings_schema:
  $schema: https://json-schema.org/draft/2020-12/schema
  type: object
  properties: {}
  additionalProperties: false
provides: []
packages: []
"""


# --- the catalog file and its loader -------------------------------------------------------


def test_catalog_lists_reminders_release_for_a_planner() -> None:
    reminders = load_catalog(CATALOG).get("reminders")

    assert reminders.distribution == "codegen-kit-reminders"
    assert reminders.path == "packages/codegen-kit-reminders"
    assert [(item.version, item.tag, item.requires_core) for item in reminders.versions] == [
        ("0.3.0", "packages/reminders/v0.3.0", ">=2,<3"),
        ("0.4.0", "packages/reminders/v0.4.0", ">=2.1,<3"),
    ]
    assert reminders.summary
    assert "remind me at a time" in reminders.capabilities
    assert [item.name for item in reminders.settings] == ["reminder_owner_ref"]
    assert [(item.name, item.required) for item in reminders.environment] == [("REDIS_URL", True)]
    assert all(item.summary for item in (*reminders.settings, *reminders.environment))


def test_bundled_catalog_is_the_repository_catalog() -> None:
    assert bundled_catalog() == load_catalog(CATALOG)


def _document() -> dict[str, Any]:
    return yaml.safe_load(CATALOG.read_text())


def _reminders(document: dict[str, Any]) -> dict[str, Any]:
    return document["packages"][0]


def _drop_summary(document: dict[str, Any]) -> None:
    del _reminders(document)["summary"]


def _drop_requires_core(document: dict[str, Any]) -> None:
    del _reminders(document)["versions"][0]["requires_core"]


def _duplicate_package(document: dict[str, Any]) -> None:
    document["packages"].append(dict(_reminders(document)))


def _duplicate_version(document: dict[str, Any]) -> None:
    versions = _reminders(document)["versions"]
    versions.append(dict(versions[0]))


def _non_pep440_version(document: dict[str, Any]) -> None:
    _reminders(document)["versions"][0].update(version="latest", tag="packages/reminders/vlatest")


def _wrong_tag(document: dict[str, Any]) -> None:
    _reminders(document)["versions"][0]["tag"] = "0.3.0"


@pytest.mark.parametrize(
    ("mutate", "error", "message"),
    [
        (lambda document: document.update(format_version=2), UnsupportedCatalogFormatError, "2"),
        (_drop_summary, InvalidCatalogEntryError, "'summary'"),
        (_drop_requires_core, InvalidCatalogEntryError, "'requires_core'"),
        (_duplicate_package, DuplicateCatalogPackageError, "'reminders' twice"),
        (_duplicate_version, DuplicateCatalogVersionError, "0.3.0 twice"),
        (_non_pep440_version, InvalidCatalogVersionError, "'latest' is not a PEP 440"),
        (_wrong_tag, InvalidCatalogEntryError, "must be 'packages/reminders/v0.3.0'"),
    ],
)
def test_loader_refuses_malformed_catalog_with_named_error(
    mutate: Callable[[dict[str, Any]], None], error: type[Exception], message: str
) -> None:
    document = _document()
    mutate(document)

    with pytest.raises(error, match=message) as raised:
        parse_catalog(yaml.safe_dump(document))
    assert str(raised.value).startswith(f"{error.__name__}: ")


def test_selection_takes_newest_version_admitting_the_core() -> None:
    document = _document()
    _reminders(document)["versions"] = [
        {"version": version, "tag": package_tag("reminders", version), "requires_core": core}
        for version, core in [("0.3.0", ">=2,<3"), ("0.10.0", ">=2,<3"), ("1.0.0", ">=3")]
    ]
    reminders = parse_catalog(yaml.safe_dump(document)).get("reminders")

    assert reminders.select("2.0.0").version == "0.10.0"
    assert reminders.newest().version == "1.0.0"
    with pytest.raises(IncompatibleCatalogVersionError, match="1.0.0 requires core >=3"):
        reminders.select("1.0.0")


def test_core_2_0_keeps_reminders_0_3_0_and_core_2_1_takes_the_timer_release() -> None:
    reminders = load_catalog(CATALOG).get("reminders")

    assert reminders.select("2.0.0").version == "0.3.0"
    assert reminders.select("2.1.0").version == "0.4.0"


# --- the catalog tied to package sources at HEAD -------------------------------------------


@pytest.mark.parametrize("package", load_catalog(CATALOG).packages, ids=lambda item: item.name)
def test_catalog_newest_version_matches_package_sources(package: CatalogPackage) -> None:
    source = KIT_ROOT / package.path
    project = tomllib.loads((source / "pyproject.toml").read_text())["project"]
    entry_points = project["entry-points"][ENTRY_POINT_GROUP]
    module = entry_points[package.name].partition(":")[0]
    manifest = load_package_manifest(source / Path(*module.split(".")) / "package.yaml")
    newest = package.newest()

    assert list(entry_points) == [package.name]
    assert manifest.name == package.name
    assert project["name"] == package.distribution
    assert newest.version == project["version"] == manifest.version
    assert newest.tag == package_tag(package.name, manifest.version)
    assert newest.requires_core == manifest.requires_core
    assert [item.name for item in package.settings] == list(manifest.settings_schema["properties"])
    assert [(item.name, item.required) for item in package.environment] == [
        (item.name, item.required) for item in manifest.environment
    ]


# --- `kit add <name>` against a local catalog source ---------------------------------------


def _git(*arguments: str, cwd: Path) -> str:
    result = subprocess.run(  # noqa: S603
        ["git", *arguments],  # noqa: S607
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    )
    return result.stdout


def _commit(repository: Path, message: str) -> None:
    _git("add", "-A", cwd=repository)
    _git(
        "-c",
        "user.name=Catalog fixture",
        "-c",
        "user.email=catalog@example.com",
        "commit",
        "--quiet",
        "-m",
        message,
        cwd=repository,
    )


def _tag(repository: Path, tag: str) -> None:
    _git(
        "-c",
        "user.name=Catalog fixture",
        "-c",
        "user.email=catalog@example.com",
        "tag",
        "--force",
        "--annotate",
        tag,
        "-m",
        tag,
        cwd=repository,
    )


def _reminders_source(package: Path, *, previous_release: bool) -> None:
    """Write the HEAD reminders source, or its 0.3.0 form: no timer, core 2.0 admitted."""

    shutil.rmtree(package, ignore_errors=True)
    shutil.copytree(
        KIT_ROOT / "packages/codegen-kit-reminders",
        package,
        ignore=shutil.ignore_patterns("__pycache__", "*.egg-info"),
    )
    (package / "_build").mkdir()
    shutil.copy2(OFFLINE_BACKEND, package / "_build/offline_backend.py")
    project = package / "pyproject.toml"
    assert HATCH_BUILD_SYSTEM in project.read_text()
    project.write_text(project.read_text().replace(HATCH_BUILD_SYSTEM, OFFLINE_BUILD_SYSTEM))
    if not previous_release:
        return
    project.write_text(project.read_text().replace('version = "0.4.0"', 'version = "0.3.0"'))
    manifest_path = package / "codegen_kit_reminders/package.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest.update(version="0.3.0", requires_core=">=2,<3")
    del manifest["timers"]
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))


@pytest.fixture
def kit_source(tmp_path: Path) -> Path:
    """A local kit repository: this catalog, reminders tagged at 0.3.0 and then at 0.4.0."""

    repository = tmp_path / "kit-source"
    package = repository / "packages/codegen-kit-reminders"
    (repository / "packages").mkdir(parents=True)
    shutil.copy2(CATALOG, repository / "packages/catalog.yaml")
    _reminders_source(package, previous_release=True)
    _git("init", "--quiet", "--initial-branch=main", cwd=repository)
    _commit(repository, "Kit with reminders 0.3.0")
    _tag(repository, "packages/reminders/v0.3.0")
    _reminders_source(package, previous_release=False)
    _commit(repository, "Kit with reminders 0.4.0")
    _tag(repository, "packages/reminders/v0.4.0")
    return repository


def _product(root: Path) -> Path:
    backend = root / "services/backend"
    backend.mkdir(parents=True)
    (backend / "pyproject.toml").write_text(
        "[project]\nname = 'backend'\n\n[tool.deptry.per_rule_ignores]\nDEP002 = [\"uvicorn\"]\n"
    )
    (backend / "manifest.yaml").write_text(PRODUCT_MANIFEST)
    return root


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture
def product(tmp_path: Path) -> Path:
    return _product(tmp_path / "product")


@pytest.fixture
def backend_site(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, list[list[str]]]:
    """Stand in for the backend uv environment and for regeneration of the package contract.

    `uv sync` unpacks the committed wheels into a site directory; regeneration resolves the
    active set from that directory and writes the active-package contract, as `generate_all`
    does from `services/backend/.venv`.
    """

    site = tmp_path / "backend-site"
    site.mkdir()
    commands: list[list[str]] = []

    def run(command: list[str], root: Path) -> None:
        commands.append(command)
        if command[:2] == ["uv", "sync"]:
            for wheel in (root / "services/backend/packages").glob("*.whl"):
                with zipfile.ZipFile(wheel) as archive:
                    archive.extractall(site)

    def regenerate(root: Path) -> None:
        manifests = {"backend": load_manifest(root / "services/backend/manifest.yaml")}
        active = resolve_active_packages(root, manifests, site_packages=site)
        specs = AllSpecs(
            models=ModelsSpec(models={}),
            events=EventsSpec(events=[]),
            manifests=manifests,
            packages=active,
        )
        PackageContractGenerator(specs, root).generate()

    monkeypatch.setattr(cli, "_run", run)
    monkeypatch.setattr(cli, "generate_all", regenerate)
    return site, commands


def _kit(monkeypatch: pytest.MonkeyPatch, *arguments: str) -> None:
    monkeypatch.setattr("sys.argv", ["kit", *arguments])
    cli.main()


def _active_packages(product: Path) -> list[dict[str, str]]:
    namespace: dict[str, Any] = {}
    exec((product / "codegen_kit/_active_packages.py").read_text(), namespace)  # noqa: S102
    return namespace["ACTIVE_PACKAGES"]


def test_kit_add_resolves_the_catalog_and_installs_the_released_tag(
    kit_source: Path,
    product: Path,
    backend_site: tuple[Path, list[list[str]]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, commands = backend_site

    _kit(
        monkeypatch,
        "add",
        "reminders",
        "--catalog-source",
        str(kit_source),
        "--product-root",
        str(product),
    )

    wheel = "codegen_kit_reminders-0.4.0-py3-none-any.whl"
    assert (product / "services/backend/packages" / wheel).is_file()
    assert commands == [
        [
            "uv",
            "add",
            "--project",
            "services/backend",
            "--no-sync",
            f"services/backend/packages/{wheel}",
        ],
        ["uv", "sync", "--project", "services/backend", "--frozen"],
    ]
    assert (
        'DEP002 = ["uvicorn", "codegen-kit-reminders"]'
        in (product / "services/backend/pyproject.toml").read_text()
    )
    assert yaml.safe_load((product / "services/backend/manifest.yaml").read_text())["packages"] == [
        "reminders"
    ]
    [active] = _active_packages(product)
    assert (active["name"], active["version"]) == ("reminders", "0.4.0")
    assert "kit: installed reminders 0.4.0 (packages/reminders/v0.4.0)" in capsys.readouterr().out


def test_kit_add_on_core_2_0_installs_the_kept_0_3_0_release(
    kit_source: Path,
    product: Path,
    backend_site: tuple[Path, list[list[str]]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(cli, "CORE_VERSION", "2.0.0")
    monkeypatch.setattr("framework.spec.package_resolution.CORE_VERSION", "2.0.0")

    _kit(
        monkeypatch,
        "add",
        "reminders",
        "--catalog-source",
        str(kit_source),
        "--product-root",
        str(product),
    )

    assert (
        product / "services/backend/packages/codegen_kit_reminders-0.3.0-py3-none-any.whl"
    ).is_file()
    [active] = _active_packages(product)
    assert (active["name"], active["version"]) == ("reminders", "0.3.0")
    assert "kit: installed reminders 0.3.0 (packages/reminders/v0.3.0)" in capsys.readouterr().out


def test_catalog_source_and_ref_come_from_the_environment(
    kit_source: Path,
    product: Path,
    backend_site: tuple[Path, list[list[str]]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The default branch carries a catalog without reminders; the stand's ref still has it.
    _git("branch", "stand", cwd=kit_source)
    catalog = kit_source / "packages/catalog.yaml"
    document = yaml.safe_load(catalog.read_text())
    document["packages"] = []
    catalog.write_text(yaml.safe_dump(document))
    _commit(kit_source, "Empty default-branch catalog")
    monkeypatch.setenv("KIT_CATALOG_SOURCE", str(kit_source))
    monkeypatch.setenv("KIT_CATALOG_REF", "refs/heads/stand")

    _kit(monkeypatch, "add", "reminders", "--product-root", str(product))

    [active] = _active_packages(product)
    assert active["version"] == "0.4.0"


Change = Callable[[Path, pytest.MonkeyPatch], None]


def _unchanged(_kit_source: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    return None


def _future_core(_kit_source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "CORE_VERSION", "9.0.0")


def _unpublish(kit_source: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
    _git("tag", "--delete", "packages/reminders/v0.4.0", cwd=kit_source)


def _retag_with_project(old: str, new: str) -> Change:
    def change(kit_source: Path, _monkeypatch: pytest.MonkeyPatch) -> None:
        project = kit_source / "packages/codegen-kit-reminders/pyproject.toml"
        assert old in project.read_text()
        project.write_text(project.read_text().replace(old, new))
        _commit(kit_source, f"Tagged source declares {new}")
        _tag(kit_source, "packages/reminders/v0.4.0")

    return change


@pytest.mark.parametrize(
    ("name", "change", "expected"),
    [
        (
            "weather",
            _unchanged,
            ["UnknownPackageError", "unknown package 'weather'", "known packages: reminders"],
        ),
        (
            "reminders",
            _future_core,
            [
                "IncompatibleCatalogVersionError",
                "0.3.0 requires core >=2,<3",
                "0.4.0 requires core >=2.1,<3",
                "compatible with core 9.0.0",
            ],
        ),
        (
            "reminders",
            _unpublish,
            ["PackageNotPublishedError", "0.4.0 is not published yet", "v0.4.0 does not exist"],
        ),
        (
            "reminders",
            _retag_with_project('version = "0.4.0"', 'version = "0.4.1"'),
            ["PackageWheelMismatchError", "version '0.4.1' is not catalog version '0.4.0'"],
        ),
        (
            "reminders",
            _retag_with_project('name = "codegen-kit-reminders"', 'name = "codegen-kit-other"'),
            ["PackageWheelMismatchError", "'codegen-kit-other' is not catalog distribution"],
        ),
    ],
    ids=["unknown-name", "incompatible-core", "unpublished-tag", "wrong-version", "wrong-dist"],
)
def test_kit_add_refusals_are_named_and_leave_the_product_unmodified(
    name: str,
    change: Change,
    expected: list[str],
    kit_source: Path,
    product: Path,
    backend_site: tuple[Path, list[list[str]]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, commands = backend_site
    change(kit_source, monkeypatch)
    before = _snapshot(product)

    with pytest.raises(SystemExit, match="1"):
        _kit(
            monkeypatch,
            "add",
            name,
            "--catalog-source",
            str(kit_source),
            "--product-root",
            str(product),
        )

    error = capsys.readouterr().err
    for fragment in expected:
        assert fragment in error
    assert _snapshot(product) == before
    assert commands == []


def test_kit_add_refuses_an_unreachable_catalog_source(
    tmp_path: Path,
    product: Path,
    backend_site: tuple[Path, list[list[str]]],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, commands = backend_site
    before = _snapshot(product)

    with pytest.raises(SystemExit, match="1"):
        _kit(
            monkeypatch,
            "add",
            "reminders",
            "--catalog-source",
            str(tmp_path / "no-such-kit"),
            "--product-root",
            str(product),
        )

    error = capsys.readouterr().err
    assert "CatalogSourceUnreachableError" in error
    assert "is unreachable" in error
    assert _snapshot(product) == before
    assert commands == []


# --- `kit add <name> --wheel` checks any catalog package ------------------------------------


def test_wheel_install_accepts_any_catalog_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = _product(tmp_path / "product")
    document = _document()
    weather = dict(_reminders(document), name="weather", distribution="weather-package")
    weather["versions"] = [
        {"version": "1.0.0", "tag": package_tag("weather", "1.0.0"), "requires_core": ">=2,<3"}
    ]
    document["packages"].append(weather)
    catalog = parse_catalog(yaml.safe_dump(document))
    artifact = tmp_path / "weather_package-1.0.0-py3-none-any.whl"
    artifact.write_bytes(b"wheel")
    monkeypatch.setattr(cli, "_run", lambda *_arguments: None)
    monkeypatch.setattr(cli, "generate_all", lambda _root: None)

    with pytest.raises(ValueError, match="wheel is not codegen-kit-reminders"):
        cli.add_package("reminders", artifact, product, catalog)
    cli.add_package("weather", artifact, product, catalog)

    assert (product / "services/backend/packages" / artifact.name).is_file()
    assert '"weather-package"' in (product / "services/backend/pyproject.toml").read_text()
    assert yaml.safe_load((product / "services/backend/manifest.yaml").read_text())["packages"] == [
        "weather"
    ]


def test_wheel_install_names_unknown_packages_from_the_tooling_catalog(tmp_path: Path) -> None:
    product = _product(tmp_path / "product")
    artifact = tmp_path / "weather_package-1.0.0-py3-none-any.whl"
    artifact.write_bytes(b"wheel")

    with pytest.raises(UnknownPackageError, match="known packages: reminders"):
        cli.add_package("weather", artifact, product)
