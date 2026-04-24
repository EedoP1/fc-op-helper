# FC26 ML Pipeline Design

**Date:** 2026-04-24
**Status:** Draft — awaiting user review
**Approach:** Supervised ML (LightGBM classifier) targeting the 1,850-opp catalog, replacing hand-crafted strategy iterations.

## Context

- 80+ hand-crafted strategy iterations have captured ~0.7% of the $70M pessimistic-fill opportunity ceiling ($512k best single strategy, `floor_buy_v31`).
- The engine has no portfolio mode, so prior "stack" claims (~$1.49M) were fiction — measured in iter 94 at $70k equal-split / −$118k no-contention.
- Full web research on algo-trading methodology (`.planning/algo_trading_methodology_research.md`) identified the current loop's structural failure modes: hypothesis-first rules, no multi-testing correction, no supervised use of the 1,850 hindsight labels.
- Decision: pivot to one ML baseline, research-first, target-flexible scope.

## Goals

- **Primary gate:** model-gated backtest strategy achieves ≥ **$2,000,000 organic PnL on $1M budget, pessimistic loader, `--min-sph 2`**, under purged walk-forward validation.
- **Research deliverable:** regardless of whether the gate passes, produce a clean feature library + labels + trained baseline + evaluation report that any future model (not just LightGBM) can consume.

## Non-goals

- Live trading plumbing. No real-time scoring, no chrome-extension integration in this project.
- Engine modifications. Pessimistic + optimistic loaders are evaluated as-is; no limit-buy simulation.
- Per-opp-subtype specialized models. Single classifier trained on all positives.
- Neural networks, stacked ensembles, or Bayesian hyperparameter optimization. Premature for a baseline.

## Architecture

A new `src/algo/ml/` package parallel to `src/algo/strategies/`. Six modules with single-responsibility boundaries:

```
src/algo/ml/
├── features.py   # ~38 pure functions: feature(snapshots, ts, ea_id) → float
├── labels.py     # triple-barrier labels on the 1,850 opps
├── dataset.py    # builds X, y, groups (week IDs for purging) from features+labels
├── train.py      # LightGBM training with purged walk-forward CV
├── score.py      # inference: load trained model, score (ea_id, hour) candidates
└── evaluate.py   # runs ml_score_v1 under 3 loaders, reports DSR + dollar PnL
```

One strategy adapter:

```
src/algo/strategies/
└── ml_score_v1.py   # scores candidates via trained model, emits top-K BUY/day
```

### Data flow — training time

```
market_snapshots + daily_listing_summaries + players
    → features.py (vectorized pandas/numpy extract_all)
    → feature matrix X (~1M rows × ~38 cols)

.planning/profit_opportunities.json
    → labels.py (triple-barrier)
    → labels y (~1M rows, ~1,850 positives)

X, y → dataset.py (groups = week_of_window)
    → train.py (LightGBM with PurgedKFold, embargo=3 days)
    → trained model artifact (models/ml_score_v1.lgb) + feature importances
```

### Data flow — backtest time

```
For each (ea_id, hour) tick where sph ≥ 2:
    → score.py loads model, returns probability
    → ml_score_v1.py keeps top-K of the day, emits BUY signal
    → engine simulates fills under pessimistic + optimistic + 3%-slip loaders
    → evaluate.py aggregates per-loader PnL + DSR + sensitivity curves
```

## Component detail

### features.py (~38 features across 4 families)

**Price-trend (12)**
- `return_1h`, `return_6h`, `return_24h`, `return_72h`, `return_168h` — log-returns
- `drawdown_24h`, `drawdown_72h`, `drawdown_168h` — `(max - now) / max` over lookback
- `rel_pos_24h`, `rel_pos_72h` — current price's percentile within window range
- `vol_24h`, `vol_72h` — std of hourly returns

**Microstructure (10)**
- `lc_now`, `lc_avg_24h`, `lc_avg_72h` — listing counts
- `lc_change_24h`, `lc_change_72h` — relative change
- `lc_pct_rank_168h` — current listing count percentile vs 7-day history
- `daily_sold_today`, `daily_sold_avg_7d` — from `daily_listing_summaries`
- `sold_velocity_ratio` — today's sold / 7-day median
- `sph_now` — current sales-per-hour estimate

**Calendar (6)**
- `hour_of_day` (0-23, cyclic-encoded as sin/cos)
- `day_of_week` (0-6, cyclic-encoded)
- `week_of_window` (0-4, ordinal)
- `is_promo_release_window` (Fri/Sat flag)
- `hours_since_last_known_opp_for_card` — time-causal, counts only opps with `sell_hour < ts`

**Volatility/regime (~10, final list TBD from feature importance on v1)**
- `whitelist_median_return_24h` — cohort-level signal (median return across all cards with ≥2 sph)
- `whitelist_breadth` — % of cohort with positive 24h return
- Remaining 6-8 slots reserved; filled based on v1 training feature importance + diagnosis of missing signal

