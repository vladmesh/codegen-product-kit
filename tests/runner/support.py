"""Pure helpers of the runner proof (docs/RUNNER_PROOF.md), unit-tested in tests/unit.

No Docker, network or orchestrator import happens here, so the local test profile covers the
parts of the runner that decide what is executed and what counts as evidence.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable, Mapping
import copy
from dataclasses import asdict, dataclass
import hashlib
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
#: How the proof obtains the target package release (docs/RUNNER_PROOF.md, "Proof modes").
PROOF_MODES = ("candidate_release", "published_release")
#: Which catalog the planner and the executor read (docs/RUNNER_PROOF.md, "Catalog modes"), and
#: the only catalog modes each proof mode admits. Nothing falls back from one to another.
CATALOG_MODES = ("remote_head", "candidate_snapshot", "pending_fixture")
ADMITTED_CATALOG_MODES = {
    "published_release": ("remote_head", "candidate_snapshot"),
    "candidate_release": ("pending_fixture",),
}
PENDING_RELEASES = "packages/pending-releases.yaml"


@dataclass(frozen=True)
class PublishedRelease:
    """A package release tag as the kit's real remote published it; immutable once pinned."""

    tag: str
    tag_object: str
    target: str
    tree: str


#: Published releases a `published_release` proof may install. The runner reads the tag from
#: the real remote before planning and refuses any other object, target or package tree.
PUBLISHED_RELEASES = {
    ("tg-channels", "0.1.2"): PublishedRelease(
        tag="packages/tg-channels/v0.1.2",
        tag_object="4f5918dba1a3afc7ad9012715cb8f83c33a8e1bc",
        target="29f481f213544b0b7a5055451d5b4e55a6249b35",
        tree="676ee7061c6b08d17923f7c1fad3742798c9870f",
    ),
}


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
            and str(entry.get("request", {}).get("uri", "")).split("?", 1)[0] == path
        ):
            statuses.append(int(entry["status"]))
    return statuses


def pushed_digest(repo_digests: Iterable[str], repository: str) -> str:
    """The registry digest of a pushed image, from `docker image inspect` RepoDigests."""
    found = [item for item in repo_digests if item.startswith(f"{repository}@sha256:")]
    if len(found) != 1:
        raise ValueError(f"expected one pushed digest for {repository}, got {found}")
    return found[0].split("@", 1)[1]


# -- candidate release transport (proof_mode=candidate_release) ------------------------------


def pending_release(document: Mapping, package: str) -> dict:
    """The single pending release of `package` from packages/pending-releases.yaml."""
    if document.get("format_version") != 1:
        raise ValueError("pending releases: unsupported format_version")
    found = [item for item in document.get("releases") or [] if item.get("package") == package]
    if len(found) != 1:
        raise ValueError(f"pending releases: expected one {package} entry, found {len(found)}")
    release = dict(found[0])
    entry = release.get("catalog_entry") or {}
    version = str(release.get("version", ""))
    if (
        set(entry) != {"version", "tag", "requires_core"}
        or str(entry["version"]) != version
        or entry["tag"] != f"packages/{package}/v{version}"
    ):
        raise ValueError(f"pending releases: {package} catalog_entry disagrees with its version")
    return release


def fixture_catalog(catalog: Mapping, release: Mapping) -> dict:
    """The published catalog plus the pending entry: the candidate release's catalog snapshot.

    Only the package's `versions` list grows; every published entry is kept unchanged.
    """
    document = copy.deepcopy(dict(catalog))
    package = next(
        (item for item in document.get("packages") or [] if item["name"] == release["package"]),
        None,
    )
    if package is None:
        raise ValueError(f"catalog has no package {release['package']}")
    if package["distribution"] != release["distribution"] or package["path"] != release["path"]:
        raise ValueError(f"pending {release['package']} names another distribution or path")
    if any(str(item["version"]) == str(release["version"]) for item in package["versions"]):
        raise ValueError(f"{release['package']} {release['version']} is already in the catalog")
    package["versions"].append(dict(release["catalog_entry"]))
    return document


def fixture_resource(path: str, refs: Iterable[str]) -> tuple[str, str] | None:
    """Split a raw-file URL path `/<ref>/<file>` of the catalog HTTP fixture.

    Refs contain slashes (`packages/tg-channels/v0.1.2`), so the longest served ref that
    prefixes the path wins; anything else is not served.
    """
    for ref in sorted(refs, key=len, reverse=True):
        prefix = f"/{ref}/"
        if path.startswith(prefix) and len(path) > len(prefix) and ".." not in path.split("/"):
            return ref, path.removeprefix(prefix)
    return None


# -- published release and catalog snapshot (proof_mode=published_release) ------------------


