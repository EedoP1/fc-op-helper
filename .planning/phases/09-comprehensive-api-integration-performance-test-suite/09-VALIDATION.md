---
phase: 9
slug: comprehensive-api-integration-performance-test-suite
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-28
---

# Phase 9 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.0.2 + vitest (extension) |
| **Config file** | `pytest.ini` (asyncio_mode = auto) |
| **Quick run command** | `python -m pytest tests/ --ignore=tests/test_health_check.py -q --tb=short` |
| **Full suite command** | `python -m pytest tests/ --ignore=tests/test_health_check.py` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/ --ignore=tests/test_health_check.py -q --tb=short`
- **After every plan wave:** Run `python -m pytest tests/`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 09-01-01 | 01 | 1 | Health-01 | infra | `python -m pytest tests/ --collect-only 2>&1 \| grep 'ERROR' && exit 1 \|\| echo 'Collection clean'` | ✅ | ⬜ pending |
| 09-01-02 | 01 | 1 | Batch-01..04 | unit | `pytest tests/test_batch_trade_records.py -x` | ❌ W0 | ⬜ pending |
| 09-02-01 | 02 | 1 | Lifecycle-01..05 | integration | `pytest tests/test_lifecycle_flows.py -x` | ❌ W0 | ⬜ pending |
| 09-03-01 | 03 | 2 | Perf-01..03 | smoke | `pytest tests/test_performance.py -x` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_health_check.py` — fix broken import (FutbinClient removed)
- [ ] `tests/test_lifecycle_flows.py` — cross-endpoint trade lifecycle flows
- [ ] `tests/test_batch_trade_records.py` — POST /trade-records/batch coverage
- [ ] `tests/test_performance.py` — latency smoke assertions

*Existing infrastructure covers test framework and fixtures.*

---

## Manual-Only Verifications

*All phase behaviors have automated verification.*

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
