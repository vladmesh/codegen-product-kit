"""Shared fixtures for tooling tests."""

from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

import pytest


@pytest.fixture
def fake_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Path, None, None]:
    """Provide an isolated repo root that tooling modules will use."""

    root = tmp_path / "repo"
    monkeypatch.setenv("SERVICE_TEMPLATE_ROOT", str(root))
    (root / "services").mkdir(parents=True)

    yield root

    monkeypatch.delenv("SERVICE_TEMPLATE_ROOT", raising=False)
