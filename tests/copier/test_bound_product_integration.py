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
import tomllib

import pytest
import yaml

from framework.catalog import parse_catalog
from framework.package_source import fetch_package_source, read_catalog
from tests.runner import support
from tests.tooling.test_package_catalog import _commit, _git, _tag

ROOT = Path(__file__).parents[2]
CHANNELS_PATH = "packages/codegen-kit-tg-channels"
RANKING_PATH = "packages/codegen-kit-ranking"
UNCONFIGURED_WARNING = "tg-channels platform access is not configured"
#: Packages the slow leg installs from the exact candidate commit, in install order.
CANDIDATE_LEGS = {"tg-channels": ["tg-channels"], "ranking": ["tg-channels", "ranking"]}


def _candidate_source(
    candidate: str, tmp_path: Path, packages: list[str]
) -> tuple[dict[str, str], list[str]]:
    """Release the candidate commit's ``packages`` from one scratch catalog source.

    The source is the candidate commit's catalog with every pending release of ``packages``
    (packages/pending-releases.yaml) appended, as the runner proof's candidate_release mode does;
    a package that is not pending keeps its newest entry, the candidate's published release, whose
    tree the candidate holds. The real default branch does not list a candidate's catalog before it
    merges, so each package tree is exported from the exact candidate commit and its tag exists only
    in the scratch source. Returns each package's version and the ``kit add`` catalog arguments.
    """
    catalog_document = yaml.safe_load(_git("show", f"{candidate}:packages/catalog.yaml", cwd=ROOT))
    releases = yaml.safe_load(_git("show", f"{candidate}:{support.PENDING_RELEASES}", cwd=ROOT))
    pending = {item["package"] for item in releases["releases"]}
    for package in packages:
        if package in pending:
            catalog_document = support.fixture_catalog(
                catalog_document, support.pending_release(releases, package)
            )
    catalog = parse_catalog(yaml.safe_dump(catalog_document), "candidate catalog")
    paths = [catalog.get(package).path for package in packages]
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
    (source / "packages/catalog.yaml").write_text(yaml.safe_dump(catalog_document))
    versions = {}
    for package in packages:
        newest = catalog.get(package).newest()
        project = tomllib.loads((source / catalog.get(package).path / "pyproject.toml").read_text())
        assert project["project"]["version"] == newest.version
        versions[package] = newest.version
    _commit(source, f"Candidate {', '.join(packages)} at {candidate}")
    for package in packages:
        _tag(source, catalog.get(package).newest().tag)
    return versions, ["--catalog-source", str(source)]


def test_candidate_source_releases_exactly_the_candidate_commit(tmp_path: Path) -> None:
    candidate = _git("rev-parse", "HEAD", cwd=ROOT).strip()
    versions, arguments = _candidate_source(candidate, tmp_path, CANDIDATE_LEGS["ranking"])
    assert arguments[0] == "--catalog-source"
    catalog = read_catalog(arguments[1], "HEAD")
    for package, path in (("tg-channels", CHANNELS_PATH), ("ranking", RANKING_PATH)):
        workdir = tmp_path / "work" / package
        workdir.mkdir(parents=True)
        entry = catalog.get(package)
        release = entry.select("2.4.0")
        assert release.version == versions[package]
        assert release.tag == f"packages/{package}/v{versions[package]}"
        exported = fetch_package_source(arguments[1], entry, release, workdir)
        for name in ("pyproject.toml", f"{entry.distribution.replace('-', '_')}/__init__.py"):
            expected = _git("show", f"{candidate}:{path}/{name}", cwd=ROOT)
            assert (exported / name).read_text() == expected


def _assert_unconfigured_platform_environment(product: Path, env_file: str) -> None:
    """tg-channels declares its platform sources; the product environment carries no values."""
    fragment = yaml.safe_load((product / "services/backend/packages/env.contract.yaml").read_text())
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


@pytest.mark.slow
@pytest.mark.parametrize("package", ["reminders", "tg-channels", "ranking"])
def test_published_bound_product_passes_own_integration(
    project_backend_tg_bot: Path, tmp_path: Path, package: str
) -> None:
    # Reminders/textparse use their published tags. tg-channels and ranking use the exact candidate
    # commit's package releases (see _candidate_source); no working-tree source is substituted.
    assert os.environ.get("CI") == "true", "Published integration proof runs in CI only"
    requirement = os.environ["CODEGEN_TOOLING_REQUIREMENT"]
    assert " @ git+" in requirement
    candidate = requirement.rsplit("@", 1)[1]
    assert re.fullmatch(r"[0-9a-f]{40}", candidate)
    product = tmp_path / "product"
    shutil.copytree(project_backend_tg_bot, product)
    versions = {"reminders": "0.5.0", "textparse": "0.1.0"}
    sources: dict[str, list[str]] = {}
    channels = package in CANDIDATE_LEGS
    if channels:
        candidates, arguments = _candidate_source(candidate, tmp_path, CANDIDATE_LEGS[package])
        versions |= candidates
        sources = dict.fromkeys(candidates, arguments)
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
                        if channels
                        else "published catalog tags",
                        "platform": "absent: no PLATFORM_KEY/PLATFORM_BASE_URL in product .env"
                        if channels
                        else None,
                        "unconfigured_warning_logged": UNCONFIGURED_WARNING
                        in result.stdout + result.stderr
                        if channels
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
    components = ["textparse", package] if package == "reminders" else CANDIDATE_LEGS[package]
    bound = [component for component in components if component != "textparse"]
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
    for component in bound:
        run(
            [
                python,
                "-m",
                "framework.cli",
                "bind",
                component,
                "--default",
                "--product-root",
                str(product),
            ]
        )
    run(["make", "generate-from-spec"])
    for name in ("bindings.py", "binding_relay.py"):
        assert (product / "services/tg_bot/src/generated" / name).is_file()
    serialized = "bindings.py" if package == "reminders" else "bindings_v1.py"
    active = (product / "codegen_kit/_active_packages.py").read_text()
    for component in bound:
        binding = product / f"services/tg_bot/bindings/{component}.yaml"
        assert yaml.safe_load(binding.read_text())["package"] == component
        assert component in (product / "services/tg_bot/src/generated" / serialized).read_text()
        assert component in active and versions[component] in active

    env_file = (product / ".env.example").read_text()
    if channels:
        _assert_unconfigured_platform_environment(product, env_file)
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
