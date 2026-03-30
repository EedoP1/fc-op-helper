---
phase: 10-split-scanner-and-api-into-separate-processes
plan: "02"
subsystem: backend
tags: [scanner, api, process-split, docker, decoupling]
dependency_graph:
  requires: [ScannerStatus-ORM, health-endpoint-db-read]
  provides: [scanner-process-entry-point, api-only-lifespan]
  affects: [src/server/scanner_main.py, src/server/main.py]
tech_stack:
  added: []
  patterns: [standalone-process-entry-point, asyncio-event-block-forever, docker-sigterm-graceful-shutdown]
key_files:
  created:
    - src/server/scanner_main.py
  modified:
    - src/server/main.py
decisions:
  - "scanner_main.py blocks via asyncio.Event().wait() — Docker SIGTERM triggers finally block for graceful shutdown"
  - "API lifespan retains inline migrations and v1 score purge — these are idempotent DB operations safe to run in API process"
  - "All 6 API routers unchanged; only scanner/scheduler/circuit_breaker removed from lifespan"
metrics:
  duration: ~2 min
  completed_date: "2026-03-30"
  tasks_completed: 2
  files_modified: 2
---

# Phase 10 Plan 02: Create scanner_main.py and strip scanner from API lifespan Summary

**One-liner:** Scanner entry point created as standalone process (D-05) and API lifespan stripped of scanner/scheduler/circuit_breaker (D-06), completing the process split.

## What Was Built

Created `src/server/scanner_main.py` as a standalone scanner process entry point that creates DB engine, ScannerService, CircuitBreaker, APScheduler, and blocks indefinitely. Rewrote `src/server/main.py` lifespan to be API-only — no scanner, no scheduler, no FutGGClient. API now starts in isolation with only the DB connection pool and inline migrations.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create scanner_main.py entry point (D-05) | 554c61c | src/server/scanner_main.py |
| 2 | Strip scanner/scheduler from API main.py lifespan (D-06) | 1883da8 | src/server/main.py |

## Decisions Made

- **asyncio.Event().wait() for blocking:** Scanner process blocks indefinitely using `await asyncio.Event().wait()`. This is the correct pattern for a long-running async process without a built-in event loop (like uvicorn). Docker SIGTERM propagates to the `finally` block for graceful shutdown.
- **API keeps inline migrations:** The is_leftover migration and v1 score purge are idempotent DB operations that remain in the API lifespan. They are safe to run on every API startup and don't require scanner presence.
- **Zero API route changes needed:** Pre-task grep confirmed no `app.state.scanner` or `app.state.circuit_breaker` references existed in any API route file (Plan 01 already cleaned health.py). Task 2 was purely lifespan surgery.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None.

## Self-Check: PASSED

- src/server/scanner_main.py exists — FOUND
- src/server/scanner_main.py contains `async def main():` — FOUND
- src/server/scanner_main.py contains `asyncio.run(main())` — FOUND
- src/server/scanner_main.py does NOT contain FastAPI or uvicorn — CONFIRMED
- src/server/main.py does NOT contain ScannerService — CONFIRMED (count=0)
- src/server/main.py does NOT contain create_scheduler — CONFIRMED (count=0)
- src/server/main.py does NOT contain CircuitBreaker — CONFIRMED (count=0)
- src/server/main.py does NOT contain app.state.scanner — CONFIRMED
- src/server/main.py does NOT contain app.state.circuit_breaker — CONFIRMED
- src/server/main.py DOES contain session_factory and create_engine_and_tables — FOUND
- `from src.server.main import app` imports successfully, `app.title == "OP Seller"` — CONFIRMED
- `grep -rn app.state.scanner|app.state.circuit_breaker src/server/api/` returns 0 matches — CONFIRMED
- Commits 554c61c and 1883da8 exist — CONFIRMED
