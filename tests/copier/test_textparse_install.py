"""Generated dependency/image contract and CI-only real library release installation."""

from email.parser import Parser
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import textwrap
import tomllib
import zipfile

import pytest

from framework.package_source import DEFAULT_CATALOG_SOURCE, build_wheel
from tests.tooling.test_package_catalog import _commit, _git, _snapshot, _tag

ROOT = Path(__file__).parents[2]


def test_tg_bot_image_has_locked_library_artifacts(project_backend_tg_bot: Path) -> None:
    service = project_backend_tg_bot / "services/tg_bot"
    dockerfile = (service / "Dockerfile").read_text()
    copy = "COPY services/tg_bot/packages ./services/tg_bot/packages"
    assert dockerfile.index(copy) < dockerfile.index("uv sync --frozen")
    assert (service / "packages/.gitkeep").is_file()
    assert "COPY --from=deps /app/services/tg_bot/.venv" in dockerfile


@pytest.mark.slow
@pytest.mark.parametrize("source_mode", ["local_fixture", "published_remote"])
def test_real_tag_wheel_installs_and_runs_in_generated_tg_bot_and_image(
    project_backend_tg_bot: Path,
    tmp_path: Path,
    source_mode: str,
) -> None:
    """Network/build/container proof belongs to the existing slow Copier CI leg."""
    product = tmp_path / "product"
    shutil.copytree(project_backend_tg_bot, product)
    source = None
    if source_mode == "local_fixture":
        source = tmp_path / "catalog-source"
        source.mkdir()
        _git("init", "--quiet", cwd=source)
        package = "packages/codegen-kit-textparse"
        shutil.copytree(
            ROOT / package, source / package, ignore=shutil.ignore_patterns("__pycache__")
        )
        shutil.copy2(ROOT / "packages/catalog.yaml", source / "packages/catalog.yaml")
        _commit(source, "Real textparse release fixture")
        _tag(source, "packages/textparse/v0.1.0")
        build_wheel(source / package, tmp_path / "dist")
    else:
        # Failure, never a skip: the required CI leg must supply exact candidate tooling.
        assert os.environ.get("CI") == "true"
        assert " @ git+" in os.environ["CODEGEN_TOOLING_REQUIREMENT"]
    before_backend = _snapshot(product / "services/backend")
    environment = {
        key: value
        for key, value in os.environ.items()
        if key != "PYTHONPATH" and not key.startswith(("GIT_", "KIT_CATALOG_"))
    }

    def run(command: list[str]) -> str:
        result = subprocess.run(
            command, cwd=product, env=environment, capture_output=True, text=True
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return result.stdout

    run(["uv", "sync", "--project", "."])
    tooling_python = product / ".venv/bin/python"
    provenance = {}
    if source_mode == "published_remote":
        tag = "refs/tags/packages/textparse/v0.1.0"
        provenance, package_tree = _published_provenance(run, tag)
        tooling = json.loads(
            run(
                [
                    str(tooling_python),
                    "-I",
                    "-c",
                    "from importlib.metadata import distribution; "
                    "print(distribution('codegen-kit-tooling').read_text('direct_url.json'))",
                ]
            )
        )
        candidate = os.environ["CODEGEN_TOOLING_REQUIREMENT"].rsplit("@", 1)[1]
        assert len(candidate) == 40
        assert tooling["vcs_info"]["commit_id"] == candidate
    installed = run(
        [
            str(tooling_python),
            "-m",
            "framework.cli",
            "add",
            "textparse",
            *(["--catalog-source", str(source)] if source is not None else []),
            "--product-root",
            str(product),
        ]
    )
    assert "textparse 0.1.0 (packages/textparse/v0.1.0)" in installed
    wheel = product / "services/tg_bot/packages/codegen_kit_textparse-0.1.0-py3-none-any.whl"
    _assert_library_wheel(wheel)
    assert _snapshot(product / "services/backend") == before_backend
    tg_bot = product / "services/tg_bot"
    project = tomllib.loads((tg_bot / "pyproject.toml").read_text())
    assert "codegen-kit-textparse" in project["project"]["dependencies"]
    assert (
        project["tool"]["uv"]["sources"]["codegen-kit-textparse"]["path"]
        == "packages/codegen_kit_textparse-0.1.0-py3-none-any.whl"
    )
    lock = tomllib.loads((tg_bot / "uv.lock").read_text())
    locked = {item["name"]: item for item in lock["package"]}
    assert locked["codegen-kit-textparse"]["dependencies"] == [{"name": "tzdata"}]
    assert "tzdata" in locked
    script = textwrap.dedent(
        """
        from datetime import datetime
        from importlib.metadata import distribution
        import json
        from codegen_kit_textparse import when
        library = distribution('codegen-kit-textparse')
        assert library.version == '0.1.0'
        assert library.requires == ['tzdata>=2024.1']
        assert not library.entry_points
        assert not distribution('tzdata').requires
        result = when('Remind me to Buy Milk in 2 minutes', 'en',
                      datetime.fromisoformat('2026-10-07T14:00:00-04:00'), 'America/New_York')
        assert result == {'at': '2026-10-07T14:02:00-04:00', 'rest': 'Buy Milk'}
        print(json.dumps(result))
        """
    )
    result = run([str(tg_bot / ".venv/bin/python"), "-I", "-c", script])
    assert json.loads(result)["rest"] == "Buy Milk"
    image = f"textparse-proof-{tmp_path.name.lower()}"
    try:
        run(["docker", "build", "--file", "services/tg_bot/Dockerfile", "--tag", image, "."])
        result = run(["docker", "run", "--rm", "--entrypoint", "python", image, "-I", "-c", script])
        assert json.loads(result)["rest"] == "Buy Milk"
        if source_mode == "published_remote":
            receipt = {
                "candidate": candidate,
                "catalog_source": DEFAULT_CATALOG_SOURCE,
                "catalog_ref": "HEAD",
                "tag_provenance": provenance,
                "package_tree": package_tree,
                "wheel_path": project["tool"]["uv"]["sources"]["codegen-kit-textparse"]["path"],
                "wheel_sha256": sha256(wheel.read_bytes()).hexdigest(),
                "dependency_closure": ["codegen-kit-textparse==0.1.0", "tzdata"],
                "parsed_output": json.loads(result),
                "backend_unchanged": True,
                "interpreter_passed": True,
                "image_passed": True,
                "run_id": os.environ["GITHUB_RUN_ID"],
            }
            Path(os.environ["CODEGEN_RELEASE_SMOKE_RECEIPT"]).write_text(
                json.dumps(receipt, indent=2) + "\n"
            )
    finally:
        subprocess.run(
            ["docker", "image", "rm", "--force", image], check=False, capture_output=True
        )


def _assert_library_wheel(wheel: Path) -> None:
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        assert "codegen_kit_textparse/__init__.py" in names
        assert any(name.endswith("/licenses/LICENSE") for name in names)
        assert any(name.endswith("/licenses/THIRD_PARTY_NOTICES.md") for name in names)
        metadata = Parser().parsestr(
            archive.read(next(name for name in names if name.endswith("/METADATA"))).decode()
        )
        assert metadata.get_all("Requires-Dist") == ["tzdata>=2024.1"]
        assert not any(name.endswith("/entry_points.txt") for name in names)
        assert not any(name.endswith("/package.yaml") for name in names)


def _published_provenance(run, tag: str) -> tuple[dict[str, str], str]:
    remote = run(["git", "ls-remote", DEFAULT_CATALOG_SOURCE, tag, tag + "^{}"])
    provenance = {ref: sha for sha, ref in (line.split() for line in remote.splitlines())}
    package_tree = run(
        ["git", "-C", str(ROOT), "rev-parse", tag + ":packages/codegen-kit-textparse"]
    ).strip()
    assert package_tree == "9f14cdb2e53a2fd4a34cabd437b9557540254846"
    assert provenance == {
        tag: "4d15b0ce524b1fe90ad363d2fec935874af04e06",
        tag + "^{}": "d68d997fe43a6067bfadf25bd76283141e1fc598",
    }
    return provenance, package_tree
