"""End-to-end package discovery and activation in a generated product."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import importlib
from importlib import metadata
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
import yaml

from framework.lint.package_imports import lint_installed_packages

FIXTURE = Path(__file__).parents[1] / "fixtures/synthetic_package"
KIT_ROOT = Path(__file__).parents[2]
REMINDERS = KIT_ROOT / "packages/codegen-kit-reminders"
LINT_LAYOUT_FIXTURES = (
    Path(__file__).parents[1] / "fixtures/synthetic_single_module",
    Path(__file__).parents[1] / "fixtures/synthetic_missing_module",
    Path(__file__).parents[1] / "fixtures/synthetic_misplaced_manifest",
)


@pytest.fixture(scope="module")
def installed_synthetic(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("installed-synthetic")
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required to install the synthetic package")
    result = subprocess.run(  # noqa: S603
        [uv, "pip", "install", "--target", str(target), str(FIXTURE)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return target


@pytest.fixture(scope="module")
def installed_lint_layouts(tmp_path_factory: pytest.TempPathFactory) -> Path:
    target = tmp_path_factory.mktemp("installed-lint-layouts")
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required to install the synthetic packages")
    for fixture in LINT_LAYOUT_FIXTURES:
        result = subprocess.run(  # noqa: S603
            [uv, "pip", "install", "--target", str(target), str(fixture)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    return target


def _load_runtime(project_backend: Path, installed: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.syspath_prepend(str(project_backend))
    monkeypatch.syspath_prepend(str(installed))
    for name in tuple(sys.modules):
        if name == "codegen_kit" or name.startswith("codegen_kit."):
            sys.modules.pop(name)
    return importlib.import_module("codegen_kit.packages")


def _installed_entry_point(installed: Path, name: str) -> metadata.EntryPoint:
    return next(
        entry_point
        for distribution in metadata.distributions(path=[str(installed)])
        for entry_point in distribution.entry_points
        if entry_point.group == "codegen_kit.packages" and entry_point.name == name
    )


@pytest.fixture(scope="module")
def generated_backend_runtime(
    project_backend: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path]:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required to install the generated backend")
    runtime_project = tmp_path_factory.mktemp("backend-runtime") / "output"
    shutil.copytree(project_backend, runtime_project)
    python = runtime_project / "services/backend/.venv/bin/python"
    commands = [
        [uv, "sync", "--project", "services/backend", "--frozen"],
        [uv, "pip", "install", "--python", str(python), str(FIXTURE)],
    ]
    for command in commands:
        result = subprocess.run(
            command,
            cwd=runtime_project,
            capture_output=True,
            text=True,
        )  # noqa: S603
        assert result.returncode == 0, result.stdout + result.stderr
    return runtime_project, python


def test_real_distribution_activates_route_and_lifecycle(
    project_backend: Path, installed_synthetic: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _load_runtime(project_backend, installed_synthetic, monkeypatch)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await runtime.startup_packages(app)
        yield
        await runtime.shutdown_packages(app)

    application = FastAPI(lifespan=lifespan)
    runtime.configure_packages(application, ["synthetic"])

    assert any(
        entry_point.name == "synthetic"
        for entry_point in metadata.entry_points(group="codegen_kit.packages")
    )
    with TestClient(application) as client:
        assert client.get("/synthetic/status").json() == {"synthetic": True}
        assert application.state.synthetic_started is True
    assert application.state.synthetic_stopped is True


def test_package_database_is_owned_and_unowned_callers_fail_before_database_access(
    project_backend: Path, installed_synthetic: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _load_runtime(project_backend, installed_synthetic, monkeypatch)
    activated = runtime.discover_packages(["synthetic"])
    synthetic = importlib.import_module("synthetic_package")

    assert activated[0].manifest.database_schema == "synthetic"
    assert type(synthetic.database).__name__ == "PackageDatabase"

    codegen_kit = importlib.import_module("codegen_kit")
    sys.modules.pop("services.backend.src.core.db", None)
    with pytest.raises(TypeError):
        codegen_kit.package_database("reminders")
    with pytest.raises(runtime.PackageDatabaseOwnershipError):
        codegen_kit.package_database()
    assert "services.backend.src.core.db" not in sys.modules


def test_generated_factory_and_lifespan_activate_installed_package(
    generated_backend_runtime: tuple[Path, Path],
) -> None:
    runtime_project, python = generated_backend_runtime
    product_manifest = runtime_project / "services/backend/manifest.yaml"
    original_product = product_manifest.read_text()
    product = yaml.safe_load(original_product)
    product["packages"] = ["synthetic"]
    product_manifest.write_text(yaml.safe_dump(product, sort_keys=False))
    generated = subprocess.run(
        [sys.executable, "-m", "framework.generate"],
        cwd=runtime_project,
        capture_output=True,
        text=True,
    )  # noqa: S603
    assert generated.returncode == 0, generated.stdout + generated.stderr
    script = textwrap.dedent(
        """
        from fastapi.testclient import TestClient
        import shared.generated.events as events

        class Broker:
            connected = False
            closed = False

            async def connect(self):
                self.connected = True

            async def close(self):
                self.closed = True

        broker = Broker()
        events._broker = broker

        from services.backend.src.app.factory import create_app

        application = create_app()
        with TestClient(application) as client:
            assert broker.connected is True
            assert application.state.synthetic_started is True
            assert client.get("/synthetic/status").json() == {"synthetic": True}
        assert application.state.synthetic_stopped is True
        assert broker.closed is True
        """
    )
    try:
        result = subprocess.run(
            [str(python), "-c", script],
            cwd=runtime_project,
            capture_output=True,
            text=True,
        )  # noqa: S603
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        product_manifest.write_text(original_product)


def test_generated_settings_seed_transaction_and_routing(
    generated_backend_runtime: tuple[Path, Path],
) -> None:
    """Run the generated product's focused settings seed contract tests."""

    runtime_project, python = generated_backend_runtime
    product_manifest = runtime_project / "services/backend/manifest.yaml"
    original_product = product_manifest.read_text()
    product = yaml.safe_load(original_product)
    product["packages"] = ["synthetic"]
    product_manifest.write_text(yaml.safe_dump(product, sort_keys=False))
    try:
        generated = subprocess.run(
            [sys.executable, "-m", "framework.generate"],
            cwd=runtime_project,
            capture_output=True,
            text=True,
        )  # noqa: S603
        assert generated.returncode == 0, generated.stdout + generated.stderr
        result = subprocess.run(
            [
                str(python),
                "-m",
                "pytest",
                "-q",
                "services/backend/tests/unit/test_product_settings.py",
            ],
            cwd=runtime_project,
            capture_output=True,
            text=True,
        )  # noqa: S603
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        product_manifest.write_text(original_product)


