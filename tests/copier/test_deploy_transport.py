"""Run the rendered deployment transport and real, nonconnecting native parsers."""

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tomllib

import pytest
import yaml

from tests.copier.conftest import BASE_DATA, VENV_COPIER, _git, template_source

RELEASE_SHA = "f23460c62fa3508858c0552557b2860af09f2656"
WORKFLOW = Path(".github/workflows/deploy.yml")
HOSTS = ["192.0.2.42", "2001:db8::42", "::ffff:192.0.2.42"]
SECRETS = {"DEPLOY_USER": "transport-user", "PROJECT_NAME": "transport-project"}
SSH_OPTIONS = [
    "-o",
    "StrictHostKeyChecking=no",
    "-o",
    "ConnectTimeout=30",
    "-i",
    "~/.ssh/deploy_key",
]
TARGET = "/opt/services/transport-project/infra"


def _workflow(project: Path) -> dict:
    return yaml.safe_load((project / WORKFLOW).read_text())


def _copy_script(project: Path, secrets: dict[str, str]) -> str:
    step = next(
        step
        for step in _workflow(project)["jobs"]["deploy"]["steps"]
        if step.get("name") == "Copy compose files to server"
    )
    return re.sub(r"\$\{\{ secrets\.(\w+) \}\}", lambda match: secrets[match[1]], step["run"])


def _capture_program(path: Path) -> None:
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "log = pathlib.Path(os.environ['TRANSPORT_LOG'])\n"
        "command = pathlib.Path(sys.argv[0]).name\n"
        "previous = log.read_text().splitlines() if log.exists() else []\n"
        "count = sum(json.loads(line)[0] == command for line in previous)\n"
        "with log.open('a') as stream:\n"
        "    stream.write(json.dumps([command, *sys.argv[1:]]) + '\\n')\n"
        "failures = int(os.environ.get(command.upper() + '_FAILURES', '0'))\n"
        "sys.exit(1 if failures < 0 or count < failures else 0)\n"
    )
    path.chmod(0o755)


def _records(log: Path) -> list[list[str]]:
    return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []


def _run_copy(
    project: Path,
    tmp_path: Path,
    host: str,
    *,
    ssh_failures: int = 0,
    scp_failures: int = 0,
    missing: str | None = None,
) -> tuple[subprocess.CompletedProcess[str], list[list[str]]]:
    commands = tmp_path / "bin"
    commands.mkdir()
    for command in ("ssh", "scp", "sleep"):
        _capture_program(commands / command)
    log = tmp_path / "commands.jsonl"
    secrets = {**SECRETS, "DEPLOY_HOST": host}
    if missing:
        secrets[missing] = ""
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", _copy_script(project, secrets)],
        cwd=project,
        env={
            **os.environ,
            "PATH": f"{commands}:{os.environ['PATH']}",
            "TRANSPORT_LOG": str(log),
            "SSH_FAILURES": str(ssh_failures),
            "SCP_FAILURES": str(scp_failures),
        },
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result, _records(log)


@pytest.fixture(params=["backend", "standalone", "backend_tg_bot"])
def deploy_project(request: pytest.FixtureRequest) -> Path:
    return request.getfixturevalue(f"project_{request.param}")


@pytest.mark.parametrize("host", HOSTS)
@pytest.mark.parametrize(
    ("ssh_failures", "scp_failures", "expected", "success"),
    [
        (0, 0, ["ssh", "scp"], True),
        (1, 0, ["ssh", "sleep", "ssh", "scp"], True),
        (0, 1, ["ssh", "scp", "sleep", "ssh", "scp"], True),
        (0, 2, ["ssh", "scp", "sleep", "ssh", "scp", "sleep", "ssh", "scp"], True),
        (-1, 0, ["ssh", "sleep", "ssh", "sleep", "ssh"], False),
        (0, -1, ["ssh", "scp", "sleep", "ssh", "scp", "sleep", "ssh", "scp"], False),
    ],
)
def test_rendered_copy_commands_and_retry_bound(
    deploy_project: Path,
    tmp_path: Path,
    host: str,
    ssh_failures: int,
    scp_failures: int,
    expected: list[str],
    success: bool,
) -> None:
    result, records = _run_copy(
        deploy_project, tmp_path, host, ssh_failures=ssh_failures, scp_failures=scp_failures
    )
    assert result.returncode == (0 if success else 1), result.stdout + result.stderr
    assert [record[0] for record in records] == expected
    scp_host = f"[{host}]" if ":" in host else host
    for record in records:
        if record[0] == "ssh":
            assert record[1:] == [*SSH_OPTIONS, f"transport-user@{host}", f"mkdir -p {TARGET}"]
        elif record[0] == "scp":
            assert record[1:] == [
                *SSH_OPTIONS,
                "infra/compose.base.yml",
                "infra/compose.prod.yml",
                f"transport-user@{scp_host}:{TARGET}/",
            ]
    sleeps = [record[1:] for record in records if record[0] == "sleep"]
    assert sleeps == [[str(wait)] for wait in (15, 30)[: len(sleeps)]]
    assert ("SCP succeeded" if success else "SCP failed after 3 attempts") in result.stdout


