# Phase 7: DOM Automation Layer - Research

**Researched:** 2026-03-27
**Domain:** Chrome Extension DOM Automation / EA Web App Interaction / TypeScript MV3
**Confidence:** MEDIUM (architecture patterns HIGH; EA DOM selectors LOW — must verify in DevTools)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

- **D-01:** Full autonomous navigation — content script drives sidebar menus and page transitions to reach the correct EA Web App page for each action type. No assumption about which page the user is on.
- **D-02:** Search by player name + card version + position + price range. PendingAction must carry additional fields: card_version and position (backend must send these).
- **D-03:** Adaptive price sweep to find cheapest listing — start with known market price as max BIN. No results → increase max BIN. Has results → decrease until the cheapest listing on the market is isolated.
- **D-04:** Price guard: only execute Buy Now if the cheapest BIN is at or below `target_price`. If too expensive, skip the action and move on.
- **D-05:** Navigate to unassigned items (or club) → find the purchased card → list on Transfer Market at the OP sell price (`target_price` for LIST actions).
- **D-06:** Navigate to Transfer List → click EA's built-in "Relist All" button. Relists everything, not just portfolio cards — acceptable.
- **D-07:** Add EXECUTE_ACTION and ACTION_RESULT message types to the discriminated union (extending Phase 6 D-06). Service worker sends EXECUTE_ACTION to content script with action data; content script replies with ACTION_RESULT containing outcome or error.
- **D-08:** Backend PendingAction response and extension `PendingAction` type must include: `card_version` (e.g., Gold, TOTW, Trailblazers) and `position` (e.g., ST, CM) in addition to existing fields.
- **D-09:** Detect CAPTCHA via MutationObserver watching for the CAPTCHA container element in the DOM. When detected, stop automation entirely — full stop, no further actions.
- **D-10:** When an expected DOM element is not found: wait a few seconds for it to appear (tolerates slow page loads), retry once, then fail loudly with the selector name.
- **D-11:** On any stop (CAPTCHA or DOM failure): content script sends failure message to service worker, which reports to backend to reset the action immediately. Do not rely on the 5-minute stale timeout.
- **D-12:** CAPTCHA = full automation stop. DOM failure on a single action = skip that action, report failure, poll for the next one, keep going.
- **D-13:** All DOM interactions use randomized jitter (800-2500ms per AUTO-05). No two consecutive action intervals are identical.
- **D-14:** All selectors centralized in one file (per AUTO-08).

### Claude's Discretion

- Exact selector discovery strategy (data attributes, ARIA roles, DOM structure) — EA Web App uses obfuscated classes, must verify with live DevTools
- Timing variation strategy beyond the 800-2500ms requirement (per-action-type delays, session pacing, cool-down between cycles)
- How to identify the correct card in unassigned/club for LIST actions
- Selector file internal organization
- Browser notification on CAPTCHA detection (in addition to stopping)
- Price sweep step size and iteration limit
- How the content script reports progress/status during multi-step action execution

### Deferred Ideas (OUT OF SCOPE)

None — discussion stayed within phase scope.
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| AUTO-01 | Extension searches transfer market for target player and executes Buy Now when BIN is at or below expected buy price | BUY flow: navigation → search → price sweep → Buy Now conditional on price guard |
| AUTO-02 | Extension skips player if current BIN exceeds backend buy price (price guard) | D-04 price guard pattern; skip + ACTION_RESULT with outcome "skipped" |
| AUTO-03 | Extension auto-lists purchased cards at the locked OP price from the portfolio | LIST flow: unassigned items → find card → set price → list |
| AUTO-04 | Extension auto-relists expired cards at the same locked OP price (price does not change) | RELIST flow: Transfer List → "Relist All" button click |
| AUTO-05 | All DOM interactions use human-like delays with randomized jitter (800-2500ms) | `randomDelay(min, max)` utility; no two intervals identical |
| AUTO-06 | Extension detects CAPTCHA and stops automation immediately, alerting the user | MutationObserver watching CAPTCHA container; sends CAPTCHA_DETECTED message; service worker reports to backend; notifies user |
| AUTO-07 | Extension fails loudly on DOM mismatch (missing elements) rather than silently continuing | `waitForElement(selector, timeout)` with retry-once then `throw new Error(\`Element not found: \${selector}\`)` |
| AUTO-08 | All selectors centralized in one file for maintainability against EA Web App updates | `extension/src/selectors.ts` — single export object, grouped by page/action |
</phase_requirements>

---

## Summary

