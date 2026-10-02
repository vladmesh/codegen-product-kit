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

from packaging.utils import canonicalize_name
import yaml

from framework.catalog import Catalog, CatalogPackage, CatalogVersion, bundled_catalog
from framework.generate import generate_all
from framework.package_source import (
    CATALOG_REF_ENV,
    CATALOG_SOURCE_ENV,
    DEFAULT_CATALOG_REF,
    DEFAULT_CATALOG_SOURCE,
    build_wheel,
    fetch_package_source,
    read_catalog,
    verify_wheel,
)
from framework.spec.package_resolution import CORE_VERSION


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


def _install_wheel(package: CatalogPackage, wheel: Path, repo_root: Path) -> None:
    """Copy, lock, allowlist, synchronize and regenerate; the only product mutation."""

    manifest_path = repo_root / "services/backend/manifest.yaml"
    backend_project = repo_root / "services/backend/pyproject.toml"
    package_dir = repo_root / "services/backend/packages"
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
            "services/backend",
            "--no-sync",
            str(relative_wheel),
        ],
        repo_root,
    )
    _allow_dynamic_dependency(backend_project, package.distribution)

    manifest = yaml.safe_load(manifest_path.read_text())
    packages = manifest.setdefault("packages", [])
    if package.name not in packages:
        packages.append(package.name)
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))

    _run(["uv", "sync", "--project", "services/backend", "--frozen"], repo_root)
    generate_all(repo_root)


def add_package(name: str, wheel: Path, repo_root: Path, catalog: Catalog | None = None) -> None:
    """Install one explicit artifact of a catalog package and regenerate the product contract.

    The name and distribution are checked against ``catalog``, by default the catalog shipped
    with this tooling; the artifact's version is the caller's choice.
    """

    package = (catalog or bundled_catalog()).get(name)
    wheel = wheel.expanduser().resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise ValueError(f"wheel does not exist: {wheel}")
    if canonicalize_name(wheel.name.split("-")[0]) != canonicalize_name(package.distribution):
        raise ValueError(f"wheel is not {package.distribution}: {wheel.name}")
    _require_backend_product(repo_root)
    _install_wheel(package, wheel, repo_root)


def add_released_package(
    name: str,
    repo_root: Path,
    source: str = DEFAULT_CATALOG_SOURCE,
    ref: str = DEFAULT_CATALOG_REF,
) -> CatalogVersion:
    """Resolve a package from the live catalog, build its released tag and install it.

    Every refusal happens before the product is touched.
    """

    _require_backend_product(repo_root)
    package = read_catalog(source, ref).get(name)
    version = package.select(CORE_VERSION)
    with TemporaryDirectory(prefix="kit-add-") as scratch:
        workdir = Path(scratch)
        project = fetch_package_source(source, package, version, workdir)
        wheel = build_wheel(project, workdir / "dist")
        verify_wheel(wheel, package, version)
        _install_wheel(package, wheel, repo_root)
    return version


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kit")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser(
        "add", help="install a catalog package, or an explicit package artifact, into this product"
    )
    add.add_argument("name", help="catalog package name, for example 'reminders'")
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
