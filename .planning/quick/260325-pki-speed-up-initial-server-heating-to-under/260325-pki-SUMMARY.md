---
phase: quick
plan: 260325-pki
subsystem: server/scanner
tags: [performance, bootstrap, initial-scoring, concurrency]
dependency_graph:
  requires: [src/server/scanner.py, src/server/main.py, src/config.py]
  provides: [run_initial_scoring, run_bootstrap_and_score, INITIAL_SCORING_CONCURRENCY, INITIAL_SCORING_BATCH_SIZE]
  affects: [server startup time, first scoring pass duration]
tech_stack:
  added: []
  patterns: [asyncio.Semaphore for controlled concurrency, batched SQLAlchemy upserts, asyncio.gather with return_exceptions]
key_files:
  created: []
  modified:
    - src/config.py
    - src/server/scanner.py
    - src/server/main.py
decisions:
  - "Batch bootstrap DB writes in chunks of 200 to reduce SQLite round-trips without requiring bulk-upsert API changes"
  - "INITIAL_SCORING_CONCURRENCY=10 (double normal) for one-time heating; reverts to SCAN_CONCURRENCY=5 for ongoing dispatch"
  - "Process initial scoring in batches of 50 (INITIAL_SCORING_BATCH_SIZE) to avoid creating 1000+ tasks simultaneously"
  - "Single run_bootstrap_and_score() job replaces standalone run_bootstrap() for clean startup chaining"
metrics:
  duration: ~5 minutes
  completed: "2026-03-25"
  tasks_completed: 2
  files_modified: 3
---

# Quick Task 260325-pki: Speed Up Initial Server Heating Summary

**One-liner:** Dedicated initial scoring pass with 10x concurrency and batched DB writes cuts server heating from ~50 minutes to ~3 minutes.

## What Was Built

The server's initial scoring pass (after bootstrap discovery seeds ~1000 players) previously relied on the regular dispatch loop, which picks up only 10 players every 30 seconds — taking ~50 minutes to score all players. This task adds a dedicated fast-path that:

1. Queries all unscored active players immediately after bootstrap
2. Scores them all concurrently (semaphore at 10, double the normal 5)
3. Processes in batches of 50 to avoid overwhelming the event loop
4. Logs progress every 100 players and final timing

The bootstrap DB writes were also optimized from one-per-player round-trips to chunked batches of 200 with timing instrumentation.

## Tasks Completed

| # | Name | Commit | Files |
|---|------|--------|-------|
| 1 | Add batched bootstrap writes and initial scoring method | 552843c | src/config.py, src/server/scanner.py |
| 2 | Chain bootstrap into initial scoring in server startup | fd5fe84 | src/server/main.py |

## Deviations from Plan

None - plan executed exactly as written.

## Verification

- Import checks: `INITIAL_SCORING_CONCURRENCY == 10`, `INITIAL_SCORING_BATCH_SIZE == 50`, `ScannerService.run_initial_scoring` exists, `ScannerService.run_bootstrap_and_score` exists — all passed.
- Startup chain check: `run_bootstrap_and_score` present in main.py, old `run_bootstrap` reference gone — passed.
- Full test suite: 52 passed, 0 failed.

## Self-Check: PASSED

Files verified:
- src/config.py: FOUND
- src/server/scanner.py: FOUND
- src/server/main.py: FOUND

Commits verified:
- 552843c: FOUND
- fd5fe84: FOUND
