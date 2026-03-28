---
phase: 09-comprehensive-api-integration-performance-test-suite
plan: "01"
subsystem: test-infrastructure
tags: [integration-tests, uvicorn, sqlite, httpx, smoke-tests]
dependency_graph:
  requires: []
  provides: [real-server-integration-test-harness, smoke-tests-all-endpoints]
  affects: [tests/integration/, tests/test_health_check.py]
tech_stack:
  added: [pytest-subprocess-uvicorn, httpx-sync-poll, aiosqlite-cleanup-engine]
  patterns: [synchronous-session-scoped-fixture, per-test-cleanup-via-async-fixture, subprocess-popen-server]
key_files:
  created:
    - tests/integration/__init__.py
    - tests/integration/server_harness.py
    - tests/integration/conftest.py
    - tests/integration/test_smoke_all_endpoints.py
  modified:
    - tests/test_health_check.py
decisions:
  - "Synchronous live_server fixture (not async def) avoids pytest-asyncio 1.3.0 session-scoped event loop bug"
  - "Catch httpx.ConnectTimeout in readiness poll in addition to ConnectError and ReadTimeout — Windows raises ConnectTimeout on fast failed connections"
  - "TEST_DB_PATH read lazily inside lifespan (not at module top level) so import does not fail when env var is absent"
  - "cleanup_tables uses connect_args={'timeout':10} on its own engine to avoid SQLite file locking on Windows"
metrics:
  duration_seconds: 222
  completed_date: "2026-03-28"
  tasks_completed: 2
  files_created_or_modified: 5
---

# Phase 09 Plan 01: Real-Server Integration Test Harness Summary

Real uvicorn integration test harness with per-test SQLite cleanup and smoke tests for all 16 API endpoints — server starts on a free port with a real SQLite file, httpx.AsyncClient makes real HTTP calls.

## What Was Built

### Task 1: Server harness and conftest with real uvicorn + SQLite

Created `tests/integration/` package with three files:

- `tests/integration/__init__.py` — empty package marker
- `tests/integration/server_harness.py` — standalone FastAPI app uvicorn can import. Contains `MockScanner` and `MockCircuitBreaker` stubs for the health endpoint, reads `TEST_DB_PATH` from environment at lifespan startup (lazy — not at import time), mounts all 6 routers.
- `tests/integration/conftest.py` — session-scoped fixtures: `live_server` (synchronous `def` fixture using `subprocess.Popen` + sync readiness poll), `client` (function-scoped async httpx.AsyncClient), `cleanup_tables` (autouse async fixture that deletes all mutable table rows after each test with Windows-safe SQLite timeout).

### Task 2: Fix broken test_health_check.py and smoke tests for all 16 endpoints

- `tests/test_health_check.py` — replaced 400-line file that imported dead `FutbinClient` with a placeholder docstring. No more collection error.
- `tests/integration/test_smoke_all_endpoints.py` — 17 smoke tests covering all 16 API endpoints via real HTTP.

## Endpoints Covered

| Router | Endpoint | Test |
|--------|----------|------|
| health | GET /api/v1/health | `test_health_returns_200` |
| players | GET /api/v1/players/top | `test_top_players_empty_db` |
| players | GET /api/v1/players/{ea_id} | `test_player_detail_not_found` |
| portfolio | GET /api/v1/portfolio | `test_portfolio_requires_budget`, `test_portfolio_empty_db` |
| portfolio | POST /api/v1/portfolio/generate | `test_generate_portfolio_empty_db` |
| portfolio | POST /api/v1/portfolio/confirm | `test_confirm_portfolio` |
| portfolio | POST /api/v1/portfolio/swap-preview | `test_swap_preview` |
| portfolio | GET /api/v1/portfolio/confirmed | `test_confirmed_portfolio` |
| portfolio | DELETE /api/v1/portfolio/{ea_id} | `test_delete_portfolio_player_not_found` |
| portfolio | POST /api/v1/portfolio/slots | `test_seed_portfolio_slots` |
| actions | GET /api/v1/actions/pending | `test_pending_action_empty` |
| actions | POST /api/v1/actions/{id}/complete | `test_complete_action_not_found` |
| actions | POST /api/v1/trade-records/direct | `test_direct_trade_record_no_slot` |
| actions | POST /api/v1/trade-records/batch | `test_batch_trade_records_empty` |
| profit | GET /api/v1/profit/summary | `test_profit_summary_empty` |
| portfolio_status | GET /api/v1/portfolio/status | `test_portfolio_status_empty` |

## Test Results

- `python -m pytest tests/ --collect-only -q` — 178 tests collected, no errors
- `python -m pytest tests/integration/ -v` — 17/17 passed in ~14 seconds
- `python -m pytest tests/test_health_check.py --collect-only` — 0 tests collected (no error)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] TEST_DB_PATH read lazily in server_harness.py**
- **Found during:** Task 1 verification
- **Issue:** `DB_PATH = os.environ["TEST_DB_PATH"]` at module top level caused `KeyError` when importing the module without the env var set (e.g., `python -c "import tests.integration.server_harness"`)
- **Fix:** Moved env var read inside the `lifespan()` async context manager so it only reads at uvicorn startup, not at import time
- **Files modified:** tests/integration/server_harness.py
- **Commit:** 4190021

**2. [Rule 1 - Bug] httpx.ConnectTimeout not caught in readiness poll**
- **Found during:** Task 1 first test run
- **Issue:** conftest.py readiness poll only caught `httpx.ConnectError, httpx.ReadTimeout` — Windows raises `httpx.ConnectTimeout` on fast connection timeouts, causing the poll to fail immediately rather than retry
- **Fix:** Added `httpx.ConnectTimeout, httpx.TimeoutException` to the except clause; increased poll from 50 to 100 iterations (10s max); increased per-request timeout from 0.5s to 1.0s
- **Files modified:** tests/integration/conftest.py
- **Commit:** 4190021

## Known Stubs

None — all 17 tests make real HTTP calls to a real server with a real SQLite database. No mocked responses.

## Self-Check: PASSED
