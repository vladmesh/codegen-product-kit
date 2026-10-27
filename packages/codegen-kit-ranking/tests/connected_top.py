"""Drive /channel and /top through the generated bot bindings against the live product backend.

Usage, with the tg_bot interpreter inside the product's integration network:
connected_top.py <directory>. The backend serves the real tg-channels and ranking routes, its
tg-channels client reads the reader double of connected_reader.py, and only the Telegram update
is a double. Seeds the ranking defaults, grants the core caller and asserts the ranked replies.
"""

import asyncio
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx

USER = 123
SEEDS = {
    "ranking.weights": {
        "views": 35,
        "reactions": 25,
        "forwards": 20,
        "freshness": 20,
        "keywords": 0,
    },
    "ranking.keywords": [],
    "ranking.top_count": 10,
}
RANKED = (2, 1, 3)
ADDED = {"ru": "Добавлен канал: @cyproplan", "en": "Channel added: @cyproplan"}
VIEWS = {"ru": "Просмотры", "en": "Views"}
READY_SECONDS = 60


def update(text):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=USER),
        effective_chat=SimpleNamespace(id=USER),
        message=SimpleNamespace(text=text, reply_text=AsyncMock()),
        callback_query=None,
    )


async def administer(client, language):
    grant = await client.post(
        "/users/grant",
        headers={"X-Grant-Capability": os.environ["USERS_GRANT_CAPABILITY"]},
        json={"channel": "telegram", "external_id": str(USER)},
    )
    assert grant.is_success, grant.text
    for key, value in {"language": language, **SEEDS}.items():
        written = await client.post(
            "/settings/set",
            headers={"X-Settings-Capability": os.environ["SETTINGS_WRITE_CAPABILITY"]},
            json={"contract_version": 1, "key": key, "scope": "product", "value": value},
        )
        assert written.is_success, written.text


async def scenario(directory):
    from services.tg_bot.src.generated import bindings as b
    from services.tg_bot.src.main import BackendClient

    deadline = time.monotonic() + READY_SECONDS
    while not (directory / "ready").exists():
        assert time.monotonic() < deadline, "reader double never started"
        await asyncio.sleep(0.5)
    posts = {post["id"]: post for post in json.loads((directory / "reader-posts.json").read_text())}
    bindings = {binding["package"]: binding for binding in b.DATA["bindings"]}
    assert sorted(bindings) == ["ranking", "tg-channels"], sorted(bindings)
    (channel,) = [
        item for item in bindings["tg-channels"]["commands"] if item["command"] == "channel"
    ]
    (top,) = bindings["ranking"]["commands"]
    runtime = b.ChannelBindings(BackendClient)

    async with httpx.AsyncClient(base_url=os.environ["BACKEND_API_URL"]) as client:
        for language in ("ru", "en"):
            await administer(client, language)
            added = update("/channel @cyproplan")
            await runtime.command(bindings["tg-channels"], channel, added, None)
            assert added.message.reply_text.call_args.args[0] == ADDED[language]

            shown = update("/top")
            await runtime.command(bindings["ranking"], top, shown, None)
            replies = [call.args[0] for call in shown.message.reply_text.call_args_list]
            expected = []
            for number in RANKED:
                post = posts[number]
                views = "?" if post["views"] is None else str(post["views"])
                expected.append(
                    f"@cyproplan\n{post['date']}\n{VIEWS[language]}: {views}\n"
                    f"{post['text']}\n{post['url']}"
                )
            assert replies == expected, replies
            print(f"connected /top {language}: {len(replies)} ranked replies in order")

    read = [
        json.loads(line) for line in (directory / "reader-requests.jsonl").read_text().splitlines()
    ]
    assert {"path": "/tg-reader/v1/posts", "authorized": True} in read, read
    print("connected /top: bound command reached ranking.top and the tg-channels provider read")


if __name__ == "__main__":
    asyncio.run(scenario(Path(sys.argv[1])))
