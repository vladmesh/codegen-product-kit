"""The generated product's one environment preparation command and every stage that uses it.

Behaviour runs the product's own `scripts/prepare-env.sh` with a recording `uv` stand-in, so
these tests need no network or package installation. The cold main image-generation regression
with real environments, images and integration containers is in the CI Runner Proof
(docs/RUNNER_PROOF.md).
"""

import os
from pathlib import Path
import shutil
import stat
import subprocess

import pytest
import yaml

SCRIPT = Path(__file__).parents[2] / "template/scripts/prepare-env.sh"
FAKE_UV = """#!/bin/sh
echo "$(basename "$PWD")|$*|${UV_PROJECT_ENVIRONMENT-unset}|${VIRTUAL_ENV-unset}" >> "$FAKE_UV_LOG"
if [ "$(basename "$PWD")" = "${FAKE_UV_FAIL-}" ]; then exit 3; fi
if [ -z "${FAKE_UV_NO_VENV-}" ]; then
    mkdir -p .venv/bin && : > .venv/bin/python && chmod +x .venv/bin/python
fi
"""


def product_layout(root: Path, services: tuple[str, ...], script: Path = SCRIPT) -> Path:
    (root / "scripts").mkdir(parents=True)
    shutil.copy(script, root / "scripts/prepare-env.sh")
    (root / "pyproject.toml").write_text("[project]\nname = 'product'\n")
    for service in services:
        (root / "services" / service).mkdir(parents=True)
        (root / "services" / service / "pyproject.toml").write_text(
            f"[project]\nname = '{service}'\n"
        )
    return root


def prepare(root: Path, *arguments: str, **environment: str) -> tuple[int, str, list[list[str]]]:
    tools = root.parent / "tools"
    tools.mkdir(exist_ok=True)
    uv = tools / "uv"
    uv.write_text(FAKE_UV)
    uv.chmod(uv.stat().st_mode | stat.S_IEXEC)
    log = root.parent / "uv.log"
    log.write_text("")
    result = subprocess.run(
        ["sh", "scripts/prepare-env.sh", *arguments],
        cwd=root,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{tools}:{os.environ['PATH']}",
            "FAKE_UV_LOG": str(log),
            **environment,
        },
    )
    calls = [line.split("|") for line in log.read_text().splitlines()]
    return result.returncode, result.stdout + result.stderr, calls


def test_prepares_each_named_environment_from_its_frozen_lock_and_repeats(tmp_path: Path) -> None:
    root = product_layout(tmp_path / "product", ("backend", "tg_bot"))
    expected = [
        ["product", "sync --frozen", "unset", "unset"],
        ["backend", "sync --frozen", "unset", "unset"],
        ["tg_bot", "sync --frozen", "unset", "unset"],
    ]
    for _ in range(2):
        code, output, calls = prepare(
            root,
            "root",
            "backend",
            "tg_bot",
            UV_PROJECT_ENVIRONMENT="/shared/venv",
            VIRTUAL_ENV="/host/venv",
        )
        assert code == 0, output
        assert calls == expected
    for path in (root, root / "services/backend", root / "services/tg_bot"):
        assert (path / ".venv/bin/python").is_file()


def test_runtime_environments_exclude_dev_dependencies_and_tooling(tmp_path: Path) -> None:
    root = product_layout(tmp_path / "product", ("backend", "tg_bot"))
    code, output, calls = prepare(root, "--runtime", "tg_bot")
    assert code == 0, output
    assert calls == [["tg_bot", "sync --frozen --no-dev --no-install-project", "unset", "unset"]]
    code, output, calls = prepare(root, "--runtime", "backend", "root")
    assert code == 2 and "root tooling has no runtime environment" in output
    assert calls == []


@pytest.mark.parametrize(
    ("arguments", "code", "message"),
    [
        (("root", "tg_bot"), 1, "tg_bot environment is required, but services/tg_bot/"),
        (("root", "../backend"), 2, "unknown environment '../backend'"),
        (("root", "-x"), 2, "unknown environment '-x'"),
        ((), 2, "usage: sh scripts/prepare-env.sh"),
        (("--runtime",), 2, "usage: sh scripts/prepare-env.sh"),
    ],
)
def test_refuses_unknown_or_missing_environments_before_syncing(
    tmp_path: Path, arguments: tuple[str, ...], code: int, message: str
) -> None:
    root = product_layout(tmp_path / "product", ("backend",))
    returned, output, calls = prepare(root, *arguments)
    assert returned == code and message in output
    assert calls == []


