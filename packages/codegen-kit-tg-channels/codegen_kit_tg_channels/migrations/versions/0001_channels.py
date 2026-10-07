"""Create caller subscriptions, singleton poll state and the durable post outbox."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "tg_channels_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "starting_list",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("channels", JSONB(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_starting_list_singleton"),
    )
    op.create_table("users", sa.Column("user_ref", sa.Text(), primary_key=True))
    op.create_table(
        "subscriptions",
        sa.Column("user_ref", sa.Text(), sa.ForeignKey("users.user_ref"), primary_key=True),
        sa.Column("channel", sa.Text(), primary_key=True),
    )
    op.create_table(
        "poll_state",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("cursor", sa.Text()),
        sa.Column("since_at", sa.DateTime(timezone=True)),
        sa.Column("retry_at", sa.DateTime(timezone=True)),
        sa.Column("stopped", sa.Boolean(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_poll_state_singleton"),
    )
    op.execute("INSERT INTO poll_state (id, stopped) VALUES (1, false)")
    op.create_table(
        "seen_posts",
        sa.Column("channel", sa.Text(), primary_key=True),
        sa.Column("post_id", sa.BigInteger(), primary_key=True),
    )
    op.create_table(
        "deliveries",
        sa.Column("event_id", sa.Uuid(), primary_key=True),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("emitted_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_deliveries_pending", "deliveries", ["emitted_at", "occurred_at"])


def downgrade() -> None:
    for table in (
        "deliveries",
        "seen_posts",
        "poll_state",
        "subscriptions",
        "users",
        "starting_list",
    ):
        op.drop_table(table)