**Explicitly excluded:** `rating`, `card_type`, `position`, `league`, `nation`. Card-identity features are prohibited — model must find signal in price/microstructure/calendar/regime only.

**Implementation rules**
- Each function: type-hinted, docstringed with formula, unit-testable
- All time-window features take `lookback_h` explicitly; never read past `ts`
- `@time_causal` decorator asserts data slice's `max(ts_column) < ts` at runtime
- Bulk extractor `extract_all(snapshots_df, hours, ea_ids) → DataFrame` vectorized (pandas/numpy), not Python loop; must process ~1M rows in <60s

### labels.py

**Positive label rule:** for each `(ea_id, ts)` in the training grid, `y=1` if there's a catalog opp with `buy_hour == ts AND ea_id == ea_id`; else `y=0`.

**Triple-barrier enrichment:** for each positive, record which of three barriers hit first within a **168h horizon** (matches `ml_score_v1.max_hold_h`):
- `profit_target` (reached sell_price at `roi_net ≥ 0.20`, matching catalog-opp definition)
- `stop_loss` (price fell ≥ 20% from entry before recovery)
- `time_max` (168h elapsed without either)

Horizon choice rationale: catalog `hold_hours` distribution has p99 = 168h (1 week). Longer horizons would add look-ahead span with no labeled catalog opps to compare against.

Stored as separate `barrier_outcome` column. Enables future weighted-label variants without recomputation.

