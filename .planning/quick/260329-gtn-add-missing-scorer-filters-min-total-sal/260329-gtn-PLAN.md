---
phase: quick
plan: 260329-gtn
type: execute
autonomous: true
files_modified:
  - src/config.py
  - src/server/scorer_v2.py
  - src/server/api/portfolio.py
  - tests/test_scorer_v2.py
  - tests/test_portfolio.py
---

<objective>
Add two missing scorer filters and fix the volatility filter to close gaps that let unreliable players into the portfolio:

1. **Minimum total resolved observations** — raise the bar beyond BOOTSTRAP_MIN_OBSERVATIONS (10) so players with few overall sales (Bacha, Yildiz, Dunga pattern) are rejected.
2. **Minimum observation history depth** — reject players whose earliest observation is too recent (Paredes pattern: card added days ago, not enough data to trust).
3. **Wire VOLATILITY_MAX_PRICE_INCREASE_ABS** — the 10k absolute threshold is defined in config.py but never used in `_get_volatile_ea_ids`. Players like Petit, Baresi, Sissi, Mascherano with large absolute price swings should be caught even if the percentage is under 30%.

Purpose: Eliminate false-positive OP sell recommendations from low-data or volatile players.
Output: Updated scorer, volatility filter, config constants, and tests.
</objective>

<context>
@src/config.py
@src/server/scorer_v2.py
@src/server/api/portfolio.py
@tests/test_scorer_v2.py
@tests/test_portfolio.py
</context>

<tasks>

<task type="auto" tdd="true">
  <name>Task 1: Add min-total-observations and min-history-depth filters to scorer_v2</name>
  <files>src/config.py, src/server/scorer_v2.py, tests/test_scorer_v2.py</files>
  <behavior>
    - Test: Player with 15 resolved observations (above BOOTSTRAP_MIN=10 but below new MIN_TOTAL_RESOLVED_OBSERVATIONS=20) returns None
    - Test: Player with 25 resolved observations passes the filter and scores normally
    - Test: Player whose earliest observation is only 1 day old (below MIN_OBSERVATION_HISTORY_DAYS=3) returns None
    - Test: Player whose earliest observation is 5 days old passes the filter and scores normally
  </behavior>
  <action>
    1. Add two new constants to src/config.py:
       - `MIN_TOTAL_RESOLVED_OBSERVATIONS = 20` — minimum resolved ListingObservation rows required (higher bar than BOOTSTRAP_MIN_OBSERVATIONS=10 which is the activation threshold; this is the quality threshold)
       - `MIN_OBSERVATION_HISTORY_DAYS = 3` — minimum days between earliest observation first_seen_at and now

    2. In src/server/scorer_v2.py:
       - Import the two new constants
       - After the existing bootstrap guard (line 74), add a second guard: if `len(observations) < MIN_TOTAL_RESOLVED_OBSERVATIONS`, return None with a debug log
       - Add a history depth guard: compute `earliest_seen = min(obs.first_seen_at for obs in observations)`, compute age in days from `datetime.utcnow() - earliest_seen`. If age < MIN_OBSERVATION_HISTORY_DAYS, return None with debug log
       - Note: BOOTSTRAP_MIN_OBSERVATIONS (10) remains as the "v2 scorer activation" threshold. MIN_TOTAL_RESOLVED_OBSERVATIONS (20) is the higher "quality" threshold. Both checks are sequential — first bootstrap, then quality.

    3. Add 4 new tests to tests/test_scorer_v2.py:
       - `test_below_min_total_resolved_observations` — seed 15 observations (above 10, below 20), assert result is None
       - `test_above_min_total_resolved_observations` — seed 25 observations with viable OP data, assert result is not None
       - `test_below_min_observation_history_days` — seed 20+ observations all within the last 24 hours, assert result is None
       - `test_above_min_observation_history_days` — seed 20+ observations spanning 5 days, assert result is not None
  </action>
  <verify>
    <automated>python -m pytest tests/test_scorer_v2.py -x -v</automated>
  </verify>
  <done>
    - Players with fewer than 20 total resolved observations are rejected by scorer_v2
    - Players with less than 3 days of observation history are rejected by scorer_v2
    - All existing scorer_v2 tests still pass (existing tests seed sufficient observations)
    - 4 new tests pass
  </done>
