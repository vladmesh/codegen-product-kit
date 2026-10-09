"""`kit check-install`: typed, read-only and deterministic; shared admission for `kit add`."""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import venv
import zipfile

import pytest
import yaml

from framework import cli
from framework.bindings import BindingError
from framework.preflight import (
    EXIT_CODES,
    INCOMPATIBLE_CODES,
    RESULT_VERSION,
    check_install,
    metadata_from_wheel,
)

ROOT = Path(__file__).parents[2]
CHANNELS = ROOT / "packages/codegen-kit-tg-channels"
MODULE = "codegen_kit_tg_channels"
LANGUAGE = """version: 1
settings_schema:
  $schema: https://json-schema.org/draft/2020-12/schema
  type: object
  properties:
    language: {type: string, enum: [ru, en]}
  additionalProperties: false
"""
#: A timezone reference that names the core language key.
TIMEZONE_AS_LANGUAGE = (
    "timezone: {key: language, scope: product, required: true, format: x-iana-tz}\n"
)
CHANNEL_COMMAND = (
    "from services.tg_bot.src.generated.commands import ProductCommand\n\n\n"
    "async def handle_channel(update, context):\n    pass\n\n\n"
    'COMMANDS = (ProductCommand("channel", handle_channel),)\n'
)


def _product(root: Path, core: str = "2.5.0") -> Path:
    for service in ("backend", "tg_bot"):
        directory = root / "services" / service
        directory.mkdir(parents=True)
        # A real, empty product virtualenv: its interpreter symlink leaves the product, the
        # environment does not. Nothing is installed into it.
        venv.EnvBuilder(symlinks=True, with_pip=False).create(directory / ".venv")
        (directory / "pyproject.toml").write_text(f"[project]\nname = '{service}'\n")
    # A valid version 1 manifest: the host contract refuses a malformed one.
    (root / "services/backend/manifest.yaml").write_text(
        "version: 1\nsettings_schema:\n"
        "  $schema: https://json-schema.org/draft/2020-12/schema\n"
        "  type: object\n  properties: {}\n  additionalProperties: false\n"
        "packages: []\n"
    )
    (root / "services/tg_bot/src").mkdir(parents=True)
    shutil.copy2(
        ROOT / "template/services/tg_bot/src/commands.py", root / "services/tg_bot/src/commands.py"
    )
    (root / "codegen_kit").mkdir()
    (root / "codegen_kit/packages.py").write_text(f'CORE_VERSION = "{core}"\n')
    return root


def _state(root: Path) -> dict[str, object]:
    """Every file, including environments: size, mtime and bytes; symlinks by target."""
    state: dict[str, object] = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if path.is_symlink():
            state[relative] = os.readlink(path)
        elif path.is_file():
            stat = path.stat()
            state[relative] = (stat.st_size, stat.st_mtime_ns, path.read_bytes())
    return state


def _check(root: Path, **kwargs):
    before = _state(root)
    result = check_install(root, "tg-channels", package_source=CHANNELS, **kwargs)
    assert _state(root) == before, "preflight wrote to the product"
    json.loads(result.to_json())
    return result


def test_fresh_product_with_published_channels_is_mechanical(tmp_path: Path) -> None:
    result = _check(_product(tmp_path))
    data = result.as_dict()
    assert data["result_version"] == RESULT_VERSION == 1
    assert (data["status"], data["glue"], data["incompatible"]) == ("mechanical", [], None)
    assert data["product_core"] == "2.5.0"
    assert data["target"]["version"] == "0.1.2"
    assert data["target"]["requires_core"] == ">=2.4,<3"
    assert data["target"]["route"] == "package_source"
    assert len(data["target"]["metadata_sha256"]) == 64
    assert result.exit_code == EXIT_CODES["mechanical"] == 0
    assert _check(_product(tmp_path / "again")).to_json() == result.to_json()


def test_language_redeclaration_is_glue_with_its_manifest_source(tmp_path: Path) -> None:
    product = _product(tmp_path)
    (product / "services/tg_bot/manifest.yaml").write_text(LANGUAGE)
    result = _check(product)
    assert result.status == "glue" and result.exit_code == 3
    [item] = result.glue
    assert (item["code"], item["path"], item["line"], item["key"], item["owner"]) == (
        "core_setting_redeclared",
        "services/tg_bot/manifest.yaml",
        6,
        "language",
        "service:tg_bot",
    )
    assert "core" in item["conflict"] and "settings API" in item["action"]


