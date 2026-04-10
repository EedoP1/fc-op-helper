---
status: awaiting_human_verify
trigger: "Run all integration tests to surface server bugs after SQLite to PostgreSQL migration"
created: 2026-03-28T00:00:00Z
updated: 2026-03-29T02:00:00Z
---

## Current Focus

hypothesis: Three server bugs (timezone, dedup, rebalance) fixed in prior session; event-loop starvation + cold index fixes applied now
test: Human verification — run integration tests and confirm all pass
expecting: All integration tests pass with no ReadTimeout failures
next_action: Await human confirmation

## Symptoms

expected: All integration tests pass against the server
actual: Multiple failures — /players/top timeout, /portfolio/confirm crash on dupe ea_ids, /portfolio/rebalance 405
errors: TypeError (naive vs aware datetime), IntegrityError (UNIQUE constraint), 405 Method Not Allowed
reproduction: Run the integration test suite
started: Server recently migrated from SQLite to PostgreSQL (Phase 09.1)

## Eliminated

## Evidence

- timestamp: 2026-03-29T00:30:00Z
  checked: Full test run (79 tests)
  found: Many tests fail with ReadTimeout — server hangs/crashes after certain operations
  implication: Cascading failures from server crashes, not individual test issues

- timestamp: 2026-03-29T01:00:00Z
  checked: test_portfolio_rebalance.py (6 tests)
  found: All return 405 Method Not Allowed — endpoint does not exist
  implication: POST /portfolio/rebalance not implemented in server

- timestamp: 2026-03-29T01:10:00Z
  checked: test_portfolio_confirm_duplicate_ea_ids (isolated)
  found: Server returns 500 + timeout on POST /portfolio/confirm with duplicate ea_ids
  implication: confirm_portfolio inserts without dedup, UNIQUE constraint on portfolio_slots.ea_id crashes asyncpg

- timestamp: 2026-03-29T01:20:00Z
  checked: GET /players/top endpoint (isolated)
  found: Always times out at 120s even with limit=1, even when generate works in 1s
  implication: Not a query performance issue — something else in the response path

- timestamp: 2026-03-29T01:25:00Z
  checked: last_scanned_at timezone from asyncpg
  found: asyncpg returns tz-aware datetime (UTC), but stale_cutoff is naive (datetime.utcnow())
  implication: TypeError on comparison crashes /players/top and /players/{ea_id} response serialization

## Resolution

root_cause: Four bugs total:
  1. Timezone mismatch: asyncpg returns tz-aware datetimes, but get_top_players and get_player compare with naive utcnow() — TypeError crashes response (fixed in prior session)
  2. Missing dedup: confirm_portfolio inserts duplicate ea_ids without dedup, violating UNIQUE constraint (fixed in prior session)
  3. Missing endpoint: POST /portfolio/rebalance not implemented — 405 Method Not Allowed (fixed in prior session)
  4. Event-loop starvation: dispatch_scans() awaited asyncio.gather() on all 200 scan tasks, blocking the APScheduler callback for up to 216s per tenacity retry cycle and starving FastAPI request handlers — causing ReadTimeout on all API calls during scan bursts
fix:
  1-3. Applied in prior session (players.py, portfolio.py)
  4a. dispatch_scans() rewritten as fire-and-forget: creates tasks with asyncio.create_task(), stores refs in self._active_tasks (prevents GC), returns immediately. APScheduler callback now completes in <1ms. (src/server/scanner.py)
  4b. Cache warmup in test harness extended to exercise the composite index ix_market_snapshots_ea_id_captured_at via the actual GROUP BY pattern used by _get_volatile_ea_ids, and the joined viable-candidates query pattern used by all portfolio endpoints. (tests/integration/server_harness.py)
  4c. scan_dispatch scheduler job re-enabled in test harness. Now that dispatch_scans() is fire-and-forget, it no longer starves the event loop, so tests run with realistic background concurrency. (tests/integration/server_harness.py)
verification: awaiting human confirmation
files_changed:
  - src/server/scanner.py
  - tests/integration/server_harness.py
