# iter70 — monday_floor_v1 (NULL RESULT)

## Angle
Time-causal floor-anchor: Monday/Tue 6-14 UTC, buy when price <= 1.05x trailing-7d min, hold to +20% / 168h.

## Supporting data points (from .planning/profit_opportunities.json, 1850 pessimistic opps)
- Monday = 33% of opps (619/1850); Mon+Tue+Fri = 67%.
- 44.5% of opps (824/1850) have buy_price <= 1.05x card-window-min, median ROI 32.8%.
- 309 opps (16.7%) hold 96-168h with median ROI 51.6%.

## Result
- Filtered (min-sph 2): -$4.3k / 8 trades / 25% win — far below 45% precision, 80 fire bar.
- Unfiltered: -$45.8k / 12 trades / 16.7% win.

## Killed by
**Continuation again.** The retrospective signal (buy was within 5% of card's 27d-min, then ROI'd up) is unconditional on whether THAT particular floor was a real bottom. At tick time, we can't distinguish:
  - real floor that bounces (the 824 opps in the data) from
  - mid-fall stop on the way to a new lower floor (e.g. Salah 24,750 -> 19,500, -21%; Yuri Berchiche flat-flat losses).

The data showed "44.5% of upside-opps started at floor proximity" but the BASE RATE of "cards at floor proximity in any given hour" was not measured — likely most of those are still falling. Same root cause as drawdown_reversion v1-v4.

Additionally: only 8-12 fires in 27d (cooldowns + age gates too restrictive given the whitelisted universe is 715 cards). Even if precision were OK, fire count is 10x too low.

## Scope verification
Only files staged for commit:
  - src/algo/strategies/monday_floor_v1.py (new)
  - monday_floor_v1_filtered_results.json (new)
  - monday_floor_v1_unfiltered_results.json (new)
  - .planning/iter70_monday_floor_v1.md (this file)

## Next direction
Avoid floor-proximity-only signals — they all hit the continuation killer. Try angle 2 (daily_listing_summaries volume-regime entry) or angle 5 (concentrate larger qty on FEWER higher-conviction signals).
