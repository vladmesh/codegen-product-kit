"""Resolve the one active package set used by every build-time contract."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from importlib import metadata
from pathlib import Path
import site
import subprocess

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from framework.spec.manifests import ServiceManifest
from framework.spec.packages import PackageManifest, load_package_manifest

ENTRY_POINT_GROUP = "codegen_kit.packages"
CORE_VERSION = "1.2.0"


class PackageResolutionError(ValueError):
    """The installed package environment and product allowlist disagree."""


@dataclass(frozen=True)
class ActivePackage:
    """One installed, allowlisted package shared by all product generators."""

    name: str
    manifest: PackageManifest
    package_root: Path
    manifest_sha256: str


def backend_site_packages(repo_root: Path) -> Path | None:
    """Return the backend environment's import directory when it exists."""

    python = repo_root / "services/backend/.venv/bin/python"
    if not python.is_file():
        current = Path(site.getsitepackages()[0])
        return current if current.is_dir() else None
    result = subprocess.run(  # noqa: S603
        [str(python), "-c", "import site; print(site.getsitepackages()[0])"],
        check=True,
        capture_output=True,
        text=True,
    )
    path = Path(result.stdout.strip())
    if not path.is_dir():
        raise PackageResolutionError(f"backend site-packages does not exist: {path}")
    return path


def _entry_points(site_packages: Path) -> dict[str, metadata.EntryPoint]:
    found: dict[str, metadata.EntryPoint] = {}
    for distribution in metadata.distributions(path=[str(site_packages)]):
        for entry_point in distribution.entry_points:
            if entry_point.group != ENTRY_POINT_GROUP:
                continue
            if entry_point.name in found:
                raise PackageResolutionError(
                    f"package entry point {entry_point.name!r} is installed more than once"
                )
            found[entry_point.name] = entry_point
    return found


def _package_root(name: str, entry_point: metadata.EntryPoint) -> Path:
    distribution = entry_point.dist
    if distribution is None:
        raise PackageResolutionError(f"{name}: entry point has no distribution metadata")
    module_name = entry_point.value.partition(":")[0]
    root = Path(distribution.locate_file(Path(*module_name.split("."))))
    if not module_name or not root.is_dir():
        raise PackageResolutionError(
            f"{name}: InvalidPackageModuleRootError: entry point module "
            f"{module_name!r} "
            "must resolve to an installed package directory"
        )
    return root


def _manifest_path(name: str, entry_point: metadata.EntryPoint, root: Path) -> Path:
    distribution = entry_point.dist
    if distribution is None:  # pragma: no cover - guarded by _package_root
        raise PackageResolutionError(f"package {name!r} has no distribution metadata")
    manifests = [item for item in distribution.files or () if item.name == "package.yaml"]
    if len(manifests) != 1:
        raise PackageResolutionError(f"package {name!r} must install exactly one package.yaml")
    path = Path(distribution.locate_file(manifests[0]))
    if path != root / "package.yaml":
        raise PackageResolutionError(
            f"{name}: package.yaml must be installed inside entry point module "
            "directory "
            f"for {name!r}"
        )
    return path


def _validate_identity(
    name: str, entry_point: metadata.EntryPoint, manifest: PackageManifest
) -> None:
    distribution = entry_point.dist
    if manifest.name != name:
        raise PackageResolutionError(
            f"{name}: package manifest name {manifest.name!r} does not match entry point"
        )
    if distribution is not None and manifest.version != distribution.version:
        raise PackageResolutionError(
            f"package {name!r} manifest version {manifest.version!r} does not match "
            f"distribution version {distribution.version!r}"
        )
    try:
        compatible = Version(CORE_VERSION) in SpecifierSet(manifest.requires_core)
    except (InvalidSpecifier, InvalidVersion, TypeError) as error:
        raise PackageResolutionError(
            f"package {name!r} has invalid core requirement {manifest.requires_core!r}"
        ) from error
    if not compatible:
        raise PackageResolutionError(
            f"package {name!r} requires core {manifest.requires_core}; core is {CORE_VERSION}"
        )
    distribution = entry_point.dist
    if distribution is None:  # pragma: no cover - guarded by _package_root
        return
    for resource in manifest.resources:
        resource_path = Path(distribution.locate_file(resource.path)).resolve()
        distribution_root = Path(distribution.locate_file("")).resolve()
        if not resource_path.is_relative_to(distribution_root) or not resource_path.exists():
            raise PackageResolutionError(
                f"package {name!r} resource {resource.name!r} does not exist at {resource.path!r}"
            )


def resolve_active_packages(
    repo_root: Path,
    manifests: dict[str, ServiceManifest],
    site_packages: Path | None = None,
    discovered_entry_points: dict[str, metadata.EntryPoint] | None = None,
) -> list[ActivePackage]:
    """Resolve installed and listed packages once, in product-manifest order."""

    backend = manifests.get("backend")
    if backend is None:
        return []
    listed = backend.packages
    discovered = discovered_entry_points
    if discovered is None:
        resolved_site = site_packages or backend_site_packages(repo_root)
        if resolved_site is None:
            if listed:
                raise PackageResolutionError(
                    "listed packages require an installed backend environment; run make setup first"
                )
            return []
        discovered = _entry_points(resolved_site)
    missing = sorted(set(listed) - set(discovered))
    if missing:
        raise PackageResolutionError(f"{missing[0]}: listed package has no installed entry point")

    active: list[ActivePackage] = []
    for name in listed:
        entry_point = discovered[name]
        root = _package_root(name, entry_point)
        manifest_path = _manifest_path(name, entry_point, root)
        manifest = load_package_manifest(manifest_path)
        _validate_identity(name, entry_point, manifest)
        active.append(
            ActivePackage(
                name=name,
                manifest=manifest,
                package_root=root,
                manifest_sha256=sha256(manifest_path.read_bytes()).hexdigest(),
            )
        )
    unlisted = sorted(set(discovered) - set(listed))
    if unlisted:
        raise PackageResolutionError(
            f"installed package {unlisted[0]!r} is not listed in the product manifest"
        )
    return active
