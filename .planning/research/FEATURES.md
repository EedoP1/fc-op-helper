# Feature Research

**Domain:** Chrome Extension for EA Web App OP Sell Automation (buy/list/relist cycle)
**Researched:** 2026-03-26
**Confidence:** MEDIUM — ecosystem well-researched via open-source tools (EasyFUT, MagicBuyer-UT, FUT-Trader, Futinator); EA Web App internals not officially documented

## Context

This research covers ONLY the new Chrome extension milestone (v1.1). The Python backend with scoring, REST API, and CLI are already built (v1.0). The extension adds a UI layer on top of the EA Web App and automates the buy/list/relist cycle using backend-provided recommendations.

The key constraint: all intelligence (scoring, OP prices, portfolio selection) lives in the Python backend. The extension is a thin executor that reads from the backend and drives the EA Web App DOM.

---

## Feature Landscape

### Table Stakes (Users Expect These)

Features every FUT automation extension has. Missing these means the extension is a toy, not a tool.

| Feature | Why Expected | Complexity | Dependencies on Existing |
|---------|--------------|------------|--------------------------|
| Overlay panel injected into EA Web App | Extension must be visible without leaving the web app. All tools (EasyFUT, Futinator, MagicBuyer-UT) inject a sidebar or bottom toolbar | MEDIUM | None — pure extension work |
| Display recommended OP sell list | Core value: show the backend-ranked portfolio with buy price, OP price, margin, expected_profit_per_hour | MEDIUM | Requires /portfolio API endpoint (already built in v1.0) |
| Start/stop automation toggle | User must be able to pause without refreshing the page. Industry standard across all FUT tools | LOW | None |
| Price guard before buy | Before clicking Buy Now, compare live BIN on page against backend buy_price. Skip if market moved up | LOW | Requires buy_price field from /portfolio (already in response) |
| Buy automation (Buy Now at BIN) | Search the transfer market for a target player and click Buy Now when BIN <= buy_price | HIGH | Requires player ea_id and buy_price from backend |
| Auto-list purchased cards at OP price | After buy, navigate to Transfer Targets, set backend-recommended OP price, list | HIGH | Requires sell_price from /portfolio (already in response) |
| Auto-relist expired cards at fresh OP price | All tools (EasyFUT, UT Web Helper, MagicBuyer-UT) offer this. Users with large lists need it daily | MEDIUM | Requires GET /portfolio or /players/{ea_id} for fresh OP price |
| Human-like delays with jitter | EA detects robotic fixed-interval timing. All open-source tools document 300ms–1500ms randomised delays | LOW | None |
| Status display in panel | User needs to know current action (buying / listing / idle / error) and last event | LOW | None |
| Error handling and safe stop | CAPTCHA detection, unknown DOM state, or API failure must stop the automation gracefully | MEDIUM | None |

### Differentiators (Competitive Advantage)

Features that distinguish this tool from generic FUT bots because it is backend-powered.

| Feature | Value Proposition | Complexity | Dependencies on Existing |
|---------|-------------------|------------|--------------------------|
| Backend-driven OP price per player | All other tools use static user-set prices or FUTBIN lookups. This tool gets a live, per-player OP price based on listing-tracking outcome data | LOW (extension side) | Depends on sell_price from /portfolio (already returned) |
| Fresh OP price on every relist cycle | Generic bots relist at the same price as before. This tool fetches a new OP price from backend on each relist — adapts to market drift | LOW (extension side) | Requires /players/{ea_id} or /portfolio endpoint — both already built |
| Budget-aware portfolio from backend | Extension just passes budget to /portfolio. All slot allocation and efficiency sorting already done server-side | LOW (extension side) | Fully backed by existing optimizer and API |
| Activity reporting to backend DB | All buys, lists, and relists written to backend for profit tracking. Generic bots have no profit ledger | MEDIUM | Requires new POST /activity endpoint in backend (new work) |
| Profit analytics queryable via existing CLI | Trading history queryable via CLI and API without a separate dashboard — zero new infrastructure | LOW (extension side) | Requires backend to aggregate /activity records (new work) |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| Headless / background tab operation | "Run overnight without watching" | EA detects hidden and suspended tabs; ban risk spikes sharply. Requires Puppeteer/Selenium-class complexity outside Chrome extension model | Keep the tab visible; document this as an operational requirement |
| Mass buying (many players in parallel) | Maximum coin efficiency | High detection surface — EA rate-limits transfer market API calls; triggers soft ban in minutes of rapid parallel buying | Serialise purchases with delays; quality of picks (OP margin) beats quantity |
| Captcha auto-solving | Uninterrupted automation | External captcha services add dependency and infrastructure; robotic patterns still trigger even after solve | Surface CAPTCHA to user immediately, pause automation, prompt for manual solve |
| Auto-bidding | Covers more cards per coin | Separate logic path, harder to test, lower margin control; bidding introduces cost unpredictability | Buy Now only — OP sell strategy is price-sensitive; known buy cost is essential |
| FUTBIN price cross-reference | "Verify backend prices against market" | FUTBIN was previously removed from this project; adds rate limits, complexity, stale data risk | Backend listing-tracking scoring is the ground truth; no external price lookup needed |
| Web dashboard (this milestone) | Central profit monitoring UI | Out of scope for v1.1; adds infrastructure work before the core automation is validated | API + CLI cover profit visibility for v1.1; dashboard deferred to v2+ |
| Multi-account management | Scale across accounts | Dramatically increases EA ban detection surface and extension complexity | Single-account focus; document explicitly |

