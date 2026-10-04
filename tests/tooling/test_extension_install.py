"""Extension admission reads installed manifests and distribution metadata before any writes."""

from pathlib import Path
import shutil

import pytest
import yaml

from framework import cli
from framework.catalog import ExtensionPreconditionError, load_catalog, parse_catalog
from framework.spec import package_resolution

ROOT = Path(__file__).parents[2]
CATALOG = ROOT / "tests/fixtures/catalog/components.yaml"


def _parent(site: Path, version: str, *, distribution_version: str | None = None) -> None:
    module = site / "codegen_kit_reminders"
    module.mkdir(parents=True)
    source = ROOT / "packages/codegen-kit-reminders/codegen_kit_reminders/package.yaml"
    manifest = yaml.safe_load(source.read_text())
    manifest["version"] = version
    (module / "package.yaml").write_text(yaml.safe_dump(manifest))
    distribution = site / "codegen_kit_reminders.dist-info"
    distribution.mkdir()
    (distribution / "METADATA").write_text(
        f"Name: codegen-kit-reminders\nVersion: {distribution_version or version}\n"
    )
    (distribution / "entry_points.txt").write_text(
        "[codegen_kit.packages]\nreminders = codegen_kit_reminders:package\n"
    )
    resources = [resource["path"] for resource in manifest.get("resources", [])]
    for resource in resources:
        path = site / resource
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("fixture resource")
    (distribution / "RECORD").write_text(
        "codegen_kit_reminders/package.yaml,,\n"
        + "".join(f"{resource},,\n" for resource in resources)
    )


def _product(root: Path, *, allowlisted: bool) -> None:
    backend = root / "services/backend"
    backend.mkdir(parents=True)
    (backend / "pyproject.toml").write_text(
        '[project]\nname = "backend"\n[tool.deptry.per_rule_ignores]\nDEP002 = ["uvicorn"]\n'
    )
    (backend / "manifest.yaml").write_text(
        yaml.safe_dump(
            {
                "version": 1,
                "packages": ["reminders"] if allowlisted else [],
                "settings_schema": {
                    "$schema": "https://json-schema.org/draft/2020-12/schema",
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
            }
        )
    )
    python = backend / ".venv/bin/python"
    python.parent.mkdir(parents=True)
    python.write_text("fixture interpreter; site discovery is patched")
    # A wheel or lock entry alone cannot establish an installed parent version.
    decoy = backend / "packages/codegen_kit_reminders-0.4.0-py3-none-any.whl"
    decoy.parent.mkdir()
    decoy.write_bytes(b"not installed")
    (root / "uv.lock").write_text("version = 1\n# reminders 0.4.0 is only a decoy\n")


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()
    }


@pytest.mark.parametrize("mode", ["released", "artifact"])
@pytest.mark.parametrize(
    "state",
    ["absent", "uninstalled", "wrong-version", "admitted", "identity-mismatch", "host-only"],
)
def test_extension_precondition_and_complete_recipe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    state: str,
) -> None:
    catalog = load_catalog(CATALOG)
    product = tmp_path / "product"
    _product(product, allowlisted=state != "absent")
    site = tmp_path / "site"
    site.mkdir()
    if state != "uninstalled":
        _parent(
            site,
            "0.3.0" if state == "wrong-version" else "0.4.0",
            distribution_version="0.3.0" if state == "identity-mismatch" else None,
        )
    if state == "host-only":
        shutil.rmtree(product / "services/backend/.venv")
    monkeypatch.setattr(package_resolution, "backend_site_packages", lambda _root: site)
    monkeypatch.setattr(cli, "read_catalog", lambda *_args: catalog)
    monkeypatch.setattr(cli, "bundled_catalog", lambda: catalog)
    artifact = tmp_path / "synthetic_extension-0.1.0-py3-none-any.whl"
    artifact.write_bytes(b"test extension artifact")
    steps: list[str] = []
    commands: list[list[str]] = []

    def fetch(*_args: object) -> Path:
        steps.append("fetch")
        return tmp_path

    def build(*_args: object) -> Path:
        steps.append("build")
        return artifact

    def verify(*_args: object) -> None:
        steps.append("verify")

    def regenerate(root: Path) -> None:
        assert root == product
        steps.append("generate")

    monkeypatch.setattr(cli, "fetch_package_source", fetch)
    monkeypatch.setattr(cli, "build_wheel", build)
    monkeypatch.setattr(cli, "verify_wheel", verify)
    monkeypatch.setattr(cli, "_run", lambda command, _root: commands.append(command))
    monkeypatch.setattr(cli, "generate_all", regenerate)
    before = _snapshot(product)

    def install() -> None:
        if mode == "released":
            version = cli.add_released_package("synthetic-extension", product)
            assert version.version == "0.1.0"
        else:
            cli.add_package("synthetic-extension", artifact, product)

    if state != "admitted":
        expected = {
            "absent": "not allowlisted",
            "uninstalled": "no installed entry point",
            "wrong-version": "installed parent version is 0.3.0",
            "identity-mismatch": "does not match distribution version",
            "host-only": "environment is not installed",
        }[state]
        with pytest.raises(ExtensionPreconditionError, match=expected):
            install()
        assert _snapshot(product) == before
        assert steps == commands == []
    else:
        install()
        assert steps == (
            ["fetch", "build", "verify", "generate"] if mode == "released" else ["generate"]
        )
        assert [command[:2] for command in commands] == [["uv", "add"], ["uv", "sync"]]
        assert (
            product / "services/backend/packages" / artifact.name
        ).read_bytes() == artifact.read_bytes()
        manifest = yaml.safe_load((product / "services/backend/manifest.yaml").read_text())
        assert manifest["packages"] == ["reminders", "synthetic-extension"]
        assert '"synthetic-extension"' in (product / "services/backend/pyproject.toml").read_text()


