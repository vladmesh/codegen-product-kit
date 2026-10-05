"""Library catalog agreement, local tag resolution and pre-write artifact refusals."""

import ast
from dataclasses import replace
from pathlib import Path
import shutil
import sys
import tomllib
import zipfile

import pytest

from framework import cli
from framework.catalog import IncompatibleCatalogVersionError, load_catalog
from framework.component_matching import primary_output_matches
from framework.package_source import (
    PackageNotPublishedError,
    PackageWheelMismatchError,
    verify_wheel,
)
from tests.tooling.test_package_catalog import _commit, _git, _snapshot, _tag

ROOT = Path(__file__).parents[2]
LIBRARY = load_catalog(ROOT / "packages/catalog.yaml").libraries[0]


def test_source_catalog_signature_and_dependency_boundary() -> None:
    project = tomllib.loads((ROOT / LIBRARY.path / "pyproject.toml").read_text())["project"]
    source = ROOT / LIBRARY.path / LIBRARY.module / "__init__.py"
    module = ast.parse(source.read_text())
    functions = {node.name: node for node in module.body if isinstance(node, ast.FunctionDef)}
    versions = [
        node.value.value
        for node in module.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and node.targets[0].id == "__version__"
    ]
    assert versions == ["0.1.0"]
    assert {node.module for node in module.body if isinstance(node, ast.ImportFrom)} == {
        "datetime",
        "typing",
        "zoneinfo",
    }
    assert {
        alias.name for node in module.body if isinstance(node, ast.Import) for alias in node.names
    } == {"re"}
    [function] = LIBRARY.functions
    assert function.name == "when"
    assert [arg.arg for arg in functions["when"].args.args] == ["text", "lang", "now", "tz"]
    assert list(function.input["properties"]) == ["text", "lang", "now", "tz"]
    assert function.input["required"] == ["text", "lang", "now", "tz"]
    assert function.output["type"] == ["object", "null"]
    assert function.output["required"] == ["at", "rest"]
    assert function.value == "at"
    version = LIBRARY.select("3.12.0")
    assert project["name"] == LIBRARY.distribution == "codegen-kit-textparse"
    assert project["version"] == version.version == "0.1.0"
    assert project["requires-python"] == version.requires_python == ">=3.11"
    assert version.tag == "packages/textparse/v0.1.0"
    assert project["dependencies"] == ["tzdata>=2024.1"]
    assert "entry-points" not in project
    assert not list((ROOT / LIBRARY.path).rglob("package.yaml"))
    reminders = load_catalog(ROOT / "packages/catalog.yaml").get("reminders")
    parameter = reminders.actions[0].input["properties"]["remind_at"]
    assert primary_output_matches(function, parameter)
    assert reminders.recommended_with[0].library == "textparse"
    assert [action.name for action in reminders.actions] == ["create", "list", "cancel"]
    assert reminders.default_binding == "codegen_kit_reminders:bindings/default.yaml"


def test_library_selection_uses_python_and_stable_version_order() -> None:
    current = LIBRARY.versions[0]
    library = replace(
        LIBRARY,
        versions=(
            current,
            replace(current, version="0.2.0", requires_python=">=3.13"),
            replace(current, version="0.3.0rc1"),
        ),
    )
    assert library.select("3.12.0").version == "0.1.0"
    assert library.select("3.13.0").version == "0.2.0"
    with pytest.raises(IncompatibleCatalogVersionError, match="Python 3.10"):
        library.select("3.10.0")


def _wheel(
    directory: Path,
    *,
    name: str = "codegen-kit-textparse",
    version: str = "0.1.0",
    requires_python: str = ">=3.11",
    module: str = "codegen_kit_textparse/__init__.py",
    entry_points: str = "",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    wheel = directory / "codegen_kit_textparse-0.1.0-py3-none-any.whl"
    info = "codegen_kit_textparse-0.1.0.dist-info"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            f"{info}/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n"
            f"Requires-Python: {requires_python}\n",
        )
        archive.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nTag: py3-none-any\n")
        archive.writestr(f"{info}/RECORD", "")
        archive.writestr(f"{info}/entry_points.txt", entry_points)
        archive.writestr(module, "# Import layout fixture; never executed.\n")
    return wheel


