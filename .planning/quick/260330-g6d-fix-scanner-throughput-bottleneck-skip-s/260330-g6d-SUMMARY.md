---
phase: quick
plan: 260330-g6d
subsystem: scanner
tags: [performance, throughput, database]
dependency_graph:
  requires: []
  provides: [scanner-throughput-fix]
  affects: [src/server/scanner.py, src/server/listing_tracker.py, src/config.py]
tech_stack:
  added: []
  patterns: [chunked-upserts, reduced-db-writes]
key_files:
  created: []
  modified:
    - src/server/scanner.py
    - src/server/listing_tracker.py
    - src/config.py
decisions:
  - "DB semaphore raised from 5 to 15 — shorter per-session work (no SnapshotSale/PricePoint inserts) allows more concurrent DB sessions without starving API handlers"
  - "Listing upsert chunk size of 50 balances throughput vs event-loop fairness"
metrics:
  duration_seconds: 134
  completed: "2026-03-30"
  tasks_completed: 2
  tasks_total: 2
---

# Quick Task 260330-g6d: Fix Scanner Throughput Bottleneck Summary

Removed ~170 unnecessary SnapshotSale/SnapshotPricePoint INSERT statements per player scan, raised DB semaphore from 5 to 15, increased dispatch batch size from 200 to 500, and batched listing tracker upserts in chunks of 50.

## Changes Made

### Task 1: Remove SnapshotSale/SnapshotPricePoint inserts and raise DB semaphore
**Commit:** 1888902

- Removed SnapshotSale and SnapshotPricePoint imports from scanner.py
- Removed the entire block inserting SnapshotSale rows (dedup set + loop) and SnapshotPricePoint rows
- Removed `await session.flush()` since snapshot.id is no longer needed for FK references
- Kept MarketSnapshot insert (needed for cleanup cascade FK)
- Raised `_db_semaphore` from 5 to 15 concurrent sessions
- Raised `SCAN_DISPATCH_BATCH_SIZE` from 200 to 500 in config.py (5000 capacity per 5min vs 2014 needed)

**Files:** src/server/scanner.py, src/config.py

### Task 2: Batch listing tracker upserts
**Commit:** 9106250

- Replaced per-entry upsert loop with two-phase approach: collect values first, then execute in chunks of 50
- Event-loop yield (`asyncio.sleep(0)`) now happens once per 50-entry chunk instead of every 20 entries
- Reduces context-switch overhead while still preventing event loop starvation

**Files:** src/server/listing_tracker.py

## Throughput Impact

**Before:** ~170 INSERTs per player (1 MarketSnapshot + ~100 SnapshotSale + ~70 SnapshotPricePoint) with DB semaphore of 5 and batch size of 200. Theoretical max: 0.59 scans/sec.

**After:** ~1 INSERT per player for MarketSnapshot + listing upserts + 1 PlayerScore. DB semaphore of 15 and batch size of 500. Theoretical capacity: 5000 scans per 5-minute cycle, well above 2014 active players.

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None.

## Self-Check: PASSED
