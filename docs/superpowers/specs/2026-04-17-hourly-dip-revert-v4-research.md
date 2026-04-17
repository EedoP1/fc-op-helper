# Hourly Dip Reversion v4 — 25%/week Strategy Research

## Summary

`hourly_dip_revert_v4` is a non-promo intraday dip-reversion strategy that buys
player cards whose 3-hour smoothed price has fallen ≥8% below their 24-hour
rolling median for three consecutive hours, then sells on a 10% profit target
(also confirmed over multiple hours) or a 12% stop-loss. It filters out promo-
batch cards (to keep signal independent from `promo_dip_buy`) and rejects any
tick whose raw price deviates >3% from the 3-hour smoothed price — this
neutralises hour-bucket outlier prints that the backtester's DISTINCT ON hour
selection leaks.

Across the available dataset (`market_snapshots` Mar 26 – Apr 17, 2026; ~6.6M
raw snapshots across 2,116 cards), the strategy hit **≥25% of starting budget
every full ISO week**, traded on **all 7 weekdays every week**, and shows
**-0.25 daily-count correlation with `promo_dip_buy`** — all three success bars.

## Signal pseudocode

```
for each hour tick (ea_id, price):
    history[ea_id].append(price)
    smoothed = median(history[ea_id][-3:])
    median_24h = median(history[ea_id][-24:])

    # OUTLIER REJECTION — throw away any tick that the backtester's
    # hour-bucket may have caught on a sparse, non-representative listing
    if abs(price - smoothed) / smoothed > 0.03:
        reset streaks; skip this ea_id for buy/sell this tick

    # BUY: confirmed dip against 24h median
    dip = (median_24h - smoothed) / median_24h
    if dip >= 0.08:
        dip_streak[ea_id] += 1
    else:
        dip_streak[ea_id] = 0

    if not holding and dip_streak[ea_id] >= 3 and
       price in [10_000, 150_000] and
       ea_id not in promo_batch_ids and
       ea_id.age >= 7 days and
       elapsed_since_data_start >= 72h:
        BUY min(qty_cap=3, available // price)

    # SELL: profit target on smoothed price, 2-hour confirmation,
    #       stop-loss on raw tick, hard max-hold exit
    if holding:
        smooth_pct = (smoothed - buy_price) / buy_price
        if smooth_pct >= 0.10:
            profit_streak[ea_id] += 1
        else:
            profit_streak[ea_id] = 0

        if profit_streak[ea_id] >= 2: SELL
        elif tick_pct <= -0.12:       SELL
        elif hold_hours >= 48:        SELL
```

## Hyperparameters (locked from sweep)

| Parameter         | Value    | Role                                        |
| ----------------- | -------- | ------------------------------------------- |
| `median_window_h` | 24       | Baseline median for detecting dips          |
| `smooth_window_h` | 3        | Rolling median for tick denoising           |
| `outlier_tol`     | 0.03     | Rejects ticks >3% off the smoothed price    |
| `dip_pct`         | 0.08     | Minimum smoothed-vs-median dip to qualify   |
| `confirm_hours`   | 3        | Consecutive dip hours required to buy       |
| `profit_target`   | 0.10     | Smoothed-price profit target                |
| `stop_loss`       | 0.12     | Raw-tick stop-loss                          |
| `max_hold_h`      | 48       | Force-exit                                  |
| `min_price`       | 10,000   | Liquidity floor                             |
| `max_price`       | 150,000  | Position-size sanity ceiling                |
| `max_positions`   | 6        | Concurrent positions                        |
| `min_age_days`    | 7        | Avoid brand-new cards (noisy discovery)     |
| `burn_in_h`       | 72       | First 3 days no buys (kills cold-start)     |
| `qty_cap`         | 3        | Max cards per buy (approximates listing depth) |

## Per-week PnL (winning backtest, budget 1,000,000)

| ISO week | Dates          | Net PnL     | % of budget | Trades | Distinct weekdays |
| -------- | -------------- | ----------- | ----------- | ------ | ----------------- |
| 2026-W13 | Mar 26 – Mar 29 (partial) | — | — | — | (burn-in period, no buys) |
| 2026-W14 | Mar 30 – Apr 5 | +319,529    | 32.0%       | 49     | 7                 |
| 2026-W15 | Apr 6 – Apr 12 | +432,138    | 43.2%       | 65     | 7                 |
| 2026-W16 | Apr 13 – Apr 17 (partial) | +426,440 | 42.6% | 50 | 7 |

**Both full weeks clear 25% by a wide margin.** Partial week 16 is on track
for ≥42% as well.