Phase 7 implements the full buy/list/relist automation loop by extending the Phase 6 foundation. The service worker gains an EXECUTE_ACTION → ACTION_RESULT request/response cycle, and the content script gains three automation flows (BUY, LIST, RELIST) that drive the EA Web App DOM. The architecture is well-understood and patterns from Phase 6 apply directly; the primary uncertainty is the exact DOM selectors because the EA Web App uses obfuscated class names.

The EA Web App is a JavaScript SPA hosted at `https://www.ea.com/ea-sports-fc/ultimate-team/web-app/`. DOM automation must use querySelector against structural and ARIA attributes rather than CSS class names, since class names are obfuscated and change between versions. Community-built automation tools (FutSniperExtension, FC26 Enhancer, Futinator, NOKA FUT, EasyFUT) confirm that DOM automation of the EA Web App is feasible from a Chrome extension content script, but none publicly document their exact selectors — live DevTools inspection is the only reliable selector source.

**Primary recommendation:** Open Phase 7 with a dedicated Wave 0 exploration task: load the EA Web App in DevTools with no automation running, navigate through each flow manually, and capture working selectors into `selectors.ts` before writing any automation logic. All other waves depend on these verified selectors.

---

## Standard Stack

### Core

| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| TypeScript | ^6.0.2 (installed) | Type safety across all new files | Already in project; discriminated unions for message types |
| WXT | ^0.20.20 (installed) | Extension build + `defineContentScript`, `defineBackground`, `fakeBrowser` | Already chosen in Phase 6; WxtVitest plugin in place |
| Vitest | ^4.1.2 (installed) | Unit tests for automation logic | Already configured with jsdom environment |
| jsdom | ^29.0.1 (installed) | DOM simulation in tests | Already installed |

### Supporting

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| chrome.notifications | MV3 built-in | Browser notification on CAPTCHA stop | Already in `wxt.config.ts` permissions? No — needs adding |
| wxt/utils/storage | WXT built-in | Store `automationEnabled` flag, `lastAction` | Already used via `enabledItem`, `lastActionItem` in storage.ts |

**No new npm packages required.** Everything needed is already installed or is a Chrome built-in API.

**Verify notifications permission is declared** — current `wxt.config.ts` only lists `['alarms', 'storage', 'tabs']`. Must add `'notifications'` for CAPTCHA browser alerts.

---

## Architecture Patterns

### Recommended Project Structure

```
extension/
├── entrypoints/
│   ├── background.ts           — extend: send EXECUTE_ACTION, receive ACTION_RESULT, call /complete
│   └── ea-webapp.content.ts    — extend: handle EXECUTE_ACTION, run automation flows, send ACTION_RESULT
├── src/
│   ├── messages.ts             — extend: add EXECUTE_ACTION, ACTION_RESULT, CAPTCHA_DETECTED variants
│   ├── storage.ts              — extend: PendingAction type (add card_version, position)
│   ├── selectors.ts            — NEW: all EA Web App selectors, grouped by page
│   └── automation/
│       ├── dom-utils.ts        — NEW: waitForElement, randomDelay, clickElement
│       ├── buy-flow.ts         — NEW: executeBuy(action) → AutomationResult
│       ├── list-flow.ts        — NEW: executeList(action) → AutomationResult
│       └── relist-flow.ts      — NEW: executeRelist() → AutomationResult
└── tests/
    ├── background.test.ts      — extend: test EXECUTE_ACTION dispatch, ACTION_RESULT handling
    ├── content.test.ts         — extend: test new message cases, automation dispatch
    ├── automation/
    │   ├── buy-flow.test.ts    — NEW
    │   ├── list-flow.test.ts   — NEW
    │   └── relist-flow.test.ts — NEW
    └── dom-utils.test.ts       — NEW
```

### Pattern 1: Message Protocol Extension (D-07)

**What:** Extend the existing discriminated union with EXECUTE_ACTION and ACTION_RESULT. Service worker drives; content script executes and replies.

**When to use:** Always for service worker → content script command/response.

```typescript
// extension/src/messages.ts
export type ExtensionMessage =
  | { type: 'PING' }
  | { type: 'PONG' }
  | { type: 'EXECUTE_ACTION'; action: PendingAction }
  | { type: 'ACTION_RESULT'; actionId: number; outcome: ActionOutcome }
  | { type: 'CAPTCHA_DETECTED' };

export type ActionOutcome =
  | { status: 'success'; price: number; result: 'bought' | 'listed' | 'relisted' }
  | { status: 'skipped'; reason: 'price_above_guard' }
  | { status: 'error'; selector: string; message: string };
```

The `assertNever` compile-time exhaustiveness guard already exists — adding new variants to the union will immediately surface unhandled cases in both the service worker and content script switch statements. This is the key safety net from Phase 6.

