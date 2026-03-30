# Phase 10: Split Scanner and API into Separate Processes - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-03-30
**Phase:** 10-split-scanner-and-api-into-separate-processes
**Areas discussed:** Health endpoint redesign, Process management, Entry points, Test harness impact

---

## Health Endpoint Redesign

| Option | Description | Selected |
|--------|-------------|----------|
| DB metrics table | Scanner upserts a single row every dispatch cycle (~30s). API reads it. Simple, no new infra. | ✓ |
| Scanner HTTP endpoint | Scanner exposes its own small HTTP server. Real-time but adds a second HTTP server. | |
| Redis | Scanner writes metrics to Redis, API reads. Real-time but new dependency. | |

**User's choice:** DB metrics table
**Notes:** Already have Postgres, no need for new infra. 30s staleness is acceptable for health checks.

---

## Process Management

| Option | Description | Selected |
|--------|-------------|----------|
| Docker Compose | Two services in docker-compose.yml alongside Postgres. One command starts everything. | ✓ |
| Shell script | run.sh that starts both processes, manages PIDs. Simpler but no auto-restart. | |
| Makefile targets | Separate make targets. Clean but requires two terminals. | |

**User's choice:** Docker Compose
**Notes:** Already using Docker for Postgres. Natural fit.

---

## Entry Points

| Option | Description | Selected |
|--------|-------------|----------|
| New scanner_main.py | Thin entry point reusing existing ScannerService. API drops scanner from lifespan. | ✓ |
| CLI subcommand | click command: python -m src.main run-scanner. Mixes CLI with server concerns. | |
| Same main.py with flag | --mode=api or --mode=scanner. Less files but more conditional logic. | |

**User's choice:** New scanner_main.py
**Notes:** Clean separation of concerns.

---

## Test Harness Impact

| Option | Description | Selected |
|--------|-------------|----------|
| Docker Compose for tests | conftest.py uses Docker Compose with DATABASE_URL override. True prod parity. | ✓ |
| Two subprocesses | conftest.py spawns 2 Popen calls. Faster but different from prod deployment. | |

**User's choice:** Docker Compose for tests — "exactly like prod, just different db path"
**Notes:** User strongly values test/prod parity. Tests should use the exact same launch mechanism as production.

---

## Claude's Discretion

- DB pool sizing per process
- Docker Compose networking details
- Health check wait timeouts
- Scanner logging config

## Deferred Ideas

None
