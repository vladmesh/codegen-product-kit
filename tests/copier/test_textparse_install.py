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
import yaml

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
            _bindings_remote_proof(run, product, tooling_python, tg_bot, candidate)
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


def _bindings_remote_proof(run, product, tooling_python, tg_bot, candidate):
    """Extend the same default-remote lane without local wheel/source substitutions."""
    tag = "refs/tags/packages/reminders/v0.5.0"
    remote = run(["git", "ls-remote", DEFAULT_CATALOG_SOURCE, tag, tag + "^{}"])
    provenance = {ref: sha for sha, ref in (line.split() for line in remote.splitlines())}
    assert provenance == {
        tag: "45a6eca816494f0100bd3ec75d45339eb977e36b",
        tag + "^{}": "2748ffd05a982b9d193f4e43d47f6e4a6ff70a21",
    }
    tree = run(
        ["git", "-C", str(ROOT), "rev-parse", tag + ":packages/codegen-kit-reminders"]
    ).strip()
    assert tree == "55d6cc832dba101e85a4a6053232dacc8513794e"
    installed = run(
        [
            str(tooling_python),
            "-m",
            "framework.cli",
            "add",
            "reminders",
            "--product-root",
            str(product),
        ]
    )
    assert "reminders 0.5.0 (packages/reminders/v0.5.0)" in installed
    run(
        [
            str(tooling_python),
            "-m",
            "framework.cli",
            "bind",
            "reminders",
            "--default",
            "--product-root",
            str(product),
        ]
    )
    run([str(tooling_python), "-m", "framework.generate"])
    backend_python = product / "services/backend/.venv/bin/python"
    installed_evidence = json.loads(
        run(
            [
                str(backend_python),
                "-c",
                "import json; from importlib.metadata import distribution; "
                "from pathlib import Path; "
                "d=distribution('codegen-kit-reminders'); "
                "print(json.dumps({'version': d.version, "
                "'entry_points': [e.name for e in d.entry_points], "
                "'default_resource': Path(d.locate_file("
                "'codegen_kit_reminders/bindings/default.yaml')).read_text()}))",
            ]
        )
    )
    assert installed_evidence["version"] == "0.5.0"
    assert "reminders" in installed_evidence["entry_points"]
    assert (product / "services/tg_bot/bindings/reminders.yaml").read_text() == installed_evidence[
        "default_resource"
    ]
    activation = yaml.safe_load((product / "services/backend/manifest.yaml").read_text())
    assert "reminders" in activation["packages"]
    generated = (product / "codegen_kit/_active_packages.py").read_text()
    assert "reminders" in generated and "0.5.0" in generated
    activation_evidence = json.loads(
        run(
            [
                str(backend_python),
                "-c",
                "import json; from dotenv import load_dotenv; load_dotenv('.env.example'); "
                "from fastapi import FastAPI; "
                "from codegen_kit.packages import configure_generated_packages; "
                "app=FastAPI(); configure_generated_packages(app); "
                "print(json.dumps({'activated': "
                "[p.manifest.name for p in app.state.codegen_packages], "
                "'routes': [r.path for r in app.routes]}))",
            ]
        ).splitlines()[-1]
    )
    assert activation_evidence["activated"] == ["reminders"]
    assert "/reminders" in activation_evidence["routes"]
    assert "/reminders/{reminder_id}" in activation_evidence["routes"]
    typecheck_output = run(["make", "typecheck"])
    evidence = {}
    for name in ("binding_scenarios", "binding_redis_scenarios"):
        script = str(ROOT / f"tests/copier/{name}.py")
        evidence[name] = json.loads(
            run(
                [
                    str(tg_bot / ".venv/bin/python"),
                    "-c",
                    f"import runpy; runpy.run_path({script!r}, run_name='__main__')",
                ]
            ).splitlines()[-1]
        )
    receipt = {
        "candidate": candidate,
        "catalog_source": DEFAULT_CATALOG_SOURCE,
        "tag_provenance": provenance,
        "package_tree": tree,
        "installed_package": installed_evidence,
        "activation": activation["packages"],
        "active_contract": generated,
        "runtime_activation": activation_evidence,
        "library_version": evidence["binding_scenarios"]["library_version"],
        "product_typecheck": {"command": ["make", "typecheck"], "stdout": typecheck_output},
        "generated_bindings_sha256": sha256(
            (product / "services/tg_bot/src/generated/bindings.py").read_bytes()
        ).hexdigest(),
        "evidence": evidence,
        "run_id": os.environ["GITHUB_RUN_ID"],
    }
    Path(os.environ["CODEGEN_BINDINGS_SMOKE_RECEIPT"]).write_text(
        json.dumps(receipt, indent=2) + "\n"
    )
