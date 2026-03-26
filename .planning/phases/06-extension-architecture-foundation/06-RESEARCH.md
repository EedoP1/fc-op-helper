# Phase 6: Extension Architecture Foundation - Research

**Researched:** 2026-03-27
**Domain:** Chrome Extension MV3, WXT Framework, TypeScript, Service Worker Lifecycle
**Confidence:** HIGH

## Summary

Phase 6 creates a greenfield TypeScript Chrome extension inside the existing Python monorepo. The extension uses WXT (v0.20.20) to scaffold a Manifest V3 project with a service worker background entrypoint and a content script entrypoint. No DOM automation, no overlay UI — just proving the communication backbone works.

The core challenge is MV3 service worker termination: Chrome kills idle workers after 30 seconds of inactivity. `chrome.alarms` (1-minute period per decision D-01) is the right tool — alarms persist across worker termination and wake the worker on fire. On each wake, the worker reads `chrome.storage.local` for the `enabled` flag and polls `http://localhost:8000/api/v1/actions/pending` if enabled. All state that must survive termination (enabled flag, last known action) lives in `chrome.storage.local`.

The PING/PONG message channel uses TypeScript discriminated unions so that the compiler enforces exhaustive switch matching. The content script injects on all EA Web App pages and uses WXT's `ctx.addEventListener(window, 'wxt:locationchange', ...)` event (not a raw MutationObserver) to detect SPA route changes and re-initialize listeners. A reconnect loop in the content script handles the case where the service worker was terminated and the port disconnects.

**Primary recommendation:** Scaffold the extension with `npx wxt@latest init` inside an `extension/` subdirectory. Use `defineBackground` + `defineContentScript` entrypoints. Use `storage.defineItem` for typed chrome.storage.local state. Use raw `chrome.runtime.sendMessage` / `chrome.tabs.sendMessage` with hand-written discriminated union types (no third-party messaging layer for Phase 6 PING/PONG).

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Use chrome.alarms with 1-minute interval for polling. No setTimeout workaround — simplicity over 30s responsiveness.
- **D-02:** On service worker wake (after termination), poll the backend immediately before resuming the 1-minute alarm cycle.
- **D-03:** Polling gated by an `enabled` flag in chrome.storage.local. Alarm fires but polling is skipped when disabled. Phase 8 UI will wire the toggle; the gate mechanism exists from Phase 6.
- **D-04:** Backend URL hardcoded to `http://localhost:8000`. No configurable setting — v1.1 is localhost-only per project constraints.
- **D-05:** Service worker <-> content script messages use TypeScript discriminated unions (`{ type: 'PING' } | { type: 'PONG' } | ...`). Compile-time exhaustive switch safety.
- **D-06:** Phase 6 defines only PING/PONG message types to prove the channel works. Future phases (7, 8) add EXECUTE_ACTION, ACTION_RESULT, STATUS_UPDATE types as needed.
- **D-07:** Content script injects on ALL EA Web App pages (broad match pattern). It's lightweight — just listens for messages. Phase 7/8 decide which pages to act on.
- **D-08:** MutationObserver on root DOM node detects SPA navigation. Re-initialize listeners when main content area swaps.
- **D-09:** Content script auto-reconnects to service worker on disconnection (e.g., extension update) with retry loop. No user action required — logs status to console.

### Claude's Discretion

