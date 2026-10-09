"""Core host contract in real generated products: preflight matrix, glue, registry runtime."""

import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest
import yaml

from framework import cli, host_contract
from framework.cli import bind_package
from framework.generate import generate_all
from framework.host_contract import HostContractError
from framework.package_source import build_wheel
from framework.preflight import check_install
from tests.copier.test_bindings import (
    test_bound_product_generation_passes_its_own_drift_and_lint as check_product_lint,
)

ROOT = Path(__file__).parents[2]
CHANNELS = ROOT / "packages/codegen-kit-tg-channels"
LANGUAGE = {"type": "string", "enum": ["ru", "en"]}
PRODUCT_COMMANDS = '''"""Product-owned Telegram commands."""

from __future__ import annotations

from telegram import Update
from telegram.ext import ContextTypes

from services.tg_bot.src.generated.commands import ProductCommand


async def handle_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        await update.message.reply_text(f"product {COMMANDS[0].name}")


COMMANDS: tuple[ProductCommand, ...] = (ProductCommand("channel", handle_channel),)
'''


def _files(root: Path) -> dict[str, object]:
    """Tracked and untracked product bytes, plus environment and lock file identities."""
    state: dict[str, object] = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = str(path.relative_to(root))
        if ".venv" in path.parts:
            stat = path.stat()
            state[relative] = (stat.st_size, stat.st_mtime_ns)
        else:
            state[relative] = path.read_bytes()
    return state


