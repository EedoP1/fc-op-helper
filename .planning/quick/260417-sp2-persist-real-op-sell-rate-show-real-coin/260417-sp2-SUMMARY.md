---
phase: quick-260417-sp2
plan: 01
subsystem: scoring+persistence+api+cli
tags: [scoring, persistence, api, cli, display-truth]
requires:
  - scorer_v3.score_player_v3 (existing loop with op_sold/op_expired per margin tier)
  - PlayerScore columns op_sales/total_sales/op_ratio (existing, repurposed — no DDL change)
  - portfolio_query._build_scored_entry (forwards op_sales/total_sales/op_ratio verbatim)
provides:
  - score_player_v3 returns op_sold_count, op_total_count, op_sell_rate
  - PlayerScore rows carry real op_sold / op_total / op_sell_rate
  - GET /portfolio + POST /portfolio/generate return coins_per_hour per player
  - CLI EP/hr column, summary panel, CSV export source real coins_per_hour
affects:
  - Player detail view (src/main.py run_player_detail) — no code change, now shows real OP values automatically
tech-stack:
  added: []
  patterns:
    - repurpose-unused-columns instead of schema migration
    - composite-score retained in expected_profit_per_hour for optimizer; display values re-sourced
key-files:
  created: []
  modified:
    - src/server/scorer_v3.py
    - src/server/scanner.py
    - src/server/api/portfolio_read.py
    - src/main.py
decisions:
  - No schema change — op_sales/total_sales/op_ratio columns exist and are repurposed from zeros/sell_ratio to real OP counts and OP success rate
  - Optimizer input (expected_profit_per_hour = weighted_score) untouched — portfolio composition is unchanged
  - Kept expected_profit_per_hour in API response alongside new coins_per_hour so other clients and future debug can still read the composite rank score
  - rank_score passthrough added to the CLI mapped dict for transparency (not rendered in this plan)
metrics:
  duration_seconds: 278
  tasks_completed: 4
  files_modified: 4
  commits: 4
  completed_at: "2026-04-17T17:48:42Z"
requirements:
  - SP2-01-scorer-returns-real-op-counts
  - SP2-02-scanner-persists-real-op-counts
  - SP2-03-api-exposes-real-coins-per-hour
  - SP2-04-cli-displays-real-values
---

# Quick 260417-sp2: Persist real OP sell rate and show real coins/hr Summary

Plumb the real OP success rate and a real coins/hr metric from the scorer through persistence and API to the CLI, replacing three misnamed display values (Win%, OP Sales, EP/hr) with ground truth while preserving optimizer ranking behavior.

## What Changed

### Task 1 — scorer_v3 returns real OP counts (commit 302f3c02)
`src/server/scorer_v3.py` — added `best_op_sold` and `best_op_total` alongside the existing `best_op_sell_rate`, captured when a new best margin tier is chosen. Return dict now exposes `op_sold_count` and `op_total_count` (int-coerced to safely handle asyncpg `Decimal`). Loop logic, weighted_score math, and the MIN_OP_OBSERVATIONS gate are unchanged.

### Task 2 — scanner persists real counts (commit 1d18ad05)
`src/server/scanner.py` — v3-success PlayerScore construction now stores:
- `op_sales = v3_result["op_sold_count"]` (was hardcoded 0)
- `total_sales = v3_result["op_total_count"]` (was hardcoded 0)
- `op_ratio = v3_result["op_sell_rate"]` (was `sell_ratio` — supply/demand, not OP success)

The `expected_profit_per_hour = weighted_score` line is unchanged, so the optimizer still ranks on the composite score. The failure-path else branch (lines 357-373) still writes zero-rows on scoring failure.

### Task 3 — portfolio API exposes coins_per_hour (commit 7eeae76f)
`src/server/api/portfolio_read.py` — both `GET /portfolio` and `POST /portfolio/generate` now include `coins_per_hour = round(net_profit × sales_per_hour × op_ratio, 2)` on every returned player. Returns `None` when `sales_per_hour` is missing. Existing keys (including `expected_profit_per_hour`) are preserved. `/portfolio/swap-preview` and `/portfolio/rebalance` are untouched.

### Task 4 — CLI displays real values (commit b19620f5)
`src/main.py`:
- `run_portfolio` mapped dict adds `coins_per_hour` (for display) and renames the composite score passthrough to `rank_score` for transparency.
- `display_results` summary panel `Expected profit/hr` line now sums `coins_per_hour` across the portfolio.
- Table EP/hr column cell sources `coins_per_hour` instead of `expected_profit_per_hour`.
- `export_csv` EP/hr CSV field switches to `coins_per_hour`.
- Win% and OP Sales columns unchanged in code; they now display real values automatically via Task 2's persisted data.

## Before/After (illustrative for a typical mid-budget card)

| Column    | Before                                 | After                                 |
|-----------|----------------------------------------|---------------------------------------|
| Win%      | ~60% (sell_ratio = sph / total_lph)    | ~30% (real op_sold / (op_sold+op_expired)) |
| OP Sales  | 0/0 (hardcoded zeros)                  | 7/22 (real observation counts)        |
| EP/hr     | ~150,000 (composite weighted_score)    | ~1,200 (net_profit × sph × op_ratio)  |
| Summary   | "Expected profit/hr: 10,500,000"       | "Expected profit/hr: 120,000"         |

Player detail view (`python -m src.main --player <ea_id>`) requires no code change — the server serializes latest PlayerScore rows verbatim, so OP Sales (`{op_sales}/{total_sales}`) and OP Ratio (`{op_ratio:.1%}`) now display real values after the next scan rewrites the row.

## Optimizer Behavior Confirmation

**No change.** The optimizer consumes `expected_profit_per_hour` from `_build_scored_entry` in `src/server/api/portfolio_query.py`, which still forwards `score.expected_profit_per_hour` — the scanner still writes `weighted_score` into that column. Portfolio composition (which players get selected and in what order) is identical pre- and post-plan.

## Existing Row Staleness

Rows written before deploy continue to report `op_sales=0`, `total_sales=0`, `op_ratio=sell_ratio` until the next scan for that ea_id overwrites them. This is acceptable — the stale-flagging pipeline is already in place and scans run on a 5-minute interval. A full portfolio refresh happens within one scan cycle (~5 min) for all players the optimizer touches.

## Deviations from Plan

None — plan executed exactly as written.

## Self-Check: PASSED

- FOUND: src/server/scorer_v3.py (modified)
- FOUND: src/server/scanner.py (modified)
- FOUND: src/server/api/portfolio_read.py (modified)
- FOUND: src/main.py (modified)
- FOUND commit: 302f3c02 (Task 1 — scorer_v3)
- FOUND commit: 1d18ad05 (Task 2 — scanner)
- FOUND commit: 7eeae76f (Task 3 — portfolio API)
- FOUND commit: b19620f5 (Task 4 — CLI)
- All four files parse as valid Python (ast.parse)
- All four per-task verify scripts passed