</task>

<task type="auto" tdd="true">
  <name>Task 2: Wire absolute volatility threshold into _get_volatile_ea_ids</name>
  <files>src/server/api/portfolio.py, src/config.py, tests/test_portfolio.py</files>
  <behavior>
    - Test: Player with 25% price increase (below 30% threshold) but 15k absolute increase (above 10k threshold) IS flagged as volatile
    - Test: Player with 5% price increase and 3k absolute increase (below both thresholds) is NOT flagged
    - Test: Player with 35% price increase and 8k absolute increase (above pct, below abs) IS flagged (existing behavior preserved)
  </behavior>
  <action>
    1. In src/server/api/portfolio.py, update `_get_volatile_ea_ids`:
       - Import `VOLATILITY_MAX_PRICE_INCREASE_ABS` from src.config
       - Change the volatility condition from `increase > threshold` (percentage only) to: `increase > threshold OR (max_bin - min_bin) > VOLATILITY_MAX_PRICE_INCREASE_ABS`
       - Update the docstring to reflect both checks

    2. Add 3 new tests to tests/test_portfolio.py:
       - `test_volatile_absolute_increase_above_threshold` — player with 100k price, min_bin=100000 max_bin=115000 (15% increase, 15k absolute). 15% is below 30% threshold, but 15k > 10k absolute threshold. Assert ea_id IS in volatile set.
       - `test_stable_below_both_thresholds` — player with 100k price, min_bin=100000 max_bin=103000 (3% increase, 3k absolute). Below both thresholds. Assert ea_id NOT in volatile set.
       - `test_volatile_pct_above_abs_below` — player with 20k price, min_bin=20000 max_bin=28000 (40% increase, 8k absolute). 40% > 30% threshold, 8k < 10k threshold. Assert ea_id IS in volatile set (pct alone sufficient).

    3. Update existing volatility tests if they make assumptions about percentage-only checking that would break with the OR condition. Review each existing test fixture to ensure absolute values don't accidentally trip the new threshold.
  </action>
  <verify>
    <automated>python -m pytest tests/test_portfolio.py -x -v</automated>
  </verify>
  <done>
    - _get_volatile_ea_ids flags players exceeding EITHER the percentage OR the absolute threshold
    - VOLATILITY_MAX_PRICE_INCREASE_ABS (10,000 coins) is now active, not dead config
    - All existing volatility tests still pass
    - 3 new tests pass covering the absolute threshold path
  </done>
</task>

</tasks>

<verification>
Run the full test suite to ensure no regressions:

```bash
python -m pytest tests/test_scorer_v2.py tests/test_portfolio.py -x -v
```

Verify config constants are properly defined:
```bash
python -c "from src.config import MIN_TOTAL_RESOLVED_OBSERVATIONS, MIN_OBSERVATION_HISTORY_DAYS, VOLATILITY_MAX_PRICE_INCREASE_ABS; print(f'MIN_OBS={MIN_TOTAL_RESOLVED_OBSERVATIONS}, MIN_DAYS={MIN_OBSERVATION_HISTORY_DAYS}, ABS={VOLATILITY_MAX_PRICE_INCREASE_ABS}')"
```
</verification>

<success_criteria>
- scorer_v2 rejects players with < 20 total resolved observations
- scorer_v2 rejects players with < 3 days of observation history
- Volatility filter catches players with > 10k absolute price swing even if percentage is under 30%
- All existing tests pass without modification (or with minimal fixture adjustments)
- 7 new tests pass (4 scorer + 3 volatility)
</success_criteria>

<output>
After completion, create `.planning/quick/260329-gtn-add-missing-scorer-filters-min-total-sal/260329-gtn-SUMMARY.md`
</output>
