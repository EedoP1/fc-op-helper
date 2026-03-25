---
phase: 04-refactor-scoring-db
verified: 2026-03-25T22:00:00Z
status: passed
score: 10/10 must-haves verified
re_verification: false
gaps: []
human_verification:
  - test: "Run the server 24-48 hours and observe ListingObservation rows accumulating then transitioning to v2 scorer"
    expected: "Players accumulate >=10 resolved observations, scorer_version flips from v1 to v2 in player_scores DB rows"
    why_human: "Requires real fut.gg liveAuctions data and time passage — cannot simulate with unit tests"
  - test: "Trigger _classify_and_schedule with a real liveAuctions response containing expiresOn field and confirm next_scan_at is ~26 minutes from now (30min expiry minus 4min buffer)"
    expected: "PlayerRecord.next_scan_at is set to roughly now+26min, not the tier-based default of 30/60/150 min"
    why_human: "Depends on live API data shape — fut.gg may or may not include expiresOn; cannot verify field availability without a real response"
---

# Phase 4: Refactor Scoring + DB Verification Report

**Phase Goal:** Replace the current scoring model with a listing-tracking system that records every individual listing, determines which sold vs expired, and computes a true OP sell conversion rate expressed as expected_profit_per_hour
**Verified:** 2026-03-25T22:00:00Z
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | ListingObservation and DailyListingSummary tables exist in the DB | VERIFIED | `class ListingObservation(Base)` and `class DailyListingSummary(Base)` in `src/server/models_db.py` lines 100-133; both imported in `src/server/db.py` line 57 |
| 2 | Each liveAuctions entry is upserted as a ListingObservation with a deterministic fingerprint | VERIFIED | `record_listings()` in `src/server/listing_tracker.py` uses `sqlite_insert().on_conflict_do_update()` on fingerprint; test_fingerprint_upsert confirms dedup |
| 3 | Disappeared listings are resolved as sold (matching completedAuctions) or expired (no match) | VERIFIED | `resolve_outcomes()` in `listing_tracker.py` implements proportional assignment; test_outcome_sold/expired/proportional all pass |
| 4 | score_player_v2 computes expected_profit_per_hour from accumulated listing data | VERIFIED | `src/server/scorer_v2.py` implements D-10 formula: `expected_profit_per_hour = net_profit * op_sell_rate * op_sales_per_hour`; test_expected_profit_per_hour passes with exact value 140.625 |
| 5 | Returns None when fewer than BOOTSTRAP_MIN_OBSERVATIONS resolved listings exist | VERIFIED | Guard at line 70 of scorer_v2.py; test_bootstrap_min confirms None returned for 5 obs |
| 6 | scan_player() calls record_listings() and resolve_outcomes() during each scan | VERIFIED | `src/server/scanner.py` lines 299-315 call both functions; imports confirmed at line 41 |
| 7 | scan_player() writes v2 score when enough data exists, falls back to v1 | VERIFIED | Lines 317-343 of scanner.py attempt score_player_v2; writes `scorer_version="v2"` when v2_result present, `"v1"` otherwise; test_v2_scorer_writes_score passes |
| 8 | Adaptive scan timing uses youngest listing expiry (D-05, D-06) | VERIFIED | `_classify_and_schedule()` lines 499-527 parse expiresOn/remainingTime fields and apply LISTING_SCAN_BUFFER_SECONDS; test_adaptive_next_scan passes |
| 9 | Scheduler runs v2 scoring job (every 15min) and daily aggregation | VERIFIED | `src/server/scheduler.py` adds `scoring_v2` job (IntervalTrigger 15min) and `aggregation` job (24h) at lines 54-74 |
| 10 | API endpoints expose expected_profit_per_hour and scorer_version | VERIFIED | `api/players.py` lines 97-98 (top players), 209-210 (player detail), 220 (score history); `api/portfolio.py` lines 52-53 (_build_scored_entry) and 135-136 (response) |

