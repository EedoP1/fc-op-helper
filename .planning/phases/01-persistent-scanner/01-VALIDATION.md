---
phase: 1
slug: persistent-scanner
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-25
---

# Phase 1 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2 |
| **Config file** | tests/ directory (existing) |
| **Quick run command** | `python -m pytest tests/ -x -q` |
| **Full suite command** | `python -m pytest tests/ -v` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/ -x -q`
- **After every plan wave:** Run `python -m pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| TBD | TBD | TBD | SCAN-01 | integration | `python -m pytest tests/test_scanner.py -v` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | SCAN-02 | integration | `python -m pytest tests/test_scanner.py -v` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | SCAN-04 | unit | `python -m pytest tests/test_circuit_breaker.py -v` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | API-03 | integration | `python -m pytest tests/test_api.py -v` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | API-04 | integration | `python -m pytest tests/test_api.py -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_scanner.py` — stubs for SCAN-01, SCAN-02
- [ ] `tests/test_circuit_breaker.py` — stubs for SCAN-04
- [ ] `tests/test_api.py` — stubs for API-03, API-04
- [ ] `tests/conftest.py` — shared fixtures (async client, test DB session)
- [ ] Install test dependencies: `pytest-httpx`, `httpx` (test client)

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Scanner survives sustained 429s over 10+ minutes | SCAN-04 | Requires real fut.gg throttling behavior | Run scanner against fut.gg for 30 min, verify no crashes in logs |
| Priority queue reorders after activity spike | SCAN-02 | Requires real market activity variance | Check DB `next_scan_at` values after a full scan cycle |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