---

## Feature Dependencies

```
[Backend API connection]                         (background worker → localhost:8000)
    └──enables──> [Display recommended list]
    └──enables──> [Activity reporting]

[Display recommended list]
    └──requires──> [Backend API connection]

[Price guard]
    └──requires──> [Backend API connection]      (need buy_price from /portfolio)

[Buy automation]
    └──requires──> [Display recommended list]    (need player targets)
    └──requires──> [Price guard]                 (must check BIN before clicking)
    └──requires──> [Human-like delays]
    └──requires──> [Error handling / safe stop]

[Auto-list purchased cards]
    └──requires──> [Buy automation]              (cards must be in Transfer Targets)
    └──requires──> [Backend API connection]      (need sell_price / OP price)

[Auto-relist expired cards]
    └──requires──> [Auto-list purchased cards]   (same DOM path, same price-setting flow)
    └──enhances──> [Fresh OP price on relist]    (one extra API call per relist)

[Activity reporting]
    └──requires──> [Buy automation]              (buys to report)
    └──requires──> [Auto-list purchased cards]   (listings to report)
    └──enhances──> [Profit analytics via CLI]    (backend aggregates the reported data)

[Start/stop toggle] ──controls──> [Buy automation, Auto-list, Auto-relist]
[Error handling / safe stop] ──guards──> [Buy automation, Auto-list, Auto-relist]
```

### Dependency Notes

- **Backend API connection is the critical path.** Everything the extension does depends on communicating with localhost:8000 from the background service worker. This must be proven first before any DOM automation work.
- **Auto-list and auto-relist share the same DOM path.** Both navigate the Transfer List and set a Buy Now price on a player card. Implement as a shared helper to avoid duplication and inconsistency.
- **Activity reporting is a side-effect, not a separate system.** Each buy/list/relist action fires a POST to the backend as a final step. Do not build a separate "reporting queue" for v1.1.
- **Price guard is not optional.** Without it, the extension will overpay on players whose market price moved up between the portfolio refresh and the buy attempt. It is P1, not a nice-to-have.

---

## MVP Definition

### Launch With (v1.1)

Minimum needed to run the buy/list/relist cycle end-to-end with real money on the line.

- [ ] Backend API connection proven: background worker can reach localhost:8000 and deserialize /portfolio response
- [ ] Overlay panel injected into EA Web App showing ranked player list (player name, buy price, OP price, margin)
- [ ] Start/stop automation toggle in the panel
- [ ] Buy automation: search for target player by name/ea_id, verify BIN <= buy_price (price guard), click Buy Now
- [ ] Auto-list: after purchase, navigate to Transfer Targets, set OP price from backend, confirm listing
- [ ] Auto-relist: on Transfer List page, detect expired cards matching the portfolio, fetch fresh OP price, relist
- [ ] Human-like delays with jitter (300ms–1500ms) on every action
- [ ] Error handling: CAPTCHA detection stops automation and shows alert to user; unrecognised DOM state stops cleanly
- [ ] Activity reporting: POST buy/list/relist events to new backend endpoint (player ea_id, price, action, timestamp)
- [ ] Status line in panel: current action, last event, running/stopped/error state

### Add After Validation (v1.x)

Add once the core cycle is confirmed working and tracking real profit data.

- [ ] Session profit summary in extension panel (pull from new backend /profit endpoint) — only useful after enough activity data exists
- [ ] Budget input in extension panel — enables quick budget changes without restarting the backend CLI
- [ ] Sound/visual notification on successful buy — useful when running and glancing away
- [ ] Session stats display (buys this session, coins spent, estimated profit) — add once activity reporting is proven reliable

### Future Consideration (v2+)

Defer until personal tool is validated and paid-product path is clearer.

- [ ] Separate web dashboard for analytics — needs multi-user support and auth infrastructure to justify
- [ ] Cloud-hosted backend with configurable extension URL — requires user accounts, deployment work
- [ ] Multi-account support — significant detection risk and complexity increase

---

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Backend API connection (background worker) | HIGH | LOW | P1 |
| Overlay panel + portfolio display | HIGH | MEDIUM | P1 |
| Start/stop toggle + status display | HIGH | LOW | P1 |
| Price guard | HIGH | LOW | P1 |
| Buy automation | HIGH | HIGH | P1 |
| Auto-list at OP price | HIGH | HIGH | P1 |
| Auto-relist expired | HIGH | MEDIUM | P1 |
| Human-like delays + error handling | HIGH | LOW | P1 |
| Activity reporting to backend | MEDIUM | MEDIUM | P1 |
| Session profit summary in panel | MEDIUM | LOW | P2 |
| Budget input in panel | MEDIUM | LOW | P2 |
| Sound/notification on buy | LOW | LOW | P3 |
| Session stats display | LOW | LOW | P3 |