@pytest.mark.parametrize("missing", ["DEPLOY_HOST", "DEPLOY_USER", "PROJECT_NAME"])
def test_missing_copy_configuration_fails_before_transport(
    deploy_project: Path, tmp_path: Path, missing: str
) -> None:
    result, records = _run_copy(deploy_project, tmp_path, HOSTS[0], missing=missing)
    assert result.returncode != 0
    assert f"{missing} is not set" in result.stderr
    assert records == []


def test_bracketed_secret_is_refused_without_transport(
    deploy_project: Path, tmp_path: Path
) -> None:
    result, records = _run_copy(deploy_project, tmp_path, "[2001:db8::42]")
    assert result.returncode != 0
    assert "DEPLOY_HOST must be a raw host without brackets" in result.stderr
    assert records == []


def _native_scp_host(tmp_path: Path, project: Path, arguments: list[str]) -> tuple[str, str]:
    transport = tmp_path / "native-transport"
    _capture_program(transport)
    log = tmp_path / "native.jsonl"
    result = subprocess.run(
        ["scp", "-F", "/dev/null", "-S", str(transport), *arguments],
        cwd=project,
        env={**os.environ, "TRANSPORT_LOG": str(log), "NATIVE-TRANSPORT_FAILURES": "-1"},
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode != 0  # The injected SSH transport exits without connecting.
    records = _records(log)
    assert len(records) == 1, result.stderr
    arguments = records[0][1:]
    assert arguments[-1] == "sftp"
    return arguments[-2], arguments[arguments.index("-l") + 1]


@pytest.mark.parametrize("host", HOSTS)
def test_native_parsers_receive_the_rendered_hosts(
    deploy_project: Path, tmp_path: Path, host: str
) -> None:
    assert shutil.which("ssh") and shutil.which("scp"), "OpenSSH is required for transport proof"
    result, records = _run_copy(deploy_project, tmp_path, host)
    assert result.returncode == 0, result.stderr
    ssh_arguments = next(record[1:] for record in records if record[0] == "ssh")
    config = subprocess.run(
        ["ssh", "-G", "-F", "/dev/null", *ssh_arguments],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert config.returncode == 0, config.stderr
    values = dict(line.split(" ", 1) for line in config.stdout.splitlines())
    assert values["hostname"] == host
    assert values["user"] == "transport-user"
    scp_arguments = next(record[1:] for record in records if record[0] == "scp")
    assert _native_scp_host(tmp_path, deploy_project, scp_arguments) == (host, "transport-user")


def test_runner_and_external_action_contract(deploy_project: Path) -> None:
    job = _workflow(deploy_project)["jobs"]["deploy"]
    assert job["runs-on"] == "ubuntu-24.04"
    action = next(step for step in job["steps"] if step.get("name") == "Deploy via SSH")
    assert action["uses"] == "appleboy/ssh-action@v1.0.0"
    assert action["with"]["host"] == "${{ secrets.DEPLOY_HOST }}"
    assert action["with"]["username"] == "${{ secrets.DEPLOY_USER }}"
    assert action["with"]["key"] == "${{ secrets.DEPLOY_SSH_KEY }}"


def test_transport_upgrade_proof_is_in_ci_with_released_history() -> None:
    workflow = yaml.safe_load(Path(".github/workflows/test-template.yml").read_text())
    steps = workflow["jobs"]["test-pytest"]["steps"]
    checkout = next(step for step in steps if step.get("name") == "Checkout")
    assert checkout["uses"] == "actions/checkout@v4"
    assert checkout["with"]["fetch-depth"] == 0
    assert any(step.get("run") == "make test-copier" for step in steps)
    assert any(step.get("run") == "make test-copier-slow" for step in steps)


def _assert_tooling_revision(project: Path, sha: str) -> None:
    metadata = tomllib.loads((project / "pyproject.toml").read_text())["project"]
    assert metadata["version"] == "0.1.0"
    assert metadata["dependencies"] == [
        f"codegen-kit-tooling @ git+https://github.com/vladmesh/codegen-product-kit.git@{sha}"
    ]
    lock = tomllib.loads((project / "uv.lock").read_text())
    tooling = next(
        package for package in lock["package"] if package["name"] == "codegen-kit-tooling"
    )
    assert tooling["version"] == "0.1.0"
    assert tooling["source"]["git"] == (
        f"https://github.com/vladmesh/codegen-product-kit.git?rev={sha}#{sha}"
    )


@pytest.fixture
def released_product(tmp_path: Path) -> Path:
    source = template_source()
    assert _git("rev-parse", "0.6.3^{}", cwd=source).stdout.strip() == RELEASE_SHA
    project = tmp_path / "released"
    result = subprocess.run(
        [
            str(VENV_COPIER),
            "copy",
            str(source),
            str(project),
            "--defaults",
            "--trust",
            "--vcs-ref=0.6.3",
            *(f"--data={key}={value}" for key, value in BASE_DATA.items()),
            "--data=modules=backend,tg_bot",
        ],
        env=_fixture_env(source),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert yaml.safe_load((project / ".copier-answers.yml").read_text())["_commit"] == "0.6.3"
    _assert_tooling_revision(project, RELEASE_SHA)
    return project


def test_exact_063_native_ipv6_failure(released_product: Path, tmp_path: Path) -> None:
    result, records = _run_copy(released_product, tmp_path, "2001:db8::42")
    assert result.returncode == 0  # Command capture alone cannot detect native misparsing.
    scp_arguments = next(record[1:] for record in records if record[0] == "scp")
    host, user = _native_scp_host(tmp_path, released_product, scp_arguments)
    assert host == "2001"
    assert host != "2001:db8::42"
    assert user == "transport-user"


def _commit_product(project: Path) -> None:
    _git("init", "--quiet", cwd=project)
    _git("add", "-A", cwd=project)
    _git("add", "--force", ".env", cwd=project)
    _git(
        "-c",
        "user.name=Upgrade tests",
        "-c",
        "user.email=tests@example.com",
        "commit",
        "--quiet",
        "-m",
        "Owned synthetic product baseline",
        cwd=project,
    )


def _update_product(project: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(VENV_COPIER), "update", "--defaults", "--trust", "--vcs-ref=HEAD", "--conflict=rej"],
        cwd=project,
        env=_fixture_env(template_source()),
        capture_output=True,
        text=True,
        timeout=120,
    )


def _fixture_env(source: Path) -> dict[str, str]:
    return {
        **os.environ,
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": f"url.file://{source}/.insteadOf",
        "GIT_CONFIG_VALUE_0": "https://github.com/vladmesh/codegen-product-kit.git",
    }


@pytest.mark.parametrize("customization", ["none", "retained", "conflicting"])
def test_real_063_update_and_owned_data_readback(
    released_product: Path, tmp_path: Path, customization: str
) -> None:
    project = released_product
    owned = [
        Path(".env"),
        Path(".env.example"),
        Path("shared/spec/models.yaml"),
        Path("shared/spec/events.yaml"),
        Path("services/backend/src/app/owned.py"),
        Path("services/backend/src/app/models/user.py"),
        Path("services/backend/src/controllers/owned.py"),
        Path("services/backend/src/controllers/users.py"),
        Path("services/tg_bot/src/app/owned.py"),
    ]
    for relative in owned:
        path = project / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((path.read_text() if path.exists() else "") + "\n# synthetic owned data\n")
    (project / ".env").write_text("SYNTHETIC_OWNED_VALUE=upgrade-canary\n")
    before = {relative: (project / relative).read_bytes() for relative in owned}
    old_workflow = _workflow(project)
    workflow_path = project / WORKFLOW
    if customization == "retained":
        workflow_path.write_text(
            workflow_path.read_text().replace("name: Deploy", "name: Owned deploy", 1)
        )
    elif customization == "conflicting":
        workflow_path.write_text(workflow_path.read_text().replace("ubuntu-latest", "self-hosted"))
    _commit_product(project)
    result = _update_product(project)
    assert result.returncode == 0, result.stdout + result.stderr
    assert {relative: (project / relative).read_bytes() for relative in owned} == before
    answers = yaml.safe_load((project / ".copier-answers.yml").read_text())
    source = template_source()
    assert (
        _git("rev-parse", f"{answers['_commit']}^{{commit}}", cwd=source).stdout
        == _git("rev-parse", "HEAD", cwd=source).stdout
    )
    assert answers["modules"] == "backend,tg_bot"
    _assert_tooling_revision(project, _git("rev-parse", "HEAD", cwd=source).stdout.strip())
    updated = _workflow(project)
    for index in (0, 1, 3):
        assert (
            updated["jobs"]["deploy"]["steps"][index]
            == old_workflow["jobs"]["deploy"]["steps"][index]
        )
    if customization == "conflicting":
        rejection = project / f"{WORKFLOW}.rej"
        assert rejection.exists(), result.stdout + result.stderr
        assert "+    runs-on: self-hosted" in rejection.read_text()
        assert f"{WORKFLOW}.rej" in _git("status", "--porcelain", cwd=project).stdout
        assert updated["jobs"]["deploy"]["runs-on"] == "ubuntu-24.04"
        assert "runs-on: self-hosted" in _git("show", f"HEAD:{WORKFLOW}", cwd=project).stdout
    else:
        assert not list(project.rglob("*.rej"))
        assert updated["jobs"]["deploy"]["runs-on"] == "ubuntu-24.04"
        assert updated["name"] == ("Owned deploy" if customization == "retained" else "Deploy")
    result, records = _run_copy(project, tmp_path, "2001:db8::42")
    assert result.returncode == 0, result.stderr
    scp_arguments = next(record[1:] for record in records if record[0] == "scp")
    assert _native_scp_host(tmp_path, project, scp_arguments) == ("2001:db8::42", "transport-user")