- What exactly persists to chrome.storage.local beyond the enabled flag (last action cache, portfolio snapshot, etc.) — choose based on WXT best practices and what makes the system robust across worker termination
- WXT project structure and file organization
- TypeScript configuration and build setup
- Test approach for service worker and content script
- Error handling patterns within the message channel

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ARCH-01 | Chrome extension built with Manifest V3, service worker handles all backend communication | WXT `defineBackground` entrypoint generates MV3 `background.service_worker`. `host_permissions` allows localhost fetch. All fetch calls in background only — content scripts never call backend directly (confirmed CORS constraint). |
| ARCH-02 | Service worker uses chrome.alarms for polling and chrome.storage.local for state (survives worker termination) | `chrome.alarms.create` with `periodInMinutes: 1`. Check-and-recreate alarm on startup (alarm state survives termination). `storage.defineItem<boolean>('local:enabled')` for gating. Immediate poll on wake (D-02). |
| ARCH-03 | Typed message protocol between service worker and content script (discriminated unions) | Shared `src/messages.ts` defines `ExtensionMessage = { type: 'PING' } | { type: 'PONG' }`. `chrome.tabs.sendMessage` from background, `chrome.runtime.onMessage` in content script. Exhaustive switch enforced by TypeScript `never` check. |
| ARCH-04 | Content script uses MutationObserver for SPA navigation detection and listener re-initialization | WXT provides `ctx.addEventListener(window, 'wxt:locationchange', ...)` which fires on History API navigation. Decision D-08 specifies MutationObserver — both approaches valid; WXT's locationchange event is preferred as it avoids observer overhead, but a fallback MutationObserver on `document.body` watching `childList: true, subtree: false` is acceptable if locationchange doesn't fire reliably on EA SPA. |
</phase_requirements>

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| wxt | 0.20.20 | Extension build framework, manifest generation, HMR, entrypoints | Project decision; Plasmo has maintenance lag, CRXJS is archived |
| typescript | 6.0.2 | Static typing for discriminated union message protocol | Project constraint; required for compile-time exhaustive switches |
| @types/chrome | 0.1.38 | Chrome API type definitions | Required for all chrome.* API calls without `any` casts |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| vitest | 4.1.2 | Unit test runner | WXT has first-class Vitest integration via `WxtVitest()` plugin |
| @webext-core/fake-browser | bundled via WXT | In-memory chrome.* mock for tests | Used automatically by `WxtVitest()` — no manual chrome mock needed |

### Alternatives Considered

| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| chrome.alarms | setTimeout keepalive loop | Alarms survive termination; setTimeout does not. D-01 locks alarms. |
| Hand-written discriminated unions | webext-bridge / @webext-core/messaging | Third-party adds abstraction; Phase 6 only needs PING/PONG — overkill. Add in Phase 7/8 if complexity grows. |
| WXT `wxt:locationchange` event | Raw MutationObserver on document.body | WXT event fires on History API navigation; MutationObserver fires on DOM mutations. D-08 says MutationObserver — use both: locationchange as primary, MutationObserver as fallback. |

**Installation (inside `extension/` subdirectory):**

```bash
# From repo root — init WXT project in extension/ subdirectory
npx wxt@latest init extension
# Choose: TypeScript, no framework (vanilla), Chrome target
cd extension
npm install
```

**Version verification:**

```
wxt:      0.20.20  (verified 2026-03-27 via npm view wxt version)
typescript: 6.0.2  (verified 2026-03-27 via npm view typescript version)
@types/chrome: 0.1.38  (verified 2026-03-27 via npm view @types/chrome version)
vitest:   4.1.2    (verified 2026-03-27 via npm view vitest version)
```

---

## Architecture Patterns

### Recommended Project Structure

```
extension/                        # WXT project root (inside Python monorepo)
├── wxt.config.ts                 # Manifest, permissions, host_permissions
├── tsconfig.json                 # WXT auto-generates; extend if needed
├── package.json                  # Extension-specific deps (wxt, typescript)
├── vitest.config.ts              # WxtVitest() plugin
├── entrypoints/
│   ├── background.ts             # Service worker: alarms, polling, message relay
│   └── ea-webapp.content.ts      # Content script: PING handler, SPA re-init
└── src/
    └── messages.ts               # Shared discriminated union ExtensionMessage type
```

The Python source stays at `/src/`. The extension is a separate npm project at `/extension/`. They share nothing at runtime; they share the git repo.

### Pattern 1: Service Worker Background Entrypoint

**What:** `defineBackground` wraps the service worker lifecycle. `main()` runs on every wake. Must be idempotent.

**When to use:** All service worker code lives here.

**Example:**

