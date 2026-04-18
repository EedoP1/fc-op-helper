---
phase: quick-260418-kzb
plan: 01
type: execute
wave: 1
depends_on: []
files_modified:
  - src/server/api/portfolio_read.py
  - tests/test_portfolio_card_types.py
  - extension/src/messages.ts
  - extension/entrypoints/background.ts
  - extension/src/overlay/panel.ts
  - extension/tests/overlay.test.ts
autonomous: true
requirements:
  - KZB-01  # Backend: GET /api/v1/portfolio/card-types returns [{card_type, count}] DESC from active players
  - KZB-02  # Extension: panel fetches card-types on renderEmpty mount and populates excludeSelect dynamically
  - KZB-03  # Message channel: new PORTFOLIO_CARD_TYPES_REQUEST/RESULT + background handler
must_haves:
  truths:
    - "GET /api/v1/portfolio/card-types returns 200 with a non-empty JSON array when the players table has active rows"
    - "Each item in the response is shaped {card_type: string, count: int} and the array is sorted by count DESC"
    - "Counts reflect only PlayerRecord rows where is_active=True (Icons/UT Heroes already absent from active set)"
    - "On extension panel mount (renderEmpty), the exclude-card-types <select> is populated from the backend response, not the hardcoded list"
    - "When the backend fetch fails, the dropdown falls back to empty (+ Add exclusion... placeholder remains) and the Generate flow still works"
    - "Clicking a dropdown option still adds a red tag; clicking the tag still removes it; Generate still sends exclude_card_types array to backend (unchanged UX)"
  artifacts:
    - path: "src/server/api/portfolio_read.py"
      provides: "GET /portfolio/card-types route handler returning sorted [{card_type, count}] from active PlayerRecord rows"
      contains: "async def get_card_types"
    - path: "tests/test_portfolio_card_types.py"
      provides: "Integration test asserting endpoint returns list-of-{card_type,count} sorted DESC with seeded players"
      contains: "async def test_card_types_returns_sorted_counts"
    - path: "extension/src/messages.ts"
      provides: "PORTFOLIO_CARD_TYPES_REQUEST and PORTFOLIO_CARD_TYPES_RESULT variants in the ExtensionMessage union"
      contains: "PORTFOLIO_CARD_TYPES_REQUEST"
    - path: "extension/entrypoints/background.ts"
      provides: "handlePortfolioCardTypes() proxying GET /api/v1/portfolio/card-types + switch case wired"
      contains: "handlePortfolioCardTypes"
    - path: "extension/src/overlay/panel.ts"
      provides: "Dynamic population of excludeSelect from runtime message; removal of hardcoded CARD_TYPES list"
      contains: "PORTFOLIO_CARD_TYPES_REQUEST"
    - path: "extension/tests/overlay.test.ts"
      provides: "Test verifying excludeSelect options come from mocked PORTFOLIO_CARD_TYPES_RESULT"
      contains: "populates exclude dropdown from card-types message"
  key_links:
    - from: "src/server/api/portfolio_read.py GET /portfolio/card-types"
      to: "PlayerRecord table (players)"
      via: "SQLAlchemy select(card_type, func.count()).where(is_active==True).group_by(card_type).order_by(count DESC)"
      pattern: "group_by\\(PlayerRecord.card_type\\)"
    - from: "extension/src/overlay/panel.ts renderEmpty()"
      to: "background.ts handlePortfolioCardTypes"
      via: "chrome.runtime.sendMessage({ type: 'PORTFOLIO_CARD_TYPES_REQUEST' })"
      pattern: "PORTFOLIO_CARD_TYPES_REQUEST"
    - from: "extension/entrypoints/background.ts handlePortfolioCardTypes"
      to: "http://localhost:8000/api/v1/portfolio/card-types"
      via: "fetch GET"
      pattern: "/api/v1/portfolio/card-types"
---

