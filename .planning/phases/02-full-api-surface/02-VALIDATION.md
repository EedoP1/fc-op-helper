---
phase: 2
slug: full-api-surface
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-25
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2 + pytest-asyncio 1.3.0 |
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
| 02-01-01 | 01 | 1 | API-01 | integration | `python -m pytest tests/test_api.py -k portfolio -v` | ❌ W0 | ⬜ pending |
| 02-01-02 | 01 | 1 | API-02 | integration | `python -m pytest tests/test_api.py -k player_detail -v` | ❌ W0 | ⬜ pending |
| 02-02-01 | 02 | 2 | SCAN-03 | unit | `python -m pytest tests/test_scanner.py -k adaptive -v` | ❌ W0 | ⬜ pending |
| 02-02-02 | 02 | 2 | SCAN-05 | integration | `python -m pytest tests/test_api.py -k score_history -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_api.py` — add test stubs for portfolio endpoint and player detail endpoint
- [ ] `tests/test_scanner.py` — add test stubs for adaptive scheduling logic

*Existing test infrastructure (ASGITransport + make_test_app + in-memory SQLite) covers framework needs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Score history accumulates over time | SCAN-05 | Requires multiple scan cycles over real time | Run server, wait for 2+ scan cycles, query DB for multiple PlayerScore rows per player |
| Adaptive scan timing observable in DB | SCAN-03 | Requires live scanning with varying activity levels | Run server, compare next_scan_at values across hot/normal/cold players after activity changes |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
