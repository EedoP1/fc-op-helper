---
phase: quick
plan: 260404-qwj
type: execute
wave: 1
depends_on: []
files_modified:
  - src/server/api/_helpers.py
  - src/server/api/portfolio_read.py
  - extension/src/messages.ts
  - extension/entrypoints/background.ts
  - extension/src/overlay/panel.ts
  - tests/test_portfolio_swap_preview.py
  - extension/tests/overlay.test.ts
  - extension/tests/background.test.ts
autonomous: true
requirements: []
must_haves:
  truths:
    - "Rapid X-clicks on draft players never produce more than TARGET_PLAYER_COUNT players"
    - "Each removal regenerates the entire portfolio from scratch excluding banned IDs"
    - "Every request is idempotent - same banned_ea_ids + budget always returns same result"
  artifacts:
    - path: "src/server/api/portfolio_read.py"
      provides: "generate_portfolio accepts banned_ea_ids and excludes them"
      contains: "banned_ea_ids"
    - path: "extension/src/overlay/panel.ts"
      provides: "X button adds to banned set and re-calls PORTFOLIO_GENERATE"
      contains: "bannedEaIds"
  key_links:
    - from: "extension/src/overlay/panel.ts"
      to: "extension/entrypoints/background.ts"
      via: "PORTFOLIO_GENERATE message with banned_ea_ids"
      pattern: "PORTFOLIO_GENERATE.*banned_ea_ids"
    - from: "extension/entrypoints/background.ts"
      to: "src/server/api/portfolio_read.py"
      via: "POST /api/v1/portfolio/generate with banned_ea_ids in body"
      pattern: "portfolio/generate"
---

<objective>
Replace the per-player swap-preview flow with an idempotent regenerate-with-bans approach.

Purpose: Eliminate the race condition where rapid X-clicks on draft players cause the portfolio to exceed TARGET_PLAYER_COUNT. Instead of each removal doing a partial swap (freed_budget + excluded_ea_ids), each removal triggers a full portfolio regeneration with {budget, banned_ea_ids}, making every request idempotent.

Output: Server generate endpoint accepts banned_ea_ids, extension sends full regenerate on each removal.
</objective>

<execution_context>
@C:/Users/maftu/.claude/get-shit-done/workflows/execute-plan.md
@C:/Users/maftu/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@src/server/api/_helpers.py
@src/server/api/portfolio_read.py
@extension/src/messages.ts
@extension/entrypoints/background.ts
@extension/src/overlay/panel.ts
@extension/tests/overlay.test.ts
@extension/tests/background.test.ts
@tests/test_portfolio_swap_preview.py

<interfaces>
From src/server/api/_helpers.py:
```python
class GenerateRequest(BaseModel):
    budget: int = Field(..., gt=0, description="Total budget in coins")

class SwapPreviewRequest(BaseModel):
    freed_budget: int = Field(..., gt=0)
    excluded_ea_ids: list[int]
    current_count: int = Field(...)
```

From extension/src/messages.ts:
```typescript
| { type: 'PORTFOLIO_GENERATE'; budget: number }
| { type: 'PORTFOLIO_GENERATE_RESULT'; data: PortfolioPlayer[]; budget_used: number; budget_remaining: number; error?: string }
| { type: 'PORTFOLIO_SWAP'; ea_id: number; freed_budget: number; excluded_ea_ids: number[]; current_count: number }
| { type: 'PORTFOLIO_SWAP_RESULT'; replacements: PortfolioPlayer[]; error?: string }
```
</interfaces>
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add banned_ea_ids to generate endpoint, remove swap-preview</name>
  <files>src/server/api/_helpers.py, src/server/api/portfolio_read.py, tests/test_portfolio_swap_preview.py</files>
  <action>
1. In `src/server/api/_helpers.py`: Add `banned_ea_ids: list[int] = Field(default_factory=list, description="EA IDs to exclude from portfolio generation")` to `GenerateRequest`. The `SwapPreviewRequest` class can remain for now (removing it is optional cleanup) but will no longer be used.

2. In `src/server/api/portfolio_read.py` function `generate_portfolio` (line ~130): After building `scored_list`, filter out banned IDs before passing to optimizer:
   ```python
   banned = set(body.banned_ea_ids)
   scored_list = [e for e in scored_list if e["ea_id"] not in banned]
   ```
   Insert this BEFORE the `if not scored_list` check so banned IDs are excluded from optimization.

3. In `tests/test_portfolio_swap_preview.py`: Add a new test `test_generate_with_banned_ea_ids` that:
   - Calls POST /portfolio/generate with `{"budget": 500000, "banned_ea_ids": [some_known_ea_id]}`
   - Asserts the response does not contain the banned ea_id in the data list
   - Asserts the response still returns players (count > 0)
   Also add `test_generate_banned_ea_ids_empty_default` that calls generate without banned_ea_ids field and asserts it works as before (backward compatible).

