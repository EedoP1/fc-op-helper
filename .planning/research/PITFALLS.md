# Domain Pitfalls

**Domain:** FUT trading automation — persistent backend, Chrome extension, profit tracking
**Researched:** 2026-03-25
**Confidence:** MEDIUM (bot detection patterns from community sources; Chrome MV3 constraints from official docs; SQLite concurrency from official docs)

---

## Critical Pitfalls

Mistakes that cause account bans, rewrites, or complete failure modes.

---

### Pitfall 1: EA Transaction-Volume Ban

**What goes wrong:** The extension buys or lists players at machine speed — no delays, uniform intervals, or round-number timing — and EA's backend flags the account for automated activity. The account receives a temporary market ban, then escalating bans, then permanent suspension. The entire project loses its test account.

**Why it happens:** EA's algorithm monitors transaction-per-minute ratios, time-between-actions consistency, and volume relative to gameplay activity. Bots expose themselves through inhuman uniformity: actions at exactly 500ms apart, 200 buy-now bids in 10 minutes, no sessions longer than the current price-check cycle.

**Consequences:** Account ban eliminates the automation target. EA bans the player, not the script — there is no fallback. If testing on a main account, years of progress are at risk.

**Prevention:**
- Add randomized delays between all extension actions: base delay + random jitter in range (e.g., 800ms–2500ms per action, not 0.15s uniform).
- Cap daily automated transactions (community ceiling: under 1,000 buy/list operations per day based on bot developer guidance).
- Implement "session breaks" — pause automation for 15–30 minutes every 1–2 hours, mimicking human fatigue.
- Never run the extension without the EA Web App tab being the active focused tab (EA can detect background tab activity patterns).
- Use a separate test/throwaway account during all development and testing phases. Never test automation on the main account.

**Detection (warning signs):**
- EA logs you out and shows a captcha mid-session.
- Account receives a temporary "trading cooldown" message.
- Sudden inability to list or search despite no network error.

**Phase mapping:** Must be addressed in Phase 1 (Chrome extension build). Every automation action added needs rate controls baked in from the start, not retrofitted.

---

### Pitfall 2: fut.gg API Becoming Unavailable or Rate-Limited at Scale

**What goes wrong:** The backend scans the full 11k–200k price range every hour. At steady state this means: paginated discovery across ~50 pages, then ~200–500 individual player-price requests per cycle. fut.gg is an unofficial third-party site with no documented rate limits, no SLA, and no obligation to support this usage pattern. Either fut.gg blocks the scanner's IP, starts returning 429s silently, or changes its API structure without notice — and the scanner breaks with no warning.

**Why it happens:** The existing code already has this problem in one-shot mode (CONCERNS.md: no exponential backoff, silent 429 failures, bare `except Exception` blocks). At 24/7 scheduled cadence, these failures compound. A blocked IP means every hourly scan fails. Silent parse errors mean the database fills with stale, incomplete scores.

**Consequences:** The backend appears healthy but outputs invalid scores. Users act on stale recommendations and lose coins. Or the scanner is fully blocked and provides no recommendations at all.

**Prevention:**
- Implement exponential backoff with jitter on all fut.gg requests before adding the scheduler (fix the existing CONCERNS.md issue first).
- Add a circuit breaker: if >20% of requests in a scan cycle return 429 or fail to parse, abort the cycle and alert (log + dashboard indicator), rather than continuing with partial data.
- Track scan health as a first-class metric in the DB: `last_scan_at`, `scan_success_rate`, `parse_failure_count`. Expose on the API so the CLI and dashboard can show a "data freshness" indicator.
- Store the last known good score per player — serve stale-but-valid data rather than nothing, but tag it with an age warning.
- Do NOT normalize scan cadence to exactly 60 minutes. Use jitter (55–65 minute window) to avoid looking like a clockwork bot to fut.gg's infrastructure.

**Detection (warning signs):**
- `scan_success_rate` drops below 80%.
- `parse_failure_count` spikes.
- Player scores stop updating (stale `last_score_at` timestamps in DB).

