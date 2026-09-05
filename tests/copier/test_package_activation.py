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
        requires_core=">=1,<2",
        http_prefix="/shared",
    )
    packages = [
        SimpleNamespace(manifest=package_manifest),
        SimpleNamespace(
            manifest=runtime.PackageManifest(
                protocol_version=1,
                name="second",
                version="1.0.0",
                requires_core=">=1,<2",
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
        runtime._activate_entry_point(name, entry_point)


def test_activation_rejects_misplaced_manifest(
    project_backend: Path,
    installed_lint_layouts: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _load_runtime(project_backend, installed_lint_layouts, monkeypatch)
    entry_point = _installed_entry_point(installed_lint_layouts, "misplaced")

    with pytest.raises(runtime.PackageManifestError, match="must install package.yaml at"):
        runtime._activate_entry_point("misplaced", entry_point)


@pytest.mark.parametrize(
    ("listed", "change", "error_name"),
    [
        (["synthetic", "missing"], None, "ListedPackageNotInstalledError"),
        (["synthetic"], {"protocol_version": 2}, "IncompatiblePackageProtocolError"),
        (["synthetic"], {"requires_core": ">=2"}, "IncompatibleCoreVersionError"),
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

    manifest_path = project / "services/backend/manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text())
    manifest["packages"] = ["synthetic"]
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

            import asyncpg
            from httpx import AsyncClient
            import pytest
            from redis.asyncio import Redis

            from services.backend.src.core.db import async_engine
            from shared.generated.events import get_broker, publish_synthetic_requested
            from shared.generated.schemas import SyntheticReady, SyntheticRequested
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
                finally:
                    await connection.close()

                second = subprocess.run(
                    ["services/backend/scripts/migrate.sh"],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                assert second.returncode == 0, second.stdout + second.stderr
                assert orm_base().metadata.schema == "synthetic"
                await async_engine.dispose()
                async with session() as package_db:
                    assert await package_db.scalar(
                        __import__("sqlalchemy").text("SELECT count(*) FROM synthetic_records")
                    ) == 0

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
