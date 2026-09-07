"""Tests for shared filesystem helpers."""

import os
from pathlib import Path

from framework.lib.fs import GENERATED_FILE_MODE, atomic_write_text


def test_atomic_write_uses_public_read_mode(tmp_path: Path) -> None:
    """Generated files are readable by runtime users other than their owner."""
    output = tmp_path / "generated.py"

    atomic_write_text(output, "value = 1\n")

    assert output.stat().st_mode & 0o777 == GENERATED_FILE_MODE


def test_atomic_rewrite_replaces_complete_file_with_final_mode(tmp_path: Path, monkeypatch) -> None:
    """The destination stays complete until a ready, correctly-mode temp replaces it."""
    output = tmp_path / "generated.py"
    output.write_text("old content\n", encoding="utf-8")
    output.chmod(0o600)
    real_replace = os.replace

    def inspect_then_replace(source: str, destination: Path) -> None:
        assert output.read_text(encoding="utf-8") == "old content\n"
        temp = Path(source)
        assert temp.read_text(encoding="utf-8") == "new content\n"
        assert temp.stat().st_mode & 0o777 == GENERATED_FILE_MODE
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", inspect_then_replace)

    atomic_write_text(output, "new content\n")

    assert output.read_text(encoding="utf-8") == "new content\n"
    assert output.stat().st_mode & 0o777 == GENERATED_FILE_MODE
