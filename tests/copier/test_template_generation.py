"""Tests for Copier template generation.

Run with: make test-copier
"""

import ast
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib

import pytest

from tests.copier.conftest import (
    BASE_DATA,
    VENV_COPIER,
    VENV_RUFF,
    check_no_jinja_artifacts,
    run_copier,
    run_copier_command,
)


def env_keys(path: Path) -> set[str]:
    """Return active and documented env keys from a generated dotenv file."""
    keys = set()
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            stripped = stripped.removeprefix("#").strip()
        if not stripped or "=" not in stripped:
            continue
        key = stripped.split("=", 1)[0].strip()
        if re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
            keys.add(key)
    return keys


def test_root_infra_readme_points_to_template_contract() -> None:
    """Worker-mode contract should be discoverable before running copier."""
    root_readme = Path("infra/README.md").read_text()

    assert "template/infra/README.md" in root_readme
    assert "worker-mode\ncontract" in root_readme


def test_template_ci_validates_compose_and_typechecks_candidate() -> None:
    """Template CI should validate compose and typecheck the exact candidate."""
    workflow = Path(".github/workflows/test-template.yml").read_text()

    assert "fetch-depth: 0" in workflow
    assert "uses: astral-sh/setup-uv@v7" in workflow
    assert 'version: "0.11.29"' in workflow
    assert '--defaults --trust --vcs-ref="${{ github.sha }}"' in workflow
    assert "if: matrix.modules == 'backend'" in workflow
    assert "make setup && make typecheck" in workflow
    makefile = Path("template/Makefile.jinja").read_text()
    assert '"$$svc/.venv/bin/mypy" "$$svc"' in makefile
    assert 'targets="$$svc/src"' not in makefile
    assert "generated/" not in Path("template/mypy.ini.jinja").read_text()
    assert ("docker compose --env-file .env -f infra/compose.base.yml config") in workflow
    assert (
        "docker compose --env-file .env -f infra/compose.base.yml -f infra/compose.dev.yml config"
    ) in workflow


class TestBackendOnlyGeneration:
    """Test generation with only backend module."""

    def test_core_files_exist(self, project_backend: Path):
        """Core files should be generated."""
        assert (project_backend / "Makefile").exists()
        assert (project_backend / "README.md").exists()
        assert (project_backend / "ARCHITECTURE.md").exists()
        assert (project_backend / "CONTRIBUTING.md").exists()

    def test_services_yml_generated(self, project_backend: Path):
        """services.yml should contain only backend."""
        content = (project_backend / "services.yml").read_text()
        assert "backend" in content
        assert "tg_bot" not in content
        assert "notifications_worker" not in content
        assert "frontend" not in content

    def test_backend_service_exists(self, project_backend: Path):
        """Backend service directory should exist."""
        assert (project_backend / "services" / "backend").exists()
        assert (project_backend / "services" / "backend" / "Dockerfile").exists()

    def test_other_services_excluded(self, project_backend: Path):
        """Other service directories should not exist."""
        assert not (project_backend / "services" / "tg_bot").exists()
        assert not (project_backend / "services" / "notifications_worker").exists()
        assert not (project_backend / "services" / "frontend").exists()

    def test_compose_files_valid(self, project_backend: Path):
        """Docker Compose files should be valid YAML."""
        import yaml

        compose_base = project_backend / "infra" / "compose.base.yml"
        assert compose_base.exists()

        content = yaml.safe_load(compose_base.read_text())
        assert "services" in content
        assert "backend" in content["services"]
        assert "db" in content["services"]
        assert "redis" in content["services"]

    def test_no_jinja_artifacts(self, project_backend: Path):
        """No Jinja template artifacts should remain."""
        errors = check_no_jinja_artifacts(project_backend)
        assert not errors, f"Found Jinja artifacts: {errors}"

    def test_readme_content(self, project_backend: Path):
        """README should contain project info."""
        content = (project_backend / "README.md").read_text()
        assert BASE_DATA["project_name"] in content
        assert "backend" in content.lower()
        assert "Use `make ps` to see the current project's Compose stack status." in content
        assert "`REDIS_HOST_PORT` maps Redis to container port `6379`" in content
        assert "use `users` as the reference entity" in content
        assert "make worker-call url=http://backend:8000/users method=POST" in content

    def test_infra_contract_documented(self, project_backend: Path):
        """Generated project should document the compose infra contract."""
        infra_readme = (project_backend / "infra" / "README.md").read_text()
        agents = (project_backend / "AGENTS.md").read_text()

        assert "Compose service names are part of the project API" in infra_readme
        assert "Do not declare custom networks" in infra_readme
        assert "-f infra/compose.base.yml -f infra/compose.dev.yml" in infra_readme
        assert "Environment priority depends on where the value is consumed" in infra_readme
        assert "shell-prefix value before `make` does not override" in infra_readme
        assert "do not append `sslmode=require`" in infra_readme
        assert "shell values for the full URLs do not replace those service" in infra_readme
        assert "consumers load `REDIS_URL` from generated `.env`" in infra_readme
        assert "## Parallel run isolation" in infra_readme
        assert "COMPOSE_PROJECT_NAME=my-project-dev make dev-start" in infra_readme
        assert "make worker-call SMOKE_RUNNER=backend url=http://backend:8000/users" in infra_readme
        assert "docker compose --env-file .env" in infra_readme
        assert "`--project-name` option explicitly" in infra_readme
        assert "`infra/README.md`" in agents

    def test_infra_contract_matches_compose_env_precedence(self, project_fullstack: Path):
        """Documented env handles should match generated compose semantics."""
        import yaml

        infra_readme = (project_fullstack / "infra" / "README.md").read_text()
        compose = yaml.safe_load((project_fullstack / "infra" / "compose.base.yml").read_text())
        makefile = (project_fullstack / "Makefile").read_text()

        assert "-include .env\nexport" in makefile
        assert "POSTGRES_HOST=custom-db make" not in infra_readme
        assert "make POSTGRES_HOST=custom-db POSTGRES_PORT=6543 migrate" in infra_readme

        backend_env = compose["services"]["backend"]["environment"]
        assert "${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}" in backend_env["DATABASE_URL"]
        assert "${DATABASE_URL:-" not in backend_env["DATABASE_URL"]
        assert "sslmode=require" not in backend_env["DATABASE_URL"]
        assert "${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}" in backend_env["ASYNC_DATABASE_URL"]
        assert "${ASYNC_DATABASE_URL:-" not in backend_env["ASYNC_DATABASE_URL"]
        assert "sslmode=require" not in backend_env["ASYNC_DATABASE_URL"]

        tg_bot = compose["services"]["tg_bot"]
        notifications_worker = compose["services"]["notifications_worker"]
        assert tg_bot["env_file"] == ["../.env"]
        assert notifications_worker["env_file"] == ["../.env"]
        assert "REDIS_URL" not in tg_bot.get("environment", {})
        assert "REDIS_URL" not in notifications_worker.get("environment", {})

        assert "full URLs do not replace those service" in infra_readme
        assert "`REDIS_URL=... docker" in infra_readme
        assert "does not override the container value" in infra_readme

    def test_product_test_scaffolding(self, project_backend: Path):
        """Product should have test scaffolding."""
        tests_dir = project_backend / "tests"
        assert tests_dir.exists()
        assert (tests_dir / "integration").exists()

        # Framework copier tests should NOT be copied
        assert not (tests_dir / "copier").exists()

    def test_ci_pins_uv(self, project_backend: Path):
        """Generated CI must pin setup-uv and uv instead of resolving 'latest'."""
        import yaml

        from framework.toolchain import SETUP_UV_ACTION, UV_VERSION

        ci_yml = yaml.safe_load(
            (project_backend / ".github" / "workflows" / "ci.yml").read_text()
        )
        steps = [
            step
            for job in ci_yml["jobs"].values()
            for step in job["steps"]
            if str(step.get("uses", "")).startswith("astral-sh/setup-uv")
        ]

        assert len(steps) == 2, "expected setup-uv in both lint-and-test and build-and-push"
        for step in steps:
            assert step["uses"] == SETUP_UV_ACTION
            assert step["with"]["version"] == UV_VERSION

    def test_copier_answers_file_created(self, project_backend: Path):
        """Generated project should have .copier-answers.yml."""
        import yaml

        answers_file = project_backend / ".copier-answers.yml"
        assert answers_file.exists()

        answers = yaml.safe_load(answers_file.read_text())
        assert answers["project_name"] == "test-project"
        assert answers["modules"] == "backend"


class TestStandaloneGeneration:
    """Test generation with standalone tg_bot module (no backend)."""

    def test_tg_bot_service_exists(self, project_standalone: Path):
        """tg_bot service directory and Dockerfile should exist."""
        assert (project_standalone / "services" / "tg_bot").exists()
        assert (project_standalone / "services" / "tg_bot" / "Dockerfile").exists()

    def test_no_jinja_artifacts(self, project_standalone: Path):
        """No Jinja artifacts in standalone generation."""
        errors = check_no_jinja_artifacts(project_standalone)
        assert not errors, f"Found Jinja artifacts: {errors}"

    def test_env_example_no_postgres(self, project_standalone: Path):
        """Standalone .env.example should not have POSTGRES variables."""
        env_content = (project_standalone / ".env.example").read_text()
        assert "POSTGRES" not in env_content

    def test_env_example_has_redis_and_telegram(self, project_standalone: Path):
        """Standalone .env.example should have REDIS and TELEGRAM variables."""
        env_content = (project_standalone / ".env.example").read_text()
        assert "REDIS_URL" in env_content
        assert "TELEGRAM_BOT_TOKEN" in env_content

    def test_compose_has_tg_bot_and_redis(self, project_standalone: Path):
        """Compose should have tg_bot + redis."""
        import yaml

        compose = yaml.safe_load((project_standalone / "infra" / "compose.base.yml").read_text())
        services = compose.get("services", {})
        assert "tg_bot" in services
        assert "redis" in services
        assert services["tg_bot"]["depends_on"]["redis"]["condition"] == "service_healthy"

    def test_services_yml_has_tg_bot(self, project_standalone: Path):
        """services.yml should contain tg_bot with a polling Python runtime type."""
        import yaml

        content = yaml.safe_load((project_standalone / "services.yml").read_text())
        services = {service["name"]: service for service in content["services"]}
        assert services["tg_bot"]["type"] == "python"
        assert "notifications_worker" not in services
        assert "frontend" not in services

    def test_workflow_matrix_has_tg_bot(self, project_standalone: Path):
        """Workflow matrix should include tg-bot."""
        ci_yml = (project_standalone / ".github" / "workflows" / "ci.yml").read_text()
        assert "id: tg-bot" in ci_yml