<objective>
Replace the 37-entry hardcoded CARD_TYPES list in the overlay panel with a live read from a new `GET /api/v1/portfolio/card-types` endpoint so the "Exclude card types" dropdown always reflects what is actually in the active `players` table (~60 distinct card_types).

Purpose: Today the dropdown is missing ~23 of the 60 active rarities (TOTS, TOTY, POTM variants, etc.), so users can't exclude them when generating a portfolio. Backend already supports `exclude_card_types` end-to-end (portfolio_read.py:162 passes it to optimize_portfolio) — the only gap is discovery.

Output: New backend route + Pydantic-free JSON response, new ExtensionMessage variant, new background handler, dynamic population in panel.ts (hardcoded list deleted), plus one backend test and one extension test. No persistence, no caching beyond per-panel-lifetime.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@.planning/STATE.md
@CLAUDE.md

# Existing backend surface (GET routes already wired through /api/v1 via portfolio.py)
@src/server/api/portfolio_read.py
@src/server/api/portfolio.py
@src/server/api/players.py
@src/server/models_db.py

# Extension message pipeline + hardcoded list to kill
@extension/src/messages.ts
@extension/entrypoints/background.ts
@extension/src/overlay/panel.ts

# Test patterns to mirror
@tests/test_portfolio_generate.py
@tests/test_api.py
@extension/tests/overlay.test.ts
@extension/tests/background.test.ts

<interfaces>
<!-- Key types and patterns the executor needs. Extracted from codebase — do not re-explore. -->

Router mount (src/server/api/portfolio.py): portfolio_read.router is included under prefix /api/v1 via portfolio.py → GET added in portfolio_read.py auto-exposes as /api/v1/portfolio/card-types.

PlayerRecord fields (src/server/models_db.py:8-30) — relevant only:
```python
class PlayerRecord(Base):
    __tablename__ = "players"
    ea_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    card_type: Mapped[str] = mapped_column(String(50))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # ... (other columns irrelevant to this endpoint)
```

Existing GET pattern to copy (portfolio_read.py:19 / players.py:36-58):
```python
@router.get("/portfolio/confirmed")
async def get_confirmed_portfolio(request: Request):
    sf = _read_session_factory(request)
    async with sf() as session:
        stmt = select(...).where(PlayerRecord.is_active == True)  # noqa: E712
        result = await session.execute(stmt)
        rows = result.all()
    return {...}
```

Helper (src/server/api/_helpers.py): `_read_session_factory(request)` returns the read session factory from app.state — use this, do not touch app.state directly.

Existing ExtensionMessage variants using the same request/result pattern (extension/src/messages.ts:125-130):
```ts
| { type: 'DASHBOARD_STATUS_REQUEST' }
| { type: 'DASHBOARD_STATUS_RESULT'; data: DashboardData | null; error?: string }
```

Existing background handler pattern to copy (background.ts:361-373):
```ts
async function handleDashboardStatus(): Promise<ExtensionMessage> {
  try {
    const resp = await fetch(`${BACKEND_URL}/api/v1/portfolio/status`);
    if (!resp.ok) return { type: 'DASHBOARD_STATUS_RESULT', data: null, error: `Backend returned ${resp.status}` };
    const data = await resp.json();
    return { type: 'DASHBOARD_STATUS_RESULT', data };
  } catch (err) {
    return { type: 'DASHBOARD_STATUS_RESULT', data: null, error: err instanceof Error ? err.message : 'Unknown' };
  }
}
```

Panel insertion site (extension/src/overlay/panel.ts:806-888):
- renderEmpty() builds the excludeSelect synchronously at lines 812-852.
- Currently iterates `CARD_TYPES.forEach(ct => { ... excludeSelect.appendChild(opt); })`.
- `excludeSelect.addEventListener('change', ...)` and tag rendering (lines 860-888) do NOT depend on when options are appended — safe to append async.
- `draftExcludedCardTypes` (line 122) is preserved across regenerates; fetch-on-mount does not disturb this.

