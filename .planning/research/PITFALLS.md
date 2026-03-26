# Pitfalls Research

**Domain:** Chrome extension automation on EA Web App — buy/list/relist cycle with Python backend
**Researched:** 2026-03-26
**Confidence:** MEDIUM-HIGH (Chrome MV3 constraints: HIGH — official docs; EA ban patterns: MEDIUM — community sources, EA forum evidence; CORS/FastAPI: HIGH — official docs; DOM targeting: HIGH — MDN + official Chrome docs)

---

## Critical Pitfalls

---

### Pitfall 1: EA Ban from Automation Volume and Inhuman Action Timing

**What goes wrong:** The extension executes buy/list/relist operations at machine speed — uniform intervals, no pauses, round-number timing. EA's backend detects the inhuman consistency and bans the account from the transfer market, escalating to full account suspension. The automation target (the EA account) is lost.

**Why it happens:** EA monitors transaction-per-minute ratios, inter-action timing consistency, and buy volume relative to concurrent gameplay activity. Bots expose themselves through uniformity: actions at exactly 500ms apart, 200 buy-now bids in 10 minutes, no session variation. EA forums confirm a documented "You've reached your limit on bid/buy actions" daily limit exists, and exceeding it triggers cooldowns that escalate with repeat violations. Community-sourced evidence (FutBotManager, FutStarz) confirms EA issues ban waves targeting accounts with recognizable automation signatures.

**How to avoid:**
- Add randomized jitter to every action delay. Use a range like 800ms–2500ms per action rather than any fixed interval.
- Cap daily automated buy/bid operations well below 1,000 per day (community ceiling for safe operation).
- Build in session breaks: pause all automation for 15–30 minutes every 1–2 hours, simulating human fatigue.
- Never automate while the EA Web App tab is in the background — EA can detect background-tab activity patterns vs. foreground human interaction.
- Vary action sequences: don't always buy-then-list in identical order. Introduce occasional "inspect" pauses, navigation detours.
- Use a dedicated throwaway account for all development and testing. Never test automation against a main account.
- Avoid sending modified or non-standard API requests (e.g., multi-rarity filter exploits) — EA has specifically cracked down on requests that deviate from browser-generated parameters.

**Warning signs:**
- EA logs the user out mid-session and presents a CAPTCHA.
- "You've reached your limit on bid/buy actions" message appears unexpectedly.
- Transfer list goes read-only without a network error.
- Account receives a 24-hour or 7-day market ban.

**Phase to address:** Phase 1 (Chrome extension foundation). Every automation action needs rate controls baked in from the first commit. Adding delays as a retrofit is error-prone and often missed for edge-case code paths.

---

### Pitfall 2: Manifest V3 Service Worker Termination Destroying In-Flight Task State

**What goes wrong:** The extension background service worker holds the current task queue and job state in JavaScript variables. Chrome terminates idle service workers after ~30 seconds of inactivity. When the worker restarts on the next event, all in-memory state is gone. A buy-list cycle abandons mid-execution: a player is bought but never listed, or a relist loop silently restarts from scratch on every worker wake.

**Why it happens:** Manifest V3 replaced MV2's persistent background pages with event-driven service workers. The service worker lifecycle is explicitly non-persistent by design. Developers who carry over MV2 patterns (global variables for state, `setTimeout` for recurring work) hit this immediately. Chrome's official docs confirm workers stop 30 seconds after the last event and may also be terminated during Chrome shutdown or resource pressure. The "debugging masked" variant of this bug: when DevTools is open, the service worker never goes inactive, so bugs only surface in production use.

