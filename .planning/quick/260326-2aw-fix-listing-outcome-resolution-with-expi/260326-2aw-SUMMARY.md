---
phase: quick
plan: 260326-2aw
subsystem: server/listing-tracker
tags: [listing-tracking, scheduling, outcome-resolution, scanner]
dependency_graph:
  requires: []
  provides: [expected_expiry_at on ListingObservation, fixed scan interval]
  affects: [src/server/listing_tracker.py, src/server/scanner.py, src/server/models_db.py, src/config.py]
tech_stack:
  added: []
  patterns: [expiry-gated outcome resolution, fixed-interval scheduling]
key_files:
  created: []
  modified:
    - src/server/models_db.py
    - src/server/listing_tracker.py
    - src/server/scanner.py
    - src/config.py
    - tests/test_listing_tracker.py
    - tests/test_scanner.py
decisions:
  - resolve_outcomes now requires expected_expiry_at IS NOT NULL AND < now, preventing false resolution of listings that rotated off the visible API window
  - SCAN_INTERVAL_SECONDS=300 replaces DEFAULT_SCAN_INTERVAL_SECONDS=3360 and LISTING_SCAN_BUFFER_SECONDS=240; adaptive scheduling removed entirely
  - _extract_remaining_seconds() defaults to 3600.0 when no expiry field present (FC26 minimum listing duration)
  - Listings with NULL expected_expiry_at (pre-migration rows) are excluded from resolution and cleaned up by the retention purge
metrics:
  duration: ~10min
  completed_date: "2026-03-26"
  tasks_completed: 2
  files_modified: 6
---

# Quick Task 260326-2aw: Fix Listing Outcome Resolution with Expiry Gating Summary

**One-liner:** Added expected_expiry_at to ListingObservation and gated resolve_outcomes on it to prevent false sold/expired from API window rotation; replaced adaptive expiry-based scheduling with fixed 5-minute interval.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add expected_expiry_at column and update record_listings + resolve_outcomes | 1d22f74 | models_db.py, listing_tracker.py, test_listing_tracker.py |
| 2 | Replace adaptive scheduling with fixed 5-minute interval and update tests | 2a25124 | config.py, scanner.py, test_scanner.py |

## What Was Built

### Task 1: expected_expiry_at and resolve_outcomes gating

**models_db.py:** Added `expected_expiry_at: Mapped[datetime | None]` column to `ListingObservation` after `last_seen_at`, nullable for backward compatibility with pre-migration rows.

**listing_tracker.py:**
- Added `_extract_remaining_seconds(entry)` helper that reads `expiresOn`/`expires` (ISO datetime), `remainingTime`/`timeRemaining` (numeric), or defaults to 3600.0.
- `record_listings` now computes `expected_expiry_at = now + timedelta(seconds=remaining)` and includes it in both INSERT values and ON CONFLICT update set.
- `resolve_outcomes` query now adds two extra WHERE conditions: `expected_expiry_at IS NOT NULL` and `expected_expiry_at < now`. This prevents resolving listings that merely rotated off the limited API window without actually expiring.

**test_listing_tracker.py:** Updated `_make_live_auction` to accept optional `remaining_seconds` parameter. Updated `test_outcome_sold`, `test_outcome_expired`, and `test_outcome_proportional` to pass `remaining_seconds=-60` so listings are recorded with `expected_expiry_at` already in the past, allowing resolution to proceed in the test.

### Task 2: Fixed 5-minute scan interval

**config.py:** Removed `DEFAULT_SCAN_INTERVAL_SECONDS = 3360` and `LISTING_SCAN_BUFFER_SECONDS = 240`. Added `SCAN_INTERVAL_SECONDS = 300`.

**scanner.py:** Deleted the entire `_classify_and_schedule()` method (56 lines of expiry-based scheduling logic). Replaced the call site in `scan_player` with a simple inline:
```python
if record is not None:
    record.next_scan_at = datetime.utcnow() + timedelta(seconds=SCAN_INTERVAL_SECONDS)
await session.commit()
```

**test_scanner.py:** Removed 3 tests (test_expiry_based_scheduling_uses_max_under_60min, test_expiry_scheduling_defaults_when_no_listing_under_60min, test_expiry_scheduling_no_auctions_uses_default). Added `test_fixed_5min_scan_interval` that calls `scan_player(5001)` and asserts `next_scan_at` is approximately `now + 300s` (within 10s tolerance).

## Test Results

- 100 tests pass (102 original - 3 deleted + 1 new = 100)
- No regressions

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check: PASSED

Files verified:
- `src/server/models_db.py` — expected_expiry_at column present
- `src/server/listing_tracker.py` — _extract_remaining_seconds, expected_expiry_at in record_listings and resolve_outcomes filter
- `src/server/scanner.py` — _classify_and_schedule deleted, SCAN_INTERVAL_SECONDS used inline
- `src/config.py` — SCAN_INTERVAL_SECONDS=300 present, old constants absent
- `tests/test_scanner.py` — test_fixed_5min_scan_interval present, old scheduling tests absent

Commits verified:
- 1d22f74 — Task 1 commit
- 2a25124 — Task 2 commit