class TestBackendWithTgBotGeneration:
    """Test generation with backend and tg_bot modules."""

    def test_both_services_exist(self, project_backend_tg_bot: Path):
        """Both backend and tg_bot should exist."""
        assert (project_backend_tg_bot / "services" / "backend").exists()
        assert (project_backend_tg_bot / "services" / "tg_bot").exists()

    def test_redis_included(self, project_backend_tg_bot: Path):
        """Redis should be included for event-driven modules."""
        import yaml

        compose_base = project_backend_tg_bot / "infra" / "compose.base.yml"
        content = yaml.safe_load(compose_base.read_text())
        assert "redis" in content["services"]

    def test_services_yml_has_both(self, project_backend_tg_bot: Path):
        """services.yml should have both services."""
        import yaml

        content = yaml.safe_load((project_backend_tg_bot / "services.yml").read_text())
        services = {service["name"]: service for service in content["services"]}
        assert services["backend"]["type"] == "python-fastapi"
        assert services["tg_bot"]["type"] == "python"

    def test_tg_bot_depends_on_redis(self, project_backend_tg_bot: Path):
        """tg_bot should depend on redis: service_healthy in services.yml."""
        import yaml

        content = yaml.safe_load((project_backend_tg_bot / "services.yml").read_text())
        tg_bot = next((s for s in content["services"] if s["name"] == "tg_bot"), None)
        assert tg_bot is not None, "tg_bot service not found"
        assert "depends_on" in tg_bot
        assert tg_bot["depends_on"].get("redis") == "service_healthy"

    def test_compose_tg_bot_waits_for_redis(self, project_backend_tg_bot: Path):
        """tg_bot should wait for Redis health in real compose output."""
        import yaml

        compose = yaml.safe_load(
            (project_backend_tg_bot / "infra" / "compose.base.yml").read_text()
        )
        depends_on = compose["services"]["tg_bot"]["depends_on"]
        assert depends_on["redis"]["condition"] == "service_healthy"
        assert depends_on["backend"]["condition"] == "service_started"


class TestFullStackGeneration:
    """Test generation with all modules."""

    def test_all_services_exist(self, project_fullstack: Path):
        """All services should exist."""
        assert (project_fullstack / "services" / "backend").exists()
        assert (project_fullstack / "services" / "tg_bot").exists()
        assert (project_fullstack / "services" / "notifications_worker").exists()
        assert (project_fullstack / "services" / "frontend").exists()

    def test_no_jinja_artifacts(self, project_fullstack: Path):
        """No Jinja artifacts in full generation."""
        errors = check_no_jinja_artifacts(project_fullstack)
        assert not errors, f"Found Jinja artifacts: {errors}"

    def test_services_yml_types(self, project_fullstack: Path):
        """Full generation should keep polling bots and FastStream workers distinct."""
        import yaml

        content = yaml.safe_load((project_fullstack / "services.yml").read_text())
        services = {service["name"]: service for service in content["services"]}
        assert services["backend"]["type"] == "python-fastapi"
        assert services["tg_bot"]["type"] == "python"
        assert services["notifications_worker"]["type"] == "python-faststream"
        assert services["frontend"]["type"] == "node"

    def test_deptry_uses_dependency_groups_without_optional_group_overrides(
        self, project_fullstack: Path
    ):
        """deptry should not look for dev deps in missing optional-dependencies groups."""
        for service in ("backend", "tg_bot", "notifications_worker"):
            pyproject = tomllib.loads(
                (project_fullstack / "services" / service / "pyproject.toml").read_text()
            )

            assert pyproject["dependency-groups"]["dev"]
            deptry_config = pyproject["tool"]["deptry"]
            assert "pep621_dev_dependency_groups" not in deptry_config
            assert "pep_621_dev_dependency_groups" not in deptry_config
            assert "optional_dependencies_dev_groups" not in deptry_config

    def test_notifications_import_before_setup_has_clear_error(self, project_fullstack: Path):
        """Fresh projects should point to setup instead of leaking an import path."""
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                "import services.notifications_worker.src.main",
            ],
            cwd=project_fullstack,
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0
        assert "run `make setup` from the project root" in result.stderr


class TestEnvExample:
    """Test .env.example generation."""

    @pytest.mark.parametrize(
        "fixture_name",
        [
            "project_backend",
            "project_standalone",
            "project_notifications",
            "project_frontend",
            "project_backend_tg_bot",
            "project_fullstack",
        ],
    )
    def test_env_and_example_have_same_keys(
        self, request: pytest.FixtureRequest, fixture_name: str
    ):
        """Generated .env and .env.example should document the same variables."""
        project = request.getfixturevalue(fixture_name)

        assert env_keys(project / ".env") == env_keys(project / ".env.example")

    def test_backend_only_env(self, project_backend: Path):
        """Backend-only should have postgres and redis but not telegram."""
        env_content = (project_backend / ".env.example").read_text()
        assert "POSTGRES" in env_content
        assert "BACKEND_PORT=8000" in env_content
        assert "# COMPOSE_PROJECT_NAME=test_project-dev" in env_content
        assert "REDIS" in env_content
        assert "TELEGRAM" not in env_content

    def test_with_tg_bot_env(self, project_backend_tg_bot: Path):
        """With tg_bot should have redis and telegram."""
        env_content = (project_backend_tg_bot / ".env.example").read_text()
        assert "POSTGRES" in env_content
        assert "REDIS" in env_content
        assert "TELEGRAM" in env_content

    def test_dev_service_host_ports_are_documented(self, project_backend_tg_bot: Path):
        """Host port overrides should be documented alongside service env vars."""
        env_content = (project_backend_tg_bot / ".env.example").read_text()
        readme = (project_backend_tg_bot / "README.md").read_text()

        assert "POSTGRES_HOST_PORT=5432" in env_content
        assert "REDIS_HOST_PORT=6379" in env_content
        assert "BACKEND_PORT=8000" in env_content
        assert "POSTGRES_HOST_PORT" in readme
        assert "REDIS_HOST_PORT" in readme
        assert "make dev-clean" in readme

    def test_standalone_env_has_redis_telegram_no_postgres(self, project_standalone: Path):
        """Standalone tg_bot should have redis and telegram but no postgres."""
        env_content = (project_standalone / ".env.example").read_text()
        assert "REDIS" in env_content
        assert "TELEGRAM" in env_content
        assert "POSTGRES" not in env_content


