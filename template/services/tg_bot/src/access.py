"""Who may talk to the bot.

Access is configuration, not code: the deployment decides the audience through two
environment values declared in ``env.contract.yaml``.

``TG_BOT_ALLOWED_TELEGRAM_IDS``
    Comma-separated Telegram ids allowed to use the bot. Unset or empty means the bot
    is public — that is the "everyone" audience, chosen deliberately at deploy time.

``TG_BOT_TEST_TELEGRAM_ID``
    A single extra identity admitted **temporarily**, for automated testing of a
    private bot. It is deliberately separate from the product audience: it is not
    merged into the allow-list, it never survives its removal from the environment,
    and its presence is observable through :func:`in_test_mode`.

Removing the value is what revokes the access — there is no state to clean up.
"""

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
    """The temporary test identity, or ``None`` when the bot is not under test."""

    parsed = _parse_ids(os.getenv(TEST_IDENTITY_ENV))
    if len(parsed) != 1:
        return None
    return next(iter(parsed))


def is_public() -> bool:
    """Whether the bot accepts everyone."""

    return not allowed_ids()


def in_test_mode() -> bool:
    """Whether a temporary test identity is currently admitted.

    A public bot is never "in test mode": there is nothing to admit it to.
    """

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

    return telegram_id == test_identity()
