---
phase: 06-extension-architecture-foundation
plan: 01
subsystem: extension
tags: [wxt, chrome-extension, mv3, typescript, vitest, service-worker, chrome-alarms, chrome-storage]

# Dependency graph
requires:
  - phase: 05-backend-infrastructure
    provides: "GET /api/v1/actions/pending endpoint that service worker polls"
provides:
  - "WXT Chrome extension project scaffolded with TypeScript, Vitest test setup"
  - "MV3 service worker with alarm-based polling, enabled-flag gate, immediate wake poll"
  - "Shared ExtensionMessage discriminated union types (PING/PONG)"
  - "Typed chrome.storage.local items: enabledItem and lastActionItem"
  - "7 passing unit tests covering alarm creation, polling gate, fetch, error handling"
affects:
  - phase-06-plan-02 (content script builds on this foundation)
  - phase-07-ea-webapp-automation (uses background.ts polling and storage items)
  - phase-08-extension-ui (wires enabled flag toggle to enabledItem)

# Tech tracking
tech-stack:
  added:
    - wxt@0.20.20 (Chrome extension build framework, MV3 manifest generation, HMR)
    - typescript@6.0.2 (static typing for discriminated union message protocol)
    - "@types/chrome@0.1.38 (Chrome API type definitions)"
    - vitest@4.1.2 (unit test runner with WxtVitest plugin)
    - "@webext-core/fake-browser (in-memory chrome.* mock bundled via WXT)"
    - jsdom@29.0.1 (vitest environment for Chrome API simulation)
  patterns:
    - "WXT defineBackground entrypoint pattern — main() called on every service worker wake"
    - "Check-and-recreate alarm pattern — chrome.alarms.get().then(alarm => !alarm && create())"
    - "Promise-based chrome.alarms.get() for fake-browser test compatibility"
    - "Storage items via storage.defineItem<T>() for typed, migration-ready persistent state"
    - "TDD with WxtVitest + fakeBrowser — no manual chrome.* mocking in tests"

key-files:
  created:
    - extension/package.json
    - extension/wxt.config.ts
    - extension/tsconfig.json
    - extension/vitest.config.ts
    - extension/.gitignore
    - extension/package-lock.json
    - extension/src/messages.ts
    - extension/src/storage.ts
    - extension/entrypoints/background.ts
    - extension/tests/background.test.ts
  modified: []

key-decisions:
  - "Use Promise-based chrome.alarms.get() (not callback) — fake-browser returns a Promise; callback form receives undefined"
  - "Add types: ['chrome'] to extension/tsconfig.json — WXT auto-generated tsconfig doesn't include @types/chrome"
  - "Call bg.main() directly in tests — WXT defineBackground() returns an object, does not auto-execute main()"
  - "jsdom added as devDependency — vitest environment: 'jsdom' requires it as explicit dep in vitest 4.x"

patterns-established:
  - "Pattern: test service worker by importing the module and calling .main() directly on the exported default"
  - "Pattern: extension directory is a self-contained npm project with its own package.json, .gitignore, and lockfile"

requirements-completed: [ARCH-01, ARCH-02]

# Metrics
duration: 8min
completed: 2026-03-27
---

# Phase 06 Plan 01: WXT Extension Scaffold and MV3 Service Worker Summary

**WXT Chrome extension scaffolded with MV3 service worker using chrome.alarms polling (1-min), enabled-flag gate, immediate-wake fetch, and typed chrome.storage.local items — verified by 7 passing Vitest unit tests and a successful production build.**

## Performance

- **Duration:** 8 min
- **Started:** 2026-03-27T00:35:41Z
- **Completed:** 2026-03-27T00:43:45Z
- **Tasks:** 2
- **Files modified:** 10

## Accomplishments

- WXT project scaffolded in `extension/` subdirectory with TypeScript, Vitest, and WxtVitest plugin
- MV3 service worker implements all D-01 through D-04 decisions: alarm polling, immediate wake, enabled gate, hardcoded localhost URL
- Shared discriminated union types (`ExtensionMessage`) and typed storage items (`enabledItem`, `lastActionItem`) defined and exported
- Build produces valid MV3 manifest with `background.service_worker`, `alarms`/`storage`/`tabs` permissions, and `localhost:8000` host_permissions
- 7 unit tests pass covering all 6 specified behaviors plus non-ok response handling

## Task Commits

Each task was committed atomically:

1. **Task 1: Scaffold WXT project with shared types and storage** - `7b31fbd` (feat)
2. **Task 2 RED: Failing tests for background service worker** - `609e50a` (test)
3. **Task 2 GREEN: Background service worker implementation** - `74969a2` (feat)
4. **Chore: Extension .gitignore and lockfile** - `ec78ff9` (chore)

_Note: TDD task had RED and GREEN commits as per TDD protocol._

## Files Created/Modified

