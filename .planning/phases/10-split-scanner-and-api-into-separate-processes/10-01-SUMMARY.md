---
phase: 10-split-scanner-and-api-into-separate-processes
plan: "01"
subsystem: backend
tags: [scanner, health, db, decoupling]
dependency_graph:
  requires: []
  provides: [ScannerStatus-ORM, scanner-db-metrics, health-endpoint-db-read]
  affects: [src/server/models_db.py, src/server/scanner.py, src/server/api/health.py, src/server/db.py]
tech_stack:
  added: []
  patterns: [postgresql-upsert, pg_insert-on_conflict_do_update, startup-race-degraded-state]
key_files:
  created: []
  modified:
    - src/server/models_db.py
    - src/server/db.py
    - src/server/scanner.py
    - src/server/api/health.py
decisions:
  - "Health endpoint reads scanner metrics from scanner_status DB table (not app.state.scanner) — prerequisite for process split"
  - "ScannerStatus.id=1 singleton row upserted every dispatch cycle with all D-02 metrics"
  - "Startup race handled by scalar_one_or_none: None returns degraded 'unknown' state, same response shape preserved"
metrics:
  duration: ~5 min
  completed_date: "2026-03-30"
  tasks_completed: 2
  files_modified: 4
---

# Phase 10 Plan 01: ScannerStatus DB model and DB-backed health endpoint Summary

**One-liner:** ScannerStatus ORM model added and health endpoint decoupled from in-process scanner state by reading from scanner_status DB table.

## What Was Built

Added `ScannerStatus` ORM model (`scanner_status` table) with all D-02 metrics (is_running, success_rate_1h, last_scan_at, queue_depth, circuit_breaker_state, updated_at). Scanner writes this row as a PostgreSQL upsert on every `dispatch_scans` call. Health endpoint rewritten to read from the DB table via `session_factory` instead of `app.state.scanner` and `app.state.circuit_breaker`.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add ScannerStatus ORM model and register in create_engine_and_tables | 3755c54 | src/server/models_db.py, src/server/db.py |
| 2 | Add ScannerStatus upsert to dispatch_scans and rewrite health endpoint | 04906fd | src/server/scanner.py, src/server/api/health.py |

## Decisions Made

- **Health endpoint decoupled from in-process state:** Health now reads from `scanner_status` table, removing all `app.state.scanner` and `app.state.circuit_breaker` references. This is the prerequisite for the process split in Plan 02.
- **Singleton row with id=1:** ScannerStatus uses a single row (id=1) upserted each dispatch cycle — simple and efficient for a single-scanner deployment.
- **Startup race with degraded state:** `scalar_one_or_none()` returns None when no row exists yet (scanner hasn't run); health returns `"scanner_status": "unknown"` and `"circuit_breaker": "unknown"` — same response shape, no breaking change.
- **upsert error is non-fatal:** `pg_insert` failure is caught and logged at ERROR level; scanner dispatch continues even if DB write fails.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None.

## Self-Check: PASSED

- src/server/models_db.py contains `class ScannerStatus(Base):` — FOUND
- src/server/db.py contains `ScannerStatus` in import — FOUND
- src/server/scanner.py contains `pg_insert(ScannerStatus)` — FOUND
- src/server/api/health.py does NOT contain `app.state.scanner` — CONFIRMED (count=0)
- src/server/api/health.py contains `select(ScannerStatus).where(ScannerStatus.id == 1)` — FOUND
- src/server/api/health.py contains `scalar_one_or_none()` — FOUND
- src/server/api/health.py contains `"scanner_status": "unknown"` — FOUND
- Commits 3755c54 and 04906fd exist — CONFIRMED
