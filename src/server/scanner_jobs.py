"""Scanner scheduled jobs sub-module: periodic cleanup tasks.

Extracted from scanner.py to keep the core scan loop isolated.
ScannerService delegates to these functions via thin wrappers.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.config import (
    MARKET_DATA_RETENTION_DAYS,
    LISTING_RETENTION_DAYS,
)
from src.server.models_db import MarketSnapshot, PlayerScore, ListingObservation, DailyListingSummary

logger = logging.getLogger(__name__)


async def run_cleanup(session_factory: async_sessionmaker) -> None:
    """Delete market snapshots older than MARKET_DATA_RETENTION_DAYS.

    Also prunes old PlayerScore rows beyond retention to keep the DB
    lean. Additionally purges old resolved and orphaned
    ListingObservation rows (D-12).

    Args:
        session_factory: Async session factory for DB writes.
    """
    cutoff = datetime.utcnow() - timedelta(days=MARKET_DATA_RETENTION_DAYS)
    listing_cutoff = datetime.utcnow() - timedelta(days=LISTING_RETENTION_DAYS)
    async with session_factory() as session:
        # Delete old snapshots
        result = await session.execute(
            delete(MarketSnapshot).where(MarketSnapshot.captured_at < cutoff)
        )
        snapshot_count = result.rowcount

        # Also prune old PlayerScore rows (keep last 48h)
        score_cutoff = datetime.utcnow() - timedelta(hours=48)
        result = await session.execute(
            delete(PlayerScore).where(PlayerScore.scored_at < score_cutoff)
        )
        score_count = result.rowcount

        # Delete old daily listing summaries beyond retention
        summary_cutoff = (datetime.utcnow() - timedelta(days=LISTING_RETENTION_DAYS)).strftime("%Y-%m-%d")
        result = await session.execute(
            delete(DailyListingSummary).where(DailyListingSummary.date < summary_cutoff)
        )
        summary_purged = result.rowcount

        # Purge orphaned unresolved observations (last_seen_at too old)
        result = await session.execute(
            delete(ListingObservation).where(
                ListingObservation.outcome.is_(None),
                ListingObservation.last_seen_at < listing_cutoff,
            )
        )
        orphaned_purged = result.rowcount

        await session.commit()
    logger.info(
        f"Cleanup: deleted {snapshot_count} snapshots, {score_count} scores "
        f"older than {MARKET_DATA_RETENTION_DAYS} days; "
        f"purged {summary_purged} old daily summaries and {orphaned_purged} orphaned "
        f"listing observations older than {LISTING_RETENTION_DAYS} days"
    )
