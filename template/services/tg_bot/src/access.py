"""Telegram bot access policy."""

from __future__ import annotations

import os
from typing import Final

ALLOWED_IDS_ENV: Final[str] = "TG_BOT_ALLOWED_TELEGRAM_IDS"
TEST_IDENTITY_ENV: Final[str] = "TG_BOT_TEST_TELEGRAM_ID"


def _parse_ids(raw: str | None) -> frozenset[int]:
    """Parse a comma-separated id list, ignoring blanks and unparsable entries."""

    if not raw:
        return frozenset()

    ids: set[int] = set()
    for chunk in raw.split(","):
        candidate = chunk.strip()
        if not candidate:
            continue
        try:
            ids.add(int(candidate))
        except ValueError:
            continue
    return frozenset(ids)


def allowed_ids() -> frozenset[int]:
    """Product audience. Empty means the bot is public."""

    return _parse_ids(os.getenv(ALLOWED_IDS_ENV))


def test_identity() -> int | None:
    """Return the temporary test identity."""

    parsed = _parse_ids(os.getenv(TEST_IDENTITY_ENV))
    if len(parsed) != 1:
        return None
    return next(iter(parsed))


def is_public() -> bool:
    """Whether the bot accepts everyone."""

    return not allowed_ids()


def in_test_mode() -> bool:
    """Test identity applies only to a private audience."""

    return not is_public() and test_identity() is not None


def is_allowed(telegram_id: int | None) -> bool:
    """Whether *telegram_id* may interact with the bot."""

    if telegram_id is None:
        return False

    audience = allowed_ids()
    if not audience:
        return True

    if telegram_id in audience:
        return True

    # Test access stays separate from the audience; deleting its value revokes it.
    return telegram_id == test_identity()