**Phase mapping:** Phase 1 backend (scheduler + DB). Cannot be deferred — the 24/7 scanning architecture is the core of this milestone.

---

### Pitfall 3: Chrome Extension Manifest V3 Service Worker State Loss

**What goes wrong:** The extension is built with state in global variables in the background service worker (e.g., current job queue, action history, session status). Chrome terminates idle service workers after 30 seconds. The worker restarts on the next event with all state wiped, causing the extension to forget what it was doing mid-task — abandoning a buy-list cycle partway through.

**Why it happens:** Manifest V3 replaced persistent background pages with event-driven service workers. This is a fundamental architectural change. Developers who treat the service worker like a persistent background script (as in MV2) will hit this immediately. Global variables between activations do not persist.

**Consequences:** Extension silently abandons in-progress automation tasks. A player is bought but never listed. A relist loop starts from scratch every 30 seconds. The UI shows "running" while the worker has already been killed.

**Prevention:**
- Store all task state in `chrome.storage.local` (sync to disk) or the backend API — never in memory variables.
- Use the Chrome Alarms API (not `setTimeout`/`setInterval`) for any recurring actions — alarms survive service worker termination and restart.
- Design the extension's task model as resumable jobs: each step reads state from storage, executes one action, writes updated state back, then schedules the next alarm. Treat the worker as stateless between events.
- Use the offscreen document API if DOM interaction is needed that can't be done from a content script.

**Detection (warning signs):**
- Extension "forgets" a queued task after the browser is idle for 30+ seconds.
- Action history shows gaps between completed steps.
- Automation appears to restart from step 1 mid-sequence.

**Phase mapping:** Phase 1 Chrome extension architecture. Must be the starting design constraint, not a fix discovered later.

---

### Pitfall 4: EA Web App SPA Navigation Breaking Content Script Injection

**What goes wrong:** The EA Web App is a single-page application (SPA). When the user navigates within it (e.g., Transfer Market → Squad → Transfer Market), the URL changes but no full page reload occurs. The content script was injected once on initial load. After SPA navigation, the DOM the script was attached to may be replaced, listeners become orphaned, and the extension stops responding to page state.

**Why it happens:** Chrome content scripts fire on `document_idle` for matching URLs. SPA navigation does not re-trigger content script injection. The extension assumes a stable DOM that the SPA quietly tears down and rebuilds on every route change.

**Consequences:** Extension UI buttons stop working after in-app navigation. Automation targeting specific DOM elements (e.g., "Buy Now" button) fails to find them because the DOM node from original injection no longer exists.

**Prevention:**
- Use `MutationObserver` on the document body to detect SPA navigation (watch for route-indicator element changes or URL changes via `history.pushState` interception).
- Re-initialize content script listeners after detecting navigation to a new SPA route.
- Target EA Web App DOM elements by stable data attributes or ARIA roles rather than CSS class names (EA minifies and rotates class names on deploys).
- Add a defensive health-check in the extension: periodically verify the expected DOM elements are still present, reinitialize if not.

**Detection (warning signs):**
- Extension buttons work on first load, fail after navigating within the Web App.
- Console errors showing `Cannot read properties of null` on element queries.
- Automation works in testing but fails after user navigates mid-session.

**Phase mapping:** Phase 1 Chrome extension. Content script architecture must account for SPA behavior before any automation logic is written.

---

### Pitfall 5: EA Web App DOM Changes Breaking Automation Silently

**What goes wrong:** EA deploys Web App updates mid-season (content updates, anti-cheat patches, UI changes). CSS class names change, button DOM structure shifts, or new overlay elements intercept click events. The extension continues "clicking" coordinates or querying selectors that no longer exist — silently doing nothing or clicking the wrong element.

**Why it happens:** The EA Web App is EA's property and can change at any time without notice. Automation that depends on specific DOM structure is brittle. EA also actively uses Web App deployments to break third-party automation tools.