**How to avoid:**
- Store ALL task state in `chrome.storage.local` (persisted to disk) or the Python backend API. Zero global variables for state that must survive a worker restart.
- Use the Chrome Alarms API (`chrome.alarms`) for any recurring action, not `setTimeout` or `setInterval`. Alarms survive worker termination and re-trigger the worker.
- Model all automation as resumable steps: each step reads current state from storage, executes exactly one action, writes updated state back, schedules the next alarm. Treat the service worker as stateless between event invocations.
- On service worker startup (`chrome.runtime.onInstalled` and `chrome.runtime.onStartup`), check storage for an in-progress job and resume if one exists — do not assume a clean start.
- Use WebSocket connections to the Python backend to keep the service worker alive during active automation sessions (an active WebSocket resets the idle timer per Chrome 116+).

**Warning signs:**
- Extension "forgets" a queued task after the browser is idle for 30+ seconds.
- Transfer list shows a newly purchased player but no listing ever occurs.
- Automation logs show a repeating "starting job" entry every 30 seconds with no completion entries between.

**Phase to address:** Phase 1 (Chrome extension architecture). This must be the foundational design constraint before any automation logic is written. Retrofitting storage-backed state onto code designed around globals requires a full rewrite.

---

### Pitfall 3: EA Web App SPA Navigation Orphaning Content Script Listeners

**What goes wrong:** The EA Web App is a single-page application. When the user navigates within it (Transfer Market → Squad → back to Transfer Market), the URL changes via `history.pushState` but no full page reload occurs. The content script injected at initial load is not re-injected. DOM nodes the script attached event listeners to are destroyed and rebuilt by the SPA router. All button handlers, MutationObservers watching specific elements, and selector queries silently fail.

**Why it happens:** Chrome injects content scripts on `document_idle` for matching URL patterns. SPA navigation does not satisfy this trigger — the document does not reload. The content script correctly assumes its injected DOM context will persist, but the SPA replaces the relevant subtree on every route change. This is especially acute on EA Web App because the Transfer Market UI is loaded lazily and may not exist in the DOM until the route is active.

**How to avoid:**
- Attach a `MutationObserver` on `document.body` to detect SPA route changes. Watch for insertion/removal of a stable route-indicator element (e.g., the top-level view container), or intercept `history.pushState` via a page-world script injection.
- Re-initialize all UI overlays and event listeners on every detected navigation to the Transfer Market route.
- Delegate event listeners to `document.body` where possible (event delegation) rather than attaching directly to leaf elements that are replaced on navigation.
- Build a "health check" that runs every 5 seconds when automation is active: verify the expected DOM anchor elements are still present and re-initialize if missing.

**Warning signs:**
- Extension overlay buttons work on first load, stop responding after in-app navigation.
- Console errors: `Cannot read properties of null (reading 'addEventListener')` on element queries.
- Automation completes one full cycle but fails on subsequent cycles during the same session.

**Phase to address:** Phase 1 (Chrome extension content script architecture). SPA navigation handling must be part of the content script scaffold before any UI or automation code is written on top of it.

---

### Pitfall 4: EA Web App DOM Structure Changes Breaking Automation Silently

**What goes wrong:** EA deploys Web App updates mid-season — content patches, anti-cheat measures, UI redesigns. CSS class names change, button elements are restructured, or new modal overlays intercept click events. The extension continues executing "actions" — querying selectors that return `null`, calling `.click()` on undefined — without throwing errors, silently doing nothing. In the worst case, focus shifts and the wrong element receives the click (e.g., "Quick Sell" instead of "List on Transfer Market").

**Why it happens:** EA treats the Web App DOM as an internal implementation detail. Class names are minified and rotate between deploys. EA has also historically deployed targeted patches specifically to break known automation tools. Any selector strategy relying on class names or DOM structure is fragile by nature. Silent failures (querying for a non-existent element returns null, not an exception) mean the automation appears to be running while doing nothing.

