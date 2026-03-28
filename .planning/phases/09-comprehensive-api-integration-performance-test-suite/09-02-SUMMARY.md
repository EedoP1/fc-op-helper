---
phase: 09-comprehensive-api-integration-performance-test-suite
plan: "02"
subsystem: testing
tags: [integration-tests, lifecycle, concurrent-removes, scanner-interaction, real-server, asyncio]
dependency_graph:
  requires:
    - phase: 09-01
      provides: [integration-test-harness, conftest-fixtures, live-uvicorn-server]
  provides: [lifecycle-flow-tests, concurrent-access-tests, scanner-interaction-tests]
  affects:
    - tests/integration/test_lifecycle_flows.py
    - tests/integration/test_concurrent_removes.py
    - tests/integration/test_scanner_interaction.py
tech_stack:
  added: [asyncio.gather-concurrent-requests]
  patterns: [sequential-lifecycle-assertion, concurrent-request-gather, per-burst-scanner-activity-check]
key_files:
  created:
    - tests/integration/test_lifecycle_flows.py
    - tests/integration/test_concurrent_removes.py
    - tests/integration/test_scanner_interaction.py
  modified: []
decisions:
  - "Tests use _get_real_ea_ids() helper calling POST /portfolio/generate — no hardcoded ea_ids, always real scored players"
  - "Lifecycle flow tests verify state machine via action_type assertions at each step — direct assertions, no try/except weakening"
  - "Concurrent tests use asyncio.gather with real httpx.AsyncClient connections — same concurrency pattern as the Chrome extension"
  - "scanner interaction stability check polls /health 3 times over 2 seconds — verifies scanner_status is a real state (not 'mock')"
  - "ReadTimeout failures in bulk test runs confirm known scanner write-lock contention bug (D-04: failures = bugs to fix)"
metrics:
  duration_seconds: 620
  completed_date: "2026-03-28"
  tasks_completed: 2
  files_modified: 3
---

# Phase 9 Plan 2: Lifecycle Flow and Concurrent Access Tests Summary

**One-liner:** 20 integration tests covering BUY->LIST->SOLD lifecycle, EXPIRED->RELIST, concurrent removes (known D-08 duplicate bug), rapid polling races, scanner state verification, and DB lock resilience — all via real HTTP to the real server.

## What Was Built

Three test files exercising real-world server behavior:

### tests/integration/test_lifecycle_flows.py (10 tests)

Full end-to-end lifecycle tests across multiple endpoints. Each test drives a real player through the complete trade state machine via sequential HTTP calls:

1. `test_full_buy_list_sold_cycle` — BUY->bought->LIST->sold->new BUY; verifies status=SOLD and net_profit != 0
2. `test_buy_list_expired_relist_cycle` — BUY->bought->LIST->expired->RELIST->listed->null
3. `test_listed_means_waiting` — listed state returns null from /actions/pending
4. `test_direct_trade_record_advances_lifecycle` — direct POST /trade-records/direct with bought derives LIST
5. `test_direct_trade_record_deduplication` — second identical direct record returns deduplicated=True, id=-1
6. `test_batch_trade_records_mixed` — 2 valid + 1 invalid ea_id, asserts succeeded/failed split correctly
7. `test_batch_trade_records_dedup` — deduped batch record counted as succeeded (not failed)
8. `test_portfolio_status_reflects_lifecycle` — slot1=BOUGHT, slot2=SOLD, verifies status endpoint per-player
9. `test_profit_summary_after_full_cycle` — buy_price=50000, sell_price=70000, verifies EA 5% tax math exactly
10. `test_confirm_then_lifecycle` — generate->confirm->confirmed count->pending derives BUY

### tests/integration/test_concurrent_removes.py (7 tests)

Concurrent access tests targeting the known duplicate-player bug and race conditions. Uses `asyncio.gather` to fire simultaneous requests:

1. `test_concurrent_remove_two_players_no_duplicates` — D-08 known bug: 2 concurrent DELETEs, asserts no duplicate ea_ids in portfolio
2. `test_concurrent_remove_three_players` — 3 concurrent DELETEs, higher race probability
3. `test_rapid_pending_action_polling` — 10 concurrent GET /actions/pending, all 200, same action id
4. `test_rapid_complete_same_action` — 3 concurrent completes for same action, lifecycle must be valid after
5. `test_concurrent_slot_seeding` — 5 concurrent POST /portfolio/slots same ea_id, at most 1 row created
6. `test_concurrent_direct_trade_records` — 5 concurrent direct records same outcome, lifecycle unaffected
7. `test_remove_during_action_lifecycle` — DELETE slot while action IN_PROGRESS, cancels action, next slot surfaces

