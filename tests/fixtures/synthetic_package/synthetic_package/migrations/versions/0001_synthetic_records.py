"""Create the synthetic package table.

Revision ID: synthetic_0001
Revises:
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "synthetic_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create a table through the package-scoped Alembic connection."""

    op.create_table(
        "synthetic_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("value", sa.String(), nullable=False),
    )


def downgrade() -> None:
    """Drop the package-owned table."""

    op.drop_table("synthetic_records")