### Pattern 2: Service Worker Dispatch (extending maybePoll)

**What:** After storing a pending action, instead of just pinging the tab, send EXECUTE_ACTION and await ACTION_RESULT.

```typescript
// In background.ts maybePoll() — after lastActionItem.setValue(data.action):
const result = await chrome.tabs.sendMessage(tabId, {
  type: 'EXECUTE_ACTION',
  action: data.action,
} satisfies ExtensionMessage);

if (result?.type === 'ACTION_RESULT') {
  await reportOutcome(result);
}
```

**Critical:** `chrome.tabs.sendMessage` rejects if no content script is listening. Wrap in try/catch, same as existing `pingActiveTab()`. The service worker must not throw — errors are silent to the user.

### Pattern 3: Content Script Handler Dispatch

**What:** The content script `handleMessage` switch receives EXECUTE_ACTION, runs the appropriate flow, and returns ACTION_RESULT via `sendResponse`.

```typescript
case 'EXECUTE_ACTION': {
  // Must return true to signal async response
  runAutomation(msg.action, sendResponse);
  return true;
}
```

`runAutomation` is async; it cannot be awaited directly inside the synchronous `handleMessage`. Call it without await and pass `sendResponse` as callback, or use a Promise chain. The handler must `return true` to hold the message channel open.

### Pattern 4: DOM Utility Layer

**What:** All DOM interactions go through thin utility functions that handle waiting, jitter, and loud failure. Never call `document.querySelector` directly in flow files.

```typescript
// extension/src/automation/dom-utils.ts

/** Random delay between min and max ms. No two consecutive calls produce the same value. */
export async function randomDelay(minMs = 800, maxMs = 2500): Promise<void> {
  const delay = Math.floor(Math.random() * (maxMs - minMs + 1)) + minMs;
  return new Promise(resolve => setTimeout(resolve, delay));
}

/**
 * Wait for selector to appear in DOM, polling at 200ms intervals.
 * Retries for up to timeoutMs before throwing with selector name in message.
 * Implements D-10: wait a few seconds, retry once, fail loudly.
 */
export async function waitForElement(
  selector: string,
  timeoutMs = 5000,
): Promise<Element> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const el = document.querySelector(selector);
    if (el) return el;
    await new Promise(r => setTimeout(r, 200));
  }
  throw new Error(`Element not found: ${selector}`);
}

/** Click an element with a randomized delay before the click (human pacing). */
export async function clickElement(selector: string): Promise<void> {
  const el = await waitForElement(selector);
  await randomDelay();
  (el as HTMLElement).click();
}
```

### Pattern 5: Selector Centralization (D-14)

**What:** Single file exports a typed object with all selectors grouped by page/action context.

```typescript
// extension/src/selectors.ts
// ALL values are placeholder strings — must be replaced with verified selectors
// from live DevTools inspection before automation code is written (see Wave 0 task).

export const SELECTORS = {
  // Transfer Market — Search page
  transferMarket: {
    navButton:        '[data-nav-id="transfer-market"]',   // LOW confidence — placeholder
    playerNameInput:  'input[placeholder*="Search"]',       // LOW confidence — placeholder
    positionDropdown: 'select.ut-drop-down-control',        // LOW confidence — placeholder
    rarityDropdown:   'select.ut-rarity-dropdown',          // LOW confidence — placeholder
    maxBinInput:      'input[data-type="max-price"]',        // LOW confidence — placeholder
    searchButton:     'button.call-to-action',              // LOW confidence — placeholder
  },
  // Transfer Market — Results page
  searchResults: {
    listingRow:       'li.listFUTItem',                     // LOW confidence — placeholder
    binPrice:         '.bid-details .value',                // LOW confidence — placeholder
    buyNowButton:     'button.buyButton',                   // LOW confidence — placeholder
    confirmButton:    'button.ea-dialog-ui-but-approve',    // LOW confidence — placeholder
    noResultsText:    '.empty-message',                     // LOW confidence — placeholder
  },
  // Unassigned Items / My Club
  unassigned: {
    navButton:        '[data-nav-id="club-house"]',         // LOW confidence — placeholder
    unassignedTab:    '[data-tab="unassigned"]',            // LOW confidence — placeholder
    playerCard:       'li.ut-item-has-known-count',         // LOW confidence — placeholder
    sendToMarketButton: 'button.ut-quick-list-btn',         // LOW confidence — placeholder
  },
  // Listing overlay
  listing: {
    startingPriceInput: 'input[data-type="start-price"]',   // LOW confidence — placeholder
    buyNowPriceInput:   'input[data-type="buy-now-price"]', // LOW confidence — placeholder
    listButton:         'button.call-to-action',            // LOW confidence — placeholder
  },
  // Transfer List page
  transferList: {
    navButton:        '[data-nav-id="transfer-list"]',      // LOW confidence — placeholder
    relistAllButton:  'button.ut-transfer-list-btn',        // LOW confidence — placeholder
  },
  // CAPTCHA
  captcha: {
    container: '#captcha-container',                        // LOW confidence — placeholder
  },
} as const;
```