**How to avoid:**
- Never interact with elements by CSS class name. Target exclusively by ARIA roles, `data-*` attributes, visible label text, or structural position relative to stable containers.
- Before executing any action (buy, list, relist), verify the target element exists AND matches the expected label/type. If not found, stop automation and notify the user with specifics ("Could not find 'Buy Now' button — EA Web App may have been updated").
- Implement a dry-run mode that logs the element that would be clicked, its current label, and its DOM path. Run dry-run after each EA deploy before resuming automation.
- Monitor EA's Web App changelog and community reports (e.g., EA Forums, FUT community Discord) for deploy announcements. Treat any deploy as requiring a compatibility verification run.

**Warning signs:**
- Automation runs without errors but the transfer list does not change.
- Follows an EA Web App maintenance window or patch announcement.
- Extension reports "completed" but no coins were spent or players listed.

**Phase to address:** Phase 1 (automation action layer), plus an ongoing maintenance task after each EA deploy. The selector verification and loud-failure patterns must be designed in from the start.

---

### Pitfall 5: CORS Blocking Extension-to-Backend Requests from Content Scripts

**What goes wrong:** Content scripts make `fetch()` calls directly to `http://localhost:8000`. Chrome treats content script requests as originating from the host page's origin (e.g., `https://www.ea.com`). The backend's CORS policy does not include `https://www.ea.com` as an allowed origin. The browser blocks the request with a CORS error. Alternatively, the extension developer adds `http://localhost:8000` to `host_permissions` but the CORS restriction still applies because the request origin header is the page origin, not the extension origin.

**Why it happens:** Since Chrome 85, cross-origin fetches from content scripts send the host page's `Origin` header (not the extension's origin). The server must explicitly allow the page origin in `Access-Control-Allow-Origin`. Extension background/service worker pages are unaffected — they can make cross-origin requests freely if `host_permissions` includes the target. Developers who test fetch calls from background scripts and then move them to content scripts encounter the block.

**How to avoid:**
- Route ALL backend API calls through the service worker (background script), not content scripts. Content scripts send messages to the service worker via `chrome.runtime.sendMessage`; the service worker makes the fetch and relays the response back.
- Add `http://localhost:8000/*` to `host_permissions` in `manifest.json` for the service worker's benefit.
- On the FastAPI backend, configure `CORSMiddleware` to allow the extension's actual origin (`chrome-extension://<extension-id>`) for defense in depth, but rely primarily on the service worker routing pattern.
- Never attempt direct cross-origin fetches from content script context to localhost.

**Warning signs:**
- Browser console shows `Access to fetch at 'http://localhost:8000/...' from origin 'https://www.ea.com' has been blocked by CORS policy`.
- Requests succeed during extension development testing (background script context) but fail after refactoring to content scripts.

**Phase to address:** Phase 1 (Chrome extension architecture). The message-passing pattern between content scripts and service worker must be established before any backend communication is implemented.

---

## Technical Debt Patterns

Shortcuts that seem reasonable but create long-term problems.

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Store automation state in service worker global variables | Simple, fast to write | State wiped on 30s idle; causes silent task abandonment; unfixable without rewrite | Never — use `chrome.storage.local` from the start |
| Target EA Web App DOM elements by CSS class name | Quick to discover and implement | Breaks on every EA deploy; silent failures; requires constant maintenance | Never — always prefer ARIA roles, data attributes, or label text |
| Hardcode `http://localhost:8000` in extension | Fast for local dev | Breaks on cloud migration; Chrome Web Store policy violation if published | Acceptable only as the initial default value in an options page that is always configurable |
| Execute automation at fixed uniform intervals | Simple implementation | EA bot detection flags uniform timing patterns; account ban risk | Never — always add randomized jitter |
| Make fetch calls to backend from content script context | Feels natural (content script is "on the page") | CORS blocked by browser; requires full refactor to service worker routing | Never — all backend calls must go through service worker |
| Test automation on a real main account | Immediate real-world validation | Any detection bug permanently bans the primary account | Never — use a dedicated test account through all development |
| Use `setTimeout` for recurring automation steps | Familiar API, easy to write | Cancelled when service worker terminates; automation silently stops | Never in MV3 service workers — use `chrome.alarms` |

