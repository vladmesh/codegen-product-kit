"""Core host contract: core settings ownership, one command registry and its hard lint."""

from __future__ import annotations

from pathlib import Path
import shutil

from jinja2 import Environment
import pytest
import yaml

from framework import host_contract
from framework.bindings import BindingError, load_binding
from framework.host_contract import (
    HostContractError,
    check_product,
    evaluate,
    registry_violation,
    render_registry,
    write_registry,
)
from framework.spec.core_settings import CORE_SETTINGS
from framework.spec.loader import SpecValidationError, load_specs

ROOT = Path(__file__).parents[2]
TEMPLATE_REGISTRY = ROOT / "template/services/tg_bot/src/generated/commands.py.jinja"
PRODUCT_MODULE = ROOT / "template/services/tg_bot/src/commands.py"
CHANNELS_BINDING = (
    ROOT / "packages/codegen-kit-tg-channels/codegen_kit_tg_channels/bindings/default.yaml"
)
MANIFEST = """version: 1
settings_schema:
  $schema: https://json-schema.org/draft/2020-12/schema
  type: object
  properties:
    unrelated: {type: integer}
    language: {type: string, enum: [ru, en]}
  additionalProperties: false
"""


def _product(root: Path, *, backend: bool = True) -> Path:
    bot = root / "services/tg_bot/src"
    bot.mkdir(parents=True)
    shutil.copy2(PRODUCT_MODULE, bot / "commands.py")
    (bot / "main.py").write_text('"""Core bot entry point."""\n')
    if backend:
        (root / "services/backend").mkdir(parents=True)
    return root


def _commands(root: Path, body: str) -> None:
    (root / "services/tg_bot/src/commands.py").write_text(
        "from services.tg_bot.src.generated.commands import ProductCommand\n\n\n"
        "async def handle(update, context):\n    pass\n\n\n" + body
    )


def _bind_channels(root: Path) -> None:
    bindings = root / "services/tg_bot/bindings"
    bindings.mkdir(parents=True, exist_ok=True)
    shutil.copy2(CHANNELS_BINDING, bindings / "tg-channels.yaml")


def _codes(contract: host_contract.HostContract) -> list[str]:
    return [item.code for item in contract.violations]


def test_fresh_product_has_core_language_and_builtins(tmp_path: Path) -> None:
    contract = evaluate(_product(tmp_path))
    assert contract.violations == []
    assert contract.settings == {"language": "core"}
    assert CORE_SETTINGS["language"] == {"type": "string", "enum": ["ru", "en"]}
    assert [(item.command, item.owner) for item in contract.commands] == [
        ("start", "core"),
        ("command", "core"),
    ]
    standalone = evaluate(_product(tmp_path / "standalone", backend=False))
    assert [item.command for item in standalone.commands] == ["start"]


@pytest.mark.parametrize("modules", ["backend,tg_bot", "tg_bot"])
def test_shipped_registry_is_the_render_of_a_fresh_product(tmp_path: Path, modules: str) -> None:
    environment = Environment(keep_trailing_newline=True)  # noqa: S701  # Copier's defaults.
    shipped = environment.from_string(TEMPLATE_REGISTRY.read_text())
    product = _product(tmp_path, backend="backend" in modules)
    assert shipped.render(modules=modules) == render_registry(evaluate(product))


def test_loader_seeds_core_language_and_refuses_any_manifest_redeclaration(
    tmp_path: Path,
) -> None:
    (tmp_path / "services/backend").mkdir(parents=True)
    specs = load_specs(tmp_path)
    assert specs.settings_schemas == {"language": {"type": "string", "enum": ["ru", "en"]}}
    assert specs.settings_schema_sources == {"language": "core"}
    manifest = tmp_path / "services/tg_bot/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(MANIFEST)  # Equal schema: ownership still conflicts.
    with pytest.raises(SpecValidationError, match="'language' is declared by both 'core'"):
        load_specs(tmp_path)


