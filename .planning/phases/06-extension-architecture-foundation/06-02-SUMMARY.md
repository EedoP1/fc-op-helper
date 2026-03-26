---
phase: 06-extension-architecture-foundation
plan: 02
subsystem: extension
tags: [wxt, chrome-extension, mv3, typescript, vitest, content-script, message-protocol, spa-navigation, auto-reconnect]

# Dependency graph
requires:
  - phase: 06-plan-01
    provides: "WXT scaffold, background.ts service worker, ExtensionMessage types, storage items"
provides:
  - "Content script injecting on EA Web App pages with PING/PONG message handling"
  - "Typed exhaustive switch via assertNever (compile-time safety for all message variants)"
  - "SPA navigation re-initialization via wxt:locationchange + MutationObserver fallback"
  - "Auto-reconnect loop with ctx.isInvalid guard and 2s retry via ctx.setTimeout"
  - "pingActiveTab() in service worker confirming content script alive after action store"
  - "5 unit tests covering all content script behaviors"
affects:
  - phase-07-ea-webapp-automation (content script foundation for DOM automation)
  - phase-08-extension-ui (enabled flag and message channel fully wired)

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "defineContentScript with WXT ctx for lifecycle management (addEventListener, onInvalidated, setTimeout, isInvalid)"
    - "Exhaustive switch with assertNever — all ExtensionMessage variants must be handled or TypeScript emits compile error"
    - "wxt:locationchange as primary SPA re-init trigger (History API), MutationObserver as shallow fallback (D-08)"
    - "tryReconnect loop: ctx.isInvalid guard + ctx.setTimeout for deferred retry (D-09)"
    - "pingActiveTab: chrome.tabs.query with URL pattern + wrapped try/catch (Pitfall 4 avoidance)"

key-files:
  created:
    - extension/entrypoints/ea-webapp.content.ts
    - extension/tests/content.test.ts
  modified:
    - extension/entrypoints/background.ts

key-decisions:
  - "Add case 'PONG' in content script switch — TypeScript requires ALL discriminated union variants handled for assertNever to receive never type; PONG is a response type the content script should never receive, but explicit case is required for compile safety"
  - "sendResponse typed as (response?: any) => void to match Chrome's onMessage.addListener callback signature — using ExtensionMessage caused TypeScript 6.x type narrowing error in the PONG case"
  - "Track wxt:locationchange handlers in test cleanup array — jsdom window is shared across tests; without cleanup, prior test handlers fire on subsequent window.dispatchEvent calls"
  - "Use addListener spy + direct listener invocation for PING/PONG test — fakeBrowser.runtime.onMessage.trigger() does not pass sendResponse callback (3rd arg), so direct invocation is required"

patterns-established:
  - "Pattern: capture addListener spy before main() call to get the registered handler function reference for direct invocation in tests"
  - "Pattern: registeredLocationChangeHandlers array for tracking and cleaning up window event listeners across tests"

requirements-completed: [ARCH-03, ARCH-04]

# Metrics
duration: 6min
completed: 2026-03-27
---

# Phase 06 Plan 02: Content Script with Typed Message Handling Summary

**Content script injecting on EA Web App with exhaustive PING/PONG switch, wxt:locationchange SPA re-initialization, MutationObserver fallback, and auto-reconnect loop — proven by 5 unit tests and successful MV3 build.**

## Performance

- **Duration:** 6 min
- **Started:** 2026-03-26T22:47:17Z
- **Completed:** 2026-03-26T22:53:45Z
- **Tasks:** 3 (2 code + 1 auto-approved checkpoint)
- **Files modified:** 3

## Accomplishments

- Content script scaffolded with `defineContentScript` matching `https://www.ea.com/ea-sports-fc/ultimate-team/web-app/*`
- Typed `handleMessage` with exhaustive switch: PING returns PONG via `sendResponse`, PONG handled explicitly, `default:` calls `assertNever(msg)` — TypeScript emits compile error if a new message variant is added but not handled
- SPA navigation re-initialization: `ctx.addEventListener(window, 'wxt:locationchange', ...)` as primary, `MutationObserver` on `document.body` with `childList: true, subtree: false` as fallback (D-08)
- Auto-reconnect loop: `tryReconnect()` checks `ctx.isInvalid`, calls `chrome.runtime.sendMessage` with PING, schedules `ctx.setTimeout(tryReconnect, 2000)` on rejection (D-09)
- `pingActiveTab()` added to background service worker: queries active EA Web App tab by URL, sends PING via `chrome.tabs.sendMessage`, confirms content script alive after action store
- MV3 manifest includes `content_scripts` entry with correct match pattern and `run_at: document_idle`
- 5 unit tests + 7 background tests = 12 total passing; build succeeds at 18.73 kB; `tsc --noEmit` exits 0

## Task Commits

Each task was committed atomically:

1. **Task 1 RED: Failing tests for content script** - `966bfb1` (test)
2. **Task 1 GREEN: Content script implementation** - `c6098cb` (feat)
3. **Task 2: Add pingActiveTab to service worker + fix exhaustive PONG case** - `51835b6` (feat)
4. **Task 3: Checkpoint auto-approved** - (no commit — verified via build artifact)

## Files Created/Modified

- `extension/entrypoints/ea-webapp.content.ts` — Content script: defineContentScript, handleMessage with exhaustive switch, initListeners/teardownListeners, wxt:locationchange handler, MutationObserver fallback, tryReconnect loop
- `extension/tests/content.test.ts` — 5 unit tests: PING/PONG, assertNever structural check, SPA re-init, reconnect stop, retry scheduling
- `extension/entrypoints/background.ts` — Added pingActiveTab(), ExtensionMessage import, ping call after action store in maybePoll()

## Decisions Made

- **Explicit `case 'PONG'` in content script switch:** `ExtensionMessage = { type: 'PING' } | { type: 'PONG' }`. The content script's switch handles PING but needs an explicit PONG case for TypeScript to narrow `msg` to `never` in the default branch. Without it, `assertNever(msg)` receives `{ type: 'PONG' }` (not `never`) and TypeScript emits a type error. Added `case 'PONG': return false;` with a comment explaining the intent.
- **`sendResponse: (response?: any) => void`:** Chrome's `onMessage.addListener` callback types `sendResponse` as `(response?: any) => void`. Using `(r: ExtensionMessage) => void` caused TypeScript 6.x to emit a type assignment error. The `satisfies ExtensionMessage` on the actual call site preserves type safety for the value passed.
- **Test cleanup for `wxt:locationchange` window listeners:** jsdom shares a single `window` object across all tests. Without explicit cleanup, locationchange handlers from previous tests accumulate and fire on subsequent `window.dispatchEvent` calls, causing incorrect call counts. Fixed by tracking registered handlers in an array and removing them in `beforeEach`.
- **Direct listener invocation for PING/PONG test:** `fakeBrowser.runtime.onMessage.trigger(msg, sender)` only passes 2 arguments — it does not supply a `sendResponse` callback as the 3rd parameter. Testing `sendResponse` requires capturing the registered handler via `addListener` spy and calling it directly with a `vi.fn()` as `sendResponse`.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Added explicit `case 'PONG'` to satisfy TypeScript exhaustive switch**
- **Found during:** Task 2 (`npx tsc --noEmit` check)
- **Issue:** `assertNever(msg)` in `default:` branch required `msg` to be `never`, but with only `case 'PING'` handled, TypeScript narrowed `msg` to `{ type: 'PONG' }` in the default — causing type error TS2345
- **Fix:** Added `case 'PONG': return false;` with documentation comment explaining why the content script never receives PONG at runtime
- **Files modified:** extension/entrypoints/ea-webapp.content.ts
- **Commit:** 51835b6

**2. [Rule 1 - Bug] Changed `sendResponse` type from `ExtensionMessage` to `any`**
- **Found during:** Task 2 (`npx tsc --noEmit` check)
- **Issue:** TypeScript 6.x type narrowing caused TS2345 with `(r: ExtensionMessage) => void` signature when passed to `chrome.runtime.onMessage.addListener`
- **Fix:** Changed to `(response?: any) => void` to match Chrome's actual callback type; added `satisfies ExtensionMessage` on the `sendResponse({ type: 'PONG' })` call site for runtime type safety
- **Files modified:** extension/entrypoints/ea-webapp.content.ts
- **Commit:** 51835b6

**3. [Rule 1 - Bug] Rewrote test assertions to use direct listener invocation and cleanup arrays**
- **Found during:** Task 1 GREEN (test failures)
- **Issue 1:** `fakeBrowser.runtime.onMessage.callListeners` doesn't exist (actual API is `trigger`); `trigger` doesn't pass `sendResponse` as 3rd arg
- **Issue 2:** `import.meta.url` pathname on Windows produced wrong path (`/C:\...`)
- **Issue 3:** `window` event listeners accumulate across tests in jsdom, causing spurious `wxt:locationchange` dispatches
- **Fix:** Used `addListener` spy to capture handler reference; used `node:path` + `fileURLToPath` for cross-platform file read; added `registeredLocationChangeHandlers` cleanup array
- **Files modified:** extension/tests/content.test.ts
- **Commit:** c6098cb (GREEN phase)

## Self-Check: PASSED

- extension/entrypoints/ea-webapp.content.ts — FOUND
- extension/tests/content.test.ts — FOUND
- extension/entrypoints/background.ts — FOUND
- .planning/phases/06-extension-architecture-foundation/06-02-SUMMARY.md — FOUND
- 966bfb1 (test RED commit) — FOUND
- c6098cb (feat GREEN commit) — FOUND
- 51835b6 (feat Task 2 commit) — FOUND
