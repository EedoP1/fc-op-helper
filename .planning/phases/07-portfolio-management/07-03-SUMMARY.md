---
phase: 07-portfolio-management
plan: 03
subsystem: ui
tags: [chrome-extension, typescript, overlay, dom, jsdom, vitest]

# Dependency graph
requires:
  - phase: 07-portfolio-management
    plan: 01
    provides: POST /portfolio/generate, /confirm, /swap-preview, GET /confirmed endpoints
  - phase: 07-portfolio-management
    plan: 02
    provides: PORTFOLIO_* message types, PortfolioPlayer/ConfirmedPortfolio types, portfolioItem storage

provides:
  - extension/src/overlay/panel.ts — createOverlayPanel() factory with three-state panel (empty/draft/confirmed)
  - extension/entrypoints/ea-webapp.content.ts — panel injection, PORTFOLIO_LOAD on mount, SPA re-injection
  - extension/tests/overlay.test.ts — 12 tests covering panel state transitions, swap, generate, confirm, destroy, toggle

affects:
  - Phase 8 (DOM automation will build on top of this content script)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - Closure-based DOM panel factory with setState() clearing and re-rendering innerHTML per state
    - Guard ctx.isInvalid before any sendMessage call in content script to avoid breaking idle-invalidated tests
    - Panel creation before event handler registration to avoid temporal dead zone in wxt:locationchange closure

key-files:
  created:
    - extension/src/overlay/panel.ts
    - extension/tests/overlay.test.ts
  modified:
    - extension/entrypoints/ea-webapp.content.ts

key-decisions:
  - "Panel declared before wxt:locationchange handler to avoid TDZ — const panel = createOverlayPanel() hoisted above ctx.addEventListener"
  - "PORTFOLIO_LOAD guarded by ctx.isInvalid check — preserves existing test contract (no sendMessage when ctx invalid)"
  - "setState() uses closure draftPlayers/draftBudget vars — full re-render on each state change (simpler than incremental DOM updates)"
  - "Instant player removal from draftPlayers before PORTFOLIO_SWAP response (D-09) — UI feels responsive, replacement spliced in when response arrives"

patterns-established:
  - "Overlay factory pattern: createOverlayPanel() returns {container, toggle, setState, destroy} — caller appends to document.body"
  - "Panel state machine: empty→draft (Generate), draft→confirmed (Confirm), confirmed→empty (Regenerate)"
  - "Test isolation for DOM panels: document.body.innerHTML = '' in beforeEach"

requirements-completed: [UI-01, UI-03]

# Metrics
duration: 20min
completed: 2026-03-27
---

# Phase 07 Plan 03: Overlay Panel Summary

**Dark-themed collapsible right sidebar injected into EA Web App with three-state portfolio flow (empty/draft/confirmed), player swap with instant removal and auto-replacement, and confirmed portfolio auto-loaded on mount — 12 new tests, 30 total passing**

## Performance

- **Duration:** ~20 min
- **Started:** 2026-03-27T10:20:00Z
- **Completed:** 2026-03-27T10:40:00Z
- **Tasks:** 1 (+ checkpoint)
- **Files modified:** 3

## Accomplishments

- `createOverlayPanel()` factory builds a 320px fixed right sidebar with dark theme (#1a1a2e) and z-index 999999
- Three panel states: empty (budget input + Generate), draft (player list with X remove, Confirm), confirmed (read-only + Regenerate)
- Remove (X) in draft instantly removes player from DOM, sends PORTFOLIO_SWAP, splices replacement at same index on response
- Generate sends PORTFOLIO_GENERATE; Confirm sends PORTFOLIO_CONFIRM; Regenerate resets to empty state
- Content script injects panel, sends PORTFOLIO_LOAD on mount to restore confirmed portfolio from service worker storage
- Panel re-injected on wxt:locationchange if removed by SPA navigation (D-04 pitfall handled)
- All 30 extension tests pass; TypeScript compiles without errors

## Task Commits

1. **Task 1: Overlay panel + content script integration** — `68116b0` (feat)
2. **Task 2: Verify on EA Web App** — checkpoint (awaiting human verification)

## Files Created/Modified

- `extension/src/overlay/panel.ts` — Full overlay panel factory: DOM construction, toggle, three-state render, swap/confirm/generate handlers (390 lines)
- `extension/entrypoints/ea-webapp.content.ts` — Added `createOverlayPanel` import, panel injection, PORTFOLIO_LOAD on mount, SPA re-injection in locationchange handler
- `extension/tests/overlay.test.ts` — 12 tests: container/toggle creation, z-index check, empty state, draft with players, confirmed without X buttons, destroy, toggle open/close, swap message, regenerate, generate

## Decisions Made

- Panel declared before `wxt:locationchange` handler to avoid temporal dead zone — the closure captures `panel` before it fires.
- `PORTFOLIO_LOAD` guarded by `ctx.isInvalid` check — existing test "stops reconnect loop when ctx.isInvalid is true" asserts no sendMessage calls; our new code would have broken that contract.
- `setState()` clears `container.innerHTML` and re-renders — simpler than incremental DOM patching; panel size is small enough that full re-render is imperceptible.
- Draft players stored in closure array (`draftPlayers`) — D-10 states draft is ephemeral and in-memory only, never written to storage.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Guarded PORTFOLIO_LOAD sendMessage with ctx.isInvalid check**
- **Found during:** Task 1 (running tests after implementation)
- **Issue:** New PORTFOLIO_LOAD call in content script fired unconditionally, breaking existing test "stops reconnect loop when ctx.isInvalid is true" — test asserts no sendMessage calls when ctx is already invalid
- **Fix:** Wrapped `chrome.runtime.sendMessage({ type: 'PORTFOLIO_LOAD' })` in `if (!ctx.isInvalid)` guard
- **Files modified:** extension/entrypoints/ea-webapp.content.ts
- **Verification:** All 30 tests pass after fix
- **Committed in:** 68116b0 (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (Rule 1 bug)
**Impact on plan:** Fix required for test correctness; no functional change in production (content script never runs with ctx.isInvalid=true at mount).

## Issues Encountered

None beyond the auto-fixed Rule 1 bug above.

## User Setup Required

None — no external service configuration required. Visual verification at EA Web App is the Task 2 checkpoint.

## Next Phase Readiness

- Overlay panel complete; extension can generate, confirm, swap portfolio from EA Web App
- Phase 07 complete after human verification checkpoint
- Phase 08 (DOM automation) can now build on top of the content script foundation
- Blocker documented in STATE.md: EA Web App DOM selectors require live DevTools inspection before Phase 08 automation code is written

---
*Phase: 07-portfolio-management*
*Completed: 2026-03-27*

## Self-Check: PASSED

- FOUND: extension/src/overlay/panel.ts
- FOUND: extension/tests/overlay.test.ts
- FOUND: .planning/phases/07-portfolio-management/07-03-SUMMARY.md
- FOUND: commit 68116b0 (feat(07-03): add overlay panel)