def test_manifest_redeclaration_is_reported_at_its_source(tmp_path: Path) -> None:
    product = _product(tmp_path)
    manifest = product / "services/tg_bot/manifest.yaml"
    manifest.write_text(MANIFEST)
    [violation] = evaluate(product).violations
    assert violation.as_dict() | {"conflict": None, "action": None} == {
        "code": "core_setting_redeclared",
        "path": "services/tg_bot/manifest.yaml",
        "line": 7,
        "owner": "service:tg_bot",
        "symbol": None,
        "key": "language",
        "command": None,
        "conflict": None,
        "action": None,
        "other": None,
    }
    assert "delete settings_schema.properties.language" in violation.action


def test_product_commands_register_between_core_and_modules(tmp_path: Path) -> None:
    product = _product(tmp_path)
    _commands(product, 'COMMANDS = (\n    ProductCommand("ping", handle),\n)\n')
    _bind_channels(product)
    contract = evaluate(product)
    assert contract.violations == []
    assert [(item.command, item.owner) for item in contract.commands] == [
        ("start", "core"),
        ("command", "core"),
        ("ping", "product"),
        ("channel", "package:tg-channels"),
        ("channels", "package:tg-channels"),
        ("digest", "package:tg-channels"),
    ]
    assert str(contract.commands[2].location) == "services/tg_bot/src/commands.py:9"
    assert str(contract.commands[3].location) == "services/tg_bot/bindings/tg-channels.yaml:6"
    registry = render_registry(contract)
    assert (
        '"command": "ping",\n        "owner": "product",\n'
        '        "source": "services/tg_bot/src/commands.py",'
    ) in registry
    assert "services/tg_bot/src/commands.py:9" not in registry
    assert render_registry(evaluate(product)) == registry


@pytest.mark.parametrize(
    ("body", "code", "other"),
    [
        ('COMMANDS = (ProductCommand("channel", handle),)\n', "command_collision", "package"),
        ('COMMANDS = (ProductCommand("start", handle),)\n', "command_collision", "core"),
        ('COMMANDS = (ProductCommand("Ping", handle),)\n', "invalid_command", None),
        ('COMMANDS = (ProductCommand("p" * 2, handle),)\n', "unsupported_product_command", None),
        ("COMMANDS = tuple()\n", "unsupported_product_command", None),
        ('COMMANDS = [ProductCommand("a", handle)]\n', "unsupported_product_command", None),
        ("COMMANDS = ()\nCOMMANDS += ()\n", "unsupported_product_command", None),
        ("def later():\n    COMMANDS = ()\n", "unsupported_product_command", None),
        (
            'COMMANDS = (ProductCommand("a", handle, help="x"),)\n',
            "unsupported_product_command",
            None,
        ),
        ('COMMANDS = (("a", handle),)\n', "unsupported_product_command", None),
    ],
)
def test_product_command_refusals_name_both_sources(
    tmp_path: Path, body: str, code: str, other: str | None
) -> None:
    product = _product(tmp_path)
    _commands(product, body)
    _bind_channels(product)
    contract = evaluate(product)
    assert _codes(contract) == [code]
    [violation] = contract.violations
    assert violation.location is not None
    assert violation.location.path == "services/tg_bot/src/commands.py"
    if other is not None:
        assert violation.other is not None and violation.other.owner.startswith(other)
        assert violation.owner == "product"
        assert str(violation.location) in violation.action
    with pytest.raises(HostContractError, match=code):
        check_product(product)


def test_standalone_reserves_command_and_modules_collide(tmp_path: Path) -> None:
    product = _product(tmp_path, backend=False)
    _commands(product, 'COMMANDS = (ProductCommand("command", handle),)\n')
    assert _codes(evaluate(product)) == ["reserved_command"]

    product = _product(tmp_path / "modules")
    _bind_channels(product)
    other = product / "services/tg_bot/bindings/zz-other.yaml"
    data = yaml.safe_load(CHANNELS_BINDING.read_text())
    data["package"] = "other"
    data["commands"] = data["commands"][:1]
    other.write_text(yaml.safe_dump(data, allow_unicode=True))
    [violation] = evaluate(product).violations
    assert (violation.code, violation.command, violation.owner) == (
        "command_collision",
        "channel",
        "package:other",
    )
    assert violation.other is not None and violation.other.owner == "package:tg-channels"
    assert "kit bind other --file" in violation.action


