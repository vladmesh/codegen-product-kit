"""Run the rendered deployment transport through fake remote boundaries and native parsers."""

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
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


def _step(project: Path, name: str) -> dict:
    return next(
        step for step in _workflow(project)["jobs"]["deploy"]["steps"] if step.get("name") == name
    )


def _copy_script(project: Path, secrets: dict[str, str]) -> str:
    step = _step(project, "Copy compose files to server")
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


DEPLOY_VALUES = {
    "DEPLOY_USER": "transport-user",
    "DEPLOY_HOST": HOSTS[0],
    "PROJECT_NAME": "transport-project",
    "DEPLOY_PORT": "8080",
    "DOTENV_B64": base64.b64encode(b"BACKEND_PORT=8080\n").decode(),
    "REGISTRY_URL": "registry.example.test",
    "REGISTRY_USER": "registry-user",
    "REGISTRY_PASSWORD": "registry-password",
}
SECRET_NAMES = {
    "DEPLOY_USER": "DEPLOY_USER",
    "DEPLOY_HOST": "DEPLOY_HOST",
    "PROJECT_NAME": "PROJECT_NAME",
    "DEPLOY_PORT": "DEPLOY_PORT",
    "DOTENV_B64": "DOTENV",
    "REGISTRY_URL": "REGISTRY_URL",
    "REGISTRY_USER": "REGISTRY_USER",
    "REGISTRY_PASSWORD": "REGISTRY_PASSWORD",
}
COMPOSE = [
    "compose",
    "--env-file",
    "../.env",
    "-f",
    "compose.base.yml",
    "-f",
    "compose.prod.yml",
]
RUNNING = [{"Name": "app-backend-1", "State": "running"}, {"Name": "app-db-1", "State": "running"}]


def _remote_host_program(path: Path) -> None:
    """Stand in for sshd: run the remote command on stdin with only the remote fake environment."""
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, subprocess, sys\n"
        "with pathlib.Path(os.environ['TRANSPORT_LOG']).open('a') as stream:\n"
        "    stream.write(json.dumps(['ssh', *sys.argv[1:]]) + '\\n')\n"
        "script = sys.stdin.read()\n"
        "if os.environ.get('FAKE_SSH_EXIT'):\n"
        "    sys.exit(int(os.environ['FAKE_SSH_EXIT']))\n"
        "remote = os.environ['REMOTE_ROOT']\n"
        "env = {key: value for key, value in os.environ.items() if key.startswith('FAKE_')}\n"
        "env.update(PATH=os.environ['REMOTE_PATH'], TRANSPORT_LOG=os.environ['TRANSPORT_LOG'])\n"
        "result = subprocess.run(\n"
        f"    ['bash', '-c', ' '.join(sys.argv[{len(SSH_OPTIONS) + 2}:])],\n"
        "    input=script.replace('/opt/services/', remote + '/'),\n"
        "    cwd=remote, env=env, text=True,\n"
        ")\n"
        "sys.exit(result.returncode)\n"
    )
    path.chmod(0o755)


def _docker_program(path: Path) -> None:
    """Record docker argv and login stdin; fail or answer as the FAKE_ variables say."""
    path.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, sys\n"
        "log = pathlib.Path(os.environ['TRANSPORT_LOG'])\n"
        "def action_of(args):\n"
        "    return args[7] if args[0] == 'compose' else args[0]\n"
        "args = sys.argv[1:]\n"
        "action = action_of(args)\n"
        "previous = [json.loads(line) for line in log.read_text().splitlines()]\n"
        "count = sum(r[0] == 'docker' and action_of(r[1:]) == action for r in previous)\n"
        "record = ['docker', *args]\n"
        "if action == 'login':\n"
        "    record.append({'stdin': sys.stdin.read()})\n"
        "with log.open('a') as stream:\n"
        "    stream.write(json.dumps(record) + '\\n')\n"
        "failures = int(os.environ.get(f'FAKE_{action.upper()}_FAILURES', '0'))\n"
        "if failures < 0 or count < failures:\n"
        "    sys.exit(1)\n"
        "if action == 'ps':\n"
        "    states = json.loads(os.environ['FAKE_PS_STATES'])\n"
        "    if '-q' in args:\n"
        "        print('\\n'.join(state['Name'] for state in states))\n"
        "    else:\n"
        "        print(os.environ.get('FAKE_PS_OUTPUT', '\\n'.join(map(json.dumps, states))))\n"
        "    sys.exit(int(os.environ.get('FAKE_PS_EXIT', '0')))\n"
        "if action == 'inspect':\n"
        "    if 'RestartCount' in args[2]:\n"
        "        print(json.loads(os.environ.get('FAKE_RESTARTS', '{}')).get(args[3], 0))\n"
        "    else:\n"
        "        print('/' + args[3])\n"
    )
    path.chmod(0o755)