```typescript
// entrypoints/background.ts
export default defineBackground({
  type: 'module',
  main() {
    // Ensure alarm exists (check-and-recreate pattern — alarm state survives termination but listener code does not)
    chrome.alarms.get('poll', (alarm) => {
      if (!alarm) {
        chrome.alarms.create('poll', { periodInMinutes: 1 });
      }
    });

    // On alarm fire: check enabled flag, poll backend
    chrome.alarms.onAlarm.addListener(async (alarm) => {
      if (alarm.name !== 'poll') return;
      await maybePoll();
    });

    // D-02: Poll immediately on wake (worker may have been terminated during a cycle)
    maybePoll();
  },
});

async function maybePoll(): Promise<void> {
  const enabled = await storage.getItem<boolean>('local:enabled');
  if (!enabled) return;
  try {
    const res = await fetch('http://localhost:8000/api/v1/actions/pending');
    if (!res.ok) return;
    const data = await res.json();
    if (data.action) {
      await storage.setItem('local:lastAction', data.action);
    }
  } catch (e) {
    console.error('[OP Seller] poll failed:', e);
  }
}
```

### Pattern 2: Typed Message Protocol (Discriminated Unions)

**What:** Shared type file defines all message shapes. Both sides import from it. TypeScript exhaustive switch catches missing cases at compile time.

**When to use:** Every message sent between service worker and content script.

**Example:**

```typescript
// src/messages.ts — Source: Chrome MV3 messaging docs + TypeScript handbook discriminated unions
export type ExtensionMessage =
  | { type: 'PING' }
  | { type: 'PONG' };

// Compile-time exhaustiveness helper
export function assertNever(x: never): never {
  throw new Error(`Unhandled message type: ${(x as any).type}`);
}
```

```typescript
// In content script — receiving PING, sending PONG
chrome.runtime.onMessage.addListener((msg: ExtensionMessage, _sender, sendResponse) => {
  switch (msg.type) {
    case 'PING':
      sendResponse({ type: 'PONG' } satisfies ExtensionMessage);
      return true;
    default:
      assertNever(msg);  // compile error if a type is unhandled
  }
});
```

```typescript
// In service worker — sending PING to active tab
async function pingActiveTab(tabId: number): Promise<ExtensionMessage | null> {
  try {
    return await chrome.tabs.sendMessage(tabId, { type: 'PING' } satisfies ExtensionMessage);
  } catch {
    return null;  // content script not ready yet
  }
}
```

### Pattern 3: Content Script SPA Re-initialization

**What:** On each SPA route change, tear down old listeners and re-initialize. WXT's `ctx` manages lifecycle and invalidation. Decision D-08 specifies MutationObserver — use WXT's locationchange event as the primary trigger and fall back to MutationObserver if EA Web App uses custom routing that doesn't trigger History API events.

**When to use:** Any listener that must survive EA Web App navigation.

**Example:**

```typescript
// entrypoints/ea-webapp.content.ts
export default defineContentScript({
  matches: ['https://www.ea.com/ea-sports-fc/ultimate-team/web-app/*'],
  runAt: 'document_idle',
  main(ctx) {
    function initListeners() {
      chrome.runtime.onMessage.addListener(handleMessage);
    }

    function teardownListeners() {
      chrome.runtime.onMessage.removeListener(handleMessage);
    }

    // Primary: WXT's locationchange fires on History API navigation (SPA routing)
    ctx.addEventListener(window, 'wxt:locationchange', () => {
      teardownListeners();
      initListeners();
      console.log('[OP Seller CS] Re-initialized after navigation to', location.href);
    });

    // Fallback: MutationObserver on document.body for custom routing (D-08)
    const observer = new MutationObserver(() => {
      // Only react to significant DOM swaps, not fine-grained mutations
      // EA SPA swaps a top-level container — observe shallow
    });
    observer.observe(document.body, { childList: true, subtree: false });
    ctx.onInvalidated(() => observer.disconnect());

    // Initial setup
    initListeners();
    handleReconnect(ctx);  // D-09: auto-reconnect loop
  },
});
```

### Pattern 4: Content Script Auto-Reconnect (D-09)

**What:** When the extension updates or service worker restarts, the `chrome.runtime.id` becomes invalid. Detect this and retry connection.

**Example:**

```typescript
// D-09: Reconnect loop — retries until ctx is invalidated
function handleReconnect(ctx: ContentScriptContext) {
  function tryPing() {
    if (ctx.isInvalid) return;
    chrome.runtime.sendMessage({ type: 'PING' } satisfies ExtensionMessage)
      .then(() => {
        console.log('[OP Seller CS] Connected to service worker');
      })
      .catch(() => {
        // Service worker not ready — retry after 2s
        ctx.setTimeout(tryPing, 2000);
      });
  }
  tryPing();
}
```

