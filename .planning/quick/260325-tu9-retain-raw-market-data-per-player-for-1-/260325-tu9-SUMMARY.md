---
phase: quick
plan: 260325-tu9
subsystem: server/scanner
tags: [persistence, market-data, cleanup, database]
dependency_graph:
  requires: [src/server/scanner.py, src/server/models_db.py, src/server/db.py]
  provides: [MarketSnapshot, SnapshotSale, SnapshotPricePoint, run_cleanup]
  affects: [src/server/scheduler.py, src/config.py]
tech_stack:
  added: []
  patterns: [FK cascade, JSON column for list storage, daily cleanup job]
key_files:
  created: []
  modified:
    - src/server/models_db.py
    - src/server/scanner.py
    - src/server/scheduler.py
    - src/config.py
    - src/server/db.py
    - tests/test_scanner.py
decisions:
  - Store live_auction_prices as JSON-encoded Text column (avoids extra table for simple int list)
  - Enable PRAGMA foreign_keys=ON in SQLite for FK cascade DELETE support
metrics:
  duration: 5min
  completed: 2026-03-25
  tasks: 3
  files: 6
---

# Quick Task 260325-tu9: Retain Raw Market Data Per Player for 1 Month

Raw market snapshot persistence with 30-day retention -- MarketSnapshot/SnapshotSale/SnapshotPricePoint tables store sales, price history, and live auction BINs from each scan for future backtesting and trend analysis.

## What Changed

### Task 1: Add MarketSnapshot DB models and config constant
- Added `MarketSnapshot`, `SnapshotSale`, `SnapshotPricePoint` ORM models to `models_db.py`
- Composite index on `(ea_id, captured_at)` and standalone index on `captured_at` for efficient queries
- FK cascade DELETE on child tables ensures clean removal
- Added `MARKET_DATA_RETENTION_DAYS = 30` to `config.py`
- Commit: `5e9f142`

### Task 2: Persist raw market data in scan_player and add cleanup job
- `scan_player()` now creates a `MarketSnapshot` with all sales and price points after scoring
- `run_cleanup()` method deletes snapshots and scores older than 30 days
- Scheduler registers daily cleanup job via `IntervalTrigger(hours=24)`
- Commit: `6512d0e`

### Task 3: Add tests for snapshot persistence and cleanup
- 6 new tests covering: snapshot creation, sales rows, price point rows, None market data guard, cleanup deletion, cleanup preservation
- Enabled `PRAGMA foreign_keys=ON` in `db.py` to support FK cascade DELETE on SQLite
- Updated `create_engine_and_tables` to import new models for auto table creation
- Commit: `4229a2d`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] SQLite FK cascade not enforced without PRAGMA**
- **Found during:** Task 3 (test for cascade delete failed)
- **Issue:** SQLite ignores `ON DELETE CASCADE` unless `PRAGMA foreign_keys=ON` is set per connection
- **Fix:** Added `cursor.execute("PRAGMA foreign_keys=ON")` to the WAL-mode event listener in `db.py`
- **Files modified:** `src/server/db.py`
- **Commit:** `4229a2d`

**2. [Rule 2 - Missing functionality] New models not imported in create_engine_and_tables**
- **Found during:** Task 3
- **Issue:** `create_engine_and_tables` only imported `PlayerRecord, PlayerScore` -- new tables would not be created on startup
- **Fix:** Added `MarketSnapshot, SnapshotSale, SnapshotPricePoint` to the import in `create_engine_and_tables`
- **Files modified:** `src/server/db.py`
- **Commit:** `4229a2d`

## Decisions Made

1. **JSON column for live_auction_prices**: Stored as `json.dumps(list[int])` in a Text column rather than a separate table. The data is write-once/read-rarely and a normalized table would add complexity for no query benefit.
2. **FK pragma enablement**: Added to the existing WAL-mode connection listener so every connection gets it. This is the standard approach for SQLite FK support.

## Known Stubs

None -- all data is wired end-to-end.

## Verification

- All 75 tests pass (21 scanner tests including 6 new ones)
- Models import correctly
- Cleanup respects retention window with cascade delete
