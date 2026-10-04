"""Read the live package catalog and build a released package wheel from its tag."""

from __future__ import annotations

import configparser
from email.message import Message
from email.parser import Parser
import io
import os
from pathlib import Path
import subprocess
import tarfile
from tempfile import TemporaryDirectory
import zipfile

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.tags import compatible_tags, cpython_tags
from packaging.utils import InvalidWheelFilename, canonicalize_name, parse_wheel_filename
from packaging.version import InvalidVersion, Version

from framework.catalog import (
    CATALOG_PATH,
    Catalog,
    CatalogError,
    CatalogLibrary,
    CatalogLibraryVersion,
    CatalogPackage,
    CatalogVersion,
    parse_catalog,
)
from framework.spec.package_resolution import ENTRY_POINT_GROUP

DEFAULT_CATALOG_SOURCE = "https://github.com/vladmesh/codegen-product-kit.git"
# A remote's HEAD is its default branch.
DEFAULT_CATALOG_REF = "HEAD"
CATALOG_SOURCE_ENV = "KIT_CATALOG_SOURCE"
CATALOG_REF_ENV = "KIT_CATALOG_REF"
# Variables that would point git at another repository than the scratch one it is given.
_GIT_LOCATION_ENV = ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY")


class CatalogSourceUnreachableError(CatalogError):
    """The catalog source or ref cannot be fetched."""


class CatalogNotFoundError(CatalogError):
    """The fetched source has no package catalog."""


class PackageNotPublishedError(CatalogError):
    """A catalog version's release tag does not exist at the source yet."""


class PackageBuildError(CatalogError):
    """The released package source cannot be turned into one wheel."""


class PackageWheelMismatchError(CatalogError):
    """The built wheel is not the distribution and version the catalog names."""


def _git(
    arguments: list[str], cwd: Path, error: type[CatalogError], message: str
) -> subprocess.CompletedProcess[bytes]:
    environment = {key: value for key, value in os.environ.items() if key not in _GIT_LOCATION_ENV}
    environment["GIT_TERMINAL_PROMPT"] = "0"
    result = subprocess.run(  # noqa: S603
        ["git", *arguments],  # noqa: S607
        cwd=cwd,
        capture_output=True,
        env=environment,
    )
    if result.returncode != 0:
        lines = result.stderr.decode(errors="replace").strip().splitlines()
        raise error(f"{message}: {lines[-1] if lines else f'git exited {result.returncode}'}")
    return result


def _fetch(
    source: str, ref: str, repository: Path, error: type[CatalogError], message: str
) -> None:
    repository.mkdir(parents=True, exist_ok=True)
    _git(["init", "--quiet"], repository, error, message)
    _git(["fetch", "--quiet", "--depth=1", "--no-tags", source, ref], repository, error, message)


def read_catalog(source: str, ref: str) -> Catalog:
    """Read the catalog live from ``source`` at ``ref``, never from the installed tooling."""

    with TemporaryDirectory(prefix="kit-catalog-") as scratch:
        repository = Path(scratch)
        _fetch(
            source,
            ref,
            repository,
            CatalogSourceUnreachableError,
            f"catalog source {source} at {ref} is unreachable",
        )
        shown = _git(
            ["show", f"FETCH_HEAD:{CATALOG_PATH}"],
            repository,
            CatalogNotFoundError,
            f"catalog source {source} at {ref} has no {CATALOG_PATH}",
        )
    return parse_catalog(shown.stdout.decode(), f"{source}@{ref}:{CATALOG_PATH}")


def fetch_package_source(
    source: str,
    package: CatalogPackage | CatalogLibrary,
    version: CatalogVersion | CatalogLibraryVersion,
    workdir: Path,
) -> Path:
    """Export the package directory at its release tag; refuse an unpublished tag."""

    tag_ref = f"refs/tags/{version.tag}"
    listed = _git(
        ["ls-remote", "--tags", source, tag_ref],
        workdir,
        CatalogSourceUnreachableError,
        f"package source {source} is unreachable",
    )
    if not listed.stdout.strip():
        raise PackageNotPublishedError(
            f"package {package.name!r} {version.version} is not published yet: "
            f"tag {version.tag} does not exist at {source}"
        )
    repository = workdir / "repository"
    _fetch(
        source,
        tag_ref,
        repository,
        CatalogSourceUnreachableError,
        f"package source {source} at {version.tag} is unreachable",
    )
    archive = _git(
        ["archive", "--format=tar", "FETCH_HEAD", "--", package.path],
        repository,
        PackageBuildError,
        f"tag {version.tag} has no package path {package.path!r}",
    )
    target = workdir / "source"
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as exported:
        exported.extractall(target, filter="data")
    return target / package.path