**Consequences:** After an EA deploy, the extension stops buying or listing without any error. Players are left in the transfer list. Worse: the extension clicks an unintended element (e.g., "Discard" instead of "List").

**Prevention:**
- Never click by pixel coordinate — always query a DOM element, verify it matches the expected label/ARIA role, then interact.
- Build a selector version registry: log which selectors were used for each action. When a selector fails to find an element, fail loudly (notification to user) instead of silently.
- Implement a dry-run mode that logs what actions would be taken without executing them — use this after any EA deploy before resuming automation.
- Subscribe to EA FC changelog/patch notes to detect deploy windows.

**Detection (warning signs):**
- Automation runs without errors but transfer list does not change.
- The extension UI shows "completed" but no actual market actions occurred.
- Follows an EA Web App maintenance window or patch release.

**Phase mapping:** Phase 1 Chrome extension, ongoing maintenance concern after each EA deploy.

---

## Moderate Pitfalls

---

### Pitfall 6: SQLite Concurrent Write Contention Under Scan Load

**What goes wrong:** The FastAPI server handles API requests (reads) while the scheduler runs hourly scans (writes). Both use SQLite. WAL mode allows concurrent reads, but only one writer at a time. If the scan job holds a write transaction open for 30+ seconds (e.g., writing 500 player scores), API write requests (e.g., marking a player as "in portfolio") queue up and eventually timeout with `database is locked`.

