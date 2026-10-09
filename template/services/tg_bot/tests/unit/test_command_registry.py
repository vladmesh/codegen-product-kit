"""The core command registry wires every handler and owns unknown input."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    TypeHandler,
)

from services.tg_bot.src.generated import bindings, commands


def _register(**overrides: Any) -> Application:
    application = ApplicationBuilder().token("123:test").build()
    builtins = {name: AsyncMock() for name in commands.owned_by("core")}
    arguments: dict[str, Any] = {"access": AsyncMock(), "builtins": builtins, "bindings": bindings}
    arguments.update(overrides)
    commands.register(application, **arguments)
    return application


def test_registry_registers_access_commands_then_core_fallback() -> None:
    application = _register()
    assert [type(item) for item in application.handlers[-1]] == [TypeHandler]
    group = application.handlers[0]
    registered = [sorted(item.commands)[0] for item in group if isinstance(item, CommandHandler)]
    assert registered == [item["command"] for item in commands.COMMANDS]
    assert isinstance(group[-1], MessageHandler)
    assert sum(isinstance(item, MessageHandler) for item in group) == 1


def test_unknown_input_uses_core_language_or_both() -> None:
    assert commands.unknown_reply("ru").startswith("Не понимаю это сообщение.")
    assert commands.unknown_reply("en").startswith("I don't understand this message.")
    both = commands.unknown_reply(None).split("\n")
    assert both == [commands.unknown_reply("ru"), commands.unknown_reply("en")]
    assert "/start" in both[1]


@pytest.mark.asyncio
async def test_language_without_backend_is_unset() -> None:
    assert await commands.language(None) is None


def test_undeclared_product_command_refuses_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    from services.tg_bot.src import commands as product

    extra = commands.ProductCommand("undeclared_command", AsyncMock())
    monkeypatch.setattr(product, "COMMANDS", (*product.COMMANDS, extra))
    with pytest.raises(commands.CommandRegistryError, match="make generate-from-spec"):
        _register()


def test_builtins_must_match_the_registry() -> None:
    with pytest.raises(commands.CommandRegistryError):
        _register(builtins={})
