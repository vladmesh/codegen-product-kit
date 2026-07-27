"""Access contract: who may talk to the bot, and how test access is revoked."""

from __future__ import annotations

import pytest

from services.tg_bot.src.access import (
    ALLOWED_IDS_ENV,
    TEST_IDENTITY_ENV,
    in_test_mode,
    is_allowed,
    is_public,
    test_identity,
)

OWNER = 111
OTHER = 222
TESTER = 333


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(ALLOWED_IDS_ENV, raising=False)
    monkeypatch.delenv(TEST_IDENTITY_ENV, raising=False)


def test_bot_without_audience_is_public() -> None:
    assert is_public()
    assert is_allowed(OWNER)
    assert is_allowed(OTHER)


def test_empty_audience_is_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALLOWED_IDS_ENV, "  , ")

    assert is_public()
    assert is_allowed(OTHER)


def test_private_bot_admits_only_its_audience(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALLOWED_IDS_ENV, str(OWNER))

    assert not is_public()
    assert is_allowed(OWNER)
    assert not is_allowed(OTHER)


def test_audience_accepts_several_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALLOWED_IDS_ENV, f"{OWNER}, {OTHER}")

    assert is_allowed(OWNER)
    assert is_allowed(OTHER)
    assert not is_allowed(TESTER)


def test_test_identity_is_admitted_while_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALLOWED_IDS_ENV, str(OWNER))
    monkeypatch.setenv(TEST_IDENTITY_ENV, str(TESTER))

    assert is_allowed(TESTER)
    assert in_test_mode()
    assert test_identity() == TESTER


def test_removing_test_identity_revokes_access(monkeypatch: pytest.MonkeyPatch) -> None:
    """Revocation is the absence of the value — there is no residual state."""

    monkeypatch.setenv(ALLOWED_IDS_ENV, str(OWNER))
    monkeypatch.setenv(TEST_IDENTITY_ENV, str(TESTER))
    assert is_allowed(TESTER)

    monkeypatch.delenv(TEST_IDENTITY_ENV)

    assert not is_allowed(TESTER)
    assert not in_test_mode()
    assert is_allowed(OWNER)


def test_test_identity_does_not_open_the_bot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALLOWED_IDS_ENV, str(OWNER))
    monkeypatch.setenv(TEST_IDENTITY_ENV, str(TESTER))

    assert not is_allowed(OTHER)


def test_public_bot_is_never_in_test_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(TEST_IDENTITY_ENV, str(TESTER))

    assert is_public()
    assert not in_test_mode()


def test_unparsable_values_do_not_admit_anyone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ALLOWED_IDS_ENV, str(OWNER))
    monkeypatch.setenv(TEST_IDENTITY_ENV, "not-a-number")

    assert test_identity() is None
    assert not in_test_mode()
    assert not is_allowed(TESTER)


def test_several_test_identities_are_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """The contract admits one temporary identity, not a second audience."""

    monkeypatch.setenv(ALLOWED_IDS_ENV, str(OWNER))
    monkeypatch.setenv(TEST_IDENTITY_ENV, f"{TESTER},{OTHER}")

    assert test_identity() is None
    assert not is_allowed(TESTER)
    assert not is_allowed(OTHER)


def test_update_without_sender_is_denied() -> None:
    assert not is_allowed(None)
