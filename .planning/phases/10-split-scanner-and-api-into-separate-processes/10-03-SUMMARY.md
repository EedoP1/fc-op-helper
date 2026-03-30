---
phase: 10-split-scanner-and-api-into-separate-processes
plan: "03"
subsystem: backend
tags: [docker, docker-compose, integration-tests, scanner, api, deployment]
dependency_graph:
  requires: [scanner-process-entry-point, api-only-lifespan]
  provides: [dockerfile, docker-compose-multi-service, docker-compose-test-override, docker-compose-integration-test-harness]
  affects: [Dockerfile, docker-compose.yml, docker-compose.test.yml, tests/integration/conftest.py]
tech_stack:
  added: [Docker, Docker Compose]
  patterns: [multi-service-docker-compose, compose-override-for-tests, docker-dns-not-localhost, compose-project-namespacing]
key_files:
  created:
    - Dockerfile
    - docker-compose.test.yml
  modified:
    - docker-compose.yml
    - tests/integration/conftest.py
decisions:
  - "Integration tests use Docker Compose (not subprocess.Popen) — exact production parity (D-07)"
  - "docker-compose.test.yml uses postgres-test Docker DNS (not localhost:5433) — containers reach each other via internal service DNS on port 5432"
  - "Test API maps to host port 8001 to avoid conflict with production on port 8000"
  - "Phase 2 wait polls scanner_status != unknown up to 90s, warns instead of failing — scanner health is eventually consistent"
  - "scanner_status added to per-test cleanup — new table introduced in Plan 01"
metrics:
  duration: ~5 min
  completed_date: "2026-03-30"
  tasks_completed: 2
  files_modified: 4
---

# Phase 10 Plan 03: Dockerfile and Docker Compose Multi-Service Config Summary

**One-liner:** Dockerfile created and docker-compose.yml extended with api + scanner services; integration tests rewritten to use Docker Compose with test-DB override instead of subprocess.Popen.

## What Was Built

Created `Dockerfile` using python:3.12-slim as the base image for both api and scanner services. Updated `docker-compose.yml` to add api (port 8000, uvicorn) and scanner (no port, scanner_main.py) services alongside the existing postgres services, both with `restart: unless-stopped` and `depends_on: condition: service_healthy`. Created `docker-compose.test.yml` as an override file that redirects DATABASE_URL to use `postgres-test` Docker service DNS, maps API to port 8001, and sets `restart: "no"`. Rewrote `tests/integration/conftest.py` to launch both services via Docker Compose (`docker compose -f docker-compose.yml -f docker-compose.test.yml -p op_seller_test up -d --build`), with a two-phase readiness wait (API HTTP 200, then scanner_status != "unknown"), and proper teardown via `docker compose ... down --remove-orphans`.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Create Dockerfile, update docker-compose.yml, create docker-compose.test.yml | efc052e | Dockerfile, docker-compose.yml, docker-compose.test.yml |
| 2 | Rewrite integration test conftest for Docker Compose | 61b5a79 | tests/integration/conftest.py |

## Decisions Made

- **Docker Compose for integration tests (D-07):** Tests now use the exact same Dockerfile and docker-compose.yml as production. The only difference is the docker-compose.test.yml override redirecting DATABASE_URL to the postgres-test service. This eliminates the divergence between how tests and production start services.
- **Docker service DNS, not localhost:** Inside Docker containers, `postgres-test` refers to the Compose service via internal DNS on port 5432. Using `localhost:5433` would fail because localhost inside a container refers to the container itself, not the host. The `docker-compose.test.yml` override correctly uses `@postgres-test:5432`.
- **Port 8001 for test API:** Avoids conflict if production stack is also running on port 8000. Mapped via docker-compose.test.yml override.
- **Two-phase scanner readiness wait:** Phase 1 polls `/health` for HTTP 200 (API startup). Phase 2 polls until `scanner_status != "unknown"` (scanner has written its first DB row). Phase 2 warns instead of failing on 90s timeout — scanner health is eventually consistent and tests should not hard-fail on startup timing.
- **scanner_status cleanup:** Added `DELETE FROM scanner_status` to per-test cleanup — this table was introduced in Plan 01 and accumulates rows between tests.
- **COMPOSE_PROJECT namespacing:** Uses `-p op_seller_test` to isolate test containers from any running production stack.

## Deviations from Plan

None — plan executed exactly as written.

## Known Stubs

None.

## Self-Check: PASSED

- Dockerfile exists at project root — FOUND
- Dockerfile contains `FROM python:3.12-slim` — FOUND
- docker-compose.yml contains `api:` service — FOUND
- docker-compose.yml contains `scanner:` service — FOUND
- docker-compose.yml contains `restart: unless-stopped` — FOUND
- docker-compose.yml contains `src.server.main:app` — FOUND
- docker-compose.yml contains `src.server.scanner_main` — FOUND
- docker-compose.test.yml contains `postgres-test` — FOUND
- docker-compose.test.yml does NOT contain `localhost` — CONFIRMED
- docker-compose.test.yml contains `8001:8000` — FOUND
- tests/integration/conftest.py does NOT contain `subprocess.Popen` — CONFIRMED (count=0)
- tests/integration/conftest.py contains `docker compose` (5 references) — FOUND
- tests/integration/conftest.py contains `unknown` (scanner wait) — FOUND
- tests/integration/conftest.py contains `DELETE FROM scanner_status` — FOUND
- Commits efc052e and 61b5a79 exist — CONFIRMED
