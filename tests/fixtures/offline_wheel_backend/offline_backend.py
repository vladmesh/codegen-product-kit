"""Minimal in-tree PEP 517 backend: catalog fixtures build real wheels without a package index.

It packs the entry-point module directory of ``[project]`` into a pure-Python wheel. Tests swap
it in for hatchling so ``uv build`` runs offline; it is never shipped to a product.
"""

from __future__ import annotations

import base64
import hashlib
from pathlib import Path
import tomllib
import zipfile


def _hash(data: bytes) -> str:
    digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
    return f"sha256={digest}"


def get_requires_for_build_wheel(config_settings: object = None) -> list[str]:
    return []


def build_wheel(
    wheel_directory: str, config_settings: object = None, metadata_directory: object = None
) -> str:
    project = tomllib.loads(Path("pyproject.toml").read_text())["project"]
    distribution = project["name"].replace("-", "_")
    version = project["version"]
    groups = project.get("entry-points", {})
    entry_points = "".join(
        f"[{group}]\n" + "".join(f"{name} = {value}\n" for name, value in entries.items()) + "\n"
        for group, entries in groups.items()
    )
    modules = {
        value.partition(":")[0].split(".")[0]
        for entries in groups.values()
        for value in entries.values()
    }
    dist_info = f"{distribution}-{version}.dist-info"
    files: dict[str, bytes] = {}
    for module in sorted(modules):
        for path in sorted(Path(module).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                files[path.as_posix()] = path.read_bytes()
    files[f"{dist_info}/METADATA"] = (
        f"Metadata-Version: 2.1\nName: {project['name']}\nVersion: {version}\n"
    ).encode()
    files[f"{dist_info}/WHEEL"] = (
        b"Wheel-Version: 1.0\nGenerator: offline_backend\nRoot-Is-Purelib: true\n"
        b"Tag: py3-none-any\n"
    )
    files[f"{dist_info}/entry_points.txt"] = entry_points.encode()
    record = "".join(f"{name},{_hash(data)},{len(data)}\n" for name, data in files.items())
    files[f"{dist_info}/RECORD"] = (record + f"{dist_info}/RECORD,,\n").encode()

    wheel_name = f"{distribution}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(Path(wheel_directory) / wheel_name, "w") as wheel:
        for name, data in files.items():
            wheel.writestr(name, data)
    return wheel_name