def test_a_failed_service_sync_stops_with_failure(tmp_path: Path) -> None:
    root = product_layout(tmp_path / "product", ("backend", "tg_bot"))
    code, output, calls = prepare(root, "root", "backend", "tg_bot", FAKE_UV_FAIL="backend")
    assert code == 1 and "backend environment sync failed" in output
    assert [call[0] for call in calls] == ["product", "backend"]
    assert not (root / "services/tg_bot/.venv").exists()


def test_a_sync_without_an_interpreter_is_not_success(tmp_path: Path) -> None:
    root = product_layout(tmp_path / "product", ("tg_bot",))
    code, output, _ = prepare(root, "tg_bot", FAKE_UV_NO_VENV="1")
    assert code == 1 and "tg_bot environment has no interpreter" in output


SHAPES = {
    "project_backend": ("backend",),
    "project_standalone": ("tg_bot",),
    "project_backend_tg_bot": ("backend", "tg_bot"),
}


@pytest.mark.parametrize("fixture", sorted(SHAPES))
def test_every_stage_prepares_through_the_one_command(
    request: pytest.FixtureRequest, fixture: str
) -> None:
    product: Path = request.getfixturevalue(fixture)
    services = SHAPES[fixture]
    assert (product / "scripts/prepare-env.sh").read_bytes() == SCRIPT.read_bytes()
    setup = subprocess.run(
        ["make", "-n", "setup"], cwd=product, capture_output=True, text=True, check=True
    ).stdout
    assert f"sh scripts/prepare-env.sh root {' '.join(services)}\n" in setup
    for path in [
        "Makefile",
        ".github/workflows/ci.yml",
        *(f"services/{service}/Dockerfile" for service in services),
    ]:
        assert "uv sync" not in (product / path).read_text(), path
    for service in services:
        dockerfile = (product / f"services/{service}/Dockerfile").read_text()
        dependencies, runtime = dockerfile.split("\nFROM base AS runtime\n", maxsplit=1)
        assert "COPY scripts/prepare-env.sh ./scripts/prepare-env.sh" in dependencies
        assert f"sh scripts/prepare-env.sh --runtime {service}\n" in dependencies
        assert "prepare-env" not in runtime and "/app/.venv" not in runtime
    if "backend" in services:
        dev = (product / "services/backend/Dockerfile").read_text().split("FROM base AS dev")[0]
        assert "sh scripts/prepare-env.sh root backend \\\n" in dev
        assert ("sh scripts/prepare-env.sh --runtime tg_bot" in dev) == ("tg_bot" in services)


@pytest.mark.parametrize("fixture", ["project_backend", "project_backend_tg_bot"])
def test_the_main_image_job_preparation_step_prepares_what_generation_reads(
    request: pytest.FixtureRequest, tmp_path: Path, fixture: str
) -> None:
    """Execute the rendered main-job step, not only read it, on the product's own script."""
    product: Path = request.getfixturevalue(fixture)
    services = SHAPES[fixture]
    workflow = yaml.safe_load((product / ".github/workflows/ci.yml").read_text())
    step = next(
        step
        for step in workflow["jobs"]["build-and-push"]["steps"]
        if step.get("name") == "Prepare generation environments"
    )
    root = product_layout(tmp_path / "product", services, product / "scripts/prepare-env.sh")
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / "uv").write_text(FAKE_UV)
    (tools / "uv").chmod(0o755)
    log = tmp_path / "uv.log"
    result = subprocess.run(
        ["bash", "-eo", "pipefail", "-c", step["run"]],
        cwd=root,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": f"{tools}:{os.environ['PATH']}", "FAKE_UV_LOG": str(log)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    synced = [line.split("|")[:2] for line in log.read_text().splitlines()]
    assert synced == [["product", "sync --frozen"], *([name, "sync --frozen"] for name in services)]
