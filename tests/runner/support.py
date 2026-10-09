"""Pure helpers of the runner proof (docs/RUNNER_PROOF.md), unit-tested in tests/unit.

No Docker, network or orchestrator import happens here, so the local test profile covers the
parts of the runner that decide what is executed and what counts as evidence.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
import json
import re
import secrets

#: The only workflow expression a product job step may use; everything else is refused.
SUPPORTED_EXPRESSION = re.compile(r"\$\{\{\s*github\.sha\s*\}\}")
EXPRESSION = re.compile(r"\$\{\{.*?\}\}", re.S)
KEY_ALPHABET = "abcdefghijklmnopqrstuvwxyz234567"
REDACTED = "<redacted>"
ENV_LINE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
#: The main job generation recipe of kit 644a0d25 (template/.github/workflows/ci.yml.jinja):
#: root and backend only, then generation. The regression replays it before the fixed path.
BASELINE_MAIN_GENERATION = (
    ("uv", "sync", "--frozen"),
    ("uv", "sync", "--project", "services/backend", "--frozen"),
    ("make", "generate-from-spec"),
)
BASELINE_FAILURE = "BindingEnvironmentError: tg_bot environment is not installed"


class WorkflowError(ValueError):
    """A product workflow step this runner cannot execute faithfully."""


@dataclass(frozen=True)
class Step:
    name: str
    run: str | None
    uses: str | None
    always: bool
    env: dict[str, str]


def job_steps(workflow: Mapping, job: str) -> list[Step]:
    """Steps of one job; only `if: always()` is accepted as a condition."""
    steps = []
    for raw in workflow["jobs"][job]["steps"]:
        condition = str(raw.get("if", "")).strip()
        if condition and condition not in ("always()", "${{ always() }}"):
            raise WorkflowError(f"{job}: unsupported step condition {condition!r}")
        steps.append(
            Step(
                name=str(raw.get("name") or raw.get("uses") or "step"),
                run=raw.get("run"),
                uses=raw.get("uses"),
                always=bool(condition),
                env={str(key): str(value) for key, value in (raw.get("env") or {}).items()},
            )
        )
    return steps


def workflow_env(workflow: Mapping, job: str) -> dict[str, str]:
    values = dict(workflow.get("env") or {})
    values.update(workflow["jobs"][job].get("env") or {})
    resolved = {str(key): str(value) for key, value in values.items()}
    for key, value in resolved.items():
        if EXPRESSION.search(value):
            raise WorkflowError(f"{job}: environment {key} uses an expression")
    return resolved


def is_secret_step(step: Step) -> bool:
    """Steps that read repository secrets (registry login): replaced by the isolated registry."""
    return step.run is not None and "secrets." in step.run


def render_run(step: Step, sha: str) -> str:
    """Substitute `${{ github.sha }}`; any other expression is refused, never guessed."""
    assert step.run is not None
    script = SUPPORTED_EXPRESSION.sub(sha, step.run)
    leftover = EXPRESSION.search(script)
    if leftover:
        raise WorkflowError(f"{step.name}: unsupported expression {leftover.group(0)}")
    return script


def render_env(step: Step, sha: str) -> dict[str, str]:
    rendered = {}
    for key, value in step.env.items():
        value = SUPPORTED_EXPRESSION.sub(sha, value)
        if EXPRESSION.search(value):
            raise WorkflowError(f"{step.name}: unsupported expression in {key}")
        rendered[key] = value
    return rendered


def build_matrix(workflow: Mapping, job: str = "build-and-push") -> list[dict[str, str]]:
    include = workflow["jobs"][job]["strategy"]["matrix"]["include"]
    return [
        {key: str(item[key]) for key in ("id", "context", "dockerfile", "image-suffix")}
        for item in include
    ]


def read_github_env(text: str) -> dict[str, str]:
    """Parse a GITHUB_ENV file: `KEY=value` lines and `KEY<<DELIMITER` blocks."""
    values: dict[str, str] = {}
    lines = iter(text.splitlines())
    for line in lines:
        if "<<" in line and "=" not in line.split("<<", 1)[0]:
            key, delimiter = line.split("<<", 1)
            block = []
            for item in lines:
                if item == delimiter:
                    break
                block.append(item)
            values[key] = "\n".join(block)
            continue
        match = ENV_LINE.match(line)
        if match:
            values[match.group(1)] = match.group(2)
        elif line.strip():
            raise WorkflowError(f"unsupported GITHUB_ENV line {line!r}")
    return values


def render_dotenv(example: str, overrides: Mapping[str, str]) -> str:
    """The product `.env.example` with values replaced in place and new keys appended."""
    lines = []
    remaining = dict(overrides)
    for line in example.splitlines():
        match = ENV_LINE.match(line)
        if match and match.group(1) in remaining:
            line = f"{match.group(1)}={remaining.pop(match.group(1))}"
        lines.append(line)
    lines.extend(f"{key}={value}" for key, value in remaining.items())
    return "\n".join(lines) + "\n"


def product_key() -> str:
    """A syntactically valid synthetic platform key: cps_<12 base32>_<43 base64url>."""
    key_id = "".join(secrets.choice(KEY_ALPHABET) for _ in range(12))
    secret = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode().rstrip("=")
    return f"cps_{key_id}_{secret}"


def key_id(key: str) -> str:
    return key.split("_")[1]


def redact(text: str, values: Iterable[str]) -> str:
    """Remove every known synthetic secret, longest first so prefixes cannot leak a suffix."""
    for value in sorted({item for item in values if len(item) >= 6}, key=len, reverse=True):
        text = text.replace(value, REDACTED)
    return text


def json_lines(text: str, prefix: str) -> list[dict]:
    """JSON records a fixture printed after `prefix`, in order; other log lines are ignored."""
    records = []
    for line in text.splitlines():
        _, found, payload = line.partition(prefix)
        if found:
            records.append(json.loads(payload))
    return records


def caddy_requests(text: str, path: str) -> list[int]:
    """Statuses of Caddy JSON access-log entries for one request path (query is stripped)."""
    statuses = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if (
            isinstance(entry, dict)
            and str(entry.get("logger", "")).startswith("http.log.access")
            and entry.get("request", {}).get("uri") == path
        ):
            statuses.append(int(entry["status"]))
    return statuses


def pushed_digest(repo_digests: Iterable[str], repository: str) -> str:
    """The registry digest of a pushed image, from `docker image inspect` RepoDigests."""
    found = [item for item in repo_digests if item.startswith(f"{repository}@sha256:")]
    if len(found) != 1:
        raise ValueError(f"expected one pushed digest for {repository}, got {found}")
    return found[0].split("@", 1)[1]