@pytest.mark.parametrize(
    ("path", "source"),
    [
        ("src/main.py", "from telegram.ext import CommandHandler\n"),
        ("src/main.py", "application.add_handler(object())\n"),
        ("src/extra.py", "from telegram.ext import MessageHandler as Catch\n"),
        ("src/extra.py", "import telegram.ext\nh = telegram.ext.TypeHandler\n"),
        ("src/extra.py", 'register = getattr(application, "add_handler")\n'),
        ("worker/loop.py", "application.add_handlers([])\n"),
        ("src/broken.py", "def broken(:\n"),
    ],
)
def test_registration_bypass_fails_closed(tmp_path: Path, path: str, source: str) -> None:
    product = _product(tmp_path)
    target = product / "services/tg_bot" / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source)
    violations = evaluate(product).violations
    assert violations and {item.code for item in violations} == {"registration_bypass"}
    assert violations[0].location is not None
    assert violations[0].location.path == f"services/tg_bot/{path}"
    with pytest.raises(HostContractError, match="registration_bypass"):
        check_product(product)


def test_tests_generated_and_environments_are_not_product_handler_code(tmp_path: Path) -> None:
    product = _product(tmp_path)
    for path in ("tests/test_bot.py", "src/generated/commands.py", ".venv/lib/x.py"):
        target = product / "services/tg_bot" / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("from telegram.ext import CommandHandler\n")
    assert evaluate(product).violations == []


def test_binding_must_reference_core_language(tmp_path: Path) -> None:
    product = _product(tmp_path)
    _bind_channels(product)
    path = product / "services/tg_bot/bindings/tg-channels.yaml"
    data = yaml.safe_load(path.read_text())
    data["language"]["key"] = "product_language"
    path.write_text(yaml.safe_dump(data, allow_unicode=True))
    assert _codes(evaluate(product)) == ["binding_language_owner"]
    assert load_binding(path).language.key == "product_language"


def test_registry_drift_is_a_lint_failure_and_write_repairs_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    product = _product(tmp_path)
    contract = evaluate(product)
    assert registry_violation(product, contract) is not None
    assert host_contract.main(["--root", str(product)]) == 1
    assert "registry_stale" in capsys.readouterr().err
    assert host_contract.main(["--root", str(product), "--write"]) == 0
    assert "Host contract PASSED" in capsys.readouterr().out
    assert registry_violation(product, contract) is None
    _commands(product, 'COMMANDS = (ProductCommand("ping", handle),)\n')
    with pytest.raises(HostContractError, match="registry_stale"):
        check_product(product)
    write_registry(product, evaluate(product))
    assert check_product(product).commands[-1].command == "ping"


def test_violations_refuse_to_render_and_are_binding_errors(tmp_path: Path) -> None:
    product = _product(tmp_path)
    _commands(product, 'COMMANDS = (ProductCommand("start", handle),)\n')
    contract = evaluate(product)
    with pytest.raises(BindingError, match="HostContractError"):
        render_registry(contract)
    assert not (product / host_contract.REGISTRY).exists()


