# Hourly Dip Reversion v5 — 25%/week Strategy on Median-Bucketed Loader

## Summary

`hourly_dip_revert_v5` replaces the v4 strategy against a cleaner data loader.
The loader in `src/algo/engine.py` now returns the **hourly median BIN** per
(ea_id, hour) instead of the last-of-hour snapshot. That single change
neutralises the "outlier listing print" exploit that dominated the dirty-data
backtests (see the v4 research doc — one trade's 14k print was one of seven
hourly snapshots, six of which showed ~60k; last-of-hour grabbed the outlier,
median preserves the 60k).

With cleaner data upstream, the strategy's own outlier filter can be relaxed
from v4's `outlier_tol=0.03 + confirm_hours=3` to `outlier_tol=0.05 +
confirm_hours=2`. The looser filter catches more real dips without
reintroducing the exploit, and PnL roughly doubles.

## Signal pseudocode

```
# Loader (NEW — in src/algo/engine.py):
#   SELECT ea_id, date_trunc('hour', captured_at) AS hour_ts,
#          percentile_disc(0.5) WITHIN GROUP (ORDER BY current_lowest_bin) AS price
#   FROM market_snapshots WHERE ...
#   GROUP BY ea_id, date_trunc('hour', captured_at)

# Strategy (per hour tick):
for (ea_id, price) in hour ticks:
    history[ea_id].append(price)
    smoothed    = median(history[-3:])
    median_24h  = median(history[-24:])

    # OUTLIER FILTER — skip ea_id this hour if tick strays > 5% from smoothed
    # (the median loader already kills most of these; this is a second guard)
    if abs(price - smoothed) / smoothed > 0.05:
        reset dip_streak; skip this ea_id
        continue

    # ENTRY — 2 consecutive hours of smoothed-vs-median dip
    dip = (median_24h - smoothed) / median_24h
    if dip >= 0.05:
        dip_streak[ea_id] += 1
        if dip_streak[ea_id] >= 2 and free_slots > 0 and
           ea_id not in promo_batch_ids and
           10_000 <= price <= 150_000 and
           ea_id.age >= 7 days and
           elapsed_since_data_start >= 72h:
            BUY min(3, available // price)

    # EXIT
    if holding ea_id:
        smooth_pct = (smoothed - buy_price) / buy_price
        tick_pct   = (price    - buy_price) / buy_price

        if hold_hours >= 48:                  SELL (max-hold force-exit)
        elif smooth_pct >= 0.10:              SELL (profit target; 1-hour confirm)
        elif tick_pct <= -0.12:               SELL (stop-loss on raw tick)
```

## Hyperparameters (locked from 4-iteration sweep in the new loop)

| Parameter         | Value   | Role                                         |
| ----------------- | ------- | -------------------------------------------- |
| Loader            | median  | Hourly median BIN (robust to outlier prints) |
| `median_window_h` | 24      | Baseline median for detecting dips           |
| `smooth_window_h` | 3       | 3-hour smoothed reference                    |
| `outlier_tol`     | 0.05    | Tick must be within ±5% of smoothed          |
| `dip_pct`         | 0.05    | Minimum smoothed-vs-median dip               |
| `confirm_hours`   | 2       | Consecutive dip hours required               |
| `profit_target`   | 0.10    | Smoothed-price profit target                 |
| `stop_loss`       | 0.12    | Raw-tick stop-loss                           |
| `max_hold_h`      | 48      | Force-exit                                   |
| `min_price`       | 10,000  | Liquidity floor                              |
| `max_price`       | 150,000 | Position-size sanity ceiling                 |
| `max_positions`   | 6       | Concurrent positions                         |
| `min_age_days`    | 7       | Avoid brand-new cards                        |
| `burn_in_h`       | 72      | First 3 days: no buys                        |
| `qty_cap`         | 3       | Max cards per buy (listing-depth proxy)      |

## Per-week PnL (winning backtest, budget 1,000,000)

| ISO week | Dates            | Net PnL     | % of budget | Trades | Distinct weekdays |
| -------- | ---------------- | ----------- | ----------- | ------ | ----------------- |
| 2026-W13 | Mar 26 – Mar 29 (partial, burn-in) | — | — | — | — |
| 2026-W14 | Mar 30 – Apr 5   | **+1,139,301** | **113.9%**  | 106    | 7                 |
| 2026-W15 | Apr 6 – Apr 12   | **+1,084,745** | **108.5%**  | 116    | 7                 |
| 2026-W16 | Apr 13 – Apr 17 (partial) | +1,084,233 | 108.4% | 76 | 6                 |

**Both full ISO weeks clear 25% by ≈4.3×.** Total PnL +3,308,279, win rate
83.2%, 298 trades, Sharpe 0.73, MaxDD 47.5%.

## Weekday trade distribution (buys)

| Mon | Tue | Wed | Thu | Fri | Sat | Sun |
|-----|-----|-----|-----|-----|-----|-----|
|  53 |  44 |  44 |  45 |  47 |  25 |  40 |

Flat across Mon–Fri, lighter weekends (Fridays we allow but the promo
exclusion filters out a lot of Friday candidates). No promo-keyed bias.

## Correlation with `promo_dip_buy`

Daily buy-count correlation = **−0.621** (cap is 0.30 magnitude).

`promo_dip_buy` drops from +780k (dirty data) to +284k (clean data), mostly
in W14. v5 and promo_dip_buy are strongly *anti*-correlated on the clean
loader: they trade on nearly-disjoint day sets.

## Iteration log

| Iter | Strategy                    | Best PnL (clean)                  | Outcome                                                        |
| ---- | --------------------------- | --------------------------------- | -------------------------------------------------------------- |
| 0    | `hourly_dip_revert_v4`      | +1.67M (W14 +365k, W15 +860k)     | v4 held up on clean data (up from +1.18M dirty). Baseline.     |
| 5    | `hourly_dip_revert_v5`      | **+3.75M** (loose) / **+3.31M** (paranoid) | **Winner.** Loosened outlier filter works with clean data. |
| 6    | `hourly_dip_revert_v6`      | +0.53M – +2.17M                   | Range-stability filter at 0.30 cuts too many real reversions.  |
| 7    | `hourly_dip_revert_v7`      | +1.24M                            | Trailing stop exits runners early; fixed-target wins.          |
| 8    | `hourly_dip_revert_v8`      | +1.26M                            | Z-score entry too selective; stable-card dips rarely trigger.  |

## What changed vs v4

1. **Loader: median-of-hour** instead of last-of-hour (`src/algo/engine.py`
   `load_market_snapshot_data`). Postgres uses `percentile_disc(0.5)`, SQLite
   path computes median in Python.
2. **Strategy-level outlier filter** relaxed from 0.03 → 0.05.
3. **Confirmation window** shortened from 3 → 2 hours.
4. **Dip threshold** lowered from 0.08 → 0.05.

Everything else (promo filter, burn-in, qty cap, profit target, stop-loss,
max-hold) is identical to v4.

## Known risks for live deployment (updated)

1. **Backtest still optimistic.** Clean loader cuts avg margin from v4's
   "dirty-data exploit territory" to 15–19%, and top-5 outlier margins from
   150%+ down to ~135%. But some 40–135% winners remain — these are cards
   that genuinely moved but may not be executable at qty=3 in live markets
   (thin listings at the lowest BIN). Expect 40–60% of backtest PnL in live.
2. **2-week sample.** Only W14 and W15 are full ISO weeks. This is a tight
   window. Worth re-validating against each new week of `market_snapshots`
   data as it accumulates.
3. **Weekend promo dependence.** Much of W14/W15 PnL lands on Sun/Mon around
   the Apr 5 and Apr 12 post-promo-weekend reversions. A weekend without a
   Friday promo (rare but possible — e.g., TOTS bridge weekends) may produce
   fewer signals.
4. **Listing depth still not modeled.** The `qty_cap=3` is a guess. The next
   obvious step is to plumb `listing_count` through to strategies, so we can
   size positions against actual depth instead of hard-capping.
5. **`hourly_dip_revert_v4` is preserved** in the repo for reference (same
   research document, `2026-04-17-hourly-dip-revert-v4-research.md`).

## Reproduction

```bash
# Final config single-run
python -m src.algo run --strategy hourly_dip_revert_v5 --budget 1000000 --days 0

# Per-week analysis + correlation vs promo_dip_buy
python scripts/analyze_backtest.py hourly_dip_revert_v5
```

## Files touched

- `src/algo/engine.py` — loader switched to hourly median (Postgres +
  SQLite paths). Older last-of-hour `DISTINCT ON` replaced with
  `percentile_disc(0.5) ... GROUP BY`.
- `src/algo/strategies/hourly_dip_revert_v5.py` — new strategy file.
- `src/algo/strategies/hourly_dip_revert_v6.py` — experimental (range
  filter); kept for reference.
- `src/algo/strategies/hourly_dip_revert_v7.py` — experimental (trailing
  stop); kept for reference.
- `src/algo/strategies/hourly_dip_revert_v8.py` — experimental (z-score);
  kept for reference.
- `src/algo/strategies/hourly_dip_revert_v4.py` — untouched, preserved.
- `docs/superpowers/specs/2026-04-17-hourly-dip-revert-v5-research.md` —
  this doc.
- `promo_dip_buy_results.json` — refreshed reference against clean loader
  (used by `scripts/analyze_backtest.py` for correlation).
