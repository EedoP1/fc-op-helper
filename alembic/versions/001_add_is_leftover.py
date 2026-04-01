"""Add is_leftover column to portfolio_slots.

Revision ID: 001
"""
from alembic import op
import sqlalchemy as sa

revision = "001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    columns = [c["name"] for c in insp.get_columns("portfolio_slots")]
    if "is_leftover" not in columns:
        op.add_column(
            "portfolio_slots",
            sa.Column("is_leftover", sa.Boolean(), nullable=False, server_default="false"),
        )


def downgrade() -> None:
    op.drop_column("portfolio_slots", "is_leftover")