@pytest.mark.parametrize(
    "body",
    [
        'extra = {"unexpected": True}\nCOMMANDS = (ProductCommand("ping", handle, **extra),)\n',
        'COMMANDS = (ProductCommand("ping", handle, handler=handle),)\n',
        'COMMANDS = (ProductCommand("ping", name="ping", handler=handle),)\n',
        'COMMANDS = (ProductCommand("ping", handle, handle),)\n',
        'arguments = ("ping", handle)\nCOMMANDS = (ProductCommand(*arguments),)\n',
        'COMMANDS = (ProductCommand("ping", handle, block=False),)\n',
    ],
)
def test_product_command_shapes_the_runtime_refuses_fail_before_writes(
    tmp_path: Path, body: str
) -> None:
    product = _product(tmp_path)
    _commands(product, body)
    assert _codes(evaluate(product)) == ["unsupported_product_command"]
    with pytest.raises(HostContractError):
        write_registry(product, evaluate(product).require_valid())
    assert not (product / host_contract.REGISTRY).exists()


@pytest.mark.parametrize(
    "body",
    [
        'COMMANDS = (ProductCommand("ping", handle),)\n',
        'COMMANDS = (ProductCommand(name="ping", handler=handle),)\n',
        'COMMANDS = (ProductCommand("ping", handler=handle),)\n',
    ],
)
def test_product_command_shapes_the_runtime_accepts_are_admitted(tmp_path: Path, body: str) -> None:
    product = _product(tmp_path)
    _commands(product, body)
    contract = evaluate(product)
    assert contract.violations == []
    assert [(item.command, item.symbol) for item in contract.commands][-1] == ("ping", "handle")


@pytest.mark.parametrize(
    "source",
    [
        "async def ping(update, context):\n"
        "    context.application.handlers[0][-1].callback = ping\n",
        "async def ping(update, context):\n    context.application.handlers.clear()\n",
        "async def ping(update, context):\n    context.application.remove_handler(handler)\n",
        "def wipe(app):\n    app.handlers[0] = []\n",
        'def wipe(application):\n    getattr(application, "handlers").clear()\n',
    ],
)
def test_registry_mutation_through_product_code_fails_closed(tmp_path: Path, source: str) -> None:
    product = _product(tmp_path)
    (product / "services/tg_bot/src/ping.py").write_text(source)
    violations = evaluate(product).violations
    assert violations and {item.code for item in violations} == {"registration_bypass"}
    assert violations[0].location == host_contract.Location("services/tg_bot/src/ping.py", 2)


def test_ordinary_product_handlers_callbacks_and_clear_are_not_banned(tmp_path: Path) -> None:
    product = _product(tmp_path)
    (product / "services/tg_bot/src/logic.py").write_text(
        "class Queue:\n"
        "    def __init__(self):\n"
        "        self.handlers = []\n"
        "        self.callback = None\n\n"
        "    def reset(self, context):\n"
        "        self.handlers.clear()\n"
        "        self.callback = context.bot_data.get('callback')\n"
        "        context.user_data.clear()\n"
    )
    assert evaluate(product).violations == []


@pytest.mark.parametrize("text", ["- language\n", "version: [1\n", "version: 1\n"])
def test_malformed_manifest_fails_closed_with_its_source(tmp_path: Path, text: str) -> None:
    product = _product(tmp_path)
    (product / "services/tg_bot/manifest.yaml").write_text(text)
    [violation] = evaluate(product).violations
    assert violation.code == "manifest_invalid"
    assert violation.location == host_contract.Location("services/tg_bot/manifest.yaml")
    with pytest.raises(HostContractError, match="manifest_invalid"):
        check_product(product)


def test_binding_timezone_naming_core_language_is_refused(tmp_path: Path) -> None:
    product = _product(tmp_path)
    _bind_channels(product)
    path = product / "services/tg_bot/bindings/tg-channels.yaml"
    path.write_text(
        path.read_text()
        + "timezone: {key: language, scope: product, required: true, format: x-iana-tz}\n"
    )
    [violation] = evaluate(product).violations
    assert (violation.code, violation.owner, violation.key) == (
        "binding_setting_conflict",
        "product",
        "language",
    )
    assert violation.location == host_contract.Location(
        "services/tg_bot/bindings/tg-channels.yaml", 71
    )
    # The same binding offered as a package default is the package's, not the product's.
    prospective = evaluate(
        product,
        {"tg-channels.yaml": load_binding(path)},
        {"tg-channels.yaml": tmp_path / "elsewhere.yaml"},
    )
    assert [item.owner for item in prospective.violations] == ["package:tg-channels"]


