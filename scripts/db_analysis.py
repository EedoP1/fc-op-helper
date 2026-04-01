"""Database analysis script — reports table sizes, orphans, and waste."""
import asyncio
import os

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+asyncpg://op_seller:op_seller@localhost:5432/op_seller",
)


async def analyze():
    engine = create_async_engine(DATABASE_URL)

    async with engine.begin() as conn:
        # 1. Table sizes
        print("=" * 70)
        print("TABLE SIZES")
        print("=" * 70)
        rows = await conn.execute(text("""
            SELECT
                relname AS table_name,
                pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
                pg_size_pretty(pg_relation_size(relid)) AS data_size,
                pg_size_pretty(pg_total_relation_size(relid) - pg_relation_size(relid)) AS index_size,
                n_live_tup AS row_count
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
        """))
        for r in rows:
            print(
                f"  {r.table_name:<30} {r.total_size:>12} "
                f"(data: {r.data_size:>10}, idx: {r.index_size:>10}) "
                f"rows: {r.row_count:>12,}"
            )

        # 2. Timestamp ranges per table
        print("\n" + "=" * 70)
        print("TIMESTAMP RANGES")
        print("=" * 70)
        timestamp_queries = [
            ("player_scores", "scored_at"),
            ("market_snapshots", "captured_at"),
            ("listing_observations", "first_seen_at"),
            ("daily_listing_summaries", "date"),
            ("trade_actions", "created_at"),
            ("trade_records", "recorded_at"),
        ]
        for table, col in timestamp_queries:
            row = await conn.execute(text(
                f"SELECT MIN({col}) AS oldest, MAX({col}) AS newest, COUNT(*) AS cnt FROM {table}"
            ))
            r = row.first()
            print(f"  {table:<30} oldest: {r.oldest}  newest: {r.newest}  count: {r.cnt:,}")

        # 3. Orphaned data
        print("\n" + "=" * 70)
        print("ORPHANED DATA (ea_ids not in players table)")
        print("=" * 70)
        for table in ["player_scores", "market_snapshots", "listing_observations"]:
            row = await conn.execute(text(f"""
                SELECT COUNT(*) AS cnt
                FROM {table} t
                WHERE NOT EXISTS (SELECT 1 FROM players p WHERE p.ea_id = t.ea_id)
            """))
            r = row.first()
            print(f"  {table:<30} orphaned rows: {r.cnt:,}")

        # 4. live_auction_prices JSON size
        print("\n" + "=" * 70)
        print("JSON COLUMN ANALYSIS (market_snapshots.live_auction_prices)")
        print("=" * 70)
        row = await conn.execute(text("""
            SELECT
                AVG(LENGTH(live_auction_prices)) AS avg_len,
                MAX(LENGTH(live_auction_prices)) AS max_len,
                MIN(LENGTH(live_auction_prices)) AS min_len,
                SUM(LENGTH(live_auction_prices)) AS total_bytes
            FROM market_snapshots
        """))
        r = row.first()
        if r.avg_len:
            print(f"  avg length: {r.avg_len:,.0f} bytes")
            print(f"  max length: {r.max_len:,} bytes")
            print(f"  min length: {r.min_len:,} bytes")
            print(f"  total size: {r.total_bytes / 1024 / 1024:,.1f} MB")
        else:
            print("  (no data)")

        # 5. Duplicate scores (same ea_id + scored_at)
        print("\n" + "=" * 70)
        print("DUPLICATE ANALYSIS")
        print("=" * 70)
        row = await conn.execute(text("""
            SELECT COUNT(*) AS dupes FROM (
                SELECT ea_id, scored_at, COUNT(*) AS cnt
                FROM player_scores
                GROUP BY ea_id, scored_at
                HAVING COUNT(*) > 1
            ) sub
        """))
        r = row.first()
        print(f"  Duplicate player_scores (same ea_id + scored_at): {r.dupes:,}")

        # 6. Scores per player distribution
        print("\n" + "=" * 70)
        print("SCORES PER PLAYER DISTRIBUTION")
        print("=" * 70)
        row = await conn.execute(text("""
            SELECT
                AVG(cnt) AS avg_scores,
                MAX(cnt) AS max_scores,
                MIN(cnt) AS min_scores,
                PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY cnt) AS median_scores
            FROM (SELECT ea_id, COUNT(*) AS cnt FROM player_scores GROUP BY ea_id) sub
        """))
        r = row.first()
        print(f"  avg scores/player: {r.avg_scores:,.1f}")
        print(f"  max scores/player: {r.max_scores:,}")
        print(f"  median scores/player: {r.median_scores:,.0f}")

        # 7. Index analysis
        print("\n" + "=" * 70)
        print("INDEX SIZES (top 20)")
        print("=" * 70)
        rows = await conn.execute(text("""
            SELECT
                indexrelname AS index_name,
                relname AS table_name,
                pg_size_pretty(pg_relation_size(indexrelid)) AS size,
                idx_scan AS scans
            FROM pg_stat_user_indexes
            ORDER BY pg_relation_size(indexrelid) DESC
            LIMIT 20
        """))
        for r in rows:
            print(f"  {r.index_name:<50} {r.size:>10} scans: {r.scans:>8,}")

        # 8. Check if live_auction_prices is read by any code
        print("\n" + "=" * 70)
        print("COLUMN USAGE CHECK")
        print("=" * 70)
        # Count how many market_snapshots have non-empty live_auction_prices
        row = await conn.execute(text("""
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE live_auction_prices IS NOT NULL AND live_auction_prices != '[]' AND live_auction_prices != '') AS non_empty
            FROM market_snapshots
        """))
        r = row.first()
        print(f"  market_snapshots total: {r.total:,}")
        print(f"  live_auction_prices non-empty: {r.non_empty:,}")

        # 9. Inactive players with data
        print("\n" + "=" * 70)
        print("INACTIVE PLAYER DATA")
        print("=" * 70)
        row = await conn.execute(text("""
            SELECT COUNT(*) AS cnt FROM players WHERE is_active = false
        """))
        r = row.first()
        print(f"  Inactive players: {r.cnt:,}")

        for table in ["player_scores", "market_snapshots", "listing_observations"]:
            row = await conn.execute(text(f"""
                SELECT COUNT(*) AS cnt
                FROM {table} t
                JOIN players p ON p.ea_id = t.ea_id
                WHERE p.is_active = false
            """))
            r = row.first()
            print(f"  {table:<30} rows for inactive players: {r.cnt:,}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(analyze())
