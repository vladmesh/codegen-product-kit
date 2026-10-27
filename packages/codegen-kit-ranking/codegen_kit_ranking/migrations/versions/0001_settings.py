"""Create the package copy of the seeded ranking product settings."""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "ranking_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "settings",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("settings")
