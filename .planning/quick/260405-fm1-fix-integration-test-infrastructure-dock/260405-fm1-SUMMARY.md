---
phase: quick
plan: 260405-fm1
subsystem: infrastructure
tags: [docker, testing, integration-tests, reliability]
dependency_graph:
  requires: []
  provides: [lean-docker-build, db-retry, integration-test-runner, test-timeout]
  affects: [src/server/db.py, pytest.ini, requirements.txt]
tech_stack:
  added: [pytest-timeout>=2.3.0]
  patterns: [retry-loop-with-backoff, dockerignore-exclusions]
key_files:
  created:
    - .dockerignore
    - scripts/run_integration_tests.sh
  modified:
    - src/server/db.py
    - pytest.ini
    - requirements.txt
decisions:
  - 5-attempt retry with 2s backoff in create_engine_and_tables() catches CannotConnectNowError Docker race
  - .dockerignore excludes .venv, .git, .planning, tests, extension to shrink 340MB+ build context
  - pytest-timeout set to 120s globally — generous for integration DB tests, catches infinite hangs
  - run_integration_tests.sh persists postgres-test across runs, tears down api+scanner only on exit
metrics:
  duration: ~5 min
  completed: "2026-04-05T08:19:52Z"
  tasks_completed: 2
  files_changed: 5
---

# Quick 260405-fm1: Fix Integration Test Infrastructure Summary

**One-liner:** Added .dockerignore to shrink Docker build context, 5-attempt DB retry to survive Postgres startup race, pytest-timeout for hang prevention, and a one-command integration test runner script.

## What Was Built

Four infrastructure gaps fixed to make integration tests reliable and fast to run:

1. **.dockerignore** — Excludes `.venv/`, `.git/`, `.planning/`, `.claude/`, `.agents/`, `__pycache__/`, `tests/`, `extension/`, `node_modules/`, `.pytest_cache/`, `*.egg-info/`, `.env`, `.env.*`. Reduces Docker build context from 340MB+ to src/ + config files only.

2. **DB connection retry in `src/server/db.py`** — `create_engine_and_tables()` now retries the `engine.begin()` block up to 5 times with a 2-second sleep between attempts. Handles Docker's startup race where Postgres accepts TCP but rejects queries with `CannotConnectNowError`. Logs each retry at WARNING level, logs ERROR and re-raises on final failure.

3. **pytest-timeout** — Added `pytest-timeout>=2.3.0` to `requirements.txt` and `timeout = 120` to `pytest.ini`. Individual tests now terminate after 2 minutes instead of hanging indefinitely.

4. **`scripts/run_integration_tests.sh`** — Self-contained one-command script:
   - Ensures `postgres-test` is running (`docker compose up -d postgres-test`) and waits for healthy status
   - Builds and starts `api` + `scanner` in test configuration (`docker-compose.test.yml` overlay, project `op_seller_test`)
   - Polls `http://localhost:8001/health` for up to 30 seconds via `curl --retry`
   - Runs `python -m pytest tests/integration/ -x -v --timeout=120`
   - Tears down `api` + `scanner` on EXIT trap; leaves `postgres-test` running for reuse

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Add .dockerignore and DB connection retry | aee6009 | .dockerignore, src/server/db.py |
| 2 | Add pytest-timeout and create test runner script | 7f522bc | requirements.txt, pytest.ini, scripts/run_integration_tests.sh |

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None.

## Self-Check: PASSED

- `.dockerignore` exists and contains `.venv`, `.git`, `tests/`
- `src/server/db.py` has retry loop (`asyncio.sleep`, `max_retries = 5`)
- `scripts/run_integration_tests.sh` exists and passes `bash -n` syntax check
- `pytest.ini` contains `timeout = 120`
- Commits aee6009 and 7f522bc present in git log