**Score:** 10/10 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/server/models_db.py` | ListingObservation, DailyListingSummary ORM models | VERIFIED | Both classes present lines 100-133; PlayerScore extended with expected_profit_per_hour (line 48) and scorer_version (line 49) |
| `src/config.py` | Listing tracking config constants | VERIFIED | LISTING_RETENTION_DAYS=7, BOOTSTRAP_MIN_OBSERVATIONS=10, LISTING_SCAN_BUFFER_SECONDS=240, SCORING_JOB_INTERVAL_MINUTES=15, AGGREGATION_HOUR_UTC=3, MIN_OP_OBSERVATIONS=3 all present lines 43-48 |
| `src/futgg_client.py` | Raw liveAuctions data in PlayerMarketData | VERIFIED | `live_auctions_raw=raw_auctions` at line 126; `log_live_auction_fields` static method at line 266 |
| `src/server/db.py` | New tables registered in create_engine_and_tables | VERIFIED | Line 57 imports ListingObservation, DailyListingSummary |
| `src/server/listing_tracker.py` | record_listings, resolve_outcomes, aggregate_daily_summaries | VERIFIED | All three public async functions present; _is_op_listing helper at line 69; sqlite_insert upsert at lines 118-138 |
| `src/server/scorer_v2.py` | score_player_v2 with D-10 formula | VERIFIED | Function present, queries ListingObservation, evaluates all MARGINS, picks max expected_profit_per_hour |
| `src/server/scanner.py` | Listing tracking integration, run_scoring, run_aggregation, adaptive timing, purge | VERIFIED | All required elements present; imports at lines 41-43; listing tracking at lines 299-315; v2 scoring at 317-324; run_scoring at line 578; run_aggregation at line 628; listing purge at lines 682-697 |
| `src/server/scheduler.py` | scoring_v2 and aggregation jobs | VERIFIED | Both jobs added at lines 54-74 |
| `src/server/api/players.py` | expected_profit_per_hour and scorer_version in responses | VERIFIED | Present in both get_top_players and get_player endpoints |
| `src/server/api/portfolio.py` | expected_profit_per_hour and scorer_version in portfolio response | VERIFIED | Present in _build_scored_entry (lines 52-53) and portfolio response serialization (lines 135-136) |
| `tests/test_listing_tracker.py` | Unit tests for listing tracker | VERIFIED | 7 tests covering fingerprint upsert, outcome resolution (sold/expired/proportional), daily summary, and OP classification |
| `tests/test_scorer_v2.py` | Unit tests for v2 scorer | VERIFIED | 6 tests covering D-10 formula, margin selection, bootstrap threshold, no-obs guard, insufficient OP, and return dict shape |
| `tests/test_scanner.py` | test_adaptive_next_scan, test_listing_purge | VERIFIED | Both tests present at lines 623 and 666 |
| `tests/test_integration.py` | test_v2_scorer_writes_score | VERIFIED | Full integration test seeding ListingObservations and asserting v2 fields written to PlayerScore |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `src/server/models_db.py` | `src/server/db.py` | Base import and table registration | VERIFIED | `from src.server.models_db import ... ListingObservation, DailyListingSummary` at db.py line 57 |
| `src/server/listing_tracker.py` | `src/server/models_db.py` | ListingObservation and DailyListingSummary ORM imports | VERIFIED | `from src.server.models_db import DailyListingSummary, ListingObservation` at listing_tracker.py line 25 |
| `src/server/listing_tracker.py` | `src/config.py` | Config constant imports | VERIFIED | `from src.config import MIN_OP_OBSERVATIONS` at listing_tracker.py line 22 |
| `src/server/scorer_v2.py` | `src/server/models_db.py` | ListingObservation query | VERIFIED | `select(ListingObservation)` at scorer_v2.py lines 61-67 |
| `src/server/scorer_v2.py` | `src/config.py` | Config imports for thresholds | VERIFIED | `from src.config import ... BOOTSTRAP_MIN_OBSERVATIONS, MIN_OP_OBSERVATIONS` at scorer_v2.py lines 23-28 |
| `src/server/scanner.py` | `src/server/listing_tracker.py` | record_listings and resolve_outcomes calls | VERIFIED | `from src.server.listing_tracker import record_listings, resolve_outcomes` at line 41; both called at lines 303 and 310 |
| `src/server/scanner.py` | `src/server/scorer_v2.py` | score_player_v2 call during scan | VERIFIED | `from src.server.scorer_v2 import score_player_v2` at line 43; called at line 320 |
| `src/server/scheduler.py` | `src/server/scanner.py` | run_scoring and run_aggregation job scheduling | VERIFIED | `scanner.run_scoring` at scheduler.py line 56; `scanner.run_aggregation` at line 63 |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `api/players.py` `get_top_players` | `score.expected_profit_per_hour` | DB query via PlayerScore join | Yes — populated by score_player_v2 during scan_player() and run_scoring() | FLOWING |
| `api/portfolio.py` `get_portfolio` | `score.expected_profit_per_hour` | DB query via PlayerScore join | Yes — same pipeline; null when v2 scorer returns None (bootstrapping) | FLOWING |
| `src/server/scanner.py` `scan_player` | `v2_result` | score_player_v2 querying ListingObservation rows | Yes — queries real resolved observations from DB | FLOWING |
| `src/server/listing_tracker.py` `record_listings` | `live_auctions_raw` | Passed from scan_player via market_data.live_auctions_raw | Yes — FutGGClient populates raw dicts from fut.gg liveAuctions API | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Full test suite passes | `python -m pytest tests/ -x -q` | 107 passed, 0 failed | PASS |
| listing tracker tests | `python -m pytest tests/test_listing_tracker.py -x -q` | 7 passed | PASS |
| scorer v2 tests | `python -m pytest tests/test_scorer_v2.py -x -q` | 6 passed | PASS |
| scanner integration | `python -m pytest tests/test_scanner.py tests/test_integration.py -x -q` | 34 passed | PASS |
| Config imports | `python -c "from src.config import LISTING_RETENTION_DAYS, BOOTSTRAP_MIN_OBSERVATIONS, MIN_OP_OBSERVATIONS; print('OK')"` | OK | PASS |
| ORM model imports | `python -c "from src.server.models_db import ListingObservation, DailyListingSummary; print('OK')"` | OK (inferred from test_db pass) | PASS |

### Requirements Coverage

The SCAN-P4-xx requirement IDs referenced in all four plan files (SCAN-P4-01 through SCAN-P4-10) are **not present in .planning/REQUIREMENTS.md**. The traceability table in REQUIREMENTS.md ends at Phase 3, with no Phase 4 entry. This is a documentation gap — the requirements exist in the plan files but have not been back-ported to REQUIREMENTS.md.

Each plan claims the following IDs:

| Requirement ID | Source Plan | Coverage Area | Implementation Status |
|----------------|-------------|---------------|-----------------------|
| SCAN-P4-01 | 04-01, 04-02 | ListingObservation DB model, fingerprint upsert | SATISFIED — model and upsert both verified |
| SCAN-P4-02 | 04-02 | Outcome resolution (sold/expired classification) | SATISFIED — resolve_outcomes() verified with 3 tests |
| SCAN-P4-03 | 04-02 | Same-price ambiguity proportional handling | SATISFIED — test_outcome_proportional passes |
| SCAN-P4-04 | 04-03 | expected_profit_per_hour D-10 formula | SATISFIED — scorer_v2.py implements formula, test confirms exact value |
| SCAN-P4-05 | 04-03 | Margin selection maximizing expected_profit_per_hour | SATISFIED — test_margin_selection passes |
| SCAN-P4-06 | 04-03 | Bootstrap threshold guard (BOOTSTRAP_MIN_OBSERVATIONS) | SATISFIED — test_bootstrap_min passes |
| SCAN-P4-07 | 04-04 | scan_player integrates listing tracking + v2 scorer | SATISFIED — both wired in scan_player(); test_v2_scorer_writes_score passes |
| SCAN-P4-08 | 04-04 | Bootstrapping fallback: v1 scorer during bootstrap period | SATISFIED — scorer_version="v1" written when v2_result is None |
| SCAN-P4-09 | 04-02 | Daily aggregation (DailyListingSummary per margin per day) | SATISFIED — aggregate_daily_summaries() verified with test_daily_summary |
| SCAN-P4-10 | 04-04 | Listing purge (7-day retention for resolved and orphaned obs) | SATISFIED — run_cleanup() purges both types; test_listing_purge passes |

**ORPHANED requirement IDs:** SCAN-P4-01 through SCAN-P4-10 are referenced in phase 4 plans but do not appear in `.planning/REQUIREMENTS.md` traceability table. This is a documentation debt — REQUIREMENTS.md should be updated to include these IDs and map them to Phase 4.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| Multiple files | Various | `datetime.utcnow()` deprecated in Python 3.12+ | Info | 97 deprecation warnings in test suite — no functional impact, but forward compatibility concern |
| `src/server/scanner.py` | 508 | `from datetime import timezone as _tz` inline import inside loop | Info | Minor style issue — works correctly but unusual placement |

No blockers or stubs found. The `expected_profit_per_hour=None` in portfolio/player responses is intentional and documented as the bootstrapping state, not a placeholder.

### Human Verification Required

#### 1. Live Bootstrapping to V2 Transition

**Test:** Start the server fresh, let it scan for 24-48 hours, then query `GET /api/v1/players/top` and check `scorer_version` values
**Expected:** After enough scans, some players have `scorer_version: "v2"` and non-null `expected_profit_per_hour` values in the response
**Why human:** Requires real fut.gg liveAuctions data accumulating over time — the bootstrap threshold (10 resolved observations) cannot be reached in unit tests representing live production behavior

#### 2. Adaptive Listing Expiry Timing

**Test:** Monitor a live scan cycle and check whether `PlayerRecord.next_scan_at` values reflect listing expiry times rather than fixed tier intervals for players with active listings
**Expected:** Players with short-expiry listings have `next_scan_at` set 4+ minutes before the youngest expiry, not the standard 30/60/150-minute tier intervals
**Why human:** Depends on fut.gg liveAuctions response shape — whether `expiresOn`, `expires`, `remainingTime`, or `timeRemaining` fields are actually returned cannot be verified without a live API response

### Gaps Summary

No gaps found. All phase goal components are implemented, wired, tested, and passing.

The only outstanding item is a documentation debt: SCAN-P4-01 through SCAN-P4-10 requirement IDs should be added to `.planning/REQUIREMENTS.md` to complete the traceability table for Phase 4.

---

_Verified: 2026-03-25T22:00:00Z_
_Verifier: Claude (gsd-verifier)_
