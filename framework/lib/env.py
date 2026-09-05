"""Environment helpers for script tooling."""

from __future__ import annotations

import os
from pathlib import Path


def get_repo_root() -> Path:
    """Return the repository root, honoring SERVICE_TEMPLATE_ROOT overrides."""

    override = os.environ.get("SERVICE_TEMPLATE_ROOT")
    if override:
        return Path(override).resolve()
    for candidate in (Path.cwd(), *Path.cwd().parents):
        if (candidate / "services.yml").exists() or (candidate / "copier.yml").exists():
            return candidate

    raise RuntimeError(
        "Could not find a codegen product root; run from the product checkout or set "
        "SERVICE_TEMPLATE_ROOT"
    )
