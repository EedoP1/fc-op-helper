"""Add weighted_score column to player_scores.

Separates the scorer_v3 composite ranking score from the three
display columns (expected_profit, efficiency, expected_profit_per_hour)
whose values were previously all overwritten with weighted_score.

Revision ID: 005
"""
from alembic import op
import sqlalchemy as sa

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    insp = sa.inspect(conn)
    columns = [c["name"] for c in insp.get_columns("player_scores")]
    if "weighted_score" not in columns:
        op.add_column(
            "player_scores",
            sa.Column("weighted_score", sa.Float(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("player_scores", "weighted_score")
