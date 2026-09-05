"""Small, deterministic product-kit commands."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import shutil
import subprocess
import sys

import yaml

from framework.generate import generate_all


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


def add_package(name: str, wheel: Path, repo_root: Path) -> None:
    """Install one known package artifact and regenerate the product contract."""

    if name != "reminders":
        raise ValueError(f"unknown package {name!r}; the kit currently knows only 'reminders'")
    wheel = wheel.expanduser().resolve()
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise ValueError(f"wheel does not exist: {wheel}")
    if not wheel.name.startswith("codegen_kit_reminders-"):
        raise ValueError(f"wheel is not codegen-kit-reminders: {wheel.name}")

    manifest_path = repo_root / "services/backend/manifest.yaml"
    backend_project = repo_root / "services/backend/pyproject.toml"
    if not manifest_path.is_file() or not backend_project.is_file():
        raise ValueError(f"{repo_root} is not a generated product with a backend")

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
    _allow_dynamic_dependency(backend_project, "codegen-kit-reminders")

    manifest = yaml.safe_load(manifest_path.read_text())
    packages = manifest.setdefault("packages", [])
    if name not in packages:
        packages.append(name)
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))

    _run(["uv", "sync", "--project", "services/backend", "--frozen"], repo_root)
    generate_all(repo_root)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kit")
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("add", help="install a package artifact into this product")
    add.add_argument("name", choices=["reminders"])
    add.add_argument("--wheel", type=Path, required=True)
    add.add_argument("--product-root", type=Path, default=Path.cwd())
    return parser


def main() -> None:
    """Run the product-kit command line."""

    arguments = _parser().parse_args()
    try:
        if arguments.command == "add":
            add_package(arguments.name, arguments.wheel, arguments.product_root.resolve())
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f"kit: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
