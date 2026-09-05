"""Two-product proof for the real reminders wheel and package tooling command."""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import shutil
import subprocess
import textwrap

import pytest

from tests.copier.conftest import run_copier

KIT_ROOT = Path(__file__).parents[2]
REMINDERS = KIT_ROOT / "packages/codegen-kit-reminders"


def _run(command: list[str], root: Path) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # noqa: S603
        command,
        cwd=root,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def _commit_baseline(product: Path) -> None:
    _run(["git", "init", "--quiet"], product)
    _run(["git", "config", "user.name", "Package proof"], product)
    _run(["git", "config", "user.email", "package-proof@example.com"], product)
    _run(["git", "add", "-A"], product)
    _run(["git", "commit", "--quiet", "-m", "Generated baseline"], product)


def _write_acceptance_test(product: Path, *, subscriber: bool) -> None:
    body = """
        from __future__ import annotations

        import asyncio
        from contextlib import asynccontextmanager
        from importlib import metadata
        import os

        from faststream.redis import RedisBroker
        from httpx import AsyncClient
        import pytest

        from codegen_kit_reminders import RemindersPackage


        @pytest.mark.asyncio
        async def test_real_package_route_entry_point_and_lifecycle() -> None:
            entry_point = next(
                item for item in metadata.entry_points(group="codegen_kit.packages")
                if item.name == "reminders"
            )
            assert entry_point.load().__class__.__name__ == "RemindersPackage"
            package = RemindersPackage()
            await package.startup(object())
            assert package.consumer.broker is not None
            await package.shutdown(object())
            assert package.consumer.broker is None
            async with AsyncClient(base_url="http://backend:8000") as client:
                response = await client.post(
                    "/reminders",
                    json={
                        "user_ref": "acceptance-user",
                        "text": "real package route",
                        "remind_at": "2040-01-01T00:00:00Z",
                    },
                )
                assert response.status_code == 201, response.text


        @pytest.mark.asyncio
        async def test_connection_check_raises_without_redis_url(monkeypatch) -> None:
            monkeypatch.delenv("REDIS_URL")
            with pytest.raises(RuntimeError, match="REDIS_URL is not set"):
                await RemindersPackage().startup(object())
    """
    if subscriber:
        body += """

        @pytest.mark.asyncio
        async def test_product_subscriber_receives_package_event() -> None:
            from services.backend.src.generated.event_adapter import create_event_adapter
            from shared.generated.schemas import ReminderDue

            received = asyncio.Event()

            class Controller:
                async def receive_due(self, session, *, payload: ReminderDue) -> None:
                    assert payload.user_ref == "subscriber-user"
                    received.set()

            class Session:
                async def commit(self) -> None:
                    pass

                async def rollback(self) -> None:
                    pass

            @asynccontextmanager
            async def get_session():
                yield Session()

            async def consume_once(session, consumer_group, event_id, effect):
                await effect()
                return True

            broker = RedisBroker(os.environ["REDIS_URL"])
            create_event_adapter(
                broker,
                get_session=get_session,
                consume_once=consume_once,
                get_reminders_consumer_controller=Controller,
            )
            await broker.start()
            try:
                async with AsyncClient(base_url="http://backend:8000") as client:
                    created = await client.post(
                        "/reminders",
                        json={
                            "user_ref": "subscriber-user",
                            "text": "through the protocol",
                            "remind_at": "2040-01-01T00:00:00Z",
                        },
                    )
                    assert created.status_code == 201, created.text
                    fired = await client.post(
                        "/jobs/fire",
                        headers={"X-Jobs-Capability": os.environ["JOBS_FIRE_CAPABILITY"]},
                        json={
                            "command_id": "two-product-tick",
                            "name": "reminders.tick",
                            "arguments": {"at": "2040-01-01T00:00:00Z"},
                            "fired_by_product": "product-b",
                            "fired_by_run": "ci",
                        },
                    )
                    assert fired.status_code == 200, fired.text
                await asyncio.wait_for(received.wait(), timeout=10)
            finally:
                await broker.stop()
        """
    target = product / "tests/integration/test_00_real_reminders.py"
    target.write_text(textwrap.dedent(body))


def _install(product: Path, wheel: Path) -> None:
    _run(["uv", "sync", "--project", ".", "--frozen"], product)
    _run(
        [
            str(product / ".venv/bin/kit"),
            "add",
            "reminders",
            "--wheel",
            str(wheel),
        ],
        product,
    )


def _make_tooling_docker_buildable(product: Path, output: Path) -> None:
    """Replace a host-only file pin; CI's exact Git pin is already buildable."""

    if " @ file://" not in (product / "pyproject.toml").read_text():
        return
    _run(["uv", "build", "--wheel", str(KIT_ROOT), "--out-dir", str(output)], KIT_ROOT)
    wheel = next(output.glob("codegen_kit_tooling-*.whl"))
    target = product / "services/backend/packages" / wheel.name
    shutil.copy2(wheel, target)
    _run(["uv", "add", str(target.relative_to(product))], product)