Do NOT remove the swap-preview endpoint yet -- keep it for backward compatibility during the transition. It will be dead code but harmless.
  </action>
  <verify>
    <automated>cd C:/Users/maftu/Projects/op-seller && python -m pytest tests/test_portfolio_swap_preview.py -x -v --timeout=120</automated>
  </verify>
  <done>POST /portfolio/generate accepts optional banned_ea_ids, filters them from candidates before optimization. Existing tests still pass, new tests verify banned ID exclusion.</done>
</task>

<task type="auto">
  <name>Task 2: Rewire extension to use PORTFOLIO_GENERATE with banned_ea_ids instead of PORTFOLIO_SWAP</name>
  <files>extension/src/messages.ts, extension/entrypoints/background.ts, extension/src/overlay/panel.ts, extension/tests/overlay.test.ts, extension/tests/background.test.ts</files>
  <action>
1. In `extension/src/messages.ts`:
   - Update `PORTFOLIO_GENERATE` message type to: `{ type: 'PORTFOLIO_GENERATE'; budget: number; banned_ea_ids?: number[] }`
   - Keep PORTFOLIO_SWAP and PORTFOLIO_SWAP_RESULT types for now (dead code, safe to remove later).

2. In `extension/entrypoints/background.ts`:
   - Update `handlePortfolioGenerate` (line ~162) to accept and pass `banned_ea_ids` in the POST body:
     ```typescript
     async function handlePortfolioGenerate(budget: number, banned_ea_ids: number[] = []): Promise<ExtensionMessage> {
     ```
     Add `banned_ea_ids` to the JSON body sent to `/api/v1/portfolio/generate`.
   - Update the PORTFOLIO_GENERATE case (line ~46) to pass `msg.banned_ea_ids ?? []` to handlePortfolioGenerate.

3. In `extension/src/overlay/panel.ts` -- this is the critical change:
   - Replace the `removeBtn` click handler (lines 1006-1049). The new behavior:
     a. Add player.ea_id to `removedEaIds` set (already exists).
     b. Remove player from `draftPlayers` immediately (instant UI feedback).
     c. Call `renderPlayerList()` for instant visual update.
     d. Send `PORTFOLIO_GENERATE` message with `{ budget: draftBudget, banned_ea_ids: [...removedEaIds] }`.
     e. On response (`PORTFOLIO_GENERATE_RESULT`): replace `draftPlayers` entirely with `res.data`, update `draftBudgetUsed = res.budget_used`, `draftBudgetRemaining = res.budget_remaining`, call `renderPlayerList()`.
     f. Keep the `swapInFlight` guard to prevent concurrent requests. While a regenerate is in flight, queue the banned ID but skip sending another request. When the in-flight request completes, if new bans were added during flight, fire another regenerate with the full banned set. Simple approach: track a `regenerateQueued` boolean. On click: add to banned, if swapInFlight then set regenerateQueued=true and return. On response complete: if regenerateQueued, reset it and fire another PORTFOLIO_GENERATE.
   - Remove the `freed_budget` / `excluded_ea_ids` / `current_count` logic from the click handler entirely. The server computes everything from scratch given budget + banned IDs.

4. In `extension/tests/overlay.test.ts`:
   - Update the test "clicking X on draft player row sends PORTFOLIO_SWAP message" (line ~231): Change it to assert `PORTFOLIO_GENERATE` is sent with `banned_ea_ids` containing the removed player's ea_id and the original budget.
   - Update the mock response to return `PORTFOLIO_GENERATE_RESULT` with data array instead of `PORTFOLIO_SWAP_RESULT` with replacements.

5. In `extension/tests/background.test.ts`:
   - Update or add a test that PORTFOLIO_GENERATE with banned_ea_ids passes them in the POST body to the backend.
  </action>
  <verify>
    <automated>cd C:/Users/maftu/Projects/op-seller/extension && npm test -- --run 2>&1 | tail -30</automated>
  </verify>
  <done>X button on draft players sends PORTFOLIO_GENERATE with banned_ea_ids, server returns full fresh portfolio. No more PORTFOLIO_SWAP messages in the active code path. Rapid clicks queue regeneration instead of racing.</done>
</task>

</tasks>

<verification>
1. Server: `python -m pytest tests/test_portfolio_swap_preview.py -x -v --timeout=120` -- all tests pass including new banned_ea_ids tests.
2. Extension: `cd extension && npm test -- --run` -- all overlay and background tests pass with new message types.
3. Manual sanity: The swap-preview endpoint still exists but is unused by the extension. No breaking changes for any other consumers.
</verification>

<success_criteria>
- POST /portfolio/generate accepts `banned_ea_ids` optional field, excludes those IDs from optimization
- Extension X button sends PORTFOLIO_GENERATE with accumulated banned IDs + budget
- Server returns a complete fresh portfolio each time (idempotent)
- Concurrent rapid clicks are queued, not raced
- All existing tests pass, new tests cover the banned_ea_ids behavior
</success_criteria>

<output>
After completion, create `.planning/quick/260404-qwj-fix-portfolio-removal-race-condition-ext/260404-qwj-SUMMARY.md`
</output>
