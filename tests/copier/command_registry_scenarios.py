"""Drive the generated core command registry through a real PTB Application, offline.

Run with a bound product's tg_bot interpreter. Product, built-in and module commands, the
core unknown fallback in RU, EN and unset language, registration order and fail-closed
registry drift are exercised through ``Application.process_update`` with only the Bot API
send and the backend HTTP transport replaced.
"""

import asyncio
from datetime import UTC, datetime
import json
import os
import sys
from unittest.mock import patch

import httpx


async def scenario(locale):  # noqa: C901, PLR0915
    from services.tg_bot.src import commands as product
    from services.tg_bot.src.generated import bindings, commands
    from services.tg_bot.src.main import BackendClient, handle_command, handle_start
    from telegram import Chat, Message, MessageEntity, Update, User
    from telegram.ext import (
        ApplicationBuilder,
        CallbackQueryHandler,
        CommandHandler,
        MessageHandler,
        TypeHandler,
    )

    os.environ["BACKEND_API_URL"] = "http://backend.test"
    os.environ["USER_IDENTITY_CAPABILITY"] = "test-capability"
    language = [locale]
    requests = []

    def respond(request):
        data = json.loads(request.content) if request.content else {}
        requests.append((request.method, request.url.path, data))
        if request.url.path == "/settings/get":
            assert data == {"contract_version": 1, "key": "language", "scope": "product"}
            if language[0] is None:
                return httpx.Response(404, json={"detail": "Setting value not found"})
            return httpx.Response(200, json={**data, "value": language[0]})
        raise AssertionError(f"unexpected backend call {request.method} {request.url.path}")

    class FakeClient(BackendClient):
        async def __aenter__(self):
            await super().__aenter__()
            await self._client.aclose()
            self._client = httpx.AsyncClient(
                base_url=self.base_url, transport=httpx.MockTransport(respond)
            )
            self.initial_delay = 0
            return self

    async def allow(update, context):
        return None

    sent = []

    async def send_message(self, chat_id, text, *args, **kwargs):
        sent.append(text)
        return Message(
            message_id=len(sent) + 1000,
            date=datetime.now(UTC),
            chat=Chat(id=chat_id, type="private"),
            text=text,
        )

    user = User(id=123, is_bot=False, first_name="Test")
    bot_user = User(id=1, is_bot=True, first_name="Bot", username="registry_bot")
    counter = [0]

    async def get_me(self, *args, **kwargs):
        self._bot_user = bot_user
        return bot_user

    def update(text, bot):
        counter[0] += 1
        entities = []
        if text.startswith("/"):
            entities = [MessageEntity("bot_command", 0, len(text.split()[0]))]
        message = Message(
            message_id=counter[0],
            date=datetime.now(UTC),
            chat=Chat(id=123, type="private"),
            from_user=user,
            text=text,
            entities=entities,
        )
        message.set_bot(bot)
        return Update(update_id=counter[0], message=message)

    with (
        patch("telegram.Bot.get_me", new=get_me),
        patch("telegram.Bot.send_message", new=send_message),
    ):
        application = ApplicationBuilder().token("test:token").build()
        builtins = {"start": handle_start, "command": handle_command}
        commands.register(
            application,
            access=allow,
            builtins=builtins,
            bindings=bindings,
            client_factory=FakeClient,
        )
        group = application.handlers[0]
        registered = [
            sorted(item.commands)[0] for item in group if isinstance(item, CommandHandler)
        ]
        assert registered == [item["command"] for item in commands.COMMANDS], registered
        assert isinstance(group[-1], MessageHandler), group[-1]
        assert [isinstance(item, TypeHandler) for item in application.handlers[-1]] == [True]
        assert all(
            isinstance(item, CommandHandler | CallbackQueryHandler | MessageHandler)
            for item in group
        )
        assert sum(isinstance(item, MessageHandler) for item in group) == 1
        product_commands = commands.owned_by("product")
        assert product_commands == [item.name for item in product.COMMANDS]
        assert "channel" in commands.module_commands()
        await application.initialize()

        async def say(text):
            before = len(sent)
            await application.process_update(update(text, application.bot))
            return sent[before:]

        listing = ", ".join(f"/{item['command']}" for item in commands.COMMANDS)
        unknown = {
            "ru": f"Не понимаю это сообщение. Доступные команды: {listing}",
            "en": f"I don't understand this message. Available commands: {listing}",
        }
        for name in product_commands:
            replies = await say(f"/{name}")
            assert replies == [f"product {name}"], replies
        assert (await say("/start"))[0].startswith("Привет!")
        empty = {"ru": "Введите имя публичного канала.", "en": "Enter a public channel username."}
        assert await say("/channel") == [empty[locale]]
        assert await say("/no_such_command") == [unknown[locale]]
        assert await say("plain text") == [unknown[locale]]
        language[0] = None
        assert await say("/no_such_command") == [f"{unknown['ru']}\n{unknown['en']}"]
        language[0] = locale
        assert all(path == "/settings/get" for _, path, _ in requests), requests
        await application.shutdown()

        # Fail closed: a product declaration the registry does not hold refuses startup.
        drifted = ApplicationBuilder().token("test:token").build()
        original = product.COMMANDS
        product.COMMANDS = (*original, commands.ProductCommand("unregistered", allow))
        try:
            commands.register(
                drifted,
                access=allow,
                builtins=builtins,
                bindings=bindings,
                client_factory=FakeClient,
            )
        except commands.CommandRegistryError as error:
            assert "make generate-from-spec" in str(error)
        else:
            raise AssertionError("an undeclared product command was registered")
        finally:
            product.COMMANDS = original

        class Rogue:
            @staticmethod
            def register(application, client_factory):
                application.add_handler(CommandHandler("channel", allow))
                application.add_handler(MessageHandler(None, allow))

        rogue = ApplicationBuilder().token("test:token").build()
        try:
            commands.register(
                rogue, access=allow, builtins=builtins, bindings=Rogue, client_factory=FakeClient
            )
        except commands.CommandRegistryError:
            pass
        else:
            raise AssertionError("a module catch-all was registered")

        # Through normal dispatch, a declared product command tries to replace the core
        # unknown reply, clear or extend the registry. Every change fails closed.
        attempts = {}
        rogue_calls = []

        async def rogue(update, context):
            rogue_calls.append(update)

        async def mutate(update, context):
            app = context.application
            changes = {
                "callback": lambda: setattr(app.handlers[0][-1], "callback", rogue),
                "clear": app.handlers.clear,
                "group": lambda: app.handlers.__setitem__(5, []),
                "remove_handler": lambda: app.remove_handler(app.handlers[0][-1]),
                "add_handler": lambda: app.add_handler(MessageHandler(None, rogue)),
            }
            for name, change in changes.items():
                try:
                    change()
                except (commands.CommandRegistryError, AttributeError) as error:
                    attempts[name] = type(error).__name__
                else:
                    attempts[name] = "applied"
            await update.message.reply_text("mutation attempted")

        target = product_commands[0]
        product.COMMANDS = tuple(
            commands.ProductCommand(item.name, mutate if item.name == target else item.handler)
            for item in original
        )
        try:
            mutated = ApplicationBuilder().token("test:token").build()
            commands.register(
                mutated,
                access=allow,
                builtins=builtins,
                bindings=bindings,
                client_factory=FakeClient,
            )
        finally:
            product.COMMANDS = original
        registered_before = {
            group: [(id(item), item.callback) for item in items]
            for group, items in mutated.handlers.items()
        }
        await mutated.initialize()
        before = len(sent)
        await mutated.process_update(update(f"/{target}", mutated.bot))
        assert sent[before:] == ["mutation attempted"], sent[before:]
        assert set(attempts) == {"callback", "clear", "group", "remove_handler", "add_handler"}
        assert "applied" not in attempts.values(), attempts
        assert {
            group: [(id(item), item.callback) for item in items]
            for group, items in mutated.handlers.items()
        } == registered_before
        before = len(sent)
        await mutated.process_update(update("text after mutation", mutated.bot))
        await mutated.process_update(update("/start", mutated.bot))
        assert sent[before] == unknown[locale], sent[before:]
        assert sent[before + 1].startswith("Привет!")
        assert rogue_calls == []
        await mutated.shutdown()
    print(f"registry {locale}: commands, module binding and unknown fallback passed")


if __name__ == "__main__":
    asyncio.run(scenario(sys.argv[1]))
