"""Package protocol v1 manifest and import-boundary tests."""

import ast
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest
import yaml

from framework.lint import package_imports
from framework.spec.package_resolution import CORE_VERSION
from framework.spec.packages import (
    MalformedPackagePrefixError,
    MissingPackageIdentityError,
    UnimplementedDeploymentModeError,
    UnknownPackageManifestFieldError,
    lint_package_imports,
    load_package_manifest,
    parse_package_manifest,
)

FIXTURE = Path(__file__).parents[1] / "fixtures/synthetic_package/synthetic_package/package.yaml"
REMINDERS = (
    Path(__file__).parents[2] / "packages/codegen-kit-reminders/codegen_kit_reminders/package.yaml"
)


def test_build_time_and_runtime_core_versions_stay_equal() -> None:
    """A one-sided façade bump must fail before generation and runtime diverge."""

    runtime_source = Path(__file__).parents[2] / "template/codegen_kit/packages.py"
    module = ast.parse(runtime_source.read_text())
    runtime_version = next(
        statement.value.value
        for statement in module.body
        if isinstance(statement, ast.Assign)
        and any(
            isinstance(target, ast.Name) and target.id == "CORE_VERSION"
            for target in statement.targets
        )
        and isinstance(statement.value, ast.Constant)
    )

    assert runtime_version == CORE_VERSION


def test_synthetic_package_manifest_is_valid() -> None:
    manifest = load_package_manifest(FIXTURE)
    assert manifest.name == "synthetic"
    assert manifest.http.prefix == "/synthetic"


def test_reminders_package_manifest_declares_only_the_implemented_deployment_mode() -> None:
    manifest = load_package_manifest(REMINDERS)

    assert manifest.name == "reminders"
    assert manifest.version == "0.1.0"
    assert manifest.deployment.modes == ["in_process"]
    assert manifest.jobs_schema["properties"]["tick"]["required"] == ["at"]
    assert manifest.events.publishes == ["reminders.due"]
    assert [(item.name, item.required) for item in manifest.environment] == [("REDIS_URL", True)]


@pytest.mark.parametrize(
    "deployment",
    [
        {"modes": []},
        {"modes": ["process"]},
        {"modes": ["in_process", "in_process"]},
        {"modes": ["in_process"], "unknown": True},
    ],
)
def test_package_deployment_declaration_is_fail_closed(deployment: object) -> None:
    data = yaml.safe_load(FIXTURE.read_text())
    data["deployment"] = deployment

    with pytest.raises(ValueError):
        parse_package_manifest(data)


@pytest.mark.parametrize("modes", [["container"], ["in_process", "container"]])
def test_package_deployment_refuses_unimplemented_container_mode(modes: list[str]) -> None:
    """A manifest must not promise a delivery form the runtime does not implement."""

    data = yaml.safe_load(FIXTURE.read_text())
    data["deployment"] = {"modes": modes}

    with pytest.raises(UnimplementedDeploymentModeError):
        parse_package_manifest(data)


def test_package_deployment_defaults_to_implemented_in_process_mode() -> None:
    data = yaml.safe_load(FIXTURE.read_text())
    data.pop("deployment")

    assert parse_package_manifest(data).deployment.modes == ["in_process"]


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"surprise": True}, UnknownPackageManifestFieldError),
        ({"http": {"prefix": "/valid", "surprise": True}}, UnknownPackageManifestFieldError),
        ({"name": ""}, MissingPackageIdentityError),
        ({"http": {"prefix": "relative"}}, MalformedPackagePrefixError),
    ],
)
def test_named_manifest_errors(change: dict[str, object], error: type[ValueError]) -> None:
    data = yaml.safe_load(FIXTURE.read_text())
    data.update(change)
    with pytest.raises(error):
        parse_package_manifest(data)


def test_package_import_lint_accepts_declared_dependencies() -> None:
    manifest = load_package_manifest(FIXTURE)
    source_root = FIXTURE.parent
    assert lint_package_imports(source_root, manifest) == []


