"""CI-only runner proof of a fresh backend,tg_bot product with tg-channels (docs/RUNNER_PROOF.md).

From the exact kit candidate: an unchanged Copier product is installed by the orchestrator's
real `scaffolder.install.run_install`, passes its own CI job, replays the cold main
image-generation failure of the old recipe and then the fixed main path, builds its runtime
images with its own Dockerfiles, pushes them to an isolated registry and runs them by digest
against the pinned platform's real auth and Caddy. A fixture reader and a fake Telegram Bot API
are the only replaced parts. Evidence: <output-dir>/runner-proof.json plus redacted logs.

Run with the orchestrator's environment, whose codegen-kit-tooling is the kit candidate:

    orchestrator/.venv/bin/python kit/tests/runner/fresh_product.py --kit-dir kit \
        --kit-sha SHA --orchestrator-dir orchestrator --orchestrator-sha SHA \
        --platform-dir platform --platform-sha SHA --output-dir OUT \
        --proof-mode published_release --catalog-mode remote_head --package-version 0.1.2 \
        --packages tg-channels

`--proof-mode published_release` installs the published tg-channels tag, read from the real
remote at its pinned object first. `--catalog-mode remote_head` plans and installs from the real
default branch's catalog with no fixture; `candidate_snapshot` serves the candidate commit's own
committed catalog as HEAD of an isolated snapshot holding the real remote's package tags by
object id (a prospective catalog, for a pull request). `--proof-mode candidate_release` with
`--catalog-mode pending_fixture` installs a pending release of the candidate through an isolated
fixture repository. Both modes are always explicit and never fall back.
`--packages reminders,tg-channels` installs the published reminders first and proves both
modules side by side.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import support
import yaml

TEMPLATE_SOURCE = "gh:vladmesh/codegen-product-kit"
KIT_REPOSITORY = "https://github.com/vladmesh/codegen-product-kit.git"
#: The raw-file base the orchestrator's planner reads the published catalog from.
PUBLISHED_RAW_SOURCE = "https://raw.githubusercontent.com/vladmesh/codegen-product-kit"
#: The URL path the catalog HTTP fixture serves the isolated repository under.
FIXTURE_RAW_PATH = "/vladmesh/codegen-product-kit"
PACKAGE = "tg-channels"
CATALOG_FILE = "packages/catalog.yaml"
#: How long the main proof waits for raw.githubusercontent.com to serve the catalog bytes git
#: reads at the real HEAD (its cache lags a push by minutes); the source never changes.
RAW_CATALOG_TIMEOUT = 900
#: Other real modules a coexistence leg may install before the target package.
COMPANIONS = ("reminders",)
#: Product-scope values the scenario sets through `/settings/set` for each binding setting.
BINDING_SETTING_VALUES = {"language": "en", "timezone": "UTC"}
#: What the core registry and the channels binding answer in each product language.
LANGUAGE_REPLIES = {
    "en": {
        "unknown": "I don't understand this message. Available commands: ",
        "channel": "Enter a public channel username.",
    },
    "ru": {
        "unknown": "Не понимаю это сообщение. Доступные команды: ",
        "channel": "Введите имя публичного канала.",
    },
}
PROJECT_NAME = "runner-proof"
REPOSITORY_ID = "runner-product"
#: The executor requires an owned GitHub URL; `remote get-url origin` answers it while the
#: actual origin is a local bare repository (the controlled transport, see docs).
GIT_URL = "https://github.com/ci/runner-product"
GIT_TOKEN = "synthetic-runner-git-token"  # noqa: S105  # never reaches a remote
STORY = "runner-proof"
PYTHON_IMAGE = "python:3.12-slim-bookworm"
REGISTRY_IMAGE = "registry:2.8.3"
PLATFORM_IMAGE = "ghcr.io/vladmesh/codegen-platform-services/auth"
FIXTURE_CHANNEL = "runner_fixture"
TELEGRAM_USER = 424242001
NOT_CONFIGURED = "Service is not configured."
CHANNEL_ADDED = f"Channel added: @{FIXTURE_CHANNEL}"
DELIVERY_TIMEOUT = 300
SELECT_PAYLOAD = """
import asyncio, sys
from pathlib import Path
from src.kit_catalog import KitCatalogReader, catalog_url
from src.catalog_install import plan_install_payload
async def main():
    source = sys.argv[3]
    snapshot = await KitCatalogReader(catalog_url(source, 'HEAD'), component_source=source).read()
    payload = plan_install_payload(snapshot, sys.argv[2], '3.12.0')
    Path(sys.argv[1]).write_text(payload.model_dump_json())
