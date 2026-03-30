---
phase: 10
slug: split-scanner-and-api-into-separate-processes
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-30
---

# Phase 10 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x with pytest-asyncio |
| **Config file** | `pytest.ini` or `pyproject.toml` |
| **Quick run command** | `pytest tests/ -x -q --timeout=60` |
| **Full suite command** | `pytest tests/ -v --timeout=120` |
| **Estimated runtime** | ~30 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/ -x -q --timeout=60`
- **After every plan wave:** Run `pytest tests/ -v --timeout=120`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 10-01-01 | 01 | 1 | SPLIT-03 scanner_status table | unit | `pytest tests/ -k scanner_status` | No W0 | pending |
| 10-01-02 | 01 | 1 | SPLIT-04 health endpoint DB read | integration | `pytest tests/ -k health` | Yes | pending |
| 10-02-01 | 02 | 2 | SPLIT-01 scanner_main entry point | unit | `pytest tests/ -k scanner_main` | No W0 | pending |
| 10-02-02 | 02 | 2 | SPLIT-02 API lifespan no scanner | integration | `pytest tests/ -k lifespan` | No W0 | pending |
| 10-03-01 | 03 | 3 | SPLIT-05 Docker Compose config | integration | `docker compose config --quiet` | No W0 | pending |
| 10-03-02 | 03 | 3 | SPLIT-06 Docker Compose integration tests | integration | `pytest tests/integration/ -v` | Yes | pending |

*Status: pending / green / red / flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_scanner_status.py` — scanner_status table model and upsert
- [ ] `tests/test_scanner_main.py` — scanner_main entry point validation
- [ ] Existing `tests/integration/conftest.py` — updated for Docker Compose startup

*Existing test infrastructure covers framework and fixtures.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Docker Compose `docker-compose up` starts both services | SPLIT-05 | Requires Docker daemon | Run `docker compose up -d`, verify both containers healthy |
| Auto-restart on failure | SPLIT-05 | Requires killing a container | `docker kill <scanner>`, verify it restarts within 10s |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