---

## Integration Gotchas

Common mistakes when connecting to external services.

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| EA Web App DOM | Rely on CSS class names as stable selectors | Target by ARIA role, `data-*` attributes, or visible label text; treat class names as ephemeral |
| EA Web App SPA | Assume content script runs once per session | Re-initialize listeners on every SPA navigation via MutationObserver |
| EA Transfer Market API | Send modified request parameters (e.g., multi-rarity filters) | Only send parameters that the EA Web App's own JS would naturally generate; no manual parameter construction |
| FastAPI backend from extension | Call backend from content script context | Route all API calls via service worker message passing; never direct fetch from content script |
| FastAPI CORS config | Set `allow_origins=["*"]` in development and forget | Configure explicitly with the extension origin (`chrome-extension://<id>`) and localhost; never use wildcard in any deployed configuration |
| `chrome.storage.local` | Use it as an unstructured dump | Define a typed state schema upfront; JSON schema or TypeScript interfaces; invalid state shapes cause silent bugs |
| Chrome Alarms API | Create duplicate alarms on service worker restart | Always check `chrome.alarms.get(name)` before creating; use named alarms to prevent duplicates |

---

## Performance Traps

Patterns that work at small scale but fail as usage grows.

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| MutationObserver watching `document.body` with no subtree filter | High CPU during any DOM change on the EA Web App page | Scope observation to the minimum necessary container; use specific attribute/childList filters | Immediately on Transfer Market pages with rapid live auction updates |
| Polling DOM for element presence in a tight loop (`setInterval` at 100ms) | CPU spikes; EA may detect synthetic activity pattern | Use MutationObserver instead of polling; observe for element insertion rather than querying repeatedly | Any page with frequent DOM updates |
| Sending `chrome.storage.local` writes on every single action | Storage I/O becomes a bottleneck; intermittent data loss on rapid writes | Batch state updates; debounce storage writes; only write at step boundaries | At 50+ rapid sequential actions |
| Opening a new port connection per action for content-script-to-service-worker messaging | Port overhead accumulates; service worker stays alive unnecessarily | Use a single long-lived port per session; reuse it for all messages during a session | At high action rates (>10 actions/min) |

---

## Security Mistakes

Domain-specific security issues beyond general web security.

| Mistake | Risk | Prevention |
|---------|------|------------|
| Logging EA session cookies or auth tokens to console or backend | Session hijacking; EA account takeover | Never log auth headers; never forward EA cookies to the backend; the backend receives only game data, not credentials |
| Storing the EA account password or session in `chrome.storage` | Credential exposure if extension storage is accessed by malicious page script | Never store EA credentials in extension storage; session is managed entirely by the EA Web App, not the extension |
| Injecting scripts into MAIN world without necessity | Exposes extension logic to page scripts; EA's page JavaScript can detect and interfere with the automation | Use ISOLATED world (default) for all content scripts; only inject into MAIN world if absolutely required for DOM interaction that isolated world cannot achieve |
| Accepting arbitrary commands from the EA Web App page via `window.postMessage` | Page-origin XSS or EA's own scripts could trigger unintended automation actions | Never listen for commands from the page origin; all automation commands originate from the extension popup or service worker only |
| Publishing the extension to the Chrome Web Store with localhost backend URL | Policy violation; extension may be removed; exposes internal tooling to public | Keep the extension as an unpacked developer extension; do not publish to the Web Store |

---

## UX Pitfalls