def build_wheel(project: Path, output: Path) -> Path:
    """Build exactly one wheel from a package source directory with ``uv build``."""

    result = subprocess.run(  # noqa: S603
        ["uv", "build", "--wheel", "--out-dir", str(output), str(project)],  # noqa: S607
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        lines = (result.stderr or result.stdout).strip().splitlines()
        detail = lines[-1] if lines else f"uv exited {result.returncode}"
        raise PackageBuildError(f"uv build failed for {project.name}: {detail}")
    wheels = sorted(output.glob("*.whl"))
    if len(wheels) != 1:
        raise PackageBuildError(f"uv build produced {len(wheels)} wheels for {project.name}")
    return wheels[0]


def _dist_info_file(archive: zipfile.ZipFile, name: str) -> str | None:
    found = [
        item
        for item in archive.namelist()
        if item.count("/") == 1
        and item.split("/")[0].endswith(".dist-info")
        and item.endswith(name)
    ]
    if len(found) > 1:
        raise PackageWheelMismatchError(f"wheel has {len(found)} dist-info/{name} files")
    return found[0] if found else None


def verify_wheel(
    wheel: Path,
    package: CatalogPackage | CatalogLibrary,
    version: CatalogVersion | CatalogLibraryVersion,
    python_version: str | None = None,
) -> None:
    """Refuse a wheel unless it is the catalog's distribution, version and entry point."""

    try:
        with zipfile.ZipFile(wheel) as archive:
            metadata_file = _dist_info_file(archive, "/METADATA")
            if metadata_file is None:
                raise PackageWheelMismatchError(f"{wheel.name} has no dist-info METADATA")
            metadata = Parser().parsestr(archive.read(metadata_file).decode())
            entry_points_file = _dist_info_file(archive, "/entry_points.txt")
            entry_points = configparser.ConfigParser(delimiters=("=",))
            entry_points.optionxform = str  # type: ignore[assignment,method-assign]
            if entry_points_file is not None:
                entry_points.read_string(archive.read(entry_points_file).decode())
            if isinstance(package, CatalogLibrary):
                _verify_library_headers(metadata)
                _verify_library_layout(archive, package, entry_points)
    except (zipfile.BadZipFile, OSError, UnicodeError, configparser.Error) as error:
        raise PackageWheelMismatchError(
            f"{wheel.name} is not a valid wheel archive: {error}"
        ) from error

    distribution = metadata.get("Name", "")
    if canonicalize_name(distribution) != canonicalize_name(package.distribution):
        raise PackageWheelMismatchError(
            f"built wheel distribution {distribution!r} is not catalog distribution "
            f"{package.distribution!r}"
        )
    built_version = metadata.get("Version", "")
    try:
        matches = Version(built_version) == Version(version.version)
    except InvalidVersion:
        matches = False
    if not matches:
        raise PackageWheelMismatchError(
            f"built wheel version {built_version!r} is not catalog version {version.version!r} "
            f"of package {package.name!r}"
        )
    if isinstance(package, CatalogLibrary):
        _verify_library_python(
            wheel, metadata.get("Requires-Python"), python_version, version, package.distribution
        )
    elif not entry_points.has_option(ENTRY_POINT_GROUP, package.name):
        raise PackageWheelMismatchError(
            f"built wheel declares no {ENTRY_POINT_GROUP} entry point {package.name!r}"
        )


def _verify_library_headers(metadata: Message) -> None:
    for field in ("Name", "Version", "Requires-Python"):
        if len(metadata.get_all(field, [])) != 1:
            raise PackageWheelMismatchError(f"library METADATA needs one {field}")


def _verify_library_layout(
    archive: zipfile.ZipFile, library: CatalogLibrary, entry_points: configparser.ConfigParser
) -> None:
    module_path = library.module.replace(".", "/")
    names = archive.namelist()
    if f"{module_path}/__init__.py" not in names and f"{module_path}.py" not in names:
        raise PackageWheelMismatchError(f"wheel has no import module {library.module!r}")
    if any(name.startswith("/") or ".." in Path(name).parts for name in names):
        raise PackageWheelMismatchError("library wheel contains an unsafe archive path")
    if len(set(names)) != len(names):
        raise PackageWheelMismatchError("library wheel contains duplicate archive paths")
    for required in ("/WHEEL", "/RECORD"):
        if _dist_info_file(archive, required) is None:
            raise PackageWheelMismatchError(f"library wheel has no dist-info{required}")
    info_directories = {
        name.split("/")[0] for name in names if name.split("/")[0].endswith(".dist-info")
    }
    if len(info_directories) != 1:
        raise PackageWheelMismatchError("library wheel must have exactly one dist-info directory")
    if entry_points.has_section(ENTRY_POINT_GROUP):
        raise PackageWheelMismatchError(f"library wheel must not declare {ENTRY_POINT_GROUP}")
    if any(name.endswith("/package.yaml") for name in names):
        raise PackageWheelMismatchError("library wheel must not contain package.yaml")


def _verify_library_python(
    wheel: Path,
    requires_python: str | None,
    python_version: str | None,
    version: CatalogVersion | CatalogLibraryVersion,
    expected_distribution: str,
) -> None:
    if python_version is None or not isinstance(version, CatalogLibraryVersion):
        raise PackageWheelMismatchError("library verification requires the target Python version")
    try:
        if not requires_python:
            raise PackageWheelMismatchError("library wheel has no Requires-Python")
        target = Version(python_version)
        if target not in SpecifierSet(requires_python) or target not in SpecifierSet(
            version.requires_python
        ):
            raise PackageWheelMismatchError(
                f"library wheel/catalog requires Python "
                f"{requires_python}/{version.requires_python}; "
                f"target Python is {python_version}"
            )
        distribution, artifact_version, _, tags = parse_wheel_filename(wheel.name)
        python_pair = (target.major, target.minor)
        interpreter = f"cp{target.major}{target.minor}"
        supported = set(cpython_tags(python_version=python_pair)) | set(
            compatible_tags(python_version=python_pair, interpreter=interpreter)
        )
        if not tags & supported:
            raise PackageWheelMismatchError(
                f"wheel tags are incompatible with Python {python_version}"
            )
        if artifact_version != Version(version.version):
            raise PackageWheelMismatchError("wheel filename version differs from catalog version")
        if distribution != canonicalize_name(expected_distribution):
            raise PackageWheelMismatchError("wheel filename distribution differs from METADATA")
    except (InvalidSpecifier, InvalidVersion, InvalidWheelFilename) as error:
        raise PackageWheelMismatchError(f"invalid library wheel metadata: {error}") from error