Total PnL over 18 trading days: **+1,178,107** (117.8% return on starting
1M budget). Win rate 68.3%, trades 164.

## Weekday trade distribution

| Weekday | Buy count |
| ------- | --------- |
| Mon     | 35        |
| Tue     | 20        |
| Wed     | 23        |
| Thu     | 27        |
| Fri     | 25        |
| Sat     | 14        |
| Sun     | 20        |

Coverage is broad and Monday-heavy — consistent with catching post-weekend
dips left behind by Sunday reward dumps. No weekday is empty. This confirms
the strategy is **not** a disguised Friday-keyed strategy.

## Correlation with `promo_dip_buy`

Daily buy-count correlation = **-0.252** (well below the 0.30 cap).

Intuition: `promo_dip_buy` only buys **Friday-created promo batches** after a
21% 12-hour rising-trend signal (typically Sat–Mon). `hourly_dip_revert_v4`
explicitly excludes those same promo-batch cards, and its trigger is a
*falling* smoothed price — the opposite direction. The two strategies
compose cleanly: one catches the promo-cycle rally, the other catches
non-promo intraday mean reversion.

## What I tried and why earlier iterations failed

| Iter | Idea                                                  | Outcome                            | Lesson                                                        |
| ---- | ----------------------------------------------------- | ---------------------------------- | ------------------------------------------------------------- |
| 1    | Simple dip-below-48h-median, skip Fridays             | +10.1M best combo, but W15 = 0     | Cold-start bias (Mar 28 had only 49k snapshots) + no W15 sig. |
| 2    | + burn-in + fixed coin cap + allow Fridays            | +34M, W14/W15 both huge            | Backtester hour-bucket leaks outlier BINs; exits at fake highs|
| 3    | + outlier rejection at 15% tolerance + qty cap        | +16M / +5M — still too high        | 15% tolerance passes too much; avg margin still ~21%          |
| 4    | Outlier tol 3%, 3-hour confirmation, qty 3 (final)    | +1.18M, 32%/43% weekly             | Pass all 3 bars with realism caveat still attached            |

## Known risks for live deployment

1. **Backtester margin inflation is real.** The raw-data inspection of a
   single +588k "iter 2" trade showed the backtester's hour bucket captured a
   14k listing at 05:58 when the sustained market was 45–62k; the next hour
   bucket captured a 59k "recovery" snapshot. In a live market, the 14k BIN
   would clear in seconds, so the buyer who sees 14k and the buyer who pays
   14k are rarely the same person. The outlier filter (3% tolerance) knocks
   this out of the easy cases, but **live returns will be lower** than the
   backtest — estimate maybe 40–60% of backtest PnL is robust, so plan for
   12–18% weekly in production. This still clears the "good strategy" bar
   even if it misses the 25% bar live.

2. **Dataset is 22 days long, 2 full weeks**. Two weeks is a small sample.
   The strategy may overfit seasonal effects (Easter promo at Apr 3, regular
   promo at Apr 10). I recommend re-validating as more weeks of
   `market_snapshots` data accumulate, especially weeks without Friday-promo
   events. A 4-week or 8-week validation window would be much more decisive.

3. **Listing depth assumption is optimistic.** `qty_cap = 3` is a guess at
   typical listings at the lowest BIN. If the actual depth at the 8%-below
   price is 1, buys will fail or execute at higher prices. The live bot
   needs to verify listing counts before placing snipes.

4. **No liquidity filter beyond price band.** The strategy might fire on
   niche cards that happen to dip 8% because nobody was listing. The
   backtester doesn't model this; live trading needs a `listing_count >= N`
   filter (not currently exposed to strategies by `load_market_snapshot_data`).

5. **Max hold = 48h** is aggressive. During a market-wide sell-off (e.g.
   TOTS reveal or bugged promo) a 48-hour stop-out could force-sell on a
   still-falling tape. Consider adding a 48h-average-rising confirmation
   before the force-exit.

6. **Compounding tested up to +1.18M.** Position sizing is currently qty-capped
   at 3 per card, so the strategy naturally doesn't compound as aggressively
   as iter 1/2. But at scale the portfolio may leave too much idle cash —
   later work should replace `qty_cap` with a listing-count-aware sizer.

## Reproduction

```bash
# Confirms single-config run producing 1.18M over 22 days
python -m src.algo run --strategy hourly_dip_revert_v4 --budget 1000000 --days 0

# Analyze the produced trade log
python scripts/analyze_backtest.py hourly_dip_revert_v4
```

`backtest_results.json` will contain the full trade log; `promo_dip_buy_results.json`
in repo root is the reference file the analyzer uses to compute correlation.
