---
phase: quick
plan: 260326-ufn
subsystem: server/scanner
tags: [data-quality, deduplication, naming, scoring]
dependency_graph:
  requires: []
  provides: [deduplicated-snapshot-sales, player-names, scorer-version-tagging]
  affects: [scanner, models_db]
tech_stack:
  added: []
  patterns: [in-memory-dedup-before-insert, unique-constraint-safety-net]
key_files:
  created: []
  modified:
    - src/server/models_db.py
    - src/server/scanner.py
    - tests/test_scanner.py
decisions:
  - "UniqueConstraint on (snapshot_id, sold_at, sold_price) as DB-level safety net; primary dedup is in-memory set"
  - "Bootstrap and discovery on_conflict_do_update includes name field to fix existing records on re-run"
metrics:
  duration: 193s
  completed: "2026-03-26"
  tasks_completed: 2
  tasks_total: 2
  tests_added: 3
  tests_total: 116
---

# Quick Plan 260326-ufn: Fix FUTBIN Findings - Deduplicate Snapshot Sales Summary

UniqueConstraint + in-memory dedup on snapshot_sales; player names extracted from fut.gg commonName/firstName/lastName; scorer_version='v2' set on all v2-scored PlayerScore rows.

## Tasks Completed

### Task 1: Deduplicate snapshot_sales and add unique constraint

- Added `UniqueConstraint("snapshot_id", "sold_at", "sold_price")` to `SnapshotSale` model
- Added in-memory deduplication using `seen_sales` set in `scan_player()` before DB insertion
- Cross-snapshot duplication remains acceptable (each snapshot is a point-in-time capture)
- Commit: `deabcfe`

### Task 2: Populate player names from API and set scorer_version

- `run_bootstrap()`: Extracts `commonName` or `firstName + lastName` from discovery data
- `run_discovery()`: Same name extraction logic for periodic rediscovery
- `scan_player()`: Updates `PlayerRecord.name` from `market_data.player.name`
- Both bootstrap and discovery `on_conflict_do_update` now include `name` field to fix existing records
- `PlayerScore` rows from v2 scorer now have `scorer_version="v2"` explicitly set
- Added 3 new tests: deduplication, name population, scorer_version tagging
- Commit: `70deddb`

## Verification

- All 17 scanner tests pass (14 existing + 3 new)
- Full test suite: 116 tests pass, 0 failures

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing functionality] Bootstrap/discovery on_conflict_do_update includes name**
- **Found during:** Task 2
- **Issue:** The plan specified extracting names in the INSERT values, but existing records would not get their names updated on re-bootstrap/re-discovery since `on_conflict_do_update` did not include `name`
- **Fix:** Added `name` to the `set_` dict in `on_conflict_do_update` for both `run_bootstrap()` and `run_discovery()`
- **Files modified:** src/server/scanner.py

## Known Stubs

None - all data flows are fully wired.

## Self-Check: PASSED