def check_modes(proof_mode: str, catalog_mode: str) -> None:
    """Refuse a catalog mode the proof mode does not admit; there is no default pairing."""
    if proof_mode not in ADMITTED_CATALOG_MODES:
        raise ValueError(f"unknown proof mode {proof_mode!r}")
    if catalog_mode not in ADMITTED_CATALOG_MODES[proof_mode]:
        raise ValueError(
            f"{proof_mode} admits catalog modes {ADMITTED_CATALOG_MODES[proof_mode]}, "
            f"not {catalog_mode!r}"
        )


def published_release(package: str, version: str) -> PublishedRelease:
    """The pinned published release; an unpinned version cannot be proven as published."""
    try:
        return PUBLISHED_RELEASES[(package, version)]
    except KeyError:
        raise ValueError(f"{package} {version} is not a pinned published release") from None


def ls_remote_refs(text: str) -> dict[str, str]:
    """`git ls-remote` output as {ref: object id}; a repeated or malformed line is refused."""
    refs: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-f]{40})\t(\S+)", line)
        if match is None or match.group(2) in refs:
            raise ValueError(f"unexpected ls-remote line {line!r}")
        refs[match.group(2)] = match.group(1)
    return refs


def verify_published_tag(
    pin: PublishedRelease, remote: Mapping[str, str], fetched: Mapping[str, str]
) -> dict[str, str]:
    """Bind the real remote's tag (ls-remote) and its fetched copy to the pinned release.

    ``remote`` is the ls-remote listing, which must carry the tag and its peeled commit;
    ``fetched`` is what the fetched copy resolves to: object type, tag object, target and tree.
    """
    ref = f"refs/tags/{pin.tag}"
    expected = {
        "tag": pin.tag,
        "type": "tag",
        "tag_object": pin.tag_object,
        "target": pin.target,
        "tree": pin.tree,
    }
    listed = {"tag_object": remote.get(ref), "target": remote.get(f"{ref}^{{}}")}
    if listed["tag_object"] is None:
        raise ValueError(f"the real remote has no tag {pin.tag}")
    if listed != {"tag_object": pin.tag_object, "target": pin.target}:
        raise ValueError(f"the real remote's {pin.tag} is {listed}, not the pinned release")
    found = {"tag": pin.tag, **{key: fetched.get(key) for key in expected if key != "tag"}}
    if found != expected:
        raise ValueError(f"the fetched {pin.tag} is {found}, not the pinned release")
    return expected


def verify_copied_tags(remote: Mapping[str, str], copied: Mapping[str, str]) -> None:
    """Every copied package tag is the real remote's object id; a missing one is refused."""
    problems = {
        name: (remote.get(f"refs/tags/{name}"), value)
        for name, value in copied.items()
        if remote.get(f"refs/tags/{name}") != value
    }
    if problems:
        raise ValueError(f"copied tags differ from the real remote (remote, copy): {problems}")


def catalog_package(catalog: Mapping, name: str) -> dict:
    """The package entry `name` of a catalog document."""
    found = [item for item in catalog.get("packages") or [] if item["name"] == name]
    if len(found) != 1:
        raise ValueError(f"catalog has {len(found)} packages named {name}")
    return dict(found[0])


def released_tags(catalog: Mapping) -> set[str]:
    """Every release tag a catalog document names, across packages, libraries and extensions."""
    return {
        item["tag"]
        for section in ("packages", "libraries", "extensions")
        for component in catalog.get(section) or []
        for item in component["versions"]
    }


def catalog_digest(text: str) -> str:
    """The catalog digest the orchestrator planner puts in a payload and the probe recomputes.

    Same formula as the planner's `installable` and `install_probe.py`: SHA-256 of the parsed
    catalog's sorted JSON, so a reformatted file with the same content has the same digest.
    """
    from framework.catalog import parse_catalog  # noqa: PLC0415  # the candidate tooling

    catalog = parse_catalog(text, "runner snapshot")
    return hashlib.sha256(
        json.dumps(
            asdict(catalog), sort_keys=True, default=lambda value: value.model_dump(mode="json")
        ).encode()
    ).hexdigest()


def verify_snapshot_reads(requests: Iterable[Mapping], served: Mapping[str, str]) -> None:
    """Every HTTP fixture read succeeded, and every read of a snapshot file got its bytes.

    ``served`` maps a request path (``/HEAD/packages/catalog.yaml``) to the SHA-256 the
    snapshot holds; each of them must have been read at least once.
    """
    seen = set()
    for request in requests:
        if request["status"] != 200:
            raise ValueError(f"catalog fixture answered {request['status']} for {request['path']}")
        path = request["path"].split("?", 1)[0]
        for suffix, digest in served.items():
            if path.endswith(suffix):
                if request["sha256"] != digest:
                    raise ValueError(f"{path} was served other bytes than the snapshot")
                seen.add(suffix)
    if missing := sorted(set(served) - seen):
        raise ValueError(f"the planner never read {missing} from the snapshot")
