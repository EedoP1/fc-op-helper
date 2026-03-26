---
phase: 5
slug: backend-infrastructure
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-26
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| **Config file** | tests/ directory (existing) |
| **Quick run command** | `python -m pytest tests/ -x -q` |
| **Full suite command** | `python -m pytest tests/ -v` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/ -x -q`
- **After every plan wave:** Run `python -m pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 05-01-01 | 01 | 1 | BACK-03 | unit | `python -c "from src.server.models_db import TradeAction, TradeRecord, PortfolioSlot"` | ✅ | ⬜ pending |
| 05-01-02 | 01 | 1 | BACK-05 | unit | `python -c "from src.server.main import app; ..."` | ✅ | ⬜ pending |
| 05-01-03 | 01 | 1 | BACK-05 | integration | `python -m pytest tests/test_cors.py -v` | ❌ W0 | ⬜ pending |
| 05-02-01 | 02 | 2 | BACK-01, BACK-02 | integration | `python -m pytest tests/test_actions.py -v` | ❌ W0 | ⬜ pending |
| 05-03-01 | 03 | 3 | BACK-04 | integration | `python -m pytest tests/test_profit.py -v` | ❌ W0 | ⬜ pending |
| 05-03-02 | 03 | 3 | BACK-06 | integration | `python -m pytest tests/test_portfolio_swap.py -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_cors.py` — BACK-05 CORS preflight integration tests (Plan 01, Task 3)
- [ ] `tests/test_actions.py` — BACK-01, BACK-02 action queue + completion tests (Plan 02, Task 1)
- [ ] `tests/test_profit.py` — BACK-04 profit summary tests (Plan 03, Task 1)
- [ ] `tests/test_portfolio_swap.py` — BACK-06 player swap tests (Plan 03, Task 2)

*Existing pytest + pytest-asyncio infrastructure covers framework needs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Chrome extension CORS | BACK-05 | Real chrome-extension:// origin needs browser | Load extension, verify fetch to localhost:8000 succeeds |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
