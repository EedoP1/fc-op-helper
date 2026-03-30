---
phase: 10-split-scanner-and-api-into-separate-processes
verified: 2026-03-30T00:00:00Z
status: passed
score: 11/11 must-haves verified
re_verification: false
gaps: []
human_verification:
  - test: "Run docker compose up and confirm api and scanner start as separate OS processes with no resource contention"
    expected: "Both services start, API responds on port 8000, scanner writes scanner_status row within 60s, /api/v1/health returns scanner_status != 'unknown'"
    why_human: "Cannot start Docker services in this environment; production parity requires live container execution"
  - test: "Run integration tests: docker compose -f docker-compose.yml -f docker-compose.test.yml -p op_seller_test up -d --build api scanner && pytest tests/integration/"
    expected: "All integration tests pass; no API timeouts during scanner operation"
    why_human: "Requires Docker daemon and postgres-test service to be running; cannot execute in static analysis"
---

# Phase 10: Split Scanner and API into Separate Processes — Verification Report

**Phase Goal:** Separate the scanner (background market scanning) and API server into independent processes to eliminate resource contention that causes production API timeouts. Scanner writes metrics to DB for health endpoint. Both processes share the same Postgres database.
**Verified:** 2026-03-30
**Status:** passed
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|---------|
| 1 | Scanner writes is_running, success_rate_1h, last_scan_at, queue_depth, circuit_breaker_state to scanner_status table on every dispatch cycle | VERIFIED | `pg_insert(ScannerStatus)` upsert at scanner.py:527 inside `dispatch_scans`, all 5 fields populated, error-caught and non-fatal |
| 2 | Health endpoint returns scanner metrics by querying scanner_status table, not app.state.scanner | VERIFIED | health.py uses `select(ScannerStatus).where(ScannerStatus.id == 1)` via `request.app.state.session_factory`; no `app.state.scanner` anywhere in src/server |
| 3 | Health endpoint returns degraded 'unknown' state when scanner_status table has no rows (startup race) | VERIFIED | health.py:39-48 handles `status is None` with `"scanner_status": "unknown"` and `"circuit_breaker": "unknown"` |
| 4 | Scanner process runs independently via python -m src.server.scanner_main | VERIFIED | scanner_main.py exists with `async def main()`, `asyncio.run(main())` under `if __name__ == "__main__":`, no FastAPI or uvicorn |
| 5 | API process starts without scanner, scheduler, FutGGClient, or CircuitBreaker in memory | VERIFIED | main.py lifespan contains no ScannerService, create_scheduler, or CircuitBreaker imports/instantiation |
| 6 | No API route file references app.state.scanner or app.state.circuit_breaker | VERIFIED | grep across src/server returns 0 matches |
| 7 | docker compose up starts postgres, api, and scanner as three services | VERIFIED | docker-compose.yml defines all three; api uses `uvicorn src.server.main:app`, scanner uses `python -m src.server.scanner_main` |
| 8 | API service binds to host port 8000, scanner has no port binding | VERIFIED | api has `ports: ["8000:8000"]`; scanner service has no ports key |
| 9 | Both api and scanner auto-restart on failure via Docker Compose restart policy | VERIFIED | Both services have `restart: unless-stopped` |
| 10 | Integration tests use Docker Compose with docker-compose.test.yml override | VERIFIED | conftest.py uses `docker compose -f docker-compose.yml -f docker-compose.test.yml -p op_seller_test`; no subprocess.Popen |
| 11 | conftest.py waits for scanner to write first scanner_status row (not just API HTTP 200) | VERIFIED | Two-phase wait: Phase 1 polls /health for HTTP 200, Phase 2 polls until `scanner_status != "unknown"` with 90s timeout |