asyncio.run(main())
"""
#: Read with the product's own tooling: what the installed modules declare side by side.
COEXISTENCE = """
import json
from pathlib import Path
from framework.binding_product import binding_files, binding_settings
from framework.host_contract import evaluate
from framework.spec.loader import load_specs
root = Path.cwd()
specs = load_specs(root)
bindings = binding_files(root)
host = evaluate(root)
print(json.dumps({
    'packages': {item.name: item.manifest.version for item in specs.packages},
    'publishes': {item.name: sorted(item.manifest.events.publishes) for item in specs.packages},
    'events': sorted(item.name for item in specs.events.events),
    'job_timers': specs.job_timers,
    'job_owners': specs.job_schema_sources,
    'settings': specs.settings_schema_sources,
    'registry': [[item.command, item.owner] for item in host.commands],
    'host_violations': [item.describe() for item in host.violations],
    'bindings': {
        item.package: {
            'file': name,
            'commands': sorted(command.command for command in item.commands),
            'settings': sorted(binding_settings(item)),
        }
        for name, item in sorted(bindings.items())
    },
}, sort_keys=True))
"""
_SNAPSHOT_TRANSPORT = (
    f"reached through a process-scoped HOME .gitconfig insteadOf {KIT_REPOSITORY} during the "
    "install executor only, and a loopback HTTP fixture serving its raw files to the planner"
)
RELEASE_TRANSPORTS = {
    "remote_head": (
        "none: the real catalog at the kit's default branch and the published tags, "
        "no fixture and no URL rewrite"
    ),
    "candidate_snapshot": (
        "an isolated snapshot repository whose HEAD is the exact candidate with its committed "
        "catalog unchanged (prospective) and whose package tags are fetched from the real "
        f"remote by object id, {_SNAPSHOT_TRANSPORT}"
    ),
    "pending_fixture": (
        "an isolated fixture repository (exact candidate, local intended tag, fixture catalog "
        f"commit), {_SNAPSHOT_TRANSPORT}"
    ),
}
TOOLING_PROVENANCE = (
    "from importlib.metadata import distribution; "
    "print(distribution('codegen-kit-tooling').read_text('direct_url.json'))"
)


class ProofError(RuntimeError):
    pass


class CatalogFixture:
    """Loopback raw-file server over an isolated catalog snapshot repository.

    Used by the `candidate_snapshot` and `pending_fixture` catalog modes.

    It answers `/<ref>/<path>` like raw.githubusercontent.com does for the kit, from the same
    repository the install executor's git fetches reach, so the planner's KitCatalogReader and
    the executor's probe read identical bytes. Every request is recorded with its body digest.
    """

    def __init__(self, repository: Path, refs: dict[str, str]) -> None:
        self.repository = repository
        self.refs = refs
        self.requests: list[dict] = []
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                status, body = fixture.serve(self.path)
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    @property
    def source(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}{FIXTURE_RAW_PATH}"

    def serve(self, path: str) -> tuple[int, bytes]:
        status, body = 404, b""
        found = None
        if path.startswith(f"{FIXTURE_RAW_PATH}/"):
            found = support.fixture_resource(path.removeprefix(FIXTURE_RAW_PATH), self.refs)
        if found is not None:
            ref, name = found
            shown = subprocess.run(  # noqa: S603
                ["git", "--git-dir", str(self.repository), "show", f"{self.refs[ref]}:{name}"],  # noqa: S607
                capture_output=True,
                check=False,
            )
            if shown.returncode == 0:
                status, body = 200, shown.stdout
        self.requests.append(
            {
                "path": path,
                "status": status,
                "sha256": hashlib.sha256(body).hexdigest() if status == 200 else None,
            }
        )
        return status, body

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def http(method: str, url: str, body: object = None, headers=None) -> tuple[int, Any]:
    data = None if body is None else json.dumps(body).encode()
    prepared = Request(url, data=data, method=method, headers=dict(headers or {}))  # noqa: S310
    if data is not None:
        prepared.add_header("Content-Type", "application/json")
    try:
        with urlopen(prepared, timeout=15) as response:  # noqa: S310  # runner loopback only
            raw = response.read()
            return response.status, json.loads(raw) if raw else None
    except HTTPError as error:
        raw = error.read()
        try:
            return error.code, json.loads(raw)
        except ValueError:
            return error.code, raw.decode(errors="replace")


def wait_until(what: str, probe: Callable[[], Any], timeout: float, interval: float = 2.0) -> Any:
    deadline = time.monotonic() + timeout
    while True:
        try:
            found = probe()
        except (OSError, ValueError, subprocess.SubprocessError):
            found = None
        if found:
            return found
        if time.monotonic() > deadline:
            raise ProofError(f"timed out after {timeout:.0f}s waiting for {what}")
        time.sleep(interval)


class Runner:  # noqa: PLR0904  # one proof, one ordered set of stages sharing its evidence
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.kit = args.kit_dir.resolve()
        self.orchestrator = args.orchestrator_dir.resolve()
        self.platform = args.platform_dir.resolve()
        self.output = args.output_dir.resolve()
        self.logs = self.output / "logs"
        self.logs.mkdir(parents=True, exist_ok=True)
        # Work, secrets and the platform checkout stay outside the uploaded output directory.
        self.work = Path(tempfile.mkdtemp(prefix="runner-proof-work-"))
        self.prefix = f"rp{secrets.token_hex(4)}"
        self.secrets: set[str] = {GIT_TOKEN}
        self.cleanups: list[tuple[str, list[str], dict[str, str] | None]] = []
        self.counter = 0
        self.sha = args.kit_sha
        self.mode = args.proof_mode
        self.catalog_mode = args.catalog_mode
        self.packages = args.packages
        #: Process-scoped HOME of the install executor when a snapshot repository is served.
        self.release_home: Path | None = None
        #: Package tags as the real remote lists them ({name: tag object}), published_release.
        self.remote_tags: dict[str, str] = {}
        self.catalog_source = PUBLISHED_RAW_SOURCE
        self.catalog_fixture: CatalogFixture | None = None
        self.evidence: dict[str, Any] = {
            "schema": "codegen-product-kit/runner-proof/2",
            "status": "running",
            "started_at": datetime.now(UTC).isoformat(),
            "proof_mode": self.mode,
            "catalog_mode": self.catalog_mode,
            "pinned": {
                "kit": args.kit_sha,
                "orchestrator": args.orchestrator_sha,
                "platform": args.platform_sha,
            },
            "matrix": {
                "packages": list(self.packages),
                "target": {"package": PACKAGE, "expected_version": args.package_version},
            },
            "fixtures": {
                "git_transport": (
                    "origin is a local bare repository; only the executor's "
                    f"`git remote get-url origin` is answered with {GIT_URL}"
                ),
                "fence": "a recording callback; every executor checkpoint is retained",
                "reader": "tests/runner/fixtures/reader.py replaces only tg-reader",
                "telegram": "tests/runner/fixtures/telegram_api.py replaces api.telegram.org",
                "registry": f"{REGISTRY_IMAGE} on the runner loopback, removed afterwards",
                "release_transport": RELEASE_TRANSPORTS[self.catalog_mode],
            },
            "resources": [],
            "commands": [],
        }

    # -- process plumbing -------------------------------------------------------------------

    def redact(self, text: str) -> str:
        return support.redact(text, self.secrets)

    def where(self, path: Path) -> str:
        for name, root in (
            ("work", self.work),
            ("kit", self.kit),
            ("orchestrator", self.orchestrator),
            ("platform", self.platform),
        ):
            if path == root or root in path.parents:
                return f"{name}/{path.relative_to(root)}".rstrip("/.")
        return str(path)

    def clean_env(self, **extra: str) -> dict[str, str]:
        keep = (
            "HOME",
            "PATH",
            "LANG",
            "LC_ALL",
            "SSL_CERT_FILE",
            "SSL_CERT_DIR",
            "DOCKER_HOST",
            "DOCKER_CONFIG",
            "TMPDIR",
            "UV_CACHE_DIR",
        )
        env = {key: os.environ[key] for key in keep if os.environ.get(key)}
        env.update(extra)
        return env

    def run(  # noqa: PLR0913
        self,
        argv: list[str],
        *,
        cwd: Path,
        label: str,
        env: dict[str, str] | None = None,
        check: bool = True,
        timeout: int = 1800,
    ) -> subprocess.CompletedProcess[str]:
        self.counter += 1
        started = time.monotonic()
        try:
            result = subprocess.run(
                argv,
                cwd=cwd,
                env=env if env is not None else self.clean_env(),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            result = subprocess.CompletedProcess(
                argv, 124, str(error.stdout or ""), f"{error.stderr or ''}\ntimeout {timeout}s"
            )
        log = self.logs / f"{self.counter:03d}-{slug(label)}.log"
        log.write_text(self.redact(f"$ {shlex.join(argv)}\n{result.stdout}{result.stderr}"))
        self.evidence["commands"].append(
            {
                "label": label,
                "argv": [self.redact(item) for item in argv],
                "cwd": self.where(cwd),
                "exit_code": result.returncode,
                "seconds": round(time.monotonic() - started, 1),
                "log": str(log.relative_to(self.output)),
            }
        )
        if check and result.returncode != 0:
            raise ProofError(f"{label} exited {result.returncode}; see {log.name}")
        return result

    def git(self, *args: str, cwd: Path, label: str) -> str:
        argv = ["git", "-c", "core.hooksPath=/dev/null", *args]
        return self.run(argv, cwd=cwd, label=label).stdout.strip()

    def cleanup_later(self, label: str, argv: list[str], env: dict[str, str] | None) -> None:
        self.cleanups.insert(0, (label, argv, env))
        self.evidence["resources"].append({"label": label, "cleanup": argv})

    # -- stages -----------------------------------------------------------------------------

    def revisions(self) -> None:
        pinned = self.evidence["pinned"]
        for name, path in (
            ("kit", self.kit),
            ("orchestrator", self.orchestrator),
            ("platform", self.platform),
        ):
            head = self.git("rev-parse", "HEAD", cwd=path, label=f"{name} checkout head")
            if head != pinned[name]:
                raise ProofError(f"{name} checkout is {head}, expected pinned {pinned[name]}")
        provenance = json.loads(
            self.run(
                [sys.executable, "-I", "-c", TOOLING_PROVENANCE],
                cwd=self.work,
                label="planner tooling provenance",
            ).stdout
        )
        if provenance["url"] != KIT_REPOSITORY or provenance["vcs_info"]["commit_id"] != self.sha:
            raise ProofError("the orchestrator planner must run the kit candidate's tooling")
        self.evidence["revisions"] = {
            "kit": self.sha,
            "orchestrator": pinned["orchestrator"],
            "platform": pinned["platform"],
            "planner_tooling": provenance,
        }

    def scaffold(self, install: Any) -> Path:
        base = self.work / "workspaces"
        base.mkdir()
        product = base / REPOSITORY_ID
        copier = Path(sys.executable).parent / "copier"
        self.run(
            [
                str(copier),
                "copy",
                "--trust",
                "--defaults",
                f"--vcs-ref={self.sha}",
                "--data",
                f"project_name={PROJECT_NAME}",
                "--data",
                "modules=backend,tg_bot",
                TEMPLATE_SOURCE,
                str(product),
            ],
            cwd=self.work,
            label="copier copy fresh backend,tg_bot product",
        )
        answers = yaml.safe_load((product / ".copier-answers.yml").read_text())
        resolved = self.git(
            "rev-parse", f"{answers['_commit']}^{{commit}}", cwd=self.kit, label="template commit"
        )
        dependencies = (product / "pyproject.toml").read_text()
        if resolved != self.sha or f"codegen-product-kit.git@{self.sha}" not in dependencies:
            raise ProofError("Copier answers or tooling requirement do not name the candidate")
        self.evidence["template"] = {
            "source": answers["_src_path"],
            "commit": answers["_commit"],
            "resolved_sha": resolved,
            "modules": answers["modules"],
            "tooling_requirement": f"codegen-kit-tooling @ git+{KIT_REPOSITORY}@{self.sha}",
        }
        product_env = install.product_environment(product)
        self.git("init", "-q", "-b", "main", cwd=product, label="product git init")
        self.run(["make", "setup"], cwd=product, env=product_env, label="product make setup")
        self.git("add", "-A", cwd=product, label="product baseline add")
        self.git(
            "-c",
            "user.name=Runner proof",
            "-c",
            "user.email=runner-proof@example.com",
            "commit",
            "-qm",
            "Fresh product baseline",
            cwd=product,
            label="product baseline commit",
        )
        remote = self.work / "remote.git"
        self.git("init", "--bare", "-q", "-b", "main", str(remote), cwd=self.work, label="remote")
        self.git("remote", "add", "origin", str(remote), cwd=product, label="product origin")
        self.git("push", "-q", "origin", "main", cwd=product, label="product baseline push")
        return product

    def candidate_release(self) -> None:
        """Prepare the documented prepublication transport (docs/RUNNER_PROOF.md).

        An isolated throwaway repository receives the exact candidate commit and the kit's
        published package tags by object id, a fixture catalog commit on top of the candidate
        appending only the pending entry, and the intended package tag, locally, at the
        candidate. Nothing is pushed anywhere else and no executor, probe or kit code changes.
        """
        pending = (self.kit / support.PENDING_RELEASES).read_bytes()
        release = support.pending_release(yaml.safe_load(pending), PACKAGE)
        version = str(release["version"])
        if version != self.args.package_version:
            raise ProofError(
                f"the pending {PACKAGE} release is {version}, not {self.args.package_version}"
            )
        tag = release["catalog_entry"]["tag"]
        published = yaml.safe_load((self.kit / "packages/catalog.yaml").read_text())
        snapshot = support.fixture_catalog(published, release)
        root = self.work / "release-fixture"
        root.mkdir()
        bare = root / "codegen-product-kit.git"
        self.git("init", "--bare", "-q", str(bare), cwd=root, label="release fixture repository")
        self.git(
            "push",
            "-q",
            str(bare),
            f"{self.sha}:refs/heads/candidate",
            "refs/tags/packages/*:refs/tags/packages/*",
            cwd=self.kit,
            label="fixture: exact candidate and published package tags",
        )
        checkout = root / "catalog"
        self.git(
            "clone",
            "-q",
            "--branch",
            "candidate",
            bare.as_uri(),
            str(checkout),
            cwd=root,
            label="fixture catalog checkout",
        )
        (checkout / "packages/catalog.yaml").write_text(yaml.safe_dump(snapshot, sort_keys=False))
        identity = ["-c", "user.name=Runner proof", "-c", "user.email=runner-proof@example.com"]
        self.git(
            *identity,
            "commit",
            "-q",
            "-am",
            f"Fixture catalog snapshot: pending {PACKAGE} {version}, not published",
            cwd=checkout,
            label="fixture catalog commit",
        )
        self.git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=checkout, label="fixture main")
        fixture = ["--git-dir", str(bare)]
        self.git(
            *fixture, "symbolic-ref", "HEAD", "refs/heads/main", cwd=root, label="fixture HEAD"
        )
        self.git(
            *fixture,
            *identity,
            "tag",
            "--annotate",
            tag,
            "-m",
            f"Local fixture tag of the unpublished {PACKAGE} {version} candidate {self.sha}",
            self.sha,
            cwd=root,
            label="fixture intended package tag (local only)",
        )

        def rev(expression: str) -> str:
            return self.git(*fixture, "rev-parse", expression, cwd=root, label="fixture rev-parse")

        tags = self.git(
            *fixture,
            "for-each-ref",
            "--format=%(refname:strip=2)",
            "refs/tags",
            cwd=root,
            label="fixture tags",
        ).split()
        released = support.released_tags(published)
        if missing := sorted(released - set(tags)):
            raise ProofError(f"the kit checkout lacks published package tags {missing}")
        if rev("refs/heads/main^") != self.sha:
            raise ProofError("the fixture catalog commit is not a child of the candidate")
        catalog_bytes = (checkout / "packages/catalog.yaml").read_bytes()
        self.evidence["release"] = {
            "mode": self.mode,
            "published": False,
            "note": "the intended tag exists only in a throwaway fixture repository",
            "package": PACKAGE,
            "version": version,
            "intended_tag": tag,
            "requires_core": release["catalog_entry"]["requires_core"],
            "pending_metadata": support.PENDING_RELEASES,
            "pending_metadata_sha256": hashlib.sha256(pending).hexdigest(),
            "published_catalog_lists_version": False,
            "source_sha": self.sha,
            "package_tree": rev(f"{self.sha}:{release['path']}"),
            "fixture_catalog_commit": rev("refs/heads/main"),
            "fixture_catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "tag_type": self.git(
                *fixture, "cat-file", "-t", f"refs/tags/{tag}", cwd=root, label="fixture tag type"
            ),
            "tag_object": rev(f"refs/tags/{tag}"),
            "tag_target": rev(f"refs/tags/{tag}^{{commit}}"),
            "published_tags": {name: rev(f"refs/tags/{name}") for name in sorted(released)},
        }
        if self.evidence["release"]["tag_target"] != self.sha:
            raise ProofError("the fixture tag does not point at the exact candidate")
        self.evidence["catalog"] = {
            "mode": self.catalog_mode,
            "prospective": True,
            "note": "the candidate's catalog plus the pending entry, in a throwaway fixture",
            "ref": "HEAD",
            "commit": rev("refs/heads/main"),
            "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "catalog_digest": support.catalog_digest(catalog_bytes.decode()),
        }
        self.serve_snapshot(root, bare, tags)

    def serve_snapshot(self, root: Path, bare: Path, tags: list[str]) -> None:
        """Make the snapshot repository the kit source of the planner and of the executor.

        The planner reads raw files from a loopback HTTP fixture over the repository; the
        executor's git fetches of the kit repository (catalog at HEAD, package tags) reach it
        through a process-scoped HOME .gitconfig insteadOf, set only while `run_install` runs.
        URLs stay unchanged and nothing else (Copier, the platform checkout) sees the rewrite.
        """
        home = root / "home"
        home.mkdir()
        real_home = Path(os.environ["HOME"])
        for name in (".cache", ".local"):
            if (real_home / name).is_dir():
                (home / name).symlink_to(real_home / name)
        (home / ".gitconfig").write_text(
            f'[url "{bare.as_uri()}"]\n\tinsteadOf = {KIT_REPOSITORY}\n'
        )
        self.release_home = home
        self.catalog_fixture = CatalogFixture(
            bare, {"HEAD": "refs/heads/main"} | {name: f"refs/tags/{name}" for name in tags}
        )
        self.catalog_fixture.start()
        self.catalog_source = self.catalog_fixture.source

    def published_tags(self) -> Path:
        """Read the published target release and every package tag from the real remote.

        A bare repository under the work directory fetches `refs/tags/packages/*` from GitHub;
        every copied tag must be the object id `ls-remote` lists, and the target tag must be
        the pinned annotated tag object, peeled commit and package tree. Nothing is created.
        """
        pin = support.published_release(PACKAGE, self.args.package_version)
        root = self.work / "published"
        root.mkdir()
        bare = root / "codegen-product-kit.git"
        self.git("init", "--bare", "-q", str(bare), cwd=root, label="published tags repository")
        remote = {
            ref: value
            for ref, value in support.ls_remote_refs(
                self.git("ls-remote", "--tags", KIT_REPOSITORY, cwd=root, label="real remote tags")
            ).items()
            if ref.startswith("refs/tags/packages/")
        }
        published = ["--git-dir", str(bare)]
        self.git(
            *published,
            "fetch",
            "-q",
            "--no-tags",
            KIT_REPOSITORY,
            "+refs/tags/packages/*:refs/tags/packages/*",
            cwd=root,
            label="fetch package tags from the real remote",
        )

        def rev(expression: str) -> str:
            return self.git(
                *published, "rev-parse", expression, cwd=root, label="published rev-parse"
            )

        names = self.git(
            *published,
            "for-each-ref",
            "--format=%(refname:strip=2)",
            "refs/tags",
            cwd=root,
            label="copied package tags",
        ).split()
        copied = {name: rev(f"refs/tags/{name}") for name in names}
        listed = {ref.removeprefix("refs/tags/") for ref in remote if not ref.endswith("^{}")}
        if set(copied) != listed:
            raise ProofError(f"copied tags {sorted(copied)} are not the remote's {sorted(listed)}")
        try:
            support.verify_copied_tags(remote, copied)
            path = support.catalog_package(
                yaml.safe_load((self.kit / CATALOG_FILE).read_text()), PACKAGE
            )["path"]
            ref = f"refs/tags/{pin.tag}"
            fetched = {
                "type": self.git(
                    *published, "cat-file", "-t", ref, cwd=root, label="published tag type"
                )
                if pin.tag in copied
                else None,
                "tag_object": copied.get(pin.tag),
                "target": rev(f"{ref}^{{commit}}") if pin.tag in copied else None,
                "tree": rev(f"{ref}^{{commit}}:{path}") if pin.tag in copied else None,
            }
            verified = support.verify_published_tag(pin, remote, fetched)
        except ValueError as error:
            raise ProofError(str(error)) from error
        self.remote_tags = copied
        self.evidence["release"] = {
            "mode": self.mode,
            "published": True,
            "package": PACKAGE,
            "version": self.args.package_version,
            "tag": pin.tag,
            "remote": KIT_REPOSITORY,
            "published_tag": verified,
            "tag_object": pin.tag_object,
            "tag_target": pin.target,
            "package_tree": pin.tree,
            "remote_package_tags": copied,
        }
        return bare

    def candidate_snapshot(self, bare: Path) -> None:
        """Serve the candidate's own committed catalog as the snapshot HEAD (prospective).

        The real remote's default branch does not list a release before this change merges, so
        a pull request proves the catalog it would publish: the exact candidate commit becomes
        `refs/heads/main` of the published-tags repository, unchanged; no entry is added, no
        tag is created and the package source is the published tag's.
        """
        root = bare.parent
        snapshot = ["--git-dir", str(bare)]
        self.git(
            "push",
            "-q",
            str(bare),
            f"{self.sha}:refs/heads/main",
            cwd=self.kit,
            label="snapshot: exact candidate as the catalog HEAD",
        )
        self.git(*snapshot, "symbolic-ref", "HEAD", "refs/heads/main", cwd=root, label="HEAD")
        head = self.git(*snapshot, "rev-parse", "HEAD", cwd=root, label="snapshot HEAD")
        blob = self.git(
            *snapshot, "rev-parse", f"HEAD:{CATALOG_FILE}", cwd=root, label="snapshot catalog"
        )
        catalog_bytes = (self.kit / CATALOG_FILE).read_bytes()
        checkout_blob = self.git(
            "hash-object", CATALOG_FILE, cwd=self.kit, label="candidate catalog blob"
        )
        if head != self.sha or blob != checkout_blob:
            raise ProofError("the snapshot HEAD is not the candidate's committed catalog")
        catalog = yaml.safe_load(catalog_bytes)
        if missing := sorted(support.released_tags(catalog) - set(self.remote_tags)):
            raise ProofError(f"the candidate catalog names tags the real remote lacks: {missing}")
        self.evidence["catalog"] = {
            "mode": self.catalog_mode,
            "prospective": True,
            "note": (
                "the candidate's committed catalog as it would be published by merging; not "
                "the real default branch, which this run does not attest"
            ),
            "ref": "HEAD",
            "head": "refs/heads/main",
            "commit": head,
            "catalog_blob": blob,
            "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "catalog_digest": support.catalog_digest(catalog_bytes.decode()),
        }
        self.serve_snapshot(root, bare, sorted(self.remote_tags))

    def remote_head_catalog(self, label: str) -> dict[str, str]:
        """The catalog at the real remote's HEAD: commit, raw bytes digest and planner digest."""
        target = self.work / f"remote-head-{label}"
        self.git(
            "clone",
            "-q",
            "--depth=1",
            "--no-tags",
            KIT_REPOSITORY,
            str(target),
            cwd=self.work,
            label=f"real remote HEAD ({label})",
        )
        catalog_bytes = (target / CATALOG_FILE).read_bytes()
        return {
            "commit": self.git("rev-parse", "HEAD", cwd=target, label=f"real HEAD ({label})"),
            "catalog_sha256": hashlib.sha256(catalog_bytes).hexdigest(),
            "catalog_digest": support.catalog_digest(catalog_bytes.decode()),
            "read_at": datetime.now(UTC).isoformat(),
        }

    def remote_head(self) -> None:
        """Record the real HEAD catalog the planner and the probe must both read (main proof).

        No fixture and no rewrite: the planner reads raw.githubusercontent.com at `HEAD` and the
        executor's probe fetches `HEAD` itself. The runner waits only until the raw service
        serves the bytes git reads at HEAD; it never switches to another catalog.
        """
        head = self.remote_head_catalog("before")
        url = f"{PUBLISHED_RAW_SOURCE}/HEAD/{CATALOG_FILE}"

        def served() -> bool:
            prepared = Request(url, headers={"Cache-Control": "no-cache"})  # noqa: S310
            with urlopen(prepared, timeout=15) as response:  # noqa: S310  # the real raw source
                return hashlib.sha256(response.read()).hexdigest() == head["catalog_sha256"]

        wait_until("the raw catalog at HEAD to match git HEAD", served, RAW_CATALOG_TIMEOUT, 15)
        self.evidence["catalog"] = {
            "mode": self.catalog_mode,
            "prospective": False,
            "note": "the real catalog at the kit's default branch, fetched by this run",
            "ref": "HEAD",
            "source": KIT_REPOSITORY,
            "raw_source": url,
        } | head

    def catalog_agreement(self, payloads: dict[str, dict]) -> None:
        """Every planner payload, HTTP read and remote re-read reached the same catalog.

        The probes compare the catalog they fetch themselves with the payload digest (their
        `catalog_changed` refusal), so a passed preflight and readback is their agreement.
        """
        catalog = self.evidence["catalog"]
        digests = {name: payload["catalog_digest"] for name, payload in payloads.items()}
        catalog["planner_digests"] = digests
        if set(digests.values()) != {catalog["catalog_digest"]}:
            raise ProofError(f"planner catalog digests {digests} are not the snapshot's")
        catalog["probe_agreement"] = {
            name: {
                "preflight_returncode": (install.get("preflight") or {}).get("returncode"),
                "readback": bool(install.get("readback")),
            }
            for name, install in self.evidence["installs"].items()
        }
        if any(
            item != {"preflight_returncode": 0, "readback": True}
            for item in catalog["probe_agreement"].values()
        ):
            raise ProofError(f"a probe did not accept the catalog: {catalog['probe_agreement']}")
        if self.catalog_fixture is not None:
            requests = list(self.catalog_fixture.requests)
            catalog["http_requests"] = requests
            try:
                support.verify_snapshot_reads(
                    requests, {f"/HEAD/{CATALOG_FILE}": catalog["catalog_sha256"]}
                )
            except ValueError as error:
                raise ProofError(str(error)) from error
        if self.catalog_mode == "remote_head":
            after = self.remote_head_catalog("after")
            catalog["after_install"] = after
            if after["catalog_digest"] != catalog["catalog_digest"]:
                raise ProofError(f"the real HEAD catalog changed during the proof: {after}")

    def select_payload(self, name: str) -> dict:
        destination = self.work / f"install-payload-{name}.json"
        self.run(
            [sys.executable, "-c", SELECT_PAYLOAD, str(destination), name, self.catalog_source],
            cwd=self.orchestrator,
            env=self.clean_env(
                PYTHONPATH=f"{self.orchestrator / 'services/langgraph'}:{self.orchestrator}"
            ),
            label=f"orchestrator catalog selection {name}",
        )
        payload = json.loads(destination.read_text())
        if payload["tooling_commit"] != self.sha or payload["package"]["name"] != name:
            raise ProofError("typed install payload does not name the candidate tooling/package")
        version = payload["package"]["version"]
        if name == PACKAGE and version != self.args.package_version:
            raise ProofError(
                f"the planner selected {PACKAGE} {version}, expected "
                f"{self.args.package_version} ({self.mode}, {self.catalog_mode})"
            )
        if payload["catalog_digest"] != self.evidence["catalog"]["catalog_digest"]:
            raise ProofError(
                f"the planner read another catalog than the {self.catalog_mode} snapshot"
            )
        self.evidence.setdefault("install_payloads", {})[name] = payload
        if name == PACKAGE:
            self.evidence["install_payload"] = payload
        return payload

    async def install(
        self, product: Path, payload: dict, modules: SimpleNamespace, name: str
    ) -> Any:
        install = modules.install
        boundary: list[dict] = []
        probes: dict[str, Any] = {}
        fences: list[dict] = []
        actual = install._run_cmd

        async def transport(argv: list[str], **kwargs: Any) -> tuple[int, str, str]:
            operation = argv[3] if argv[0] == "git" and len(argv) > 3 else None
            credential = any(
                key.startswith("GIT_CONFIG_VALUE_") and value.startswith("Authorization: ")
                for key, value in kwargs["env"].items()
            )
            boundary.append(
                {
                    "executable": Path(argv[0]).name,
                    "git_operation": operation,
                    "credential_present": credential,
                }
            )
            if argv == ["git", "-c", "core.hooksPath=/dev/null", "remote", "get-url", "origin"]:
                return 0, GIT_URL + "\n", ""
            code, out, err = await actual(argv, **kwargs)
            if len(argv) > 3 and argv[2].endswith("install_probe.py"):
                probes[argv[3]] = {
                    "returncode": code,
                    "result": json.loads(out) if code == 0 else self.redact(err or out)[:4000],
                }
            return code, out, err

        async def fence(command: Any) -> None:
            fences.append(command.model_dump(mode="json"))

        message = modules.ScaffoldMessage(
            project_id="runner-proof-project",
            repository_id=REPOSITORY_ID,
            template_repo=TEMPLATE_SOURCE,
            template_ref=self.evidence["template"]["commit"],
            project_name=PROJECT_NAME,
            modules="backend,tg_bot",
            mode="install",
            task_id=f"runner-proof-install-{name}",
            story_id=STORY,
            operation_id=f"runner-proof-operation-{name}",
            cycle_started_at=datetime.now(UTC),
            install=payload,
        )
        installs = self.evidence.setdefault("installs", {})
        home = os.environ["HOME"]
        install._run_cmd = transport
        if self.release_home is not None:
            # The executor passes HOME to its children: their git reads the fixture insteadOf.
            os.environ["HOME"] = str(self.release_home)
        try:
            result = await install.run_install(
                message,
                SimpleNamespace(workspace_base_path=str(product.parent)),
                GIT_URL,
                GIT_TOKEN,
                fence,
            )
        except Exception as error:
            installs[name] = {
                "status": "failed",
                "stage": getattr(error, "stage", None),
                "error": self.redact(str(error))[:4000],
                "probes": probes,
                "fence": fences,
                "credential_boundary": boundary,
            }
            raise
        finally:
            install._run_cmd = actual
            os.environ["HOME"] = home
        installs[name] = {
            "status": "published",
            "package": name,
            "executor": "scaffolder.src.install.run_install",
            "head_sha": result.head_sha,
            "base_sha": result.base_sha,
            "stages": [
                {
                    "stage": item["stage"],
                    "argv": [self.redact(str(arg)) for arg in item["argv"]],
                    "returncode": item["returncode"],
                }
                for item in result.stages
            ],
            "preflight": probes.get("preflight"),
            "readback": result.evidence,
            "protected_sha256": result.protected_sha256,
            "fence": fences,
            "credential_boundary": boundary,
        }
        if name == PACKAGE:
            self.evidence["install"] = installs[name]
        return result

    def release_evidence(self, product: Path, payload: dict, readback: dict) -> None:
        """Bind the installed target release to its source: fixture tag or published tag."""
        source = next(item for item in readback["component_sources"] if item["name"] == PACKAGE)
        installed = readback["distributions"][PACKAGE]
        wheels = {
            name: hashlib.sha256((product / name).read_bytes()).hexdigest()
            for name in self.git(
                "ls-files", "services/*/packages/*.whl", cwd=product, label="installed wheels"
            ).split()
        }
        version = payload["package"]["version"]
        if not any(f"-{version}-" in name and "tg_channels" in name for name in wheels):
            raise ProofError(f"no committed {PACKAGE} {version} wheel: {sorted(wheels)}")
        if installed["version"] != version:
            raise ProofError(f"the backend has {PACKAGE} {installed['version']}, not {version}")
        release = self.evidence["release"]
        if self.mode == "published_release":
            # The probe fetched the release itself; it must be the tag read before planning, and
            # every other installed component the real remote's tag object.
            if payload["package"]["tag"] != release["tag"] or {
                key: source[key] for key in ("tag", "tag_object", "target", "tree")
            } != {
                "tag": release["tag"],
                "tag_object": release["tag_object"],
                "target": release["tag_target"],
                "tree": release["package_tree"],
            }:
                raise ProofError(f"the probe read another source than the published tag: {source}")
            components = [
                item for module in self.evidence["modules"].values() for item in module["sources"]
            ]
            if foreign := [
                item
                for item in components
                if self.remote_tags.get(item["tag"]) != item["tag_object"]
            ]:
                raise ProofError(f"installed sources are not the real remote's tags: {foreign}")
        if self.mode == "candidate_release" and (
            source["tag"] != release["intended_tag"]
            or source["tag_object"] != release["tag_object"]
            or source["target"] != self.sha
            or source["tree"] != release["package_tree"]
        ):
            raise ProofError(f"the probe read another source than the fixture release: {source}")
        release |= {
            "planner_catalog_digest": payload["catalog_digest"],
            "probe_source": source,
            "installed_distribution": installed,
            "wheels": wheels,
        }

    def coexistence(self, product: Path, install: Any) -> dict:
        """Settings, jobs, events and bindings of every installed module, read by the product."""
        result = self.run(
            [str(product / ".venv/bin/python"), "-I", "-c", COEXISTENCE],
            cwd=product,
            env=install.product_environment(product),
            label="installed modules side by side",
        )
        found = json.loads(result.stdout.strip().splitlines()[-1])
        owners = {found["job_owners"][name] for name in found["job_timers"]}
        commands = [item for value in found["bindings"].values() for item in value["commands"]]
        problems = []
        if set(found["packages"]) != set(self.packages):
            problems.append(f"installed {sorted(found['packages'])}")
        if found["packages"].get(PACKAGE) != self.args.package_version:
            problems.append(f"{PACKAGE} {found['packages'].get(PACKAGE)} is active")
        if owners != {f"package:{name}" for name in self.packages}:
            problems.append(f"timer owners {sorted(owners)}")
        for name, events in found["publishes"].items():
            if missing := sorted(set(events) - set(found["events"])):
                problems.append(f"{name} events {missing} are not generated")
        if set(found["bindings"]) != set(self.packages) or len(commands) != len(set(commands)):
            problems.append(f"bindings {found['bindings']}")
        for name, value in found["bindings"].items():
            if missing := sorted(set(value["settings"]) - set(found["settings"])):
                problems.append(f"{name} binding settings {missing} are not declared")
        problems.extend(support.host_problems(found))
        if problems:
            raise ProofError(f"installed modules do not coexist: {problems}")
        self.evidence["coexistence"] = found
        return found

    def clone(self, name: str) -> Path:
        target = self.work / "clones" / name
        target.parent.mkdir(exist_ok=True)
        self.git(
            "clone",
            "-q",
            "--branch",
            f"story/{STORY}",
            str(self.work / "remote.git"),
            str(target),
            cwd=self.work,
            label=f"clone {name}",
        )
        return target

    def check_toolchain(self, workflow: dict, job: str) -> None:
        """The runner job's setup actions provide exactly the product job's pinned versions."""
        for raw in workflow["jobs"][job]["steps"]:
            uses = str(raw.get("uses", ""))
            wanted = (raw.get("with") or {}).get(
                "version" if "setup-uv" in uses else "python-version"
            )
            if uses.startswith("astral-sh/setup-uv"):
                found = self.run(["uv", "--version"], cwd=self.work, label="uv version").stdout
                if found.split()[1] != wanted:
                    raise ProofError(f"{job} pins uv {wanted}, runner provides {found}")
            elif uses.startswith("actions/setup-python"):
                found = self.run(["python3", "--version"], cwd=self.work, label="python version")
                if not found.stdout.split()[1].startswith(f"{wanted}."):
                    raise ProofError(f"{job} pins Python {wanted}, runner provides {found.stdout}")

    def run_job(self, clone: Path, job: str, head: str) -> list[dict]:
        """Execute a product workflow job's `run` steps in order, `always()` steps last."""
        workflow = yaml.safe_load((clone / ".github/workflows/ci.yml").read_text())
        self.check_toolchain(workflow, job)
        github_env = self.work / f"github-env-{job}"
        github_env.write_text("")
        base = self.clean_env(CI="true", GITHUB_SHA=head, GITHUB_WORKSPACE=str(clone))
        base.update(support.workflow_env(workflow, job))
        exported: dict[str, str] = {}
        executed: list[dict] = []
        failed: str | None = None
        for step in support.job_steps(workflow, job):
            if step.run is None:
                executed.append({"name": step.name, "uses": step.uses, "provided_by": "runner job"})
                continue
            if support.is_secret_step(step):
                executed.append({"name": step.name, "skipped": "isolated registry, no secret"})
                continue
            if failed is not None and not step.always:
                executed.append({"name": step.name, "skipped": f"after failed {failed!r}"})
                continue
            script = support.render_run(step, head)
            env = base | exported | support.render_env(step, head) | {"GITHUB_ENV": str(github_env)}
            result = self.run(
                ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c", script],
                cwd=clone,
                env=env,
                label=f"{job}: {step.name}",
                check=False,
            )
            exported.update(support.read_github_env(github_env.read_text()))
            github_env.write_text("")
            executed.append({"name": step.name, "exit_code": result.returncode})
            if result.returncode != 0 and failed is None:
                failed = step.name
        if failed is not None:
            raise ProofError(f"product job {job} failed at step {failed!r}")
        return executed

    def product_ci(self, head: str) -> None:
        clone = self.clone("product-ci")
        steps = self.run_job(clone, "lint-and-test", head)
        contract = clone / f"artifacts/env-contract-{head}.json"
        if not contract.is_file():
            raise ProofError("product CI did not write its environment contract artifact")
        shutil.copy(contract, self.output / contract.name)
        self.evidence["product_ci"] = {"job": "lint-and-test", "head": head, "steps": steps}

    def cold_main_regression(self) -> None:
        """Replay the old main recipe: root and backend ready, tg_bot absent, binding installed."""
        clone = self.clone("main-baseline-recipe")
        shutil.copy(clone / ".env.example", clone / ".env")
        bindings = sorted(path.name for path in (clone / "services/tg_bot/bindings").glob("*.yaml"))
        for argv in support.BASELINE_MAIN_GENERATION[:-1]:
            self.run(list(argv), cwd=clone, label="baseline main: " + " ".join(argv))
        result = self.run(
            list(support.BASELINE_MAIN_GENERATION[-1]),
            cwd=clone,
            label="baseline main: make generate-from-spec (expected failure)",
            check=False,
        )
        output = result.stdout + result.stderr
        prepared = {
            name: (clone / path / ".venv/bin/python").is_file()
            for name, path in (
                ("root", Path()),
                ("backend", Path("services/backend")),
                ("tg_bot", Path("services/tg_bot")),
            )
        }
        if (
            result.returncode == 0
            or support.BASELINE_FAILURE not in output
            or prepared != {"root": True, "backend": True, "tg_bot": False}
            or f"{PACKAGE}.yaml" not in bindings
        ):
            raise ProofError("the cold main regression did not reproduce the original failure")
        self.evidence["cold_main_regression"] = {
            "recipe": [" ".join(argv) for argv in support.BASELINE_MAIN_GENERATION],
            "bindings": bindings,
            "prepared_environments": prepared,
            "exit_code": result.returncode,
            "failure": support.BASELINE_FAILURE,
        }

    def start_registry(self) -> str:
        name = f"{self.prefix}-registry"
        self.run(
            [
                "docker",
                "run",
                "-d",
                "--name",
                name,
                "--label",
                f"runner-proof={self.prefix}",
                "-p",
                "127.0.0.1::5000",
                REGISTRY_IMAGE,
            ],
            cwd=self.work,
            label="start isolated registry",
        )
        self.cleanup_later("registry", ["docker", "rm", "-f", "-v", name], None)
        mapped = self.run(["docker", "port", name, "5000/tcp"], cwd=self.work, label="registry")
        address = mapped.stdout.split()[0]
        wait_until(
            "isolated registry",
            lambda: http("GET", f"http://{address}/v2/")[0] == 200,
            timeout=60,
            interval=1,
        )
        return address

    def main_images(self, head: str) -> dict[str, dict]:
        """The product main job: prepare, generate, then build its matrix and publish."""
        clone = self.clone("main")
        steps = self.run_job(clone, "build-and-push", head)
        drift = self.git(
            "status", "--porcelain", "--untracked-files=no", cwd=clone, label="main drift"
        )
        if drift:
            raise ProofError(f"main generation changed tracked files:\n{drift}")
        workflow = yaml.safe_load((clone / ".github/workflows/ci.yml").read_text())
        registry = self.start_registry()
        images = {}
        for entry in support.build_matrix(workflow):
            repository = f"{registry}/{PROJECT_NAME}-{entry['image-suffix']}"
            tag = f"{repository}:{head[:12]}"
            env = self.clean_env(DOCKER_BUILDKIT="1")
            self.run(
                ["docker", "build", "--file", entry["dockerfile"], "--tag", tag, entry["context"]],
                cwd=clone,
                env=env,
                label=f"build {entry['id']} runtime image",
            )
            self.run(["docker", "push", tag], cwd=clone, env=env, label=f"push {entry['id']}")
            inspected = self.run(
                ["docker", "image", "inspect", "--format", "{{json .RepoDigests}}", tag],
                cwd=clone,
                label=f"digest {entry['id']}",
            )
            digest = support.pushed_digest(json.loads(inspected.stdout), repository)
            reference = f"{repository}@{digest}"
            # Remove the local copy: the deployment pulls the digest from the registry.
            self.run(["docker", "image", "rm", tag], cwd=clone, label=f"untag {entry['id']}")
            self.run(
                ["docker", "image", "rm", reference],
                cwd=clone,
                label=f"drop local {entry['id']}",
                check=False,
            )
            images[entry["id"]] = {**entry, "tag": tag, "digest": digest, "reference": reference}
        self.evidence["main_images"] = {"job": "build-and-push", "steps": steps, "images": images}
        return images

    def integration_without_host_environments(self) -> None:
        clone = self.clone("integration-cold")
        hosts = [str(path) for path in clone.glob("**/.venv/bin/python")]
        if hosts:
            raise ProofError(f"cold clone unexpectedly has host environments: {hosts}")
        shutil.copy(clone / ".env.example", clone / ".env")
        result = self.run(
            ["make", "test-integration"], cwd=clone, label="cold make test-integration"
        )
        if support.BASELINE_FAILURE in result.stdout + result.stderr:
            raise ProofError("integration generation still needs host environments")
        self.evidence["integration_without_host_environments"] = {
            "command": "make test-integration",
            "host_environments_before": hosts,
            "summary": (re.findall(r"\b\d+ passed\b[^\n]*", result.stdout) or [None])[-1],
        }

    # -- platform and deployment ------------------------------------------------------------

    def platform_compose(self) -> list[str]:
        return [
            "docker",
            "compose",
            "-p",
            f"{self.prefix}-platform",
            "-f",
            str(self.platform / "deploy/compose.yml"),
            "-f",
            str(self.kit / "tests/runner/compose.platform.yml"),
        ]

    def start_platform(self, product: Path, key: str) -> dict[str, str]:
        tag = f"runner-{self.prefix}"
        self.run(
            [
                "docker",
                "build",
                "--build-arg",
                "SERVICE=auth",
                "--tag",
                f"{PLATFORM_IMAGE}:{tag}",
                ".",
            ],
            cwd=self.platform,
            env=self.clean_env(DOCKER_BUILDKIT="1"),
            label="build pinned platform auth image",
        )
        secrets_dir = self.work / "platform-secrets"
        self.run(
            ["bash", str(self.platform / "deploy/ci/fake-secrets.sh"), str(secrets_dir)],
            cwd=self.platform,
            label="platform synthetic secrets",
        )
        (secrets_dir / "runner_product_key").write_text(key)
        (secrets_dir / "runner_product_key").chmod(0o644)
        self.secrets.update(path.read_text().strip() for path in secrets_dir.iterdir())
        network = f"{self.prefix}-orch-link"
        self.run(
            [
                "docker",
                "network",
                "create",
                "--internal",
                "--label",
                f"runner-proof={self.prefix}",
                network,
            ],
            cwd=self.work,
            label="create runner orch-link network",
        )
        self.cleanup_later("orch-link network", ["docker", "network", "rm", network], None)
        contract = yaml.safe_load(
            (product / "services/backend/packages/env.contract.yaml").read_text()
        )["entries"]["PLATFORM_KEY"]
        grant = {"scopes": contract["scopes"], "quota": contract["quota"]}
        env = self.clean_env(
            PLATFORM_IMAGE_TAG=tag,
            PLATFORM_SECRETS_DIR=str(secrets_dir),
            RUNNER_FIXTURES_DIR=str(self.kit / "tests/runner/fixtures"),
            RUNNER_FIXTURE_CHANNEL=FIXTURE_CHANNEL,
            RUNNER_FIXTURE_POST_TEXT=self.post_text,
            RUNNER_PRODUCT_ID=self.product_id,
            RUNNER_GRANT=json.dumps(grant),
            RUNNER_ORCH_LINK_NETWORK=network,
        )
        compose = self.platform_compose()
        self.cleanup_later("platform project", [*compose, "down", "-v", "--remove-orphans"], env)
        self.run([*compose, "up", "-d", "caddy"], cwd=self.platform, env=env, label="platform up")
        caddy = self.run([*compose, "ps", "-q", "caddy"], cwd=self.work, env=env, label="caddy id")
        wait_until(
            "healthy platform caddy",
            lambda: self.inspect(caddy.stdout.strip(), "{{.State.Health.Status}}") == "healthy",
            timeout=180,
        )
        admin = self.run(
            [*compose, "run", "--rm", "runner-admin"],
            cwd=self.platform,
            env=env,
            label="platform registration and ingress refusals",
            check=False,
        )
        summary = (support.json_lines(admin.stdout, "runner-admin ") or [{"status": "absent"}])[-1]
        if admin.returncode != 0 or summary["status"] != "passed":
            raise ProofError(f"platform admin/ingress check failed: {summary}")
        if self.reader_requests(env):
            raise ProofError("a refused ingress request reached the reader")
        self.evidence["platform"] = {
            "auth_image": f"{PLATFORM_IMAGE}:{tag} (built from the pinned source)",
            "product_id": self.product_id,
            "grant": grant,
            "admin": summary,
            "reader_requests_after_refusals": 0,
        }
        return env

    def inspect(self, container: str, template: str) -> str:
        return self.run(
            ["docker", "inspect", "--format", template, container],
            cwd=self.work,
            label=f"inspect {container[:12]}",
        ).stdout.strip()

    def logs_of(self, compose: list[str], service: str, env: dict[str, str]) -> str:
        return self.run(
            [*compose, "logs", "--no-color", "--no-log-prefix", service],
            cwd=self.work,
            env=env,
            label=f"logs {service}",
        ).stdout

    def reader_requests(self, env: dict[str, str]) -> list[dict]:
        records = support.json_lines(
            self.logs_of(self.platform_compose(), "tg-reader", env), "runner-reader "
        )
        return [item for item in records if item.get("event") == "request"]

    def telegram_certificates(self) -> Path:
        directory = self.work / "telegram"
        directory.mkdir()
        (directory / "server.ext").write_text(
            "subjectAltName=DNS:api.telegram.org\nbasicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\n"
            "subjectKeyIdentifier=hash\nauthorityKeyIdentifier=keyid,issuer\n"
        )
        for argv in (
            ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "2",
             "-subj", "/CN=runner proof CA", "-keyout", "ca.key", "-out", "ca.pem",
             "-addext", "basicConstraints=critical,CA:TRUE",
             "-addext", "keyUsage=critical,keyCertSign,cRLSign"],
            ["openssl", "req", "-newkey", "rsa:2048", "-nodes", "-subj", "/CN=api.telegram.org",
             "-keyout", "server.key", "-out", "server.csr"],
            ["openssl", "x509", "-req", "-in", "server.csr", "-CA", "ca.pem", "-CAkey", "ca.key",
             "-CAcreateserial", "-days", "2", "-out", "server.pem", "-extfile", "server.ext"],
        ):  # fmt: skip
            self.run(argv, cwd=directory, label=f"telegram fixture {argv[1]}")
        (directory / "server.key").chmod(0o644)
        return directory

    def product_compose(self, deploy: Path) -> list[str]:
        return [
            "docker",
            "compose",
            "--env-file",
            str(deploy / ".env"),
            "-p",
            f"{self.prefix}-product",
            "-f",
            str(deploy / "infra/compose.base.yml"),
            "-f",
            str(deploy / "infra/compose.prod.yml"),
            "-f",
            str(self.kit / "tests/runner/compose.product.yml"),
        ]

    def deploy_product(self, images: dict[str, dict], invalid_key: str) -> SimpleNamespace:
        """Lay out the product the way its deploy workflow does, then start it by digest."""
        source = self.work / "clones/main"
        deploy = self.work / "deploy"
        (deploy / "infra").mkdir(parents=True)
        for name in ("compose.base.yml", "compose.prod.yml"):
            shutil.copy(source / "infra" / name, deploy / "infra" / name)
        port = free_port()
        values = {
            "APP_ENV": "production",
            "APP_SECRET_KEY": secrets.token_urlsafe(32),
            "USERS_GRANT_CAPABILITY": secrets.token_urlsafe(24),
            "SETTINGS_WRITE_CAPABILITY": secrets.token_urlsafe(24),
            "JOBS_FIRE_CAPABILITY": secrets.token_urlsafe(24),
            "USER_IDENTITY_CAPABILITY": secrets.token_urlsafe(24),
            "DEBUG": "false",
            "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
            "TELEGRAM_BOT_TOKEN": f"{secrets.randbelow(10**9) + 10**9}:{secrets.token_urlsafe(26)}",
            "BACKEND_IMAGE": images["backend"]["reference"],
            "TG_BOT_IMAGE": images["tg-bot"]["reference"],
            "BACKEND_PORT": str(port),
            "PLATFORM_BASE_URL": "http://caddy:8080/tg-reader",
            "PLATFORM_KEY": invalid_key,
        }
        self.secrets.update(
            value
            for key, value in values.items()
            if key.endswith(("CAPABILITY", "SECRET_KEY", "PASSWORD", "TOKEN", "PLATFORM_KEY"))
        )
        example = (source / ".env.example").read_text()
        (deploy / ".env").write_text(support.render_dotenv(example, values))
        telegram = self.telegram_certificates()
        env = self.clean_env(
            RUNNER_TELEGRAM_DIR=str(telegram),
            RUNNER_TELEGRAM_TOKEN=values["TELEGRAM_BOT_TOKEN"],
            RUNNER_PYTHON_IMAGE=PYTHON_IMAGE,
            RUNNER_FIXTURES_DIR=str(self.kit / "tests/runner/fixtures"),
            RUNNER_PLATFORM_EDGE_NETWORK=f"{self.prefix}-platform_edge",
        )
        compose = self.product_compose(deploy)
        self.cleanup_later("product project", [*compose, "down", "-v", "--remove-orphans"], env)
        infra = deploy / "infra"
        self.run([*compose, "pull"], cwd=infra, env=env, label="product pull by digest")
        self.run(
            [*compose, "up", "-d", "--no-build", "--remove-orphans"],
            cwd=infra,
            env=env,
            label="product up (unknown platform key)",
        )
        deployment = SimpleNamespace(
            deploy=deploy, compose=compose, env=env, values=values, port=port, example=example
        )
        self.wait_backend(deployment)
        running = {}
        for service, image in (("backend", "backend"), ("tg_bot", "tg-bot")):
            container = self.run(
                [*compose, "ps", "-q", service], cwd=infra, env=env, label=f"{service} id"
            ).stdout.strip()
            configured = self.inspect(container, "{{.Config.Image}}")
            if configured != images[image]["reference"]:
                raise ProofError(f"{service} runs {configured}, not the pushed digest")
            running[service] = configured
        self.evidence["deployment"] = {
            "images": running,
            "platform_base_url": values["PLATFORM_BASE_URL"],
        }
        return deployment

    def wait_backend(self, deployment: SimpleNamespace) -> None:
        wait_until(
            "healthy product backend",
            lambda: http("GET", f"http://127.0.0.1:{deployment.port}/health")[0] == 200,
            timeout=240,
        )

    def initialize_product(self, deployment: SimpleNamespace) -> dict:
        """Binding settings (language, timezone) and access only through the product's APIs."""
        url = f"http://127.0.0.1:{deployment.port}"
        values = deployment.values
        settings = sorted(
            {
                key
                for item in self.evidence["coexistence"]["bindings"].values()
                for key in item["settings"]
            }
        )
        if unknown := [key for key in settings if key not in BINDING_SETTING_VALUES]:
            raise ProofError(f"no scenario value for binding settings {unknown}")
        steps = {
            f"setting {key}": http(
                "POST",
                f"{url}/settings/set",
                {"key": key, "scope": "product", "value": BINDING_SETTING_VALUES[key]},
                {"X-Settings-Capability": values["SETTINGS_WRITE_CAPABILITY"]},
            )
            for key in settings
        } | {
            "grant": http(
                "POST",
                f"{url}/users/grant",
                {"channel": "telegram", "external_id": str(TELEGRAM_USER)},
                {"X-Grant-Capability": values["USERS_GRANT_CAPABILITY"]},
            ),
            "access": http(
                "GET", f"{url}/users/access?channel=telegram&external_id={TELEGRAM_USER}"
            ),
        }
        if (
            any(status != 200 for status, _ in steps.values())
            or steps["access"][1]["status"] != "active"
        ):
            raise ProofError(f"product initialization failed: {self.redact(json.dumps(steps))}")
        return {name: {"status": status, "body": body} for name, (status, body) in steps.items()}

    def telegram(self, deployment: SimpleNamespace) -> str:
        mapped = self.run(
            [*deployment.compose, "port", "telegram-api", "8081"],
            cwd=deployment.deploy / "infra",
            env=deployment.env,
            label="telegram control port",
        )
        return f"http://{mapped.stdout.strip()}"

    def say(self, control: str, text: str) -> int:
        watermark = len(http("GET", f"{control}/control/state")[1]["sent"])
        status, body = http(
            "POST", f"{control}/control/messages", {"user_id": TELEGRAM_USER, "text": text}
        )
        if status != 200:
            raise ProofError(f"telegram fixture refused the update: {body}")
        return watermark

    def sent_after(
        self, control: str, watermark: int, accept: Callable[[dict], bool]
    ) -> dict | None:
        sent = http("GET", f"{control}/control/state")[1]["sent"]
        return next(
            (
                item
                for item in sent
                if item["seq"] > watermark and item["chat_id"] == TELEGRAM_USER and accept(item)
            ),
            None,
        )

    def scenario(self, deployment: SimpleNamespace, platform_env: dict[str, str], key: str) -> None:
        initialized = self.initialize_product(deployment)
        control = self.telegram(deployment)
        wait_until(
            "bot polling the Bot API",
            lambda: http("GET", f"{control}/control/state")[1]["calls"].get("getupdates"),
            timeout=180,
        )
        command = f"/channel @{FIXTURE_CHANNEL}"
        path = f"/tg-reader/v1/channels/{FIXTURE_CHANNEL}"

        # Negative control: an unknown key through the real ingress yields no post.
        watermark = self.say(control, command)
        refused = wait_until(
            "not-configured reply",
            lambda: self.sent_after(control, watermark, lambda item: True),
            timeout=120,
        )
        time.sleep(5)
        caddy = self.logs_of(self.platform_compose(), "caddy", platform_env)
        negative = {
            "command": command,
            "reply": refused,
            "ingress_statuses": support.caddy_requests(caddy, path),
            "reader_requests": len(self.reader_requests(platform_env)),
            "messages_after": [
                item["text"]
                for item in http("GET", f"{control}/control/state")[1]["sent"]
                if item["seq"] > watermark
            ],
        }
        if (
            refused["text"] != NOT_CONFIGURED
            or negative["reader_requests"] != 0
            or negative["ingress_statuses"].count(401) < 4
            or any(
                FIXTURE_CHANNEL in text and text.startswith("New post")
                for text in negative["messages_after"]
            )
        ):
            raise ProofError(f"negative control did not hold: {negative}")

        # Positive: the registered key, through the same real Caddy and auth.
        deployment.values["PLATFORM_KEY"] = key
        (deployment.deploy / ".env").write_text(
            support.render_dotenv(deployment.example, deployment.values)
        )
        self.run(
            [
                *deployment.compose,
                "up",
                "-d",
                "--no-build",
                "--no-deps",
                "--force-recreate",
                "backend",
            ],
            cwd=deployment.deploy / "infra",
            env=deployment.env,
            label="product backend with the registered platform key",
        )
        self.wait_backend(deployment)
        watermark = self.say(control, command)
        added = wait_until(
            "channel added reply",
            lambda: self.sent_after(control, watermark, lambda item: True),
            timeout=120,
        )
        if added["text"] != CHANNEL_ADDED:
            raise ProofError(f"channel subscription answered {added['text']!r}")
        delivered = wait_until(
            "fixture post delivery",
            lambda: self.sent_after(
                control, added["seq"], lambda item: self.post_text in item["text"]
            ),
            timeout=DELIVERY_TIMEOUT,
            interval=3,
        )
        requests = self.reader_requests(platform_env)
        caddy = self.logs_of(self.platform_compose(), "caddy", platform_env)
        positive = {
            "command": command,
            "subscription_reply": added,
            "delivered": delivered,
            "reader_requests": requests,
            "ingress_statuses": {
                path: support.caddy_requests(caddy, path),
                "/tg-reader/v1/posts": support.caddy_requests(caddy, "/tg-reader/v1/posts"),
            },
        }
        identities = {json.dumps(item.get("identity"), sort_keys=True) for item in requests}
        expected_text = (
            f"New post: @{FIXTURE_CHANNEL}\n",
            f"\n{self.post_text}\nhttps://t.me/{FIXTURE_CHANNEL}/",
        )
        if (
            not requests
            or "cps_" in caddy
            or any(item["status"] != 200 or not item.get("identity") for item in requests)
            or len(identities) != 1
            or requests[0]["identity"]["product_id"] != self.product_id
            or not any(item["path"] == "/v1/posts" and item.get("post_seqs") for item in requests)
            or not delivered["text"].startswith(expected_text[0])
            or expected_text[1] not in delivered["text"]
            or delivered["chat_id"] != TELEGRAM_USER
        ):
            raise ProofError(f"post delivery evidence is incomplete: {positive}")
        self.evidence["scenario"] = {
            "initialization": initialized,
            "telegram_user": TELEGRAM_USER,
            "fixture_channel": FIXTURE_CHANNEL,
            "fixture_post_text": self.post_text,
            "negative_unknown_key": negative,
            "positive": positive,
            "delivered_post": {
                "chat_id": delivered["chat_id"],
                "text": delivered["text"],
                "url": delivered["text"].rstrip().rsplit("\n", 1)[-1],
                "post_id": int(delivered["text"].rstrip().rsplit("/", 1)[-1]),
            },
        }
        if "reminders" in self.packages:
            self.evidence["scenario"]["reminders"] = self.reminder_scenario(control)
        self.evidence["scenario"]["languages"] = self.language_scenario(deployment, control)

    def set_language(self, deployment: SimpleNamespace, value: str) -> dict:
        status, body = http(
            "POST",
            f"http://127.0.0.1:{deployment.port}/settings/set",
            {"key": support.LANGUAGE_KEY, "scope": "product", "value": value},
            {"X-Settings-Capability": deployment.values["SETTINGS_WRITE_CAPABILITY"]},
        )
        if status != 200 or body.get("value") != value:
            raise ProofError(f"core language {value} was not set: {status} {body}")
        return {"status": status, "body": body}

    def reply_to(self, control: str, text: str) -> dict:
        watermark = self.say(control, text)
        return wait_until(
            f"reply to {text!r}",
            lambda: self.sent_after(
                control,
                watermark,
                lambda item: (
                    not item["text"].startswith(("New post", "Новая публикация"))
                    and not item["text"].startswith("Reminder: ")
                ),
            ),
            timeout=120,
        )

    def language_scenario(self, deployment: SimpleNamespace, control: str) -> dict:
        """Core unknown input and a module command answer in RU and EN via the Bot API."""
        results = {}
        for language in ("ru", "en"):  # End on the language the delivery scenario used.
            expected = LANGUAGE_REPLIES[language]
            setting = self.set_language(deployment, language)
            replies = {
                "unknown_command": self.reply_to(control, "/runner_unknown_command"),
                "unknown_text": self.reply_to(control, "runner plain text"),
                "channel_without_name": self.reply_to(control, "/channel"),
            }
            if (
                not replies["unknown_command"]["text"].startswith(expected["unknown"])
                or "/channel" not in replies["unknown_command"]["text"]
                or replies["unknown_text"]["text"] != replies["unknown_command"]["text"]
                or replies["channel_without_name"]["text"] != expected["channel"]
            ):
                raise ProofError(f"{language} command replies are wrong: {replies}")
            results[language] = {"setting": setting, "replies": replies}
        return results

    def reminder_scenario(self, control: str) -> dict:
        """The companion module keeps working: its command, timer and relay deliver a reminder."""
        text = f"Runner proof reminder {secrets.token_hex(4)}"
        command = f"/remind {text} in 1 minute"
        watermark = self.say(control, command)
        scheduled = wait_until(
            "reminder scheduled reply",
            lambda: self.sent_after(
                control, watermark, lambda item: not item["text"].startswith("New post")
            ),
            timeout=120,
        )
        if not (
            scheduled["text"].startswith("Scheduled for ") and scheduled["text"].endswith(text)
        ):
            raise ProofError(f"/remind answered {scheduled['text']!r}")
        due = wait_until(
            "reminder delivery",
            lambda: self.sent_after(
                control, scheduled["seq"], lambda item: item["text"] == f"Reminder: {text}"
            ),
            timeout=DELIVERY_TIMEOUT,
            interval=3,
        )
        return {"command": command, "scheduled_reply": scheduled, "delivered": due}

    # -- driver -----------------------------------------------------------------------------

    def collect_logs(self) -> None:
        for label, argv, env in self.cleanups:
            if argv[:2] == ["docker", "compose"]:
                compose = argv[: argv.index("down")]
                self.run(
                    [*compose, "ps", "-a"], cwd=self.work, env=env, label=f"{label} ps", check=False
                )
                self.run(
                    [*compose, "logs", "--no-color"],
                    cwd=self.work,
                    env=env,
                    label=f"{label} logs",
                    check=False,
                )

    def clean_up(self) -> None:
        for label, argv, env in self.cleanups:
            self.run(
                argv,
                cwd=self.work,
                env=env or self.clean_env(),
                label=f"clean up {label}",
                check=False,
            )

    def prove(self) -> None:
        sys.path[1:1] = [str(self.orchestrator), str(self.orchestrator / "services/scaffolder")]
        from shared.contracts.queues.scaffold import ScaffoldMessage  # noqa: PLC0415
        from src import install  # noqa: PLC0415

        modules = SimpleNamespace(install=install, ScaffoldMessage=ScaffoldMessage)
        self.product_id = f"runner-{self.prefix}"
        self.post_text = f"Runner proof fixture post {secrets.token_hex(6)}"
        key, invalid_key = support.product_key(), support.product_key()
        self.secrets.update((key, invalid_key))
        self.revisions()
        if self.catalog_mode == "pending_fixture":
            self.candidate_release()
        else:
            published = self.published_tags()
            if self.catalog_mode == "candidate_snapshot":
                self.candidate_snapshot(published)
        product = self.scaffold(install)
        if self.catalog_mode == "remote_head":
            self.remote_head()
        payloads, results = {}, {}
        # The executor installs each module on the same story branch, companions first.
        for name in self.packages:
            payloads[name] = self.select_payload(name)
            results[name] = asyncio.run(self.install(product, payloads[name], modules, name))
        result = results[PACKAGE]
        remote = self.git(
            "ls-remote",
            str(self.work / "remote.git"),
            f"refs/heads/story/{STORY}",
            cwd=self.work,
            label="story readback",
        ).split()[0]
        if remote != result.head_sha:
            raise ProofError("published story head differs from the executor result")
        readback = result.evidence
        self.evidence["modules"] = {
            name: {
                "package": name,
                "version": payloads[name]["package"]["version"],
                "tag": payloads[name]["package"]["tag"],
                "sources": results[name].evidence["component_sources"],
                "installed": results[name].evidence["distributions"],
            }
            for name in self.packages
        }
        self.evidence["module"] = self.evidence["modules"][PACKAGE]
        self.evidence["revisions"]["product_tooling"] = readback["tooling"]
        self.release_evidence(product, payloads[PACKAGE], readback)
        self.catalog_agreement(payloads)
        self.coexistence(product, install)
        self.product_ci(result.head_sha)
        self.cold_main_regression()
        images = self.main_images(result.head_sha)
        self.integration_without_host_environments()
        platform_env = self.start_platform(product, key)
        deployment = self.deploy_product(images, invalid_key)
        self.scenario(deployment, platform_env, key)

    def execute(self) -> int:
        try:
            self.prove()
            self.evidence["status"] = "passed"
        except Exception as error:
            self.evidence["status"] = "failed"
            self.evidence["error"] = self.redact(f"{type(error).__name__}: {error}")[:4000]
            self.collect_logs()
        finally:
            if self.catalog_fixture is not None:
                self.catalog_fixture.stop()
            self.clean_up()
            self.evidence["finished_at"] = datetime.now(UTC).isoformat()
            text = self.redact(json.dumps(self.evidence, indent=2, default=str)) + "\n"
            (self.output / "runner-proof.json").write_text(text)
            shutil.rmtree(self.work, ignore_errors=True)
        print(f"runner proof {self.evidence['status']}: {self.output / 'runner-proof.json'}")
        return 0 if self.evidence["status"] == "passed" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    for name in ("kit", "orchestrator", "platform"):
        parser.add_argument(f"--{name}-dir", type=Path, required=True)
        parser.add_argument(f"--{name}-sha", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--proof-mode",
        choices=support.PROOF_MODES,
        required=True,
        help="published_release: the pinned published tag, read from the real remote first; "
        "candidate_release: a pending release of the candidate through the isolated fixture",
    )
    parser.add_argument(
        "--catalog-mode",
        choices=support.CATALOG_MODES,
        required=True,
        help="remote_head: the real default branch's catalog, no fixture; candidate_snapshot: "
        "the candidate's committed catalog as an isolated prospective HEAD; pending_fixture: "
        "the candidate catalog plus its pending entry (candidate_release only)",
    )
    parser.add_argument("--package-version", required=True, help=f"expected {PACKAGE} version")
    parser.add_argument(
        "--packages",
        default=PACKAGE,
        help=f"install order, ending with {PACKAGE}; companions: {', '.join(COMPANIONS)}",
    )
    args = parser.parse_args()
    try:
        support.check_modes(args.proof_mode, args.catalog_mode)
    except ValueError as error:
        parser.error(str(error))
    for name in ("kit", "orchestrator", "platform"):
        if not re.fullmatch(r"[0-9a-f]{40}", getattr(args, f"{name}_sha")):
            parser.error(f"--{name}-sha must be a full commit SHA")
    args.packages = tuple(args.packages.split(","))
    if (
        args.packages[-1] != PACKAGE
        or len(set(args.packages)) != len(args.packages)
        or not set(args.packages[:-1]) <= set(COMPANIONS)
    ):
        parser.error(f"--packages must name companions from {COMPANIONS} and end with {PACKAGE}")
    return Runner(args).execute()


if __name__ == "__main__":
    sys.exit(main())