Common user experience mistakes in this domain.

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| Automation runs silently with no progress indication | User cannot tell if the extension is active, stuck, or broken | Show a live status panel in the extension popup: current action, last action timestamp, items bought/listed this session |
| No way to pause or stop automation mid-cycle | User cannot interrupt a buy sequence if they see an unexpected price | Provide a prominent "Pause" button that the content script and service worker both respect; check pause state before every action |
| Showing recommended players without data age | User buys on a score that was computed 90 minutes ago during a price spike | Always surface the `scored_at` timestamp alongside every recommendation; warn when data is older than 60 minutes |
| Automation starts immediately on page load | User has no chance to review the portfolio before purchases begin | Require explicit "Start Automation" confirmation per session; never auto-start |
| Error messages that say "Something went wrong" | User cannot tell if the EA Web App changed, the backend is down, or there is a network error | Distinguish and report: EA DOM mismatch vs. backend unreachable vs. EA rate limit hit; each needs a different user action |

---

## "Looks Done But Isn't" Checklist

Things that appear complete but are missing critical pieces.

- [ ] **Content script injection:** Often appears to work in testing because the service worker is warm and the page has not been navigated — verify that re-injection/re-initialization works after SPA navigation to a different route and back.
- [ ] **Automation action timing:** Often appears safe in short test runs — verify with a full simulated session of 50+ actions over 2 hours; measure inter-action timing distribution.
- [ ] **Service worker state persistence:** Works perfectly when DevTools is open (worker never terminates) — verify by closing DevTools, letting the browser idle for 60 seconds, then resuming automation.
- [ ] **Backend CORS configuration:** Works from the extension popup (service worker context) — verify separately that content script requests also function correctly (they should go through service worker, but confirm the routing is actually working).
- [ ] **Selector stability:** Works today — verify immediately after any EA Web App update by running dry-run mode and confirming all selectors still resolve.
- [ ] **Task resumability:** Works from a clean start — verify that if the browser crashes or the extension is reloaded mid-buy-cycle, the task either completes correctly or fails cleanly without orphaned purchases.
- [ ] **Configurable backend URL:** Works on localhost — verify the options page actually persists the URL change and that all code paths (service worker, content script messaging) use the persisted value, not a hardcoded fallback.
- [ ] **EA rate limiting respect:** Works for a 10-minute test — run a 4-hour session and verify no "limit reached" messages appear; confirm daily transaction counter resets correctly at midnight UTC.

---

## Recovery Strategies

When pitfalls occur despite prevention, how to recover.

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| EA account market ban (temporary) | LOW-MEDIUM | Wait out the ban period (24h–7 days); audit extension action logs for the timing/volume pattern that triggered detection; tighten jitter ranges and daily caps before resuming |
| EA account permanent ban | HIGH | Only possible recovery: EA support appeal (rare success rate); rebuild with clean account; use the banned account's data as evidence of which patterns to avoid |
| Service worker state loss mid-cycle | LOW | Implement `onStartup` resume handler in the service worker; if orphaned purchase detected (player in club but not in tracking DB), handle via manual relist flow |
| EA Web App DOM breaking selectors | MEDIUM | Activate dry-run mode; audit which selectors fail; update selector strategy to use stable attributes; test against the updated DOM; resume only after dry-run passes all checks |
| CORS blocking content script requests | LOW | Verify all backend calls are routed through service worker; if any direct fetch existed in content script, move to message-passing pattern; no architectural change required if designed correctly from the start |
| MV3 service worker not keeping alive | LOW | Add WebSocket ping to backend to maintain live connection during active automation; this resets the 30-second idle timer per Chrome 116+ behavior |

---

## Pitfall-to-Phase Mapping

How roadmap phases should address these pitfalls.

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| EA automation ban from volume/timing | Phase 1: Automation action layer | 4-hour test session shows no limit-reached messages; action timing distribution is not uniform |
| MV3 service worker state loss | Phase 1: Extension architecture | Close DevTools, idle 60s, resume automation — task continues from last checkpoint |
| SPA navigation orphaning content script | Phase 1: Content script scaffold | Navigate away and back to Transfer Market mid-session — all UI and automation still respond |
| EA Web App DOM changes | Phase 1: Automation action layer + ongoing | Dry-run mode passes after each EA deploy; all actions fail loudly when selectors are missing |
| CORS content script to backend | Phase 1: Extension architecture | Confirm all network calls in content script context go through service worker; no direct fetches to localhost from page context |
| Hardcoded localhost backend URL | Phase 1: Extension foundation | Options page persists custom backend URL; all code paths use the persisted value |

