"""Stable identities for reminder occurrences."""

from uuid import NAMESPACE_URL, UUID, uuid5


def due_event_id(reminder_id: UUID) -> UUID:
    """Return the stable logical-notification identity for a one-time occurrence."""

    return uuid5(NAMESPACE_URL, f"codegen-kit-reminders:due:{reminder_id}")
