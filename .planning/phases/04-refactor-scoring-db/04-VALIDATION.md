---
phase: 04
slug: refactor-scoring-db
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-25
---

# Phase 04 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| **Config file** | pytest.ini / pyproject.toml (asyncio_mode=auto) |
| **Quick run command** | `python -m pytest tests/test_scorer_v2.py tests/test_listing_tracker.py -q` |
| **Full suite command** | `python -m pytest tests/ -q` |
| **Estimated runtime** | ~10 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/test_scorer_v2.py tests/test_listing_tracker.py -q`
- **After every plan wave:** Run `python -m pytest tests/ -q`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 | 1 | SCAN-P4-01 | unit | `pytest tests/test_listing_tracker.py::test_fingerprint_upsert -x` | ❌ W0 | ⬜ pending |
| 04-01-02 | 01 | 1 | SCAN-P4-02 | unit | `pytest tests/test_listing_tracker.py::test_outcome_sold -x` | ❌ W0 | ⬜ pending |
| 04-01-03 | 01 | 1 | SCAN-P4-03 | unit | `pytest tests/test_listing_tracker.py::test_outcome_expired -x` | ❌ W0 | ⬜ pending |
| 04-02-01 | 02 | 2 | SCAN-P4-04 | unit | `pytest tests/test_scorer_v2.py::test_expected_profit_per_hour -x` | ❌ W0 | ⬜ pending |
| 04-02-02 | 02 | 2 | SCAN-P4-05 | unit | `pytest tests/test_scorer_v2.py::test_margin_selection -x` | ❌ W0 | ⬜ pending |
| 04-02-03 | 02 | 2 | SCAN-P4-06 | unit | `pytest tests/test_scorer_v2.py::test_bootstrap_min -x` | ❌ W0 | ⬜ pending |
| 04-03-01 | 03 | 2 | SCAN-P4-07 | unit | `pytest tests/test_scanner.py::test_adaptive_next_scan -x` | ❌ W0 | ⬜ pending |
| 04-03-02 | 03 | 2 | SCAN-P4-08 | unit | `pytest tests/test_scanner.py::test_listing_purge -x` | ❌ W0 | ⬜ pending |
| 04-03-03 | 03 | 2 | SCAN-P4-09 | unit | `pytest tests/test_listing_tracker.py::test_daily_summary -x` | ❌ W0 | ⬜ pending |
| 04-04-01 | 04 | 3 | SCAN-P4-10 | integration | `pytest tests/test_integration.py::test_v2_scorer_writes_score -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_listing_tracker.py` — stubs for SCAN-P4-01, SCAN-P4-02, SCAN-P4-03, SCAN-P4-09
- [ ] `tests/test_scorer_v2.py` — stubs for SCAN-P4-04, SCAN-P4-05, SCAN-P4-06
- [ ] Additional test cases in `tests/test_scanner.py` — stubs for SCAN-P4-07, SCAN-P4-08
- [ ] Additional test case in `tests/test_integration.py` — stub for SCAN-P4-10

*Existing infrastructure covers framework and fixture needs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| liveAuctions field discovery (D-04) | SCAN-P4-01 | Requires live API call | Call `get_player_prices()` for any player, inspect `liveAuctions[0].keys()` in log output |
| Bootstrapping transition (v1→v2) | SCAN-P4-10 | Requires real data accumulation over time | Deploy, wait 24-48h, verify `/api/v1/players/top` shows v2 scores |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
