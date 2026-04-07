---
phase: quick
plan: 260407-bh0
subsystem: algo-backtester
tags: [performance, refactor, backtesting]
dependency_graph:
  requires: []
  provides: [run_sweep_single_pass]
  affects: [run_cli]
tech_stack:
  added: []
  patterns: [single-pass-timeline-walk]
key_files:
  created: []
  modified:
    - src/algo/engine.py
    - tests/algo/test_engine.py
decisions:
  - "Single-pass builds timeline once and walks once for all combos; each combo gets independent Portfolio"
  - "run_cli routes --all and --strategy-without-params through single-pass; --strategy with --params uses direct run_backtest"
metrics:
  duration_seconds: 130
  completed: "2026-04-07T05:20:07Z"
---

# Quick Task 260407-bh0: Refactor Backtesting Engine to Single-Pass Summary

Single-pass timeline walk across all strategy+param combos, building the timeline once instead of 129 times per sweep.

## What Changed

### run_sweep_single_pass (src/algo/engine.py)

New function that:
1. Builds the timeline (defaultdict + sort of all price points) ONCE
2. Instantiates all strategy+param combos with independent Portfolio instances
3. Walks sorted timestamps once, calling on_tick for every combo at each tick
4. Force-sells open positions and computes metrics per combo
5. Returns same result dict format as run_backtest

### run_cli updated

- `--all` and `--strategy X` (without `--params`) now use `run_sweep_single_pass` instead of looping `run_sweep` per strategy class
- `--strategy X --params '{...}'` still uses direct `run_backtest` (single combo, no sweep needed)
- Logs total combo count before starting

### Existing functions preserved

`run_backtest` and `run_sweep` remain unchanged and importable -- no breaking changes.

## Tests Added

| Test | What it proves |
|------|----------------|
| test_single_pass_matches_sequential | Single-pass results are identical to sequential run_sweep (ThresholdStrategy, 3 combos) |
| test_single_pass_with_multiple_strategies | Multiple strategy classes produce correct per-combo results with correct strategy_name |
| test_single_pass_independent_portfolios | Two identical combos produce identical results (portfolios are independent, not shared) |

## Commits

| # | Hash | Message |
|---|------|---------|
| 1 | 1ac0b1f | test(quick-260407-bh0): add failing tests for run_sweep_single_pass |
| 2 | 80921f1 | feat(quick-260407-bh0): implement run_sweep_single_pass for single-timeline walk |
| 3 | ac96739 | feat(quick-260407-bh0): wire run_cli to use single-pass for sweep mode |

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None.

## Self-Check: PASSED
