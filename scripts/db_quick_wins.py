"""One-time DB cleanup: drop unused index, drop dead column, fix score retention."""
import asyncio
import os
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://op_seller:op_seller@localhost:5432/op_seller",
)

async def run():
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        # 1. Drop unused index (107 MB, 0 scans)
        print("Dropping ix_listing_obs_resolved...")
        await conn.execute(text("DROP INDEX IF EXISTS ix_listing_obs_resolved"))
        print("Done.")

        # 2. Drop live_auction_prices column (write-only, never read)
        print("Dropping market_snapshots.live_auction_prices column...")
        await conn.execute(text("ALTER TABLE market_snapshots DROP COLUMN IF EXISTS live_auction_prices"))
        print("Done.")

        # 3. Purge old player_scores (keep last 48h)
        print("Purging player_scores older than 48 hours...")
        result = await conn.execute(text(
            "DELETE FROM player_scores WHERE scored_at < NOW() - INTERVAL '48 hours'"
        ))
        print(f"Deleted {result.rowcount:,} old score rows.")

    await engine.dispose()
    print("All quick wins applied.")

if __name__ == "__main__":
    asyncio.run(run())
