---
phase: 8
slug: dom-automation-layer
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-30
---

# Phase 8 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 9.x (backend), manual browser testing (extension DOM) |
| **Config file** | `pytest.ini` (backend), none (extension — manual verification) |
| **Quick run command** | `python -m pytest tests/ -x -q --timeout=30` |
| **Full suite command** | `python -m pytest tests/ -v --timeout=60` |
| **Estimated runtime** | ~15 seconds (backend only) |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/ -x -q --timeout=30`
- **After every plan wave:** Run `python -m pytest tests/ -v --timeout=60`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| TBD | TBD | TBD | AUTO-01 | manual | Browser: search + buy flow | N/A | ⬜ pending |
| TBD | TBD | TBD | AUTO-02 | manual | Browser: list after buy | N/A | ⬜ pending |
| TBD | TBD | TBD | AUTO-03 | manual | Browser: relist expired | N/A | ⬜ pending |
| TBD | TBD | TBD | AUTO-04 | unit | `pytest tests/test_daily_cap.py` | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | AUTO-05 | unit+manual | Jitter unit test + browser timing | ❌ W0 | ⬜ pending |
| TBD | TBD | TBD | AUTO-06 | manual | Browser: start/stop/resume | N/A | ⬜ pending |
| TBD | TBD | TBD | AUTO-07 | manual | Browser: missing selector alert | N/A | ⬜ pending |
| TBD | TBD | TBD | AUTO-08 | code review | Verify selectors.ts centralization | N/A | ⬜ pending |
| TBD | TBD | TBD | UI-02 | manual | Browser: status display | N/A | ⬜ pending |
| TBD | TBD | TBD | UI-04 | manual | Browser: start automation button | N/A | ⬜ pending |
| TBD | TBD | TBD | UI-05 | manual | Browser: activity log | N/A | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_daily_cap.py` — stubs for AUTO-04 daily cap backend endpoint
- [ ] `tests/test_automation_api.py` — stubs for fresh price endpoint
- [ ] DOM exploration task — live DevTools inspection to document selectors in selectors.ts

*Note: Most AUTO requirements involve live browser DOM interaction and cannot be fully automated in unit tests. Backend endpoints (daily cap, fresh prices) have automated coverage.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Buy flow execution | AUTO-01 | Requires live EA Web App session | Search player, verify buy only when BIN <= target |
| Post-buy listing | AUTO-02 | Requires live EA Web App session | Buy card, verify auto-list at OP price |
| Relist expired | AUTO-03 | Requires expired listings in EA | Wait for expiry, verify relist-all at same price |
| Human-paced jitter | AUTO-05 | Timing observation needed | Watch automation, verify random delays 800-2500ms |
| CAPTCHA/DOM failure | AUTO-07 | Cannot simulate CAPTCHA | Manually break a selector, verify loud failure |
| Start/stop/resume | AUTO-06 | Full cycle state management | Start, stop mid-cycle, resume, verify correct action |
| Status display | UI-02 | Visual verification | Check overlay shows current action, last event, state |
| Activity log | UI-05 | Visual verification | Check collapsible log with timestamps |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