@pytest.mark.parametrize("artifact", [False, True])
def test_cli_extension_refusal_is_named_and_exits_before_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    artifact: bool,
) -> None:
    catalog = load_catalog(CATALOG)
    product = tmp_path / "product"
    _product(product, allowlisted=False)
    monkeypatch.setattr(cli, "read_catalog", lambda *_args: catalog)
    monkeypatch.setattr(cli, "bundled_catalog", lambda: catalog)
    arguments = ["kit", "add", "synthetic-extension", "--product-root", str(product)]
    if artifact:
        wheel = tmp_path / "synthetic_extension-0.1.0-py3-none-any.whl"
        wheel.write_bytes(b"fixture wheel")
        arguments.extend(["--wheel", str(wheel)])
    monkeypatch.setattr("sys.argv", arguments)
    before = _snapshot(product)
    with pytest.raises(SystemExit, match="1"):
        cli.main()
    error = capsys.readouterr().err
    assert "ExtensionPreconditionError" in error
    assert "not allowlisted" in error
    assert _snapshot(product) == before


@pytest.mark.parametrize("mode", ["released", "artifact"])
@pytest.mark.parametrize(
    "extends", [{"package": "reminders", "versions": ">=0.4,<0.5"}, "not a parent declaration"]
)
def test_misplaced_extension_fails_parsing_before_install_operations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mode: str,
    extends: object,
) -> None:
    document = yaml.safe_load(CATALOG.read_text())
    extension = document["extensions"].pop()
    extension["extends"] = extends
    document["packages"].append(extension)
    source = yaml.safe_dump(document)
    product = tmp_path / "product"
    _product(product, allowlisted=False)
    monkeypatch.setattr(cli, "read_catalog", lambda *_args: parse_catalog(source))
    monkeypatch.setattr(cli, "bundled_catalog", lambda: parse_catalog(source))
    operations: list[str] = []

    def unexpected_operation(*_args: object) -> None:
        operations.append("unexpected operation")

    for operation in (
        "fetch_package_source",
        "build_wheel",
        "verify_wheel",
        "_install_wheel",
        "_run",
        "generate_all",
    ):
        monkeypatch.setattr(cli, operation, unexpected_operation)
    arguments = ["kit", "add", "synthetic-extension", "--product-root", str(product)]
    if mode == "artifact":
        wheel = tmp_path / "synthetic_extension-0.1.0-py3-none-any.whl"
        wheel.write_bytes(b"fixture wheel")
        arguments.extend(["--wheel", str(wheel)])
    monkeypatch.setattr("sys.argv", arguments)
    before = _snapshot(product)
    with pytest.raises(SystemExit, match="1"):
        cli.main()
    assert (
        "InvalidCatalogEntryError: packages[1] 'synthetic-extension' has misplaced 'extends'"
        in capsys.readouterr().err
    )
    assert operations == []
    assert _snapshot(product) == before
