# Phase 7: Portfolio Management - Research

**Researched:** 2026-03-27
**Domain:** FastAPI endpoint additions + Chrome Extension DOM injection (TypeScript/WXT)
**Confidence:** HIGH — all findings verified against existing codebase; no speculative library choices needed

## Summary

Phase 7 is primarily integration work on two well-established foundations. The backend already has `GET /portfolio` and `DELETE /portfolio/{ea_id}` fully implemented. The extension already has WXT 0.20 + Vitest + discriminated union message protocol running. The core implementation work is: (1) two new FastAPI endpoints (`POST /generate` preview, `POST /confirm` seeding), (2) a new storage item for confirmed portfolio, (3) new message types for portfolio operations, and (4) an overlay panel injected into the EA Web App DOM.

The overlay panel is the hardest part. It injects a collapsible right-sidebar into a live SPA without disrupting EA's Angular/React layout. The safe pattern is absolute/fixed position with high z-index, injected as a shadow DOM or isolated div to prevent style leakage. Confirm and swap operations route through the service worker (content scripts cannot call localhost directly — existing CORS architecture constraint).

The two-step flow (preview → confirm) is the key design decision: `POST /generate` runs the optimizer and returns data without writing to DB; `POST /confirm` takes the previewed list and seeds `portfolio_slots`. Draft state lives only in content script memory — no persistence until Confirm is clicked. On page load, the content script asks the service worker to fetch the current confirmed portfolio from the backend.

**Primary recommendation:** Extend existing patterns exactly — add two backend endpoints in `portfolio.py`, add `portfolioItem` to `storage.ts`, add `PORTFOLIO_*` message types to `messages.ts`, inject overlay panel from `ea-webapp.content.ts`.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

#### Overlay Panel Layout
- **D-01:** Right sidebar, fixed position, ~300-350px wide. Collapsible via a toggle tab on the right edge — slides in/out.
- **D-02:** Styled to match the EA Web App dark theme (dark background, similar fonts/colors). Should feel native, not jarring.
- **D-03:** Each player row shows detailed info: name, OVR rating, position, buy price, OP sell price, margin %, expected profit, OP ratio. Taller rows are acceptable — list is scrollable.

#### Portfolio Generation Flow
- **D-04:** Budget input is a text field at the top of the overlay panel. User types budget and hits Generate.
- **D-05:** Two-step flow: first endpoint returns portfolio preview (no DB seeding), second endpoint confirms and seeds portfolio_slots. User reviews before committing.
- **D-06:** Regeneration replaces the previous portfolio entirely. No append, no confirmation warning — clean slate each time.

#### Player Swap Interaction
- **D-07:** Full flow is: Generate (preview) → swap players freely → Confirm to lock in. Swaps happen in the preview/draft phase before confirmation.
- **D-08:** Auto-accept replacements — when a player is removed, backend returns replacement(s) that are automatically added to the draft. No selection step.
- **D-09:** Instant remove — click X on a player row, immediately removed and replacement appears. No "are you sure?" prompt. Since the portfolio isn't committed yet (pre-confirm), there's no risk.

#### Data Persistence & Sync
- **D-10:** Draft portfolio (pre-confirm) lives in-memory in the content script only. Closing the tab loses the draft. Nothing persisted until the user hits Confirm.
- **D-11:** Confirmed portfolio fetched from backend on EA Web App page load. Content script asks service worker, service worker calls backend. Backend DB is the single source of truth.
- **D-12:** Three distinct overlay states: (1) Empty — budget input + Generate button, (2) Draft — player list with swap/remove + Confirm button, (3) Confirmed — player list with Regenerate option.

