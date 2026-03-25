---
phase: 04-refactor-scoring-db
plan: 03
subsystem: scoring
tags: [scoring, sqlalchemy, listing-tracking, tdd, d-10-formula]

# Dependency graph
requires:
  - phase: 04-01
    provides: ListingObservation ORM model, BOOTSTRAP_MIN_OBSERVATIONS config
provides:
  - score_player_v2 async function implementing D-10 expected_profit_per_hour formula
  - Margin selection that maximises expected_profit_per_hour across all MARGINS tiers
  - Bootstrap guard (BOOTSTRAP_MIN_OBSERVATIONS) and OP threshold guard (MIN_OP_OBSERVATIONS)
affects: [04-04, scoring-job, api-players]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - TDD: failing test committed before implementation (RED then GREEN)
    - OP sell rate = op_sold / op_total (sold + expired) — accounts for failed OP listings
    - hours_of_data = max(span_hours, 0.5) — prevents division by zero for short windows
    - Margin loop evaluates all MARGINS and selects the one maximising expected_profit_per_hour
      (vs v1 which took the first margin meeting MIN_OP_SALES threshold)

key-files:
  created:
    - src/server/scorer_v2.py
    - tests/test_scorer_v2.py
  modified: []

decisions:
  - scorer_v2 evaluates all MARGINS tiers and picks maximum expected_profit_per_hour — unlike v1
    which stopped at the first viable margin (highest margin wins in v1, best EPH wins in v2)
  - hours_of_data uses max(span, 0.5h) floor — prevents divide-by-zero when all observations
    are within the same minute
  - op_sell_rate = op_sold / (op_sold + op_expired) — only counts OP listings in denominator,
    not all listings. This measures sell-through rate within the OP cohort specifically.

metrics:
  duration: 156s
  completed: 2026-03-25
  tasks: 1
  files: 2
---

# Phase 04 Plan 03: Scorer V2 Summary

**One-liner:** scorer_v2 reads ListingObservation rows and computes expected_profit_per_hour via D-10 formula (net_profit * op_sell_rate * op_sales_per_hour), picking the margin that maximises the metric.

## What Was Built

`src/server/scorer_v2.py` — `score_player_v2(ea_id, session, buy_price) -> dict | None`

The v2 scorer replaces v1's inference from `completedAuctions` snapshots with direct observation of listing outcomes. Key differences:

- **Bootstrap guard**: returns `None` until `BOOTSTRAP_MIN_OBSERVATIONS` resolved rows exist
- **OP sell rate**: `op_sold / (op_sold + op_expired)` — the denominator is only OP listings (not all sales), measuring sell-through within the OP cohort
- **Margin selection**: evaluates all MARGINS tiers and picks the one maximising `expected_profit_per_hour` (v1 used greedy-first-viable)
- **hours_of_data**: computed as `(max(last_seen_at) - min(first_seen_at)).hours`, floored at 0.5h

### D-10 Formula

```
expected_profit_per_hour = net_profit * op_sell_rate * op_sales_per_hour

where:
  op_sell_rate     = op_sold / op_total              (op_total = sold + expired)
  op_sales_per_hour = op_sold / hours_of_data
  net_profit       = sell_price * (1 - EA_TAX_RATE) - buy_price
```

### Return Dict Shape

```python
{
    "ea_id": int,
    "buy_price": int,
    "sell_price": int,
    "net_profit": int,
    "margin_pct": int,
    "op_sold": int,
    "op_total": int,
    "op_sell_rate": float,
    "op_sales_per_hour": float,
    "expected_profit_per_hour": float,
    "efficiency": float,     # expected_profit_per_hour / buy_price
    "hours_of_data": float,
}
```

## Tests

`tests/test_scorer_v2.py` — 6 tests:

| Test | Verifies |
|------|---------|
| `test_expected_profit_per_hour` | Formula correctness: 5/8 sell rate * 0.5 sales/hr * 450 net = 140.625 |
| `test_margin_selection` | High-volume margin 10% beats low-volume margin 20% on EPH |
| `test_bootstrap_min` | None returned when < BOOTSTRAP_MIN_OBSERVATIONS resolved rows |
| `test_no_resolved_observations` | None returned when zero observations exist |
| `test_insufficient_op_observations` | None when all margins have < MIN_OP_OBSERVATIONS OP listings |
| `test_return_dict_shape` | All 12 required keys present and correctly typed |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Test data produced wrong hours_of_data**
- **Found during:** Task 1 GREEN phase
- **Issue:** `test_expected_profit_per_hour` seeded 12 non-OP observations with `first_seen_at = t_start + i*0.7h`, making the latest `last_seen_at = t_start + 8.2h` instead of the expected 10h. This caused `op_sales_per_hour = 5/8.2 ≈ 0.61` instead of 0.5.
- **Fix:** Changed non-OP observation spacing to distribute evenly so the last entry anchors `last_seen_at` at exactly `t_start + 10h`.
- **Files modified:** `tests/test_scorer_v2.py`
- **Commit:** 161ee81

## Known Stubs

None.

## Self-Check: PASSED

Files created:
- FOUND: src/server/scorer_v2.py
- FOUND: tests/test_scorer_v2.py

Commits:
- FOUND: fadef16 (test RED)
- FOUND: 161ee81 (feat GREEN)

Test results: 97 tests passed, 0 failures.