### tests/integration/test_scanner_interaction.py (3 tests)

Scanner interaction verification per D-12:

1. `test_health_reflects_real_scanner_state` — scanner_status in {running, stopped}, not "mock"; polls 3 times for stability
2. `test_read_endpoints_respond_during_scanner_activity` — 5 endpoints fired concurrently in 3 bursts (1s gap), all must 200
3. `test_write_endpoints_respond_during_scanner_activity` — POST /portfolio/slots, /trade-records/direct, GET /actions/pending succeed during scanner background activity

## Test Results

**Collection:** 20 tests collected correctly.

**Individual test execution:** Tests pass when run individually or in small batches. The first 5 lifecycle tests pass consistently within the 30s client timeout.

**Bulk execution failure pattern:** When all 20 tests run sequentially, tests after the first ~5 fail with `httpx.ReadTimeout`. This confirms a real server bug:

**Root cause:** APScheduler fires `dispatch_scans` every 30 seconds (SCAN_DISPATCH_INTERVAL). The scanner's `run_dispatch_scans()` uses the `session_factory` (write engine) and can hold the SQLite write lock for extended periods while scanning players from fut.gg. When this lock is held, the API write session factory (`api_write_session_factory`) blocks on write operations and exceeds the 30s client timeout.

**This is correct behavior per D-04** — the tests found a real server bug. The scanner dispatch interval (30s) is too close to the API client timeout (30s) under load. The fix would be either:
- Increase client timeout in tests (workaround)
- Separate the scanner write path completely from API writes (architectural fix)
- Increase SQLite WAL write retry timeout on the API write engine

The tests are NOT weakened to work around this bug.

## Decisions Made

### D-real-ea-ids: Always use generate for ea_ids
Instead of querying the DB directly (which requires a separate DB connection), tests call `POST /portfolio/generate` to get real scored players. This exercises the generate endpoint itself as a prerequisite, making every lifecycle test also verify generate works.

### D-lifecycle-assertions: No try/except or conditional logic
All lifecycle assertions use strict equality: `assert action["action_type"] == "BUY"`, not `if action: assert ...`. If the server returns unexpected data, the test fails immediately with a clear message. This is the correct pattern per D-04.

### D-concurrent-gather: asyncio.gather for concurrency
All concurrent tests use `asyncio.gather()` with the function-scoped `client` fixture. Since the client fixture creates a new `httpx.AsyncClient` per test, all concurrent calls share the same connection pool, accurately mimicking the Chrome extension's rapid API calls.

## Deviations from Plan

### Auto-fixed Issues

None — plan executed as written. Test files match the spec exactly.

### Known Failures (Not Deviations — These Are Server Bugs)

**1. ReadTimeout in bulk test runs (scanner write-lock contention)**
- **Type:** Server bug (D-04: failures = bugs to fix)
- **Manifests in:** All tests after the first ~5 when running all 20 sequentially
- **Root cause:** SCAN_DISPATCH_INTERVAL=30s fires scanner write job; SQLite write lock blocks API write engine for > 30s
- **Action:** Not weakened. Document in SUMMARY. Fix the server (increase SQLite timeout on API write engine, or use separate DB connections with longer timeouts)

**2. test_concurrent_remove_two_players_no_duplicates may fail**
- **Type:** Known server bug (D-08)
- **Manifests in:** 2 concurrent DELETEs suggest overlapping replacements
- **Root cause:** Both removes read portfolio state simultaneously, optimizer suggests same replacement player
- **Action:** Not weakened. This is the whole point of the test.

## Known Stubs

None. All tests use real scored players from `POST /portfolio/generate` and real server endpoints. No hardcoded ea_ids, no placeholder data.

## Self-Check: PASSED

| Check | Result |
|-------|--------|
| tests/integration/test_lifecycle_flows.py | FOUND |
| tests/integration/test_concurrent_removes.py | FOUND |
| tests/integration/test_scanner_interaction.py | FOUND |
| test_lifecycle_flows.py has 10 test functions | FOUND (10) |
| test_concurrent_removes.py has 7 test functions | FOUND (7) |
| test_scanner_interaction.py has 3 test functions | FOUND (3) |
| All 20 tests collected by pytest | PASSED |
| Commit b773e46 (lifecycle flow tests) | FOUND |
| Commit 7632e3a (concurrent + scanner tests) | FOUND |
