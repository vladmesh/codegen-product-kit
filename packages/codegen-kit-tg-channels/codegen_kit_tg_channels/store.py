"""Transactional subscriptions, product cursor, post identities and delivery outbox."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text as sql

from codegen_kit_tg_channels.database import database
from codegen_kit_tg_channels.models import PostPage
from codegen_kit_tg_channels.service import ActionError, username

CHANNEL_LIMIT = 50
SUBSCRIPTION_SLACK = timedelta(minutes=10)


class Store:
    def transaction(self) -> Any:
        return database.session()

    async def lock_poll(self, session: Any) -> dict[str, Any]:
        row = (await session.execute(sql("SELECT * FROM poll_state WHERE id = 1 FOR UPDATE"))).one()
        return dict(row._mapping)

    async def _initialize(self, session: Any, user_ref: str) -> None:
        created = await session.scalar(
            sql(
                "INSERT INTO users (user_ref) VALUES (:user_ref) "
                "ON CONFLICT DO NOTHING RETURNING user_ref"
            ),
            {"user_ref": user_ref},
        )
        if created is None:
            return
        seeds = await session.scalar(sql("SELECT channels FROM starting_list WHERE id = 1"))
        for channel in seeds or []:
            await self._insert(session, user_ref, channel)

    async def _insert(self, session: Any, user_ref: str, channel: str) -> None:
        union = await self.subscriptions(session)
        if channel not in union and len(union) >= CHANNEL_LIMIT:
            raise ActionError("try_later")
        await session.execute(
            sql(
                "INSERT INTO subscriptions (user_ref, channel, subscribed_at) "
                "VALUES (:user_ref, :channel, :subscribed_at) "
                "ON CONFLICT DO NOTHING"
            ),
            {"user_ref": user_ref, "channel": channel, "subscribed_at": datetime.now(UTC)},
        )

    async def add(self, user_ref: str, channel: str) -> None:
        async with self.transaction() as session:
            await self.lock_poll(session)
            await self._initialize(session, user_ref)
            await self._insert(session, user_ref, channel)

    async def list(self, user_ref: str) -> list[str]:
        async with self.transaction() as session:
            await self.lock_poll(session)
            await self._initialize(session, user_ref)
            return list(
                (
                    await session.execute(
                        sql(
                            "SELECT channel FROM subscriptions WHERE user_ref = :user_ref "
                            "ORDER BY channel"
                        ),
                        {"user_ref": user_ref},
                    )
                ).scalars()
            )

    async def remove(self, user_ref: str, channel: str) -> None:
        async with self.transaction() as session:
            await self.lock_poll(session)
            await self._initialize(session, user_ref)
            await session.execute(
                sql("DELETE FROM subscriptions WHERE user_ref = :user_ref AND channel = :channel"),
                {"user_ref": user_ref, "channel": channel},
            )

    async def subscriptions(self, session: Any) -> dict[str, dict[str, datetime]]:
        rows = (
            await session.execute(sql("SELECT channel, user_ref, subscribed_at FROM subscriptions"))
        ).all()
        result: dict[str, dict[str, datetime]] = {}
        for row in rows:
            result.setdefault(row.channel, {})[row.user_ref] = row.subscribed_at
        return result

    async def save_state(self, session: Any, state: dict[str, Any]) -> None:
        await session.execute(
            sql(
                "UPDATE poll_state SET cursor = :cursor, since_at = :since_at, "
                "retry_at = :retry_at, stopped = :stopped WHERE id = 1"
            ),
            state,
        )

    async def accept_page(
        self,
        session: Any,
        page: PostPage,
        subscribers: dict[str, dict[str, datetime]],
        at: datetime,
    ) -> None:
        for post in sorted(page.items, key=lambda item: item.seq):
            if post.channel not in subscribers:
                continue
            inserted = await session.scalar(
                sql(
                    "INSERT INTO seen_posts (channel, post_id) VALUES (:channel, :post_id) "
                    "ON CONFLICT DO NOTHING RETURNING post_id"
                ),
                {"channel": post.channel, "post_id": post.id},
            )
            if inserted is None or post.deleted:
                continue
            payload = post.view().model_dump()
            for user_ref, subscribed_at in subscribers[post.channel].items():
                if post.date < subscribed_at - SUBSCRIPTION_SLACK:
                    continue
                event_id = uuid5(NAMESPACE_URL, f"tg-channels:{post.channel}:{post.id}:{user_ref}")
                await session.execute(
                    sql(
                        "INSERT INTO deliveries (event_id, payload, occurred_at) "
                        "VALUES (:event_id, CAST(:payload AS jsonb), :at) ON CONFLICT DO NOTHING"
                    ),
                    {
                        "event_id": event_id,
                        "payload": json.dumps(payload | {"user_ref": user_ref}),
                        "at": at,
                    },
                )

    async def pending(self) -> list[Any]:
        async with self.transaction() as session:
            return list(
                (
                    await session.execute(
                        sql(
                            "SELECT event_id, payload, occurred_at FROM deliveries "
                            "WHERE emitted_at IS NULL ORDER BY occurred_at, event_id LIMIT 200"
                        )
                    )
                ).all()
            )

    async def confirm(self, event_id: Any, at: datetime) -> None:
        async with self.transaction() as session:
            await session.execute(
                sql("UPDATE deliveries SET emitted_at = :at WHERE event_id = :event_id"),
                {"at": at, "event_id": event_id},
            )

    async def seed(self, session: Any, key: str, value: Any) -> None:
        if key != "tg_channels.starting_channels" or not isinstance(value, list):
            raise ValueError("unsupported channel setting seed")
        channels = sorted({username(item) for item in value})
        if len(channels) > CHANNEL_LIMIT:
            raise ValueError("too many starting channels")
        # Seeds run with the core search path; qualify the package-owned table explicitly.
        await session.execute(
            sql(
                "INSERT INTO tg_channels.starting_list (id, channels) "
                "VALUES (1, CAST(:channels AS jsonb)) ON CONFLICT DO NOTHING"
            ),
            {"channels": json.dumps(channels)},
        )