@pytest.fixture(scope="module")
def fresh_bot_product(project_backend_tg_bot, tmp_path_factory):
    product = tmp_path_factory.mktemp("host-product") / "product"
    # The shared fixture may already hold environments or artifacts other tests created.
    shutil.copytree(
        project_backend_tg_bot,
        product,
        ignore=shutil.ignore_patterns(".venv", "artifacts", "__pycache__"),
    )
    for service in ("backend", "tg_bot"):
        result = subprocess.run(
            ["uv", "sync", "--project", f"services/{service}", "--frozen"],
            cwd=product,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    return product


def _declare_conflicts(product: Path, *, language: bool = True, command: bool = True) -> None:
    if language:
        manifest = product / "services/tg_bot/manifest.yaml"
        manifest.write_text(
            yaml.safe_dump(
                {
                    "version": 1,
                    "settings_schema": {
                        "$schema": "https://json-schema.org/draft/2020-12/schema",
                        "type": "object",
                        "properties": {"language": LANGUAGE},
                        "additionalProperties": False,
                    },
                },
                sort_keys=False,
            )
        )
    if command:
        (product / "services/tg_bot/src/commands.py").write_text(PRODUCT_COMMANDS)


def _apply_glue(product: Path, glue: list[dict]) -> None:
    """Apply each returned action through the product author's ordinary files and API."""
    for item in glue:
        path = product / item["path"]
        if item["code"] == "core_setting_redeclared":
            data = yaml.safe_load(path.read_text())
            del data["settings_schema"]["properties"][item["key"]]
            path.write_text(yaml.safe_dump(data, sort_keys=False))
        else:
            assert item["code"] == "command_collision" and item["owner"] == "product"
            source = path.read_text()
            old = f'ProductCommand("{item["command"]}"'
            assert old in source and item["path"] in item["action"]
            path.write_text(source.replace(old, f'ProductCommand("my{item["command"]}"'))
    generate_all(product)  # The author's `make generate-from-spec`.


def _preflight(product: Path):
    before = _files(product)
    result = check_install(product, "tg-channels", package_source=CHANNELS)
    assert _files(product) == before, "check-install wrote to the product"
    return result


def test_preflight_matrix_in_a_fresh_generated_product(fresh_bot_product):
    product = fresh_bot_product
    commands = product / "services/tg_bot/src/commands.py"
    core = product / "codegen_kit/packages.py"
    originals = {path: path.read_bytes() for path in (commands, core)}
    manifest = product / "services/tg_bot/manifest.yaml"
    assert not manifest.exists()
    try:
        fresh = _preflight(product)
        assert fresh.status == "mechanical" and fresh.glue == []
        assert fresh.target["version"] == "0.1.2" and fresh.product_core == "2.5.0"
        assert host_contract.check_product(product).settings == {"language": "core"}

        _declare_conflicts(product, command=False)
        [language] = _preflight(product).glue
        assert (language["code"], language["path"], language["key"]) == (
            "core_setting_redeclared",
            "services/tg_bot/manifest.yaml",
            "language",
        )
        manifest.unlink()

        _declare_conflicts(product, language=False)
        [command] = _preflight(product).glue
        assert command["code"] == "command_collision" and command["command"] == "channel"
        assert command["owner"] == "product"
        assert command["path"] == str(commands.relative_to(product))
        assert command["other"]["owner"] == "package:tg-channels"

        _declare_conflicts(product)
        both = _preflight(product)
        assert both.status == "glue"
        assert {item["code"] for item in both.glue} == {
            "core_setting_redeclared",
            "command_collision",
        }

        old_core = core.read_text().replace('CORE_VERSION = "2.5.0"', 'CORE_VERSION = "2.3.0"')
        core.write_text(old_core)
        incompatible = _preflight(product)
        assert incompatible.status == "incompatible"
        assert incompatible.incompatible["code"] == "core_range"
    finally:
        manifest.unlink(missing_ok=True)
        for path, content in originals.items():
            path.write_bytes(content)


def _install_channels(product: Path) -> None:
    result = subprocess.run(
        [
            "uv",
            "pip",
            "install",
            "--python",
            str(product / "services/backend/.venv/bin/python"),
            str(CHANNELS),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = product / "services/backend/manifest.yaml"
    data = yaml.safe_load(manifest.read_text())
    data["packages"].append("tg-channels")
    manifest.write_text(yaml.safe_dump(data, sort_keys=False))


def _run_registry_scenarios(product: Path) -> None:
    for locale in ("ru", "en"):
        result = subprocess.run(
            [
                str(product / "services/tg_bot/.venv/bin/python"),
                str(ROOT / "tests/copier/command_registry_scenarios.py"),
                locale,
            ],
            cwd=product,
            env=os.environ | {"PYTHONPATH": f"{product}:{product / 'shared'}"},
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert f"registry {locale}: commands, module binding and unknown fallback" in result.stdout


def test_applied_glue_makes_install_mechanical_and_product_ci_green(fresh_bot_product, tmp_path):
    product = tmp_path / "glued"
    shutil.copytree(fresh_bot_product, product, symlinks=True)
    _declare_conflicts(product)
    glue = _preflight(product)
    assert glue.status == "glue" and len(glue.glue) == 2
    with pytest.raises(HostContractError):
        generate_all(product)  # The same conflicts fail the product's generation.
    _apply_glue(product, glue.glue)
    assert _preflight(product).status == "mechanical"

    _install_channels(product)
    generate_all(product)
    assert bind_package("tg-channels", product) == "bound"
    manifest = yaml.safe_load((product / "services/tg_bot/manifest.yaml").read_text())
    assert "language" not in manifest["settings_schema"]["properties"]
    registry = host_contract.check_product(product)
    assert [(item.command, item.owner) for item in registry.commands] == [
        ("start", "core"),
        ("command", "core"),
        ("mychannel", "product"),
        ("channel", "package:tg-channels"),
        ("channels", "package:tg-channels"),
        ("digest", "package:tg-channels"),
    ]
    settings = (product / "services/backend/src/generated/settings_schemas.py").read_text()
    assert "'language': 'core'" in settings or '"language": "core"' in settings
    _run_registry_scenarios(product)
    unit = subprocess.run(
        [str(product / "services/tg_bot/.venv/bin/pytest"), "services/tg_bot/tests/unit", "-q"],
        cwd=product,
        env=os.environ
        | {
            "PYTHONPATH": f"{product}:{product / 'shared'}",
            "REDIS_URL": "redis://redis.invalid:6379",
        },
        capture_output=True,
        text=True,
    )
    assert unit.returncode == 0, unit.stdout + unit.stderr
    lint_root = tmp_path / "lint"
    lint_root.mkdir()
    check_product_lint(
        SimpleNamespace(getfixturevalue=lambda fixture: product), "channel_product", lint_root
    )


TIMEZONE_AS_LANGUAGE = (
    "timezone: {key: language, scope: product, required: true, format: x-iana-tz}\n"
)


def test_retained_binding_conflict_refuses_writes_then_glue_installs(fresh_bot_product, tmp_path):
    """A product-kept binding naming core language as its timezone: glue, then normal install."""
    product = tmp_path / "retained"
    shutil.copytree(fresh_bot_product, product, symlinks=True)
    binding = product / "services/tg_bot/bindings/tg-channels.yaml"
    binding.parent.mkdir(parents=True)
    default = (CHANNELS / "codegen_kit_tg_channels/bindings/default.yaml").read_text()
    binding.write_text(default + TIMEZONE_AS_LANGUAGE)

    [item] = _preflight(product).glue
    assert (item["code"], item["owner"], item["path"], item["key"]) == (
        "binding_setting_conflict",
        "product",
        "services/tg_bot/bindings/tg-channels.yaml",
        "language",
    )
    assert item["line"] == len(default.splitlines()) + 1
    _install_channels(product)
    before = _files(product)
    with pytest.raises(HostContractError, match="binding_setting_conflict"):
        generate_all(product)
    with pytest.raises(HostContractError, match="binding_setting_conflict"):
        bind_package("tg-channels", product, binding_file=binding)
    assert _files(product) == before

    binding.write_text(default)  # The glue action: give timezone no core key (none needed).
    assert _preflight(product).status == "mechanical"
    generate_all(product)
    assert bind_package("tg-channels", product) == "unchanged"
    assert host_contract.check_product(product).violations == []


@pytest.mark.parametrize(
    ("path", "source"),
    [
        (
            "services/tg_bot/src/main.py",
            "\nfrom telegram.ext import CommandHandler as _Handler  # noqa: E402\n",
        ),
        (
            "services/tg_bot/src/catch_all.py",
            "from telegram.ext import MessageHandler, filters\n\n"
            "def install(application):\n"
            "    application.add_handler(MessageHandler(filters.ALL, print))\n",
        ),
    ],
)
def test_generation_and_lint_fail_closed_on_bypass_before_writes(fresh_bot_product, path, source):
    product = fresh_bot_product
    target = product / path
    original = target.read_bytes() if target.exists() else None
    try:
        target.write_text((original or b"").decode() + source)
        before = _files(product)
        with pytest.raises(HostContractError, match="registration_bypass"):
            generate_all(product)
        assert _files(product) == before
        lint = subprocess.run(
            [sys.executable, "-m", "framework.host_contract"],
            cwd=product,
            capture_output=True,
            text=True,
        )
        assert lint.returncode == 1 and f"{path}:" in lint.stderr, lint.stderr
    finally:
        if original is None:
            target.unlink()
        else:
            target.write_bytes(original)


def test_standalone_bot_lints_and_regenerates_its_registry(project_standalone, tmp_path):
    product = tmp_path / "standalone"
    shutil.copytree(project_standalone, product)
    makefile = (product / "Makefile").read_text()
    assert "$(PYTHON) -m framework.host_contract --write" in makefile
    assert "\t$(PYTHON) -m framework.host_contract\n" in makefile
    assert host_contract.main(["--root", str(product)]) == 0
    (product / "services/tg_bot/src/commands.py").write_text(
        PRODUCT_COMMANDS.replace('"channel"', '"ping"')
    )
    assert host_contract.main(["--root", str(product)]) == 1  # Registry is stale.
    assert host_contract.main(["--root", str(product), "--write"]) == 0
    assert [item.command for item in host_contract.check_product(product).commands] == [
        "start",
        "ping",
    ]


@pytest.mark.slow
def test_glue_then_real_wheel_install_bind_and_registry(fresh_bot_product, tmp_path):
    """CI only: the normal `kit add --wheel` (uv add/sync, generation) after applied glue."""
    product = tmp_path / "product"
    shutil.copytree(fresh_bot_product, product, symlinks=True)
    _declare_conflicts(product)
    wheel = build_wheel(CHANNELS, tmp_path / "dist")
    before = _files(product)
    with pytest.raises(HostContractError):
        cli.add_package("tg-channels", wheel, product)
    assert _files(product) == before
    _apply_glue(product, _preflight(product).glue)
    assert _preflight(product).status == "mechanical"
    cli.add_package("tg-channels", wheel, product)
    assert bind_package("tg-channels", product) == "bound"
    assert host_contract.check_product(product).violations == []
    _run_registry_scenarios(product)