### Claude's Discretion
- Panel width and exact toggle button styling
- Loading/spinner states during generate and swap API calls
- How player rows are sorted within the panel (by efficiency, by price, etc.)
- Error handling for API failures (network issues, empty portfolio)
- New message types to add to messages.ts for portfolio communication
- Whether to add a budget summary (used/remaining) to the panel header
- API endpoint design details (request/response shapes beyond what's specified)

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PORT-01 | Backend exposes endpoint to generate OP sell portfolio for a given budget (runs scorer/optimizer) and seeds portfolio_slots | Two new endpoints in `portfolio.py`: `POST /generate` (preview, no DB write) and `POST /confirm` (seeds portfolio_slots). Reuses existing `optimize_portfolio()` and `_build_scored_entry()`. |
| UI-01 | Overlay panel injected into EA Web App showing backend-recommended portfolio (player name, buy price, OP price, margin) | Content script injects fixed-position right sidebar via DOM. Service worker proxies `GET /api/v1/portfolio/confirmed` to retrieve confirmed portfolio on page load. Panel implements 3 states (D-12). |
| UI-03 | User can remove a player from the list and receive replacement player(s) from the backend | Draft swap: content script maintains draft list in memory. On X click, calls backend `DELETE /portfolio/{ea_id}?budget=N` (already implemented), merges returned replacements into draft list. Only committed to DB on Confirm. |
</phase_requirements>

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastAPI | (existing) | Two new portfolio endpoints | Already the app framework |
| SQLAlchemy async | (existing) | `PortfolioSlot` DB writes in confirm endpoint | Already used in all other endpoints |
| WXT | 0.20.20 | Extension build, content script injection | Established in Phase 6 |
| Vitest | 4.1.2 | Extension unit tests | Established in Phase 6 |
| pytest-asyncio | 1.3.0 | Backend endpoint tests | Established in prior phases |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `wxt/utils/storage` | (WXT bundled) | `portfolioItem` for confirmed portfolio persistence | Survives service worker termination, typed API |
| `fakeBrowser` (wxt/testing) | (WXT bundled) | Mock chrome.* in Vitest tests | All extension unit tests |
| httpx AsyncClient | (existing) | Backend integration tests | All FastAPI endpoint tests |
| jsdom | 29.0.1 | DOM testing in Vitest | Testing overlay panel DOM mutations |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Injecting raw DOM | Shadow DOM | Shadow DOM provides true style isolation but requires more setup; a scoped CSS class prefix (`.op-seller-*`) on a fixed-position div is simpler and sufficient given narrow scope |
| In-memory draft | chrome.storage for draft | D-10 is explicit: draft is ephemeral — storage adds unnecessary persistence and complexity |
| New backend confirm endpoint | Reuse `GET /portfolio` + direct DB write from content script | Content scripts cannot write to backend directly without routing through service worker. The confirm endpoint is the right abstraction. |

**Installation:** No new packages required. All dependencies already installed.

## Architecture Patterns

### Recommended Project Structure Additions
```
src/server/api/
└── portfolio.py          — add POST /generate and POST /confirm here (extends existing file)

extension/src/
├── messages.ts           — add PORTFOLIO_GENERATE_REQUEST/RESPONSE, PORTFOLIO_CONFIRM_REQUEST/RESPONSE,
│                            PORTFOLIO_SWAP_REQUEST/RESPONSE, PORTFOLIO_LOAD_REQUEST/RESPONSE
├── storage.ts            — add portfolioItem: ConfirmedPortfolio | null
└── overlay/
    └── panel.ts          — overlay DOM construction and state management (new file)

extension/entrypoints/
└── ea-webapp.content.ts  — import and mount panel, handle portfolio messages

extension/tests/
├── background.test.ts    — extend with portfolio proxy fetch tests
├── content.test.ts       — extend with PORTFOLIO_* message handling tests
└── overlay.test.ts       — new: overlay panel state transitions
```

### Pattern 1: New Backend Endpoints (portfolio.py extension)

**What:** Two endpoints added to the existing `portfolio.py` router. `POST /generate` runs optimizer without seeding. `POST /confirm` seeds `portfolio_slots` from the previewed list.

**When to use:** Follow the exact pattern of `get_portfolio()` — session_factory from `request.app.state`, same query pattern for viable scores, `_build_scored_entry()` to build optimizer input.

```python
# Source: existing portfolio.py patterns (verified in codebase)
from pydantic import BaseModel

class GenerateRequest(BaseModel):
    budget: int

class ConfirmRequest(BaseModel):
    players: list[dict]  # list of {ea_id, buy_price, sell_price} from preview

@router.post("/portfolio/generate")
async def generate_portfolio(request: Request, body: GenerateRequest):
    """Preview-only: runs optimizer, returns player list, does NOT seed portfolio_slots."""
    # Same DB query as get_portfolio(), run optimize_portfolio(), return serialized list
    # No DB writes — pure read + compute
    ...

@router.post("/portfolio/confirm")
async def confirm_portfolio(request: Request, body: ConfirmRequest):
    """Seed portfolio_slots from the confirmed player list.

    Clears existing slots before inserting — D-06: clean slate on regeneration.
    """
    async with session_factory() as session:
        # Clear existing slots
        await session.execute(delete(PortfolioSlot))
        # Insert new slots
        for player in body.players:
            session.add(PortfolioSlot(
                ea_id=player["ea_id"],
                buy_price=player["buy_price"],
                sell_price=player["sell_price"],
                added_at=datetime.utcnow(),
            ))
        await session.commit()
    return {"confirmed": len(body.players)}
```

**CORS note:** The existing `allow_methods=["GET", "POST", "DELETE"]` in `server/main.py` already covers POST. No CORS changes needed.

### Pattern 2: Confirmed Portfolio Storage Item

**What:** Add a typed storage item to `storage.ts` for the confirmed portfolio (persists across service worker termination and tab reloads per D-11).

```typescript
// Source: existing storage.ts pattern (verified in codebase)
export type PortfolioPlayer = {
  ea_id: number;
  name: string;
  rating: number;
  position: string;
  price: number;           // buy_price
  sell_price: number;
  margin_pct: number;
  expected_profit: number;
  op_ratio: number;
};

export type ConfirmedPortfolio = {
  players: PortfolioPlayer[];
  budget: number;
  confirmed_at: string; // ISO timestamp
};

export const portfolioItem = storage.defineItem<ConfirmedPortfolio | null>(
  'local:portfolio',
  { fallback: null },
);
```

### Pattern 3: Message Type Extensions

**What:** Extend the discriminated union in `messages.ts` with portfolio-specific types. Service worker proxies all backend calls (content script cannot call localhost directly).

```typescript
// Source: existing messages.ts pattern + CORS constraint from STATE.md
export type ExtensionMessage =
  | { type: 'PING' }
  | { type: 'PONG' }
  // Portfolio operations
  | { type: 'PORTFOLIO_GENERATE'; budget: number }
  | { type: 'PORTFOLIO_GENERATE_RESULT'; data: PortfolioPlayer[]; budget_used: number; error?: string }
  | { type: 'PORTFOLIO_CONFIRM'; players: PortfolioPlayer[] }
  | { type: 'PORTFOLIO_CONFIRM_RESULT'; confirmed: number; error?: string }
  | { type: 'PORTFOLIO_SWAP'; ea_id: number; budget: number; draft: PortfolioPlayer[] }
  | { type: 'PORTFOLIO_SWAP_RESULT'; replacements: PortfolioPlayer[]; error?: string }
  | { type: 'PORTFOLIO_LOAD' }
  | { type: 'PORTFOLIO_LOAD_RESULT'; portfolio: ConfirmedPortfolio | null };
```

**Key rule:** Every new message type added to the union MUST be handled in every switch statement that uses `assertNever`. Currently only `ea-webapp.content.ts` has such a switch. The background service worker's `onMessage` handler must also be extended.

### Pattern 4: Overlay Panel DOM Injection

**What:** Content script creates a fixed-position div and appends it to `document.body`. Panel is isolated from EA's CSS via scoped class naming (`op-seller-*`). State machine drives three render states (D-12).

**When to use:** Always inject from the content script's `main(ctx)` function. Tear down via `ctx.onInvalidated()` when the script is invalidated.

```typescript
// Source: content script pattern from ea-webapp.content.ts (verified in codebase)
// Inject panel on script load, remove on invalidation
export default defineContentScript({
  matches: ['https://www.ea.com/ea-sports-fc/ultimate-team/web-app/*'],
  runAt: 'document_idle',
  main(ctx) {
    // ... existing listener setup ...

    // Inject overlay
    const panel = createOverlayPanel();
    document.body.appendChild(panel.element);
    ctx.onInvalidated(() => panel.element.remove());

    // Load confirmed portfolio on mount (D-11)
    chrome.runtime.sendMessage({ type: 'PORTFOLIO_LOAD' })
      .then((res: ExtensionMessage) => {
        if (res.type === 'PORTFOLIO_LOAD_RESULT') {
          panel.setConfirmedState(res.portfolio);
        }
      });
  }
});
```

**Panel state machine (D-12):**
- `EMPTY`: show budget input + Generate button
- `DRAFT`: show player list with X buttons + Confirm button (in-memory list only)
- `CONFIRMED`: show player list with Regenerate button (loaded from `portfolioItem`)

### Pattern 5: Service Worker as Portfolio Proxy

**What:** Background service worker handles all new PORTFOLIO_* message types, calls backend, and sends responses back to the content script tab.

```typescript
// Extend background.ts onMessage handler
chrome.runtime.onMessage.addListener((msg: ExtensionMessage, sender, sendResponse) => {
  switch (msg.type) {
    case 'PORTFOLIO_GENERATE':
      handleGenerate(msg.budget).then(sendResponse);
      return true; // async response

    case 'PORTFOLIO_CONFIRM':
      handleConfirm(msg.players).then(async (result) => {
        // On success, persist to storage for page reload (D-11)
        if (!result.error) {
          await portfolioItem.setValue({
            players: msg.players,
            budget: msg.players.reduce((s, p) => s + p.price, 0),
            confirmed_at: new Date().toISOString(),
          });
        }
        sendResponse(result);
      });
      return true;

    case 'PORTFOLIO_SWAP':
      handleSwap(msg.ea_id, msg.budget).then(sendResponse);
      return true;

    case 'PORTFOLIO_LOAD':
      portfolioItem.getValue().then(portfolio =>
        sendResponse({ type: 'PORTFOLIO_LOAD_RESULT', portfolio })
      );
      return true;
    // ... existing cases
  }
});
```

### Pattern 6: Draft Swap Logic (client-side, pre-confirm)

**What:** Swap during draft phase is client-side mutation of the in-memory player array. The backend returns replacements; the content script splices out the removed player and splices in the replacements.

```typescript
// Source: D-07, D-08, D-09 — draft swap is pre-confirm, pure in-memory
function applySwap(
  draft: PortfolioPlayer[],
  removed_ea_id: number,
  replacements: PortfolioPlayer[],
): PortfolioPlayer[] {
  const idx = draft.findIndex(p => p.ea_id === removed_ea_id);
  const next = [...draft];
  next.splice(idx, 1, ...replacements); // replace 1 with N
  return next;
}
```

The backend's `DELETE /portfolio/{ea_id}` is used for swap — but note: in draft phase, the player is NOT in `portfolio_slots` yet. The existing DELETE endpoint requires the player to be in `portfolio_slots` (returns 404 otherwise). This means the swap during draft needs a different backend call.

**Resolution:** Use `POST /portfolio/generate` with the remaining draft budget (current draft minus the removed player) to get replacement candidates, OR add a lightweight `POST /portfolio/swap-preview` that takes current draft ea_ids + budget + removed ea_id and returns replacements without any DB interaction.

Recommended: `POST /portfolio/swap-preview` — accepts `{budget, excluded_ea_ids, freed_budget}`, returns optimizer output for freed budget excluding all excluded players. This keeps draft swaps fully stateless on the backend.

### Anti-Patterns to Avoid
- **Content script calling localhost directly:** CORS architecture constraint — all fetch calls must route through service worker. Verified in STATE.md.
- **sendMessage without `return true`:** Chrome MV3 requires `return true` in message listener to keep the channel open for async responses. Missing this silently drops the response.
- **Mutating `chrome.runtime.onMessage` state in tests:** Use `vi.spyOn(fakeBrowser.runtime.onMessage, 'addListener')` pattern established in Phase 6 tests, never directly manipulate `fakeBrowser.runtime.onMessage.hasListeners`.
- **PortfolioSlot without clearing first:** D-06 requires clean slate on regeneration. The confirm endpoint MUST `delete(PortfolioSlot)` before inserting — otherwise duplicate unique constraint on `ea_id` will throw.
- **Adding message types without updating all switch statements:** The existing `assertNever` in `ea-webapp.content.ts` will cause a TypeScript compile error if new types are added to the union without handling them — this is intentional, but all switch statements must be updated together.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Portfolio persistence across tabs | Custom localStorage serialization | `storage.defineItem<ConfirmedPortfolio>('local:portfolio')` from `wxt/utils/storage` | WXT storage provides typed, promise-based API, survives worker termination |
| Draft state management | Complex state manager / signals | Plain TypeScript variable in closure scope | Draft is ephemeral (D-10) — a simple array variable suffices |
| CSS isolation for overlay | CSS-in-JS library | Scoped class names (`.op-seller-*`) on a single container div | No build complexity, no runtime overhead, adequate isolation |
| DOM cleanup on navigation | Manual removeEventListener chains | `ctx.onInvalidated()` from WXT ContentScriptContext | Already established in Phase 6 pattern |
| Type-safe message protocol | Ad hoc string checks | Discriminated union + `assertNever` — already established in messages.ts | Compile-time safety, established pattern |

**Key insight:** This phase is integration of existing primitives, not new infrastructure. The backend optimizer, WXT storage, and message protocol are all proven — the task is wiring them together with minimal new surface area.

## Common Pitfalls

### Pitfall 1: DELETE /portfolio/{ea_id} Is Wrong for Draft Swaps
**What goes wrong:** The existing DELETE endpoint checks that the player is in `portfolio_slots` and returns 404 if not. During draft phase (pre-confirm), no players are in portfolio_slots. Calling DELETE during draft will always 404.
**Why it happens:** The DELETE endpoint was designed for post-confirm portfolio mutation (Phase 8 automation). Draft swaps are pre-confirm.
**How to avoid:** Add a separate `POST /portfolio/swap-preview` endpoint that accepts current excluded ea_ids + freed budget and runs optimizer without any DB reads/writes to portfolio_slots.
**Warning signs:** Tests that call DELETE with a valid ea_id during draft get 404 responses.

### Pitfall 2: CORS allow_methods Missing PATCH/POST for New Endpoints
**What goes wrong:** `server/main.py` currently has `allow_methods=["GET", "POST", "DELETE"]`. POST is already included — no change needed. But any future PUT or PATCH additions would fail silently in the extension.
**Why it happens:** CORS preflight for new HTTP methods fails when not listed.
**How to avoid:** Verify CORS configuration covers all needed methods before adding new endpoints. For this phase, POST is already covered.
**Warning signs:** Network tab shows CORS preflight 403 for new request types.

### Pitfall 3: sendMessage Return Value Ignored for Async Responses
**What goes wrong:** Content script sends a PORTFOLIO_GENERATE message and the promise never resolves because the service worker's listener doesn't return `true`.
**Why it happens:** Chrome MV3 requires listeners to return `true` synchronously to signal an async response. Without it, `sendResponse` is garbage collected and the promise hangs.
**How to avoid:** Every `chrome.runtime.onMessage.addListener` case that calls `sendResponse` asynchronously must `return true`. This is established in Phase 6 but must be replicated for all new message handlers.
**Warning signs:** `chrome.runtime.sendMessage()` promise never resolves, no error thrown.

### Pitfall 4: Overlay Panel Z-Index Conflicts with EA Modal Dialogs
**What goes wrong:** EA Web App uses z-index values in the hundreds/thousands for modals. An overlay with insufficient z-index appears behind EA dialogs.
**Why it happens:** EA's Angular app has its own z-index stack.
**How to avoid:** Use `z-index: 999999` for the panel container. Use a toggle tab with the same z-index that protrudes slightly beyond the right edge.
**Warning signs:** Panel disappears when EA dialogs open.

### Pitfall 5: SPA Navigation Destroys Injected DOM
**What goes wrong:** EA Web App navigation rebuilds DOM, removing the injected overlay panel.
**Why it happens:** EA's SPA replaces `document.body` children on route changes.
**How to avoid:** The existing `wxt:locationchange` handler in the content script already handles re-initialization. The overlay injection should be called from `initListeners()` (or a new `initOverlay()` function called at same points) so it re-injects after each navigation.
**Warning signs:** Panel disappears when navigating between EA Web App pages.

### Pitfall 6: PortfolioSlot Unique Constraint Violation on Confirm
**What goes wrong:** `portfolio_slots.ea_id` has `unique=True`. If confirm is called twice for the same player, the second call throws `UNIQUE constraint failed: portfolio_slots.ea_id`.
**Why it happens:** The confirm endpoint inserts without first clearing existing slots.
**How to avoid:** The confirm endpoint MUST execute `DELETE FROM portfolio_slots` before inserting the new set. D-06 mandates clean slate — this is both a business requirement and a technical necessity.
**Warning signs:** 500 errors on second Confirm click.

### Pitfall 7: TypeScript Union Exhaustiveness Errors on Build
**What goes wrong:** Adding new types to `ExtensionMessage` causes `tsc --noEmit` compile error in `ea-webapp.content.ts` because the existing `switch/assertNever` pattern is exhaustive.
**Why it happens:** This is intentional safety — but it means all switch statements must be updated together with the union.
**How to avoid:** When adding new message types, update BOTH the switch in `ea-webapp.content.ts` AND any switch in `background.ts` in the same commit.
**Warning signs:** `npm run compile` fails with "Argument of type 'X' is not assignable to parameter of type 'never'".

## Code Examples

### Backend: Generate Endpoint (read-only optimizer run)
```python
# Source: existing portfolio.py patterns (verified against get_portfolio() implementation)
class GenerateRequest(BaseModel):
    budget: int = Field(..., gt=0)

@router.post("/portfolio/generate")
async def generate_portfolio(request: Request, body: GenerateRequest):
    """Preview portfolio for budget — does NOT seed portfolio_slots (D-05)."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # Same subquery pattern as get_portfolio()
        latest_subq = (
            select(PlayerScore.ea_id, func.max(PlayerScore.scored_at).label("max_scored_at"))
            .where(PlayerScore.is_viable == True)
            .group_by(PlayerScore.ea_id)
            .subquery()
        )
        stmt = (
            select(PlayerScore, PlayerRecord)
            .join(latest_subq, ...)
            .join(PlayerRecord, PlayerRecord.ea_id == PlayerScore.ea_id)
            .where(PlayerRecord.is_active == True)
        )
        rows = (await session.execute(stmt)).all()

    scored_list = [_build_scored_entry(s, r) for s, r in rows]
    if not scored_list:
        return {"error": "No viable players yet.", "data": [], ...}

    selected = optimize_portfolio(scored_list, body.budget)
    budget_used = sum(e["buy_price"] for e in selected)
    # serialize same as get_portfolio() ...
    return {"data": data, "count": len(data), "budget": body.budget,
            "budget_used": budget_used, "budget_remaining": body.budget - budget_used}
```

### Backend: Confirm Endpoint (seeds portfolio_slots)
```python
# Source: models_db.py PortfolioSlot definition (verified)
class ConfirmPlayer(BaseModel):
    ea_id: int
    buy_price: int
    sell_price: int

class ConfirmRequest(BaseModel):
    players: list[ConfirmPlayer]

@router.post("/portfolio/confirm")
async def confirm_portfolio(request: Request, body: ConfirmRequest):
    """Seed portfolio_slots from previewed list. Replaces any existing portfolio (D-06)."""
    session_factory = request.app.state.session_factory
    async with session_factory() as session:
        # Clean slate (D-06): remove all existing slots
        await session.execute(delete(PortfolioSlot))
        # Insert new slots
        now = datetime.utcnow()
        for p in body.players:
            session.add(PortfolioSlot(
                ea_id=p.ea_id, buy_price=p.buy_price,
                sell_price=p.sell_price, added_at=now,
            ))
        await session.commit()
    logger.info("Portfolio confirmed: %d players seeded", len(body.players))
    return {"confirmed": len(body.players), "status": "ok"}
```

### Backend: Swap Preview Endpoint (draft-phase, no DB required)
```python
class SwapPreviewRequest(BaseModel):
    freed_budget: int = Field(..., gt=0)
    excluded_ea_ids: list[int]  # current draft players (minus removed one)

@router.post("/portfolio/swap-preview")
async def swap_preview(request: Request, body: SwapPreviewRequest):
    """Return replacement candidates for freed budget, excluding current draft players."""
    # Same DB query as generate, then filter excluded ea_ids
    # Run optimizer on freed_budget only
    # Return replacement list
```

### Extension: Storage Item Addition
```typescript
// Source: extension/src/storage.ts pattern (verified)
export type PortfolioPlayer = {
  ea_id: number; name: string; rating: number; position: string;
  price: number; sell_price: number; margin_pct: number;
  expected_profit: number; op_ratio: number;
};

export type ConfirmedPortfolio = {
  players: PortfolioPlayer[];
  budget: number;
  confirmed_at: string;
};

export const portfolioItem = storage.defineItem<ConfirmedPortfolio | null>(
  'local:portfolio',
  { fallback: null },
);
```

### Extension: DOM Overlay (minimal structure)
```typescript
// Source: D-01, D-02, D-03, D-12 from CONTEXT.md
export function createOverlayPanel() {
  const container = document.createElement('div');
  container.className = 'op-seller-panel';
  // Fixed right sidebar — z-index above EA modals
  container.style.cssText = `
    position: fixed; top: 0; right: 0; width: 320px; height: 100vh;
    background: #1a1a2e; color: #fff; z-index: 999999;
    font-family: inherit; overflow-y: auto;
    transform: translateX(100%); transition: transform 0.2s ease;
  `;

  // Toggle tab on right edge
  const toggle = document.createElement('button');
  toggle.className = 'op-seller-toggle';
  toggle.style.cssText = `
    position: fixed; top: 50%; right: 320px;
    transform: translateY(-50%);
    background: #1a1a2e; color: #fff; border: none;
    cursor: pointer; z-index: 999999; padding: 8px 4px;
  `;
  toggle.textContent = '◀';

  let open = false;
  toggle.addEventListener('click', () => {
    open = !open;
    container.style.transform = open ? 'translateX(0)' : 'translateX(100%)';
    toggle.textContent = open ? '▶' : '◀';
    toggle.style.right = open ? '320px' : '0';
  });

  document.body.appendChild(toggle);

  return {
    element: container,
    toggleElement: toggle,
    // setState(state: 'empty' | 'draft' | 'confirmed', data?: ...) { ... }
  };
}
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Content script calls backend directly | Service worker proxies all backend calls | Phase 6 decision (STATE.md) | All portfolio API calls must go through SW |
| Ad hoc message types | Discriminated union + assertNever | Phase 6 | Must extend union for all new types |
| No portfolio state | PortfolioSlot + portfolio_slots table | Phase 5 | Confirm endpoint just seeds this table |
| No overlay | Phase 7 adds right sidebar overlay | Now | First DOM injection into EA Web App |

## Open Questions

1. **Swap preview endpoint name**
   - What we know: Draft swaps cannot use `DELETE /portfolio/{ea_id}` (player not in DB during draft)
   - What's unclear: Whether to reuse `POST /generate` with `excluded_ea_ids` param, or add a dedicated `POST /portfolio/swap-preview` endpoint
   - Recommendation: Dedicated `POST /portfolio/swap-preview` is cleaner — generate takes a budget for full portfolio, swap-preview takes freed_budget + exclusion list. Keeps API semantics clear.

2. **GET /api/v1/portfolio/confirmed endpoint**
   - What we know: D-11 says "confirmed portfolio fetched from backend on page load" but no such endpoint exists yet — only `GET /portfolio?budget=N` (requires budget param, runs optimizer each time)
   - What's unclear: Should the load-on-page-load path read from `portfolio_slots` directly (simple DB read), or re-run the optimizer?
   - Recommendation: Add `GET /api/v1/portfolio/confirmed` that returns current `portfolio_slots` directly from DB (no optimizer run). Fast and deterministic. The service worker fetches this and caches in `portfolioItem`.

3. **EA Web App dark theme color values**
   - What we know: EA Web App uses dark colors, overlays should match (D-02)
   - What's unclear: Exact hex values without live DevTools inspection
   - Recommendation: Use sensible dark defaults (`#1a1a2e` background, `#e0e0e0` text) — can be tweaked post-implementation via visual inspection. This is Claude's discretion territory.

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Node.js | Extension build (WXT) | Yes | 24.14.0 | — |
| Python 3.12 | Backend endpoints | Yes | 3.12.10 | — |
| Vitest | Extension unit tests | Yes | 4.1.2 (devDep) | — |
| pytest-asyncio | Backend tests | Yes | 1.3.0 (installed) | — |
| WXT | Extension build | Yes | 0.20.20 (devDep) | — |
| jsdom | Overlay DOM tests | Yes | 29.0.1 (devDep) | — |

No missing dependencies. All required tools are available.

## Validation Architecture

### Test Framework

| Property | Value |
|----------|-------|
| Framework (backend) | pytest 9.0.2 + pytest-asyncio 1.3.0 |
| Config file (backend) | `pytest.ini` or inline (check `pyproject.toml`) |
| Quick run command | `pytest tests/test_portfolio.py tests/test_portfolio_swap.py -x` |
| Full suite command | `pytest tests/ -x` |
| Framework (extension) | Vitest 4.1.2 + WxtVitest plugin + jsdom |
| Config file (extension) | `extension/vitest.config.ts` |
| Quick run command | `cd extension && npm test -- --run` |
| Full suite command | `cd extension && npm test -- --run` |

### Phase Requirements → Test Map

| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PORT-01 | POST /generate returns optimizer output without seeding DB | integration | `pytest tests/test_portfolio_generate.py -x` | Wave 0 |
| PORT-01 | POST /confirm seeds portfolio_slots (clean slate) | integration | `pytest tests/test_portfolio_confirm.py -x` | Wave 0 |
| PORT-01 | POST /swap-preview returns replacements excluding draft players | integration | `pytest tests/test_portfolio_swap_preview.py -x` | Wave 0 |
| PORT-01 | GET /portfolio/confirmed returns current portfolio_slots | integration | `pytest tests/test_portfolio_confirmed.py -x` | Wave 0 |
| UI-01 | PORTFOLIO_GENERATE message triggers backend call and returns data | unit | `cd extension && npm test -- --run tests/background.test.ts` | Extend existing |
| UI-01 | PORTFOLIO_LOAD message returns portfolioItem value | unit | `cd extension && npm test -- --run tests/background.test.ts` | Extend existing |
| UI-01 | Overlay panel renders in 3 states (empty/draft/confirmed) | unit | `cd extension && npm test -- --run tests/overlay.test.ts` | Wave 0 |
| UI-03 | Swap removes player from draft and merges replacements | unit | `cd extension && npm test -- --run tests/overlay.test.ts` | Wave 0 |
| UI-03 | PORTFOLIO_SWAP message triggers swap-preview backend call | unit | `cd extension && npm test -- --run tests/background.test.ts` | Extend existing |

### Sampling Rate
- **Per task commit:** `pytest tests/test_portfolio*.py -x && cd extension && npm test -- --run`
- **Per wave merge:** `pytest tests/ -x && cd extension && npm test -- --run`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_portfolio_generate.py` — covers PORT-01 generate endpoint
- [ ] `tests/test_portfolio_confirm.py` — covers PORT-01 confirm endpoint
- [ ] `tests/test_portfolio_swap_preview.py` — covers PORT-01 swap-preview endpoint
- [ ] `tests/test_portfolio_confirmed.py` — covers PORT-01 GET confirmed endpoint
- [ ] `extension/tests/overlay.test.ts` — covers UI-01 panel states, UI-03 swap

Existing test files to extend (not create):
- `extension/tests/background.test.ts` — add PORTFOLIO_* message handler tests
- `extension/tests/content.test.ts` — add PORTFOLIO_* message type handling tests (assertNever coverage)

## Sources

### Primary (HIGH confidence)
- Existing `src/server/api/portfolio.py` — actual endpoint implementations verified by direct read
- Existing `extension/src/messages.ts` — discriminated union pattern verified
- Existing `extension/src/storage.ts` — WXT storage pattern verified
- Existing `extension/entrypoints/ea-webapp.content.ts` — content script lifecycle verified
- Existing `extension/entrypoints/background.ts` — service worker proxy pattern verified
- `.planning/phases/07-portfolio-management/07-CONTEXT.md` — locked decisions D-01 through D-12
- `.planning/STATE.md` — CORS architecture constraint (content scripts cannot call backend directly)
- `src/server/models_db.py` — PortfolioSlot schema verified (unique ea_id constraint)
- `src/server/main.py` — CORS allow_methods=["GET","POST","DELETE"] verified

### Secondary (MEDIUM confidence)
- Chrome MV3 `return true` for async message responses — documented in Phase 6 research artifacts (STATE.md pitfall note)
- z-index strategy for overlay over EA Web App — standard DOM overlay pattern, not EA-specific

### Tertiary (LOW confidence)
- EA Web App dark theme hex values — requires live DevTools inspection to confirm exact values
- EA SPA DOM replacement behavior on navigation — observed pattern, not officially documented

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all libraries already installed and in use
- Architecture: HIGH — patterns verified against existing working code
- Pitfalls: HIGH for backend patterns (verified against existing tests), MEDIUM for overlay behavior (EA Web App DOM not officially documented)
- API endpoint design: HIGH — follows exact pattern of existing portfolio.py endpoints

**Research date:** 2026-03-27
**Valid until:** 2026-05-01 (stable stack — no fast-moving dependencies)
