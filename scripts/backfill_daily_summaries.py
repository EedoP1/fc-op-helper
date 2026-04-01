"""One-time backfill: aggregate existing resolved listing_observations into daily_listing_summaries.

Reads all resolved listing_observations grouped by (ea_id, date), classifies
each by margin tier, inserts into daily_listing_summaries, then deletes the
resolved observations. Reports progress every 100 (ea_id, date) pairs.

Usage:
    python scripts/backfill_daily_summaries.py

Prerequisites:
    - Run the ALTER TABLE migration first (add total_sold_count, total_expired_count columns)
    - Ensure DATABASE_URL env var is set or defaults to local PostgreSQL
"""
import asyncio
import os
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import MARGINS, MAX_OP_MARGIN_PCT

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://op_seller:op_seller@localhost:5432/op_seller",
)


async def backfill():
    engine = create_async_engine(DATABASE_URL)

    async with engine.begin() as conn:
        # Add columns if they don't exist (idempotent migration)
        await conn.execute(text(
            "ALTER TABLE daily_listing_summaries ADD COLUMN IF NOT EXISTS total_sold_count INTEGER DEFAULT 0"
        ))
        await conn.execute(text(
            "ALTER TABLE daily_listing_summaries ADD COLUMN IF NOT EXISTS total_expired_count INTEGER DEFAULT 0"
        ))
        print("Schema migration applied (total_sold_count, total_expired_count).")

        # Get distinct (ea_id, date) pairs from resolved observations
        rows = await conn.execute(text("""
            SELECT DISTINCT ea_id, CAST(first_seen_at AS DATE) AS obs_date
            FROM listing_observations
            WHERE outcome IS NOT NULL
            ORDER BY ea_id, obs_date
        """))
        pairs = rows.all()
        print(f"Found {len(pairs)} (ea_id, date) pairs to aggregate")

        for i, (ea_id, obs_date) in enumerate(pairs):
            date_str = str(obs_date)

            # Get all resolved observations for this player+date
            obs_rows = await conn.execute(text("""
                SELECT id, buy_now_price, market_price_at_obs, outcome
                FROM listing_observations
                WHERE ea_id = :ea_id AND outcome IS NOT NULL
                  AND CAST(first_seen_at AS DATE) = :obs_date
            """), {"ea_id": ea_id, "obs_date": obs_date})
            observations = obs_rows.all()

            n_total = len(observations)
            n_sold = sum(1 for o in observations if o.outcome == "sold")
            n_expired = sum(1 for o in observations if o.outcome == "expired")

            max_op_factor = 1 + MAX_OP_MARGIN_PCT / 100.0

            for margin_pct in MARGINS:
                op_sold = 0
                op_expired = 0
                op_listed = 0
                for o in observations:
                    if o.market_price_at_obs > 0 and o.buy_now_price < int(o.market_price_at_obs * max_op_factor):
                        if o.buy_now_price >= int(o.market_price_at_obs * (1 + margin_pct / 100.0)):
                            op_listed += 1
                            if o.outcome == "sold":
                                op_sold += 1
                            else:
                                op_expired += 1

                await conn.execute(text("""
                    INSERT INTO daily_listing_summaries
                        (ea_id, date, margin_pct, op_listed_count, op_sold_count, op_expired_count,
                         total_listed_count, total_sold_count, total_expired_count)
                    VALUES (:ea_id, :date, :margin_pct, :op_listed, :op_sold, :op_expired,
                            :total, :total_sold, :total_expired)
                    ON CONFLICT (ea_id, date, margin_pct)
                    DO UPDATE SET
                        op_listed_count = EXCLUDED.op_listed_count,
                        op_sold_count = EXCLUDED.op_sold_count,
                        op_expired_count = EXCLUDED.op_expired_count,
                        total_listed_count = EXCLUDED.total_listed_count,
                        total_sold_count = EXCLUDED.total_sold_count,
                        total_expired_count = EXCLUDED.total_expired_count
                """), {
                    "ea_id": ea_id, "date": date_str, "margin_pct": margin_pct,
                    "op_listed": op_listed, "op_sold": op_sold, "op_expired": op_expired,
                    "total": n_total, "total_sold": n_sold, "total_expired": n_expired,
                })

            # Delete resolved observations for this player+date
            obs_ids = [o.id for o in observations]
            for j in range(0, len(obs_ids), 500):
                chunk = obs_ids[j:j + 500]
                placeholders = ",".join(str(x) for x in chunk)
                await conn.execute(text(
                    f"DELETE FROM listing_observations WHERE id IN ({placeholders})"
                ))

            if (i + 1) % 100 == 0:
                print(f"  Processed {i + 1}/{len(pairs)} pairs...")

    await engine.dispose()
    print("Backfill complete.")


if __name__ == "__main__":
    asyncio.run(backfill())