**CRITICAL:** Every selector above is a placeholder. The Wave 0 exploration task must replace all values with verified selectors from DevTools inspection before any automation flow is written.

### Pattern 6: CAPTCHA Detection via MutationObserver (D-09)

**What:** Attach a persistent MutationObserver to `document.body` watching for the CAPTCHA container. If it appears, dispatch a stop signal via the message channel.

```typescript
// In content script main() after initListeners()
const captchaObserver = new MutationObserver(() => {
  const captchaEl = document.querySelector(SELECTORS.captcha.container);
  if (captchaEl) {
    captchaObserver.disconnect();
    chrome.runtime.sendMessage({ type: 'CAPTCHA_DETECTED' } satisfies ExtensionMessage);
  }
});
captchaObserver.observe(document.body, { childList: true, subtree: true });
ctx.onInvalidated(() => captchaObserver.disconnect());
```

The service worker handles `CAPTCHA_DETECTED` by: (1) disabling the enabled flag, (2) calling POST /actions/{id}/complete with outcome "error", (3) calling `chrome.notifications.create()` to alert the user.

### Pattern 7: Failure Escalation (D-11, D-12)

**What:** Two tiers of failure. DOM failure on a single action: catch the error in the flow, send ACTION_RESULT with status "error", let the service worker call /complete with a failure outcome, then poll for next action. CAPTCHA: full stop — disable `enabledItem`, notify user, do NOT poll for next action.

```typescript
// In service worker handling ACTION_RESULT:
if (result.outcome.status === 'error') {
  await reportActionError(action.id, result.outcome.selector);
  // enabledItem stays true — automation continues with next action
}

// In service worker handling CAPTCHA_DETECTED:
await enabledItem.setValue(false);  // Full stop
await reportActionError(currentActionId, 'CAPTCHA detected');
chrome.notifications.create('captcha-alert', {
  type: 'basic',
  title: 'OP Seller — Automation Stopped',
  message: 'CAPTCHA detected. Manual intervention required.',
  iconUrl: '/icon/128.png',
});
```

### Pattern 8: PendingAction Schema Extension (D-08)

**What:** Add `card_version` and `position` to `PendingAction` type in `storage.ts` and to the backend `_claim_action()` response. Both `PortfolioSlot` DB model and `TradeAction` DB model currently lack these fields — they must be added.

**Backend changes needed:**
1. `PortfolioSlot` in `models_db.py` — add `card_version: str` and `position: str` columns
2. `SlotEntry` pydantic model in `actions.py` — add `card_version` and `position` fields
3. `_claim_action()` in `actions.py` — include `card_version` and `position` in returned dict
4. `TradeAction` DB model — add `card_version` and `position` columns (needed to preserve them when action is created from slot)

**Extension changes needed:**
1. `PendingAction` type in `storage.ts` — add `card_version: string` and `position: string`
2. Existing `MOCK_ACTION` in `background.test.ts` — add fields to avoid TS errors

### Anti-Patterns to Avoid

- **Hardcoding CSS class names:** EA Web App uses obfuscated/minified class names that change each FC release. Use ARIA attributes, data attributes, or structural selectors.
- **Fire-and-forget DOM clicks:** Always await the action result before proceeding. The EA Web App has confirmation dialogs that must be dismissed.
- **Single-step element polling:** `querySelector` returning null may mean the page is still navigating. Always use `waitForElement` with a timeout.
- **Blocking `handleMessage` with async work:** The Chrome message handler is synchronous. Must `return true` and pass `sendResponse` as a callback, not inline await.
- **Global automation state in content script variables:** Content script may be re-initialized on SPA navigation. Automation state that must survive navigation should live in `chrome.storage.local`.
- **Silent catch blocks:** Never `catch (e) {}` in automation flows. Always propagate with the selector name for loud failure (AUTO-07).

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| DOM polling / waiting | Custom polling loop in each flow | `waitForElement(selector, timeout)` utility | Centralizes timeout logic; consistent loud failure |
| Randomized delays | Inline `Math.random()` scattered across flows | `randomDelay(min, max)` utility | Ensures consistent timing contract; easy to tune |
| Selector inventory | Hardcoded strings scattered across flow files | `selectors.ts` centralized object | AUTO-08 requirement; single update point when EA updates DOM |
| Message type exhaustiveness | Manual if/else chains | `assertNever` (already exists in messages.ts) | Compile-time safety on new message variants |
| Storage API | Direct `chrome.storage.local.get/set` | `wxt/utils/storage` typed items | Already used; type safety + WXT testing compatibility |
| Browser notification | Direct DOM injection for CAPTCHA alert | `chrome.notifications.create()` | Persists even when tab is backgrounded; no DOM injection needed |

