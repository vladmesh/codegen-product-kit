"""CI-only released, bound products run their unchanged integration Make target."""

from hashlib import sha256
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile

import pytest
import yaml

from framework.catalog import parse_catalog
from framework.package_source import fetch_package_source, read_catalog
from tests.runner import support
from tests.tooling.test_package_catalog import _commit, _git, _tag

ROOT = Path(__file__).parents[2]
CHANNELS_PATH = "packages/codegen-kit-tg-channels"
UNCONFIGURED_WARNING = "tg-channels platform access is not configured"


def _candidate_channels_source(candidate: str, tmp_path: Path) -> tuple[str, list[str]]:
    """Release the candidate commit's tg-channels from a scratch catalog source.

    The source is the candidate commit's catalog: unchanged when tg-channels is not pending (its
    newest entry is then the candidate's published release, whose tree the candidate holds), or
    with the pending release (packages/pending-releases.yaml) appended, as the runner proof's
    candidate_release mode does. Either way the real default branch does not list a candidate's
    catalog before it merges, so the package tree is exported from the exact candidate commit and
    its tag exists only in the scratch source. Returns that version and the ``kit add`` catalog
    arguments.
    """
    paths = ["packages/catalog.yaml", support.PENDING_RELEASES, CHANNELS_PATH]
    archive = subprocess.run(  # noqa: S603
        ["git", "archive", "--format=tar", candidate, "--", *paths],  # noqa: S607
        cwd=ROOT,
        check=True,
        capture_output=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    )
    source = tmp_path / "candidate-catalog"
    source.mkdir()
    _git("init", "--quiet", cwd=source)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as exported:
        exported.extractall(source, filter="data")
    pending = source / support.PENDING_RELEASES
    releases = yaml.safe_load(pending.read_text())
    pending.unlink()
    published = source / "packages/catalog.yaml"
    release = None
    if any(item["package"] == "tg-channels" for item in releases["releases"]):
        release = support.pending_release(releases, "tg-channels")
        published.write_text(
            yaml.safe_dump(support.fixture_catalog(yaml.safe_load(published.read_text()), release))
        )
    catalog = parse_catalog(published.read_text(), "candidate catalog")
    newest = catalog.get("tg-channels").newest()
    manifest = source / CHANNELS_PATH / "codegen_kit_tg_channels/package.yaml"
    assert yaml.safe_load(manifest.read_text())["version"] == newest.version
    assert release is None or release["version"] == newest.version
    _commit(source, f"Candidate tg-channels {newest.version} at {candidate}")
    _tag(source, newest.tag)
    return newest.version, ["--catalog-source", str(source)]


def test_candidate_channels_source_releases_exactly_the_candidate_commit(tmp_path: Path) -> None:
    candidate = _git("rev-parse", "HEAD", cwd=ROOT).strip()
    version, arguments = _candidate_channels_source(candidate, tmp_path)
    assert arguments[0] == "--catalog-source"
    channels = read_catalog(arguments[1], "HEAD").get("tg-channels")
    release = channels.select("2.4.0")
    assert release.version == version and release.tag == f"packages/tg-channels/v{version}"
    workdir = tmp_path / "work"
    workdir.mkdir()
    exported = fetch_package_source(arguments[1], channels, release, workdir)
    for name in ("pyproject.toml", "codegen_kit_tg_channels/__init__.py"):
        expected = _git("show", f"{candidate}:{CHANNELS_PATH}/{name}", cwd=ROOT)
        assert (exported / name).read_text() == expected