Test app factory (tests/test_api.py:45): `make_test_app(session_factory)` builds a FastAPI app and wires app.state — use `app.include_router(portfolio_router)` from `src.server.api.portfolio` to get the /api/v1 prefix.
</interfaces>

</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Add GET /api/v1/portfolio/card-types endpoint + integration test</name>
  <files>
    src/server/api/portfolio_read.py
    tests/test_portfolio_card_types.py
  </files>
  <behavior>
    - With 0 active PlayerRecord rows, endpoint returns 200 with `[]` (empty array, not an error object).
    - With seeded rows of mixed card_types across active=True/False, response is a list of {card_type: str, count: int}.
    - Only active rows are counted (is_active=True). Inactive rows MUST NOT appear even if their card_type is unique.
    - Results sorted by count DESC. When seeded with e.g. 3×"Team of the Season", 2×"Rare", 1×"TOTY ICON" → first entry is "Team of the Season" with count 3, last is "TOTY ICON" with count 1.
    - Status is always 200 on success (no Pydantic body; plain list return — FastAPI serializes to JSON array).
  </behavior>
  <action>
    Add a new handler to `src/server/api/portfolio_read.py` (right after `get_confirmed_portfolio`, before `get_actions_needed`) that returns active card_type counts.

    Implementation (copy the style already used in this file — `_read_session_factory`, `async with sf() as session`, SQL via SQLAlchemy select+group_by):
    ```python
    @router.get("/portfolio/card-types")
    async def get_card_types(request: Request):
        """Return distinct card_types from active PlayerRecord rows with counts.

        Used by the extension overlay to populate the "Exclude card types" dropdown
        dynamically instead of a hardcoded list. Sorted by count DESC so the most
        common rarities surface first.

        Returns:
            List of {card_type: str, count: int}, sorted by count DESC. Empty list
            if no active players exist.
        """
        sf = _read_session_factory(request)
        async with sf() as session:
            stmt = (
                select(PlayerRecord.card_type, func.count(PlayerRecord.ea_id).label("count"))
                .where(PlayerRecord.is_active == True)  # noqa: E712
                .group_by(PlayerRecord.card_type)
                .order_by(func.count(PlayerRecord.ea_id).desc())
            )
            result = await session.execute(stmt)
            rows = result.all()
        return [{"card_type": r.card_type, "count": r.count} for r in rows]
    ```

    `select` and `func` are already imported at the top of portfolio_read.py (line 6) — do not re-import. `PlayerRecord` is already imported at line 8 — do not re-import.

    Create `tests/test_portfolio_card_types.py` mirroring the fixture pattern from `tests/test_portfolio_generate.py`:

    ```python
    """Integration tests for GET /api/v1/portfolio/card-types endpoint."""
    from __future__ import annotations

    import pytest
    from datetime import datetime

    from httpx import AsyncClient, ASGITransport

    from src.server.db import create_engine_and_tables
    from src.server.models_db import PlayerRecord
    from src.server.api.portfolio import router as portfolio_router
    from tests.test_api import make_test_app


    @pytest.fixture
    async def db():
        engine, session_factory = await create_engine_and_tables("sqlite+aiosqlite:///:memory:")
        yield engine, session_factory
        await engine.dispose()


    async def _seed(session_factory, rows):
        """rows = [(ea_id, card_type, is_active)]"""
        now = datetime.utcnow()
        async with session_factory() as session:
            for ea_id, card_type, is_active in rows:
                session.add(PlayerRecord(
                    ea_id=ea_id, name=f"P{ea_id}", rating=85, position="ST",
                    nation="Brazil", league="LaLiga", club="Real Madrid",
                    card_type=card_type, scan_tier="normal",
                    last_scanned_at=now, is_active=is_active,
                    listing_count=30, sales_per_hour=10.0,
                ))
            await session.commit()


    async def test_card_types_returns_sorted_counts(db):
        """Endpoint returns list of {card_type, count} sorted by count DESC from active rows only."""
        _, session_factory = db
        await _seed(session_factory, [
            (3001, "Team of the Season", True),
            (3002, "Team of the Season", True),
            (3003, "Team of the Season", True),
            (3004, "Rare", True),
            (3005, "Rare", True),
            (3006, "TOTY ICON", True),
            (3007, "Inactive Type", False),  # must be excluded
        ])
        app = make_test_app(session_factory)
        app.include_router(portfolio_router)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/portfolio/card-types")

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body, list)
        # Shape check
        for entry in body:
            assert set(entry.keys()) == {"card_type", "count"}
            assert isinstance(entry["card_type"], str)
            assert isinstance(entry["count"], int)
        # Inactive-row card_type must not appear
        assert "Inactive Type" not in [e["card_type"] for e in body]
        # DESC-by-count ordering
        counts = [e["count"] for e in body]
        assert counts == sorted(counts, reverse=True)
        # First entry is the most frequent
        assert body[0] == {"card_type": "Team of the Season", "count": 3}
        # Total: 3 distinct active card_types
        assert len(body) == 3


    async def test_card_types_empty_db_returns_empty_list(db):
        """Empty DB → 200 with empty list (not an error object)."""
        _, session_factory = db
        app = make_test_app(session_factory)
        app.include_router(portfolio_router)

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            resp = await client.get("/api/v1/portfolio/card-types")

        assert resp.status_code == 200
        assert resp.json() == []
    ```

    Do NOT add a Pydantic response_model — the existing endpoints in portfolio_read.py return raw dicts/lists and we match that style.
    Do NOT touch `optimize_portfolio` or any scoring code. This is a pure read endpoint over the `players` table.
  </action>
  <verify>
    <automated>python -m pytest tests/test_portfolio_card_types.py -x -v</automated>
  </verify>
  <done>
    - `tests/test_portfolio_card_types.py` passes (2 tests).
    - `GET /api/v1/portfolio/card-types` returns a sorted list of active card_type counts.
    - Running the live server and hitting `curl http://localhost:8000/api/v1/portfolio/card-types | jq 'length'` returns ~60 (current count of distinct active card_types in production DB).
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Add PORTFOLIO_CARD_TYPES message variants + background handler</name>
  <files>
    extension/src/messages.ts
    extension/entrypoints/background.ts
  </files>
  <behavior>
    - ExtensionMessage union has `{ type: 'PORTFOLIO_CARD_TYPES_REQUEST' }` and `{ type: 'PORTFOLIO_CARD_TYPES_RESULT'; data: Array<{ card_type: string; count: number }> | null; error?: string }`.
    - background.ts switch on msg.type has a new case `'PORTFOLIO_CARD_TYPES_REQUEST'` that calls `handlePortfolioCardTypes()` and returns `true` (async response).
    - `handlePortfolioCardTypes()` GETs `http://localhost:8000/api/v1/portfolio/card-types`, returns `{ type: 'PORTFOLIO_CARD_TYPES_RESULT', data: <list>}` on 200, or `{ ..., data: null, error: 'Backend returned <N>' }` on non-ok or thrown fetch.
  </behavior>
  <action>
    In `extension/src/messages.ts`, extend the `ExtensionMessage` union. Add immediately after the existing `DASHBOARD_STATUS_*` block (around line 127) — same request/result pair style:

    ```ts
    // Card types for the exclude dropdown (panel -> service worker -> backend)
    | { type: 'PORTFOLIO_CARD_TYPES_REQUEST' }
    | { type: 'PORTFOLIO_CARD_TYPES_RESULT'; data: Array<{ card_type: string; count: number }> | null; error?: string }
    ```

    In `extension/entrypoints/background.ts`:

    1. Add a new `case 'PORTFOLIO_CARD_TYPES_REQUEST':` branch inside the existing `chrome.runtime.onMessage.addListener` switch (place it near the other PORTFOLIO_* cases, e.g. right after `case 'PORTFOLIO_LOAD':` at line 63-65):
    ```ts
    case 'PORTFOLIO_CARD_TYPES_REQUEST':
      handlePortfolioCardTypes().then(sendResponse);
      return true;
    ```

    2. Add the handler implementation alongside `handleDashboardStatus` (below it is fine). Mirror its error-handling shape exactly:
    ```ts
    /**
     * Fetch distinct active card_types from the backend for the exclude dropdown.
     * GET /api/v1/portfolio/card-types → PORTFOLIO_CARD_TYPES_RESULT
     */
    async function handlePortfolioCardTypes(): Promise<ExtensionMessage> {
      try {
        const resp = await fetch(`${BACKEND_URL}/api/v1/portfolio/card-types`);
        if (!resp.ok) {
          return { type: 'PORTFOLIO_CARD_TYPES_RESULT', data: null, error: `Backend returned ${resp.status}` };
        }
        const data = await resp.json();
        return { type: 'PORTFOLIO_CARD_TYPES_RESULT', data };
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Unknown error';
        return { type: 'PORTFOLIO_CARD_TYPES_RESULT', data: null, error: message };
      }
    }
    ```

    3. Because `ExtensionMessage` is a discriminated union, TypeScript will force every consumer switch (content scripts, etc.) to acknowledge the new types. Search for any `default:` branches that already swallow "types not handled here" (background.ts:130-135 already does this with a comment/return false). If a content-script switch uses `assertNever`, add explicit `case 'PORTFOLIO_CARD_TYPES_REQUEST':` and `case 'PORTFOLIO_CARD_TYPES_RESULT':` branches that `return false` — do NOT weaken assertNever; follow the existing Phase 07 pattern (STATE.md: "content script returns false for those types to maintain exhaustive switch").
       - To locate consumers: check `extension/src/automation.ts`, `extension/src/trade-observer.ts`, `entrypoints/content.ts` for `switch (msg.type)` blocks. Add pass-through cases only where the compiler complains.

    Do NOT change the existing `PORTFOLIO_GENERATE` body shape or `exclude_card_types` wire format — that remains the existing comma-joined-free array-of-strings the panel already sends.
  </action>
  <verify>
    <automated>cd extension && npx vitest run tests/background.test.ts</automated>
  </verify>
  <done>
    - `extension/` TypeScript compiles (`npx tsc --noEmit` from extension/ shows no new errors; any newly required exhaustive-switch cases added).
    - Existing `extension/tests/background.test.ts` still passes (no regression from new union variant).
    - New message types are visible from `import { ExtensionMessage } from '../src/messages'`.
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 3: Wire panel.ts renderEmpty() to fetch card types on mount + extension test</name>
  <files>
    extension/src/overlay/panel.ts
    extension/tests/overlay.test.ts
  </files>
  <behavior>
    - When `renderEmpty()` runs, it sends `{ type: 'PORTFOLIO_CARD_TYPES_REQUEST' }` via `chrome.runtime.sendMessage`.
    - On `PORTFOLIO_CARD_TYPES_RESULT` with `data` array, each `card_type` becomes an `<option>` appended to `excludeSelect` after the `+ Add exclusion...` default option.
    - On error / null data, the select retains only the default option (no crash, no toast).
    - The hardcoded `CARD_TYPES` array is deleted from panel.ts.
    - Test: mocks `chrome.runtime.sendMessage` so that `PORTFOLIO_CARD_TYPES_REQUEST` resolves to `{ data: [{card_type: "TOTY ICON", count: 1}, {card_type: "Team of the Season", count: 5}] }`; after `createOverlayPanel()` + await microtask, the DOM exposes both strings as `<option>` values in the select.
  </behavior>
  <action>
    In `extension/src/overlay/panel.ts`, inside `renderEmpty()` (lines 779-888):

    1. **Delete the hardcoded `CARD_TYPES` array** (lines 812-824 inclusive, including trailing blank line).
    2. **Keep** the `const excludedTypes: Set<string> = new Set();` declaration and the `excludeRow`, `excludeSelect`, `defaultOpt` setup exactly as-is, but **remove** the `CARD_TYPES.forEach(...)` loop that appends options (currently lines 845-850).
    3. After `excludeRow.appendChild(excludeSelect); container.appendChild(excludeRow);` (line 851-852), insert the async population block:

    ```ts
    // Populate dropdown from backend (replaces old hardcoded CARD_TYPES list).
    // Fetch is best-effort: if it fails, the select stays empty (just the placeholder)
    // so Generate still works without exclusions.
    chrome.runtime.sendMessage({ type: 'PORTFOLIO_CARD_TYPES_REQUEST' } satisfies ExtensionMessage)
      .then((res: ExtensionMessage) => {
        if (res?.type !== 'PORTFOLIO_CARD_TYPES_RESULT' || !res.data) return;
        for (const entry of res.data) {
          const opt = document.createElement('option');
          opt.value = entry.card_type;
          opt.textContent = entry.card_type;
          excludeSelect.appendChild(opt);
        }
      })
      .catch(() => {
        // Silent fallback — dropdown just stays empty. No toast; this is non-essential UX.
      });
    ```

    Do NOT change:
    - `excludedTypes` Set or `draftExcludedCardTypes` (panel.ts:122) — those remain the source of truth for what the user picked.
    - `renderExcludeTags()`, the `change` handler, or the Generate click handler (all remain as-is; they operate on `excludedTypes` which is unchanged).
    - The async microtask ordering: `excludeSelect.addEventListener('change', ...)` must still be attached synchronously (it already is, on line 882 — keep it).

    In `extension/tests/overlay.test.ts`, add a new test inside the existing `describe('overlay panel', ...)` block. Copy the existing mockSendMessage pattern (the `beforeEach` already wires a default mock — extend it for the new message type, then add an assertion-focused test):

    ```ts
    it('populates exclude dropdown from PORTFOLIO_CARD_TYPES_RESULT on mount', async () => {
      mockSendMessage.mockImplementation((msg: any) => {
        if (msg.type === 'PORTFOLIO_CARD_TYPES_REQUEST') {
          return Promise.resolve({
            type: 'PORTFOLIO_CARD_TYPES_RESULT',
            data: [
              { card_type: 'Team of the Season', count: 54 },
              { card_type: 'TOTY ICON', count: 1 },
            ],
          });
        }
        if (msg.type === 'ACTIONS_NEEDED_REQUEST') {
          return Promise.resolve({ type: 'ACTIONS_NEEDED_RESULT', data: { actions: [], summary: { to_buy: 0, to_list: 0, to_relist: 0, waiting: 0 } } });
        }
        return Promise.resolve(undefined);
      });

      const panel = createOverlayPanel();
      document.body.appendChild(panel.container);
      // renderEmpty runs in the factory constructor (panel.ts:1912), so the
      // PORTFOLIO_CARD_TYPES_REQUEST fires immediately. Await microtasks to let
      // the .then() populate the select.
      await new Promise((r) => setTimeout(r, 10));

      const select = panel.container.querySelector('select') as HTMLSelectElement;
      expect(select).toBeTruthy();
      const optionValues = Array.from(select.options).map(o => o.value);
      // Default placeholder + 2 fetched card_types
      expect(optionValues).toContain('');
      expect(optionValues).toContain('Team of the Season');
      expect(optionValues).toContain('TOTY ICON');
      // Hardcoded list is gone — "FUT Birthday" (from old list) must NOT appear
      expect(optionValues).not.toContain('FUT Birthday');
    });

    it('leaves exclude dropdown empty when PORTFOLIO_CARD_TYPES_RESULT returns error', async () => {
      mockSendMessage.mockImplementation((msg: any) => {
        if (msg.type === 'PORTFOLIO_CARD_TYPES_REQUEST') {
          return Promise.resolve({ type: 'PORTFOLIO_CARD_TYPES_RESULT', data: null, error: 'Backend down' });
        }
        if (msg.type === 'ACTIONS_NEEDED_REQUEST') {
          return Promise.resolve({ type: 'ACTIONS_NEEDED_RESULT', data: { actions: [], summary: { to_buy: 0, to_list: 0, to_relist: 0, waiting: 0 } } });
        }
        return Promise.resolve(undefined);
      });

      const panel = createOverlayPanel();
      document.body.appendChild(panel.container);
      await new Promise((r) => setTimeout(r, 10));

      const select = panel.container.querySelector('select') as HTMLSelectElement;
      expect(select).toBeTruthy();
      // Only the default "+ Add exclusion..." option
      expect(select.options.length).toBe(1);
      expect(select.options[0].value).toBe('');
    });
    ```

    These two tests mirror the mock style in overlay.test.ts:54-73 (mockSendMessage.mockImplementation with per-msg-type branching).
  </action>
  <verify>
    <automated>cd extension && npx vitest run tests/overlay.test.ts</automated>
  </verify>
  <done>
    - The hardcoded `CARD_TYPES` array no longer exists in `extension/src/overlay/panel.ts` (`grep -n "CARD_TYPES" extension/src/overlay/panel.ts` returns nothing).
    - New overlay tests pass; existing overlay tests still pass.
    - Manual smoke: loading the extension against a running backend populates the dropdown with ~60 card types (matches `curl /api/v1/portfolio/card-types | jq 'length'`).
    - Generate + exclusion flow still works end-to-end: user picks a card_type → tag appears → Generate sends it in the `exclude_card_types` body (unchanged from today).
  </done>