---

## Sources

- EA FC 26 ban patterns and evidence: [EA Forums FC26 bid/buy limit](https://forums.ea.com/discussions/fc-26-general-discussion-en/limit-on-bidbuy-actions-after-finally-getting-access-to-the-transfer-market/12599137), [EA Forums FC26 false transfer market ban](https://forums.ea.com/discussions/ea-forums-general-discussion-en/false-transfer-market-ban-fc-26/12655634), [FutBotManager EA ban wave avoidance](https://futbotmanager.com/ea-ban-wave-avoidance-futbotmanager/), [Unbanster EA FC 26 unban guide](https://unbanster.com/get-unbanned-ea-sports-fc/)
- EA transfer market daily limits: [EA Forums: "You've reached your limit on bid/buy actions" 24 hours](https://forums.ea.com/discussions/fc-26-technical-issues-en/you%E2%80%99ve-reached-your-limit-on-bidbuy-actions-please-come-back-in-24-hours/12739200), [EA Forums FC26 bid/buy limit bug](https://forums.ea.com/discussions/fc-26-ultimate-team-en/transfer-market-limit-reached---bug/12612333)
- Chrome MV3 service worker lifecycle: [Chrome for Developers: service worker lifecycle](https://developer.chrome.com/docs/extensions/develop/concepts/service-workers/lifecycle), [Chrome for Developers: migrate to service workers](https://developer.chrome.com/docs/extensions/develop/migrate/to-service-workers), [Medium: Chrome Extension V3 service worker timeout mitigation](https://medium.com/@bhuvan.gandhi/chrome-extension-v3-mitigate-service-worker-timeout-issue-in-the-easiest-way-fccc01877abd)
- Chrome MV3 persistent patterns: [Medium: Building persistent Chrome Extension using MV3](https://rahulnegi20.medium.com/building-persistent-chrome-extension-using-manifest-v3-198000bf1db6), [Chromium: ServiceWorker is shut down every 5 minutes](https://issues.chromium.org/issues/40733525)
- Content script CORS behavior: [Chromium: Changes to Cross-Origin Requests in Chrome Extension Content Scripts](https://www.chromium.org/Home/chromium-security/extension-content-script-fetches/), [Chrome for Developers: network requests](https://developer.chrome.com/docs/extensions/develop/concepts/network-requests)
- FastAPI CORS configuration: [FastAPI official CORS docs](https://fastapi.tiangolo.com/tutorial/cors/)
- MutationObserver for SPA navigation: [MDN: MutationObserver](https://developer.mozilla.org/en-US/docs/Web/API/MutationObserver), [Chrome for Developers: content scripts](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts)
- Content script isolated vs main world: [Chrome for Developers: content scripts isolated world](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts)
- Extension state management in MV3: [Chrome Extensions Groups: Best Practices for State Management in MV3](https://groups.google.com/a/chromium.org/g/chromium-extensions/c/WSepGQIMqd8), [Chrome for Developers: message passing](https://developer.chrome.com/docs/extensions/develop/concepts/messaging)
- Extension toolchain 2025-2026: [2025 State of Browser Extension Frameworks: Plasmo, WXT, CRXJS](https://redreamality.com/blog/the-2025-state-of-browser-extension-frameworks-a-comparative-analysis-of-plasmo-wxt-and-crxjs/)
- Bot detection mechanics: [GeeTest: Top Strategies for Detecting Bots In-Game 2025](https://www.geetest.com/en/article/top-strategies-for-detecting-a-bot-ingame-in-2025)

---
*Pitfalls research for: Chrome extension EA Web App automation — FC26 OP Sell Platform v1.1*
*Researched: 2026-03-26*