@pytest.mark.slow
@pytest.mark.parametrize("package", ["reminders", "tg-channels"])
def test_published_bound_product_passes_own_integration(
    project_backend_tg_bot: Path, tmp_path: Path, package: str
) -> None:
    # Reminders/textparse use their published tags. tg-channels uses the exact candidate commit's
    # package release (see _candidate_channels_source); no working-tree source is substituted.
    assert os.environ.get("CI") == "true", "Published integration proof runs in CI only"
    requirement = os.environ["CODEGEN_TOOLING_REQUIREMENT"]
    assert " @ git+" in requirement
    candidate = requirement.rsplit("@", 1)[1]
    assert re.fullmatch(r"[0-9a-f]{40}", candidate)
    product = tmp_path / "product"
    shutil.copytree(project_backend_tg_bot, product)
    versions = {"reminders": "0.5.0", "textparse": "0.1.0"}
    sources: dict[str, list[str]] = {}
    if package == "tg-channels":
        versions[package], sources[package] = _candidate_channels_source(candidate, tmp_path)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in ("PYTHONPATH", "VIRTUAL_ENV")
        and not key.startswith(("GIT_", "KIT_CATALOG_", "PLATFORM_"))
    }

    def run(command: list[str]) -> str:
        result = subprocess.run(
            command, cwd=product, env=environment, capture_output=True, text=True
        )
        if command == ["make", "test-integration"]:
            logs = Path(os.environ.get("CODEGEN_BOUND_INTEGRATION_LOG_DIR", str(tmp_path / "logs")))
            logs.mkdir(parents=True, exist_ok=True)
            (logs / f"{package}.log").write_text(result.stdout + result.stderr)
            (logs / f"{package}.json").write_text(
                json.dumps(
                    {
                        "candidate": candidate,
                        "package": package,
                        "components": {name: versions[name] for name in components},
                        "command": command,
                        "exit_code": result.returncode,
                        "test_contract_sha256": {
                            str(path.relative_to(product)): sha256(content).hexdigest()
                            for path, content in contract.items()
                        },
                        "package_source": f"candidate {candidate} (scratch tag only)"
                        if package == "tg-channels"
                        else "published catalog tags",
                        "platform": "absent: no PLATFORM_KEY/PLATFORM_BASE_URL in product .env"
                        if package == "tg-channels"
                        else None,
                        "unconfigured_warning_logged": UNCONFIGURED_WARNING
                        in result.stdout + result.stderr
                        if package == "tg-channels"
                        else None,
                    },
                    indent=2,
                )
                + "\n"
            )
        assert result.returncode == 0, f"{command}\n{result.stdout}{result.stderr}"
        return result.stdout

    run(["make", "setup"])
    contract = {
        path: path.read_bytes()
        for path in [
            product / "Makefile",
            product / "infra/compose.tests.integration.yml",
            *sorted((product / "tests/integration").glob("*.py")),
        ]
    }
    python = str(product / ".venv/bin/python")
    tooling = json.loads(
        run(
            [
                python,
                "-I",
                "-c",
                "from importlib.metadata import distribution; "
                "print(distribution('codegen-kit-tooling').read_text('direct_url.json'))",
            ]
        )
    )
    assert tooling["vcs_info"]["commit_id"] == candidate
    components = ["textparse", package] if package == "reminders" else [package]
    for component in components:
        installed = run(
            [
                python,
                "-m",
                "framework.cli",
                "add",
                component,
                *sources.get(component, []),
                "--product-root",
                str(product),
            ]
        )
        version = versions[component]
        assert f"{component} {version} (packages/{component}/v{version})" in installed
    run(
        [
            python,
            "-m",
            "framework.cli",
            "bind",
            package,
            "--default",
            "--product-root",
            str(product),
        ]
    )
    run(["make", "generate-from-spec"])
    binding = product / f"services/tg_bot/bindings/{package}.yaml"
    assert yaml.safe_load(binding.read_text())["package"] == package
    for name in ("bindings.py", "binding_relay.py"):
        assert (product / "services/tg_bot/src/generated" / name).is_file()
    serialized = "bindings.py" if package == "reminders" else "bindings_v1.py"
    assert package in (product / "services/tg_bot/src/generated" / serialized).read_text()
    active = (product / "codegen_kit/_active_packages.py").read_text()
    assert package in active and versions[package] in active

    env_file = (product / ".env.example").read_text()
    if package == "tg-channels":
        fragment = yaml.safe_load(
            (product / "services/backend/packages/env.contract.yaml").read_text()
        )
        entries = fragment["entries"]
        assert entries["PLATFORM_KEY"]["source"] == "platform_key"
        assert entries["PLATFORM_BASE_URL"]["source"] == "platform_base_url"
        assert entries["PLATFORM_KEY"]["service"] == "tg-reader"
        assert entries["PLATFORM_KEY"]["scopes"] == ["tg-reader:read"]
        assert entries["PLATFORM_KEY"]["quota"] == {
            "channels_max": 50,
            "requests_per_minute": 60,
            "resolve_per_day": 200,
        }
        assert entries["PLATFORM_BASE_URL"]["url"] == "https://platform.vladmesh.dev/tg-reader"
        # Like a real product CI, the integration environment has no platform values: the
        # package starts not configured and its timer makes no platform request.
        assert not re.search(r"^\s*PLATFORM_", env_file, re.M)
    (product / ".env").write_text(env_file)
    makefile = (product / "Makefile").read_text()
    assert re.search(r"^test-integration:\n", makefile, re.M)
    assert "--exit-code-from integration-tests" in makefile
    # No replacement pytest command, altered tests or alternate Compose stack.
    assert {path: path.read_bytes() for path in contract} == contract
    output = run(["make", "test-integration"])
    summary = re.search(r"\b\d+ passed\b[^\n]*", output)
    assert summary is not None, output
    assert {path: path.read_bytes() for path in contract} == contract
    print(f"{package}: own make test-integration: {summary.group(0)}")
