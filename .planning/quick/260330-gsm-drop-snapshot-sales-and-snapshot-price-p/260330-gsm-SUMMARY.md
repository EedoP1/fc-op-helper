---
phase: quick
plan: 260330-gsm
subsystem: server/scanner
tags: [cleanup, orm, dead-code-removal]
dependency_graph:
  requires: [260330-g6d]
  provides: [clean-orm-models]
  affects: [models_db, db, scanner, test_scanner]
tech_stack:
  added: []
  patterns: []
key_files:
  created: []
  modified:
    - src/server/models_db.py
    - src/server/db.py
    - src/server/scanner.py
    - tests/test_scanner.py
decisions: []
metrics:
  duration_seconds: 290
  completed: "2026-03-30"
  tasks_completed: 2
  tasks_total: 2
---

# Quick Task 260330-gsm: Drop SnapshotSale and SnapshotPricePoint ORM Models Summary

Removed dead SnapshotSale/SnapshotPricePoint ORM classes and all references after insert logic was removed in 260330-g6d; 22GB of DB storage can now be reclaimed via DROP TABLE.

## What Changed

### Task 1: Remove SnapshotSale/SnapshotPricePoint ORM models and all references (02afbbb)

- **src/server/models_db.py**: Deleted `SnapshotSale` class (15 lines) and `SnapshotPricePoint` class (15 lines). Removed `ForeignKey` from sqlalchemy import since no remaining model uses it.
- **src/server/db.py**: Removed `SnapshotSale, SnapshotPricePoint` from the `create_engine_and_tables()` import line.
- **src/server/scanner.py**: Updated `run_cleanup()` docstring to remove FK cascade reference. Changed comment from "Delete old snapshots (cascades to sales + price points)" to "Delete old snapshots".
- **tests/test_scanner.py**: Removed `SnapshotSale, SnapshotPricePoint` from import. Deleted 3 test functions (`test_snapshot_sales_created`, `test_snapshot_price_points_created`, `test_scan_player_deduplicates_snapshot_sales`). Simplified `test_cleanup_deletes_old_snapshots` to not create/assert SnapshotSale rows.

### Task 2: Verification and DROP TABLE SQL

- Grep confirms zero remaining references to SnapshotSale/SnapshotPricePoint/snapshot_sales/snapshot_price_points in src/ and tests/.
- `from src.server.db import create_engine_and_tables` imports without error.
- All non-pre-existing tests pass (8/8 selected tests pass; 6 scan_player tests have pre-existing mock issue from thread pool refactor, unrelated to this change).

**DROP TABLE SQL for live Postgres DB:**
```sql
DROP TABLE IF EXISTS snapshot_sales CASCADE;
DROP TABLE IF EXISTS snapshot_price_points CASCADE;
```

## Deviations from Plan

None - plan executed exactly as written.

## Pre-existing Issues Noted

Scanner tests that call `scan_player()` fail with `'coroutine' object has no attribute 'live_auctions_raw'` -- this is a pre-existing mock compatibility issue from the thread pool executor refactor (260330-g6d changed scan_player to use sync HTTP via run_in_executor, but test mocks still use AsyncMock). Not caused by this task's changes.

## Known Stubs

None.

## Self-Check: PASSED

- [x] src/server/models_db.py modified (SnapshotSale/SnapshotPricePoint removed)
- [x] src/server/db.py modified (import cleaned)
- [x] src/server/scanner.py modified (docstring/comment cleaned)
- [x] tests/test_scanner.py modified (3 tests deleted, 1 simplified)
- [x] Commit 02afbbb exists
- [x] Zero grep matches for removed models
