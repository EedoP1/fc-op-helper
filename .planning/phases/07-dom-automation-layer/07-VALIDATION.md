---
phase: 7
slug: dom-automation-layer
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
| **Framework** | Vitest ^4.1.2 + WXT fakeBrowser |
| **Config file** | `extension/vitest.config.ts` |
| **Quick run command** | `cd extension && npm test -- --run` |
| **Full suite command** | `cd extension && npm test -- --run` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd extension && npm test -- --run`
- **After every plan wave:** Run `cd extension && npm test -- --run && npm run compile`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 07-01-01 | 01 | 0 | AUTO-08 | structural | Source inspection | ❌ W0 | ⬜ pending |
| 07-02-01 | 02 | 1 | AUTO-05 | unit | `npm test -- --run tests/dom-utils.test.ts` | ❌ W0 | ⬜ pending |
| 07-02-02 | 02 | 1 | AUTO-07 | unit | `npm test -- --run tests/dom-utils.test.ts` | ❌ W0 | ⬜ pending |
| 07-03-01 | 03 | 1 | AUTO-06 | unit | `npm test -- --run tests/content.test.ts` | ❌ extend | ⬜ pending |
| 07-04-01 | 04 | 2 | AUTO-01 | unit | `npm test -- --run tests/automation/buy-flow.test.ts` | ❌ W0 | ⬜ pending |
| 07-04-02 | 04 | 2 | AUTO-02 | unit | `npm test -- --run tests/automation/buy-flow.test.ts` | ❌ W0 | ⬜ pending |
| 07-05-01 | 05 | 2 | AUTO-03 | unit | `npm test -- --run tests/automation/list-flow.test.ts` | ❌ W0 | ⬜ pending |
| 07-06-01 | 06 | 2 | AUTO-04 | unit | `npm test -- --run tests/automation/relist-flow.test.ts` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `extension/tests/dom-utils.test.ts` — stubs for AUTO-05, AUTO-07
- [ ] `extension/tests/automation/buy-flow.test.ts` — stubs for AUTO-01, AUTO-02
- [ ] `extension/tests/automation/list-flow.test.ts` — stubs for AUTO-03
- [ ] `extension/tests/automation/relist-flow.test.ts` — stubs for AUTO-04
- [ ] `extension/src/selectors.ts` — centralized selector file (AUTO-08)
- [ ] `extension/src/automation/dom-utils.ts` — shared DOM utilities
- [ ] Add `'notifications'` to `wxt.config.ts` permissions array

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| EA Web App selector correctness | AUTO-01–04 | Obfuscated DOM; selectors change per EA version | Verify each selector against live FC26 Web App via DevTools |
| CAPTCHA detection triggers stop | AUTO-06 | Requires real CAPTCHA to appear in EA app | Simulate by injecting CAPTCHA container element in DevTools |
| Full buy/list/relist cycle | ALL | End-to-end requires live EA Web App session | Execute one full cycle manually with automation enabled |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
