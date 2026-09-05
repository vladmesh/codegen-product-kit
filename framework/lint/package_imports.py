"""Lint installed package imports against package protocol v1 declarations."""

from __future__ import annotations

from importlib import metadata
from pathlib import Path

import yaml

from framework.spec.packages import lint_package_imports, parse_package_manifest

ENTRY_POINT_GROUP = "codegen_kit.packages"


def lint_installed_packages(repo_root: Path) -> list[str]:
    """Lint every package explicitly listed by the backend service manifest."""

    product = yaml.safe_load((repo_root / "services/backend/manifest.yaml").read_text())
    listed = set(product.get("packages", []))
    violations: list[str] = []
    entry_points = {
        entry_point.name: entry_point
        for entry_point in metadata.entry_points(group=ENTRY_POINT_GROUP)
    }
    for name in sorted(listed & set(entry_points)):
        entry_point = entry_points[name]
        distribution = entry_point.dist
        if distribution is None:
            continue
        manifest_files = [file for file in distribution.files or () if file.name == "package.yaml"]
        if len(manifest_files) != 1:
            continue
        manifest = parse_package_manifest(
            yaml.safe_load(Path(distribution.locate_file(manifest_files[0])).read_text())
        )
        module_root = entry_point.value.partition(":")[0].partition(".")[0]
        package_root = Path(distribution.locate_file(module_root))
        violations.extend(
            f"{name}: {item}" for item in lint_package_imports(package_root, manifest)
        )
    return violations


def main() -> int:
    """Run the package import check for the current generated product."""

    violations = lint_installed_packages(Path.cwd())
    if violations:
        print("Package import lint FAILED:")
        print("\n".join(f"  - {item}" for item in violations))
        return 1
    print("Package import lint PASSED.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