**Universe**
- Every `(ea_id, ts)` where:
  - `market_snapshots` data exists (non-null `current_lowest_bin`)
  - `sph_now ≥ 2` — **sph** = sales-per-hour, a liquidity execution constraint (we literally cannot sell cards that don't transact)
- No rating filter, no card_type filter, no price filter
- Estimated size: ~900k-1.2M rows, 1,850 positives (~1:540 class imbalance)

### dataset.py

- Joins X and y; produces NumPy arrays + group vector (one entry per row = `week_of_window ∈ {0..4}`)
- Drops rows with ≥30% NaN features; reports drop % per week
- Returns `(X, y, groups, barrier_outcome, row_keys)` — `row_keys = [(ea_id, ts), ...]` for traceability

### train.py

**Model:** `lightgbm.LGBMClassifier`

**Hyperparameters (v1 defaults, no tuning):**
- `n_estimators=500`
- `learning_rate=0.05`
- `num_leaves=31`
- `max_depth=6`
- `min_child_samples=20`
- `reg_alpha=0.1`, `reg_lambda=0.1`
- `scale_pos_weight = n_negatives / n_positives`
- `is_unbalance=False` (we set scale explicitly)
- Early stopping on held-out fold

**Cross-validation:** `sklearn.model_selection.PurgedKFold` (custom implementation, see López de Prado Ch. 7)
- 5 folds, time-grouped by `week_of_window`
- Embargo = 3 days between train and test
- Purging = remove training rows whose triple-barrier outcome window overlaps any test row's feature-lookback window

**Output artifacts:**
- `models/ml_score_v1.lgb` — final model trained on all data for inference
- `models/ml_score_v1_oof_preds.parquet` — out-of-fold predictions (one row per training row, score from the fold that didn't see it). This is what PnL is evaluated on.
- `models/ml_score_v1_importances.parquet` — feature importance scores (gain + split)

**Hyperparameter tuning (conditional):**
- If v1 clears gate → ship v1
- If v1 fails → ONE round of Optuna (20 trials max) over `num_leaves`, `max_depth`, `learning_rate`, `min_child_samples`
- Reject trials with `deflated_sharpe_ratio < 1.0` (DSR = Sharpe ratio adjusted for the number of trials run; < 1.0 means the Sharpe could plausibly have arisen from noise under the null hypothesis)

### score.py

- `load_model(path) → LGBMClassifier`
- `score_batch(model, X) → np.ndarray[float]` — probabilities in [0, 1]
- Thin wrapper; no logic

### ml_score_v1.py (strategy adapter)

- Inherits `src/algo/strategies/base.py` Strategy class
- At each tick: compute features for all candidate `(ea_id, ts)` where `sph ≥ 2`, call `score_batch`, emit top-K BUY signals per UTC day (K=10 default)
- Exit policy: `profit_target=+0.25`, `max_hold_h=168`, `smoothed_stop=-0.25` with `stop_consec_hours=14` (recipe proven in daily_trend_dip_v5 → v24)
- Sizing: 8 slots × $125k, `qty_cap=50` (engine defaults)

### evaluate.py

Runs `ml_score_v1` under 3 loaders:
1. Pessimistic (engine default, buy@max / sell@min — drag ~9.6%)
2. Optimistic (buy@median / sell@median — drag ~0%)
3. 3%-slip (`--exec-slip 0.03` — drag ~6%)

For each, reports:
- Total organic PnL (excludes trades closing on data boundary)
- Total trades
- Win rate
- Per-week PnL breakdown
- Max drawdown
- Sharpe ratio
- **Deflated Sharpe Ratio** (López de Prado formula, accounting for multiple trials)
- Median trade profit, trade-count concentration (reject $2M from ≤5 trades or single week)

Also reports:
- PnL sensitivity for K ∈ {2, 5, 10, 20} to detect overfit to K=10
- Loader sensitivity: the gap between pessimistic and optimistic quantifies execution-cost sensitivity

## Validation scheme

Purged walk-forward 5-fold CV. Folds structured around week boundaries:

| Fold | Train | Embargo | Test |
|---|---|---|---|
| 1 | W13-W14 | W15 first 3d | W15 rest + W16 + W17 |
| 2 | W13-W15 | W16 first 3d | W16 rest + W17 |
| 3 | W13 + W15-W17 | W14 ±3d | W14 middle |
| 4 | W13-W14 + W16-W17 | W15 ±3d | W15 middle |
| 5 | W14-W17 | W13 last 3d | W13 early |

All reported PnL uses out-of-fold predictions.

## Risk & error handling

### Data risks
- **Missing feature data:** drop rows with ≥30% NaN. LightGBM handles residual NaN natively. No imputation.
- **Timezone drift:** one `_to_utc_hour()` helper, used in labels and features, unit-tested with mixed-TZ inputs.
- **Time-causality:** `@time_causal` decorator on every feature function, asserting data slice's `max(ts) < ts` at runtime.

### Model risks
- **Overfitting the 27-day window:** purged walk-forward CV, DSR reporting, regularized hyperparameters (max_depth=6, min_child_samples=20).
- **Calendar-artifact capture:** feature importance audit after v1 — if `week_of_window`/`day_of_week`/`hour_of_day` in top 5, investigate.
- **Cohort feature leakage:** exclude `self` by default in cohort aggregations; one test verifies.

### Evaluation risks
- **PnL distribution:** gate fails if $2M concentrates in ≤5 trades or 1 week.
- **Top-K sensitivity:** report PnL for K ∈ {2, 5, 10, 20}; pick K only if plateau exists.
- **Loader sensitivity:** must evaluate under all 3 loaders; signal must survive pessimistic for gate to pass.

### Gate-failure protocol
If v1 fails the $2M gate:
1. Do NOT iterate on the model (trap we came from)
2. Write post-mortem with achieved PnL + DSR + feature importances + loader sensitivity
3. User decides one of: (a) v2 feature library with specific additions based on importance gaps, (b) engine work on limit-buy simulation, (c) accept ceiling and ship best available

## Testing

### Unit (`tests/algo/ml/`)
- `test_features.py` — one formula test + one time-causality invariant test per feature family
- `test_labels.py` — known positives recovered; triple-barrier outcomes correct on constructed trajectories; timezone round-trip
- `test_dataset.py` — shape, no NaN in y, purge+embargo correctness
- `test_train.py` — smoke test (probabilities in [0,1]), synthetic-signal AUC > 0.7

### Integration (`tests/algo/ml/integration/`)
- `test_pipeline_integration.py` — end-to-end 2-day slice, <60s, produces model + OOF predictions
- `test_ml_score_v1.py` — strategy emits correct top-K, respects sph ≥ 2

### Runtime assertions (in `evaluate.py`)
- No look-ahead: each OOF prediction's model max-train-ts < test-row-ts
- Label coverage: positive count == len(catalog_opps)
- Feature finite: X.isfinite().all() pre-training

### Success criteria
- All unit tests < 10s
- Integration tests < 2 minutes
- Coverage 80%+ on `src/algo/ml/*.py`

### Explicitly NOT tested
- UI / display (none)
- DB CRUD (covered elsewhere)
- LightGBM internals (trusted dependency)
- Exhaustive hyperparameter combinations

## Deliverables

1. `src/algo/ml/` package (5 modules + tests)
2. `src/algo/strategies/ml_score_v1.py` adapter
3. `tests/algo/ml/` unit + integration suite
4. `models/ml_score_v1.{lgb,oof_preds.parquet,importances.parquet}` artifacts
5. `.planning/ml_v1_evaluation_report.md` — per-loader PnL, DSR, sensitivity curves, gate verdict
6. Feature importance plot (top-20 features by gain)

## Open questions deferred to implementation plan

- Exact signature of `extract_all()` (single-call vs iterator over days for memory)
- Triple-barrier horizon: 168h matches max-hold; verify against catalog opp `hold_hours` distribution
- Top-K default: 10 is a guess; sensitivity curve will inform
- The 6-8 reserve feature slots: will name specific candidates after v1 training

## References

- Marcos López de Prado, *Advances in Financial Machine Learning* (Wiley, 2018) — Ch. 3 (labeling), Ch. 7 (purged CV), Ch. 11 (DSR)
- `.planning/algo_trading_methodology_research.md` — this session's research synthesis
- `.planning/opportunity_signatures_report.md` — prior signature analysis (feature AUC baselines)
- `.planning/profit_opportunities.json` — the 1,850 labeled opportunities
- `.planning/algo-2m-target-final-report.md` — prior hand-crafted loop's post-mortem