class TestModuleExclusion:
    """Test that unselected modules are properly excluded."""

    def test_copy_without_trust_excludes_unselected_modules_before_copy(self, tmp_path: Path):
        """Standalone tg_bot generation should not copy unselected module paths."""
        output, result = run_copier_command(tmp_path, "tg_bot")
        assert result.returncode == 0, (
            f"Copier failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        log_output = f"{result.stdout}\n{result.stderr}".replace("\\", "/")
        unselected_paths = (
            "services/backend",
            "services/frontend",
            "services/notifications_worker",
        )

        for path in unselected_paths:
            assert not (output / path).exists()
            assert path not in log_output

    def test_copy_with_trust_still_succeeds(self, tmp_path: Path):
        """Existing callers may keep passing --trust."""
        output = run_copier(tmp_path, "tg_bot", trust=True)
        assert (output / "services" / "tg_bot").exists()

    def test_backend_generation_keeps_backend_specs(self, tmp_path: Path):
        """Backend projects should keep shared and service specs."""
        output = run_copier(tmp_path, "backend")
        assert (output / "shared" / "spec").exists()
        assert (output / "services" / "backend" / "spec").exists()

    def test_copier_update_on_fresh_project(self, tmp_path: Path):
        """Fresh generated projects should update without post-task side effects."""
        output = run_copier(tmp_path, "tg_bot")

        subprocess.run(["git", "init"], cwd=output, check=True, capture_output=True, text=True)  # noqa: S603
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=output,
            check=True,
            capture_output=True,
            text=True,
        )  # noqa: S603
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=output,
            check=True,
            capture_output=True,
            text=True,
        )  # noqa: S603
        subprocess.run(["git", "add", "."], cwd=output, check=True, capture_output=True, text=True)  # noqa: S603
        subprocess.run(
            ["git", "commit", "-m", "Initial generated project"],
            cwd=output,
            check=True,
            capture_output=True,
            text=True,
        )  # noqa: S603

        update_result = subprocess.run(  # noqa: S603
            [str(VENV_COPIER), "update", "--defaults", "--vcs-ref=HEAD"],
            cwd=output,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert update_result.returncode == 0, (
            f"Copier update failed:\nstdout: {update_result.stdout}\nstderr: {update_result.stderr}"
        )

    def test_notifications_excluded_when_not_selected(self, tmp_path: Path):
        """notifications_worker should not exist when not in modules."""
        output = run_copier(tmp_path, "backend,tg_bot")
        assert not (output / "services" / "notifications_worker").exists()
        assert "notifications_worker" not in (output / "services.yml").read_text()

    def test_frontend_excluded_when_not_selected(self, tmp_path: Path):
        """frontend should not exist when not in modules."""
        output = run_copier(tmp_path, "backend,notifications")
        assert not (output / "services" / "frontend").exists()
        assert "frontend" not in (output / "services.yml").read_text()

    def test_tg_bot_excluded_when_not_selected(self, tmp_path: Path):
        """tg_bot should not exist when not in modules."""
        output = run_copier(tmp_path, "backend,frontend")
        assert not (output / "services" / "tg_bot").exists()
        assert "tg_bot" not in (output / "services.yml").read_text()


class TestComposeServices:
    """Test Docker Compose service generation."""

    def test_redis_with_backend(self, project_backend: Path):
        """Redis should be included with backend because REST endpoints publish events."""
        import yaml

        compose = yaml.safe_load((project_backend / "infra" / "compose.base.yml").read_text())
        assert "redis" in compose.get("services", {})

    def test_redis_with_notifications(self, tmp_path: Path):
        """Redis should be included with notifications module."""
        import yaml

        output = run_copier(tmp_path, "backend,notifications")
        compose = yaml.safe_load((output / "infra" / "compose.base.yml").read_text())
        assert "redis" in compose["services"]

    def test_fullstack_compose_services(self, project_fullstack: Path):
        """All selected services should be in compose."""
        import yaml

        compose = yaml.safe_load((project_fullstack / "infra" / "compose.base.yml").read_text())
        assert "backend" in compose["services"]
        assert "tg_bot" in compose["services"]
        assert "notifications_worker" in compose["services"]
        assert "redis" in compose["services"]
        assert "db" in compose["services"]

    def test_dev_compose_has_event_services(self, project_backend_tg_bot: Path):
        """compose.dev.yml should include tg_bot and redis when selected."""
        import yaml

        compose_dev = yaml.safe_load(
            (project_backend_tg_bot / "infra" / "compose.dev.yml").read_text()
        )
        assert "tg_bot" in compose_dev["services"]
        assert "redis" in compose_dev["services"]
        assert "profiles" not in compose_dev["services"]["tg_bot"]
        allow_placeholder = compose_dev["services"]["tg_bot"]["environment"][
            "TG_BOT_ALLOW_PLACEHOLDER_TOKEN"
        ]
        expected_enabled = str(True).lower()
        assert allow_placeholder == expected_enabled

    def test_services_yml_does_not_profile_tg_bot(self, project_backend_tg_bot: Path):
        """tg_bot should start with the default dev compose stack."""
        import yaml

        content = yaml.safe_load((project_backend_tg_bot / "services.yml").read_text())
        tg_bot = next((s for s in content["services"] if s["name"] == "tg_bot"), None)

        assert tg_bot is not None
        assert "profiles" not in tg_bot

    def test_base_and_dev_compose_do_not_publish_ports(self, project_backend_tg_bot: Path):
        """base+dev is safe for sibling workers that share one Docker host."""
        import yaml

        for filename in ("compose.base.yml", "compose.dev.yml"):
            compose = yaml.safe_load((project_backend_tg_bot / "infra" / filename).read_text())
            for service_name, service in compose.get("services", {}).items():
                assert "ports" not in service, f"{filename}:{service_name} publishes ports"

    def test_base_compose_sets_project_name_default(self, project_fullstack: Path):
        """Base compose should use COMPOSE_PROJECT_NAME or deterministic slug."""
        import yaml

        compose = yaml.safe_load((project_fullstack / "infra" / "compose.base.yml").read_text())

        assert compose["name"] == "${COMPOSE_PROJECT_NAME:-test_project}"

    def test_compose_extends_paths_match_infra_directory(self, project_fullstack: Path):
        """Compose overrides should keep the root compose contract from main."""
        import yaml

        for filename in (
            "compose.dev.yml",
            "compose.prod.yml",
            "compose.tests.integration.yml",
        ):
            compose = yaml.safe_load((project_fullstack / "infra" / filename).read_text())
            for service in compose.get("services", {}).values():
                if "extends" in service:
                    assert service["extends"]["file"] == "compose.base.yml"

    def test_compose_uses_only_implicit_default_network(self, project_fullstack: Path):
        """Generated compose files must not declare custom networks."""
        import yaml

        for filename in ("compose.base.yml", "compose.dev.yml", "compose.local.yml"):
            compose = yaml.safe_load((project_fullstack / "infra" / filename).read_text())
            assert "networks" not in compose, f"{filename} declares custom networks"

    def test_local_compose_uses_configurable_host_ports(self, project_fullstack: Path):
        """compose.local.yml should keep host port UX configurable."""
        import yaml

        compose_local_path = project_fullstack / "infra" / "compose.local.yml"
        compose_text = compose_local_path.read_text()
        compose_local = yaml.safe_load(compose_text)

        assert '"${BACKEND_PORT:-8000}:8000"' in compose_text
        assert '"${POSTGRES_HOST_PORT:-5432}:5432"' in compose_text
        assert '"${REDIS_HOST_PORT:-6379}:6379"' in compose_text
        assert '"${FRONTEND_PORT:-3000}:3000"' in compose_text
        assert compose_local["services"]["backend"]["ports"] == ["${BACKEND_PORT:-8000}:8000"]
        assert compose_local["services"]["db"]["ports"] == ["${POSTGRES_HOST_PORT:-5432}:5432"]
        assert compose_local["services"]["redis"]["ports"] == ["${REDIS_HOST_PORT:-6379}:6379"]
        assert compose_local["services"]["frontend"]["ports"] == ["${FRONTEND_PORT:-3000}:3000"]

    def test_prod_compose_publishes_backend_port(self, project_backend_tg_bot: Path):
        """Prod deploy (base+prod, no local) must publish backend on the host."""
        import yaml

        compose_prod_path = project_backend_tg_bot / "infra" / "compose.prod.yml"
        compose_text = compose_prod_path.read_text()
        compose_prod = yaml.safe_load(compose_text)

        backend_ports = compose_prod["services"]["backend"]["ports"]
        expected = "${BACKEND_PORT:?Set BACKEND_PORT to a unique host port for this app}:8000"
        assert backend_ports == [expected]
        # Host port comes from BACKEND_PORT with no hardcoded fallback, so two
        # apps with distinct BACKEND_PORT values never collide on the host.
        assert ":8000" in compose_text
        assert "8000:8000" not in compose_text

    def test_dev_compose_has_redis_backend_only(self, project_backend: Path):
        """compose.dev.yml should include redis for backend event publishing."""
        import yaml

        compose_dev = yaml.safe_load((project_backend / "infra" / "compose.dev.yml").read_text())
        assert "redis" in compose_dev.get("services", {})
        assert "tg_bot" not in compose_dev.get("services", {})

    def test_dev_compose_uses_image_venvs(self, project_fullstack: Path):
        """compose.dev.yml should not run Python from host-created venvs."""
        import yaml

        compose_dev = yaml.safe_load((project_fullstack / "infra" / "compose.dev.yml").read_text())
        service_paths = {
            "backend": "/app/services/backend/.venv",
            "tg_bot": "/app/services/tg_bot/.venv",
            "notifications_worker": "/app/services/notifications_worker/.venv",
        }

        for service_name, venv_path in service_paths.items():
            service = compose_dev["services"][service_name]
            path = service["environment"]["PATH"]
            volumes = service["volumes"]
            assert path.startswith(f"{venv_path}/bin:"), f"{service_name} PATH is {path}"
            assert f"/workspace/services/{service_name}/.venv/bin" not in path
            assert venv_path in volumes

    def test_base_compose_no_required_var_syntax(self, project_backend: Path):
        """x-backend-env must not use ${VAR:?} — breaks env_file-based workflows.

        Docker Compose evaluates ${VAR:?} from the shell environment BEFORE
        processing env_file directives. This causes 'required variable is missing'
        errors even when .env contains all values.
        """
        import re

        compose = (project_backend / "infra" / "compose.base.yml").read_text()
        # Everything before 'services:' is the YAML anchors section
        anchor_section = compose.split("services:")[0]
        required_vars = re.findall(r"\$\{(\w+):\?[^}]*\}", anchor_section)
        assert not required_vars, (
            f"x-backend-env uses ${{VAR:?}} for {required_vars} — "
            "this breaks env_file workflows. Use ${{VAR:-default}} instead."
        )
        # Bare ${VAR} without defaults also breaks — empty string when not in shell
        bare_vars = re.findall(r"\$\{(\w+)\}", anchor_section)
        assert not bare_vars, (
            f"x-backend-env uses bare ${{VAR}} for {bare_vars} — "
            "resolves to empty string when not in shell env. Use ${{VAR:-default}}."
        )

    def test_prod_compose_no_env_prod_file(self, project_backend: Path):
        """compose.prod.yml must not reference .env.prod.

        The orchestrator assembles all env vars into a single DOTENV_B64 secret
        decoded into .env at deploy time. .env.prod is always empty — it only
        exists because compose.prod.yml references it and deploy.yml touches it
        to prevent 'file not found'. This is a fragile no-op that misleads
        developers and agents into thinking prod overrides belong there.
        """
        prod_compose = (project_backend / "infra" / "compose.prod.yml").read_text()
        assert ".env.prod" not in prod_compose, (
            "compose.prod.yml still references .env.prod — "
            "all prod env vars come from the single .env decoded from DOTENV_B64"
        )

    def test_no_env_prod_file_in_project(self, project_backend: Path):
        """Generated project must not contain .env.prod file.

        .env.prod was always an empty placeholder. With compose.prod.yml no longer
        referencing it, the file itself should not be generated.
        """
        assert not (project_backend / "infra" / ".env.prod").exists(), (
            "infra/.env.prod should not exist — all prod env comes from .env"
        )


class TestIntegrationCompose:
    """Validate compose.tests.integration.yml semantics (not just YAML validity)."""

    def test_backend_path_uses_image_venv(self, project_backend: Path):
        """Backend PATH must reference /app/ (image venv), not /workspace/ (host venv).

        Host-built .venv has shebangs like #!/opt/hostedtoolcache/Python/.../python
        which don't exist inside the container — causes 'cannot execute' errors.
        """
        import yaml

        compose = yaml.safe_load(
            (project_backend / "infra" / "compose.tests.integration.yml").read_text()
        )
        path_val = compose["services"]["backend"]["environment"]["PATH"]
        assert "/workspace/services/backend/.venv/bin" not in path_val, (
            f"backend PATH must not use host-mounted venv: {path_val}"
        )

    def test_integration_tests_path_uses_image_venv(self, project_backend: Path):
        """integration-tests PATH must reference /app/ (image venv), not /workspace/."""
        import yaml

        compose = yaml.safe_load(
            (project_backend / "infra" / "compose.tests.integration.yml").read_text()
        )
        path_val = compose["services"]["integration-tests"]["environment"]["PATH"]
        assert "/workspace/services/backend/.venv/bin" not in path_val, (
            f"integration-tests PATH must not use host-mounted venv: {path_val}"
        )

    def test_workspace_writers_require_checkout_ownership(self, project_backend: Path):
        """Both containers writing to /workspace must use the checkout owner."""
        import yaml

        compose_text = (project_backend / "infra" / "compose.tests.integration.yml").read_text()
        compose = yaml.safe_load(compose_text)
        expected = (
            "${HOST_UID:?set HOST_UID to the checkout owner UID}:"
            "${HOST_GID:?set HOST_GID to the checkout owner GID}"
        )

        for service_name in ("backend", "integration-tests"):
            assert compose["services"][service_name]["user"] == expected

        assert "${HOST_UID:-1000}" not in compose_text
        assert "${HOST_GID:-1000}" not in compose_text

    def test_integration_tests_run_generation_in_container(self, project_backend: Path):
        """The integration container must exercise generation against the bind mount."""
        import yaml

        compose = yaml.safe_load(
            (project_backend / "infra" / "compose.tests.integration.yml").read_text()
        )
        command = compose["services"]["integration-tests"]["command"]
        assert "python -m framework.generate" in command
        assert (
            compose["services"]["integration-tests"]["environment"]["SERVICE_TEMPLATE_ROOT"]
            == "/workspace"
        )
        dockerignore = (project_backend / ".dockerignore").read_text()
        assert "!.framework/**" in dockerignore

    def test_backend_has_healthcheck(self, project_backend: Path):
        """Backend must define a healthcheck so integration-tests can wait for readiness."""
        import yaml

        compose = yaml.safe_load(
            (project_backend / "infra" / "compose.tests.integration.yml").read_text()
        )
        assert "healthcheck" in compose["services"]["backend"], (
            "backend must have a healthcheck for depends_on service_healthy"
        )

    def test_integration_tests_wait_for_backend_healthy(self, project_backend: Path):
        """integration-tests must use condition: service_healthy, not service_started."""
        import yaml

        compose = yaml.safe_load(
            (project_backend / "infra" / "compose.tests.integration.yml").read_text()
        )
        deps = compose["services"]["integration-tests"]["depends_on"]
        assert deps["backend"]["condition"] == "service_healthy", (
            f"Expected service_healthy, got: {deps['backend']['condition']}"
        )

    def test_database_urls_use_postgres_host_and_port_env(self, project_backend: Path):
        """Integration compose should allow external database host overrides."""
        import yaml

        compose_text = (project_backend / "infra" / "compose.tests.integration.yml").read_text()
        compose = yaml.safe_load(compose_text)

        assert "@db:5432" not in compose_text
        for service_name in ("backend", "integration-tests"):
            env = compose["services"][service_name]["environment"]
            assert "${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}" in env["DATABASE_URL"]
            assert "${POSTGRES_HOST:-db}:${POSTGRES_PORT:-5432}" in env["ASYNC_DATABASE_URL"]

    def test_redis_fixture_url_is_parameterized(self, project_backend_tg_bot: Path):
        """Redis tests should keep defaults while allowing env overrides."""
        conftest = (project_backend_tg_bot / "tests" / "conftest.py").read_text()

        assert 'os.getenv("TEST_REDIS_URL")' in conftest
        assert 'os.getenv("TEST_REDIS_HOST", "localhost")' in conftest
        assert 'os.getenv("TEST_REDIS_PORT", "6379")' in conftest
        assert 'os.getenv("TEST_REDIS_URL", "redis://localhost:6379/15")' not in conftest


class TestIntegration:
    """Integration tests - validate generated project structure."""

    def test_docker_compose_config_valid(self, tmp_path: Path):
        """docker compose config should pass on generated project."""
        if shutil.which("docker") is None:
            pytest.skip("docker not available")

        output = run_copier(tmp_path, "backend")
        shutil.copy(output / ".env.example", output / ".env")
        result = subprocess.run(  # noqa: S603, S607
            [
                "docker",
                "compose",
                "--env-file",
                ".env",
                "-f",
                "infra/compose.base.yml",
                "config",
            ],
            capture_output=True,
            text=True,
            cwd=output,
        )
        assert result.returncode == 0, f"docker compose config failed: {result.stderr}"

    def test_docker_compose_worker_config_valid(self, tmp_path: Path):
        """worker compose should resolve from the generated project root."""
        if shutil.which("docker") is None:
            pytest.skip("docker not available")

        output = run_copier(tmp_path, "backend,notifications")
        shutil.copy(output / ".env.example", output / ".env")
        result = subprocess.run(  # noqa: S603, S607
            [
                "docker",
                "compose",
                "--env-file",
                ".env",
                "-f",
                "infra/compose.base.yml",
                "-f",
                "infra/compose.dev.yml",
                "config",
            ],
            capture_output=True,
            text=True,
            cwd=output,
        )
        assert result.returncode == 0, f"docker compose config failed: {result.stderr}"

    def test_integration_generation_with_non_default_checkout_owner(self, tmp_path: Path):
        """Container generation can write a checkout owned by a non-1000 UID/GID."""
        if shutil.which("docker") is None:
            pytest.skip("docker not available")

        output = run_copier(tmp_path, "backend")
        shutil.copy(output / ".env.example", output / ".env")
        owner = "12345:12345"
        compose = ["docker", "compose", "-f", "infra/compose.tests.integration.yml"]
        env = {**os.environ, "HOST_UID": "12345", "HOST_GID": "12345"}

        try:
            chown = subprocess.run(  # noqa: S603, S607
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{output}:/workspace",
                    "alpine:3.20",
                    "chown",
                    "-R",
                    owner,
                    "/workspace",
                ],
                capture_output=True,
                text=True,
            )
            assert chown.returncode == 0, chown.stderr

            registry = output / "services" / "backend" / "src" / "generated" / "registry.py"
            registry.unlink(missing_ok=True)
            result = subprocess.run(  # noqa: S603, S607
                [
                    *compose,
                    "run",
                    "--rm",
                    "--no-deps",
                    "--build",
                    "integration-tests",
                    "python",
                    "-m",
                    "framework.generate",
                ],
                cwd=output,
                env=env,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stderr
            assert registry.exists()
            assert registry.stat().st_uid == 12345
            assert registry.stat().st_gid == 12345
            read_as_runtime_user = subprocess.run(  # noqa: S603, S607
                [
                    "docker",
                    "run",
                    "--rm",
                    "--user",
                    "23456:23456",
                    "-v",
                    f"{output}:/workspace:ro",
                    "alpine:3.20",
                    "cat",
                    "/workspace/services/backend/src/generated/registry.py",
                ],
                capture_output=True,
                text=True,
            )
            assert read_as_runtime_user.returncode == 0, read_as_runtime_user.stderr
            assert "def create_api_router" in read_as_runtime_user.stdout
        finally:
            subprocess.run(  # noqa: S603, S607
                [
                    "docker",
                    "run",
                    "--rm",
                    "-v",
                    f"{output}:/workspace",
                    "alpine:3.20",
                    "chown",
                    "-R",
                    f"{os.getuid()}:{os.getgid()}",
                    "/workspace",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            subprocess.run(  # noqa: S603, S607
                [*compose, "down", "--volumes", "--remove-orphans"],
                cwd=output,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

    def test_docker_compose_project_name_default_and_env_override(self, tmp_path: Path):
        """Compose should use the slug by default and COMPOSE_PROJECT_NAME when set."""
        if shutil.which("docker") is None:
            pytest.skip("docker not available")

        import yaml

        output = run_copier(tmp_path, "backend")
        env = {key: value for key, value in os.environ.items() if key != "COMPOSE_PROJECT_NAME"}
        base_cmd = [
            "docker",
            "compose",
            "--env-file",
            ".env",
            "-f",
            "infra/compose.base.yml",
            "config",
        ]

        shutil.copy(output / ".env.example", output / ".env")
        default_result = subprocess.run(  # noqa: S603, S607
            base_cmd,
            capture_output=True,
            text=True,
            cwd=output,
            env=env,
        )
        assert default_result.returncode == 0, default_result.stderr
        assert yaml.safe_load(default_result.stdout)["name"] == "test_project"

        (output / ".env").write_text(
            (output / ".env.example").read_text() + "\nCOMPOSE_PROJECT_NAME=custom_project\n"
        )
        custom_result = subprocess.run(  # noqa: S603, S607
            base_cmd,
            capture_output=True,
            text=True,
            cwd=output,
            env=env,
        )
        assert custom_result.returncode == 0, custom_result.stderr
        assert yaml.safe_load(custom_result.stdout)["name"] == "custom_project"

        explicit_result = subprocess.run(  # noqa: S603, S607
            [
                "docker",
                "compose",
                "--project-name",
                "external_project",
                "--env-file",
                ".env",
                "-f",
                "infra/compose.base.yml",
                "config",
            ],
            capture_output=True,
            text=True,
            cwd=output,
            env=env,
        )
        assert explicit_result.returncode == 0, explicit_result.stderr
        assert yaml.safe_load(explicit_result.stdout)["name"] == "external_project"

    def test_docker_compose_config_full_stack(self, tmp_path: Path):
        """docker compose config should pass for full stack."""
        if shutil.which("docker") is None:
            pytest.skip("docker not available")

        output = run_copier(tmp_path, "backend,tg_bot,notifications,frontend")
        shutil.copy(output / ".env.example", output / ".env")
        result = subprocess.run(  # noqa: S603, S607
            [
                "docker",
                "compose",
                "--env-file",
                ".env",
                "-f",
                "infra/compose.base.yml",
                "config",
            ],
            capture_output=True,
            text=True,
            cwd=output,
        )
        assert result.returncode == 0, f"docker compose config failed: {result.stderr}"

    def test_framework_generate_runs(self, project_backend: Path):
        """framework generation should run successfully via python."""
        framework_path = project_backend / ".framework"
        env = {"PYTHONPATH": str(framework_path)}

        result = subprocess.run(
            [sys.executable, "-m", "framework.generate"],
            cwd=project_backend,
            env={**os.environ, **env},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"framework.generate failed:\nstderr: {result.stderr}\nstdout: {result.stdout}"
        )

    def test_makefile_has_correct_targets(self, project_backend: Path):
        """Makefile should have expected targets."""
        makefile = (project_backend / "Makefile").read_text()
        assert "dev-start:" in makefile
        assert "worker-start:" in makefile
        assert "worker-stop:" in makefile
        assert "worker-call:" in makefile
        assert "smoke-probe:" in makefile
        assert "infra-start:" in makefile
        assert "ps:" in makefile
        assert "$(DOCKER_COMPOSE) $(COMPOSE_DEV) ps" in makefile
        assert "dev-smoke:" in makefile

    def test_makefile_passes_checkout_owner_to_integration_compose(self, project_backend: Path):
        """Local integration runs derive the same ownership contract as CI."""
        makefile = (project_backend / "Makefile").read_text()
        assert "CHECKOUT_UID := $(shell stat -c '%u' .)" in makefile
        assert "CHECKOUT_GID := $(shell stat -c '%g' .)" in makefile
        assert "HOST_UID=$(CHECKOUT_UID) HOST_GID=$(CHECKOUT_GID)" in makefile
        assert "dev-stop:" in makefile
        assert "dev-clean:" in makefile
        assert ("$(DOCKER_COMPOSE) $(COMPOSE_LOCAL) down --volumes --remove-orphans") in makefile
        assert "lint:" in makefile
        assert "tests:" in makefile

    def test_migration_targets_run_alembic_in_backend_container_by_default(
        self, project_backend: Path
    ):
        """Migration targets should use the backend container by default."""
        makefile = (project_backend / "Makefile").read_text()

        assert "SKIP_INFRA_START" in makefile
        assert "COMPOSE_LOCAL := $(COMPOSE_DEV) -f infra/compose.local.yml" in makefile
        assert "BACKEND_ALEMBIC := PYTHONPATH=. services/backend/.venv/bin/alembic" in makefile
        assert (
            "DOCKER_ALEMBIC := $(DOCKER_COMPOSE) $(COMPOSE_DEV) run --rm --build backend "
            "alembic -c $(ALEMBIC_CONFIG)"
        ) in makefile
        assert "$(MAKE) worker-start svc=db" in makefile
        assert "$(MAKE) dev-start svc=db" not in makefile
        assert "$(ALEMBIC) upgrade head" in makefile
        assert '$(ALEMBIC) revision --autogenerate -m "$(name)"' in makefile

    def test_worker_targets_use_dev_compose_without_local_ports(self, project_backend: Path):
        """Worker mode should use base+dev compose and omit the local port layer."""
        makefile = (project_backend / "Makefile").read_text()

        assert ("$(DOCKER_COMPOSE) $(COMPOSE_DEV) up -d --build --wait $(svc)") in makefile
        assert "$(DOCKER_COMPOSE) $(COMPOSE_DEV) down --remove-orphans" in makefile
        assert ("$(DOCKER_COMPOSE) $(COMPOSE_DEV) down --volumes --remove-orphans") in makefile
        assert "SMOKE_URL ?= http://backend:8000/health" in makefile
        assert (
            "$(DOCKER_COMPOSE) $(COMPOSE_DEV) run --rm --no-deps $(SMOKE_RUNNER) python -c"
        ) in makefile
        assert 'worker-call:\n\t@if [ -z "$(SMOKE_RUNNER)" ] || [ -z "$(url)" ]; then' in makefile
        assert "method ?= POST" in makefile
        assert "urllib.request.Request(url, data=data, method=method.upper())" in makefile
        assert "urllib.request.urlopen" in makefile
        assert '"$$url" "$$method" "$$body"' in makefile

    def test_worker_call_preserves_json_body_quoting(self, tmp_path: Path, project_backend: Path):
        """worker-call should pass JSON bodies to the runner without shell stripping."""
        capture = tmp_path / "argv.txt"
        fake_compose = tmp_path / "docker-compose"
        fake_compose.write_text(
            "#!/usr/bin/env python3\n"
            "import pathlib\n"
            "import sys\n"
            f"pathlib.Path({str(capture)!r}).write_text(repr(sys.argv[1:]))\n"
        )
        fake_compose.chmod(0o755)

        result = subprocess.run(
            [
                "make",
                "worker-call",
                f"DOCKER_COMPOSE={fake_compose}",
                "SMOKE_RUNNER=backend",
                "url=http://backend:8000/users",
                "method=POST",
                'body={"name":"Ada"}',
            ],
            cwd=project_backend,
            capture_output=True,
            text=True,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        argv = ast.literal_eval(capture.read_text())
        assert argv[-3:] == [
            "http://backend:8000/users",
            "POST",
            '{"name":"Ada"}',
        ]

    def test_dev_start_uses_local_compose_layer(self, project_backend_tg_bot: Path):
        """Human dev-start should still publish ports through compose.local.yml."""
        makefile = (project_backend_tg_bot / "Makefile").read_text()

        assert ("$(DOCKER_COMPOSE) $(COMPOSE_LOCAL) up -d --build --wait $(svc)") in makefile
        assert "$(DOCKER_COMPOSE) $(COMPOSE_LOCAL) down --remove-orphans" in makefile
        assert ("$(DOCKER_COMPOSE) $(COMPOSE_LOCAL) down --volumes --remove-orphans") in makefile
        assert ("$(DOCKER_COMPOSE) $(COMPOSE_DEV) up -d --wait $(INFRA_SERVICES)") in makefile
        assert "INFRA_SERVICES := db redis" in makefile

    def test_infra_start_uses_only_available_infra_services(self, project_standalone: Path):
        """Standalone event projects should not try to start a missing db service."""
        makefile = (project_standalone / "Makefile").read_text()

        assert "INFRA_SERVICES := redis" in makefile
        assert "up -d --wait $(INFRA_SERVICES)" in makefile

    def test_makemigrations_upgrades_before_autogenerate(self, project_backend: Path):
        """makemigrations should bring a clean database to head before autogenerate."""
        result = subprocess.run(
            ["make", "makemigrations", "name=add_items", "DOCKER_COMPOSE=/bin/echo"],
            cwd=project_backend,
            capture_output=True,
            text=True,
        )
        output = result.stdout + result.stderr

        assert result.returncode == 0, output
        upgrade = "alembic -c services/backend/migrations/alembic.ini upgrade head"
        revision = (
            "alembic -c services/backend/migrations/alembic.ini "
            "revision --autogenerate -m add_items"
        )
        assert upgrade in output
        assert revision in output
        assert "-f infra/compose.local.yml" not in output
        assert output.index(upgrade) < output.index(revision)

    def test_migration_targets_can_skip_docker_infra(self, tmp_path: Path, project_backend: Path):
        """SKIP_INFRA_START should use the backend venv and not call docker compose."""
        project = tmp_path / "project"
        shutil.copytree(project_backend, project)
        alembic = project / "services/backend/.venv/bin/alembic"
        alembic.parent.mkdir(parents=True)
        alembic.write_text('#!/bin/sh\necho "fake alembic $@"\n')
        alembic.chmod(0o755)

        result = subprocess.run(
            [
                "make",
                "makemigrations",
                "name=add_items",
                "SKIP_INFRA_START=1",
                "DOCKER_COMPOSE=/bin/false",
            ],
            cwd=project,
            capture_output=True,
            text=True,
        )
        output = result.stdout + result.stderr

        assert result.returncode == 0, output
        assert "fake alembic -c services/backend/migrations/alembic.ini upgrade head" in output
        assert (
            "fake alembic -c services/backend/migrations/alembic.ini "
            "revision --autogenerate -m add_items"
        ) in output
        assert "dev-start svc=db" not in output

    def test_alembic_renders_custom_datetime_as_sqlalchemy_type(self, project_backend: Path):
        """Autogenerated migrations should not reference unimported runtime modules."""
        env_py = (project_backend / "services/backend/migrations/env.py").read_text()

        assert "from services.backend.src.core.db import TzAwareDateTime" in env_py
        assert "def render_item(" in env_py
        assert 'return "sa.DateTime(timezone=True)"' in env_py
        assert "render_item=render_item" in env_py

    def test_dev_smoke_uses_isolated_compose_project(self, project_backend_tg_bot: Path):
        """dev-smoke cleanup must not stop or remove ordinary dev compose resources."""
        makefile = (project_backend_tg_bot / "Makefile").read_text()

        assert "COMPOSE_ENV_DEV_SMOKE := COMPOSE_PROJECT_NAME=test_project-dev-smoke" in makefile
        assert "@set -e;" in makefile
        assert (
            "cleanup() { $(COMPOSE_ENV_DEV_SMOKE) $(DOCKER_COMPOSE) $(COMPOSE_DEV) down "
            "--volumes --remove-orphans"
        ) in makefile
        assert (
            "$(COMPOSE_ENV_DEV_SMOKE) $(DOCKER_COMPOSE) $(COMPOSE_DEV) run --rm "
            '--build --no-deps "$$svc"'
        ) in makefile

    def test_dev_smoke_fails_when_any_service_fails(self, project_fullstack: Path, tmp_path: Path):
        """dev-smoke should fail fast when a non-final service check fails."""
        fake_compose = tmp_path / "fake-compose"
        calls_log = tmp_path / "compose-calls.log"
        fake_compose.write_text(
            f"""#!/bin/sh
printf '%s\\n' "$*" >> {calls_log}
case " $* " in
  *" down "*) exit 0 ;;
  *" run "*" backend "*) exit 23 ;;
  *) exit 0 ;;
esac
"""
        )
        fake_compose.chmod(0o755)

        result = subprocess.run(  # noqa: S603
            ["make", "dev-smoke"],
            cwd=project_fullstack,
            env={**os.environ, "DOCKER_COMPOSE": str(fake_compose)},
            capture_output=True,
            text=True,
        )

        assert result.returncode != 0
        assert "Error 23" in result.stderr
        calls = calls_log.read_text()
        assert "run --rm --build --no-deps backend" in calls
        assert "run --rm --build --no-deps tg_bot" not in calls
        assert "down --volumes --remove-orphans" in calls

    def test_architecture_md_conditional_content(self, project_backend: Path):
        """ARCHITECTURE.md should have conditional content based on modules."""
        arch = (project_backend / "ARCHITECTURE.md").read_text()
        assert "PostgreSQL" in arch
        assert "python-fastapi" in arch
        repository_secrets = arch.split("Required repository secrets:", 1)[1]
        assert "APP_SECRET_KEY" not in repository_secrets

    def test_architecture_md_with_events(self, project_backend_tg_bot: Path):
        """ARCHITECTURE.md should mention Redis when event modules selected."""
        arch = (project_backend_tg_bot / "ARCHITECTURE.md").read_text()
        assert "Redis" in arch
        assert "python" in arch
        assert "python-faststream" not in arch

    def test_contributing_md_conditional_content(self, project_backend: Path):
        """CONTRIBUTING.md should include broker lifecycle guidance for backend."""
        contributing = (project_backend / "CONTRIBUTING.md").read_text()
        assert "Common Pitfalls" in contributing
        assert "Stale Shared Code" in contributing
        assert "Missing Broker Connection" in contributing

    def test_contributing_md_with_tg_bot(self, project_backend_tg_bot: Path):
        """CONTRIBUTING.md should include broker pitfall when event modules selected."""
        contributing = (project_backend_tg_bot / "CONTRIBUTING.md").read_text()
        assert "Common Pitfalls" in contributing
        assert "Missing Broker Connection" in contributing

    def test_standalone_tg_bot_docs_do_not_reference_generated_events(
        self, project_standalone: Path
    ):
        """Standalone tg_bot docs should not point agents to excluded generated modules."""
        root_agents = (project_standalone / "AGENTS.md").read_text()
        service_agents = (project_standalone / "services" / "tg_bot" / "AGENTS.md").read_text()
        contributing = (project_standalone / "CONTRIBUTING.md").read_text()

        assert "Standalone Telegram Bot" in root_agents
        assert "shared.generated" not in root_agents
        assert "broker event publishing is not available" in service_agents
        assert "shared.generated" not in service_agents
        assert "from .handlers import" not in service_agents
        assert "from .config import" not in service_agents
        assert "from services.tg_bot.src.handlers import" not in service_agents
        assert "from services.tg_bot.src.main import handle_start" in service_agents
        assert "from .middleware import install_update_logging" in service_agents
        assert "`REDIS_URL` | Yes" not in service_agents
        assert "Missing Broker Connection" not in contributing
        assert "spec validation, spec compliance, controller sync" in contributing
        assert "service dependency checks" in contributing
        assert "deptry" in contributing

    def test_backend_tg_bot_docs_keep_event_publishing(self, project_backend_tg_bot: Path):
        """backend+tg_bot docs should keep broker event instructions."""
        root_agents = (project_backend_tg_bot / "AGENTS.md").read_text()
        service_agents = (project_backend_tg_bot / "services" / "tg_bot" / "AGENTS.md").read_text()
        contributing = (project_backend_tg_bot / "CONTRIBUTING.md").read_text()

        assert "Брокер событий" in root_agents
        assert "from shared.generated.events import publish_command_received" in root_agents
        assert "Бот публикует события напрямую в Redis Streams" in service_agents
        assert "from shared.generated.schemas import CommandReceived" in service_agents
        assert "`REDIS_URL` | Yes" in service_agents
        assert "--max-absolute B --max-modules A --max-average A" in contributing
        assert "ruff format --check" in root_agents
        assert "spec validation, spec compliance, controller sync" in root_agents
        assert "`make check-deps`" in root_agents


class TestWorkflowGeneration:
    """Tests for GitHub Actions workflow generation."""

    def test_workflows_exist(self, project_backend: Path):
        """GitHub workflows should be generated."""
        workflows_dir = project_backend / ".github" / "workflows"
        assert workflows_dir.exists()
        assert (workflows_dir / "ci.yml").exists()
        assert (workflows_dir / "deploy.yml").exists()

    def test_backend_ci_exports_checkout_owner(self, project_backend: Path):
        """CI must pass the checkout owner UID/GID to integration compose."""
        ci_yml = (project_backend / ".github" / "workflows" / "ci.yml").read_text()
        assert 'echo "HOST_UID=$(stat -c \'%u\' .)" >> "$GITHUB_ENV"' in ci_yml
        assert 'echo "HOST_GID=$(stat -c \'%g\' .)" >> "$GITHUB_ENV"' in ci_yml

    def test_workflow_no_jinja_source_files(self, project_backend: Path):
        """Jinja source templates should not be copied."""
        workflows_dir = project_backend / ".github" / "workflows"
        for f in workflows_dir.iterdir():
            assert not f.name.endswith(".jinja"), f"Jinja source found: {f.name}"
        assert not (workflows_dir / "test-template.yml").exists()

    def test_backend_only_workflow_matrix(self, project_backend: Path):
        """Backend-only should have only backend in CI matrix."""
        ci_yml = (project_backend / ".github" / "workflows" / "ci.yml").read_text()
        assert "id: backend" in ci_yml
        assert "id: tg-bot" not in ci_yml
        assert "id: frontend" not in ci_yml
        assert "id: notifications-worker" not in ci_yml

    def test_full_stack_workflow_matrix(self, project_fullstack: Path):
        """Full stack should have all services in CI matrix."""
        ci_yml = (project_fullstack / ".github" / "workflows" / "ci.yml").read_text()
        assert "id: backend" in ci_yml
        assert "id: tg-bot" in ci_yml
        assert "id: frontend" in ci_yml
        assert "id: notifications-worker" in ci_yml

    def test_partial_modules_workflow_matrix(self, project_backend_tg_bot: Path):
        """Partial module selection should reflect in CI matrix."""
        ci_yml = (project_backend_tg_bot / ".github" / "workflows" / "ci.yml").read_text()
        assert "id: backend" in ci_yml
        assert "id: tg-bot" in ci_yml
        assert "id: frontend" not in ci_yml
        assert "id: notifications-worker" not in ci_yml

    def test_workflow_runs_dev_smoke(self, project_backend_tg_bot: Path):
        """CI should exercise dev compose, not only integration compose."""
        ci_yml = (project_backend_tg_bot / ".github" / "workflows" / "ci.yml").read_text()
        assert "run: make dev-smoke" in ci_yml

    def test_workflow_valid_yaml(self, project_fullstack: Path):
        """Generated workflows should be valid YAML."""
        import yaml

        workflows_dir = project_fullstack / ".github" / "workflows"
        for workflow_file in workflows_dir.iterdir():
            if workflow_file.suffix in (".yml", ".yaml"):
                content = yaml.safe_load(workflow_file.read_text())
                assert "name" in content, f"{workflow_file.name} missing 'name'"
                assert "jobs" in content, f"{workflow_file.name} missing 'jobs'"

    def test_workflow_no_jinja_artifacts(self, project_backend: Path):
        """Workflows should not have unrendered Jinja artifacts."""
        workflows_dir = project_backend / ".github" / "workflows"
        for workflow_file in workflows_dir.iterdir():
            if workflow_file.suffix in (".yml", ".yaml"):
                content = workflow_file.read_text()
                assert "{% if" not in content, f"Jinja in {workflow_file.name}"
                assert "{% endif" not in content, f"Jinja in {workflow_file.name}"
                assert "{{ modules" not in content, f"Jinja in {workflow_file.name}"
                assert "{{ project_" not in content, f"Jinja in {workflow_file.name}"

    def test_deploy_uses_dotenv_secret(self, project_backend: Path):
        """Deploy workflow should use DOTENV base64 approach."""
        deploy_yml = (project_backend / ".github" / "workflows" / "deploy.yml").read_text()
        assert "DOTENV_B64" in deploy_yml
        assert "base64 -d" in deploy_yml
        assert "secrets.DEPLOY_HOST" in deploy_yml
        assert "secrets.PROJECT_NAME" in deploy_yml

    def test_deploy_verifies_container_health(self, project_backend: Path):
        """deploy.yml must check container health after 'docker compose up -d'.

        Without this check, a crashing container (e.g. ModuleNotFoundError) still
        results in a green workflow — the orchestrator records deployed_url and
        reports success while the service is in a restart loop.
        """
        deploy_yml = (project_backend / ".github" / "workflows" / "deploy.yml").read_text()
        assert "ps --format json" in deploy_yml, "Missing post-deploy container status check"
        assert "sys.exit(1)" in deploy_yml, (
            "Health check must fail the workflow when containers are unhealthy"
        )

    def test_deploy_script_fails_fast(self, project_backend: Path):
        """Deploy SSH script must use 'set -euo pipefail' so health check failures propagate."""
        deploy_yml = (project_backend / ".github" / "workflows" / "deploy.yml").read_text()
        assert "set -euo pipefail" in deploy_yml

    def test_deploy_detects_crash_loops(self, project_backend: Path):
        """deploy.yml must check RestartCount to detect crash loops.

        A container with restart_policy: on-failure can show State=running
        at the exact moment of a point-in-time check while being in a crash
        loop. RestartCount > 0 within seconds of deploy is a definitive signal.
        """
        deploy_yml = (project_backend / ".github" / "workflows" / "deploy.yml").read_text()
        assert "RestartCount" in deploy_yml, (
            "Deploy must check docker RestartCount to reliably detect crash loops"
        )

    def test_deploy_no_touch_env_prod(self, project_backend: Path):
        """deploy.yml must not touch .env.prod.

        .env.prod was a workaround for compose.prod.yml referencing a second
        env file. With that reference removed, the touch is dead code that
        can mask failures if reintroduced.
        """
        deploy_yml = (project_backend / ".github" / "workflows" / "deploy.yml").read_text()
        assert ".env.prod" not in deploy_yml, (
            "deploy.yml still references .env.prod — remove the touch command"
        )

    def test_deploy_validates_dotenv_not_empty(self, project_backend: Path):
        """deploy.yml must fail fast if decoded .env is empty.

        If DOTENV_B64 secret is missing or corrupt, base64 -d produces an
        empty file. Without a check, compose starts with no env vars and
        services crash with confusing errors.
        """
        deploy_yml = (project_backend / ".github" / "workflows" / "deploy.yml").read_text()
        assert "! -s" in deploy_yml, (
            "deploy.yml must check that .env is non-empty after base64 decode"
        )


class TestCIWorkflowSimulation:
    """Simulate CI workflow on generated project to catch setup issues."""

    def _run_ci_env_setup(self, project_dir: Path) -> tuple[bool, str]:
        """Execute the 'Prepare environment files' step from ci.yml."""
        import yaml

        ci_yml = project_dir / ".github" / "workflows" / "ci.yml"
        if not ci_yml.exists():
            return False, "ci.yml not found"

        ci_content = yaml.safe_load(ci_yml.read_text())

        for job in ci_content.get("jobs", {}).values():
            for step in job.get("steps", []):
                if step.get("name") == "Prepare environment files":
                    script = step.get("run", "")
                    result = subprocess.run(  # noqa: S603, S607
                        ["bash", "-e", "-c", script],
                        cwd=project_dir,
                        capture_output=True,
                        text=True,
                    )
                    if result.returncode != 0:
                        return False, f"Env setup script failed:\n{result.stderr}"
                    return True, ""

        return False, "No 'Prepare environment files' step found in ci.yml"

    def _verify_compose_env_files(self, project_dir: Path) -> list[str]:
        """Verify all env_file paths in compose files exist after CI env setup."""
        import yaml

        errors = []
        compose_files = [
            f
            for f in (project_dir / "infra").glob("compose.*.yml")
            if "prod" not in f.name and "frontend" not in f.name
        ]

        for compose_path in compose_files:
            try:
                compose_content = yaml.safe_load(compose_path.read_text())
            except yaml.YAMLError as e:
                errors.append(f"{compose_path.name}: Invalid YAML: {e}")
                continue

            for service_name, service_config in compose_content.get("services", {}).items():
                if not isinstance(service_config, dict):
                    continue
                for env_file in service_config.get("env_file", []):
                    if env_file.startswith("${PWD}/"):
                        env_path = project_dir / env_file.removeprefix("${PWD}/")
                    else:
                        env_path = (compose_path.parent / env_file).resolve()
                    if not env_path.exists():
                        errors.append(
                            f"{compose_path.name}:{service_name} expects env_file "
                            f"'{env_file}' but it doesn't exist after CI env setup"
                        )

        return errors

    def _verify_compose_configs(self, project_dir: Path) -> list[str]:
        """Run 'docker compose config' on test compose files."""
        if shutil.which("docker") is None:
            return []

        # Ensure .env exists for variable interpolation
        env_file = project_dir / ".env"
        if not env_file.exists():
            env_example = project_dir / ".env.example"
            if env_example.exists():
                shutil.copy(env_example, env_file)

        errors = []
        compose_files = list((project_dir / "infra").glob("compose.tests.*.yml"))
        checkout_stat = project_dir.stat()
        env = {
            **os.environ,
            "HOST_UID": str(checkout_stat.st_uid),
            "HOST_GID": str(checkout_stat.st_gid),
        }

        for compose_path in compose_files:
            result = subprocess.run(  # noqa: S603, S607
                [
                    "docker",
                    "compose",
                    "--env-file",
                    ".env",
                    "-f",
                    str(compose_path.relative_to(project_dir)),
                    "config",
                ],
                cwd=project_dir,
                env=env,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                errors.append(
                    f"{compose_path.name}: docker compose config failed:\n{result.stderr}"
                )

        return errors

    def test_ci_env_setup_creates_required_files(self, tmp_path: Path):
        """CI 'Prepare environment files' step should create all required env files."""
        output = run_copier(tmp_path, "backend")

        success, error = self._run_ci_env_setup(output)
        assert success, error

        errors = self._verify_compose_env_files(output)
        assert not errors, "Missing env files after CI setup:\n" + "\n".join(errors)

    def test_compose_configs_valid_after_ci_setup(self, tmp_path: Path):
        """All compose files should pass 'docker compose config' after CI env setup."""
        if shutil.which("docker") is None:
            pytest.skip("docker not available")

        output = run_copier(tmp_path, "backend")

        success, error = self._run_ci_env_setup(output)
        assert success, error

        errors = self._verify_compose_configs(output)
        assert not errors, "Compose config validation failed:\n" + "\n".join(errors)

    @pytest.mark.parametrize(
        "modules",
        [
            "backend",
            "tg_bot",
            "backend,tg_bot",
            "backend,notifications",
            "backend,tg_bot,notifications",
            "backend,tg_bot,notifications,frontend",
        ],
    )
    def test_ci_simulation_all_module_combinations(self, tmp_path: Path, modules: str):
        """Every module combination should have valid CI env setup and compose configs."""
        output = run_copier(tmp_path, modules)

        success, error = self._run_ci_env_setup(output)
        assert success, f"modules={modules}: {error}"

        env_errors = self._verify_compose_env_files(output)
        assert not env_errors, f"modules={modules}: Missing env files:\n" + "\n".join(env_errors)

        if shutil.which("docker") is not None:
            compose_errors = self._verify_compose_configs(output)
            assert not compose_errors, f"modules={modules}: Compose config failed:\n" + "\n".join(
                compose_errors
            )


class TestDockerReadiness:
    """Tests that generated projects are ready for Docker builds and runtime."""

    def test_dockerignore_exists_and_excludes_venv(self, project_backend: Path):
        """.dockerignore should exist and exclude host .venv directories."""
        dockerignore = project_backend / ".dockerignore"
        assert dockerignore.exists(), (
            ".dockerignore not found — host .venv will break Docker builds"
        )
        content = dockerignore.read_text()
        assert ".venv/" in content, ".dockerignore must exclude .venv/"
        assert "services/*/.venv/" in content, ".dockerignore must exclude services/*/.venv/"

    def test_compose_dev_no_login_shell(self, project_backend: Path):
        """compose.dev.yml should not use bash -l (login shell resets Docker PATH)."""
        compose_dev = (project_backend / "infra" / "compose.dev.yml").read_text()
        assert "bash -lc" not in compose_dev, (
            "compose.dev.yml uses 'bash -lc' which resets Docker PATH, "
            "losing .venv/bin. Use 'bash -c' instead."
        )

    def test_backend_lifespan_has_broker(self, project_backend: Path):
        """Backend lifespan should connect the broker for REST event publishing."""
        lifespan = project_backend / "services" / "backend" / "src" / "app" / "lifespan.py"
        assert lifespan.exists(), "lifespan.py not found"
        content = lifespan.read_text()
        assert "get_broker" in content

    def test_fullstack_lifespan_has_broker(self, project_fullstack: Path):
        """Fullstack lifespan should import get_broker for event-driven modules."""
        lifespan = project_fullstack / "services" / "backend" / "src" / "app" / "lifespan.py"
        assert lifespan.exists(), "lifespan.py not found"
        content = lifespan.read_text()
        assert "get_broker" in content, (
            "Fullstack lifespan.py should import get_broker for tg_bot/notifications."
        )

    def test_compose_prod_config_valid(self, tmp_path: Path):
        """compose.prod.yml should pass docker compose config validation."""
        if shutil.which("docker") is None:
            pytest.skip("docker not available")

        output = run_copier(tmp_path, "backend,tg_bot,notifications,frontend")
        shutil.copy(output / ".env.example", output / ".env")

        # Set required IMAGE variables for prod compose
        env = {
            **os.environ,
            "BACKEND_IMAGE": "test:latest",
            "TG_BOT_IMAGE": "test:latest",
            "NOTIFICATIONS_WORKER_IMAGE": "test:latest",
            "FRONTEND_IMAGE": "test:latest",
        }

        result = subprocess.run(  # noqa: S603, S607
            [
                "docker",
                "compose",
                "--env-file",
                ".env",
                "-f",
                "infra/compose.base.yml",
                "-f",
                "infra/compose.prod.yml",
                "config",
            ],
            capture_output=True,
            text=True,
            cwd=output,
            env=env,
        )
        assert result.returncode == 0, (
            f"compose.prod.yml config failed (full stack):\n{result.stderr}"
        )

    def test_compose_dev_config_valid(self, tmp_path: Path):
        """compose.dev.yml should pass docker compose config validation."""
        if shutil.which("docker") is None:
            pytest.skip("docker not available")

        output = run_copier(tmp_path, "backend,tg_bot")
        shutil.copy(output / ".env.example", output / ".env")

        result = subprocess.run(  # noqa: S603, S607
            [
                "docker",
                "compose",
                "--env-file",
                ".env",
                "-f",
                "infra/compose.base.yml",
                "-f",
                "infra/compose.dev.yml",
                "config",
            ],
            capture_output=True,
            text=True,
            cwd=output,
        )
        assert result.returncode == 0, f"compose.dev.yml config failed:\n{result.stderr}"

    def test_health_endpoint_matches_test_assertion(self, project_backend: Path):
        """Integration test assertion should match actual health endpoint response."""
        health_py = (
            project_backend / "services" / "backend" / "src" / "app" / "api" / "v1" / "health.py"
        )
        test_file = project_backend / "tests" / "integration" / "test_example.py"

        if not health_py.exists() or not test_file.exists():
            pytest.skip("health endpoint or integration test not found")

        health_content = health_py.read_text()
        test_content = test_file.read_text()

        # Extract status value from health endpoint
        import re

        health_match = re.search(r'"status":\s*"(\w+)"', health_content)
        assert health_match, "Could not find status value in health.py"
        actual_status = health_match.group(1)

        # Extract status assertion from test
        test_match = re.search(r'data\["status"\]\s*==\s*"(\w+)"', test_content)
        assert test_match, "Could not find status assertion in test_example.py"
        expected_status = test_match.group(1)

        assert actual_status == expected_status, (
            f"Health endpoint returns '{actual_status}' but integration test "
            f"asserts '{expected_status}'"
        )


class TestCIWorkflowCorrectness:
    """Tests for CI workflow correctness beyond basic structure."""

    def test_standalone_ci_no_integration_cleanup(self, project_standalone: Path):
        """Standalone CI should not have docker compose down for non-existent integration stack."""
        ci_yml = (project_standalone / ".github" / "workflows" / "ci.yml").read_text()
        assert "compose.tests.integration" not in ci_yml or "Run integration tests" in ci_yml, (
            "Standalone CI references compose.tests.integration.yml (in Clean up step) "
            "but has no integration tests. This is a no-op that should be removed."
        )


class TestFormattingQuality:
    """Tests for generated file formatting quality."""

    def test_services_yml_no_excessive_blank_lines(self, project_standalone: Path):
        """services.yml should not have excessive blank lines from Jinja conditionals."""
        content = (project_standalone / "services.yml").read_text()
        assert "\n\n\n" not in content, (
            "services.yml has triple blank lines — likely from Jinja conditional whitespace"
        )

    def test_services_yml_no_leading_blank_in_list(self, project_backend: Path):
        """services.yml services list should not start with a blank line."""
        content = (project_backend / "services.yml").read_text()
        # Check for "services:\n\n  - name:" pattern (blank line after services:)
        assert "services:\n\n" not in content, (
            "services.yml has a blank line between 'services:' and first item"
        )


class TestGeneratedCodeQuality:
    """Tests that ensure the generated code is high quality."""

    def test_generated_code_passes_strict_linting(self, project_fullstack: Path):
        """Generated code must pass strict linting despite being excluded in user config."""
        ruff_toml = project_fullstack / "ruff.toml"
        config_content = ruff_toml.read_text()

        strict_content = "\n".join(
            line for line in config_content.splitlines() if "generated" not in line
        )
        strict_config_path = project_fullstack / "ruff.strict.toml"
        strict_config_path.write_text(strict_content)

        # Auto-fix first (import sorting etc. that's hard to get perfect in Jinja)
        ruff = str(VENV_RUFF)
        fix_cmd = [ruff, "check", "--config", "ruff.strict.toml", "--fix", "."]
        subprocess.run(fix_cmd, cwd=project_fullstack, capture_output=True, text=True)  # noqa: S603

        cmd = [ruff, "check", "--config", "ruff.strict.toml", "."]
        result = subprocess.run(cmd, cwd=project_fullstack, capture_output=True, text=True)  # noqa: S603

        assert result.returncode == 0, (
            f"Strict linting failed on generated code.\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        )


class TestEntrypointSafety:
    """Verify that Dockerfile entrypoints have no relative imports."""

    @staticmethod
    def _extract_python_entrypoint(dockerfile: Path) -> str | None:
        """Parse CMD from a Dockerfile and return the Python file path, if any."""
        for line in dockerfile.read_text().splitlines():
            stripped = line.strip()
            if not stripped.upper().startswith("CMD"):
                continue
            # CMD ["python", "services/.../main.py"]
            if "python" in stripped:
                parts = stripped.split('"')
                for part in parts:
                    if part.endswith(".py"):
                        return part
        return None

    @pytest.mark.parametrize(
        "fixture_name",
        ["project_backend", "project_standalone", "project_backend_tg_bot", "project_fullstack"],
    )
    def test_no_relative_imports_in_entrypoints(self, fixture_name: str, request):
        """Entrypoint files must not use relative imports (they run as scripts)."""
        project: Path = request.getfixturevalue(fixture_name)
        errors = []
        for dockerfile in project.rglob("services/*/Dockerfile"):
            entrypoint = self._extract_python_entrypoint(dockerfile)
            if entrypoint is None:
                continue
            entrypoint_path = project / entrypoint
            if not entrypoint_path.exists():
                continue
            content = entrypoint_path.read_text()
            for i, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("from .") and "import" in stripped:
                    errors.append(f"{entrypoint}:{i}: {stripped}")
        assert not errors, "Relative imports in script-mode entrypoints:\n" + "\n".join(errors)


class TestTemplateTestHealth:
    """Verify that template test files are correct before running them."""

    def test_async_fixtures_use_pytest_asyncio_decorator(self, project_backend_tg_bot: Path):
        """Async generator fixtures must use @pytest_asyncio.fixture, not @pytest.fixture.

        pytest-asyncio won't handle async teardown properly with the wrong decorator,
        causing tests to receive coroutine objects instead of resolved values.
        """
        errors = []
        for test_file in project_backend_tg_bot.rglob("tests/**/test_*.py"):
            source = test_file.read_text()
            try:
                tree = ast.parse(source)
            except SyntaxError:
                continue

            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if not isinstance(node, ast.AsyncFunctionDef):
                    continue

                is_fixture = False
                is_asyncio_fixture = False
                for dec in node.decorator_list:
                    dec_name = ast.unparse(dec)
                    if "pytest_asyncio.fixture" in dec_name:
                        is_asyncio_fixture = True
                    elif "pytest.fixture" in dec_name:
                        is_fixture = True

                if is_fixture and not is_asyncio_fixture:
                    rel = test_file.relative_to(project_backend_tg_bot)
                    errors.append(
                        f"{rel}:{node.lineno} — async def {node.name}() "
                        f"uses @pytest.fixture instead of @pytest_asyncio.fixture"
                    )

        assert not errors, "Async fixtures must use @pytest_asyncio.fixture:\n" + "\n".join(errors)

    def test_shared_transitive_deps_in_service_lockfiles(self, project_backend_tg_bot: Path):
        """Service uv.lock files must include transitive deps from shared package.

        shared/pyproject.toml declares dependencies (e.g. structlog) that services
        consume via path dep. CI runs 'uv sync --frozen' which won't install
        packages missing from the lock file.
        """
        shared_toml = project_backend_tg_bot / "shared" / "pyproject.toml"
        if not shared_toml.exists():
            pytest.skip("no shared package")

        # Parse direct deps from shared/pyproject.toml
        with open(shared_toml, "rb") as f:
            pyproject = tomllib.load(f)

        raw_deps = pyproject.get("project", {}).get("dependencies", [])
        assert raw_deps, "No dependencies found in shared/pyproject.toml"

        shared_deps = set()
        for dep_str in raw_deps:
            # Extract package name (before any version specifier, extras, or markers)
            pkg = re.split(r"[>=<!\[;]", dep_str)[0].strip().lower()
            if pkg:
                shared_deps.add(pkg)

        errors = []
        for svc_dir in sorted((project_backend_tg_bot / "services").iterdir()):
            lock_file = svc_dir / "uv.lock"
            if not lock_file.exists():
                continue
            lock_content = lock_file.read_text().lower()
            for dep in shared_deps:
                # uv.lock uses [[package]] sections with name = "pkg"
                if f'name = "{dep}"' not in lock_content:
                    errors.append(
                        f"services/{svc_dir.name}/uv.lock missing '{dep}' "
                        f"(transitive dep from shared)"
                    )

        assert not errors, (
            "Transitive deps from shared/ must be in service lock files "
            "(CI uses 'uv sync --frozen'):\n" + "\n".join(errors)
        )


@pytest.mark.slow
class TestSlowIntegration:
    """Slow integration tests — run with make test-copier-slow."""

    @pytest.mark.parametrize("modules", ["backend", "tg_bot", "backend,tg_bot"])
    def test_make_setup_succeeds(self, tmp_path: Path, modules: str):
        """make setup should complete successfully in generated project."""
        output = run_copier(tmp_path, modules)

        result = subprocess.run(  # noqa: S603, S607
            ["make", "setup"],
            cwd=output,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, (
            f"make setup failed for modules={modules}:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_make_setup_warns_but_succeeds_with_lint_dirty_user_code(self, tmp_path: Path):
        """setup should install envs; lint remains responsible for lint failures."""
        output = run_copier(tmp_path, "tg_bot")
        dirty_file = output / "services" / "tg_bot" / "src" / "user_code.py"
        dirty_file.write_text('token = "secret-value"\n')

        setup_result = subprocess.run(  # noqa: S603, S607
            ["make", "setup"],
            cwd=output,
            capture_output=True,
            text=True,
            timeout=300,
        )
        setup_output = setup_result.stdout + setup_result.stderr

        assert setup_result.returncode == 0, (
            "make setup should not fail on remaining lint issues:\n"
            f"stdout: {setup_result.stdout}\nstderr: {setup_result.stderr}"
        )
        assert "Setup complete!" in setup_output
        assert "Warning: lint auto-fix left issues" in setup_output
        assert (output / ".venv" / "bin" / "python").exists()
        assert (output / "services" / "tg_bot" / ".venv" / "bin" / "python").exists()

        lint_result = subprocess.run(  # noqa: S603, S607
            ["make", "lint"],
            cwd=output,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert lint_result.returncode != 0, "make lint should report the dirty user file"
        assert "S105" in (lint_result.stdout + lint_result.stderr)

    @pytest.mark.parametrize("modules", ["backend", "tg_bot", "notifications", "frontend"])
    def test_make_lint_after_setup(self, tmp_path: Path, modules: str):
        """make lint should pass after make setup in generated project."""
        output = run_copier(tmp_path, modules)

        setup_result = subprocess.run(  # noqa: S603, S607
            ["make", "setup"],
            cwd=output,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert setup_result.returncode == 0, (
            f"make setup failed for modules={modules}:\n{setup_result.stderr}"
        )

        lint_result = subprocess.run(  # noqa: S603, S607
            ["make", "lint"],
            cwd=output,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert lint_result.returncode == 0, (
            f"make lint failed for modules={modules}:\n"
            f"stdout: {lint_result.stdout}\nstderr: {lint_result.stderr}"
        )
        combined_output = lint_result.stdout + lint_result.stderr
        assert "Warning: Trying to extract the dependencies" not in combined_output
        assert "optional dependency groups" not in combined_output

    def test_e2e_dual_transport_pipeline(self, tmp_path: Path):
        """E2E: setup → generate-from-spec → lint → tests with dual-transport ops."""
        modules = "backend,tg_bot"
        output = run_copier(tmp_path, modules)

        # Step 1: make setup (creates venvs, installs deps, generates code)
        result = subprocess.run(  # noqa: S603, S607
            ["make", "setup"],
            cwd=output,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, (
            f"make setup failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Step 2: make generate-from-spec (idempotent regeneration)
        result = subprocess.run(  # noqa: S603, S607
            ["make", "generate-from-spec"],
            cwd=output,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            f"make generate-from-spec failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Step 3: make lint (ruff + spec validation + controller sync)
        result = subprocess.run(  # noqa: S603, S607
            ["make", "lint"],
            cwd=output,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            f"make lint failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Step 4: make tests (unit tests for all services)
        result = subprocess.run(  # noqa: S603, S607
            ["make", "tests"],
            cwd=output,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, (
            f"make tests failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    @pytest.mark.parametrize(
        ("service", "modules"),
        [
            ("backend", "backend,tg_bot"),
            ("tg_bot", "backend,tg_bot"),
            ("notifications_worker", "backend,tg_bot,notifications"),
        ],
    )
    def test_docker_entrypoint_imports(self, tmp_path: Path, service: str, modules: str):
        """Build Docker image and verify entrypoint imports succeed."""
        output = run_copier(tmp_path, modules)

        # make setup to generate code and install deps
        result = subprocess.run(  # noqa: S603, S607
            ["make", "setup"],
            cwd=output,
            capture_output=True,
            text=True,
            timeout=300,
        )
        assert result.returncode == 0, (
            f"make setup failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

        infra = output / "infra"
        if not infra.exists():
            pytest.skip("infra/ directory not found in generated project")

        compose_file = None
        for name in ("compose.base.yml", "docker-compose.yml", "compose.yml"):
            if (infra / name).exists():
                compose_file = name
                break
        if compose_file is None:
            pytest.skip("No compose file found in infra/")

        compose_cmd = ["docker", "compose", "-f", compose_file]

        # Build the specific service image
        result = subprocess.run(  # noqa: S603
            [*compose_cmd, "build", service],
            cwd=infra,
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert result.returncode == 0, (
            f"docker compose build {service} failed:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )

        # Extract the entrypoint module and verify imports work inside the container
        dockerfile = output / "services" / service / "Dockerfile"
        entrypoint = TestEntrypointSafety._extract_python_entrypoint(dockerfile)
        if entrypoint is None:
            pytest.skip(f"No Python entrypoint in {service} Dockerfile")

        # Convert file path to module path (services/x/src/main.py → services.x.src.main)
        module_path = entrypoint.replace("/", ".").removesuffix(".py")

        result = subprocess.run(  # noqa: S603
            [
                *compose_cmd,
                "run",
                "--rm",
                "--no-deps",
                "--entrypoint",
                "python",
                service,
                "-c",
                f"import {module_path}; print('OK')",
            ],
            cwd=infra,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, (
            f"Import check failed for {service} (module={module_path}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