def test_reminders_package_import_lint_uses_only_public_core_and_declared_dependencies() -> None:
    manifest = load_package_manifest(REMINDERS)

    assert lint_package_imports(REMINDERS.parent, manifest) == []


def test_reminders_due_event_identity_is_stable() -> None:
    identity_path = REMINDERS.parent / "identity.py"
    spec = importlib.util.spec_from_file_location("reminders_identity", identity_path)
    assert spec is not None and spec.loader is not None
    identity = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(identity)
    reminder_id = UUID("d39a1a90-a914-4d8b-b8fe-7659270b6a4f")

    assert identity.due_event_id(reminder_id) == identity.due_event_id(reminder_id)


def test_package_import_lint_rejects_product_internal_import(tmp_path: Path) -> None:
    manifest = load_package_manifest(FIXTURE)
    (tmp_path / "package.py").write_text("from services.backend.src.core import db\n")
    assert lint_package_imports(tmp_path, manifest) == [
        "package.py:1: forbidden import 'services.backend.src.core'"
    ]


def test_package_import_lint_does_not_allow_namesake_subdirectory(tmp_path: Path) -> None:
    manifest = load_package_manifest(FIXTURE)
    source_root = tmp_path / "synthetic_package"
    (source_root / "requests").mkdir(parents=True)
    (source_root / "requests/__init__.py").write_text("")
    (source_root / "module.py").write_text("import requests\n")

    assert lint_package_imports(source_root, manifest) == [
        "module.py:1: forbidden import 'requests'"
    ]


def test_package_import_lint_rejects_empty_source_directory(tmp_path: Path) -> None:
    manifest = load_package_manifest(FIXTURE)
    source_root = tmp_path / "synthetic_package"
    source_root.mkdir()

    assert lint_package_imports(source_root, manifest) == [
        "package sources could not be located for module 'synthetic_package'"
    ]


@pytest.mark.parametrize("value", ["", "   ", "/does/not/exist"])
def test_package_import_lint_rejects_invalid_site_packages(value: str) -> None:
    with pytest.raises(ValueError, match="--site-packages"):
        package_imports._distribution_path(value)


@pytest.mark.parametrize(
    ("distribution", "message"),
    [
        (None, "entry point has no distribution metadata"),
        (
            SimpleNamespace(locate_file=lambda path: Path("/missing") / path),
            "InvalidPackageModuleRootError: entry point module 'synthetic_package' "
            "must resolve to an installed package directory",
        ),
    ],
)
def test_installed_package_lint_reports_missing_manifest_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    distribution: object,
    message: str,
) -> None:
    manifest_path = tmp_path / "services/backend/manifest.yaml"
    manifest_path.parent.mkdir(parents=True)
    manifest_path.write_text("packages: [synthetic]\n")
    entry_point = SimpleNamespace(dist=distribution, value="synthetic_package:package")
    monkeypatch.setattr(
        package_imports,
        "_package_entry_points",
        lambda _path: {"synthetic": entry_point},
    )

    assert package_imports.lint_installed_packages(tmp_path) == [f"synthetic: {message}"]


def _publications_inside_package_transactions(source: str) -> list[str]:
    """Name every `publish_event` await that a `package_session` block encloses."""

    module = ast.parse(source)
    enclosed: list[str] = []
    for node in ast.walk(module):
        if not isinstance(node, ast.AsyncWith):
            continue
        opens_session = any(
            isinstance(item.context_expr, ast.Call)
            and isinstance(item.context_expr.func, ast.Name)
            and item.context_expr.func.id == "package_session"
            for item in node.items
        )
        if not opens_session:
            continue
        enclosed.extend(
            f"line {inner.lineno}"
            for inner in ast.walk(node)
            if isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Name)
            and inner.func.id == "publish_event"
        )
    return enclosed


def test_reminders_never_publishes_inside_an_open_package_transaction() -> None:
    """A stalled transport must never hold outbox rows locked by an open transaction."""

    runtime_source = (REMINDERS.parent / "runtime.py").read_text()

    assert _publications_inside_package_transactions(runtime_source) == []