def _run_deploy(
    project: Path,
    tmp_path: Path,
    values: dict[str, str] | None = None,
    **fake: str,
) -> tuple[subprocess.CompletedProcess[str], list[list], Path]:
    runner = tmp_path / "runner-bin"
    remote = tmp_path / "remote-bin"
    root = tmp_path / "remote-root"
    for directory in (runner, remote, root):
        directory.mkdir(exist_ok=True)
    _remote_host_program(runner / "ssh")
    _docker_program(remote / "docker")
    for command in ("ufw", "sleep"):
        _capture_program(remote / command)
    log = tmp_path / "commands.jsonl"
    log.touch()
    step = _step(project, "Deploy via SSH")
    assert step["env"] == {
        name: f"${{{{ secrets.{SECRET_NAMES[name]} }}}}" for name in SECRET_NAMES
    }
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", step["run"]],
        cwd=project,
        env={
            "PATH": f"{runner}:{os.environ['PATH']}",
            "HOME": str(tmp_path),
            "TRANSPORT_LOG": str(log),
            "REMOTE_ROOT": str(root),
            "REMOTE_PATH": f"{remote}:{os.environ['PATH']}",
            "FAKE_PS_STATES": json.dumps(RUNNING),
            **{f"FAKE_{key}": value for key, value in fake.items()},
            **(DEPLOY_VALUES if values is None else values),
        },
        capture_output=True,
        text=True,
        timeout=20,
    )
    return result, _records(log), root


def _docker_actions(records: list[list]) -> list[list]:
    """Drop the compose prefix after checking it names the project's env file and both layers."""
    docker = [record[1:] for record in records if record[0] == "docker"]
    assert all(args[:7] == COMPOSE for args in docker if args[0] == "compose")
    return [args[7:] if args[0] == "compose" else args for args in docker]


def _expected_ready_actions(*, first_deploy: bool) -> list[list]:
    login = ["login", "registry.example.test", "-u", "registry-user", "--password-stdin"]
    inspect = [
        ["inspect", "--format", field, state["Name"]]
        for state in RUNNING
        for field in ("{{.RestartCount}}", "{{.Name}}")
    ]
    return [
        [*login, {"stdin": "registry-password\n"}],
        *([["down", "-v", "--remove-orphans"]] if first_deploy else []),
        ["pull"],
        ["up", "--wait", "--wait-timeout", "120", "--remove-orphans"],
        ["ps", "--format", "json"],
        ["ps", "-q"],
        *inspect,
    ]