**Key insight:** The Phase 6 patterns (discriminated unions, assertNever, WXT storage items, fakeBrowser tests) are the foundation for Phase 7. Extend, don't replace.

---

## Common Pitfalls

### Pitfall 1: `sendMessage` Returns Undefined When Content Script Returns `false`

**What goes wrong:** Service worker calls `chrome.tabs.sendMessage(tabId, msg)` and the promise resolves to `undefined` instead of the expected ACTION_RESULT object. No error is thrown.

**Why it happens:** Chrome's `sendMessage` resolves to `undefined` (not null) when the message handler returns `false` or `undefined`. If the new `EXECUTE_ACTION` case accidentally falls through to `default: assertNever(msg)`, it throws — but if it just returns false, the service worker gets undefined silently.

**How to avoid:** In the content script switch, the `EXECUTE_ACTION` case must `return true` explicitly and call `sendResponse` asynchronously. Add a guard in the service worker: `if (!result || result.type !== 'ACTION_RESULT') { /* handle missing response */ }`.

**Warning signs:** `result` is `undefined` in service worker after sending EXECUTE_ACTION; no error logged.

### Pitfall 2: Message Channel Closes Before Async Response

**What goes wrong:** Content script starts the async automation flow but the Chrome message port closes before `sendResponse` is called. Service worker receives no response.

**Why it happens:** Chrome MV3 closes the message channel after the synchronous `handleMessage` function returns unless it returns `true`. If the handler returns a Promise, the channel also closes (Chrome does not handle returned Promises in `onMessage` listeners — only `return true` works).

**How to avoid:** The `EXECUTE_ACTION` case must be: `runAutomation(msg.action).then(result => sendResponse(result)); return true;` — NOT `return somePromise`.

**Warning signs:** "The message port closed before a response was received" in console; automation runs but service worker never gets ACTION_RESULT.

### Pitfall 3: EA DOM Is Not Ready After Navigation

**What goes wrong:** Navigation to a new section completes (URL changes, or `wxt:locationchange` fires) but the target elements do not exist yet. `querySelector` returns null immediately.

**Why it happens:** EA Web App is a SPA with async React rendering. Navigation fires before new page elements are inserted into the DOM.

**How to avoid:** Always use `waitForElement(selector, 5000)` after any navigation step. Never assume an element is immediately available after a click or URL change.

**Warning signs:** "Element not found: [selector]" thrown immediately after navigation rather than after a full wait.

### Pitfall 4: `assertNever` Compile Error on New Message Types

**What goes wrong:** Adding EXECUTE_ACTION or ACTION_RESULT to `ExtensionMessage` causes TypeScript compile errors in the content script switch statement.

**Why it happens:** This is intentional and correct — the `assertNever` in the default branch forces all variants to be handled. This is a Phase 6 safety feature.

**How to avoid:** Add cases for all new message types in BOTH the content script and service worker switch statements before compiling. Run `npm run compile` to verify.

**Warning signs:** `Argument of type 'ExtensionMessage' is not assignable to parameter of type 'never'` — this is the feature working correctly.

### Pitfall 5: EA Web App Selectors Change Between FC Versions

**What goes wrong:** Selectors that worked in FC25 do not work in FC26, or selectors break after an EA app update mid-season.

**Why it happens:** The EA Web App is not a stable public API. CSS class names are obfuscated and can change. Data attributes and ARIA labels are more stable but still not guaranteed.

**How to avoid:** (1) Use `selectors.ts` centralization so fixes require editing one file. (2) Prefer stable attributes: ARIA roles (`role="button"`), `aria-label`, input `placeholder` text, structural position in the DOM, and `data-*` attributes that correspond to game concepts. (3) Include a comment with the selector discovery date so staleness is visible.

**Warning signs:** `waitForElement` throwing consistently on a selector that previously worked — EA likely updated the app.

### Pitfall 6: CAPTCHA MutationObserver Missing Subtree Events

**What goes wrong:** CAPTCHA container is injected deep in the DOM tree but the observer is configured with `subtree: false`, so it never fires.

**Why it happens:** The EA Web App may inject CAPTCHA inside a nested container rather than directly into `document.body`.

