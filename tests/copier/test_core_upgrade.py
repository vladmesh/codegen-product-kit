"""CI-only native update of an untouched released core 2.1 product."""

from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
import tomllib

import pytest
import yaml

from framework.package_source import DEFAULT_CATALOG_SOURCE
from tests.copier.conftest import BASE_DATA, REPO_ROOT, VENV_COPIER, _git
from tests.copier.test_deploy_transport import _assert_tooling_revision, _fixture_env
from tests.copier.test_textparse_install import (
    _assert_library_wheel,
    _bindings_remote_proof,
    _published_provenance,
)

PRODUCER = {
    "tag": "0.7.1",
    "object": "b1c90af4d0daee59bd63146d7871d4c8bf63f24a",
    "commit": "56da5c83cb8d011823ce2cb70345415b223b93ab",
    "tree": "ce069524a095e3adfbed5d65ad7ebe66cd5b73d8",
    "facade_blob": "b6c81a9c1e2db902ee29128c6871c55c7ae12263",
}
MARKER = re.compile(rb"^(?:<<<<<<< |=======\r?$|>>>>>>> |\|\|\|\|\|\|\| )", re.MULTILINE)


@pytest.fixture(scope="session", autouse=True)
def copier_available():
    """This required lane fails rather than inheriting the generic optional skip."""
    assert VENV_COPIER.is_file(), "Real Copier executable is required"


def _files(product: Path) -> list[Path]:
    names = _git("ls-files", "--cached", "--others", "--exclude-standard", cwd=product)
    return [product / name for name in sorted(set(names.stdout.splitlines()))]


def _hashes(product: Path) -> dict[str, str]:
    return {
        str(path.relative_to(product)): sha256(path.read_bytes()).hexdigest()
        for path in _files(product)
        if path.is_file()
    }


def _protected(product: Path) -> dict[str, str]:
    hashes = _hashes(product)
    return {
        name: digest
        for name, digest in hashes.items()
        if name in {".env", ".env.example", "shared/spec/models.yaml", "shared/spec/events.yaml"}
        or re.match(r"services/[^/]+/src/(?:app|controllers)/", name)
    }


def _conflicts(product: Path) -> dict[str, list[str]]:
    artifacts = sorted(
        str(path.relative_to(product))
        for path in product.rglob("*")
        if path.is_file()
        and not any(part in {".git", ".venv"} for part in path.relative_to(product).parts)
        and (path.suffix in {".rej", ".orig"} or path.name.endswith("~"))
    )
    markers = [
        str(path.relative_to(product))
        for path in _files(product)
        if path.is_file() and MARKER.search(path.read_bytes())
    ]
    unmerged = _git("ls-files", "--unmerged", cwd=product).stdout.splitlines()
    return {"artifacts": artifacts, "markers": markers, "unmerged": unmerged}


def _changes(before: dict[str, str], after: dict[str, str]) -> dict[str, dict]:
    return {
        name: {"before": before.get(name), "after": after.get(name)}
        for name in sorted(before.keys() | after.keys())
        if before.get(name) != after.get(name)
    }


def _runtime(run, product: Path, version: str) -> dict:
    result = json.loads(
        run(
            [
                str(product / ".venv/bin/python"),
                "-c",
                "import json, codegen_kit; "
                "print(json.dumps({'core': codegen_kit.CORE_VERSION, "
                "'protocol': codegen_kit.PACKAGE_PROTOCOL_VERSION, "
                "'origin': codegen_kit.__file__}))",
            ]
        )
    )
    assert result["core"] == version and result["protocol"] == 1
    assert Path(result["origin"]).is_relative_to(product / "codegen_kit")
    return result


def _installed_tooling(run, product: Path, revision: str) -> dict:
    result = json.loads(
        run(
            [
                str(product / ".venv/bin/python"),
                "-I",
                "-c",
                "import json, sys, framework; from importlib.metadata import distribution; "
                "d=distribution('codegen-kit-tooling'); "
                "print(json.dumps({'version': d.version, 'origin': framework.__file__, "
                "'prefix': sys.prefix, 'direct_url': json.loads(d.read_text('direct_url.json'))}))",
            ]
        )
    )
    assert result["version"] == "0.1.0"
    assert Path(result["prefix"]) == product / ".venv"
    assert Path(result["origin"]).is_relative_to(product / ".venv")
    direct = result["direct_url"]
    assert direct["url"] == "https://github.com/vladmesh/codegen-product-kit.git"
    assert direct["vcs_info"]["commit_id"] == revision
    return result