def test_generated_core_timer_loop_fires_through_the_jobs_core(
    generated_backend_runtime: tuple[Path, Path],
) -> None:
    """Run the generated product's timer-loop tests beside its unchanged jobs-core tests."""

    runtime_project, python = generated_backend_runtime
    product_manifest = runtime_project / "services/backend/manifest.yaml"
    original_product = product_manifest.read_text()
    product = yaml.safe_load(original_product)
    product["packages"] = ["synthetic"]
    product_manifest.write_text(yaml.safe_dump(product, sort_keys=False))
    try:
        generated = subprocess.run(
            [sys.executable, "-m", "framework.generate"],
            cwd=runtime_project,
            capture_output=True,
            text=True,
        )  # noqa: S603
        assert generated.returncode == 0, generated.stdout + generated.stderr
        jobs = (runtime_project / "services/backend/src/generated/jobs_schemas.py").read_text()
        assert "JOB_TIMERS: dict[str, int] = {}" in jobs
        result = subprocess.run(
            [
                str(python),
                "-m",
                "pytest",
                "-q",
                "services/backend/tests/unit/test_job_timers.py",
                "services/backend/tests/unit/test_jobs.py",
            ],
            cwd=runtime_project,
            capture_output=True,
            text=True,
        )  # noqa: S603
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        product_manifest.write_text(original_product)


REMINDERS_CALLER_IDENTITY_TEST = textwrap.dedent(
    '''
    """The installed reminders routes act only for the core-verified caller."""

    from __future__ import annotations

    from collections.abc import AsyncIterator
    from contextlib import asynccontextmanager
    from datetime import datetime
    import sqlite3
    from uuid import UUID

    from fastapi import FastAPI, status
    from httpx import AsyncClient
    import pytest
    import pytest_asyncio
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession

    from codegen_kit_reminders import api
    from services.backend.src.app.repositories.user import UserRepository
    from services.backend.src.core.settings import get_settings

    # The package's SQL is PostgreSQL-shaped; these adapters let the SQLite unit database
    # store the same values without changing what the routes send.
    sqlite3.register_adapter(UUID, str)
    sqlite3.register_adapter(datetime, datetime.isoformat)


    class SessionDatabase:
        def __init__(self, session: AsyncSession) -> None:
            self._session = session

        @asynccontextmanager
        async def session(self) -> AsyncIterator[AsyncSession]:
            yield self._session


    @pytest_asyncio.fixture
    async def reminders_app(
        app: FastAPI, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> FastAPI:
        await db_session.execute(
            text(
                "CREATE TABLE IF NOT EXISTS reminders ("
                "id TEXT PRIMARY KEY, user_ref TEXT NOT NULL, text TEXT NOT NULL, "
                "remind_at TEXT NOT NULL, state TEXT NOT NULL, created_at TEXT NOT NULL, "
                "cancelled_at TEXT, due_at TEXT, emitted_at TEXT)"
            )
        )
        # The product activated the installed package and mounted its router at /reminders;
        # only the package's PostgreSQL schema session is replaced by the unit database.
        monkeypatch.setattr(api, "database", SessionDatabase(db_session))
        users = UserRepository(db_session)
        for external_id in ("101", "202"):
            await users.grant("telegram", external_id)
        await users.grant("telegram", "303")
        await users.revoke("telegram", "303")
        return app


    def _as(external_id: str) -> dict[str, str]:
        return {
            "X-Identity-Capability": get_settings().user_identity_capability,
            "X-User-Channel": "telegram",
            "X-User-External-Id": external_id,
        }


    @pytest.mark.asyncio
    async def test_reminders_belong_to_the_verified_caller(
        reminders_app: FastAPI, client: AsyncClient
    ) -> None:
        body = {"text": "water the plants", "remind_at": "2040-01-01T00:00:00Z"}
        first = await client.post("/reminders", headers=_as("101"), json=body)
        second = await client.post(
            "/reminders", headers=_as("202"), json={**body, "text": "call home"}
        )
        assert first.status_code == status.HTTP_201_CREATED, first.text
        assert second.status_code == status.HTTP_201_CREATED, second.text
        assert first.json()["user_ref"] == "telegram:101"
        assert second.json()["user_ref"] == "telegram:202"

        first_list = await client.get("/reminders", headers=_as("101"))
        second_list = await client.get("/reminders", headers=_as("202"))
        assert [item["id"] for item in first_list.json()] == [first.json()["id"]]
        assert [item["id"] for item in second_list.json()] == [second.json()["id"]]

        foreign = await client.delete(f"/reminders/{second.json()['id']}", headers=_as("101"))
        assert foreign.status_code == status.HTTP_404_NOT_FOUND
        missing = await client.delete(
            "/reminders/00000000-0000-0000-0000-000000000000", headers=_as("101")
        )
        assert missing.status_code == status.HTTP_404_NOT_FOUND
        assert foreign.json() == missing.json()

        own = await client.delete(f"/reminders/{second.json()['id']}", headers=_as("202"))
        assert own.status_code == status.HTTP_200_OK
        assert own.json()["state"] == "cancelled"
        first_after = await client.get("/reminders", headers=_as("101"))
        assert first_after.json()[0]["state"] == "scheduled"


    @pytest.mark.asyncio
    async def test_reminders_refuse_requests_without_verified_identity(
        reminders_app: FastAPI, client: AsyncClient
    ) -> None:
        body = {"text": "nobody", "remind_at": "2040-01-01T00:00:00Z"}
        created = await client.post("/reminders", headers=_as("101"), json=body)
        reminder_id = created.json()["id"]

        anonymous = [
            await client.post("/reminders", json=body),
            await client.get("/reminders"),
            await client.delete(f"/reminders/{reminder_id}"),
        ]
        assert {response.status_code for response in anonymous} == {
            status.HTTP_401_UNAUTHORIZED
        }
        inactive = await client.get("/reminders", headers=_as("303"))
        assert inactive.status_code == status.HTTP_403_FORBIDDEN
        unknown = await client.get("/reminders", headers=_as("404"))
        assert unknown.status_code == status.HTTP_403_FORBIDDEN

        # The owner is never an input: a body or query user_ref cannot select another owner.
        smuggled = await client.post(
            "/reminders", headers=_as("202"), json={**body, "user_ref": "telegram:101"}
        )
        assert smuggled.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        queried = await client.get(
            "/reminders", headers=_as("202"), params={"user_ref": "telegram:101"}
        )
        assert queried.status_code == status.HTTP_200_OK
        assert queried.json() == []
        still_mine = await client.get("/reminders", headers=_as("101"))
        assert [item["id"] for item in still_mine.json()] == [reminder_id]


    @pytest.mark.asyncio
    async def test_reminders_contract_has_no_owner_input(reminders_app: FastAPI) -> None:
        operations = reminders_app.openapi()["paths"]
        for path in ("/reminders", "/reminders/{reminder_id}"):
            for operation in operations[path].values():
                names = {parameter["name"] for parameter in operation.get("parameters", [])}
                assert "user_ref" not in names
        create = reminders_app.openapi()["components"]["schemas"]["ReminderCreate"]
        assert set(create["properties"]) == {"text", "remind_at"}
    '''
)


