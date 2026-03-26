---
phase: 6
slug: extension-architecture-foundation
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-03-27
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | Vitest 4.1.2 |
| **Config file** | `extension/vitest.config.ts` (Wave 0 — does not exist yet) |
| **Quick run command** | `cd extension && npm run test -- --run` |
| **Full suite command** | `cd extension && npm run test` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `cd extension && npx tsc --noEmit`
- **After every plan wave:** Run `cd extension && npm run test -- --run`
- **Before `/gsd:verify-work`:** Full suite must be green + `npm run build` succeeds
- **Max feedback latency:** 10 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 06-01-01 | 01 | 1 | ARCH-01 | smoke | `cd extension && npm run build && node -e "const m=require('.output/chrome-mv3/manifest.json');if(!m.background?.service_worker)throw new Error('no SW')"` | ❌ W0 | ⬜ pending |
| 06-01-02 | 01 | 1 | ARCH-02 | unit | `cd extension && npm run test -- --run tests/background.test.ts` | ❌ W0 | ⬜ pending |
| 06-01-03 | 01 | 1 | ARCH-02 | unit | `cd extension && npm run test -- --run tests/background.test.ts` | ❌ W0 | ⬜ pending |
| 06-02-01 | 02 | 1 | ARCH-03 | unit | `cd extension && npm run test -- --run tests/content.test.ts` | ❌ W0 | ⬜ pending |
| 06-02-02 | 02 | 1 | ARCH-03 | compile | `cd extension && npx tsc --noEmit` | inherent | ⬜ pending |
| 06-02-03 | 02 | 1 | ARCH-04 | unit | `cd extension && npm run test -- --run tests/content.test.ts` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `extension/vitest.config.ts` — WxtVitest plugin config
- [ ] `extension/tests/background.test.ts` — stubs for ARCH-02
- [ ] `extension/tests/content.test.ts` — stubs for ARCH-03, ARCH-04
- [ ] Framework install: `cd extension && npm install` — after WXT init

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Extension loads in Chrome without manifest errors | ARCH-01 | Requires Chrome browser + unpacked load | Load unpacked from `extension/.output/chrome-mv3/`, check `chrome://extensions` for errors |
| Service worker survives DevTools-close + 60s idle | ARCH-02 | Requires live Chrome with DevTools | Open DevTools for service worker, close DevTools, wait 60s, verify alarm still fires |
| SPA navigation re-initialization on EA Web App | ARCH-04 | Requires EA Web App loaded in Chrome | Navigate between pages on EA Web App, verify console logs show re-init |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 10s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
