"""Small, deterministic product-kit commands."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

from packaging.specifiers import SpecifierSet
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import Version
import yaml

from framework.catalog import (
    Catalog,
    CatalogLibrary,
    CatalogLibraryVersion,
    CatalogPackage,
    CatalogVersion,
    ExtensionPreconditionError,
    IncompatibleCatalogVersionError,
    bundled_catalog,
)
from framework.generate import generate_all
from framework.package_source import (
    CATALOG_REF_ENV,
    CATALOG_SOURCE_ENV,
    DEFAULT_CATALOG_REF,
    DEFAULT_CATALOG_SOURCE,
    PackageWheelMismatchError,
    build_wheel,
    fetch_package_source,
    read_catalog,
    verify_wheel,
)
from framework.spec.loader import SpecValidationError, load_manifest
from framework.spec.package_resolution import CORE_VERSION, resolve_active_packages


def _run(command: list[str], repo_root: Path) -> None:
    subprocess.run(command, cwd=repo_root, check=True)  # noqa: S603


def _allow_dynamic_dependency(project: Path, distribution: str) -> None:
    """Teach deptry about a dependency loaded exclusively through an entry point."""

    source = project.read_text()
    pattern = re.compile(r"^(DEP002\s*=\s*\[)([^\n]*)(\])$", re.MULTILINE)
    match = pattern.search(source)
    if match is None:
        raise ValueError(f"{project} has no tool.deptry DEP002 declaration")
    if f'"{distribution}"' in match.group(2):
        return
    separator = ", " if match.group(2).strip() else ""
    replacement = f'{match.group(1)}{match.group(2)}{separator}"{distribution}"{match.group(3)}'
    project.write_text(source[: match.start()] + replacement + source[match.end() :])


def _require_backend_product(repo_root: Path) -> None:
    manifest_path = repo_root / "services/backend/manifest.yaml"
    backend_project = repo_root / "services/backend/pyproject.toml"
    if not manifest_path.is_file() or not backend_project.is_file():
        raise ValueError(f"{repo_root} is not a generated product with a backend")


def _require_tg_bot_product(repo_root: Path) -> None:
    if not (repo_root / "services/tg_bot/pyproject.toml").is_file():
        raise ValueError(f"{repo_root} has no services/tg_bot; libraries target tg_bot")


def _library_python_version(repo_root: Path) -> str:
    """Read the selected service interpreter, without installing or changing the product."""
    python = repo_root / "services/tg_bot/.venv/bin/python"
    if not python.is_file():
        found = subprocess.run(  # noqa: S603
            ["uv", "python", "find", "--project", "services/tg_bot", "--no-python-downloads"],  # noqa: S607
            cwd=repo_root,
            capture_output=True,
            text=True,
        )
        if found.returncode != 0:
            raise IncompatibleCatalogVersionError(
                f"cannot find tg_bot Python without downloads: {found.stderr.strip()}"
            )
        python = Path(found.stdout.strip())
    result = subprocess.run(  # noqa: S603
        [str(python), "-I", "-c", "import platform; print(platform.python_version())"],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _install_wheel(package: CatalogPackage | CatalogLibrary, wheel: Path, repo_root: Path) -> None:
    """Copy and lock the dependency; activate/regenerate only runtime packages."""

    manifest_path = repo_root / "services/backend/manifest.yaml"
    backend_project = repo_root / "services/backend/pyproject.toml"
    service = "services/tg_bot" if isinstance(package, CatalogLibrary) else "services/backend"
    package_dir = repo_root / service / "packages"
    package_dir.mkdir(parents=True, exist_ok=True)
    installed_wheel = package_dir / wheel.name
    if installed_wheel != wheel:
        shutil.copy2(wheel, installed_wheel)

    relative_wheel = installed_wheel.relative_to(repo_root)
    _run(
        [
            "uv",
            "add",
            "--project",
            service,
            "--no-sync",
            str(relative_wheel),
        ],
        repo_root,
    )
    if isinstance(package, CatalogLibrary):
        _run(["uv", "sync", "--project", service, "--frozen"], repo_root)
        return
    _allow_dynamic_dependency(backend_project, package.distribution)

    manifest = yaml.safe_load(manifest_path.read_text())
    packages = manifest.setdefault("packages", [])
    if package.name not in packages:
        packages.append(package.name)
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))

    _run(["uv", "sync", "--project", "services/backend", "--frozen"], repo_root)
    generate_all(repo_root)


def _require_extension_parent(package: CatalogPackage, repo_root: Path) -> None:
    """Read the product's validated installed set before fetching or mutating anything."""

    parent = package.extends
    if parent is None:
        return
    where = (
        f"extension {package.name!r} requires installed parent {parent.package!r} {parent.versions}"
    )
    try:
        manifest = load_manifest(repo_root / "services/backend/manifest.yaml")
    except SpecValidationError as error:
        raise ExtensionPreconditionError(f"{where}; {error}") from error
    if parent.package not in manifest.packages:
        raise ExtensionPreconditionError(f"{where}; parent is not allowlisted")
    # Do not let the resolver's development fallback use the tooling host environment.
    if not (repo_root / "services/backend/.venv/bin/python").is_file():
        raise ExtensionPreconditionError(f"{where}; product backend environment is not installed")
    try:
        active = resolve_active_packages(repo_root, {"backend": manifest})
    except (ValueError, SpecValidationError) as error:
        raise ExtensionPreconditionError(f"{where}; {error}") from error
    installed = next((item for item in active if item.name == parent.package), None)
    if installed is None:
        raise ExtensionPreconditionError(f"{where}; parent is not active")
    version = installed.manifest.version  # resolver also verifies distribution version identity
    if Version(version) not in SpecifierSet(parent.versions):
        raise ExtensionPreconditionError(f"{where}; installed parent version is {version}")


