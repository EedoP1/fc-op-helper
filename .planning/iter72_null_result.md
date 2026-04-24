# Iter 72 — daily_trend_dip_v2 (stop-tolerance test): NULL RESULT

## Hypothesis
v1 (-$384k, 35.6% win) was killed by smoothed/hard stops cashing 92.9% loader-min
continuations into eventual recoveries. The entry gate showed +12.8% mean forward
ROI and +35.4% precision in EDA, suggesting it was directionally right.

Test: keep v1's gate, REMOVE all stops, exit only on profit_target +20% net or
max_hold_h. Pre-simulate which max_hold makes the entries profitable.

## Pre-simulation (re-running v1's 45 entries)
| max_hold | exit-price model | net PnL | win% | PT hits |
|----------|------------------|---------|------|---------|
| 96h      | hour median      |  -$3k   | 57.8 | 46.7%   |
| 96h      | hour min         | -$44k   | 51.1 | 33.3%   |
| 144h     | hour median      | +$208k  | 66.7 | 60.0%   |
| 144h     | hour min         | +$180k  | 62.2 | 46.7%   |

Picked: max_hold_h=144h, profit_target=0.20, NO STOP. Both pre-sim models
projected ~+$180k–$208k from the same 45 entries.

## Backtest reality
- Filtered (--min-sph 2): **-$91,463**, 38 trades, 60.5% win
- Unfiltered:             **+$35,483**, 43 trades, 58.1% win

Improvement vs v1: +$293k filtered, +$420k unfiltered. Big directional improvement
but still loss-making for the stack.

## Why pre-sim was off (~+$180k → -$91k = -$270k miss)
1. **Engine fills at live tick price, not hour-median.** When the strategy "smoothed
   close >= +20% net" trigger fires, the engine sells at the next tick's actual
   loader-min price — typically 8–12% below the smoothed value. PT hits realized
   significantly less than the pre-sim's gross-target assumption.

2. **Max-hold survivors are catastrophic.** 7 trades held to max_hold_h=144h
   with no recovery, sum loss ~-$240k of -$312k loss bucket:
   - Diego Luna: -$40,600 (held 144h, drift -42%)
   - Pierre Højbjerg-class: -$38,375 / -$36,350 / -$32,000 / -$29,175 / -$27,525
   - Pre-sim's `last_hour_min` exit assumed last bucket has data; reality is the
     drift kept compounding past the 96h decision window.

3. **Portfolio state divergence.** With longer holds (144h vs ~12h average for v1),
   slot-cap (12 positions) blocked some v1 entries → only 36/45 entry overlap (80%).
   The 9 "missed" v1 entries were among the worst losers, biasing v2 toward the
   surviving subset that pre-sim happened to hit.

## Lesson
"Mean forward ROI +12.8%" on a daily gate is an EDA artifact of pessimistic
loader-drag asymmetry: forward-window MAX includes brief loader spikes that
strategies can't realistically capture, while forward-window MIN (where stops
trigger and where max-hold exits land) is structurally lower. Neither stop nor
no-stop fully fixes this — the gate's true realized expectancy is closer to
break-even than EDA implies.

## Next direction
The "intersect daily-regime gate with floor_buy_v19's $10–13k floor band"
fallback from the iter prompt is unused (because v2 was directionally
positive enough not to pivot mid-iter). That intersection remains a candidate
for iter 73: floor_buy_v19's downside-bounded band could neutralize the
catastrophic max-hold survivors that killed v2's PnL.
