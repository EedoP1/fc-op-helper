# Iter 69 — Stabilization-Detection Gate Analysis

## Question
Iters 66-68 all killed by continuation (buying mid-fall, price keeps dropping).
Does a "stabilization confirmed" gate (local bottom of last 3-6h) fix this?

## Step 1: data-first gate scan — found a winner

Method:
- 1,850 pessimistic opps from `.planning/profit_opportunities.json`, filtered to
  whitelist cards (rating 86-91, repeater card_types) → 884 opps, 772 with
  enough pre-buy history.
- 8,000 random (ea_id, hour) entries sampled from same whitelist universe,
  liquidity floor (listing_count >= 10) → 7,961 negatives.
- At each buy_hour, compute features on prior-72h hourly medians.

Top gates (precision = pos_fire / (pos_fire + neg_fire), base_rate 8.8%):

| Gate                                                      | pos | neg  | prec  | lift  |
|-----------------------------------------------------------|-----|------|-------|-------|
| dd72h>=25%                                                | 520 | 1787 | 22.5% | 2.55x |
| dd72h>=30%                                                | 415 | 1218 | 25.4% | 2.88x |
| dd72h>=30% AND local_bottom_6h                            | 369 |  475 | 43.7% | 4.95x |
| dd72h>=35% AND local_bottom_6h                            | 271 |  329 | 45.2% | 5.11x |
| dd72h>=40% AND local_bottom_6h                            | 200 |  221 | 47.5% | 5.37x |
| **dd72h>=30% AND local_bottom_6h AND lc_24h>=20**         | **220** | **192** | **53.4%** | **6.04x** |
| dd72h>=30% AND local_bottom_6h AND lc_24h>=30             |  27 |   25 | 51.9% | 5.87x |

The 53.4% / 412-fire gate clears both the 50% precision and 100-fire thresholds.

## Step 2: strategy backtest — FAILED catastrophically

Strategy `stab_bottom_v1` with that gate + tight exits (25% target / -12% smooth /
-15% hard / 120h max hold):

- **Filtered (min-sph 2)**: -$732,870 total, 96 trades, 43.8% win, organic -$761,720
- **Unfiltered**: -$676,338 total, 121 trades, 51.2% win
- Organic win rate: 42%. Median trade ROI: -1.4%. Avg trade ROI: -2.1%.
- Per-week organic: W14 -$334k, W15 -$164k, W16 -$251k, W17 -$12k. Catastrophic in all but W17.
- Unfiltered > filtered → edge is NOT liquidity-driven; lc gate is hurting not helping.

## Root cause — loader drag, not bad gate

The data-first analysis used **hourly median** price (same basis as
`profit_opportunities.json`). The backtester executes **BUY@max / SELL@min**
within the hour (pessimistic loader). Trade sample shows many positions
hitting -22% to -33% within 3-8h of entry — these are the classic continuation
tail. The "local_bottom_6h" signal is satisfied at the median, but the
buy price is at the hour-max, often 5-10% ABOVE the median floor, pushing
entry into territory where residual continuation still triggers the -15%
hard stop.

Cross-reference: `project_pessimistic_loader_drag.md` — "BUY@max/SELL@min
imposes ~9.6% break-even; random entries lose ~9% per 48h trade." The lift
from 8.8% base-rate to 53.4% precision (6x) is insufficient to cover the
~9.6% break-even because:
- The "precision" of 53.4% is against FILTERED positives that are ALREADY
  selected for being profitable at the median. The actual continuation rate
  of a median-detected local-bottom at max-of-hour execution is much higher.
- Hard-stop losses average -$25k/trade × ~50 trades = -$1.25M gross, which
  swamps the +$10-30k wins in the ~40 profitable trades.

## Compare to iters 66-68

Same continuation root cause, same catastrophic W14/W15/W16 losses, same
pattern of ~40-50% trade win rate but negative average PnL because losers
are 2-3x bigger than winners. Adding local_bottom_6h to the gate reduces
fire count (412 vs thousands) but does NOT break the loader-drag wall.

## Conclusion

The stabilization-gate hypothesis is **falsified for pessimistic execution**.
To break continuation with a BUY@max executor, we'd need either:
1. Execution smoothing (limit-buy below max, not tested here — engine doesn't
   support it), or
2. A DIFFERENT primary signal where even max-of-hour entries preserve edge
   (e.g., whitelist-expiry patterns, supply-absorption that does not invert,
   or multi-period confirmation like daily bottom + hourly stab, which my
   analysis didn't test because hourly-only features exhausted the promising
   space).

Iter 69 result: **wip loss**, root cause = median-vs-max execution gap on
stabilization signal, NOT a new failure mode beyond iters 66-68.
