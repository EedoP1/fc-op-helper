# Iter 71 — Daily-bar regime detection

## Daily table schema

`daily_listing_summaries`: `(ea_id, date, margin_pct, op_listed_count,
op_sold_count, op_expired_count, total_listed_count, total_sold_count,
total_expired_count)`. NO daily price (open/high/low/close); per-margin OP
breakdown only.

Date coverage: **2026-04-12 .. 2026-04-24** (12 days only; opps span W13-W17).

## Hit-rate test (realistic exec: BUY@max, SELL@min, 5% tax)

Universe: whitelist (rating 86-91 + repeater types) ∩ liquid (sph>=2) ∩
has-daily ∩ has-hourly = **228 ea_ids → 1,120 (ea, day) feature rows**.

Base rates (ALL feature rows):
- net_roi >= 20% within 96h: **23.0%**
- mean net ROI: **+8.9%**
- continuation -5% in 24h: **85.7%** ← universal pessimistic-loader floor

Top precision gates (>=10 fires):

| gate | fires | prec(>=20%) | mean_roi | cont_24h |
|---|---|---|---|---|
| trend_3d <= -0.10 | 311 | 35.4% | +16.4% | 92.6% |
| trend_3d <= -0.15 | 238 | 38.2% | +18.1% | 92.9% |
| trend_3d <= -0.05 & op_demand>=1.5 | 126 | 30.2% | +12.8% | 92.9% |
| vol>=1.5 & trend3d<=-0.05 | 15 | 40.0% | +22.9% | 100% |

**Continuation FAILS the 35% bar in every gate.**

## Outcome

Implemented `daily_trend_dip_v1` with the best clearable combo (trend_3d<=-0.05,
op_demand>=1.5, fire 1×/day at 00 UTC, hold 96h, smoothed-stop=10%).

Backtest results:
- **Filtered (sph>=2): -$384k, 45 trades, 35.6% win, MaxDD 99%**
- **Unfiltered: -$400k, 56 trades, 37.5% win, MaxDD 99%**

## Why it failed

The +16% MEAN ROI was measured at SELL@MAX(min over future). The actual
backtest engine does pessimistic-loader exits — a 10% smoothed-stop trips on
the first hour where the loader's per-hour min dipped 10% below entry-max,
which the 92.6% continuation rate predicted would happen on essentially
every entry. EA tax + bid-ask spread eats winners; stops dump losers at
the worst loader-pessimism point.

This confirms the recurring killer: the ~9.6% pessimistic-loader break-even
isn't escapable by smoothing the trigger — exits also live in loader-min
land. Daily-bar regime detection identified directionally-correct entries
(35% of dips do return >=20% net within 96h) but those returns can't be
captured with any stop tighter than the loader's natural ~10-15% intraday
range.

## Next direction (for parent loop)

Don't fight loader pessimism with smoothed exits. Either:
1. **No stop at all** — only profit_target / max_hold_h (let losers run for
   the full 96h, since 35% will turn). Test daily_trend_dip_v2 with
   stop_loss removed.
2. **Skip daily-regime entirely** — combine with v19 floor-band (the only
   strategy that wins, because $10-13k cards' loader-min is bounded by
   the EA price floor itself).
