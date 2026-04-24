# iter77 — daily_trend_dip v6 re-entry formalization (NULL RESULT)

## Hypothesis tested
After v5's smoothed-stop frees capital, formally re-enter the same ea_id 24-72h
later if the daily_trend_dip gate re-fires. The +$20k Robin Roefs "happy
accident" in v5 suggested this might be a repeatable edge.

## Pre-analysis on v5 filtered trades

v5 booked +$142,899 / 31 trades / 67.7% win across the full window.
Classifying every trade by exit reason (hold_h + raw pct):

- profit_target (raw_pct >= 18%, hold < 144h): 11 trades
- max_hold (hold_h >= 143): 1 trade (50565012, -$11,550)
- smoothed_stop (raw_pct <= -20%): **2 trades**
- other (early sells via profit_target on smoothed price >= 20% but raw drift,
  or housekeeping at sub-target levels triggered by max_hold approximations):
  17 trades

### The two stop events
| ea_id    | stop_at              | stop_px | buy_px | recovery (price reached later) |
|----------|----------------------|---------|--------|--------------------------------|
| 50595446 | 2026-04-15 13:00 UTC | $32,750 | $49,750| $14k floor → $19k by 2026-04-24 |
| 50590888 | 2026-04-18 08:00 UTC | $26,750 | $42,750| $17k floor → $42k by 2026-04-23 |

### Re-entries actually observed in v5
- 50595446: re-entry happened ORGANICALLY in v5 at 2026-04-18 00:00 @ $14k →
  sold $17,750 → +$20,037. The gate fired naturally; no formalization needed.
- 50590888: NO re-entry in v5 trades, despite a clear price recovery from
  $17k floor to $42k peak by 2026-04-23. The gate did not re-trigger
  (likely trend_3d at fire_hour 00:00 was not <= -0.05 once price had
  already started recovering, or op_demand@10 < 1.5).

### Viable re-entry count
**1 of 2 stops produced a viable re-entry, and that 1 is already booked
in v5's PnL.** A second re-entry on 50590888 would have required either:
(a) relaxing the gate (look-ahead / overfit risk), or
(b) unconditional re-entry at a fixed interval (catches recovery here but
likely catches knife-falling cards too in the broader population).

## Decision

Per the iter77 anti-overfit rule: "If pre-analysis identifies fewer than 3
distinct re-entry opportunities in v5's data, the signal is too sparse to
formalize — null result."

We have **1** organic re-entry already captured by v5's natural daily gate.
Formalizing this as a feature on n=1 is textbook overfit. The +$20k Robin
Roefs trade is not evidence of an exploitable re-entry edge — it's evidence
that v5's gate already does the right thing when conditions repeat.

**No v6 strategy file written. v5 remains the line champion at +$142.9k.**

## Stack snapshot
- v5 organic: +$142.9k filtered (delta vs v4 +$69.4k)
- Total stack: ~$848k (no change this iter)
- Gap to $2M: ~$1.15M

## Next direction candidates
1. Investigate WHY 50590888's recovery failed to re-trigger the gate —
   if trend_3d > -0.05 once recovery starts, the gate is too strict for the
   bounce phase. A separate "post-stop bounce" strategy with a relaxed
   trend gate (e.g. trend_3d <= +0.10) and a positive momentum filter
   (price > 5d-low * 1.3) might catch these without overfitting to n=1.
2. Different orthogonal axis: extend the dual-band lower edge (sub-$11k
   floor cards) to capture the stop-target population entirely.
3. Different strategy line: bring v19_ext or post_dump_v15 ideas into a
   merged stack rather than chasing more $20-70k margin per iter on
   daily_trend_dip alone.