#: The reviewer's author source: a supported ProductCommand whose handler replaces the registry
#: through an ordinary alias of ``context.application``.
ALIAS_REPLACEMENT = (
    "from telegram import Update\n"
    "from telegram.ext import ContextTypes\n"
    "from services.tg_bot.src.generated.commands import ProductCommand\n\n"
    "async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:\n"
    "    bot = context.application\n"
    "    bot.handlers = {}\n\n"
    'COMMANDS = (ProductCommand("ping", ping),)\n'
)


#: A product handler that keeps the application under an ordinary local name.
PING = "async def ping(update, context):\n"
ALIAS = PING + "    bot = context.application\n"


@pytest.mark.parametrize(
    ("source", "line"),
    [
        (ALIAS + "    del bot.handlers\n", 3),
        (PING + "    a = context.application\n    b = a\n    b.handlers = {}\n", 4),
        ("def wipe(bot: Application) -> None:\n    bot.handlers = {}\n", 2),
        (PING + "    bot: 'Application' = context.bot_data['app']\n    bot.handlers = {}\n", 3),
        (ALIAS + "    setattr(bot, 'handlers', {})\n", 3),
        (ALIAS + "    delattr(bot, 'handlers')\n", 3),
        (PING + "    if (bot := context.application):\n        bot.handlers = {}\n", 3),
        (PING + "    bot, n = context.application, 1\n    bot.handlers = {}\n", 3),
        (ALIAS + "\n    def later():\n        bot.handlers = {}\n", 5),
        (
            "class Hook:\n    def __init__(self, context):\n"
            "        self.bot = context.application\n\n"
            "    def wipe(self):\n        self.bot.handlers = {}\n",
            6,
        ),
    ],
)
def test_registry_reached_through_an_application_alias_fails_closed(
    tmp_path: Path, source: str, line: int
) -> None:
    product = _product(tmp_path)
    (product / "services/tg_bot/src/ping.py").write_text(source)
    [violation] = evaluate(product).violations
    assert (violation.code, violation.symbol) == ("registration_bypass", "application.handlers")
    assert violation.location == host_contract.Location("services/tg_bot/src/ping.py", line)


def test_alias_replacement_in_a_declared_command_is_refused_before_writes(tmp_path: Path) -> None:
    product = _product(tmp_path)
    (product / "services/tg_bot/src/commands.py").write_text(ALIAS_REPLACEMENT)
    contract = evaluate(product)
    [violation] = contract.violations
    assert (violation.code, violation.owner) == ("registration_bypass", "product")
    assert violation.location == host_contract.Location(host_contract.PRODUCT_COMMANDS, 7)
    assert [item.command for item in contract.commands][-1] == "ping"  # The declaration is valid.
    with pytest.raises(HostContractError, match="registration_bypass"):
        write_registry(product, contract.require_valid())
    with pytest.raises(BindingError, match="registration_bypass"):
        render_registry(contract)
    assert not (product / host_contract.REGISTRY).exists()
    with pytest.raises(HostContractError, match="registration_bypass"):
        check_product(product)


def test_unrelated_handlers_and_a_shadowing_parameter_stay_admitted(tmp_path: Path) -> None:
    product = _product(tmp_path)
    (product / "services/tg_bot/src/logic.py").write_text(
        "async def ping(update, context):\n"
        "    bot = context.application\n"
        "    bot.bot_data['seen'] = True\n\n"
        "    def reset(bot):\n"
        "        bot.handlers = []\n\n"
        "    sender = context.bot\n"
        "    sender.handlers = None\n"
        "    queue = Queue()\n"
        "    queue.handlers.clear()\n"
        "    queue.callback = ping\n"
    )
    assert evaluate(product).violations == []