- `extension/package.json` — npm project config with wxt, typescript, vitest, @types/chrome, jsdom devDependencies
- `extension/wxt.config.ts` — MV3 manifest definition with permissions and host_permissions
- `extension/tsconfig.json` — Extends WXT-generated tsconfig, adds `types: ["chrome"]`
- `extension/vitest.config.ts` — WxtVitest plugin for fake-browser test support, jsdom environment
- `extension/.gitignore` — Excludes node_modules/, .output/, .wxt/ from git
- `extension/package-lock.json` — Dependency lockfile
- `extension/src/messages.ts` — ExtensionMessage discriminated union (PING/PONG) and assertNever helper
- `extension/src/storage.ts` — enabledItem (boolean gate) and lastActionItem (PendingAction | null) with full type definitions
- `extension/entrypoints/background.ts` — MV3 service worker: alarm polling, maybePoll, error handling
- `extension/tests/background.test.ts` — 7 unit tests with WxtVitest + fakeBrowser

## Decisions Made

- **Promise-based chrome.alarms.get():** `@webext-core/fake-browser` implements `alarms.get()` as an async function returning a Promise. The callback form (used in the research examples) passes `undefined` to the callback in the fake implementation. Switched to `chrome.alarms.get(name).then(callback)` which works with both real Chrome and fake-browser.
- **`types: ["chrome"]` in tsconfig:** WXT's auto-generated tsconfig doesn't include `@types/chrome`. The `defineBackground` function references `chrome.*` which TypeScript couldn't resolve. Added `"types": ["chrome"]` to extension/tsconfig.json.
- **Call `bg.main()` directly in tests:** WXT's `defineBackground()` returns a config object with a `main` function — it does NOT auto-execute. Tests must call `bg.main()` after importing the module.
- **jsdom as explicit devDependency:** vitest 4.x requires jsdom as an explicit package when using `environment: 'jsdom'`, unlike earlier vitest versions where it was bundled.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Installed missing jsdom dependency**
- **Found during:** Task 2 (test execution)
- **Issue:** vitest 4.x requires jsdom as explicit devDependency when using `environment: 'jsdom'`; plan didn't specify it
- **Fix:** `npm install --save-dev jsdom --ignore-scripts`
- **Files modified:** extension/package.json, extension/package-lock.json
- **Verification:** Tests run successfully with jsdom environment
- **Committed in:** `ec78ff9` (chore commit)

**2. [Rule 1 - Bug] Switched chrome.alarms.get from callback to Promise form**
- **Found during:** Task 2 GREEN (alarm creation test failure)
- **Issue:** `fakeBrowser.alarms.get(name, callback)` — fake-browser doesn't invoke the callback; test expected alarm to be created but callback never fired
- **Fix:** Changed to `chrome.alarms.get(POLL_ALARM).then(alarm => ...)` which works with fake-browser's Promise implementation
- **Files modified:** extension/entrypoints/background.ts
- **Verification:** "creates poll alarm on startup" test passes; real Chrome supports Promise form in MV3
- **Committed in:** `74969a2` (Task 2 GREEN commit)

**3. [Rule 1 - Bug] Added `types: ["chrome"]` to tsconfig**
- **Found during:** Task 2 (tsc --noEmit check)
- **Issue:** TypeScript reported `Cannot find name 'chrome'` — WXT's generated tsconfig doesn't include @types/chrome
- **Fix:** Updated extension/tsconfig.json to add `"compilerOptions": { "types": ["chrome"] }`
- **Files modified:** extension/tsconfig.json
- **Verification:** `npx tsc --noEmit` exits 0 with no errors
- **Committed in:** `74969a2` (Task 2 GREEN commit)

**4. [Rule 1 - Bug] Updated tests to call bg.main() directly**
- **Found during:** Task 2 GREEN (tests not triggering background execution)
- **Issue:** Initial test used `await import('../entrypoints/background')` expecting it to auto-execute. WXT's `defineBackground()` returns an object — it does NOT auto-call `main()`.
- **Fix:** Updated test helper to call `const bg = mod.default; bg.main()` after importing
- **Files modified:** extension/tests/background.test.ts
- **Verification:** Tests pass with correct behavior; 7/7 green
- **Committed in:** `74969a2` (Task 2 GREEN commit)

---

**Total deviations:** 4 auto-fixed (1 blocking dependency, 3 bugs)
**Impact on plan:** All auto-fixes were necessary for tests to run and TypeScript to compile. No scope creep — all fixes are within the extension/ boundary.

## Issues Encountered

- WXT postinstall (`wxt prepare`) failed on empty `entrypoints/` directory — resolved by creating placeholder background.ts before running `npm install`, then running `npx wxt prepare` separately
- `npx wxt@latest init extension` was not used (skipped to avoid interactive prompts) — project was scaffolded manually by writing files directly, which produced the same output

## User Setup Required

None — no external service configuration required. Extension loads from `extension/` directory as an unpacked extension in Chrome developer mode.

## Next Phase Readiness

- Extension project builds successfully; ready for Phase 06-02 (content script + message channel)
- Service worker polling infrastructure complete; Phase 07 can add DOM automation triggered by `lastActionItem`
- Enabled flag gate exists in storage; Phase 08 UI can wire the toggle immediately

---
*Phase: 06-extension-architecture-foundation*
*Completed: 2026-03-27*
