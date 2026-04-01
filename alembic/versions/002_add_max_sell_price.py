"""Add max_sell_price column to player_scores.

Revision ID: 002
"""
from alembic import op
import sqlalchemy as sa

revision = "002"
down_revision = "001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    columns = [c["name"] for c in insp.get_columns("player_scores")]
    if "max_sell_price" not in columns:
        op.add_column(
            "player_scores",
            sa.Column("max_sell_price", sa.Integer(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("player_scores", "max_sell_price")
