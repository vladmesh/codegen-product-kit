"""Record each user's subscription instant without inferring historical delivery eligibility."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "tg_channels_0002"
down_revision: str | None = "tg_channels_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Existing unreleased installations start at migration time, not an invented past instant.
    op.add_column(
        "subscriptions",
        sa.Column(
            "subscribed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
    )
    op.alter_column("subscriptions", "subscribed_at", server_default=None)


def downgrade() -> None:
    op.drop_column("subscriptions", "subscribed_at")