def test_core_upgrade_required_ci_route() -> None:
    workflow = yaml.safe_load((REPO_ROOT / ".github/workflows/test-template.yml").read_text())
    steps = workflow["jobs"]["test-pytest"]["steps"]
    checkout = next(step for step in steps if step.get("name") == "Checkout")
    assert checkout["with"]["fetch-depth"] == 0
    slow = next(step for step in steps if step.get("run") == "make test-copier-slow")
    assert slow["env"]["CODEGEN_CORE_UPGRADE_RECEIPT"] == (
        "${{ runner.temp }}/core-21-upgrade-receipt.json"
    )
    assert workflow["jobs"]["test-pytest"]["env"]["CODEGEN_CORE_UPGRADE_SHA"] == (
        "${{ github.event.pull_request.head.sha || github.sha }}"
    )
    upload = next(
        step for step in steps if step.get("with", {}).get("name", "").startswith("core-21-upgrade")
    )
    assert upload["uses"] == "actions/upload-artifact@v4"
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["path"] == slow["env"]["CODEGEN_CORE_UPGRADE_RECEIPT"]


@pytest.mark.slow
def test_released_core_21_native_upgrade_and_published_bindings(tmp_path: Path) -> None:  # noqa: PLR0915
    """Fail on missing CI/ref/configuration; never manufacture a compatible baseline."""
    assert os.environ.get("CI") == "true", "Released upgrade proof runs in CI only"
    candidate = os.environ["CODEGEN_CORE_UPGRADE_SHA"]
    assert re.fullmatch(r"[0-9a-f]{40}", candidate)
    receipt_path = Path(os.environ["CODEGEN_CORE_UPGRADE_RECEIPT"])
    assert VENV_COPIER.is_file(), "Real Copier executable is required"
    assert not _git("status", "--porcelain", cwd=REPO_ROOT).stdout.strip()
    assert _git("rev-parse", candidate + "^{commit}", cwd=REPO_ROOT).stdout.strip() == candidate

    source = tmp_path / "source"
    _git("clone", "--quiet", "--no-hardlinks", str(REPO_ROOT), str(source), cwd=REPO_ROOT)
    _git("checkout", "--quiet", "--detach", candidate, cwd=source)
    assert not _git("status", "--porcelain", cwd=source).stdout.strip()
    assert _git("cat-file", "-t", "0.7.1", cwd=source).stdout.strip() == "tag"
    for ref, expected in (
        ("0.7.1", PRODUCER["object"]),
        ("0.7.1^{}", PRODUCER["commit"]),
        ("0.7.1^{tree}", PRODUCER["tree"]),
        ("0.7.1:template/codegen_kit/packages.py", PRODUCER["facade_blob"]),
    ):
        assert _git("rev-parse", ref, cwd=source).stdout.strip() == expected
    facade = _git("show", "0.7.1:template/codegen_kit/packages.py", cwd=source).stdout
    assert 'CORE_VERSION = "2.1.0"' in facade

    product = tmp_path / "product"
    environment = {
        key: value
        for key, value in os.environ.items()
        if key != "PYTHONPATH" and not key.startswith(("GIT_", "KIT_CATALOG_"))
    }
    mapped = _fixture_env(source)
    mapped = {key: value for key, value in mapped.items() if key != "PYTHONPATH"}
    commands = []

    def run(argv: list[str], *, mapping=False, cwd=None) -> str:
        result = subprocess.run(
            argv,
            cwd=cwd or product,
            env=mapped if mapping else environment,
            capture_output=True,
            text=True,
            timeout=600,
        )
        commands.append(
            {
                "argv": argv,
                "cwd": str(cwd or product),
                "source_mapping": mapping,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    remote = run(
        ["git", "ls-remote", DEFAULT_CATALOG_SOURCE, "refs/tags/0.7.1", "refs/tags/0.7.1^{}"],
        cwd=source,
    )
    assert dict(line.split()[::-1] for line in remote.splitlines()) == {
        "refs/tags/0.7.1": PRODUCER["object"],
        "refs/tags/0.7.1^{}": PRODUCER["commit"],
    }
    copy_argv = [
        str(VENV_COPIER),
        "copy",
        str(source),
        str(product),
        "--defaults",
        "--trust",
        "--vcs-ref=0.7.1",
        *(f"--data={k}={v}" for k, v in BASE_DATA.items()),
        "--data=modules=backend,tg_bot",
    ]
    run(copy_argv, mapping=True, cwd=source)
    answers_before = yaml.safe_load((product / ".copier-answers.yml").read_text())
    assert answers_before["_commit"] == "0.7.1"
    assert answers_before["modules"] == "backend,tg_bot"
    assert answers_before["_src_path"] == str(source)
    assert "tooling_requirement" not in answers_before
    _assert_tooling_revision(product, PRODUCER["commit"])
    assert (
        _git("hash-object", "codegen_kit/packages.py", cwd=product).stdout.strip()
        == (PRODUCER["facade_blob"])
    )
    assert not (product / "services/tg_bot/bindings/reminders.yaml").exists()
    assert not yaml.safe_load((product / "services/backend/manifest.yaml").read_text()).get(
        "packages"
    )
    baseline_files = _hashes_after_init(product)
    protected_before = _protected(product)
    assert ".env.example" in protected_before and "shared/spec/models.yaml" in protected_before
    assert any(name.startswith("services/backend/src/controllers/") for name in protected_before)
    baseline = _git("rev-parse", "HEAD", cwd=product).stdout.strip()
    baseline_lock = (product / "uv.lock").read_text()
    run(["uv", "sync", "--frozen"], mapping=True)
    old_runtime = _runtime(run, product, "2.1.0")
    old_tooling = _installed_tooling(run, product, PRODUCER["commit"])
    assert _hashes(product) == baseline_files
    assert not _git("status", "--porcelain", cwd=product).stdout.strip()

    update_argv = [
        str(VENV_COPIER),
        "update",
        "--defaults",
        "--trust",
        f"--vcs-ref={candidate}",
        "--conflict=rej",
    ]
    run(update_argv, mapping=True)
    conflict_scan = _conflicts(product)
    assert conflict_scan == {"artifacts": [], "markers": [], "unmerged": []}
    protected_after = _protected(product)
    assert protected_after == protected_before
    after_copier = _hashes(product)
    answers_after = yaml.safe_load((product / ".copier-answers.yml").read_text())
    assert answers_after["modules"] == "backend,tg_bot"
    assert answers_after["_src_path"] == answers_before["_src_path"]
    assert "tooling_requirement" not in answers_after
    assert _git("rev-parse", answers_after["_commit"], cwd=source).stdout.strip() == candidate
    _assert_tooling_revision(product, candidate)
    assert 'CORE_VERSION = "2.3.0"' in (product / "codegen_kit/packages.py").read_text()
    assert (product / "services/backend").is_dir() and (product / "services/tg_bot").is_dir()
    seed = product / "services/tg_bot/src/generated/bindings.py"
    assert "No product bindings" in seed.read_text()
    main = (product / "services/tg_bot/src/main.py").read_text()
    for hook in (
        "bindings.register(application, BackendClient)",
        "await bindings.start(application)",
        "await bindings.stop(application)",
    ):
        assert hook in main
    for name in (
        "services/tg_bot/pyproject.toml",
        "services/tg_bot/uv.lock",
        "services/tg_bot/src/main.py",
        "services/tg_bot/src/generated/bindings.py",
    ):
        assert baseline_files.get(name) != after_copier[name]
    dependencies = tomllib.loads((product / "services/tg_bot/pyproject.toml").read_text())[
        "project"
    ]["dependencies"]
    assert {"faststream[redis]>=0.6.6,<0.7", "redis>=5,<8", "jsonschema>=4.0,<5.0"}.issubset(
        dependencies
    )
    updated_lock = (product / "uv.lock").read_text()
    updated_files = {
        name: (product / name).read_text()
        for name in (
            "pyproject.toml",
            "services/tg_bot/pyproject.toml",
            "services/tg_bot/uv.lock",
            "services/tg_bot/src/main.py",
            "services/tg_bot/src/generated/bindings.py",
        )
    }

    run(["make", "setup"], mapping=True)
    tooling = _installed_tooling(run, product, candidate)
    runtime = _runtime(run, product, "2.3.0")
    run(["make", "validate-specs"])
    run(["make", "generate-from-spec"])
    unbound_typecheck = run(["make", "typecheck"])
    assert "error:" not in unbound_typecheck
    run(["make", "tests", "REDIS_URL=redis://redis.invalid:6379"])
    unbound = json.loads(
        run(
            [
                str(product / "services/tg_bot/.venv/bin/python"),
                "-c",
                "import json, sys; from services.tg_bot.src.generated import bindings; "
                "bindings.register(object()); "
                "assert 'codegen_kit_textparse' not in sys.modules; "
                "assert 'faststream' not in sys.modules; "
                "print(json.dumps({'unbound_seed': True}))",
            ]
        )
    )
    before_install = _hashes(product)
    cli = str(product / ".venv/bin/kit")
    for name, expected in (("reminders", "0.5.0"), ("textparse", "0.1.0")):
        output = run([cli, "add", name, "--product-root", str(product)])
        assert f"{name} {expected} (packages/{name}/v{expected})" in output
    _assert_library_wheel(
        product / "services/tg_bot/packages/codegen_kit_textparse-0.1.0-py3-none-any.whl"
    )
    library_provenance, library_tree = _published_provenance(
        run, "refs/tags/packages/textparse/v0.1.0"
    )
    bindings = _bindings_remote_proof(
        run,
        product,
        product / ".venv/bin/python",
        product / "services/tg_bot",
        candidate,
        install_reminders=False,
        run_redis=False,
    )
    assert "error:" not in bindings["product_typecheck"]["stdout"]
    manifest = yaml.safe_load((product / "services/tg_bot/manifest.yaml").read_text())
    timezone = manifest["settings_schema"]["properties"]["timezone"]
    assert timezone["type"] == "string" and timezone["format"] == "x-iana-tz"
    assert "default" not in timezone
    assert (
        "timezone" in (product / "services/backend/src/generated/settings_schemas.py").read_text()
    )
    run(["make", "validate-specs"])
    run(["make", "generate-from-spec"])
    run(["make", "tests", "REDIS_URL=redis://redis.invalid:6379"])
    _assert_tooling_revision(product, candidate)
    assert _installed_tooling(run, product, candidate) == tooling
    receipt = {
        "candidate": candidate,
        "candidate_tree": _git("rev-parse", candidate + "^{tree}", cwd=source).stdout.strip(),
        "producer": PRODUCER,
        "source": str(source),
        "source_mapping": {
            "remote": "https://github.com/vladmesh/codegen-product-kit.git",
            "clone": str(source),
        },
        "declared_answers": {**BASE_DATA, "modules": "backend,tg_bot"},
        "answers_before": answers_before,
        "answers_after": answers_after,
        "baseline_commit": baseline,
        "baseline_clean": True,
        "baseline_runtime": old_runtime,
        "baseline_tooling": old_tooling,
        "baseline_lock": baseline_lock,
        "updated_lock": updated_lock,
        "updated_core_owned_files": updated_files,
        "runtime": runtime,
        "tooling": tooling,
        "copy_argv": copy_argv,
        "update_argv": update_argv,
        "conflict_scan": conflict_scan,
        "protected_before": protected_before,
        "protected_after_copier": protected_after,
        "copier_changes": _changes(baseline_files, after_copier),
        "setup_generation_changes": _changes(after_copier, before_install),
        "component_install_binding_changes": _changes(before_install, _hashes(product)),
        "unbound": unbound,
        "bindings": bindings,
        "timezone_schema": timezone,
        "textparse_provenance": library_provenance,
        "textparse_tree": library_tree,
        "commands": commands,
        "run_id": os.environ["GITHUB_RUN_ID"],
        "run_url": f"https://github.com/{os.environ['GITHUB_REPOSITORY']}/actions/runs/{os.environ['GITHUB_RUN_ID']}",
    }
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")


def _hashes_after_init(product: Path) -> dict[str, str]:
    """Commit generated output as-is; configuration supplies Git metadata only."""
    _git("init", "--quiet", cwd=product)
    _git("add", "-A", cwd=product)
    _git(
        "-c",
        "user.name=Upgrade CI",
        "-c",
        "user.email=tests@example.com",
        "commit",
        "--quiet",
        "-m",
        "Unmodified released 0.7.1 product",
        cwd=product,
    )
    assert not _git("status", "--porcelain", cwd=product).stdout.strip()
    return _hashes(product)