### Pattern 5: Storage Items for Persistent State

**What:** Named storage items with types survive service worker termination. Everything the worker needs on wake must be in storage.

**Example:**

```typescript
// src/storage.ts — define all storage items centrally
import { storage } from 'wxt/utils/storage';

// Polling enabled/disabled gate (D-03)
export const enabledItem = storage.defineItem<boolean>('local:enabled', {
  fallback: false,
});

// Last known action (survives worker termination)
export const lastActionItem = storage.defineItem<PendingAction | null>('local:lastAction', {
  fallback: null,
});

type PendingAction = {
  id: number;
  ea_id: number;
  action_type: 'BUY' | 'LIST' | 'RELIST';
  target_price: number;
  player_name: string;
};
```

### Anti-Patterns to Avoid

- **setTimeout for polling:** Does not survive worker termination. Alarms are the only safe periodic mechanism in MV3.
- **Global variables for worker state:** Worker restarts with fresh memory. All state must be in chrome.storage.local.
- **Content script fetching backend directly:** Chrome CORS blocks cross-origin requests from content scripts to `http://localhost:8000`. All fetch calls must go through the service worker. This is a locked project constraint.
- **`persistent: true` in defineBackground:** Only valid for MV2 Firefox. Has no effect in MV3 Chrome and should not be set.
- **Placing runtime code outside `main()`:** WXT imports background/content files in Node.js during build. Any top-level code runs at build time and breaks the build. All runtime code goes inside the `main()` function.
- **`chrome.runtime.onMessage` returning a Promise directly in Chrome < 146:** Chrome < 116 does not support returning a Promise from `onMessage`. Use `return true` + `sendResponse()` for synchronous safety. Check minimum Chrome version in manifest.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Storage type safety | Custom storage wrapper | `storage.defineItem<T>()` from `wxt/utils/storage` | Built-in, handles migrations, watch callbacks, fallback values |
| Alarm check-and-recreate | Custom persistence logic | `chrome.alarms.get` + `create` pattern | Chrome guarantees alarm names are unique — check-and-recreate is the official pattern |
| Extension context invalidation | Manual `try/catch` on every API call | WXT `ContentScriptContext` (`ctx.isInvalid`, `ctx.setTimeout`, etc.) | Wraps all async ops to silently cancel on invalidation |
| Test mocking of chrome.* APIs | Manual jest/vitest mocks | `WxtVitest()` plugin + `fakeBrowser` from `@webext-core/fake-browser` | In-memory implementation of full Chrome API — no manual mock per test |
| SPA navigation detection | `history.pushState` monkey-patching | `ctx.addEventListener(window, 'wxt:locationchange', ...)` | WXT wraps History API and fires the event reliably |

**Key insight:** The extension lifecycle (service worker termination, context invalidation, storage migration) has many edge cases. WXT's abstractions handle the majority — fight the framework only where project constraints require it.

---

## Common Pitfalls

### Pitfall 1: Alarm Listener Lost on Worker Termination

**What goes wrong:** Service worker is terminated. Alarm fires. Worker wakes. But `onAlarm.addListener` is not re-registered because no one calls `main()` again — wrong. Chrome DOES re-execute the service worker module on alarm wake, so `main()` runs again.

**Why it happens:** Confusion about whether the service worker re-runs from the top. It does. The `main()` function runs on every wake. `chrome.alarms.onAlarm.addListener` must be called on every wake to receive the current alarm event.

**How to avoid:** Always register `onAlarm.addListener` inside `main()`. Do not assume listeners persist across termination — they do not.