**How to avoid:** CAPTCHA observer must use `{ childList: true, subtree: true }`. This is higher overhead than the shallow observer used for SPA detection, but CAPTCHA is rare and correctness matters.

### Pitfall 7: `chrome.notifications` Permission Missing

**What goes wrong:** `chrome.notifications.create()` throws or silently fails.

**Why it happens:** `wxt.config.ts` currently only lists `['alarms', 'storage', 'tabs']` in `manifest.permissions`. `notifications` is not included.

**How to avoid:** Add `'notifications'` to the `permissions` array in `wxt.config.ts` as part of Phase 7 Wave 0.

---

## Code Examples

### Message Protocol Extension

```typescript
// Source: Phase 6 established pattern + Phase 7 D-07
// extension/src/messages.ts
export type ExtensionMessage =
  | { type: 'PING' }
  | { type: 'PONG' }
  | { type: 'EXECUTE_ACTION'; action: PendingAction }
  | { type: 'ACTION_RESULT'; actionId: number; outcome: ActionOutcome }
  | { type: 'CAPTCHA_DETECTED' };

export type ActionOutcome =
  | { status: 'success'; price: number; result: 'bought' | 'listed' | 'relisted' }
  | { status: 'skipped'; reason: string }
  | { status: 'error'; selector: string; message: string };
```

### Async sendResponse Pattern (Critical)

```typescript
// Source: Chrome MV3 message passing semantics
// extension/entrypoints/ea-webapp.content.ts
case 'EXECUTE_ACTION': {
  // MUST return true — async response via sendResponse callback
  // Do NOT return a Promise here (Chrome closes the channel on Promise return)
  runAutomation(msg.action)
    .then((outcome) => sendResponse({ type: 'ACTION_RESULT', actionId: msg.action.id, outcome }))
    .catch((err) => sendResponse({
      type: 'ACTION_RESULT',
      actionId: msg.action.id,
      outcome: { status: 'error', selector: err.selector ?? 'unknown', message: err.message },
    }));
  return true;  // Keep channel open
}
```

### Price Sweep Pattern (D-03)

```typescript
// extension/src/automation/buy-flow.ts
// Adaptive sweep: lower max BIN until cheapest listing found, or raise if no results
async function sweepForCheapestListing(
  action: PendingAction,
  stepSize = 200,
  maxIterations = 20,
): Promise<number | null> {
  let maxBin = action.target_price;

  // Phase 1: ensure any results exist by raising max BIN
  for (let i = 0; i < maxIterations; i++) {
    await setSearchFilters(action, maxBin);
    await clickElement(SELECTORS.transferMarket.searchButton);
    await randomDelay();
    if (await hasResults()) break;
    maxBin += stepSize;
    if (i === maxIterations - 1) return null; // no listings at all
  }

  // Phase 2: narrow down to cheapest listing
  while (maxBin > 0) {
    const cheapest = await readCheapestBin();
    const trial = cheapest - stepSize;
    if (trial <= 0) break;
    await setSearchFilters(action, trial);
    await clickElement(SELECTORS.transferMarket.searchButton);
    await randomDelay();
    if (!await hasResults()) break;
    maxBin = trial;
  }
  return await readCheapestBin();
}
```

### waitForElement Implementation

```typescript
// Source: standard DOM polling pattern for SPA automation
// extension/src/automation/dom-utils.ts
export async function waitForElement(
  selector: string,
  timeoutMs = 5000,
  pollIntervalMs = 200,
): Promise<Element> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const el = document.querySelector(selector);
    if (el) return el;
    await new Promise(r => setTimeout(r, pollIntervalMs));
  }
  // AUTO-07: loud failure with selector name
  const err = new Error(`Element not found: ${selector}`) as any;
  err.selector = selector;
  throw err;
}
```

### Backend: _claim_action Response Extension (D-08)