</task>

</tasks>

<verification>
Run both test suites end-to-end:
- `python -m pytest tests/test_portfolio_card_types.py tests/test_portfolio_generate.py -x -v`  (new + existing portfolio backend suite still green)
- `cd extension && npx vitest run tests/overlay.test.ts tests/background.test.ts`  (new + existing extension suites still green)

Live smoke (manual, post-implementation):
- Start server: `python -m src.server.main` (or your usual command)
- `curl -s http://localhost:8000/api/v1/portfolio/card-types | jq 'length'` returns ~60 (current active card_type count).
- `curl -s http://localhost:8000/api/v1/portfolio/card-types | jq '.[0:3]'` shows objects with `card_type` and `count` keys, most-frequent first.
- Open EA Web App with the extension loaded → click OP Seller toggle → the "Exclude card types" dropdown now lists all ~60 rarities (including TOTS, TOTY, POTM variants that were missing before). Select "Team of the Season" → red tag appears → click Generate → backend receives `exclude_card_types: ["Team of the Season"]` and produces a portfolio with zero TOTS cards (verify in dev console Network tab).
</verification>

<success_criteria>
- [ ] `GET /api/v1/portfolio/card-types` returns 200 with sorted `[{card_type, count}]` from active `PlayerRecord` rows.
- [ ] `tests/test_portfolio_card_types.py` asserts: shape, DESC ordering, inactive-row exclusion, empty-DB → empty list.
- [ ] `PORTFOLIO_CARD_TYPES_REQUEST` / `PORTFOLIO_CARD_TYPES_RESULT` variants exist in `ExtensionMessage` union.
- [ ] `background.ts` routes the new request to `handlePortfolioCardTypes()` and returns the result.
- [ ] `panel.ts renderEmpty()` populates `excludeSelect` from the runtime message; the 37-entry hardcoded `CARD_TYPES` array is deleted.
- [ ] On fetch error, the dropdown still renders with only the `+ Add exclusion...` placeholder — no crash, no toast, Generate button still usable.
- [ ] Extension test verifies: dropdown contains both seeded card_types from the mocked message; hardcoded "FUT Birthday" is NOT present.
- [ ] Existing portfolio generation flow (send `exclude_card_types` in POST /portfolio/generate body) is unchanged — no backend filter semantics touched.
</success_criteria>

<output>
After completion, create `.planning/quick/260418-kzb-add-get-api-v1-portfolio-card-types-endp/260418-kzb-SUMMARY.md`
</output>
