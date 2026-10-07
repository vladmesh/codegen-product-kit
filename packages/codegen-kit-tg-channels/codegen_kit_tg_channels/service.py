"""Caller-owned lists and a deterministic latest-post digest."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from http import HTTPStatus
import re
from typing import Any

from codegen_kit_tg_channels.client import ReaderClient, ServiceError
from codegen_kit_tg_channels.models import ChannelView, Post, PostView

USERNAME = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,31}\Z")


class ActionError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def username(text: str) -> str:
    value = text.strip()
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if value.startswith(prefix):
            value = value.removeprefix(prefix)
            break
    if not USERNAME.fullmatch(value):
        raise ActionError("invalid_username")
    return value.lower()


def service_error(error: ServiceError) -> ActionError:
    if error.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
        return ActionError("not_configured")
    if error.status == HTTPStatus.TOO_MANY_REQUESTS:
        return ActionError("try_later")
    if error.status == HTTPStatus.BAD_REQUEST:
        return ActionError("invalid_username")
    return ActionError("try_later")


class ChannelsService:
    def __init__(self, store: Any, client: ReaderClient) -> None:
        self.store = store
        self.client = client

    async def add(self, user_ref: str, text: str) -> ChannelView:
        channel = username(text)
        try:
            resolved = await self.client.resolve(channel)
        except ServiceError as error:
            raise service_error(error) from None
        if resolved.status not in ("ok", "pending"):
            code = resolved.status if resolved.status != "error" else "try_later"
            raise ActionError(code)
        await self.store.add(user_ref, channel)
        if resolved.status == "pending":
            # Accepted and persisted; v2's declared error route supplies the checking reply.
            raise ActionError("pending")
        return ChannelView(id=channel, channel=channel)

    async def list(self, user_ref: str) -> list[ChannelView]:
        return [
            ChannelView(id=channel, channel=channel) for channel in await self.store.list(user_ref)
        ]

    async def remove(self, user_ref: str, channel: str) -> ChannelView:
        channel = username(channel)
        await self.store.remove(user_ref, channel)
        return ChannelView(id=channel, channel=channel)

    async def digest(self, user_ref: str) -> list[PostView]:
        channels = await self.store.list(user_ref)
        if not channels:
            return []
        since = datetime.now(UTC) - timedelta(hours=72)
        cursor = None
        latest: dict[tuple[str, int], Post] = {}
        try:
            # Bounded work: at most 20 requests, with the remaining feed left for timer polling.
            for _ in range(20):
                page = await self.client.posts(channels, since=since, cursor=cursor)
                for post in page.items:
                    if post.channel in channels:
                        identity = (post.channel, post.id)
                        if identity not in latest or latest[identity].seq < post.seq:
                            latest[identity] = post
                cursor = page.next_cursor
                if not page.has_more:
                    break
            else:
                # A partial change feed could still contain an unseen edit/tombstone.
                raise ActionError("try_later")
        except ServiceError as error:
            raise service_error(error) from None
        posts = sorted(
            (post for post in latest.values() if not post.deleted),
            key=lambda post: (post.date, post.channel, post.id),
            reverse=True,
        )
        return [post.view() for post in posts[:20]]