@pytest.mark.parametrize("host", HOSTS)
def test_native_deploy_reaches_ready_services_without_fixed_sleep(
    deploy_project: Path, tmp_path: Path, host: str
) -> None:
    result, records, root = _run_deploy(
        deploy_project, tmp_path, {**DEPLOY_VALUES, "DEPLOY_HOST": host}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    ssh = [record[1:] for record in records if record[0] == "ssh"]
    assert ssh == [[*SSH_OPTIONS, f"transport-user@{host}", "bash", "-s"]]
    config = subprocess.run(
        ["ssh", "-G", "-F", "/dev/null", *ssh[0][:-2]],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert config.returncode == 0, config.stderr
    values = dict(line.split(" ", 1) for line in config.stdout.splitlines())
    assert (values["hostname"], values["user"]) == (host, "transport-user")
    assert values["stricthostkeychecking"] == "false"
    assert values["connecttimeout"] == "30"
    assert [record[1:] for record in records if record[0] == "ufw"] == [["allow", "8080/tcp"]]
    assert _docker_actions(records) == _expected_ready_actions(first_deploy=True)
    assert [record for record in records if record[0] == "sleep"] == []
    assert (root / "transport-project/.env").read_bytes() == b"BACKEND_PORT=8080\n"
    assert "All 2 containers running" in result.stdout
    assert result.stdout.rstrip().endswith("No crash loops detected")


def test_redeploy_keeps_installed_services_and_data(deploy_project: Path, tmp_path: Path) -> None:
    project_dir = tmp_path / "remote-root/transport-project"
    (project_dir / "infra").mkdir(parents=True)
    (project_dir / ".env").write_text("PREVIOUS=1\n")
    (project_dir / "data.db").write_text("installed data\n")
    result, records, _ = _run_deploy(deploy_project, tmp_path)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _docker_actions(records) == _expected_ready_actions(first_deploy=False)
    assert (project_dir / ".env.bak").read_text() == "PREVIOUS=1\n"
    assert (project_dir / "data.db").read_text() == "installed data\n"


def test_deploy_values_reach_the_remote_shell_as_literal_data(
    deploy_project: Path, tmp_path: Path
) -> None:
    canary = "sp ace 'single' \"double\" $HOME ${HOME} $(touch pwned) `touch pwned`"
    dotenv = f"CANARY='{canary}'\nSECOND=\"line two\"\n".encode()
    values = {
        **DEPLOY_VALUES,
        "PROJECT_NAME": f"project {canary}",
        "DEPLOY_PORT": f"8080 {canary}",
        "DOTENV_B64": base64.b64encode(dotenv).decode(),
        "REGISTRY_URL": f"registry {canary}",
        "REGISTRY_USER": f"user {canary}",
        "REGISTRY_PASSWORD": f"password {canary}\nsecond line",
    }
    result, records, root = _run_deploy(deploy_project, tmp_path, values)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (root / values["PROJECT_NAME"] / ".env").read_bytes() == dotenv
    assert [record[1:] for record in records if record[0] == "ufw"] == [
        ["allow", f"{values['DEPLOY_PORT']}/tcp"]
    ]
    assert _docker_actions(records)[0] == [
        "login",
        values["REGISTRY_URL"],
        "-u",
        values["REGISTRY_USER"],
        "--password-stdin",
        {"stdin": values["REGISTRY_PASSWORD"] + "\n"},
    ]
    assert not list(tmp_path.rglob("pwned"))
    assert not (deploy_project / "pwned").exists()
    ssh = next(record for record in records if record[0] == "ssh")
    output = result.stdout + result.stderr
    for name in ("DOTENV_B64", "REGISTRY_PASSWORD", "REGISTRY_USER", "DEPLOY_PORT"):
        assert all(values[name] not in argument for argument in ssh)
        assert values[name] not in output
    assert dotenv.decode() not in output


@pytest.mark.parametrize("missing", list(SECRET_NAMES))
def test_missing_deploy_value_fails_before_transport(
    deploy_project: Path, tmp_path: Path, missing: str
) -> None:
    result, records, _ = _run_deploy(deploy_project, tmp_path, {**DEPLOY_VALUES, missing: ""})
    assert result.returncode != 0
    assert f"{SECRET_NAMES[missing]} is not set" in result.stderr
    assert records == []


def test_ssh_failure_fails_the_deploy(deploy_project: Path, tmp_path: Path) -> None:
    result, records, _ = _run_deploy(deploy_project, tmp_path, SSH_EXIT="255")
    assert result.returncode == 255
    assert [record[0] for record in records] == ["ssh"]


@pytest.mark.parametrize(
    ("fake", "message", "last_action"),
    [
        ({"UP_FAILURES": "-1"}, "FATAL: services failed or were not ready within 120s", "up"),
        ({"PS_OUTPUT": ""}, "No containers found", "ps"),
        ({"PS_OUTPUT": "{not json"}, "JSONDecodeError", "ps"),
        (
            {"PS_STATES": json.dumps([*RUNNING[:1], {"Name": "app-bot-1", "State": "restarting"}])},
            "FAILED: ['app-bot-1']",
            "ps",
        ),
        ({"PS_EXIT": "1"}, "All 2 containers running", "ps"),
        ({"RESTARTS": json.dumps({"app-db-1": 2})}, "app-db-1 restarted 2 times", "inspect"),
        ({"DOTENV": "\n"}, "decoded .env is empty", None),
        ({"DOTENV": "!corrupt!"}, "invalid input", None),
    ],
)
def test_remote_failure_fails_the_deploy(
    deploy_project: Path,
    tmp_path: Path,
    fake: dict[str, str],
    message: str,
    last_action: str | None,
) -> None:
    fake = dict(fake)
    values = {**DEPLOY_VALUES, "DOTENV_B64": fake.pop("DOTENV", DEPLOY_VALUES["DOTENV_B64"])}
    result, records, _ = _run_deploy(deploy_project, tmp_path, values, **fake)
    output = result.stdout + result.stderr
    assert result.returncode == 1, output
    assert message in output
    assert "No crash loops detected" not in output
    actions = _docker_actions(records)
    assert (actions[-1][0] if actions else None) == last_action
    assert [record for record in records if record[0] == "sleep"] == []


@pytest.mark.parametrize(
    ("fake", "success", "sleeps"),
    [
        ({"LOGIN_FAILURES": "1"}, True, ["5"]),
        ({"LOGIN_FAILURES": "-1"}, False, ["5", "15"]),
        ({"PULL_FAILURES": "2"}, True, ["10", "20"]),
        ({"PULL_FAILURES": "-1"}, False, ["10", "20"]),
    ],
)
def test_registry_retries_are_bounded(
    deploy_project: Path, tmp_path: Path, fake: dict[str, str], success: bool, sleeps: list[str]
) -> None:
    result, records, _ = _run_deploy(deploy_project, tmp_path, **fake)
    assert result.returncode == (0 if success else 1), result.stdout + result.stderr
    assert [record[1] for record in records if record[0] == "sleep"] == sleeps
    actions = [action[0] for action in _docker_actions(records)]
    assert actions.count("login" if "LOGIN_FAILURES" in fake else "pull") == len(sleeps) + 1
    assert ("up" in actions) == success


_WAIT_SERVICES = {
    "healthy": {"test": ["CMD", "redis-cli", "ping"], "interval": "1s", "retries": 30},
    "unhealthy": {"test": ["CMD-SHELL", "exit 1"], "interval": "1s", "retries": 1},
    "starting": {"test": ["CMD-SHELL", "exit 1"], "interval": "1s", "start_period": "300s"},
}


@pytest.mark.slow
@pytest.mark.parametrize(
    ("service", "wait_timeout", "ready"),
    [("healthy", "120", True), ("unhealthy", "120", False), ("starting", "5", False)],
)
def test_real_compose_wait_bounds_readiness(
    tmp_path: Path, service: str, wait_timeout: str, ready: bool
) -> None:
    if not shutil.which("docker"):
        pytest.skip("Docker is required for the real Compose readiness proof")
    compose = tmp_path / "compose.yml"
    compose.write_text(
        yaml.safe_dump(
            {
                "services": {
                    service: {
                        "image": "redis:7-alpine",
                        "healthcheck": _WAIT_SERVICES[service],
                    }
                }
            }
        )
    )
    command = [
        "docker",
        "compose",
        "-p",
        f"deploy-wait-{tmp_path.name}".lower(),
        "-f",
        str(compose),
    ]
    try:
        started = time.monotonic()
        result = subprocess.run(
            [*command, "up", "--wait", "--wait-timeout", wait_timeout, "--remove-orphans"],
            capture_output=True,
            text=True,
            timeout=180,
        )
        elapsed = time.monotonic() - started
    finally:
        subprocess.run(
            [*command, "down", "-v", "--remove-orphans"],
            capture_output=True,
            timeout=120,
            check=False,
        )
    assert (result.returncode == 0) == ready, result.stdout + result.stderr
    assert elapsed < 60


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
    released_product: Path, project_backend_tg_bot: Path, tmp_path: Path, customization: str
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
    steps = updated["jobs"]["deploy"]["steps"]
    assert steps[:2] == old_workflow["jobs"]["deploy"]["steps"][:2]
    assert steps[2:] == _workflow(project_backend_tg_bot)["jobs"]["deploy"]["steps"][2:]
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
    (tmp_path / "deploy").mkdir()
    result, records, _ = _run_deploy(
        project, tmp_path / "deploy", {**DEPLOY_VALUES, "DEPLOY_HOST": "2001:db8::42"}
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert next(record for record in records if record[0] == "ssh")[-3:] == [
        "transport-user@2001:db8::42",
        "bash",
        "-s",
    ]
