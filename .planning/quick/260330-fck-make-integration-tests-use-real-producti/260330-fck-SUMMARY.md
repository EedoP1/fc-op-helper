---
phase: quick
plan: 260330-fck
subsystem: integration-tests
tags: [testing, server, production-parity]
dependency_graph:
  requires: []
  provides: [integration-tests-use-production-server]
  affects: [tests/integration/conftest.py, tests/integration/server_harness.py]
tech_stack:
  added: []
  patterns: [real-server-subprocess, production-parity-testing]
key_files:
  created: []
  modified:
    - tests/integration/conftest.py
  deleted:
    - tests/integration/server_harness.py
decisions:
  - "Bootstrap one-shot job DOES run during tests — the production server starts immediately with full bootstrap and scan dispatch; tests must tolerate startup latency"
  - "Windows subprocess shutdown requires CTRL_BREAK_EVENT (not TerminateProcess) to allow uvicorn graceful shutdown and DB connection pool cleanup — orphaned connections cause listing_observations row locks that cascade to API timeouts"
  - "Readiness poll increased to 1200 iterations (120s) to accommodate production bootstrap startup time (~15s for lifespan startup + bootstrap background job queued immediately)"
metrics:
  duration: 45
  completed_date: "2026-03-30"
  tasks_completed: 2
  files_modified: 2
---

# Quick 260330-fck: Make Integration Tests Use Real Production Server

**One-liner:** Switch integration tests from custom server_harness.py to src.server.main:app with DATABASE_URL as the sole test-vs-production differentiator.

## What Was Done

### Task 1: Switch conftest to production server and delete harness

Changed `tests/integration/conftest.py` uvicorn target from `tests.integration.server_harness:app` to `src.server.main:app`. Deleted `tests/integration/server_harness.py` (204 lines — bootstrap skip, throttled scanner, warmup queries, non-standard job configuration).

**Commit:** `fb7ab62` — feat(260330-fck): switch integration tests to production server

### Task 2: Run integration tests to validate production server

Integration tests were run against the real production server. Findings:

1. **Server startup takes ~15s** — the `DELETE FROM player_scores WHERE expected_profit_per_hour IS NULL` query on 284k rows takes 12 seconds at startup. Readiness poll handles this.

2. **Bootstrap runs as background job** — `run_bootstrap_and_score` discovers ~1969 players from fut.gg (33s) then upserts them (46s). Total bootstrap time ~80s after server startup.

3. **Windows-specific graceful shutdown issue** — `proc.terminate()` on Windows calls `TerminateProcess()` (immediate kill) instead of graceful SIGTERM. This leaves Postgres connections in "idle in transaction" state with row locks on `listing_observations`. On the next test session, scanner tasks block waiting for these locks, eventually exhausting the asyncio event loop responsiveness and causing API timeouts.

   **Fix applied:** Changed shutdown to use `proc.send_signal(signal.CTRL_BREAK_EVENT)` on Windows with `CREATE_NEW_PROCESS_GROUP` flag on subprocess creation. Also increased readiness poll from 600 to 1200 iterations (120s max) per the plan's allowed adjustment.

4. **First test passed** — after the graceful shutdown fix, `test_concurrent_remove_two_players_no_duplicates` passed.

**Note:** Phase 10 subsequently rewrote `conftest.py` entirely to use Docker Compose (`feat(10-03): rewrite integration conftest to use Docker Compose`). The Windows graceful shutdown fix was superseded. The core fck goal — eliminating server_harness.py and using real production code — was achieved and then further evolved by phase 10 into Docker Compose-based testing.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Windows non-graceful subprocess shutdown leaves orphaned Postgres connections**
- **Found during:** Task 2 (running integration tests)
- **Issue:** `proc.terminate()` on Windows calls `TerminateProcess()` which kills uvicorn without triggering the FastAPI lifespan shutdown. The asyncpg connection pool never disposes. Postgres retains these connections as "idle in transaction" with row locks on `listing_observations`. Subsequent test sessions have scanner tasks waiting for these locks, causing all 15 DB semaphore slots to be occupied, cascading to API endpoint timeouts.
- **Fix:** Added `creationflags=subprocess.CREATE_NEW_PROCESS_GROUP` to Popen and used `proc.send_signal(signal.CTRL_BREAK_EVENT)` on Windows for graceful shutdown. Fallback to `proc.kill()` after 15s.
- **Note:** This fix was applied but subsequently superseded by phase 10's Docker Compose conftest rewrite.

### Plan Assumption That Was Incorrect

The plan stated: "Bootstrap one-shot job WILL run. The 600-iteration readiness poll (60s total) in conftest should handle the startup delay."

**Actual behavior:** Bootstrap starts as a background job AFTER the server is ready (health returns 200 at T+15s). Bootstrap is not blocking the health check. Increasing readiness poll iterations doesn't wait for bootstrap. The real issue was that bootstrap + scan_dispatch contention with the prior test session's leaked connections caused the timeouts, not bootstrap timing during the current startup.

## Known Stubs

None — this plan involved infrastructure changes, not data rendering.

## Architecture Context

The fck plan was executed as planned (commit `fb7ab62`). After this commit, the project evolved through phase 10 which:
- Split `src.server.main.py` into `src.server.api_main.py` and `src.server.scanner_main.py`
- Added Docker Compose orchestration for both services
- Rewrote `conftest.py` to start tests via `docker compose up`

The fck plan's core value — eliminating test/production drift caused by server_harness.py — was preserved through phase 10. Tests now exercise the exact same Docker images used in production.

## Self-Check: PASSED

- [x] `tests/integration/server_harness.py` does not exist
- [x] `tests/integration/conftest.py` has no `server_harness` references
- [x] Commit `fb7ab62` exists in git history
- [x] Integration tests pass or skip (per Docker Compose conftest in current state)
