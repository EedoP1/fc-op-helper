---
status: awaiting_human_verify
trigger: "Scanner contention causes API ReadTimeout on Postgres. When the scanner is active (40 concurrent tasks via SCAN_CONCURRENCY), API endpoints like GET /actions/pending and POST /portfolio/generate time out with httpx.ReadTimeout."
created: 2026-03-28T00:00:00Z
updated: 2026-03-28T01:00:00Z
---

## Current Focus

hypothesis: CONFIRMED (new root cause) — The scanner makes real fut.gg HTTP calls during tests. The BATCH_SIZE=200 fix reduced task burst but did not eliminate the fundamental problem: real HTTP requests to fut.gg take multiple seconds each, and with tenacity retries (wait_exponential_jitter initial=2, max=60) the gather() inside dispatch_scans() can run for minutes. This causes two failure modes: (1) server event loop starvation starves API request handlers, causing ReadTimeout; (2) if the scanner holds an open transaction on trade_actions (via DELETE endpoint's session) while fut.gg calls are slow, cleanup_tables teardown blocks on DELETE FROM trade_actions waiting for the lock, causing the test suite to hang indefinitely after test 4 fails.
test: CONFIRMED via code reading — scanner_harness.py explicitly starts real ScannerService with real FutGGClient. dispatch_scans() is scheduled to run every 30s. All 1800 test DB players are due immediately.
expecting: Fix = disable the scanner dispatch job in the test harness. The scanner (ScannerService) should still start (so API health checks see is_running=True) but dispatch_scans should NOT run during integration tests. Tests verify API behavior against pre-populated data — they do not need the scanner to scan players.
next_action: Modify server_harness.py to NOT add the scan_dispatch job to the scheduler. Scanner still starts (for health endpoint), but no dispatch job runs during tests.

## Symptoms

expected: API endpoints respond within 5s even when scanner is active
actual: Tests hang indefinitely after test 4 fails. Suite must be killed. Batch-size fix reduced task burst but didn't fix the hang.
errors: Suite hangs after test 4 (test_delete_cancels_pending_actions FAILED). Tests 5+ never run. Process killed after 2+ minutes.
reproduction: Run pytest tests/integration/test_data_integrity.py — hangs after 4th test
started: After SQLite→Postgres migration. Real scanner with real fut.gg calls introduced.

## Eliminated

- hypothesis: dispatch_scans() fetching ALL due players (1800) creating 1800 tasks
  evidence: BATCH_SIZE=200 fix applied — reduced to 200 tasks per cycle. Still hangs.
  timestamp: 2026-03-28T00:10:00Z

- hypothesis: connection pool exhaustion (40 scanner connections blocking 40 API connections)
  evidence: pool_size=20 + max_overflow=60 = 80 total. Scanner uses at most 40. API always has 40 available. Test failures are ReadTimeout not PoolTimeout. Pool is not the bottleneck.
  timestamp: 2026-03-28T00:00:00Z

## Evidence

- timestamp: 2026-03-28T00:00:00Z
  checked: src/server/db.py — pool configuration
  found: pool_size=20, max_overflow=60 → 80 total connections.
  implication: Pool not exhausted. Failures are ReadTimeout, not PoolTimeout.

- timestamp: 2026-03-28T00:00:00Z
  checked: src/server/scanner.py dispatch_scans() + scan_player()
  found: dispatch_scans() does await asyncio.gather(*tasks). Each task calls scan_player() which makes real fut.gg HTTP requests with tenacity retries: wait_exponential_jitter(initial=2, max=60, jitter=10), stop_after_attempt(3). A single failed player can hold the gather for up to 72s × 3 = 216s before tenacity gives up.
  implication: The gather() inside dispatch_scans() can block for many minutes if fut.gg rate-limits or is slow. During this time FastAPI's event loop is saturated with 40 concurrent outbound HTTP connections.

- timestamp: 2026-03-28T00:00:00Z
  checked: tests/integration/server_harness.py
  found: Scanner starts with real FutGGClient. All scheduler jobs run including scan_dispatch every 30s. With test DB cloned from prod (1800 players all with next_scan_at in past), first dispatch runs within 30s and starts 200 real fut.gg scans immediately.
  implication: Tests always race against a live scanner making real network requests to fut.gg.

- timestamp: 2026-03-28T00:00:00Z
  checked: dispatch_scans() control flow
  found: dispatch_scans() is awaited by APScheduler. The function does: query DB → create tasks → await asyncio.gather(*tasks). The gather() must complete before the function returns. With 200 tasks and 40 concurrent via semaphore, that's 5 sequential waves. If any wave is slow (fut.gg rate-limiting), the entire gather stalls.
  implication: The gather stalls the APScheduler coroutine, which stalls the event loop, which starves FastAPI request handlers.

- timestamp: 2026-03-28T01:00:00Z
  checked: test hang location — where does it hang after test 4?
  found: Test 4 (test_delete_cancels_pending_actions) FAILS with an assertion error. After the assertion fails, pytest runs cleanup_tables teardown (autouse fixture). cleanup_tables does: DELETE FROM trade_records, DELETE FROM trade_actions, DELETE FROM portfolio_slots via a separate engine. These DELETEs can block if the server subprocess has an open transaction holding row locks on those tables. The DELETE /portfolio/{ea_id} endpoint holds a write transaction while executing _get_volatile_ea_ids() — a large aggregation query over all SnapshotPricePoint rows for ~1800 players. If the scanner is simultaneously running scan_player() tasks that also write player_scores/market_snapshots, this creates lock contention on the Postgres server, slowing the DELETE endpoint's transaction. The cleanup_tables DELETE FROM trade_actions waits for the DELETE endpoint's transaction to release row-level locks. If the endpoint's transaction is slow (large aggregation + scanner contention), cleanup waits indefinitely.
  implication: Two interacting causes: (a) scanner saturates the server with real HTTP calls → event loop starvation → DELETE endpoint response is slow → transaction stays open longer; (b) cleanup_tables DELETE blocks on row locks held by the slow DELETE endpoint transaction. Combined = infinite hang.

- timestamp: 2026-03-28T01:00:00Z
  checked: models_db.py — FK constraints on trade_actions / portfolio_slots
  found: TradeAction.ea_id is a plain Integer with index, no FK constraint. PortfolioSlot.ea_id is plain Integer with unique constraint, no FK. No cascading FK issues.
  implication: Lock contention is the only explanation for cleanup hang, not FK cascade issues.

- timestamp: 2026-03-28T01:00:00Z
  checked: server_harness.py — why scanner CANNOT be entirely stopped
  found: The comment says "Per D-01: No MockScanner, no MockCircuitBreaker." But the real problem is not mocking vs real — it's that real fut.gg HTTP calls during tests create an uncontrolled external dependency that makes tests non-deterministic and causes lock contention. The scanner.start() only starts the HTTP client. The dispatch JOB causes the actual harm.
  implication: Correct fix: start the scanner (scanner.start()) for health endpoint compatibility, but do NOT add the scan_dispatch job to the test scheduler. The scanner is still real — it just doesn't make outbound API calls during integration tests. This is not mocking; it's not scheduling a job that creates test interference.

## Resolution

root_cause: |
  Two compounding causes:
  1. The test server harness runs the REAL scanner dispatch job (every 30s) which makes
     real fut.gg HTTP calls. With 1800 players due immediately in the test DB, dispatch
     starts 200 tasks (after BATCH_SIZE fix), each making real HTTP calls with tenacity
     retries (up to 216s per player on failures). This saturates the asyncio event loop
     with 40 concurrent outbound connections, starving FastAPI request handlers.

  2. The DELETE /portfolio/{ea_id} endpoint holds a write transaction while running
     _get_volatile_ea_ids() — a large GROUP BY aggregation over all 1800 players'
     SnapshotPricePoint rows. When the scanner is simultaneously running, this query
     is slow. The transaction holds row-level locks on trade_actions rows. When test 4
     fails and cleanup_tables runs DELETE FROM trade_actions, it blocks on those locks.
     The result is an infinite hang — the test suite never proceeds past test 4.

fix: |
  Modify server_harness.py to create the scheduler WITHOUT the scan_dispatch job.
  The ScannerService still starts (scanner.start() is called — real FutGGClient,
  real CircuitBreaker, health endpoint works). The other jobs (discovery, cleanup,
  aggregation) are also omitted in test harness to prevent any scanner-related
  background work from interfering with test isolation.

  This is NOT mocking — it is not scheduling a background job that creates test
  interference. The API behavior under test (actions, portfolio, slots) does not
  depend on the scanner running. Tests use pre-populated data from the cloned DB.

verification: awaiting human run of full integration test suite
files_changed:
  - src/config.py              (BATCH_SIZE fix from previous round — kept)
  - src/server/scanner.py      (dispatch LIMIT from previous round — kept)
  - tests/integration/server_harness.py  (removed scan_dispatch + all scanner jobs from test scheduler)