def test_generated_reminders_routes_act_only_for_the_verified_caller(
    generated_backend_runtime: tuple[Path, Path],
) -> None:
    """The installed reminders routes take their owner from the core caller identity."""

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required to install the reminders package")
    runtime_project, python = generated_backend_runtime
    product_manifest = runtime_project / "services/backend/manifest.yaml"
    original_product = product_manifest.read_text()
    product = yaml.safe_load(original_product)
    product["packages"] = ["synthetic", "reminders"]
    product_test = runtime_project / "services/backend/tests/unit/test_reminders_identity.py"
    installed = subprocess.run(
        [uv, "pip", "install", "--python", str(python), str(REMINDERS)],
        capture_output=True,
        text=True,
    )  # noqa: S603
    assert installed.returncode == 0, installed.stdout + installed.stderr
    try:
        product_manifest.write_text(yaml.safe_dump(product, sort_keys=False))
        generated = subprocess.run(
            [sys.executable, "-m", "framework.generate"],
            cwd=runtime_project,
            capture_output=True,
            text=True,
        )  # noqa: S603
        assert generated.returncode == 0, generated.stdout + generated.stderr
        product_test.write_text(REMINDERS_CALLER_IDENTITY_TEST)
        result = subprocess.run(
            [str(python), "-m", "pytest", "-q", str(product_test.relative_to(runtime_project))],
            cwd=runtime_project,
            capture_output=True,
            text=True,
        )  # noqa: S603
        assert result.returncode == 0, result.stdout + result.stderr
        assert "3 passed" in result.stdout
    finally:
        product_test.unlink(missing_ok=True)
        product_manifest.write_text(original_product)
        # Other tests in this module share the runtime and list only the synthetic package.
        subprocess.run(
            [uv, "pip", "uninstall", "--python", str(python), "codegen-kit-reminders"],
            capture_output=True,
            text=True,
        )  # noqa: S603


def test_generated_lifespan_cleans_up_partial_package_startup(
    generated_backend_runtime: tuple[Path, Path],
) -> None:
    runtime_project, python = generated_backend_runtime
    script = textwrap.dedent(
        """
        from types import SimpleNamespace

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import shared.generated.events as events

        calls = []

        class Broker:
            closed = False

            async def connect(self):
                calls.append("broker-connect")

            async def close(self):
                self.closed = True
                calls.append("broker-close")

        class Package:
            def __init__(self, name, fail_start=False, fail_stop=False):
                self.name = name
                self.fail_start = fail_start
                self.fail_stop = fail_stop

            async def startup(self, application):
                calls.append(f"{self.name}-start")
                if self.fail_start:
                    raise RuntimeError("startup failed")

            async def shutdown(self, application):
                calls.append(f"{self.name}-stop")
                if self.fail_stop:
                    raise RuntimeError("shutdown failed")

        broker = Broker()
        events._broker = broker

        from services.backend.src.app.lifespan import lifespan

        application = FastAPI(lifespan=lifespan)
        application.state.codegen_packages = [
            SimpleNamespace(runtime=Package("first")),
            SimpleNamespace(runtime=Package("second", fail_stop=True)),
            SimpleNamespace(runtime=Package("third", fail_start=True)),
            SimpleNamespace(runtime=Package("fourth")),
        ]
        try:
            with TestClient(application):
                pass
        except RuntimeError as error:
            assert str(error) == "startup failed"
        else:
            raise AssertionError("partial startup unexpectedly succeeded")

        assert calls == [
            "broker-connect",
            "first-start",
            "second-start",
            "third-start",
            "second-stop",
            "first-stop",
            "broker-close",
        ]
        assert broker.closed is True
        """
    )
    result = subprocess.run(
        [str(python), "-c", script],
        cwd=runtime_project,
        capture_output=True,
        text=True,
    )  # noqa: S603
    assert result.returncode == 0, result.stdout + result.stderr


