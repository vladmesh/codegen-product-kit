"""Two-product proof for the real reminders wheel and package tooling command."""

from __future__ import annotations

from hashlib import sha256
import os
from pathlib import Path
import shutil
import subprocess
import textwrap
import zipfile

import pytest
import yaml

from tests.copier.conftest import run_copier

KIT_ROOT = Path(__file__).parents[2]
REMINDERS = KIT_ROOT / "packages/codegen-kit-reminders"
# A reserved TLD never resolves: a package runtime that reaches for Redis fails fast.
UNREACHABLE_REDIS_URL = "redis://redis.invalid:6379"


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


        async def as_user(client: AsyncClient, external_id: str) -> dict[str, str]:
            # Grant one Telegram identity and return the headers the bot sends for it.
            granted = await client.post(
                "/users/grant",
                headers={"X-Grant-Capability": os.environ["USERS_GRANT_CAPABILITY"]},
                json={"channel": "telegram", "external_id": external_id},
            )
            assert granted.status_code == 200, granted.text
            return {
                "X-Identity-Capability": os.environ["USER_IDENTITY_CAPABILITY"],
                "X-User-Channel": "telegram",
                "X-User-External-Id": external_id,
            }


        @pytest.mark.asyncio
        async def test_real_package_route_entry_point_and_lifecycle(monkeypatch) -> None:
            entry_point = next(
                item for item in metadata.entry_points(group="codegen_kit.packages")
                if item.name == "reminders"
            )
            assert entry_point.load().__class__.__name__ == "RemindersPackage"
            package = RemindersPackage()
            lifecycle_calls = []

            async def start() -> None:
                lifecycle_calls.append("startup")

            async def stop() -> None:
                lifecycle_calls.append("shutdown")

            monkeypatch.setattr(package.consumer, "start", start)
            monkeypatch.setattr(package.consumer, "stop", stop)
            await package.startup(object())
            await package.shutdown(object())
            assert lifecycle_calls == ["startup", "shutdown"]
            async with AsyncClient(base_url="http://backend:8000") as client:
                body = {"text": "real package route", "remind_at": "2040-01-01T00:00:00Z"}
                refused = await client.post("/reminders", json=body)
                assert refused.status_code == 401, refused.text
                response = await client.post(
                    "/reminders", headers=await as_user(client, "1001"), json=body
                )
                assert response.status_code == 201, response.text
                assert response.json()["user_ref"] == "telegram:1001"


        @pytest.mark.asyncio
        async def test_connection_check_raises_without_redis_url(monkeypatch) -> None:
            monkeypatch.delenv("REDIS_URL")
            with pytest.raises(RuntimeError, match="REDIS_URL is not set"):
                await RemindersPackage().startup(object())
    """
    if subscriber:
        body += """

        @pytest.mark.asyncio
        async def test_core_timer_fires_due_reminder_without_jobs_fire() -> None:
            # Runs before any explicit tick in this file: nothing here calls /jobs/fire.
            from datetime import UTC, datetime, timedelta

            from services.backend.src.generated.event_adapter import create_event_adapter
            from services.backend.src.generated.jobs_schemas import JOB_TIMERS
            from shared.generated.schemas import ReminderDue

            assert JOB_TIMERS == {"reminders.tick": 60}
            received: list[ReminderDue] = []
            delivered = asyncio.Event()

            class Controller:
                async def receive_due(self, session, *, payload: ReminderDue) -> None:
                    if payload.user_ref == "telegram:2002":
                        received.append(payload)
                        delivered.set()

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
                    timer_user = await as_user(client, "2002")
                    remind_at = datetime.now(UTC) + timedelta(seconds=1)
                    created = await client.post(
                        "/reminders",
                        headers=timer_user,
                        json={
                            "text": "fired by the core timer",
                            "remind_at": remind_at.isoformat(),
                        },
                    )
                    assert created.status_code == 201, created.text
                    reminder = created.json()
                    await asyncio.wait_for(delivered.wait(), timeout=150)
                    for _ in range(50):
                        listed = await client.get("/reminders", headers=timer_user)
                        if listed.json()[0]["state"] == "emitted":
                            break
                        await asyncio.sleep(0.2)
                    view = listed.json()[0]
                    assert view["state"] == "emitted"
                    slot = datetime.fromisoformat(view["due_at"]).astimezone(UTC)
                    assert slot.timestamp() % 60 == 0
                    assert slot >= remind_at.replace(microsecond=0)
                    evidence = await client.post(
                        "/jobs/evidence",
                        json={
                            "command_id": "core-timer:reminders.tick:"
                            + slot.strftime("%Y-%m-%dT%H:%M:%SZ"),
                            "fired_by_product": os.environ["APP_NAME"],
                        },
                    )
                    assert evidence.status_code == 200, evidence.text
                    assert evidence.json()["fired_by_run"] == "core-timer"
                    assert evidence.json()["dispatch_status"] == "dispatched"
                    assert evidence.json()["arguments"] == {
                        "at": slot.strftime("%Y-%m-%dT%H:%M:%SZ")
                    }
            finally:
                await broker.stop()
            # One logical notification: a redelivery would carry the same event identity.
            assert {str(item.reminder_id) for item in received} == {reminder["id"]}


        @pytest.mark.asyncio
        async def test_product_subscriber_receives_package_event() -> None:
            from services.backend.src.generated.event_adapter import create_event_adapter
            from shared.generated.schemas import ReminderDue

            received = asyncio.Event()

            class Controller:
                async def receive_due(self, session, *, payload: ReminderDue) -> None:
                    assert payload.user_ref == "telegram:3003"
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
                        headers=await as_user(client, "3003"),
                        json={"text": "through the protocol", "remind_at": "2040-01-01T00:00:00Z"},
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
    wheel = next(output.glob("codegen_kit_reminders-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        resource = "codegen_kit_reminders/bindings/default.yaml"
        assert archive.read(resource) == (REMINDERS / resource).read_bytes()
        assert yaml.safe_load(archive.read(resource))["binding_version"] == 1
    return wheel


@pytest.mark.slow
def test_main_push_commands_restore_real_package_entry_point_before_generation(
    reminders_wheel: Path,
    tmp_path: Path,
) -> None:
    """Reproduce the package-carrying product boundary from the failed main-push job."""
    product = run_copier(tmp_path, "backend")
    _run(["uv", "sync", "--frozen"], product)
    _run(
        [
            str(product / ".venv/bin/kit"),
            "add",
            "reminders",
            "--wheel",
            str(reminders_wheel),
        ],
        product,
    )
    _commit_baseline(product)

    workflow = yaml.safe_load((product / ".github/workflows/ci.yml").read_text())
    main_push_commands = next(
        step["run"].splitlines()
        for step in workflow["jobs"]["build-and-push"]["steps"]
        if step.get("name") == "Generate code from specs"
    )
    assert main_push_commands == [
        "uv sync --frozen",
        "uv sync --project services/backend --frozen",
        "make generate-from-spec",
    ]

    shutil.rmtree(product / "services/backend/.venv")
    _run(main_push_commands[0].split(), product)
    missing_entry_point = subprocess.run(  # noqa: S603
        main_push_commands[2].split(),
        cwd=product,
        capture_output=True,
        text=True,
        env={key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"},
    )
    assert missing_entry_point.returncode != 0
    assert "reminders: listed package has no installed entry point" in (
        missing_entry_point.stdout + missing_entry_point.stderr
    )

    _run(main_push_commands[1].split(), product)
    _run(main_push_commands[2].split(), product)
    resolved = _run(
        [
            str(product / "services/backend/.venv/bin/python"),
            "-c",
            "from importlib import metadata; "
            "print(next(ep.value for ep in metadata.entry_points(group='codegen_kit.packages') "
            "if ep.name == 'reminders'))",
        ],
        product,
    )
    assert resolved.stdout.strip() == "codegen_kit_reminders:package"
    assert '"name": "reminders"' in (product / "codegen_kit/_active_packages.py").read_text()
    _run(
        [
            "git",
            "diff",
            "--exit-code",
            "--",
            "codegen_kit/_active_packages.py",
            "services/backend/packages/env.contract.yaml",
            "services/backend/src/generated/",
            "shared/shared/generated/",
        ],
        product,
    )


@pytest.fixture(scope="module")
def two_products(
    reminders_wheel: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path, Path, Path]:
    root = tmp_path_factory.mktemp("two-package-products")
    (root / "product-a").mkdir()
    (root / "product-b").mkdir()
    product_a = run_copier(root / "product-a", "backend")
    product_b = run_copier(root / "product-b", "backend,tg_bot")
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
        contract_artifact = product.parent / "package-proof-env-contract.json"
        _run(
            [
                str(product / ".venv/bin/python"),
                "-m",
                "framework.contracts.env_usage",
                "--root",
                ".",
                "--artifact",
                str(contract_artifact),
                "--commit-sha",
                "package-proof",
            ],
            product,
        )
        assert contract_artifact.is_file()

        package_fragment = yaml.safe_load(
            (product / "services/backend/packages/env.contract.yaml").read_text()
        )
        infra_fragment = yaml.safe_load((product / "infra/env.contract.yaml").read_text())
        assert package_fragment["entries"]["REDIS_URL"] == infra_fragment["entries"]["REDIS_URL"]

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
    assert allowed_exact <= paths
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
def test_product_with_installed_reminders_passes_its_unit_leg_without_redis(
    two_products: tuple[Path, Path, Path, Path],
) -> None:
    """Run product B's CI "Run tests" step with reminders installed and no Redis.

    Kit 0.7.0's lifespan unit tests started the real reminders consumer, which failed
    with a Redis ConnectionError on the product CI's unit leg.
    """
    _, product_b, _, _ = two_products
    workflow = yaml.safe_load((product_b / ".github/workflows/ci.yml").read_text())
    steps = {step.get("name"): step for step in workflow["jobs"]["lint-and-test"]["steps"]}
    assert steps["Prepare environment files"]["run"].splitlines()[0] == "cp .env.example .env"
    assert steps["Run tests"]["run"] == "make tests"

    dotenv = product_b / ".env"
    original_dotenv = dotenv.read_bytes() if dotenv.exists() else None
    shutil.copy2(product_b / ".env.example", dotenv)
    try:
        # A command-line variable overrides the exported .env value, so the unit leg
        # sees a Redis host that can never resolve.
        _run(["make", "tests", f"REDIS_URL={UNREACHABLE_REDIS_URL}"], product_b)
    finally:
        if original_dotenv is None:
            dotenv.unlink(missing_ok=True)
        else:
            dotenv.write_bytes(original_dotenv)


@pytest.mark.slow
def test_both_real_products_pass_compose_acceptance(
    two_products: tuple[Path, Path, Path, Path],
) -> None:
    product_a, product_b, _, _ = two_products
    for product, subscriber in ((product_a, False), (product_b, True)):
        _write_acceptance_test(product, subscriber=subscriber)
        _run(["make", "test-integration"], product)


def _compose_environment(product: Path, project_name: str) -> dict[str, str]:
    """Return the generated Makefile's compose interpolation environment."""

    dotenv = dict(
        line.split("=", 1)
        for line in (product / ".env").read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    return {
        **{key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"},
        **dotenv,
        "COMPOSE_PROJECT_NAME": project_name,
        "HOST_UID": str(os.getuid()),
        "HOST_GID": str(os.getgid()),
    }


def _start_idle_backend(product: Path, project_name: str) -> None:
    compose = ["docker", "compose", "-f", "infra/compose.tests.integration.yml"]
    environment = _compose_environment(product, project_name)
    started = subprocess.run(  # noqa: S603
        [*compose, "up", "-d", "--build", "--wait", "backend"],
        cwd=product,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert started.returncode == 0, started.stdout + started.stderr


def _idle_backend_rss_kib(product: Path, project_name: str) -> int:
    compose = ["docker", "compose", "-f", "infra/compose.tests.integration.yml"]
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
        env=_compose_environment(product, project_name),
    )
    return int(measured.stdout.strip())


def _stop_idle_backend(product: Path, project_name: str) -> None:
    subprocess.run(  # noqa: S603
        [
            "docker",
            "compose",
            "-f",
            "infra/compose.tests.integration.yml",
            "down",
            "--volumes",
            "--remove-orphans",
        ],
        cwd=product,
        check=False,
        capture_output=True,
        text=True,
        env=_compose_environment(product, project_name),
    )


@pytest.mark.slow
def test_product_a_incremental_idle_rss_is_measured(
    two_products: tuple[Path, Path, Path, Path],
    pytestconfig: pytest.Config,
) -> None:
    product_a, _, package_free, _ = two_products
    package_free_project = "rss-package-free"
    product_a_project = "rss-product-a"
    try:
        _start_idle_backend(package_free, package_free_project)
        _start_idle_backend(product_a, product_a_project)
        baseline_readings = [
            _idle_backend_rss_kib(package_free, package_free_project) for _ in range(5)
        ]
        product_a_readings = [_idle_backend_rss_kib(product_a, product_a_project) for _ in range(5)]
    finally:
        _stop_idle_backend(product_a, product_a_project)
        _stop_idle_backend(package_free, package_free_project)

    baseline_range = (min(baseline_readings), max(baseline_readings))
    product_a_range = (min(product_a_readings), max(product_a_readings))
    incremental_range = (
        product_a_range[0] - baseline_range[1],
        product_a_range[1] - baseline_range[0],
    )
    message = (
        f"idle RSS readings (KiB): package-free={baseline_readings}, "
        f"product-a={product_a_readings}; ranges: package-free={baseline_range}, "
        f"product-a={product_a_range}, incremental={incremental_range}"
    )
    reporter = pytestconfig.pluginmanager.getplugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(message)
    assert all(reading > 0 for reading in baseline_readings)
    assert all(reading > 0 for reading in product_a_readings)
