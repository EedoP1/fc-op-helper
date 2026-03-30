# Phase 10: Split Scanner and API into Separate Processes - Context

**Gathered:** 2026-03-30
**Status:** Ready for planning

<domain>
## Phase Boundary

Separate the scanner (background market scanning, scoring, discovery) and API server (FastAPI endpoints) into two independent OS processes. Both share the same Postgres database. This eliminates the production bug where scanner resource usage (40 HTTP threads, DB writes) starves API handlers and causes ReadTimeout errors experienced by the Chrome extension.

</domain>

<decisions>
## Implementation Decisions

### Health Endpoint Redesign
- **D-01:** Scanner writes operational metrics to a `scanner_status` DB table, upserted every dispatch cycle (~30s). API's `/health` endpoint reads from this table instead of in-memory scanner state.
- **D-02:** Metrics to persist: `is_running`, `success_rate_1h`, `last_scan_at`, `queue_depth`, `circuit_breaker_state`. The `players_in_db` metric is already a DB query — no change needed.

### Process Management
- **D-03:** Both processes managed via Docker Compose. Two services: `api` (uvicorn src.server.main:app) and `scanner` (python -m src.server.scanner_main). Alongside the existing Postgres service.
- **D-04:** Single `docker-compose up` starts everything. Auto-restart on failure via Docker Compose restart policies.

### Entry Points
- **D-05:** New `src/server/scanner_main.py` — thin entry point that creates DB engine, ScannerService, CircuitBreaker, APScheduler, and runs `asyncio.run()`. Reuses all existing scanner code unchanged.
- **D-06:** `src/server/main.py` lifespan drops all scanner/scheduler startup. API process has no scanner, no scheduler, no FutGGClient. Just FastAPI + DB pool.

### Test Harness
- **D-07:** Integration tests use Docker Compose — exactly like production, with DATABASE_URL pointing to the test DB as the only difference.
- **D-08:** conftest.py starts services via Docker Compose (or equivalent), waits for both to be healthy, runs tests, tears down after.

### Claude's Discretion
- DB pool sizing for each process (scanner vs API may need different pool configs)
- Docker Compose networking details
- Health check wait timeout values
- Whether scanner_main.py needs its own logging config or reuses existing

</decisions>

<canonical_refs>
## Canonical References

No external specs — requirements fully captured in decisions above.

### Key Source Files
- `src/server/main.py` — Current combined lifespan (scanner + API)
- `src/server/scanner.py` — ScannerService class (reused as-is)
- `src/server/scheduler.py` — APScheduler config (moves to scanner process)
- `src/server/api/health.py` — Health endpoint (must be rewritten for DB reads)
- `src/server/circuit_breaker.py` — CircuitBreaker (moves to scanner process only)
- `tests/integration/conftest.py` — Test server startup (must use Docker Compose)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `ScannerService` class: fully self-contained, takes `session_factory` and `circuit_breaker` as constructor args — can be instantiated from any entry point
- `create_scheduler()`: already a standalone function that takes a scanner instance — moves to scanner_main.py unchanged
- `CircuitBreaker`: stateless between restarts, only scanner needs it

### Established Patterns
- `create_engine_and_tables()` in `db.py`: creates engine + session factory from DATABASE_URL env var — both processes can call this independently
- Lifespan context manager pattern in `main.py`: scanner_main.py can follow same pattern

### Integration Points
- Health endpoint (`/api/v1/health`): currently reads `app.state.scanner` — must be rewritten to query `scanner_status` DB table
- `app.state.scanner` and `app.state.circuit_breaker`: removed from API process entirely
- Docker Compose: new `docker-compose.yml` (or extend existing if one exists for Postgres)

</code_context>

<specifics>
## Specific Ideas

- User explicitly wants test/prod parity: "tests and prod should be exactly the same"
- The production API timeout bug is real and happening daily — this is not a theoretical concern
- Scanner resource contention (40 threads, batch 200, semaphore 5 DB writes) is the confirmed root cause

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 10-split-scanner-and-api-into-separate-processes*
*Context gathered: 2026-03-30*