def test_shutdown_without_startup_is_safe(
    project_backend: Path, installed_synthetic: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _load_runtime(project_backend, installed_synthetic, monkeypatch)
    asyncio.run(runtime.shutdown_packages(FastAPI()))


def test_shutdown_never_suppresses_cancellation(
    project_backend: Path, installed_synthetic: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _load_runtime(project_backend, installed_synthetic, monkeypatch)

    class CancellingPackage:
        async def shutdown(self, application: FastAPI) -> None:
            raise asyncio.CancelledError

    application = FastAPI()
    application.state.codegen_started_packages = [SimpleNamespace(runtime=CancellingPackage())]
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime.shutdown_packages(application, suppress_errors=True))


def test_duplicate_package_prefix_has_named_activation_error(
    project_backend: Path,
    installed_synthetic: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _load_runtime(project_backend, installed_synthetic, monkeypatch)
    package_manifest = runtime.PackageManifest(
        protocol_version=1,
        name="first",
        version="1.0.0",
        requires_core=">=2,<3",
        http_prefix="/shared",
    )
    packages = [
        SimpleNamespace(manifest=package_manifest),
        SimpleNamespace(
            manifest=runtime.PackageManifest(
                protocol_version=1,
                name="second",
                version="1.0.0",
                requires_core=">=2,<3",
                http_prefix="/shared",
            )
        ),
    ]

    with pytest.raises(runtime.DuplicatePackageHttpPrefixError):
        runtime._validate_http_prefixes(packages)


def test_installed_but_unlisted_is_named_and_does_not_mount_route(
    project_backend: Path, installed_synthetic: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _load_runtime(project_backend, installed_synthetic, monkeypatch)
    application = FastAPI()
    with pytest.raises(runtime.InstalledPackageNotListedError):
        runtime.configure_packages(application, [])
    assert all(route.path != "/synthetic/status" for route in application.routes)


def test_installed_package_import_lint_reads_real_distribution(
    project_backend: Path, installed_synthetic: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _load_runtime(project_backend, installed_synthetic, monkeypatch)
    product_manifest = project_backend / "services/backend/manifest.yaml"
    original_product = product_manifest.read_text()
    product = yaml.safe_load(original_product)
    product["packages"] = ["synthetic"]
    product_manifest.write_text(yaml.safe_dump(product, sort_keys=False))
    source = installed_synthetic / "synthetic_package/__init__.py"
    package_manifest = installed_synthetic / "synthetic_package/package.yaml"
    original_source = source.read_text()
    original_manifest = package_manifest.read_text()
    try:
        assert lint_installed_packages(project_backend, installed_synthetic) == []
        source.write_text(original_source + "\nfrom services.backend.src.core import db\n")
        violations = lint_installed_packages(project_backend, installed_synthetic)
        assert len(violations) == 1
        assert "forbidden import 'services.backend.src.core'" in violations[0]
        source.write_text(original_source)
        manifest_data = yaml.safe_load(original_manifest)
        manifest_data["name"] = "other"
        package_manifest.write_text(yaml.safe_dump(manifest_data, sort_keys=False))
        assert lint_installed_packages(project_backend, installed_synthetic) == [
            "synthetic: package manifest name 'other' does not match entry point"
        ]
    finally:
        source.write_text(original_source)
        package_manifest.write_text(original_manifest)
        product_manifest.write_text(original_product)


def test_installed_package_import_lint_rejects_single_file_module(
    tmp_path: Path,
    installed_lint_layouts: Path,
) -> None:
    product_manifest = tmp_path / "services/backend/manifest.yaml"
    product_manifest.parent.mkdir(parents=True)
    product_manifest.write_text("packages: [single-module]\n")
    assert lint_installed_packages(tmp_path, installed_lint_layouts) == [
        "single-module: InvalidPackageModuleRootError: entry point module "
        "'single_module' must resolve to an installed package directory"
    ]


def test_installed_package_import_lint_rejects_missing_module_root(
    tmp_path: Path,
    installed_lint_layouts: Path,
) -> None:
    product_manifest = tmp_path / "services/backend/manifest.yaml"
    product_manifest.parent.mkdir(parents=True)
    product_manifest.write_text("packages: [missing-module]\n")

    assert lint_installed_packages(tmp_path, installed_lint_layouts) == [
        "missing-module: InvalidPackageModuleRootError: entry point module "
        "'missing_module' must resolve to an installed package directory"
    ]


def test_installed_package_import_lint_rejects_misplaced_manifest(
    tmp_path: Path,
    installed_lint_layouts: Path,
) -> None:
    product_manifest = tmp_path / "services/backend/manifest.yaml"
    product_manifest.parent.mkdir(parents=True)
    product_manifest.write_text("packages: [misplaced]\n")

    assert lint_installed_packages(tmp_path, installed_lint_layouts) == [
        "misplaced: package.yaml must be installed inside entry point module directory "
        "for 'misplaced'"
    ]


@pytest.mark.parametrize("name", ["single-module", "missing-module"])
def test_activation_rejects_non_directory_module_roots(
    name: str,
    project_backend: Path,
    installed_lint_layouts: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _load_runtime(project_backend, installed_lint_layouts, monkeypatch)
    entry_point = _installed_entry_point(installed_lint_layouts, name)

    with pytest.raises(runtime.InvalidPackageModuleRootError):
        runtime.discover_packages([name], entry_points=lambda: [entry_point])


def test_activation_rejects_misplaced_manifest(
    project_backend: Path,
    installed_lint_layouts: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _load_runtime(project_backend, installed_lint_layouts, monkeypatch)
    entry_point = _installed_entry_point(installed_lint_layouts, "misplaced")

    with pytest.raises(runtime.PackageManifestError, match="must install package.yaml at"):
        runtime.discover_packages(["misplaced"], entry_points=lambda: [entry_point])


@pytest.mark.parametrize(
    ("listed", "change", "error_name"),
    [
        (["synthetic", "missing"], None, "ListedPackageNotInstalledError"),
        (["synthetic"], {"protocol_version": 2}, "IncompatiblePackageProtocolError"),
        (["synthetic"], {"requires_core": ">=3"}, "IncompatibleCoreVersionError"),
        (
            ["synthetic"],
            {"deployment": {"modes": ["in_process", "container"]}},
            "UnimplementedDeploymentModeError",
        ),
    ],
)
def test_named_activation_failures_use_real_entry_point(
    project_backend: Path,
    installed_synthetic: Path,
    monkeypatch: pytest.MonkeyPatch,
    listed: list[str],
    change: dict[str, object] | None,
    error_name: str,
) -> None:
    runtime = _load_runtime(project_backend, installed_synthetic, monkeypatch)
    installed_manifest = installed_synthetic / "synthetic_package/package.yaml"
    original = installed_manifest.read_text()
    try:
        if change:
            data = yaml.safe_load(original)
            data.update(change)
            installed_manifest.write_text(yaml.safe_dump(data, sort_keys=False))
        with pytest.raises(getattr(runtime, error_name)):
            runtime.discover_packages(listed)
    finally:
        installed_manifest.write_text(original)


def test_runtime_rejects_malformed_deployment_declaration(
    project_backend: Path, installed_synthetic: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _load_runtime(project_backend, installed_synthetic, monkeypatch)
    installed_manifest = installed_synthetic / "synthetic_package/package.yaml"
    original = installed_manifest.read_text()
    try:
        data = yaml.safe_load(original)
        data["deployment"] = {"modes": ["unknown"]}
        installed_manifest.write_text(yaml.safe_dump(data, sort_keys=False))
        with pytest.raises(runtime.PackageManifestError, match="deployment"):
            runtime.discover_packages(["synthetic"])
    finally:
        installed_manifest.write_text(original)


@pytest.mark.parametrize(
    "setting_seeds",
    [
        [{"key": "missing", "scope": "product"}],
        [{"key": "enabled", "scope": "user"}],
        [{"key": "enabled", "scope": "product", "unknown": True}],
        [
            {"key": "enabled", "scope": "product"},
            {"key": "enabled", "scope": "product"},
        ],
    ],
)
def test_runtime_refuses_invalid_setting_seed_declarations(
    project_backend: Path,
    installed_synthetic: Path,
    monkeypatch: pytest.MonkeyPatch,
    setting_seeds: object,
) -> None:
    runtime = _load_runtime(project_backend, installed_synthetic, monkeypatch)
    installed_manifest = installed_synthetic / "synthetic_package/package.yaml"
    original = installed_manifest.read_text()
    try:
        data = yaml.safe_load(original)
        data["setting_seeds"] = setting_seeds
        installed_manifest.write_text(yaml.safe_dump(data, sort_keys=False))
        with pytest.raises(runtime.PackageManifestError, match="setting_seeds"):
            runtime.discover_packages(["synthetic"])
    finally:
        installed_manifest.write_text(original)


def test_runtime_activates_package_with_setting_fields_omitted(
    project_backend: Path, installed_synthetic: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _load_runtime(project_backend, installed_synthetic, monkeypatch)
    installed_manifest = installed_synthetic / "synthetic_package/package.yaml"
    original = installed_manifest.read_text()
    try:
        data = yaml.safe_load(original)
        data.pop("settings_schema")
        data.pop("setting_seeds", None)
        installed_manifest.write_text(yaml.safe_dump(data, sort_keys=False))

        activated = runtime.discover_packages(["synthetic"])

        assert activated[0].manifest.setting_seeds == ()
    finally:
        installed_manifest.write_text(original)


def test_runtime_activates_a_package_that_declares_timers(
    project_backend: Path, installed_synthetic: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Timers are read from the generated contract; activation only admits the field."""

    runtime = _load_runtime(project_backend, installed_synthetic, monkeypatch)
    installed_manifest = installed_synthetic / "synthetic_package/package.yaml"
    original = installed_manifest.read_text()
    try:
        data = yaml.safe_load(original)
        data["timers"] = [{"job": "refresh", "every_seconds": 60}]
        installed_manifest.write_text(yaml.safe_dump(data, sort_keys=False))

        activated = runtime.discover_packages(["synthetic"])

        assert [package.manifest.name for package in activated] == ["synthetic"]
        assert runtime.CORE_VERSION == "2.1.0"
    finally:
        installed_manifest.write_text(original)


def test_runtime_requires_callback_for_declared_setting_seed(
    project_backend: Path, installed_synthetic: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = _load_runtime(project_backend, installed_synthetic, monkeypatch)
    installed_manifest = installed_synthetic / "synthetic_package/package.yaml"
    original = installed_manifest.read_text()
    try:
        data = yaml.safe_load(original)
        data["setting_seeds"] = [{"key": "enabled", "scope": "product"}]
        installed_manifest.write_text(yaml.safe_dump(data, sort_keys=False))
        with pytest.raises(runtime.MissingSettingSeedCallbackError, match="seed_setting"):
            runtime.discover_packages(["synthetic"])
    finally:
        installed_manifest.write_text(original)


@pytest.mark.slow
def test_package_contract_and_migrations_against_real_postgres(
    project_backend: Path,
    tmp_path: Path,
) -> None:
    """The existing integration stack proves the complete installed-package path."""

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is required to build the synthetic package")
    if shutil.which("docker") is None:
        pytest.skip("Docker is required for the Postgres package migration proof")
    project = tmp_path / "package-product"
    shutil.copytree(project_backend, project)
    packages = project / "services/backend/packages"
    tooling_result = subprocess.run(
        [uv, "build", "--wheel", str(KIT_ROOT), "--out-dir", str(packages)],
        capture_output=True,
        text=True,
    )  # noqa: S603
    assert tooling_result.returncode == 0, tooling_result.stdout + tooling_result.stderr
    tooling_wheel = next(packages.glob("codegen_kit_tooling-*.whl"))
    tooling_add = subprocess.run(
        [uv, "add", str(tooling_wheel.relative_to(project))],
        cwd=project,
        capture_output=True,
        text=True,
    )  # noqa: S603
    assert tooling_add.returncode == 0, tooling_add.stdout + tooling_add.stderr
    wheel_result = subprocess.run(
        [uv, "build", "--wheel", str(FIXTURE), "--out-dir", str(packages)],
        capture_output=True,
        text=True,
    )  # noqa: S603
    assert wheel_result.returncode == 0, wheel_result.stdout + wheel_result.stderr
    wheel = next(packages.glob("codegen_kit_synthetic_package-*.whl"))
    add_result = subprocess.run(
        [uv, "add", "--project", "services/backend", str(wheel.relative_to(project))],
        cwd=project,
        capture_output=True,
        text=True,
    )  # noqa: S603
    assert add_result.returncode == 0, add_result.stdout + add_result.stderr
    reminders_wheel_result = subprocess.run(
        [uv, "build", "--wheel", str(REMINDERS), "--out-dir", str(packages)],
        capture_output=True,
        text=True,
    )  # noqa: S603
    assert reminders_wheel_result.returncode == 0, (
        reminders_wheel_result.stdout + reminders_wheel_result.stderr
    )
    reminders_wheel = next(packages.glob("codegen_kit_reminders-*.whl"))
    reminders_add_result = subprocess.run(
        [
            uv,
            "add",
            "--project",
            "services/backend",
            str(reminders_wheel.relative_to(project)),
        ],
        cwd=project,
        capture_output=True,
        text=True,
    )  # noqa: S603
    assert reminders_add_result.returncode == 0, (
        reminders_add_result.stdout + reminders_add_result.stderr
    )

    manifest_path = project / "services/backend/manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest["packages"] = ["synthetic", "reminders"]
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False))
    (project / "services/backend/spec/synthetic_consumer.yaml").write_text(
        "domain: synthetic_consumer\n"
        "operations:\n"
        "  accept_synthetic:\n"
        "    input: SyntheticRequested\n"
        "    events:\n"
        "      subscribe: synthetic.requested\n"
    )
    sync_result = subprocess.run(
        [uv, "sync", "--project", ".", "--frozen"],
        cwd=project,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"},
    )  # noqa: S603
    assert sync_result.returncode == 0, sync_result.stdout + sync_result.stderr
    generate_result = subprocess.run(
        [sys.executable, "-m", "framework.generate"],
        cwd=project,
        capture_output=True,
        text=True,
    )  # noqa: S603
    assert generate_result.returncode == 0, generate_result.stdout + generate_result.stderr
    lint_result = subprocess.run(
        ["make", "lint"],
        cwd=project,
        capture_output=True,
        text=True,
    )  # noqa: S603
    assert lint_result.returncode == 0, lint_result.stdout + lint_result.stderr

    (project / "tests/integration/test_package_protocol.py").write_text(
        textwrap.dedent(
            """
            from __future__ import annotations

            import os
            from pathlib import Path
            import subprocess
            import sys
            import textwrap
            from uuid import UUID

            import asyncpg
            from faststream.redis import RedisBroker, StreamSub
            from faststream.redis.parser import BinaryMessageFormatV1
            from httpx import AsyncClient
            import pytest
            from redis.asyncio import Redis

            from codegen_kit_reminders.runtime import CONSUMER_GROUP, due_event_id
            from services.backend.src.app.factory import create_app
            from services.backend.src.core.db import async_engine
            from shared.generated.events import (
                EventEnvelope,
                get_broker,
                publish_synthetic_requested,
            )
            from shared.generated.schemas import ReminderDue, SyntheticReady, SyntheticRequested
            from synthetic_package import announce_ready, orm_base, session


            @pytest.mark.asyncio
            async def test_package_schema_contract_and_job() -> None:
                database_url = os.environ["ASYNC_DATABASE_URL"].replace(
                    "postgresql+asyncpg://", "postgresql://"
                )
                connection = await asyncpg.connect(database_url)
                try:
                    tables = await connection.fetch(
                        "SELECT table_schema, table_name FROM information_schema.tables "
                        "WHERE table_name IN ('alembic_version', 'synthetic_records')"
                    )
                    locations = {(row["table_schema"], row["table_name"]) for row in tables}
                    assert ("public", "alembic_version") in locations
                    assert ("synthetic", "alembic_version") in locations
                    assert ("synthetic", "synthetic_records") in locations
                    assert ("public", "synthetic_records") not in locations
                    assert await connection.fetchval(
                        "SELECT version_num FROM synthetic.alembic_version"
                    ) == "synthetic_0001"
                    version_xmin = await connection.fetchval(
                        "SELECT xmin::text FROM synthetic.alembic_version"
                    )
                finally:
                    await connection.close()

                second = subprocess.run(
                    ["services/backend/scripts/migrate.sh"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                assert second.returncode == 0, second.stdout + second.stderr
                connection = await asyncpg.connect(database_url)
                try:
                    assert await connection.fetchval(
                        "SELECT xmin::text FROM synthetic.alembic_version"
                    ) == version_xmin
                finally:
                    await connection.close()
                assert "Running upgrade" not in second.stdout + second.stderr

                connection = await asyncpg.connect(database_url)
                try:
                    await connection.execute(
                        "UPDATE synthetic.alembic_version SET version_num = 'missing_revision'"
                    )
                    missing = subprocess.run(
                        ["services/backend/scripts/migrate.sh"],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    assert missing.returncode != 0
                    assert "Can't locate revision identified by 'missing_revision'" in (
                        missing.stdout + missing.stderr
                    )
                finally:
                    await connection.execute(
                        "UPDATE synthetic.alembic_version SET version_num = 'synthetic_0001'"
                    )
                    await connection.close()
                assert orm_base().metadata.schema == "synthetic"
                await async_engine.dispose()
                async with session() as package_db:
                    assert await package_db.scalar(
                        __import__("sqlalchemy").text("SELECT count(*) FROM synthetic_records")
                    ) == 0
                # Do not carry this loop's pooled connection into the next async test.
                await async_engine.dispose()

                async with AsyncClient(base_url="http://backend:8000") as client:
                    headers = {"X-Jobs-Capability": os.environ["JOBS_FIRE_CAPABILITY"]}
                    payload = {
                        "command_id": "package-command",
                        "name": "synthetic.refresh",
                        "arguments": {"force": True},
                        "fired_by_product": "package-test",
                        "fired_by_run": "run-1",
                    }
                    accepted = await client.post("/jobs/fire", headers=headers, json=payload)
                    assert accepted.status_code == 200, accepted.text
                    payload["name"] = "synthetic.undeclared"
                    refused = await client.post("/jobs/fire", headers=headers, json=payload)
                    assert refused.status_code == 404

                redis = Redis.from_url(os.environ["REDIS_URL"])
                broker = get_broker()
                await broker.connect()
                try:
                    envelope = await announce_ready(
                        SyntheticReady(package_id="installed-package")
                    )
                    assert envelope.payload.package_id == "installed-package"
                    requested = await publish_synthetic_requested(
                        SyntheticRequested(request_id="published-consumed-event")
                    )
                    assert requested.payload.request_id == "published-consumed-event"
                    assert await redis.xlen("synthetic.ready") >= 1
                    assert await redis.xlen("synthetic.requested") >= 1
                finally:
                    await broker.close()
                    await redis.aclose()

                adapter = Path("services/backend/src/generated/event_adapter.py").read_text()
                assert '"synthetic.requested"' in adapter
                assert "SyntheticRequested" in adapter


            def _as_user(external_id: str) -> dict[str, str]:
                return {
                    "X-Identity-Capability": os.environ["USER_IDENTITY_CAPABILITY"],
                    "X-User-Channel": "telegram",
                    "X-User-External-Id": external_id,
                }


            async def _grant(client: AsyncClient, external_id: str) -> None:
                granted = await client.post(
                    "/users/grant",
                    headers={"X-Grant-Capability": os.environ["USERS_GRANT_CAPABILITY"]},
                    json={"channel": "telegram", "external_id": external_id},
                )
                assert granted.status_code == 200, granted.text


            async def _due_reader(name: str):
                broker = RedisBroker(os.environ["REDIS_URL"])
                subscriber = broker.subscriber(
                    stream=StreamSub(
                        "reminders.due",
                        group=f"events:reminders-proof:{name}",
                        consumer=name,
                    )
                )
                await broker.start()
                return broker, subscriber


            async def _wait_group_idle(redis: Redis, stream: str, group: str) -> None:
                async with __import__("asyncio").timeout(10):
                    while True:
                        groups = await redis.xinfo_groups(stream)
                        state = next(
                            item
                            for item in groups
                            if item["name"] in (group, group.encode())
                        )
                        lag = state.get("lag")
                        if state["pending"] == 0 and lag == 0:
                            return
                        await __import__("asyncio").sleep(0.01)


            @pytest.mark.asyncio
            async def test_reminder_http_tick_cancel_and_restart_outbox() -> None:
                redis = Redis.from_url(os.environ["REDIS_URL"])
                await redis.flushdb()
                assert not await redis.exists("job_fired")

                # Start and restart the generated backend lifecycle against empty Redis.
                # The installed package, rather than product seed code, owns its group.
                first_application = create_app()
                first_lifespan = first_application.router.lifespan_context(first_application)
                await first_lifespan.__aenter__()
                groups = await redis.xinfo_groups("job_fired")
                assert [group["name"] for group in groups] == [CONSUMER_GROUP.encode()]
                await first_lifespan.__aexit__(None, None, None)

                application = create_app()
                lifespan = application.router.lifespan_context(application)
                await lifespan.__aenter__()
                groups = await redis.xinfo_groups("job_fired")
                assert [group["name"] for group in groups] == [CONSUMER_GROUP.encode()]
                headers = {"X-Jobs-Capability": os.environ["JOBS_FIRE_CAPABILITY"]}
                async with AsyncClient(base_url="http://backend:8000") as client:
                    settings_headers = {
                        "X-Settings-Capability": os.environ["SETTINGS_WRITE_CAPABILITY"]
                    }
                    for external_id in ("seed-owner", "4242", "cancel-owner"):
                        await _grant(client, external_id)
                    seed_payload = {
                        "key": "reminders.reminder_owner_ref",
                        "scope": "product",
                        "value": "telegram:seed-owner",
                    }
                    seeded = await client.post(
                        "/settings/set", headers=settings_headers, json=seed_payload
                    )
                    retried = await client.post(
                        "/settings/set", headers=settings_headers, json=seed_payload
                    )
                    assert seeded.status_code == retried.status_code == 200
                    # Factory recreation reuses activation and does not seed at startup.
                    assert len(create_app().state.codegen_packages) == 2
                    seed_tick = await client.post(
                        "/jobs/fire",
                        headers=headers,
                        json={
                            "command_id": "seed-tick",
                            "name": "reminders.tick",
                            "arguments": {"at": "2001-01-01T00:00:00Z"},
                            "fired_by_product": "reminders-proof",
                            "fired_by_run": "run-1",
                        },
                    )
                    assert seed_tick.status_code == 200, seed_tick.text
                    await _wait_group_idle(redis, "job_fired", CONSUMER_GROUP)
                    seeded_list = await client.get("/reminders", headers=_as_user("seed-owner"))
                    assert len(seeded_list.json()) == 1
                    assert seeded_list.json()[0]["state"] == "emitted"
                    advanced_retry = await client.post(
                        "/settings/set", headers=settings_headers, json=seed_payload
                    )
                    assert advanced_retry.status_code == 200
                    preserved = await client.get("/reminders", headers=_as_user("seed-owner"))
                    assert len(preserved.json()) == 1
                    assert preserved.json()[0]["state"] == "emitted"

                    # Isolate the existing manual HTTP/outbox proof from the seed event.
                    await redis.delete("reminders.due")
                    reader, subscriber = await _due_reader("main")
                    anonymous = await client.post(
                        "/reminders",
                        json={"text": "no owner", "remind_at": "2040-01-01T00:00:00Z"},
                    )
                    assert anonymous.status_code == 401
                    created = await client.post(
                        "/reminders",
                        headers=_as_user("4242"),
                        json={"text": "one-time text", "remind_at": "2040-01-01T00:00:00Z"},
                    )
                    assert created.status_code == 201, created.text
                    reminder = created.json()
                    assert reminder["user_ref"] == "telegram:4242"
                    listed = await client.get("/reminders", headers=_as_user("4242"))
                    assert [item["id"] for item in listed.json()] == [reminder["id"]]

                    cancelled = await client.post(
                        "/reminders",
                        headers=_as_user("cancel-owner"),
                        json={"text": "never publish", "remind_at": "2040-01-01T00:00:00Z"},
                    )
                    cancelled_id = cancelled.json()["id"]
                    others = await client.get("/reminders", headers=_as_user("cancel-owner"))
                    assert [item["id"] for item in others.json()] == [cancelled_id]
                    foreign = await client.delete(
                        f"/reminders/{cancelled_id}", headers=_as_user("4242")
                    )
                    assert foreign.status_code == 404
                    foreign_owned = await client.delete(
                        f"/reminders/{reminder['id']}", headers=_as_user("cancel-owner")
                    )
                    assert foreign_owned.status_code == 404
                    response = await client.delete(
                        f"/reminders/{cancelled_id}", headers=_as_user("cancel-owner")
                    )
                    assert response.status_code == 200
                    assert response.json()["state"] == "cancelled"

                    fire = {
                        "name": "reminders.tick",
                        "arguments": {"at": "2040-01-01T00:00:00Z"},
                        "fired_by_product": "reminders-proof",
                        "fired_by_run": "run-1",
                    }
                    for command_id in ("tick-once", "tick-twice"):
                        response = await client.post(
                            "/jobs/fire",
                            headers=headers,
                            json={**fire, "command_id": command_id},
                        )
                        assert response.status_code == 200, response.text
                    await _wait_group_idle(redis, "job_fired", CONSUMER_GROUP)
                    refused = await client.post(
                        "/jobs/fire",
                        headers=headers,
                        json={
                            **fire,
                            "command_id": "bad-tick",
                            "arguments": {
                                "at": "2040-01-01T00:00:00Z",
                                "undeclared": True,
                            },
                        },
                    )
                    assert refused.status_code == 422

                delivery = await subscriber.get_one(timeout=10)
                assert delivery is not None
                envelope = EventEnvelope[ReminderDue].model_validate(await delivery.decode())
                assert str(envelope.payload.reminder_id) == reminder["id"]
                assert envelope.payload.user_ref == "telegram:4242"
                assert envelope.event_id == due_event_id(UUID(reminder["id"]))
                assert await redis.xlen("reminders.due") == 1
                await reader.stop()

                # The first replacement backend process is killed after its durable
                # transition commits but before publication. A new process handles
                # the next tick against the same package schema and recovers the row.
                async with AsyncClient(base_url="http://backend:8000") as client:
                    await _grant(client, "restart-owner")
                    restart = await client.post(
                        "/reminders",
                        headers=_as_user("restart-owner"),
                        json={"text": "survive restart", "remind_at": "2050-01-01T00:00:00Z"},
                    )
                    assert restart.status_code == 201
                    restart_id = restart.json()["id"]

                crashing = textwrap.dedent(
                    '''
                    import asyncio
                    import os
                    import codegen_kit_reminders.runtime as runtime
                    from services.backend.src.app.factory import create_app

                    async def crash(*args, **kwargs):
                        os._exit(75)

                    async def main():
                        application = create_app()
                        async with application.router.lifespan_context(application):
                            runtime.publish_event = crash
                            await runtime.handle_job_fired({
                                "payload": {
                                    "name": "reminders.tick",
                                    "arguments": {"at": "2050-01-01T00:00:00Z"},
                                }
                            })

                    asyncio.run(main())
                    '''
                )
                crashed = subprocess.run([sys.executable, "-c", crashing], check=False)
                assert crashed.returncode == 75
                connection = await asyncpg.connect(
                    os.environ["ASYNC_DATABASE_URL"].replace(
                        "postgresql+asyncpg://", "postgresql://"
                    )
                )
                try:
                    pending = await connection.fetchrow(
                        "SELECT r.state, e.emitted_at FROM reminders.reminders AS r "
                        "JOIN reminders.due_emissions AS e ON e.reminder_id = r.id "
                        "WHERE r.id = $1",
                        UUID(restart_id),
                    )
                    assert tuple(pending.values()) == ("due", None)
                finally:
                    await connection.close()

                restart_reader, restart_subscriber = await _due_reader("restart")
                recovering = crashing.replace(
                    "runtime.publish_event = crash",
                    "# replacement process uses the real publisher",
                )
                recovered = subprocess.run(
                    [sys.executable, "-c", recovering], capture_output=True, text=True
                )
                assert recovered.returncode == 0, recovered.stdout + recovered.stderr
                recovered_delivery = await restart_subscriber.get_one(timeout=10)
                assert recovered_delivery is not None
                recovered_envelope = EventEnvelope[ReminderDue].model_validate(
                    await recovered_delivery.decode()
                )
                assert str(recovered_envelope.payload.reminder_id) == restart_id
                assert recovered_envelope.event_id == due_event_id(UUID(restart_id))
                assert await redis.xlen("reminders.due") == 2
                await restart_reader.stop()
                await lifespan.__aexit__(None, None, None)
                await redis.aclose()


            @pytest.mark.asyncio
            async def test_concurrent_ticks_emit_one_logical_due_per_reminder() -> None:
                # Two overlapping ticks race over the same due rows while no tick
                # holds a row lock across publication. The stream is read with
                # xrange because a grouped faststream subscriber answers exactly
                # one `get_one`, which cannot count an unknown number of entries.
                redis = Redis.from_url(os.environ["REDIS_URL"])
                async with AsyncClient(base_url="http://backend:8000") as client:
                    reminder_ids = []
                    for index in (1, 2):
                        await _grant(client, f"concurrent-{index}")
                        created = await client.post(
                            "/reminders",
                            headers=_as_user(f"concurrent-{index}"),
                            json={
                                "text": f"concurrent {index}",
                                "remind_at": "2060-01-01T00:00:00Z",
                            },
                        )
                        assert created.status_code == 201, created.text
                        reminder_ids.append(created.json()["id"])

                concurrent = textwrap.dedent(
                    '''
                    import asyncio
                    import codegen_kit_reminders.runtime as runtime
                    from services.backend.src.app.factory import create_app

                    async def main():
                        application = create_app()
                        async with application.router.lifespan_context(application):
                            envelope = {
                                "payload": {
                                    "name": "reminders.tick",
                                    "arguments": {"at": "2060-01-01T00:00:00Z"},
                                }
                            }
                            await asyncio.gather(
                                runtime.handle_job_fired(envelope),
                                runtime.handle_job_fired(envelope),
                            )

                    asyncio.run(main())
                    '''
                )
                ticked = subprocess.run(
                    [sys.executable, "-c", concurrent], capture_output=True, text=True
                )
                assert ticked.returncode == 0, ticked.stdout + ticked.stderr

                connection = await asyncpg.connect(
                    os.environ["ASYNC_DATABASE_URL"].replace(
                        "postgresql+asyncpg://", "postgresql://"
                    )
                )
                try:
                    for reminder_id in reminder_ids:
                        rows = await connection.fetch(
                            "SELECT r.state, e.event_id, e.emitted_at "
                            "FROM reminders.reminders AS r "
                            "JOIN reminders.due_emissions AS e ON e.reminder_id = r.id "
                            "WHERE r.id = $1",
                            UUID(reminder_id),
                        )
                        assert len(rows) == 1
                        assert rows[0]["state"] == "emitted"
                        assert rows[0]["event_id"] == due_event_id(UUID(reminder_id))
                        assert rows[0]["emitted_at"] is not None
                finally:
                    await connection.close()

                published = {}
                for _entry_id, fields in await redis.xrange("reminders.due"):
                    body, _headers = BinaryMessageFormatV1.parse(fields[b"__data__"])
                    envelope = EventEnvelope[ReminderDue].model_validate_json(body)
                    published.setdefault(
                        str(envelope.payload.reminder_id), set()
                    ).add(envelope.event_id)
                await redis.aclose()

                assert {
                    reminder_id: published.get(reminder_id)
                    for reminder_id in reminder_ids
                } == {
                    reminder_id: {due_event_id(UUID(reminder_id))}
                    for reminder_id in reminder_ids
                }
            """
        )
    )

    result = subprocess.run(
        ["make", "test-integration"],
        cwd=project,
        capture_output=True,
        text=True,
    )  # noqa: S603
    assert result.returncode == 0, result.stdout + result.stderr