@pytest.fixture(scope="module")
def reminders_wheel(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("reminders-wheel")
    _run(["uv", "build", "--wheel", str(REMINDERS), "--out-dir", str(output)], KIT_ROOT)
    return next(output.glob("codegen_kit_reminders-*.whl"))


@pytest.fixture(scope="module")
def two_products(
    reminders_wheel: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path, Path, Path]:
    root = tmp_path_factory.mktemp("two-package-products")
    (root / "product-a").mkdir()
    (root / "product-b").mkdir()
    product_a = run_copier(root / "product-a", "backend")
    product_b = run_copier(root / "product-b", "backend")
    _make_tooling_docker_buildable(product_a, root / "tooling-a")
    _make_tooling_docker_buildable(product_b, root / "tooling-b")

    _run(["make", "setup"], product_a)
    _commit_baseline(product_a)
    package_free = root / "package-free"
    shutil.copytree(product_a, package_free)

    _run(["make", "setup"], product_b)
    consumer = product_b / "services/backend/spec/reminders_consumer.yaml"
    consumer.write_text(
        "domain: reminders_consumer\n"
        "operations:\n"
        "  receive_due:\n"
        "    input: ReminderDue\n"
        "    events:\n"
        "      subscribe: reminders.due\n"
    )
    _commit_baseline(product_b)

    _install(product_a, reminders_wheel)
    _install(product_b, reminders_wheel)
    return product_a, product_b, package_free, reminders_wheel


@pytest.mark.slow
def test_two_products_install_one_unchanged_wheel_without_authored_source(
    two_products: tuple[Path, Path, Path, Path],
) -> None:
    product_a, product_b, _, wheel = two_products
    artifact_hash = sha256(wheel.read_bytes()).hexdigest()
    for product in (product_a, product_b):
        installed = product / "services/backend/packages" / wheel.name
        assert sha256(installed.read_bytes()).hexdigest() == artifact_hash
        _run(["make", "lint"], product)

    changed = set(_run(["git", "status", "--porcelain"], product_a).stdout.splitlines())
    paths = {line[3:] for line in changed}
    allowed_exact = {
        "services/backend/manifest.yaml",
        "services/backend/pyproject.toml",
        "services/backend/uv.lock",
        f"services/backend/packages/{wheel.name}",
        "codegen_kit/_active_packages.py",
        "services/backend/packages/env.contract.yaml",
    }
    assert all(
        path in allowed_exact
        or path.startswith("shared/shared/generated/")
        or path.startswith("services/backend/src/generated/")
        for path in paths
    ), paths
    assert not any(
        path.startswith("services/backend/src/app/") or "/controllers/" in path for path in paths
    )


@pytest.mark.slow
def test_both_real_products_pass_compose_acceptance(
    two_products: tuple[Path, Path, Path, Path],
) -> None:
    product_a, product_b, _, _ = two_products
    for product, subscriber in ((product_a, False), (product_b, True)):
        _write_acceptance_test(product, subscriber=subscriber)
        _run(["make", "test-integration"], product)


def _idle_backend_rss_kib(product: Path, project_name: str) -> int:
    compose = ["docker", "compose", "-f", "infra/compose.tests.integration.yml"]
    dotenv = dict(
        line.split("=", 1)
        for line in (product / ".env").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    environment = {
        **{key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"},
        **dotenv,
        "COMPOSE_PROJECT_NAME": project_name,
        "HOST_UID": str(os.getuid()),
        "HOST_GID": str(os.getgid()),
    }
    try:
        started = subprocess.run(  # noqa: S603
            [*compose, "up", "-d", "--build", "--wait", "backend"],
            cwd=product,
            capture_output=True,
            text=True,
            env=environment,
        )
        assert started.returncode == 0, started.stdout + started.stderr
        measured = subprocess.run(  # noqa: S603
            [
                *compose,
                "exec",
                "-T",
                "backend",
                "awk",
                "/^VmRSS:/ {print $2}",
                "/proc/1/status",
            ],
            cwd=product,
            check=True,
            capture_output=True,
            text=True,
            env=environment,
        )
        return int(measured.stdout.strip())
    finally:
        subprocess.run(  # noqa: S603
            [*compose, "down", "--volumes", "--remove-orphans"],
            cwd=product,
            check=False,
            capture_output=True,
            text=True,
            env=environment,
        )


@pytest.mark.slow
def test_product_a_incremental_idle_rss_is_measured(
    two_products: tuple[Path, Path, Path, Path],
    pytestconfig: pytest.Config,
) -> None:
    product_a, _, package_free, _ = two_products
    baseline_kib = _idle_backend_rss_kib(package_free, "rss-package-free")
    product_a_kib = _idle_backend_rss_kib(product_a, "rss-product-a")
    delta_kib = product_a_kib - baseline_kib
    message = (
        f"idle RSS: package-free={baseline_kib} KiB, product-a={product_a_kib} KiB, "
        f"incremental={delta_kib} KiB"
    )
    reporter = pytestconfig.pluginmanager.getplugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(message)
    assert baseline_kib > 0
    assert product_a_kib > 0