def test_product_channel_command_is_glue_with_both_owners(tmp_path: Path) -> None:
    product = _product(tmp_path)
    (product / "services/tg_bot/src/commands.py").write_text(CHANNEL_COMMAND)
    [item] = _check(product).glue
    assert item["code"] == "command_collision" and item["command"] == "channel"
    assert (item["owner"], item["path"], item["line"], item["symbol"]) == (
        "product",
        "services/tg_bot/src/commands.py",
        8,
        "handle_channel",
    )
    assert item["other"] == {
        "owner": "package:tg-channels",
        "path": "services/tg_bot/bindings/tg-channels.yaml",
        "line": 6,
        "symbol": "text_create",
    }
    assert item["action"].startswith("rename the product command /channel")


def test_simultaneous_conflicts_are_all_reported_in_order(tmp_path: Path) -> None:
    product = _product(tmp_path)
    (product / "services/tg_bot/manifest.yaml").write_text(LANGUAGE)
    (product / "services/tg_bot/src/commands.py").write_text(CHANNEL_COMMAND)
    (product / "services/tg_bot/src/catch_all.py").write_text(
        "from telegram.ext import MessageHandler\n"
    )
    result = _check(product)
    assert [(item["code"], item["path"]) for item in result.glue] == [
        ("core_setting_redeclared", "services/tg_bot/manifest.yaml"),
        ("registration_bypass", "services/tg_bot/src/catch_all.py"),
        ("command_collision", "services/tg_bot/src/commands.py"),
    ]


def _replace_python(product: Path, service: str, content: str) -> None:
    python = product / f"services/{service}/.venv/bin/python"
    python.unlink()
    python.write_text(content)
    python.chmod(0o755)


def _host_venv(product: Path, source: Path, tmp_path: Path) -> None:
    # The whole environment lives outside the product, as a host venv would.
    outside = tmp_path / "host-venv"
    shutil.move(product / "services/tg_bot/.venv", outside)
    (product / "services/tg_bot/.venv").symlink_to(outside)


def _default_binding(source: Path, edit) -> None:
    binding = source / MODULE / "bindings/default.yaml"
    binding.write_text(edit(binding.read_text()))


def _manifest(product: Path, text: str) -> None:
    (product / "services/tg_bot/manifest.yaml").write_text(text)


#: Each case changes the product, or the package source, before the preflight runs.
INCOMPATIBLE_CASES = {
    "core-missing": (
        "core_unsupported",
        lambda p, s, t: (p / "codegen_kit/packages.py").write_text(""),
    ),
    "no-tg-bot": ("product_shape", lambda p, s, t: shutil.rmtree(p / "services/tg_bot")),
    "no-venv": (
        "environment_missing",
        lambda p, s, t: shutil.rmtree(p / "services/tg_bot/.venv"),
    ),
    "host-venv": ("environment_invalid", _host_venv),
    "placeholder": ("environment_invalid", lambda p, s, t: _replace_python(p, "tg_bot", "")),
    "broken-interpreter": (
        "environment_invalid",
        lambda p, s, t: _replace_python(p, "backend", "#!/bin/sh\nexit 3\n"),
    ),
    "manifest-list": ("manifest_invalid", lambda p, s, t: _manifest(p, "- language\n")),
    "manifest-yaml": ("manifest_invalid", lambda p, s, t: _manifest(p, "version: [1\n")),
    "no-metadata": (
        "artifact_unavailable",
        lambda p, s, t: (s / MODULE / "package.yaml").unlink(),
    ),
    "unreadable-metadata": (
        "artifact_unavailable",
        lambda p, s, t: (s / MODULE / "package.yaml").chmod(0),
    ),
    "binding-language": (
        "binding_language_owner",
        lambda p, s, t: _default_binding(
            s, lambda text: text.replace("key: language", "key: product_language")
        ),
    ),
    "default-timezone-language": (
        "binding_setting_conflict",
        lambda p, s, t: _default_binding(s, lambda text: text + TIMEZONE_AS_LANGUAGE),
    ),
}
#: Cases that change the request instead: package name, version or provenance.
REQUEST_CASES = {
    "core-old": ("core_range", {}),
    "no-provenance": ("provenance_required", {"package_source": None}),
    "both-provenances": ("provenance_required", {"catalog_ref": "HEAD", "catalog": "self"}),
    "other-name": ("package_mismatch", {"name": "reminders"}),
    "other-version": ("package_mismatch", {"version": "0.1.1"}),
    "catalog-unreachable": (
        "catalog_unavailable",
        {"package_source": None, "catalog_ref": "HEAD", "catalog": "missing"},
    ),
}


