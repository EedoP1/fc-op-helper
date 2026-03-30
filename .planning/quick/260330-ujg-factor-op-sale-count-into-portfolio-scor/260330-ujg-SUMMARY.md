---
phase: quick
plan: 260330-ujg
subsystem: optimizer, scorer
tags: [scoring, optimizer, confidence, op_sales]
completed: "2026-03-30T19:09:24Z"
duration_min: 15
tasks_completed: 2
files_modified: 3
commits:
  - hash: 3ba2b90
    message: "feat(260330-ujg): add op_sales confidence multiplier to optimizer ranking"
  - hash: a8e86c9
    message: "feat(260330-ujg): add op_sold/op_total debug log on successful score in scorer_v2"
key_decisions:
  - "Confidence multiplier is log(1+op_sales)/log(1+MIN_OP_OBSERVATIONS): minimum-qualifying players get 1.0x, 50 OP sales yields ~2.83x boost — logarithmic scale gives diminishing returns above ~50 sales"
  - "Final output sort remains by raw EPPH descending — confidence boost only affects selection ordering, not display ranking"
  - "Test suite updated to use _make_fillers() pattern from main branch: 80 filler players at 1 coin prevent drop-and-backfill loop interference on small test datasets"
dependencies:
  requires: [src/config.py MIN_OP_OBSERVATIONS]
  provides: [op_sales-weighted ranking in optimize_portfolio]
  affects: [portfolio selection order, player scoring observability]
---

# Quick Task 260330-ujg: Factor op_sales Count into Portfolio Scoring — Summary

**One-liner:** Logarithmic op_sales confidence multiplier (`log(1+n)/log(1+3)`) applied to EPPH ranking in optimizer so statistically robust OP sellers beat thin-sample players with equal EPPH.

## What Was Done

### Task 1: Add op_sales confidence boost to optimizer ranking

Modified `optimize_portfolio()` in `src/optimizer.py` to compute a confidence-adjusted ranking score instead of using raw EPPH for selection ordering.

**Formula:**
```python
import math
_log_min = math.log(1 + MIN_OP_OBSERVATIONS)  # log(4) = 1.386
confidence = math.log(1 + op_count) / _log_min if op_count > 0 else 1.0
s["_ranking_profit"] = epph * confidence
```

**Multiplier reference:**
- 3 OP sales (minimum): 1.0x (no boost)
- 20 OP sales: ~2.2x
- 50 OP sales: ~2.83x
- 100 OP sales: ~3.33x

The final output sort still uses raw `expected_profit_per_hour` — the confidence boost only affects greedy fill order and upgrade loop decisions.

Updated `tests/test_optimizer.py` to use the `_make_fillers()` pattern (80 filler players at 1 coin each) so small test datasets don't trigger the drop-and-backfill loop. Added 4 new tests covering the confidence boost edge cases.

### Task 2: Add op_sold count to scorer_v2 logging

Added a DEBUG-level log in `score_player_v2()` after a successful score is found:
```
score_player_v2: ea_id=12345 margin=15% op_sold=42/120 rate=35.0% epph=1234.56
```

This helps verify in production logs that the confidence multiplier is applied to players with meaningful OP sale volumes.

## Files Modified

| File | Change |
|------|--------|
| `src/optimizer.py` | Add `import math`, `MIN_OP_OBSERVATIONS` import, confidence multiplier in ranking computation |
| `tests/test_optimizer.py` | Rewrite to `_make_fillers()` pattern; add 4 confidence boost tests; update `_make_scored` to accept `op_sales` param |
| `src/server/scorer_v2.py` | Add DEBUG log on successful score with op_sold/op_total/rate/epph |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Pre-existing test suite broken: small datasets trigger drop-and-backfill**
- **Found during:** Task 1 (TDD GREEN phase — existing tests failing on base code)
- **Issue:** `test_fills_budget`, `test_no_duplicates`, `test_swap_replaces_expensive_with_cheaper`, and others used tiny datasets (2–20 players) that all triggered the `_MIN_FILL_COUNT=80` drop-and-backfill loop, causing unexpected player bans and reduced portfolio size.
- **Fix:** Replaced affected tests with the `_make_fillers()` pattern from commit `4ea45ba` (which was on main but not yet in this worktree). Tests now pad candidate pools to 80+ so greedy fill reaches `_MIN_FILL_COUNT` without affecting assertions.
- **Files modified:** `tests/test_optimizer.py`
- **Commit:** `3ba2b90`

**2. [Out of scope — deferred] `tests/test_scorer_v2.py` uses SQLite but scorer SQL uses PostgreSQL-specific `CROSS JOIN (VALUES ...)` syntax**
- Pre-existing, unrelated to this task.
- Logged to deferred items; not fixed.

## Known Stubs

None.

## Self-Check: PASSED

- src/optimizer.py: FOUND
- src/server/scorer_v2.py: FOUND
- tests/test_optimizer.py: FOUND
- Commit 3ba2b90: FOUND
- Commit a8e86c9: FOUND