**Warning signs:** Alarm fires (visible in chrome://extensions DevTools) but poll never executes.

### Pitfall 2: Service Worker Terminated During a Fetch

**What goes wrong:** Worker starts a fetch, browser terminates it at the 30-second inactivity mark (different from the fetch itself), request is dropped.

**Why it happens:** The 30-second idle timer is reset by extension API calls, not by in-flight fetch promises.

**How to avoid:** Fetch to `localhost:8000` is fast (< 1s). This is not a real risk for the polling pattern. For long operations, use `chrome.alarms` rather than long-running async chains.

**Warning signs:** Intermittent missed polls with no error log.

### Pitfall 3: Content Script "Extension context invalidated" Errors

**What goes wrong:** Extension is updated while a tab is open. Content script continues running but calling `chrome.runtime.sendMessage` throws "Extension context invalidated".

**Why it happens:** After extension update, the old content script's runtime context is revoked. Chrome does not kill the old script automatically.

**How to avoid:** Wrap all `chrome.runtime.*` calls in try/catch. Use the reconnect loop (D-09) which detects the failure and stops retrying when `ctx.isInvalid`. Log to console only — no user-visible error needed at Phase 6.

**Warning signs:** Console errors "Extension context invalidated" in the EA Web App tab after extension reload.

### Pitfall 4: `sendMessage` Returns `undefined` When No Listener Responds

**What goes wrong:** Service worker calls `chrome.tabs.sendMessage(tabId, { type: 'PING' })` but the content script is not injected yet (navigating, not matched, etc.). The call rejects with "Could not establish connection".

**Why it happens:** `sendMessage` throws (not returns null) when no listener is registered on the other end.

**How to avoid:** Wrap all `tabs.sendMessage` calls in try/catch. Return null on error — this is expected behavior when the EA Web App tab is not active.

**Warning signs:** Unhandled promise rejections in the service worker console.

### Pitfall 5: `host_permissions` Required for `localhost` Fetch

**What goes wrong:** Service worker fetch to `http://localhost:8000` throws a CORS/network error even though CORS middleware is configured on the backend.

**Why it happens:** MV3 service workers require `host_permissions` to include the target origin before any `fetch()` call is permitted.

**How to avoid:** Add `"http://localhost:8000/*"` to `manifest.host_permissions` in `wxt.config.ts`.

**Warning signs:** `Failed to fetch` error in service worker with no backend log entry (request never reaches the server).

### Pitfall 6: Minimum Alarm Period is 30 Seconds (Not 1 Minute)

**What goes wrong:** Developer attempts `periodInMinutes: 0.25` (15 seconds) and Chrome silently rounds up.

**Why it happens:** Chrome 120+ enforces a minimum of 30 seconds (0.5 minutes) for production extensions. Dev mode has no limit.

**How to avoid:** D-01 uses 1-minute intervals — this is safely above the minimum. No issue in production.

**Warning signs:** Alarm fires less frequently than expected in production builds.

---

## Code Examples

### wxt.config.ts (Complete)

```typescript
// Source: WXT official docs — wxt.dev/guide/essentials/config/manifest
import { defineConfig } from 'wxt';

export default defineConfig({
  manifest: {
    name: 'OP Seller',
    description: 'FC26 OP sell automation assistant',
    version: '0.1.0',
    permissions: ['alarms', 'storage', 'tabs'],
    host_permissions: [
      'http://localhost:8000/*',        // backend API
      'https://www.ea.com/*',           // EA Web App content script
    ],
  },
});
```

### Background Service Worker (Skeleton)

```typescript
// entrypoints/background.ts
// Source: WXT entrypoints docs + Chrome alarms docs (developer.chrome.com/docs/extensions/reference/api/alarms)
export default defineBackground({
  type: 'module',
  main() {
    // Register alarm (idempotent — check first)
    chrome.alarms.get('poll', (alarm) => {
      if (!alarm) {
        chrome.alarms.create('poll', { periodInMinutes: 1 });
      }
    });

    chrome.alarms.onAlarm.addListener(async (alarm) => {
      if (alarm.name === 'poll') {
        await maybePoll();
      }
    });

    // D-02: Immediate poll on wake
    maybePoll();
  },
});
```

### Content Script with SPA Re-init (Skeleton)

```typescript
// entrypoints/ea-webapp.content.ts
// Source: WXT content script docs (wxt.dev/guide/essentials/content-scripts)
export default defineContentScript({
  matches: ['https://www.ea.com/ea-sports-fc/ultimate-team/web-app/*'],
  runAt: 'document_idle',
  main(ctx) {
    const handleMessage = (
      msg: ExtensionMessage,
      _sender: chrome.runtime.MessageSender,
      sendResponse: (r: ExtensionMessage) => void,
    ) => {
      switch (msg.type) {
        case 'PING':
          sendResponse({ type: 'PONG' });
          return true;
        default:
          assertNever(msg);
      }
    };

    // WXT locationchange = SPA navigation via History API (D-04/D-08)
    ctx.addEventListener(window, 'wxt:locationchange', () => {
      chrome.runtime.onMessage.removeListener(handleMessage);
      chrome.runtime.onMessage.addListener(handleMessage);
      console.log('[OP Seller CS] Re-init after nav');
    });

    chrome.runtime.onMessage.addListener(handleMessage);
    console.log('[OP Seller CS] Initialized');
  },
});
```

### Vitest Config

```typescript
// vitest.config.ts
// Source: WXT unit testing docs (wxt.dev/guide/essentials/unit-testing)
import { defineConfig } from 'vitest/config';
import { WxtVitest } from 'wxt/testing/vitest-plugin';

export default defineConfig({
  plugins: [WxtVitest()],
  test: {
    environment: 'jsdom',
  },
});
```

### Test Example: Alarm Registration

```typescript
// tests/background.test.ts
import { describe, it, expect, beforeEach } from 'vitest';
import { fakeBrowser } from 'wxt/testing/fake-browser';

describe('poll alarm', () => {
  beforeEach(() => fakeBrowser.reset());

  it('creates poll alarm on startup if not present', async () => {
    // Import triggers main() via the entrypoint
    await import('../entrypoints/background');
    const alarm = await fakeBrowser.alarms.get('poll');
    expect(alarm).toBeDefined();
    expect(alarm?.periodInMinutes).toBe(1);
  });
});
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| MV2 persistent background page | MV3 event-driven service worker | Chrome 112 (MV2 deprecated) | Worker terminates when idle; all state must be external |
| 30-second minimum alarms | 30-second minimum (0.5 min) | Chrome 120 | D-01 uses 1 minute — safely above minimum |
| CRXJS for build | WXT | 2024 (CRXJS archived) | WXT is now the clear standard |
| Plasmo for build | WXT | 2024 (Plasmo maintenance lag) | Project decision locked |
| Manual chrome.* mocking in tests | WxtVitest + fake-browser | WXT 0.16+ | Zero manual mocking for storage/alarms |

**Deprecated/outdated:**

- `background.persistent: true`: MV2-only. No effect in MV3 Chrome.
- `chrome.extension.sendRequest`: Removed in Chrome 33. Use `chrome.runtime.sendMessage`.
- `setTimeout` for periodic work in service workers: Will be killed before firing if worker is terminated.

---

## Open Questions

1. **Does `wxt:locationchange` fire reliably on EA Web App SPA navigation?**
   - What we know: WXT fires it on History API (`pushState`/`replaceState`) navigation.
   - What's unclear: EA Web App may use a custom SPA router that does not use the standard History API, or may use hash routing.
   - Recommendation: Implement `wxt:locationchange` as primary. Add a shallow MutationObserver on `document.body` (D-08) as fallback. Phase 7 verifies behavior by live DevTools inspection.

2. **EA Web App exact match pattern**
   - What we know: `https://www.ea.com/ea-sports-fc/ultimate-team/web-app/*` is the expected URL pattern.
   - What's unclear: Whether EA uses URL fragments (`#`) or sub-paths that need a broader match.
   - Recommendation: Use `https://www.ea.com/ea-sports-fc/ultimate-team/web-app/*` as the content script match. Phase 7 verifies by loading the Web App in Chrome with the extension installed.

3. **chrome.storage.local beyond `enabled` flag**
   - What we know: `enabled` flag is required (D-03). D-02 requires the worker to know what to do immediately on wake.
   - What's unclear: Should the last fetched action be cached in storage so Phase 7 DOM automation can access it?
   - Recommendation: Add `local:lastAction` (nullable `PendingAction | null`) to storage definitions in Phase 6 even if unused until Phase 7. Costs nothing; avoids a storage migration later.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Node.js | WXT build, npm scripts | Yes | v24.14.0 | — |
| npm | Package installation | Yes | 11.9.0 | — |
| Chrome browser | Loading unpacked extension, testing | Assumed present | Unknown | — |
| Python backend (localhost:8000) | Service worker polling | Assumed running during integration test | — | Test with backend mocked in unit tests |

**Missing dependencies with no fallback:**

- Chrome browser for end-to-end testing — assumed present on developer machine; cannot be verified programmatically.

**Missing dependencies with fallback:**

- Backend server: not needed for unit tests (service worker polls `localhost:8000` but `maybePoll()` can be tested with `fetch` mocked via `msw` or direct vitest mock).

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | Vitest 4.1.2 |
| Config file | `extension/vitest.config.ts` (Wave 0 — does not exist yet) |
| Quick run command | `cd extension && npm run test -- --run` |
| Full suite command | `cd extension && npm run test` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ARCH-01 | MV3 manifest generated with `background.service_worker` key | smoke | `cd extension && npm run build && node -e "const m=require('.output/chrome-mv3/manifest.json');if(!m.background?.service_worker)throw new Error('no SW')"` | Wave 0 (build artifact check) |
| ARCH-02 | `poll` alarm created on startup with 1-minute period | unit | `cd extension && npm run test -- --run tests/background.test.ts` | Wave 0 |
| ARCH-02 | Immediate poll fires on wake when enabled=true | unit | same file | Wave 0 |
| ARCH-02 | Poll skipped when enabled=false | unit | same file | Wave 0 |
| ARCH-03 | PONG response returned for PING | unit | `cd extension && npm run test -- --run tests/content.test.ts` | Wave 0 |
| ARCH-03 | TypeScript compile error when message type unhandled | compile | `cd extension && npx tsc --noEmit` | Inherent to build |
| ARCH-04 | Re-init fires after `wxt:locationchange` event | unit | `cd extension && npm run test -- --run tests/content.test.ts` | Wave 0 |

### Sampling Rate

- **Per task commit:** `cd extension && npx tsc --noEmit`
- **Per wave merge:** `cd extension && npm run test -- --run`
- **Phase gate:** Full suite green + `npm run build` succeeds before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `extension/vitest.config.ts` — WxtVitest plugin config
- [ ] `extension/tests/background.test.ts` — covers ARCH-02
- [ ] `extension/tests/content.test.ts` — covers ARCH-03, ARCH-04
- [ ] Framework install: `cd extension && npm install` — after WXT init

---

## Sources

### Primary (HIGH confidence)

- WXT official docs (wxt.dev) — entrypoints, storage, unit testing, messaging
- Chrome for Developers — [Service worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle)
- Chrome for Developers — [chrome.alarms API](https://developer.chrome.com/docs/extensions/reference/api/alarms)
- Chrome for Developers — [Extension messaging](https://developer.chrome.com/docs/extensions/develop/concepts/messaging)
- npm registry — `wxt@0.20.20`, `typescript@6.0.2`, `@types/chrome@0.1.38`, `vitest@4.1.2` (all verified 2026-03-27)

### Secondary (MEDIUM confidence)

- [WXT GitHub — examples/dynamic-content-scripts](https://github.com/wxt-dev/examples/blob/main/examples/dynamic-content-scripts/wxt.config.ts) — manifest config patterns
- [The 2025 State of Browser Extension Frameworks](https://redreamality.com/blog/the-2025-state-of-browser-extension-frameworks-a-comparative-analysis-of-plasmo-wxt-and-crxjs/) — WXT vs Plasmo vs CRXJS comparison confirming WXT as standard
- [vitest-chrome](https://github.com/probil/vitest-chrome) — Alternative Chrome mock (not needed given WxtVitest)

### Tertiary (LOW confidence)

- Community discussion threads on chromium-extensions Google Groups — service worker termination edge cases (single source, not official docs)

---

## Metadata

**Confidence breakdown:**

- Standard stack: HIGH — WXT version verified via npm; Chrome API docs are official
- Architecture: HIGH — All patterns sourced from official WXT and Chrome docs
- Pitfalls: MEDIUM — Alarm/fetch interaction pitfalls sourced from official docs; content script invalidation patterns confirmed by WXT docs but EA-specific behavior not tested
- Test approach: HIGH — WxtVitest documented officially; vitest version verified

**Research date:** 2026-03-27
**Valid until:** 2026-04-27 (WXT releases frequently — re-verify version before install if > 2 weeks)
