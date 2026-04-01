"""Add total_sold_count and total_expired_count to daily_listing_summaries.

Revision ID: 004
"""
from alembic import op
import sqlalchemy as sa

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    columns = [c["name"] for c in insp.get_columns("daily_listing_summaries")]
    if "total_sold_count" not in columns:
        op.add_column(
            "daily_listing_summaries",
            sa.Column("total_sold_count", sa.Integer(), nullable=False, server_default="0"),
        )
    if "total_expired_count" not in columns:
        op.add_column(
            "daily_listing_summaries",
            sa.Column("total_expired_count", sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    op.drop_column("daily_listing_summaries", "total_sold_count")
    op.drop_column("daily_listing_summaries", "total_expired_count")