@pytest.fixture
def product(tmp_path: Path) -> Path:
    root = tmp_path / "product"
    service = root / "services/tg_bot"
    (service / ".venv/bin").mkdir(parents=True)
    (service / ".venv/bin/python").symlink_to(sys.executable)
    (service / "pyproject.toml").write_text('[project]\nname="tg-bot"\nrequires-python=">=3.11"\n')
    backend = root / "services/backend"
    backend.mkdir()
    (backend / "manifest.yaml").write_text("version: 1\npackages: []\n")
    (backend / "pyproject.toml").write_text('[project]\nname="backend"\n')
    return root


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    recorded: list[list[str]] = []
    monkeypatch.setattr(cli, "_run", lambda command, _root: recorded.append(command))
    monkeypatch.setattr(
        cli, "generate_all", lambda _root: pytest.fail("libraries must not generate")
    )
    return recorded


@pytest.fixture
def source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    root.mkdir()
    _git("init", "--quiet", cwd=root)
    shutil.copytree(
        ROOT / LIBRARY.path, root / LIBRARY.path, ignore=shutil.ignore_patterns("__pycache__")
    )
    shutil.copy2(ROOT / "packages/catalog.yaml", root / "packages/catalog.yaml")
    _commit(root, "Library fixture 0.1.0")
    _tag(root, "packages/textparse/v0.1.0")
    # Catalog HEAD differs from the tag: source must come from the independent release.
    project = root / LIBRARY.path / "pyproject.toml"
    project.write_text(project.read_text().replace('version = "0.1.0"', 'version = "9.0.0"'))
    _commit(root, "Unreleased library development")
    _git("branch", "catalog-test", cwd=root)
    return root


def test_released_library_fetches_exact_tag_and_targets_only_tg_bot(
    source: Path,
    product: Path,
    commands: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = _snapshot(product / "services/backend")

    def build(project: Path, output: Path) -> Path:
        metadata = tomllib.loads((project / "pyproject.toml").read_text())["project"]
        assert metadata["version"] == "0.1.0"
        assert (project / LIBRARY.module / "__init__.py").is_file()
        return _wheel(output)

    monkeypatch.setattr(cli, "build_wheel", build)
    monkeypatch.setenv("KIT_CATALOG_SOURCE", str(source))
    monkeypatch.setenv("KIT_CATALOG_REF", "refs/heads/catalog-test")
    monkeypatch.setattr(sys, "argv", ["kit", "add", "textparse", "--product-root", str(product)])
    cli.main()
    artifact = "codegen_kit_textparse-0.1.0-py3-none-any.whl"
    assert (product / "services/tg_bot/packages" / artifact).is_file()
    assert commands == [
        [
            "uv",
            "add",
            "--project",
            "services/tg_bot",
            "--no-sync",
            f"services/tg_bot/packages/{artifact}",
        ],
        ["uv", "sync", "--project", "services/tg_bot", "--frozen"],
    ]
    assert _snapshot(product / "services/backend") == before


def test_explicit_library_wheel_needs_no_backend(
    tmp_path: Path,
    product: Path,
    commands: list[list[str]],
) -> None:
    shutil.rmtree(product / "services/backend")
    wheel = _wheel(tmp_path / "wheels")
    cli.add_package("textparse", wheel, product)
    assert commands[-1] == ["uv", "sync", "--project", "services/tg_bot", "--frozen"]
    assert not (product / "services/backend").exists()


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"name": "other"}, "distribution"),
        ({"version": "9.0.0"}, "version"),
        ({"requires_python": ">=9"}, "Python"),
        ({"requires_python": "latest"}, "metadata"),
        ({"requires_python": ""}, "Requires-Python"),
        ({"module": "different/__init__.py"}, "import module"),
        (
            {"module": "codegen_kit_textparse.data/purelib/codegen_kit_textparse/__init__.py"},
            "import module",
        ),
        (
            {"entry_points": "[codegen_kit.packages]\ntextparse = codegen_kit_textparse:when\n"},
            "must not declare",
        ),
        ({"entry_points": "[invalid"}, "valid wheel"),
    ],
)
def test_explicit_wheel_refusals_preserve_all_product_files(
    changes: dict[str, str],
    message: str,
    tmp_path: Path,
    product: Path,
    commands: list[list[str]],
) -> None:
    before = _snapshot(product)
    wheel = _wheel(tmp_path / "wheels", **changes)
    with pytest.raises(PackageWheelMismatchError, match=message):
        cli.add_package("textparse", wheel, product)
    assert _snapshot(product) == before
    assert commands == []