**Score:** 11/11 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/server/models_db.py` | ScannerStatus ORM model with all D-02 columns | VERIFIED | `class ScannerStatus(Base)` at line 166, __tablename__ = "scanner_status", all 6 columns: id, is_running, last_scan_at, success_rate_1h, queue_depth, circuit_breaker_state, updated_at |
| `src/server/scanner.py` | ScannerStatus upsert in dispatch_scans | VERIFIED | `pg_insert(ScannerStatus)` at line 527, wrapped in try/except, all metrics written, on_conflict_do_update with id=1 |
| `src/server/api/health.py` | DB-based health endpoint with startup-race handling | VERIFIED | `select(ScannerStatus).where(ScannerStatus.id == 1)`, `scalar_one_or_none()`, "unknown" degraded state on None |
| `src/server/db.py` | ScannerStatus imported in create_engine_and_tables | VERIFIED | Line 60 includes `ScannerStatus` in the import list |
| `src/server/scanner_main.py` | Standalone scanner entry point (>= 30 lines) | VERIFIED | 66 lines; creates engine, CircuitBreaker, ScannerService, scheduler; blocks via asyncio.Event().wait(); graceful shutdown in finally block |
| `src/server/main.py` | API-only lifespan with all 6 routers | VERIFIED | No scanner/scheduler/circuit_breaker; includes players, health, portfolio, actions, profit, status routers; inline migrations preserved |
| `Dockerfile` | python:3.12-slim image for both services | VERIFIED | FROM python:3.12-slim, WORKDIR /app, COPY requirements.txt, RUN pip install, COPY . . |
| `docker-compose.yml` | Multi-service with api + scanner + postgres | VERIFIED | api (port 8000, restart: unless-stopped), scanner (no port, restart: unless-stopped), existing postgres services unchanged |
| `docker-compose.test.yml` | Test override using postgres-test Docker DNS | VERIFIED | DATABASE_URL uses @postgres-test:5432 (not localhost), port 8001:8000, restart: "no" for both services |
| `tests/integration/conftest.py` | Docker Compose test harness with scanner wait | VERIFIED | docker compose command via subprocess.run, two-phase wait, scanner_status cleanup in cleanup_tables, all original fixtures preserved |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| scanner.py | scanner_status table | `pg_insert(ScannerStatus)` in dispatch_scans | WIRED | Line 527; upsert executes and commits every dispatch cycle |
| health.py | scanner_status table | `select(ScannerStatus)` query | WIRED | Line 27; reads via session_factory, not in-memory state |
| scanner_main.py | scanner.py | `ScannerService(session_factory=session_factory, ...)` | WIRED | Line 40; constructor called with both required args |
| scanner_main.py | scheduler.py | `create_scheduler(scanner)` | WIRED | Line 43; scheduler created and started with scanner instance |
| docker-compose.yml | Dockerfile | `build: .` | WIRED | Both api and scanner services use `build: .` pointing to project root |
| docker-compose.yml | src/server/main.py | api command `src.server.main:app` | WIRED | Line 38: `uvicorn src.server.main:app --host 0.0.0.0 --port 8000` |
| docker-compose.yml | src/server/scanner_main.py | scanner command | WIRED | Line 50: `python -m src.server.scanner_main` |
| docker-compose.test.yml | postgres-test service | DATABASE_URL with Docker service DNS | WIRED | `@postgres-test:5432` in both api and scanner environment overrides; no localhost |
| tests/integration/conftest.py | docker-compose.test.yml | `docker compose -f` override flag | WIRED | COMPOSE_TEST_OVERRIDE resolved to absolute path; passed as second `-f` arg |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| health.py | `status` (ScannerStatus row) | `select(ScannerStatus).where(id == 1)` DB query | Yes — reads live DB row written by scanner | FLOWING |
| health.py | `players_in_db` | `select(func.count()).select_from(PlayerRecord).where(is_active == True)` | Yes — live DB count | FLOWING |
| scanner.py dispatch_scans | scanner_status upsert | all fields sourced from `self.is_running`, `self.last_scan_at`, `self.success_rate_1h()`, `self._queue_depth_cache`, `self._circuit_breaker.state.value` | Yes — live runtime values | FLOWING |

### Behavioral Spot-Checks

Step 7b: SKIPPED for Docker/container runtime checks (requires live Docker daemon). Static analysis completed in lieu.

The following static checks were verified programmatically:

| Behavior | Check | Result |
|----------|-------|--------|
| ScannerStatus model importable | `class ScannerStatus(Base)` present with correct `__tablename__` | PASS |
| No in-memory scanner references in health.py | No `app.state.scanner` or `app.state.circuit_breaker` in src/server | PASS |
| scanner_main.py has no web server code | No `FastAPI` or `uvicorn` in scanner_main.py | PASS |
| conftest.py uses Docker Compose, not subprocess.Popen | No `subprocess.Popen` in conftest.py | PASS |
| docker-compose.test.yml uses Docker DNS not localhost | `@postgres-test:5432` present, no `localhost` | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|---------|
| SPLIT-01 | 10-02-PLAN.md | Scanner runs as independent OS process via `python -m src.server.scanner_main` with its own DB engine, ScannerService, CircuitBreaker, APScheduler | SATISFIED | scanner_main.py creates all dependencies, blocks via asyncio.Event().wait(), runnable as `python -m src.server.scanner_main` |
| SPLIT-02 | 10-02-PLAN.md | API process starts without scanner/scheduler/FutGGClient/CircuitBreaker — only DB pool and FastAPI routers | SATISFIED | main.py lifespan has no ScannerService, create_scheduler, CircuitBreaker imports; only create_engine_and_tables + routers |
| SPLIT-03 | 10-01-PLAN.md | Scanner writes is_running, success_rate_1h, last_scan_at, queue_depth, circuit_breaker_state to scanner_status DB table every dispatch cycle | SATISFIED | pg_insert upsert in dispatch_scans with all 5 metrics, on_conflict_do_update on id=1 |
| SPLIT-04 | 10-01-PLAN.md | Health endpoint reads scanner metrics from scanner_status table instead of app.state.scanner; returns degraded "unknown" when scanner hasn't written yet | SATISFIED | health.py reads via select(ScannerStatus), scalar_one_or_none(), "unknown" degraded state on None result |
| SPLIT-05 | 10-03-PLAN.md | Both processes managed via Docker Compose — `docker compose up` starts postgres, api, and scanner with auto-restart | SATISFIED | docker-compose.yml defines all three services; api and scanner both have `restart: unless-stopped` and `depends_on: condition: service_healthy` |
| SPLIT-06 | 10-03-PLAN.md | Integration tests use Docker Compose with docker-compose.test.yml override matching production deployment exactly | SATISFIED | conftest.py uses `docker compose -f docker-compose.yml -f docker-compose.test.yml`, two-phase scanner readiness wait, scanner_status cleanup |

All 6 SPLIT requirements accounted for. No orphaned requirements.

### Anti-Patterns Found

No blockers or stubs found.

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| scanner_main.py | 29 | `logging.FileHandler("scanner.log")` — relative path bakes a file into the container working directory | Info | Log file written to /app/scanner.log inside container; ephemeral unless volume-mounted. No functional impact. |

The FileHandler note is informational only — it does not block the process split goal and is standard for a local-machine deployment.

### Human Verification Required

#### 1. Full docker compose up smoke test

**Test:** From repo root, run `docker compose up -d` then `curl http://localhost:8000/api/v1/health`
**Expected:** HTTP 200 with JSON; after ~60s, `scanner_status` transitions from `"unknown"` to `"running"`; no API timeouts occur while scanner is actively scanning
**Why human:** Requires live Docker daemon, Postgres data volume at D:/op-seller/postgres_data, and network access; cannot execute in static analysis

#### 2. Integration test suite execution

**Test:** Ensure postgres-test is running with cloned data, then run `pytest tests/integration/ -v`
**Expected:** All integration tests pass; live_server fixture starts both api and scanner containers via Docker Compose; cleanup_tables removes scanner_status rows between tests
**Why human:** Requires Docker daemon, pre-populated test DB (scripts/setup_test_db.py), and approximately 2-5 minutes for the two-phase scanner readiness wait

### Gaps Summary

No gaps. All automated checks passed. Phase goal is fully achieved in code.

The process split is complete: scanner and API run as independent processes, health endpoint is fully decoupled from in-process scanner state, Docker Compose orchestrates both services with proper networking and auto-restart, and integration tests use Docker Compose for production parity.

---

_Verified: 2026-03-30_
_Verifier: Claude (gsd-verifier)_