def add_package(name: str, wheel: Path, repo_root: Path, catalog: Catalog | None = None) -> None:
    """Install an explicit catalog artifact into its service.

    The name and distribution are checked against ``catalog``, by default the catalog shipped
    with this tooling. Package versions retain the explicit-artifact behavior; library
    versions must be declared and verified against the catalog and target Python.
    """

    package = (catalog or bundled_catalog()).get_component(name)
    if isinstance(package, CatalogLibrary):
        _require_tg_bot_product(repo_root)
        python_version = _library_python_version(repo_root)
        wheel = wheel.expanduser().resolve()
        if not wheel.is_file():
            raise PackageWheelMismatchError(f"wheel does not exist: {wheel}")
        try:
            _, artifact_version, _, _ = parse_wheel_filename(wheel.name)
        except InvalidWheelFilename as error:
            raise PackageWheelMismatchError(str(error)) from error
        version = next(
            (item for item in package.versions if Version(item.version) == artifact_version), None
        )
        if version is None:
            raise PackageWheelMismatchError(
                f"library {name!r} has no catalog version {artifact_version}"
            )
        verify_wheel(wheel, package, version, python_version)
        _install_wheel(package, wheel, repo_root)
        return
    wheel = wheel.expanduser().resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise ValueError(f"wheel does not exist: {wheel}")
    if canonicalize_name(wheel.name.split("-")[0]) != canonicalize_name(package.distribution):
        raise ValueError(f"wheel is not {package.distribution}: {wheel.name}")
    _require_backend_product(repo_root)
    _require_extension_parent(package, repo_root)
    _install_wheel(package, wheel, repo_root)


def add_released_package(
    name: str,
    repo_root: Path,
    source: str = DEFAULT_CATALOG_SOURCE,
    ref: str = DEFAULT_CATALOG_REF,
) -> CatalogVersion | CatalogLibraryVersion:
    """Resolve a package from the live catalog, build its released tag and install it.

    Every refusal happens before the product is touched.
    """

    package = read_catalog(source, ref).get_component(name)
    python_version = None
    if isinstance(package, CatalogLibrary):
        _require_tg_bot_product(repo_root)
        python_version = _library_python_version(repo_root)
        version = package.select(python_version)
    else:
        _require_backend_product(repo_root)
        version = package.select(CORE_VERSION)
        _require_extension_parent(package, repo_root)
    with TemporaryDirectory(prefix="kit-add-") as scratch:
        workdir = Path(scratch)
        project = fetch_package_source(source, package, version, workdir)
        wheel = build_wheel(project, workdir / "dist")
        if isinstance(package, CatalogLibrary):
            verify_wheel(wheel, package, version, python_version)
        else:
            verify_wheel(wheel, package, version)
        _install_wheel(package, wheel, repo_root)
    return version


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kit")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser(
        "add",
        help="install a library into tg_bot, or a runtime package/extension into backend",
    )
    add.add_argument("name", help="catalog component name, for example 'reminders' or 'textparse'")
    add.add_argument(
        "--wheel",
        type=Path,
        help="install this built artifact instead of resolving the live catalog",
    )
    add.add_argument(
        "--catalog-source",
        default=os.environ.get(CATALOG_SOURCE_ENV) or DEFAULT_CATALOG_SOURCE,
        help=f"git repository holding the catalog and package tags (env {CATALOG_SOURCE_ENV})",
    )
    add.add_argument(
        "--catalog-ref",
        default=os.environ.get(CATALOG_REF_ENV) or DEFAULT_CATALOG_REF,
        help=f"ref of the catalog source to read the catalog from (env {CATALOG_REF_ENV})",
    )
    add.add_argument("--product-root", type=Path, default=Path.cwd())
    return parser


def main() -> None:
    """Run the product-kit command line."""

    arguments = _parser().parse_args()
    try:
        if arguments.command == "add":
            product_root = arguments.product_root.resolve()
            if arguments.wheel is not None:
                add_package(arguments.name, arguments.wheel, product_root)
            else:
                version = add_released_package(
                    arguments.name,
                    product_root,
                    arguments.catalog_source,
                    arguments.catalog_ref,
                )
                print(f"kit: installed {arguments.name} {version.version} ({version.tag})")
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f"kit: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