@pytest.mark.parametrize("change", [*INCOMPATIBLE_CASES, *REQUEST_CASES])
def test_incompatible_results_carry_a_stable_reason(tmp_path: Path, change: str) -> None:
    product = _product(tmp_path / "product", "2.3.0" if change == "core-old" else "2.5.0")
    source = tmp_path / "source"
    shutil.copytree(CHANNELS, source, ignore=shutil.ignore_patterns("__pycache__"))
    request: dict = {"name": "tg-channels", "package_source": source}
    if change in INCOMPATIBLE_CASES:
        code, apply = INCOMPATIBLE_CASES[change]
        apply(product, source, tmp_path)
    else:
        code, overrides = REQUEST_CASES[change]
        request |= overrides
        catalog = request.pop("catalog", None)
        if catalog is not None:
            request["catalog_source"] = str(
                tmp_path / catalog if catalog == "missing" else tmp_path
            )
    assert code in INCOMPATIBLE_CODES
    before = _state(product)
    result = check_install(product, request.pop("name"), **request)
    assert _state(product) == before
    assert result.status == "incompatible" and result.exit_code == 4, result.to_json()
    assert result.incompatible is not None and result.incompatible["code"] == code
    assert result.incompatible["explanation"] and result.glue == []
    json.loads(result.to_json())


def _git(*arguments: str, cwd: Path) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-c", "user.name=Kit", "-c", "user.email=kit@example.com", *arguments],  # noqa: S607
        cwd=cwd,
        check=True,
        capture_output=True,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
    )


def test_catalog_route_reads_the_exact_tag_without_a_live_default(tmp_path: Path) -> None:
    source = tmp_path / "kit"
    (source / "packages").mkdir(parents=True)
    shutil.copy2(ROOT / "packages/catalog.yaml", source / "packages/catalog.yaml")
    shutil.copytree(
        CHANNELS,
        source / "packages/codegen-kit-tg-channels",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    _git("init", "--quiet", "--initial-branch=main", cwd=source)
    _git("add", "-A", cwd=source)
    _git("commit", "--quiet", "-m", "kit", cwd=source)
    _git("tag", "--annotate", "packages/tg-channels/v0.1.2", "-m", "0.1.2", cwd=source)
    product = _product(tmp_path / "product")
    before = _state(product)
    result = check_install(product, "tg-channels", catalog_source=str(source), catalog_ref="HEAD")
    assert _state(product) == before
    assert result.status == "mechanical", result.to_json()
    assert result.target is not None
    assert (result.target["route"], result.target["tag"], result.target["version"]) == (
        "catalog",
        "packages/tg-channels/v0.1.2",
        "0.1.2",
    )
    old = check_install(
        product, "tg-channels", catalog_source=str(source), catalog_ref="HEAD", version="0.1.1"
    )
    assert old.incompatible is not None and old.incompatible["code"] == "artifact_unavailable"
    library = check_install(product, "textparse", catalog_source=str(source), catalog_ref="HEAD")
    assert library.incompatible is not None
    assert library.incompatible["code"] == "unsupported_component"


def test_cli_prints_the_versioned_payload_and_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    product = _product(tmp_path)
    (product / "services/tg_bot/manifest.yaml").write_text(LANGUAGE)
    argv = ["kit", "check-install", "tg-channels", "--json", "--product-root", str(product)]
    monkeypatch.setattr("sys.argv", [*argv, "--package-source", str(CHANNELS)])
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == 3
    payload = json.loads(capsys.readouterr().out)
    assert (payload["status"], payload["glue"][0]["code"]) == ("glue", "core_setting_redeclared")
    monkeypatch.setattr("sys.argv", argv)
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == 4
    assert json.loads(capsys.readouterr().out)["incompatible"]["code"] == "provenance_required"


def _wheel(tmp_path: Path) -> Path:
    wheel = tmp_path / "codegen_kit_tg_channels-0.1.2-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for relative in ("package.yaml", "bindings/default.yaml"):
            archive.write(CHANNELS / MODULE / relative, f"{MODULE}/{relative}")
    return wheel


def test_wheel_metadata_is_read_as_data(tmp_path: Path) -> None:
    metadata = metadata_from_wheel("tg-channels", _wheel(tmp_path))
    assert metadata.manifest.version == "0.1.2"
    assert metadata.binding is not None and metadata.binding.package == "tg-channels"
    assert metadata.provenance["route"] == "wheel"


@pytest.mark.parametrize("conflict", ["language", "command"])
def test_kit_add_refuses_named_conflicts_before_any_product_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, conflict: str
) -> None:
    product = _product(tmp_path / "product")
    if conflict == "language":
        (product / "services/tg_bot/manifest.yaml").write_text(LANGUAGE)
    else:
        (product / "services/tg_bot/src/commands.py").write_text(CHANNEL_COMMAND)
    wheel = _wheel(tmp_path)
    calls: list[object] = []
    monkeypatch.setattr(cli, "_run", lambda command, root: calls.append(command))
    monkeypatch.setattr(cli, "generate_all", lambda root: calls.append(root))
    before = _state(product)
    with pytest.raises(BindingError, match="HostContractError"):
        cli.add_package("tg-channels", wheel, product)
    assert calls == [] and _state(product) == before
    manifest = yaml.safe_load((product / "services/backend/manifest.yaml").read_text())
    assert manifest["packages"] == []


