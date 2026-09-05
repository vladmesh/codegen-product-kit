"""Create reminders and their durable emission outbox.

Revision ID: reminders_0001
Revises:
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "reminders_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create package-owned reminder and pending-emission state."""

    op.create_table(
        "reminders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_ref", sa.Text(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("remind_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("emitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('scheduled', 'cancelled', 'due', 'emitted')",
            name="ck_reminders_state",
        ),
    )
    op.create_index("ix_reminders_user_ref", "reminders", ["user_ref"])
    op.create_index("ix_reminders_due", "reminders", ["state", "remind_at"])
    op.create_table(
        "due_emissions",
        sa.Column(
            "reminder_id",
            sa.Uuid(),
            sa.ForeignKey("reminders.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("event_id", sa.Uuid(), nullable=False, unique=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("emitted_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop all package-owned reminder state."""

    op.drop_table("due_emissions")
    op.drop_index("ix_reminders_due", table_name="reminders")
    op.drop_index("ix_reminders_user_ref", table_name="reminders")
    op.drop_table("reminders")
