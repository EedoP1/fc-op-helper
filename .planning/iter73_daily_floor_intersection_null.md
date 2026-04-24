# iter73 — daily_trend_dip × floor_band intersection — NULL RESULT

## Hypothesis
Combine v2's daily entry gate (trend_3d ≤ -0.05 AND op_demand@10 ≥ 1.5) with
v19's floor band (buy_price ≤ $13.5k) to bound max-hold drift losses while
preserving v2's 60.5% win rate and orthogonal contribution to v19.

## Pre-analysis result — refuted

Of v2's 38 filtered trades:

| Buy-price band | Trades | Sum PnL  | Wins  | Overlap w/ v19 ea_ids |
|----------------|--------|----------|-------|-----------------------|
| ≤ $13,500      | 2      | -$4,600  | 1/2   | 0 / 2                 |
| ≤ $15,000      | 3      | -$2,850  | 2/3   | 0 / 3                 |
| ≤ $20,000      | 8      | +$34,762 | 6/8   | 0 / 8                 |

v19 stack: 19 trades, +$480.4k. All 8 v2 sub-$20k trades miss v19's ea-id set
(orthogonal — good), BUT marginal contribution is only +$34.7k at best,
nowhere near the $1.3M needed to close the $2M stack gap.

## Why the gate barely fires in the floor band

`trend_3d ≤ -0.05` requires a measurable downward 3-day move. Cards parked at
the EA hard floor ($10-13k) are already absorbed there — they don't generate
trend. v2's catastrophic drains (84122541 at $79.5k → -$32k; 84108572 at $89.5k
→ -$29k; 67363107 at $93k → -$38k; 67324180 at $62k → -$36k) all sit ABOVE
$60k where there's room to drift down 5%+ over 3 days. The intersection
literally selects against itself: the band where the gate fires reliably is
precisely the band that produces the drains the gate is supposed to fix.

## Pre-sim verdict (without running backtest)

- Fire-count: 2 trades / 27d at $≤13.5k = 0.07/day (way below ≥30 threshold)
- Marginal stack PnL: ≤ +$35k (band ≤ $20k); ≤ -$5k (band ≤ $13.5k)
- Both bands fail the stack-meaningful contribution test

## Decision: skip implementation, pivot

Skipping coding per honesty rule. Next iteration should attack the v2 drift
drains differently:

- **Trim by buy_price ceiling** ($60k cap to exclude all 5 catastrophic
  drains, which all bought at $62k+) rather than floor — would preserve the
  $20k-$60k winners (~$220k of v2 wins concentrated there)
- **OR** different exit: opportunistic smoothed-trail-stop after position has
  been in profit ≥ +5% (cuts the four >$25k drains without firing on entry-day
  drift)
- **OR** require trend reversal confirmation in 24h after entry (filters the
  cards that just keep dumping)
