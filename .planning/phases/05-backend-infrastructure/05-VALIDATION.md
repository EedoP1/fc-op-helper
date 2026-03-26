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
| 05-01-01 | 01 | 1 | BACK-03 | unit | `python -m pytest tests/test_trade_models.py -v` | ❌ W0 | ⬜ pending |
| 05-01-02 | 01 | 1 | BACK-01 | unit | `python -m pytest tests/test_action_queue.py -v` | ❌ W0 | ⬜ pending |
| 05-02-01 | 02 | 1 | BACK-01 | integration | `python -m pytest tests/test_actions_api.py -v` | ❌ W0 | ⬜ pending |
| 05-02-02 | 02 | 1 | BACK-02 | integration | `python -m pytest tests/test_actions_api.py -v` | ❌ W0 | ⬜ pending |
| 05-02-03 | 02 | 1 | BACK-04 | integration | `python -m pytest tests/test_profit_api.py -v` | ❌ W0 | ⬜ pending |
| 05-02-04 | 02 | 1 | BACK-06 | integration | `python -m pytest tests/test_swap_api.py -v` | ❌ W0 | ⬜ pending |
| 05-03-01 | 03 | 2 | BACK-05 | integration | `python -m pytest tests/test_cors.py -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_trade_models.py` — stubs for BACK-03 trade DB models
- [ ] `tests/test_action_queue.py` — stubs for BACK-01 action queue logic
- [ ] `tests/test_actions_api.py` — stubs for BACK-01, BACK-02 API endpoints
- [ ] `tests/test_profit_api.py` — stubs for BACK-04 profit summary
- [ ] `tests/test_swap_api.py` — stubs for BACK-06 player swap
- [ ] `tests/test_cors.py` — stubs for BACK-05 CORS config

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