**Priority key:** P1 = must have for v1.1 launch, P2 = add after validation, P3 = nice to have

---

## Competitor Feature Analysis

| Feature | EasyFUT / MagicBuyer-UT | Futinator / UT Web Helper | This Tool |
|---------|-------------------------|--------------------------|-----------|
| Player target selection | User configures search filters (price, rating, card type) | User sets search params manually | Backend provides ranked portfolio; extension is a pure executor |
| List price | User specifies a fixed price | User specifies a fixed price | Backend provides per-player OP price, refreshed on relist |
| Profit tracking | None | FUT Alert (separate extension) | Backend DB records all activity; CLI queryable from day one |
| Price guard | Implicit (buys only at or below configured price) | Not documented | Explicit: compare BIN against backend buy_price before every click |
| Relist price | Same as original list price (stale) | Same as original | Fresh OP price fetched from backend on each relist cycle |
| Data intelligence | FUTBIN for prices (MagicBuyer-UT), no outcome scoring | Real-time prices from fut.gg | Full listing-tracking outcome scoring; 10-day D-10 window |
| Architecture | Tampermonkey userscript or unpacked extension | Chrome extension | TypeScript Manifest V3 Chrome extension + Python FastAPI backend |

---

## Technical Implementation Notes

These constrain how features must be built — not design choices but hard requirements.

**Cross-origin communication architecture** (HIGH confidence — Chrome MV3 docs):
Content scripts on webapp.ea.com cannot directly fetch localhost:8000. Cross-origin requests from content scripts are blocked under Manifest V3. The required architecture is:
```
Content Script (webapp.ea.com DOM)
    → chrome.runtime.sendMessage
    → Background Service Worker
    → fetch("http://localhost:8000/...")
    → response passes back via message
    → Content Script updates panel / drives DOM
```
`host_permissions: ["http://localhost:8000/*"]` must be declared in manifest.json.

**DOM manipulation vs API interception** (MEDIUM confidence — observed in open-source tools):
Two approaches exist: (1) click real buttons and read/write real input fields (DOM manipulation), or (2) intercept EA's internal XHR/Fetch calls. All open-source FUT tools use DOM manipulation. API interception requires reverse-engineering EA's undocumented, session-authenticated transfer market API. DOM manipulation is the pragmatic choice and the only one with community precedent.

**EA DOM stability** (LOW confidence — no official docs):
EA does not publish the web app's internal structure. The DOM changes annually with each FC title release. Write all CSS selectors defensively with fallbacks. Add DOM existence checks before every interaction. Expect maintenance work each year when EA updates the web app.

**EA ban surface** (MEDIUM confidence — community consensus from forums and open-source tool docs):
- Fixed-interval robotic timing is the primary soft-ban trigger
- Rapid sequential BIN purchases trigger transfer market rate limits
- CAPTCHA appears during high-volume sessions; failing or leaving it unsolved causes a transfer market ban (12–72 hours; escalates with repetition)
- Mitigation: randomised delays per action, serialised (not parallel) execution, visible tab required, automated stop on CAPTCHA detection

---

## Sources

- [EasyFUT GitHub (Kava4)](https://github.com/Kava4/EasyFUT) — content.js, background.js, auto-relist architecture
- [MagicBuyer-UT GitHub (AMINE1921)](https://github.com/AMINE1921/MagicBuyer-UT) — buy/list/relist flow, price guard pattern, periodic relist implementation
- [FUT-Trader GitHub (ckalgos)](https://github.com/ckalgos/FUT-Auto-Buyer) — CAPTCHA handling, delay strategies, settings panel UX
- [Chrome Manifest V3 content scripts](https://developer.chrome.com/docs/extensions/reference/manifest/content-scripts) — cross-origin restriction confirmed HIGH confidence
- [Chrome message passing docs](https://developer.chrome.com/docs/extensions/develop/concepts/messaging) — background worker pattern confirmed HIGH confidence
- [EA soft ban forum thread (FC25)](https://forums.ea.com/discussions/fc-25-technical-issues-en/soft-ban-on-fc-web-app/12364687) — ban detection triggers
- [Futinator Chrome Web Store](https://chrome.google.com/webstore/detail/futinator/ahfgcgcekjnnnacekibcangfooibmehc) — UX pattern: keyboard bindings, buy/bid/list automation
- [FUT Alert Portfolio](https://portfolio.futalert.co.uk/) — profit tracking pattern in the FUT extension ecosystem
- [EA FC rules](https://help.ea.com/en/articles/ea-sports-fc/fc-rules/) — confirms third-party automation violates ToS

---
*Feature research for: Chrome Extension — EA Web App OP Sell Automation (v1.1 milestone)*
*Researched: 2026-03-26*
