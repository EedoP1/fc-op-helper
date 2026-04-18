"""One-off remediation: unstick the ~118 pre-czo cards with created_at=NULL.

These active PlayerRecords were scanned before quick-260418-czo fixed the
"current_bin is None -> return None -> created_at never set" silent-data-loss
hole. They have last_scanned_at set but created_at=NULL, which makes them
invisible to promo_dip_buy's Friday-batch detection.

The fix: set their next_scan_at = utcnow() so the next dispatch cycle picks
them up. With the czo fix in place, the rescan will either populate a real
current_bin (and a real MarketSnapshot) or a shell (and still populate
created_at). Either way they're unstuck.

NOT scheduled; NOT a migration. Run once against the live DB after the czo
fix is deployed.

Usage:
    python -m tools.rescan_stuck_players         # prompts for confirmation
    python -m tools.rescan_stuck_players --yes   # skip prompt
"""
from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.config import DATABASE_URL
from src.server.models_db import PlayerRecord

logger = logging.getLogger(__name__)


async def count_stuck(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Count active players with created_at IS NULL AND last_scanned_at IS NOT NULL."""
    async with session_factory() as session:
        result = await session.execute(
            select(func.count()).select_from(PlayerRecord).where(
                PlayerRecord.is_active == True,  # noqa: E712
                PlayerRecord.created_at.is_(None),
                PlayerRecord.last_scanned_at.is_not(None),
            )
        )
        return result.scalar() or 0


async def requeue_stuck(session_factory: async_sessionmaker[AsyncSession]) -> int:
    """Set next_scan_at = utcnow() for all stuck active players. Returns row count."""
    now = datetime.utcnow()
    async with session_factory() as session:
        result = await session.execute(
            update(PlayerRecord)
            .where(
                PlayerRecord.is_active == True,  # noqa: E712
                PlayerRecord.created_at.is_(None),
                PlayerRecord.last_scanned_at.is_not(None),
            )
            .values(next_scan_at=now)
        )
        await session.commit()
        return result.rowcount or 0


async def main(yes: bool) -> None:
    engine = create_async_engine(DATABASE_URL)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        count = await count_stuck(session_factory)
        print(f"Found {count} stuck active players (created_at=NULL, last_scanned_at set).")

        if count == 0:
            print("Nothing to do.")
            return

        if not yes:
            reply = input(f"Requeue all {count} for immediate rescan? [y/N]: ").strip().lower()
            if reply not in ("y", "yes"):
                print("Aborted.")
                return

        updated = await requeue_stuck(session_factory)
        print(
            f"Set next_scan_at=utcnow() for {updated} players. "
            "They'll be picked up by the next dispatch cycle."
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(
        description="Requeue stuck pre-czo PlayerRecords for immediate rescan.",
    )
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    args = parser.parse_args()
    asyncio.run(main(args.yes))
