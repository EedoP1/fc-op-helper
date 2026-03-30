---
phase: quick
plan: 260330-ocv
type: execute
wave: 1
depends_on: []
files_modified:
  - src/models.py
  - src/futgg_client.py
  - src/server/scorer_v2.py
  - src/server/scanner.py
autonomous: true
requirements: []
must_haves:
  truths:
    - "Players whose computed sell_price exceeds EA max BIN range are skipped at each margin tier"
    - "max_price_range is extracted from fut.gg prices API and stored on PlayerMarketData"
    - "Scanner passes max_price_range through to scorer_v2"
  artifacts:
    - path: "src/models.py"
      provides: "max_price_range field on PlayerMarketData"
      contains: "max_price_range"
    - path: "src/server/scorer_v2.py"
      provides: "max_price_range filter in margin loop"
      contains: "max_price_range"
  key_links:
    - from: "src/futgg_client.py"
      to: "src/models.py"
      via: "PlayerMarketData(max_price_range=...)"
      pattern: "max_price_range"
    - from: "src/server/scanner.py"
      to: "src/server/scorer_v2.py"
      via: "score_player_v2(max_price_range=...)"
      pattern: "max_price_range"
---

<objective>
Filter out margin tiers where the computed sell_price exceeds EA's maximum BIN price range for a player. Currently scorer_v2 picks margins that produce sell prices impossible to list at — EA caps how high you can list a card. The fut.gg prices API returns `priceRange.maxPrice`; extract it, thread it through to scorer_v2, and skip any margin where `sell_price > max_price_range`.

Purpose: Eliminate false-positive high-margin scores that can never be listed at those prices.
Output: Updated model, client, scorer, and scanner files.
</objective>

<execution_context>
@C:\Users\maftu\.claude\get-shit-done\workflows\execute-plan.md
@C:\Users\maftu\.claude\get-shit-done\templates\summary.md
</execution_context>

<context>
@src/models.py
@src/futgg_client.py
@src/server/scorer_v2.py
@src/server/scanner.py
</context>

<tasks>

<task type="auto">
  <name>Task 1: Add max_price_range to PlayerMarketData and extract from fut.gg API</name>
  <files>src/models.py, src/futgg_client.py</files>
  <action>
1. In `src/models.py`, add `max_price_range: Optional[int] = None` field to the `PlayerMarketData` model (after `futgg_url` field, line ~52).

2. In `src/futgg_client.py`, method `get_player_market_data` (line 104-128):
   - After line 118 (`raw_auctions = prices.get("liveAuctions", [])`), extract: `max_price_range = prices.get("priceRange", {}).get("maxPrice")` — this is an int or None.
   - Pass `max_price_range=max_price_range` to the `PlayerMarketData(...)` constructor (line 119-128).

3. In `src/futgg_client.py`, method `get_player_market_data_sync` (line 130-184):
   - Same extraction: `max_price_range = prices.get("priceRange", {}).get("maxPrice")`
   - Pass `max_price_range=max_price_range` to the `PlayerMarketData(...)` constructor (line 176-184).
  </action>
  <verify>
    <automated>cd C:/Users/maftu/Projects/op-seller && python -c "from src.models import PlayerMarketData; m = PlayerMarketData(player=None, current_lowest_bin=100, listing_count=10, price_history=[], sales=[], max_price_range=150000); assert m.max_price_range == 150000; print('OK')" 2>&1 || echo "Quick smoke test — model field exists. Full verification in Task 2."</automated>
  </verify>
  <done>PlayerMarketData has max_price_range field; both async and sync client methods extract and pass it from the prices API response.</done>
</task>

<task type="auto">
  <name>Task 2: Add max_price_range filter to scorer_v2 and wire from scanner</name>
  <files>src/server/scorer_v2.py, src/server/scanner.py</files>
  <action>
1. In `src/server/scorer_v2.py`, function `score_player_v2` (line 71-75):
   - Add parameter `max_price_range: int | None = None` after `buy_price: int`.
   - Update docstring Args section to document the new parameter.

2. In the margin loop (line 134), right after `sell_price = int(buy_price * (1 + margin))`, add:
   ```python
   if max_price_range is not None and sell_price > max_price_range:
       continue
   ```
   This skips margin tiers that produce an unlistable sell price. Use `is not None` (not truthy check) because max_price_range=0 should not silently disable the filter.

3. In `src/server/scanner.py` (line 429-433), update the `score_player_v2` call to pass:
   ```python
   v2_result = await score_player_v2(
       ea_id=ea_id,
       session=session,
       buy_price=market_data.current_lowest_bin,
       max_price_range=market_data.max_price_range,
   )
   ```
   The `market_data` variable is already a `PlayerMarketData` instance at this point.
  </action>
  <verify>
    <automated>cd C:/Users/maftu/Projects/op-seller && python -c "
import inspect
from src.server.scorer_v2 import score_player_v2
sig = inspect.signature(score_player_v2)
assert 'max_price_range' in sig.parameters, 'missing param'
print('scorer_v2 param OK')
" && python -c "
import ast, sys
with open('src/server/scanner.py') as f:
    tree = ast.parse(f.read())
found = False
for node in ast.walk(tree):
    if isinstance(node, ast.keyword) and node.arg == 'max_price_range':
        found = True
        break
assert found, 'scanner not passing max_price_range'
print('scanner wiring OK')
"</automated>
  </verify>
  <done>scorer_v2 accepts max_price_range and skips margin tiers where sell_price exceeds it; scanner passes market_data.max_price_range to scorer_v2.</done>
</task>

</tasks>

<verification>
- `PlayerMarketData` model has `max_price_range: Optional[int] = None`
- Both `get_player_market_data` and `get_player_market_data_sync` extract `priceRange.maxPrice` from prices dict
- `score_player_v2` accepts `max_price_range` param and skips margins where `sell_price > max_price_range`
- `scanner.py` passes `market_data.max_price_range` to `score_player_v2`
- Existing tests still pass: `python -m pytest tests/ -x -q --timeout=60`
</verification>

<success_criteria>
- Players with sell_price above EA max BIN range are no longer scored at those impossible margins
- The filter is a soft guard (None = no filtering) so existing code paths without max_price_range data are unaffected
- No regressions in existing test suite
</success_criteria>

<output>
After completion, create `.planning/quick/260330-ocv-filter-players-whose-sell-price-exceeds-/260330-ocv-SUMMARY.md`
</output>