@pytest.mark.parametrize("failure", ["absent-tg-bot", "python", "unpublished", "bad-build"])
def test_resolution_refusals_do_not_write(
    failure: str,
    source: Path,
    product: Path,
    commands: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected: type[Exception] = ValueError
    if failure == "absent-tg-bot":
        shutil.rmtree(product / "services/tg_bot")
    elif failure == "python":
        monkeypatch.setattr(cli, "_library_python_version", lambda _root: "3.10.0")
        expected = IncompatibleCatalogVersionError
    elif failure == "unpublished":
        _git("tag", "--delete", "packages/textparse/v0.1.0", cwd=source)
        expected = PackageNotPublishedError
    else:
        monkeypatch.setattr(
            cli, "build_wheel", lambda _project, output: _wheel(output, version="9.0.0")
        )
        expected = PackageWheelMismatchError
    before = _snapshot(product)
    with pytest.raises(expected):
        cli.add_released_package("textparse", product, str(source), "refs/heads/catalog-test")
    assert _snapshot(product) == before
    assert commands == []


@pytest.mark.parametrize("failure", ["zip", "filename", "undeclared", "tags", "record", "manifest"])
def test_malformed_artifact_is_named_before_writes(
    failure: str,
    tmp_path: Path,
    product: Path,
    commands: list[list[str]],
) -> None:
    wheel = _wheel(tmp_path / "wheels")
    if failure == "zip":
        wheel.write_bytes(b"not a zip")
    elif failure in ("filename", "undeclared", "tags"):
        names = {
            "filename": "other-0.1.0-py3-none-any.whl",
            "undeclared": "codegen_kit_textparse-0.2.0-py3-none-any.whl",
            "tags": "codegen_kit_textparse-0.1.0-cp39-cp39-win_amd64.whl",
        }
        wheel = wheel.rename(wheel.with_name(names[failure]))
    elif failure == "record":
        with zipfile.ZipFile(wheel) as archive:
            entries = {
                name: archive.read(name)
                for name in archive.namelist()
                if not name.endswith("/RECORD")
            }
        with zipfile.ZipFile(wheel, "w") as archive:
            for name, data in entries.items():
                archive.writestr(name, data)
    else:
        with zipfile.ZipFile(wheel, "a") as archive:
            archive.writestr("codegen_kit_textparse/package.yaml", "version: 1")
    before = _snapshot(product)
    with pytest.raises(PackageWheelMismatchError):
        cli.add_package("textparse", wheel, product)
    assert _snapshot(product) == before
    assert commands == []


def test_verifier_requires_target_python(tmp_path: Path) -> None:
    wheel = _wheel(tmp_path)
    with pytest.raises(PackageWheelMismatchError, match="target Python"):
        verify_wheel(wheel, LIBRARY, LIBRARY.versions[0])


def test_explicit_install_without_tg_bot_refuses_before_interpreter_or_writes(
    tmp_path: Path,
    product: Path,
    commands: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shutil.rmtree(product / "services/tg_bot")
    before = _snapshot(product)
    monkeypatch.setattr(cli, "_library_python_version", lambda _root: pytest.fail("no service"))
    with pytest.raises(ValueError, match="libraries target tg_bot"):
        cli.add_package("textparse", _wheel(tmp_path / "wheels"), product)
    assert _snapshot(product) == before
    assert commands == []


def test_library_resolution_respects_an_unreachable_ref_without_writes(
    source: Path,
    product: Path,
    commands: list[list[str]],
) -> None:
    before = _snapshot(product)
    with pytest.raises(ValueError, match="CatalogSourceUnreachableError"):
        cli.add_released_package("textparse", product, str(source), "refs/heads/not-present")
    assert _snapshot(product) == before
    assert commands == []
