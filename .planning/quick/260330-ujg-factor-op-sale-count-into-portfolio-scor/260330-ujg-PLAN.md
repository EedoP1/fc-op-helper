---
phase: quick
plan: 260330-ujg
type: execute
wave: 1
depends_on: []
files_modified:
  - src/server/scorer_v2.py
  - src/optimizer.py
  - tests/test_optimizer.py
autonomous: true
requirements: []

must_haves:
  truths:
    - "Players with more OP sales are preferred over players with fewer OP sales at similar EPPH"
    - "Players with very few OP sales (low confidence) are penalized relative to high-volume OP sellers"
    - "Existing EPPH-based ranking remains the primary signal — op_sales is a secondary boost"
  artifacts:
    - path: "src/server/scorer_v2.py"
      provides: "op_sold count factored into scoring output"
    - path: "src/optimizer.py"
      provides: "Ranking incorporates op_sales alongside EPPH"
    - path: "tests/test_optimizer.py"
      provides: "Tests verifying op_sales influence on ranking"
  key_links:
    - from: "src/server/scorer_v2.py"
      to: "PlayerScore.op_sales"
      via: "scanner saves op_sold as op_sales"
      pattern: "op_sold"
    - from: "src/optimizer.py"
      to: "scored dict"
      via: "op_sales field in scored entry"
      pattern: "op_sales"
---

<objective>
Factor OP sale count (op_sales / op_sold) into portfolio scoring so that players with higher absolute numbers of OP sales are preferred over players with similar EPPH but lower sample sizes.

Purpose: Currently `expected_profit_per_hour = net_profit * op_sell_rate` treats a player with 3/5 OP sales (60% rate) the same confidence as 50/200 (25% rate). The absolute count of OP sales is a confidence/volume signal — more OP sales = more reliable and repeatable profit. This change makes the portfolio favor statistically robust OP sellers.

Output: Modified scorer and optimizer that incorporate op_sales count as a confidence multiplier.
</objective>

<execution_context>
@$HOME/.claude/get-shit-done/workflows/execute-plan.md
@$HOME/.claude/get-shit-done/templates/summary.md
</execution_context>

<context>
@src/server/scorer_v2.py
@src/optimizer.py
@src/config.py
@src/server/models_db.py (PlayerScore model — op_sales field already exists)
@src/server/api/portfolio.py (_build_scored_entry passes op_sales into scored dict)
@tests/test_optimizer.py

<interfaces>
<!-- The scored dict passed to optimize_portfolio() has these relevant fields: -->
From src/server/api/portfolio.py _build_scored_entry():
```python
{
    "player": _PlayerProxy(score.ea_id),  # .resource_id
    "buy_price": score.buy_price,
    "op_sales": score.op_sales,           # absolute count of OP sold listings
    "total_sales": score.total_sales,     # total resolved observations at chosen margin
    "op_ratio": score.op_ratio,           # op_sales / total_sales
    "expected_profit_per_hour": score.expected_profit_per_hour,  # net_profit * op_sell_rate
    # ... other fields
}
```

