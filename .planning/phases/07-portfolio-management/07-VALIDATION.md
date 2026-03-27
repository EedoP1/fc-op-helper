---
phase: 7
slug: portfolio-management
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-27
---

# Phase 7 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework (backend)** | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| **Config file (backend)** | `pytest.ini` or inline |
| **Quick run command (backend)** | `pytest tests/test_portfolio*.py -x` |
| **Full suite command (backend)** | `pytest tests/ -x` |
| **Framework (extension)** | Vitest 4.1.2 + WxtVitest + jsdom |
| **Config file (extension)** | `extension/vitest.config.ts` |
| **Quick run command (extension)** | `cd extension && npm test -- --run` |
| **Full suite command** | `pytest tests/ -x && cd extension && npm test -- --run` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `pytest tests/test_portfolio*.py -x && cd extension && npm test -- --run`
- **After every plan wave:** Run `pytest tests/ -x && cd extension && npm test -- --run`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 07-01-01 | 01 | 1 | PORT-01 | integration | `pytest tests/test_portfolio_generate.py -x` | ❌ W0 | ⬜ pending |
| 07-01-02 | 01 | 1 | PORT-01 | integration | `pytest tests/test_portfolio_confirm.py -x` | ❌ W0 | ⬜ pending |
| 07-01-03 | 01 | 1 | PORT-01 | integration | `pytest tests/test_portfolio_swap_preview.py -x` | ❌ W0 | ⬜ pending |
| 07-01-04 | 01 | 1 | PORT-01 | integration | `pytest tests/test_portfolio_confirmed.py -x` | ❌ W0 | ⬜ pending |
| 07-02-01 | 02 | 1 | UI-01 | unit | `cd extension && npm test -- --run tests/background.test.ts` | ✅ extend | ⬜ pending |
| 07-02-02 | 02 | 1 | UI-01 | unit | `cd extension && npm test -- --run tests/overlay.test.ts` | ❌ W0 | ⬜ pending |
| 07-03-01 | 03 | 2 | UI-01, UI-03 | unit | `cd extension && npm test -- --run tests/overlay.test.ts` | ❌ W0 | ⬜ pending |
| 07-03-02 | 03 | 2 | UI-03 | unit | `cd extension && npm test -- --run tests/background.test.ts` | ✅ extend | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_portfolio_generate.py` — stubs for PORT-01 generate endpoint
- [ ] `tests/test_portfolio_confirm.py` — stubs for PORT-01 confirm endpoint
- [ ] `tests/test_portfolio_swap_preview.py` — stubs for PORT-01 swap-preview endpoint
- [ ] `tests/test_portfolio_confirmed.py` — stubs for PORT-01 GET confirmed endpoint
- [ ] `extension/tests/overlay.test.ts` — stubs for UI-01 panel states, UI-03 swap

*Existing infrastructure covers extension message tests (extend `background.test.ts`, `content.test.ts`).*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Overlay visual match to EA dark theme | UI-01 (D-02) | Requires visual inspection of injected DOM on live EA Web App | Load EA Web App with extension, open overlay, compare colors/fonts |
| Panel survives SPA navigation | UI-01 (D-11) | Requires live EA Web App SPA route changes | Navigate between EA Web App pages, verify panel persists |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