**Why it happens:** SQLite serializes all writers. A background task writing a large batch is indistinguishable from a lock to all other writers. This is a known pitfall when mixing FastAPI's async model with SQLite's threading constraints (`check_same_thread=False` is required but doesn't prevent lock contention).

**Prevention:**
- Use WAL mode explicitly (`PRAGMA journal_mode=WAL`) — it is not the default.
- Batch scan writes into small transactions (commit per 20–50 players, not all 500 at end).
- Set a generous `PRAGMA busy_timeout` (5000ms) so brief contention resolves without error.
- Use a single shared connection pool with a thread lock, not per-request connections.
- Design the schema so scan writes and user API writes hit different tables where possible (reduce conflict surface).

**Detection (warning signs):**
- `OperationalError: database is locked` in FastAPI logs during scan cycles.
- API response times spike during the top-of-hour scan window.

**Phase mapping:** Phase 1 backend DB design.

---

### Pitfall 7: Stale Scores Presented as Current

**What goes wrong:** A player's price spikes or crashes between scan cycles. The backend's stored score reflects an hour-old price. The CLI or extension shows this player as an excellent OP sell opportunity, but the buy price has since moved and the OP window is closed or the margin is now negative.

**Why it happens:** The hourly scan cadence is correct for normal conditions, but FUT market events (TOTW release, SBC drops, promo announcements) cause sudden, large price movements. A score computed at 9:00am is invalid by 9:15am after a TOTW announcement.

**Prevention:**
- Tag every score with `scored_at` timestamp. Surface data age in all client displays ("Scored 47 min ago").
- Add a "freshness threshold" filter in the API: default to only returning scores from the last 90 minutes; warn when serving older data.
- Detect likely market events via fut.gg's `momentum` field — if momentum spikes sharply on a player, flag the score as potentially stale and trigger an immediate re-scan for that player.
- The extension should perform a live price check via the backend before executing any buy action, not rely solely on the cached score.

**Detection (warning signs):**
- User reports buying a player on a recommended score only to find the OP opportunity no longer exists.
- Price history for a player shows a sharp movement in the last 30–90 minutes.

**Phase mapping:** Phase 1 backend (score schema + API), Phase 2 extension (pre-buy validation).

---

### Pitfall 8: APScheduler Running Multiple Scan Instances

**What goes wrong:** The FastAPI server is restarted (e.g., during development) while a scan job is mid-flight. On restart, APScheduler starts a new scan job immediately because the previous run's completion was not recorded. Two scan cycles run simultaneously — doubling API calls to fut.gg, causing rate limiting, and potentially writing duplicate or conflicting rows to the DB.

**Why it happens:** APScheduler's in-memory job store does not survive process restarts. The scheduler does not know a job was already running. With `misfire_grace_time` misconfigured, it can also trigger catch-up runs for missed intervals.

**Prevention:**
- Use SQLite as the APScheduler job store (persisted), not the in-memory default.
- Set `coalesce=True` on the scan job to prevent catch-up runs after missed intervals.
- Implement a DB-level "scan lock" row: insert a `scan_running=True` record before a scan, delete after. On startup, check if a lock row exists — if yes, clear it (the previous process crashed) and log a warning before starting fresh.
- Set `max_instances=1` on the scan job.

**Detection (warning signs):**
- Duplicate rows appearing in the scores table for the same player and timestamp.
- fut.gg rate limit errors appearing in bursts immediately after server restart.
- Scan job duration logs showing two overlapping runs.

**Phase mapping:** Phase 1 backend scheduler setup.

---

### Pitfall 9: Price-at-Time Fallback Causing False OP Scores at Scale

**What goes wrong:** This is a known issue in CONCERNS.md but becomes critical at 24/7 scale. When the hourly price history has a gap (fut.gg doesn't return a data point within ±2 hours of a sale), the scorer falls back to current BIN. In the persistent backend, this fallback is silent and baked into the stored score. Users see a high-margin player that is not actually OP — the score was inflated by a stale-price comparison.

**Why it happens:** fut.gg's price history is hourly and not always complete. Volatile cards that have been repriced multiple times in a short window, or newly added cards, often have sparse history. The ±2 hour search window is generous but still misses gaps. The one-shot CLI can be re-run; the persistent backend stores bad scores indefinitely.

**Prevention:**
- Add a `fallback_used_count` column to the scores table. Record how many OP-detected sales relied on the BIN fallback.
- Reject or heavily discount scores where `fallback_used_count / total_op_sales > 0.3` (more than 30% of OP detections used a fallback price).
- Log warnings when fallback is used (as recommended in CONCERNS.md) — critical now because these become permanent DB records.
- Consider a stricter mode for the persistent backend: require price history coverage for the full sale window (no fallback allowed), since the backend can re-try the scan later when data may be fresher.

**Detection (warning signs):**
- Players with very high margins (>25%) that have sparse price history.
- Score distribution showing more high-margin outliers than expected.
- OP sales clustered during a time window with no corresponding price history entries.

**Phase mapping:** Phase 1 scorer persistence layer. Must be tracked in the schema from the start.

---

## Minor Pitfalls

---

### Pitfall 10: CSV File Accumulation from Legacy CLI

**What goes wrong:** CONCERNS.md already flags this. The CLI currently creates a timestamped CSV per run. When the CLI becomes a thin API client, it may still output CSVs on each query. Over weeks of use, the project directory fills with hundreds of files.

**Prevention:** Move CSV output to a `results/` subdirectory. Add a `--no-csv` flag. Implement a cleanup of CSVs older than 7 days. Address during CLI refactor phase.

**Phase mapping:** Phase 1 CLI refactor.

---

### Pitfall 11: Magic Number Scoring Config Not Exposed to Backend

**What goes wrong:** `MIN_OP_SALES = 3`, `MIN_SALES_PER_HOUR = 7`, `MIN_LIVE_LISTINGS = 20` are hardcoded in `scorer.py`. The persistent backend scans all players with these fixed thresholds. As market conditions change mid-season (e.g., lower liquidity during off-peak hours), these thresholds may exclude too many or too few players. There is no way to tune them without a code change and server restart.

**Prevention:** Move all scoring thresholds to `config.py`, expose as backend environment variables. Add an admin API endpoint (internal only) to update thresholds without restart. Document the rationale for each threshold value so future changes are informed.

**Phase mapping:** Phase 1 backend config design.

---

### Pitfall 12: Extension Communicating to Backend on localhost

**What goes wrong:** During development, the extension makes API calls to `http://localhost:8000`. When the backend moves to cloud hosting (even just a VPS), the hardcoded localhost URLs fail. If the extension is published to the Chrome Web Store, hardcoded localhost is a policy violation and will be rejected.

**Prevention:** Make the backend URL configurable in the extension's options page from day one. Use `chrome.storage.sync` to persist the configured URL. Default to `http://localhost:8000` for development, but never hardcode it in non-configurable paths.

**Phase mapping:** Phase 1 Chrome extension foundation.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Scheduler setup | APScheduler multi-instance on restart | SQLite job store + `coalesce=True` + `max_instances=1` |
| fut.gg scan at scale | Silent 429 failures + no circuit breaker | Exponential backoff + health metrics in DB |
| Chrome extension background script | MV3 service worker state loss | Alarms API + storage-backed state, no global variables |
| Content script targeting EA Web App | SPA navigation orphaning listeners | MutationObserver for route changes, stable selector targeting |
| EA deploy updates | DOM changes silently breaking automation | Loud failure on missing selectors, dry-run mode |
| DB concurrency | SQLite write contention during scans | WAL mode + small batch commits + busy_timeout |
| Score persistence | False OP from price-at-time fallback | Track `fallback_used_count`, discount fallback-heavy scores |
| Automation volume | EA account ban from machine-speed actions | Randomized delays, session breaks, daily transaction cap, test account |
| SPA UI automation | EA Web App DOM changes post-deploy | Selector version registry, element verification before action |
| Backend URL config | Localhost hardcoded in extension | Configurable URL in extension options from day one |

---

## Sources

- EA FC bot detection patterns: [FutStarz ban avoidance](https://www.futstarz.com/en), [FutBotManager EA ban wave guide](https://futbotmanager.com/ea-ban-wave-avoidance-futbotmanager/), [EA Forums FC25 Unfair Trading Ban](https://forums.ea.com/discussions/fc-25-technical-issues-en/fc-25-unfair-trading-ban/12185942), [Unbanster EA FC 26](https://unbanster.com/get-unbanned-ea-sports-fc/)
- Bot detection mechanics: [GeeTest top strategies for detecting bots in-game 2025](https://www.geetest.com/en/article/top-strategies-for-detecting-a-bot-ingame-in-2025), [Security Boulevard bot detection 101](https://securityboulevard.com/2025/03/bot-detection-101-how-to-detect-bots-in-2025/)
- Chrome MV3 service worker limitations: [Chrome for Developers: migrate to service workers](https://developer.chrome.com/docs/extensions/develop/migrate/to-service-workers), [Chromium extensions group: service worker execution time limits](https://groups.google.com/a/chromium.org/g/chromium-extensions/c/L3EbiNMjIGI)
- Chrome MV3 offscreen documents: [Chrome for Developers: Offscreen Documents in MV3](https://developer.chrome.com/blog/Offscreen-Documents-in-Manifest-v3)
- SPA content script challenges: [Medium: Making Chrome Extension Smart by Supporting SPA websites](https://medium.com/@softvar/making-chrome-extension-smart-by-supporting-spa-websites-1f76593637e8), [Chrome for Developers: content scripts](https://developer.chrome.com/docs/extensions/develop/concepts/content-scripts)
- SQLite WAL concurrency: [SQLite WAL official docs](https://sqlite.org/wal.html), [SkyPilot blog: abusing SQLite for concurrency](https://blog.skypilot.co/abusing-sqlite-to-handle-concurrency/), [SQLite connection pool pitfalls in server deployment](https://www.jtti.cc/supports/3154.html)
- APScheduler + FastAPI pitfalls: [APScheduler GitHub issue: SQLAlchemyJobStore OperationalError](https://github.com/agronholm/apscheduler/issues/499), [Medium: Scheduled Jobs with FastAPI and APScheduler](https://ahaw021.medium.com/scheduled-jobs-with-fastapi-and-apscheduler-5a4c50580b0e)
- CORS in Chrome extensions: [Reintech: CORS in Chrome extensions](https://reintech.io/blog/cors-chrome-extensions)
- Existing codebase concerns: `.planning/codebase/CONCERNS.md` (2026-03-25 audit)
