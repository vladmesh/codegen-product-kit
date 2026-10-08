"""One persisted product cursor and durable, recipient-specific new-post events."""

from datetime import datetime, timedelta
from http import HTTPStatus
import logging
from typing import Any

from codegen_kit_tg_channels.client import ReaderClient, ServiceError

LOGGER = logging.getLogger(__name__)
RESTART_MARGIN = timedelta(hours=24)
RETENTION_HORIZON = timedelta(days=30)


class Poller:
    def __init__(self, store: Any, client: ReaderClient | None, publish: Any) -> None:
        self.store = store
        self.client = client
        self.publish = publish

    async def _page(
        self, client: ReaderClient, channels: list[str], state: dict[str, Any], at: datetime
    ) -> Any:
        try:
            return await client.posts(channels, since=state["since_at"], cursor=state["cursor"])
        except ServiceError as error:
            if error.status != HTTPStatus.CONFLICT:
                raise
        # The set of channels is part of the opaque cursor. Restart and rely on stored ids.
        state["cursor"] = None
        state["since_at"] = max(state["since_at"], at - RESTART_MARGIN, at - RETENTION_HORIZON)
        return await client.posts(channels, since=state["since_at"])

    async def poll(self, at: datetime) -> None:
        client = self.client
        if client is None:
            # Not configured: startup warned once; no platform request and no state change.
            return
        async with self.store.transaction() as session:
            state = await self.store.lock_poll(session)
            if state["stopped"] or (state["retry_at"] and state["retry_at"] > at):
                return
            subscribers = await self.store.subscriptions(session)
            if not subscribers:
                return
            state["since_at"] = max(
                state["since_at"] or at - timedelta(hours=72), at - RETENTION_HORIZON
            )
            try:
                page = await self._page(client, sorted(subscribers), state, at)
            except ServiceError as error:
                if error.status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
                    state["stopped"] = True
                    LOGGER.error("Channel polling stopped: service not configured")
                else:
                    state["retry_at"] = at + timedelta(seconds=error.retry_after)
                    LOGGER.warning("Channel polling delayed (status %s)", error.status)
            else:
                await self.store.accept_page(session, page, subscribers, at)
                state["cursor"] = page.next_cursor
                state["since_at"] = max(
                    state["since_at"], at - RESTART_MARGIN, at - RETENTION_HORIZON
                )
                state["retry_at"] = None
            await self.store.save_state(session, state)

    async def emit_pending(self, at: datetime) -> None:
        for row in await self.store.pending():
            await self.publish(
                "tg-channels.post",
                row.payload,
                event_id=row.event_id,
                occurred_at=row.occurred_at,
            )
            await self.store.confirm(row.event_id, at)

    async def tick(self, at: datetime) -> None:
        # A delayed/stopped platform poll does not prevent committed deliveries being retried.
        await self.emit_pending(at)
        await self.poll(at)
        await self.emit_pending(at)
