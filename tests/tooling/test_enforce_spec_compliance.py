"""Tests for enforce_spec_compliance module."""

from __future__ import annotations

import pytest


def test_is_violation_base_model(fake_repo) -> None:
    """Test is_violation detects BaseModel inheritance."""
    root = fake_repo

    import ast

    import framework.enforce_spec_compliance as enforce_mod

    # Create a file with BaseModel violation
    test_file = root / "services" / "test_service" / "src" / "bad_model.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        "from pydantic import BaseModel\n\nclass BadModel(BaseModel):\n    pass\n",
        encoding="utf-8",
    )

    content = test_file.read_text(encoding="utf-8")
    tree = ast.parse(content)

    violations_found = False
    for node in ast.walk(tree):
        if enforce_mod.is_violation(node):
            violations_found = True
            break

    assert violations_found


def test_is_violation_api_router(fake_repo) -> None:
    """Test is_violation detects APIRouter instantiation."""
    root = fake_repo

    import ast

    import framework.enforce_spec_compliance as enforce_mod

    # Create a file with APIRouter violation
    test_file = root / "services" / "test_service" / "src" / "bad_router.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        "from fastapi import APIRouter\n\nrouter = APIRouter()\n", encoding="utf-8"
    )

    content = test_file.read_text(encoding="utf-8")
    tree = ast.parse(content)

    violations_found = False
    for node in ast.walk(tree):
        if enforce_mod.is_violation(node):
            violations_found = True
            break

    assert violations_found


def test_check_file_no_violations(fake_repo) -> None:
    """Test check_file with no violations."""
    root = fake_repo

    import framework.enforce_spec_compliance as enforce_mod

    # Create a clean file
    test_file = root / "services" / "test_service" / "src" / "clean.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def hello() -> str:\n    return 'world'\n", encoding="utf-8")

    violations = enforce_mod.check_file(test_file)
    assert len(violations) == 0


def test_check_file_with_violation(fake_repo) -> None:
    """Test check_file detects violations."""
    root = fake_repo

    import framework.enforce_spec_compliance as enforce_mod

    # Create a file with violation
    test_file = root / "services" / "test_service" / "src" / "violation.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        "from pydantic import BaseModel\n\nclass Bad(BaseModel):\n    pass\n",
        encoding="utf-8",
    )

    violations = enforce_mod.check_file(test_file)
    # Should find at least one violation (the BaseModel class)
    assert len(violations) >= 1
    # Check that violation message mentions BaseModel or Pydantic
    violation_messages = [msg for _, msg in violations]
    assert any("BaseModel" in msg or "Pydantic" in msg for msg in violation_messages)


def test_check_file_with_noqa(fake_repo) -> None:
    """Test check_file ignores violations with noqa comment."""
    root = fake_repo

    import framework.enforce_spec_compliance as enforce_mod

    # Create a file with violation but noqa comment
    test_file = root / "services" / "test_service" / "src" / "noqa.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        "from pydantic import BaseModel\n\nclass Bad(BaseModel):  # noqa\n    pass\n",
        encoding="utf-8",
    )

    violations = enforce_mod.check_file(test_file)
    # Should be ignored due to noqa
    assert len(violations) == 0


def test_check_file_invalid_syntax(fake_repo) -> None:
    """Test check_file handles invalid syntax gracefully."""
    root = fake_repo

    import framework.enforce_spec_compliance as enforce_mod

    # Create a file with invalid syntax
    test_file = root / "services" / "test_service" / "src" / "invalid.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def invalid syntax here!!!", encoding="utf-8")

    violations = enforce_mod.check_file(test_file)
    # Should return empty list on parse error
    assert isinstance(violations, list)


def test_enforce_spec_compliance_main_no_violations(
    fake_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test main function with no violations."""
    root = fake_repo

    # Create services directory with clean files
    service_dir = root / "services" / "test_service" / "src"
    service_dir.mkdir(parents=True, exist_ok=True)
    clean_file = service_dir / "clean.py"
    clean_file.write_text("def hello() -> str:\n    return 'world'\n", encoding="utf-8")

    import sys

    import framework.enforce_spec_compliance as enforce_mod

    # Mock sys.exit to avoid actually exiting
    original_exit = sys.exit
    exit_called = []

    def mock_exit(code: int) -> None:
        exit_called.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(sys, "exit", mock_exit)

    try:
        enforce_mod.main()
        # Should not exit with error code
        assert len(exit_called) == 0 or exit_called[0] == 0
    except SystemExit as e:
        # If it exits, should be with code 0 (success)
        assert e.code == 0
    finally:
        sys.exit = original_exit


def test_enforce_spec_compliance_main_with_violations(
    fake_repo, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test main function with violations."""
    root = fake_repo

    # Create services directory with violation
    service_dir = root / "services" / "test_service" / "src"
    service_dir.mkdir(parents=True, exist_ok=True)
    bad_file = service_dir / "bad.py"
    bad_file.write_text(
        "from pydantic import BaseModel\n\nclass Bad(BaseModel):\n    pass\n",
        encoding="utf-8",
    )

    import sys

    import framework.enforce_spec_compliance as enforce_mod

    # Mock sys.exit to capture exit code
    exit_called = []

    def mock_exit(code: int) -> None:
        exit_called.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(sys, "exit", mock_exit)

    try:
        enforce_mod.main()
    except SystemExit as e:
        # Should exit with error code 1
        assert e.code == 1
        assert len(exit_called) > 0
        assert exit_called[0] == 1