From src/server/scorer_v2.py score_player_v2() return dict:
```python
{
    "op_sold": op_sold,           # OP sales that actually sold at this margin
    "op_total": op_total,         # total (sold + expired) at this margin
    "op_sell_rate": op_sell_rate, # op_sold / op_total
    "expected_profit_per_hour": round(epph, 2),  # net_profit * op_sell_rate
}
```
</interfaces>
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Add op_sales confidence boost to optimizer ranking</name>
  <files>src/optimizer.py, tests/test_optimizer.py</files>
  <behavior>
    - Test: Player with 50 op_sales and EPPH=400 ranks above player with 3 op_sales and EPPH=400 (same EPPH, more OP sales wins)
    - Test: Player with 50 op_sales and EPPH=350 ranks above player with 3 op_sales and EPPH=400 (volume boost can overcome small EPPH gap)
    - Test: Player with 3 op_sales and EPPH=2000 still ranks above player with 50 op_sales and EPPH=100 (EPPH dominates when difference is large)
    - Test: Existing test_ranks_by_expected_profit_per_hour still passes (backward compat)
  </behavior>
  <action>
    Modify optimize_portfolio() to compute a confidence-adjusted ranking score instead of using raw EPPH.

    The approach: apply a logarithmic confidence multiplier based on op_sales count. Use `log(1 + op_sales)` as a soft scaling factor so that going from 3 to 50 OP sales gives a meaningful boost, but going from 50 to 500 gives diminishing returns.

    Formula for `_ranking_profit`:
    ```python
    import math
    base_epph = s.get("expected_profit_per_hour") or 0
    op_count = s.get("op_sales") or 0
    # Normalize: log(1+3)=1.39, log(1+20)=3.04, log(1+50)=3.93, log(1+100)=4.62
    # Divide by log(1+MIN_OP_OBSERVATIONS) so that the minimum qualifying count gives multiplier ~1.0
    confidence = math.log(1 + op_count) / math.log(1 + 3)  # 3 = MIN_OP_OBSERVATIONS
    s["_ranking_profit"] = base_epph * confidence
    ```

    This means:
    - Player with 3 OP sales (minimum): confidence = 1.0x (no boost)
    - Player with 20 OP sales: confidence = ~2.2x
    - Player with 50 OP sales: confidence = ~2.8x
    - Player with 100 OP sales: confidence = ~3.3x

    Import MIN_OP_OBSERVATIONS from src.config (value is 3) to keep the reference count centralized.

    Update the _make_scored helper in tests to ensure op_sales is always set (it already is, value=10). Add new test functions for the confidence boost behavior described above.
  </action>
  <verify>
    <automated>python -m pytest tests/test_optimizer.py -x -v</automated>
  </verify>
  <done>
    - optimize_portfolio uses op_sales-based confidence multiplier for ranking
    - Players with more OP sales rank higher than players with same EPPH but fewer OP sales
    - Large EPPH differences still dominate over op_sales count
    - All existing optimizer tests pass
    - New tests cover confidence boost edge cases
  </done>
</task>

<task type="auto">
  <name>Task 2: Add op_sold count to scorer_v2 logging for observability</name>
  <files>src/server/scorer_v2.py</files>
  <action>
    Add op_sold count to the debug log message on line 177 (the "no viable margin found" case) and to a new info-level log when a score IS found, so production logs show the OP sale volume alongside the chosen margin. This helps verify the confidence boost is working as expected.

    In the success path (after the `for row in margin_rows` loop, when `best is not None`), add:
    ```python
    logger.debug(
        "score_player_v2: ea_id=%d margin=%d%% op_sold=%d/%d rate=%.1f%% epph=%.2f",
        ea_id, best["margin_pct"], best["op_sold"], best["op_total"],
        best["op_sell_rate"] * 100, best["expected_profit_per_hour"],
    )
    ```

    No behavioral change — pure observability improvement.
  </action>
  <verify>
    <automated>python -m pytest tests/test_scorer_v2.py -x -v 2>/dev/null; echo "exit: $?"</automated>
  </verify>
  <done>
    - scorer_v2 logs op_sold count alongside margin and EPPH on successful scores
    - No functional change to scoring output
    - Existing scorer tests pass
  </done>
</task>

</tasks>

<verification>
- `python -m pytest tests/test_optimizer.py tests/test_scorer_v2.py -x -v` — all pass
- Inspect optimizer output: players with high op_sales and moderate EPPH rank above players with low op_sales and similar EPPH
</verification>

<success_criteria>
- Portfolio optimizer factors op_sales count into ranking via confidence multiplier
- Minimum-qualifying players (3 OP sales) get no boost (1.0x)
- High-volume OP sellers (50+ sales) get meaningful ranking boost (~2.8x)
- EPPH remains the dominant signal; op_sales is a secondary confidence weight
- All existing tests pass, new tests cover the confidence boost
</success_criteria>

<output>
After completion, create `.planning/quick/260330-ujg-factor-op-sale-count-into-portfolio-scor/260330-ujg-SUMMARY.md`
</output>
