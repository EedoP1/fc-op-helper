# Phase 3: DB Analysis & Optimization — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Analyze the 7GB database, identify waste and redundancy, propose and implement space optimizations with user approval.

**Architecture:** The database has 10 tables. The largest consumers are likely `market_snapshots` (JSON blob in `live_auction_prices`), `listing_observations`, and `player_scores` (one row per scan per player). Analysis must run against the real production database (PostgreSQL), not test fixtures.

**Tech Stack:** Python 3.12, SQLAlchemy async, PostgreSQL, asyncpg

---

### Task 1: Write DB analysis script

**Files:**
- Create: `scripts/db_analysis.py`

This script connects to the production database and reports:
- Row count per table
- Approximate table size (pg_total_relation_size)
- Oldest and newest timestamps per table
- Orphaned data (scores/snapshots for ea_ids not in `players` table)
- Duplicate rows
- Average size of `live_auction_prices` JSON column
- Index sizes

- [ ] **Step 1: Write the analysis script**

```python
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
                relname AS table,
                pg_size_pretty(pg_total_relation_size(relid)) AS total_size,
                pg_size_pretty(pg_relation_size(relid)) AS data_size,
                pg_size_pretty(pg_total_relation_size(relid) - pg_relation_size(relid)) AS index_size,
                n_live_tup AS row_count
            FROM pg_stat_user_tables
            ORDER BY pg_total_relation_size(relid) DESC
        """))
        for r in rows:
            print(f"  {r.table:<30} {r.total_size:>12} (data: {r.data_size:>10}, idx: {r.index_size:>10}) rows: {r.row_count:>12,}")

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
        print("INDEX SIZES")
        print("=" * 70)
        rows = await conn.execute(text("""
            SELECT
                indexrelname AS index_name,
                relname AS table,
                pg_size_pretty(pg_relation_size(indexrelid)) AS size,
                idx_scan AS scans
            FROM pg_stat_user_indexes
            ORDER BY pg_relation_size(indexrelid) DESC
            LIMIT 20
        """))
        for r in rows:
            print(f"  {r.index_name:<50} {r.size:>10} scans: {r.scans:>8,}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(analyze())
```

- [ ] **Step 2: Run the analysis**

```bash
python scripts/db_analysis.py
```

Capture the full output — this becomes the basis for optimization decisions.

- [ ] **Step 3: Commit the script**

```bash
git add scripts/db_analysis.py
git commit -m "chore: add database analysis script for space optimization"
```

---

### Task 2: Review analysis results and propose optimizations

This task requires reviewing the output from Task 1 with the user. Present findings and propose specific actions based on what the data shows.

- [ ] **Step 1: Present analysis results to user**

Show the output from the analysis script and highlight:
- Which tables consume the most space
- How much of the 7GB is the `live_auction_prices` JSON column
- How many orphaned rows exist
- How many duplicate scores exist
- Whether retention policies are being enforced

- [ ] **Step 2: Propose specific optimizations**

Based on findings, propose concrete changes. Likely candidates:
- Drop `live_auction_prices` column if no code reads it (check first)
- Purge orphaned rows
- Tighten retention on `player_scores` (keep last N per player instead of all)
- Enforce `market_snapshots` 30-day retention more aggressively
- Remove duplicate score rows
- VACUUM FULL after cleanup

- [ ] **Step 3: Get user approval on each proposed change**

Each optimization must be approved individually before implementation.

---

### Task 3: Implement approved optimizations

This task depends entirely on what Task 2 approves. For each approved optimization:

- [ ] **Step 1: Write migration/cleanup script**

Create `scripts/db_cleanup.py` with the approved operations.

- [ ] **Step 2: Run against production with progress output**

```bash
python scripts/db_cleanup.py
```

- [ ] **Step 3: Verify space savings**

```bash
python scripts/db_analysis.py
```

Compare before/after.

- [ ] **Step 4: Update config if retention policies changed**

Modify `src/config.py` with any new retention values.

- [ ] **Step 5: Commit**

```bash
git add scripts/db_cleanup.py src/config.py
git commit -m "chore: implement DB optimizations — [describe what was done]"
```

---

### Task 4: Run tests after DB changes

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/ --ignore=tests/integration -v
```

Expected: All tests still pass. DB cleanup scripts don't affect test fixtures.

- [ ] **Step 2: Run integration tests if config changed**

```bash
python -m pytest tests/integration/ -v
```

Expected: All pass.
