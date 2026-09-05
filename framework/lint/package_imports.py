"""Lint installed package imports against package protocol v1 declarations."""

from __future__ import annotations

import argparse
from importlib import metadata
from pathlib import Path

import yaml

from framework.spec.packages import lint_package_imports, parse_package_manifest

ENTRY_POINT_GROUP = "codegen_kit.packages"
INVALID_MODULE_ROOT = "InvalidPackageModuleRootError"


def _package_entry_points(distribution_path: Path | None) -> dict[str, metadata.EntryPoint]:
    """Read package entry points from the active or an explicit installation."""

    if distribution_path is None:
        entry_points = metadata.entry_points(group=ENTRY_POINT_GROUP)
    else:
        entry_points = (
            entry_point
            for distribution in metadata.distributions(path=[str(distribution_path)])
            for entry_point in distribution.entry_points
            if entry_point.group == ENTRY_POINT_GROUP
        )
    return {entry_point.name: entry_point for entry_point in entry_points}


def _package_root(
    entry_point: metadata.EntryPoint,
    distribution: metadata.Distribution,
) -> tuple[Path, Path] | str:
    """Resolve the protocol-defined package directory and relative module path."""

    module_name = entry_point.value.partition(":")[0]
    module_path = Path(*module_name.split("."))
    package_root = Path(distribution.locate_file(module_path))
    if not module_name or not package_root.is_dir():
        return (
            f"{INVALID_MODULE_ROOT}: entry point module {module_name!r} "
            "must resolve to an installed package directory"
        )
    return package_root, module_path


def _manifest_path(
    name: str,
    distribution: metadata.Distribution,
    package_root: Path,
) -> Path | str:
    """Resolve the sole manifest at its protocol-defined package location."""

    manifest_files = [file for file in distribution.files or () if file.name == "package.yaml"]
    if len(manifest_files) != 1:
        return f"distribution must contain exactly one package.yaml; found {len(manifest_files)}"
    manifest_path = Path(distribution.locate_file(manifest_files[0]))
    if manifest_path != package_root / "package.yaml":
        return f"package.yaml must be installed inside entry point module directory for {name!r}"
    return manifest_path


def lint_installed_packages(repo_root: Path, distribution_path: Path | None = None) -> list[str]:
    """Lint every package explicitly listed by the backend service manifest."""

    product = yaml.safe_load((repo_root / "services/backend/manifest.yaml").read_text())
    listed = set(product.get("packages", []))
    violations: list[str] = []
    entry_points = _package_entry_points(distribution_path)
    for name in sorted(listed - set(entry_points)):
        violations.append(f"{name}: listed package has no installed entry point")
    for name in sorted(listed & set(entry_points)):
        entry_point = entry_points[name]
        distribution = entry_point.dist
        if distribution is None:
            violations.append(f"{name}: entry point has no distribution metadata")
            continue
        resolved_root = _package_root(entry_point, distribution)
        if isinstance(resolved_root, str):
            violations.append(f"{name}: {resolved_root}")
            continue
        package_root, _ = resolved_root
        resolved_manifest = _manifest_path(name, distribution, package_root)
        if isinstance(resolved_manifest, str):
            violations.append(f"{name}: {resolved_manifest}")
            continue
        manifest = parse_package_manifest(yaml.safe_load(resolved_manifest.read_text()))
        if manifest.name != name:
            violations.append(
                f"{name}: package manifest name {manifest.name!r} does not match entry point"
            )
            continue
        module_root = entry_point.value.partition(":")[0].partition(".")[0]
        violations.extend(
            f"{name}: {item}"
            for item in lint_package_imports(
                package_root,
                manifest,
                module_root=module_root,
            )
        )
    return violations


def _distribution_path(value: str | None) -> Path | None:
    """Validate an explicitly supplied site-packages path without a vacuous fallback."""

    if value is None:
        return None
    if not value.strip():
        raise ValueError("--site-packages must not be empty")
    path = Path(value)
    if not path.is_dir():
        raise ValueError(f"--site-packages is not an existing directory: {path}")
    return path


def main() -> int:
    """Run the package import check for the current generated product."""

    parser = argparse.ArgumentParser()
    parser.add_argument("--site-packages")
    arguments = parser.parse_args()
    try:
        distribution_path = _distribution_path(arguments.site_packages)
    except ValueError as error:
        print(f"Package import lint FAILED: {error}")
        return 1
    violations = lint_installed_packages(Path.cwd(), distribution_path)
    if violations:
        print("Package import lint FAILED:")
        print("\n".join(f"  - {item}" for item in violations))
        return 1
    print("Package import lint PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