```python
# src/server/api/actions.py — _claim_action()
# Must be extended to include card_version and position from the slot
return {
    "id": action.id,
    "ea_id": action.ea_id,
    "action_type": action.action_type,
    "target_price": action.target_price,
    "player_name": action.player_name,
    "card_version": action.card_version,  # NEW
    "position": action.position,           # NEW
}
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Polling with setTimeout in background page | chrome.alarms (alarm survives worker termination) | MV3 | Already implemented in Phase 6 |
| `return sendResponse(x)` in message handler | `sendResponse(x); return true;` | Chrome MV3 semantics | Must follow for async responses |
| Callback-based `chrome.tabs.sendMessage` | Promise-based with `await` | Chrome 98+ | Already used in Phase 6 pingActiveTab |
| Wildcard `allow_origins` for CORS | `allow_origin_regex` | Phase 5 decision | Already implemented in backend |

**Deprecated/outdated:**
- Returning a Promise from `onMessage` listener: Chrome ignores returned Promises; only `return true` keeps the channel open. Do NOT use `async function handleMessage(...)` — it returns a Promise which Chrome treats as falsy.

---

## Open Questions

1. **EA Web App selector values for FC26**
   - What we know: Community tools confirm DOM automation is possible; selectors exist
   - What's unclear: Exact selector strings (class names, data attributes, ARIA labels) for FC26 specifically
   - Recommendation: **Wave 0 exploration task is mandatory.** Assign a task to manually navigate the EA Web App with DevTools open, capture all needed selectors, and write them into `selectors.ts` before any automation flows are coded. This is the single most important prerequisite for Phase 7.

2. **CAPTCHA container selector**
   - What we know: EA uses a CAPTCHA on suspicious activity; MutationObserver pattern is solid
   - What's unclear: Exact element ID or selector for the CAPTCHA container in FC26
   - Recommendation: Include CAPTCHA selector discovery in the Wave 0 DevTools task.

3. **Card identification in unassigned items for LIST flow**
   - What we know: Must find the correct card (by ea_id) after a BUY action in the unassigned items list
   - What's unclear: Whether ea_id is exposed as a data attribute on card elements, or whether card identification must rely on player name + rarity visual matching
   - Recommendation: Inspect unassigned items DOM for any `data-item-id` or `data-ea-id` attribute. If not present, fall back to player name + position text matching.

4. **EA daily transaction cap**
   - What we know: STATE.md blocker — unpublished cap, conservative estimate 500/day
   - What's unclear: Whether the cap applies to Buy Now actions only, or all transfer market interactions
   - Recommendation: Implement a daily action counter in `chrome.storage.local`. Log each completed action. Pause automation when counter approaches 500. Verify empirically during testing.

5. **`card_version` and `position` data availability in backend**
   - What we know: `PortfolioSlot` in the DB currently has no `card_version` or `position` columns. `SlotEntry` pydantic model also lacks these fields.
   - What's unclear: Whether `card_version`/`position` can be inferred from existing scorer data (PlayerScore table may have these) or must be provided by the extension when seeding slots via POST /portfolio/slots.
   - Recommendation: The simplest path is to add `card_version` and `position` as nullable columns to `PortfolioSlot` and `TradeAction`, and require the extension to populate them via the seeding endpoint. The scorer already has this data from fut.gg.

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Node.js / npm | Extension build | ✓ | Present (npm in package.json) | — |
| WXT | Build + test | ✓ | ^0.20.20 installed | — |
| Vitest | Tests | ✓ | ^4.1.2 installed | — |
| Chrome (with DevTools) | Wave 0 selector discovery | Human-provided | — | Cannot automate without verified selectors |
| EA Web App (live) | Wave 0 selector discovery | Human-provided | FC26 | Cannot automate without live access |

**Missing dependencies with no fallback:**
- Live DevTools access to EA Web App (FC26) — required for Wave 0 selector discovery. Cannot be substituted by static analysis or community research; selectors must be verified against the live app.

**Missing dependencies with fallback:**
- None.

---

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework | Vitest ^4.1.2 + WXT fakeBrowser |
| Config file | `extension/vitest.config.ts` |
| Quick run command | `cd extension && npm test -- --run` |
| Full suite command | `cd extension && npm test -- --run` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| AUTO-01 | BUY flow finds cheapest BIN and clicks Buy Now | unit | `npm test -- --run tests/automation/buy-flow.test.ts` | ❌ Wave 0 |
| AUTO-02 | Price guard skips action when BIN > target_price | unit | `npm test -- --run tests/automation/buy-flow.test.ts` | ❌ Wave 0 |
| AUTO-03 | LIST flow navigates to unassigned and lists at target_price | unit | `npm test -- --run tests/automation/list-flow.test.ts` | ❌ Wave 0 |
| AUTO-04 | RELIST flow clicks "Relist All" button | unit | `npm test -- --run tests/automation/relist-flow.test.ts` | ❌ Wave 0 |
| AUTO-05 | randomDelay produces values in 800-2500ms range; no two identical | unit | `npm test -- --run tests/dom-utils.test.ts` | ❌ Wave 0 |
| AUTO-06 | CAPTCHA_DETECTED message sent when CAPTCHA element appears | unit | `npm test -- --run tests/content.test.ts` | ❌ extend existing |
| AUTO-07 | waitForElement throws with selector name after timeout | unit | `npm test -- --run tests/dom-utils.test.ts` | ❌ Wave 0 |
| AUTO-08 | All selectors exported from single `selectors.ts` file | structural | Source inspection test (like existing assertNever test) | ❌ Wave 0 |

Note: Flow tests use jsdom + mocked `document.querySelector` / `document.querySelectorAll`. They test logic (flow control, price guard, error propagation) not actual EA selectors. Selector correctness is validated by manual DevTools inspection only.

### Sampling Rate

- **Per task commit:** `cd extension && npm test -- --run`
- **Per wave merge:** `cd extension && npm test -- --run && npm run compile`
- **Phase gate:** Full suite green + `npm run compile` zero errors before `/gsd:verify-work`

### Wave 0 Gaps

- [ ] `extension/tests/dom-utils.test.ts` — covers AUTO-05, AUTO-07
- [ ] `extension/tests/automation/buy-flow.test.ts` — covers AUTO-01, AUTO-02
- [ ] `extension/tests/automation/list-flow.test.ts` — covers AUTO-03
- [ ] `extension/tests/automation/relist-flow.test.ts` — covers AUTO-04
- [ ] `extension/src/selectors.ts` — must exist with verified selectors before flow files
- [ ] `extension/src/automation/dom-utils.ts` — shared utilities
- [ ] Add `'notifications'` to `wxt.config.ts` permissions array

---

## Project Constraints (from CLAUDE.md)

The following CLAUDE.md directives apply to this phase:

| Directive | Impact on Phase 7 |
|-----------|-------------------|
| Python 3.12 / httpx / pydantic for backend | Backend changes (card_version/position fields) use existing SQLAlchemy + Pydantic patterns |
| TypeScript for Chrome extension | All new extension files must be `.ts`; no plain `.js` |
| Snake_case for Python, camelCase/PascalCase for TypeScript | `card_version`/`position` in Python models; `cardVersion`/`position` in TS types (or keep as snake_case to match API response) |
| No FUTBIN dependency | N/A — automation uses DOM only |
| Data source: fut.gg only | N/A — card_version/position data sourced from fut.gg player definitions, already in scorer |
| SQLite for storage, designed for PostgreSQL migration | PortfolioSlot / TradeAction column additions follow existing SQLAlchemy declarative pattern |
| Must use GSD workflow for edits | All implementation must go through `/gsd:execute-phase` |
| Use `from src.X import Y` absolute imports | Applies to backend changes in `src/server/` |
| Protocol-based abstraction pattern | Not directly applicable to DOM automation layer |
| All selectors in one file | Already captured as D-14 and AUTO-08 |
| Loud failure on DOM mismatch | Already captured as D-10, D-11, AUTO-07 |

---

## Sources

### Primary (HIGH confidence)

- Chrome for Developers — Message Passing: https://developer.chrome.com/docs/extensions/develop/concepts/messaging — `tabs.sendMessage` semantics, async response pattern, `return true` requirement
- Phase 6 established patterns — Phase 6 CONTEXT.md + existing extension code — discriminated unions, assertNever, WXT storage, fakeBrowser test patterns
- WXT documentation — https://wxt.dev — `defineContentScript`, `defineBackground`, `WxtVitest`, `wxt/utils/storage`
- CLAUDE.md project directives — Project naming, import patterns, error handling conventions

### Secondary (MEDIUM confidence)

- Community tool survey (EasyFUT, FutSniperExtension, Futinator, NOKA FUT) — confirms DOM automation of EA Web App from content scripts is feasible; GitHub repositories confirm live automation works but source code not inspectable from README alone
- Chrome Notifications API (MV3): https://developer.chrome.com/docs/extensions/reference/api/notifications — `chrome.notifications.create()` available in service worker; requires `notifications` permission in manifest

### Tertiary (LOW confidence)

- EA Web App selector placeholders — all selector values in `selectors.ts` are LOW confidence until verified against live FC26 app in DevTools
- EA daily transaction cap estimate (500/day) — from STATE.md, originally from community observation; unverified by EA official docs

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already installed, patterns proven in Phase 6
- Message protocol extension: HIGH — discriminated union pattern is established and tested
- DOM utility patterns: HIGH — standard DOM polling and jitter patterns, well-understood
- Automation flow logic: MEDIUM — buy/list/relist flow structure is clear from decisions; exact DOM interactions depend on verified selectors
- EA Web App selectors: LOW — obfuscated DOM, must be verified by live DevTools before implementation
- Backend schema extension: MEDIUM — SQLAlchemy pattern is established; migration strategy (Alembic vs drop-recreate) not yet decided

**Research date:** 2026-03-27
**Valid until:** 2026-04-27 (EA may update Web App; re-verify selectors before implementation if more than ~2 weeks pass)
