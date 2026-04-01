"""Purge stale v1 scores lacking expected_profit_per_hour.

Revision ID: 003
"""
from alembic import op

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM player_scores WHERE expected_profit_per_hour IS NULL")


def downgrade() -> None:
    pass  # Cannot restore deleted data