def _retain_default(product: Path, edit) -> Path:
    """The product keeps its own copy of the binding, as `kit bind --file` leaves it."""
    retained = product / "services/tg_bot/bindings/tg-channels.yaml"
    retained.parent.mkdir(parents=True, exist_ok=True)
    retained.write_text(edit((CHANNELS / MODULE / "bindings/default.yaml").read_text()))
    return retained


@pytest.mark.parametrize(
    ("edit", "code", "line"),
    [
        (lambda text: text + TIMEZONE_AS_LANGUAGE, "binding_setting_conflict", 71),
        (
            lambda text: text.replace("key: language", "key: product_language"),
            "binding_language_owner",
            3,
        ),
    ],
)
def test_retained_binding_language_conflict_is_glue_at_its_file(
    tmp_path: Path, edit, code: str, line: int
) -> None:
    product = _product(tmp_path)
    _retain_default(product, edit)
    [item] = _check(product).glue
    assert (item["code"], item["owner"], item["path"], item["line"]) == (
        code,
        "product",
        "services/tg_bot/bindings/tg-channels.yaml",
        line,
    )
    assert item["action"].startswith("edit services/tg_bot/bindings/tg-channels.yaml")


def test_retained_binding_is_validated_instead_of_the_default(tmp_path: Path) -> None:
    product = _product(tmp_path)
    _retain_default(product, lambda text: text.replace("tg-channels.add", "tg-channels.missing"))
    result = _check(product)
    assert result.incompatible is not None and result.incompatible["code"] == "binding_invalid"
    assert (
        "retained services/tg_bot/bindings/tg-channels.yaml" in result.incompatible["explanation"]
    )


def test_cli_malformed_manifest_is_typed_json_not_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    product = _product(tmp_path)
    (product / "services/tg_bot/manifest.yaml").write_text("- language\n")
    monkeypatch.setattr(
        "sys.argv",
        [
            "kit",
            "check-install",
            "tg-channels",
            "--json",
            "--package-source",
            str(CHANNELS),
            "--product-root",
            str(product),
        ],
    )
    with pytest.raises(SystemExit) as exited:
        cli.main()
    assert exited.value.code == 4
    payload = json.loads(capsys.readouterr().out)
    assert payload["result_version"] == 1
    assert payload["incompatible"]["code"] == "manifest_invalid"


def test_kit_add_refuses_a_retained_binding_conflict_before_any_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    product = _product(tmp_path / "product")
    _retain_default(product, lambda text: text + TIMEZONE_AS_LANGUAGE)
    wheel = _wheel(tmp_path)
    calls: list[object] = []
    monkeypatch.setattr(cli, "_run", lambda command, root: calls.append(command))
    monkeypatch.setattr(cli, "generate_all", lambda root: calls.append(root))
    before = _state(product)
    with pytest.raises(BindingError, match="binding_setting_conflict"):
        cli.add_package("tg-channels", wheel, product)
    assert calls == [] and _state(product) == before


def test_kit_add_prepares_a_missing_environment_but_refuses_a_foreign_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Normal add creates a missing service environment; one that exists must be the product's."""
    product = _product(tmp_path / "product")
    shutil.rmtree(product / "services/backend/.venv")
    wheel = _wheel(tmp_path)
    installs: list[Path] = []
    monkeypatch.setattr(cli, "_install_wheel", lambda package, path, root: installs.append(path))
    cli.add_package("tg-channels", wheel, product)
    assert installs == [wheel]

    _host_venv(product, tmp_path, tmp_path)
    before = _state(product)
    with pytest.raises(BindingError, match="tg_bot must own a product virtualenv"):
        cli.add_package("tg-channels", wheel, product)
    assert installs == [wheel] and _state(product) == before
