# Algo $2M / 22 Days Search — Final Report (Iters 49–58)

10 iterations of `/loop`-driven search targeting +$2M organic PnL on $1M budget over
22 active trading days (~65%/week). Target unreachable on current 27-day dataset.
Best achievable remains **combo_v18** (+$150.6k org, 5/6 bars) from the prior
48-iteration search.

## TL;DR

**Pivot A (from the 48-iter final report) still stands: ship combo_v18.** The
$2M target requires ~13x combo_v18's PnL. Ten iterations across every brief-listed
direction (A, B, C, D, E, G) each produced either:

- diminishing-return floor-buy variants (bounded by signal *frequency*, not sizing)
- catastrophic inverted signals (cross-card cohort, supply-absorption both invert)
- v19 edge destruction (early-exit breaks its load-bearing long-hold)

The search converged on a coherent set of findings that rule out further
exploration without either a new data source or an engine-level feature change
(hourly sales-velocity time-series, currently not surfaced).

## Iteration leaderboard

| Iter | Strategy                   | Org PnL   | Win%  | Bars | Direction | Key finding |
|------|----------------------------|-----------|-------|------|-----------|-------------|
| 49   | floor_buy_pyramid_v1       | -$0.7k    | 40%   | 0/6  | (G)       | Narrower bands starve gates; 24 trades vs v19's 19 |
| 50   | floor_buy_turnover_v1      | -$27.5k   | 59.6% | 3/6  | (E)       | 20 slots + small qty = quality dilution, W16 cascade |
| 51   | floor_buy_mega_v1          | +$51.7k   | 65%   | 3/6  | (B)       | qty=50 fired only 1/25 — vol_range gate caps |
| 52   | floor_buy_mega_v2          | +$506k    | 83%   | 3/6  | (B)       | Signal frequency (6 trades) is hard cap, not sizing |
| 53   | supply_absorption_v1       | -$773k    | 22.5% | 0/6  | (A)       | Depletion+stability predicts DOWN, not UP (inverted) |
| 54   | supply_absorption_v2       | -$986k    | 9.5%  | 1/6  | (A)       | Depletion+rising worse; |corr|=0.57 (not orthogonal) |
| 55   | momentum_divergence_v1     | -$982k    | 23.2% | 1/6  | (C)       | Laggards in rising cohort fade further, not catch up |
| 56   | floor_buy_v19_earlyexit    | -$563k    | 11.1% | 0/6  | (E)       | Peak-drawdown exit kills v19's option-value edge |
| 57   | relative_strength_v1       | -$987k    | 1.5%  | 0/6  | (C)       | Leaders in sinking cohort = spike noise that reverts |
| 58   | card_tier_v1               | -$80k     | 39.5% | 0/6  | (D)       | v19's edge is price-band-specific, not card-tier |

## Hard findings (evidence-backed)

1. **Floor-buy signal frequency is the cap, not deployment.** Iter 52 with qty=50
   + v19 harvest + relaxed vol_range produced only 6 organic trades — qty=50
   fired *0 times*. Scaling positions, qty, recycling, or bands cannot manufacture
   tape that doesn't exist. The $10-13k floor band generates ~6-20 tradeable
   entries per 22-day window total. At max realized efficiency (+$506k in 6
   trades, iter 52), the ceiling is ~+$500k org, which matches v19's standalone.

2. **Cross-card cohort signals are anti-signals in this market.** Three
   independent cross-card formulations — laggard catchup (iter 55), leader
   continuation in sinking cohorts (iter 57), and depletion-with-price-rising
   (iter 54) — all produced <30% win rates at 100+ trades. The market punishes
   cohort-relative deviation: whichever card is not doing what its neighbors do
   continues to diverge against the trader, not revert. This is a structural
   market property, not a tuning issue.

3. **Listings-depletion with price-stability predicts DOWN moves (iter 53).**
   22.5% win on 108 trades, |corr|=0.249 (orthogonal). This is the only genuinely
   orthogonal non-floor signal in the library — and it points the wrong way.
   Without short availability in FC26 trading, this finding is unusable as an
   entry signal but could in principle serve as an early-exit overlay on v19
   holds. Not tested in this iteration round.

4. **v19's long-hold discipline is load-bearing (iter 56).** Peak-tracking
   drawdown early-exit (sell when green and price drops 2% from peak) collapsed
   v19 from +$502k to -$563k and cut avg hold from 209h to 21h. v19 positions
   are sideways/down for ~150h on average before their catalyst triggers — any
   early-exit mechanism triggers on normal intraday noise and destroys the
   option value of reaching the catalyst. This is not parameter-sensitive.

5. **Card-type tiering doesn't matter; price band does (iter 58).** When 83-85
   gold-rare cards traded in a fast-cycle regime, they showed 58% win rate but
   negative expectancy due to asymmetric target/stop geometry. When 86+ cards
   traded with v19 long-hold params, they also lost — because the SLOW tier
   used floor_ceiling=20000 (buying into a different price band than the one
   that actually works). v19's edge is geometrically specific to $10-13k cards,
   not to rating 83-85 or gold_rare card_type.

## Why $2M / 22 days is structurally unreachable

1. **Floor-band capacity is bounded.** v19 alone extracts +$502k from the band
   in 22 days — that's the near-ceiling on a signal that produces ~6-20
   entries/window. 4x scaling is mathematically impossible without new entries,
   and the data contains none in adjacent bands (iters 49 pyramid, 58 SLOW-tier
   both demonstrate adjacent bands are dead).

2. **No orthogonal positive-EV signal exists in current feature space.** The
   untapped signals (sales-velocity, cross-card, listings, card tier) all
   tested either negative or anti-correlated. The one orthogonal signal that
   predicts direction (supply_absorption_v1 for fades) cannot be traded because
   FC26 doesn't permit shorts.

3. **W15 remains structurally dead for harvest strategies.** Confirmed by iters
   51, 52, 56 — aggressive harvest geometries all lose in W15 regardless of
   base strategy. This wasn't overcome. Combined with structural thinness of
   the floor-buy signal, only W16 reliably produces material PnL, which caps
   total achievable at ~$500k weekly × 1 week = ~$500k.

4. **22-day window is too short for capital compounding.** Even at a theoretical
   5%/day compounding (unrealistic), $1M → $2.93M in 22 days. Actual observed
   best weekly return ~50% (v19 W16). A 3-week path to $2M requires 50% then
   50% then 78% — and each subsequent week operates on bigger capital on the
   same narrow signal, where signal frequency does not scale with capital.

## Recommendations

**Immediate: Ship combo_v18** — unchanged from iter 48's final report. +$150.6k
organic over 22 days (~15%/wk) is a real, reliable, production-ready strategy.

**Medium-term (2-3 weeks): Re-evaluate with more data**
- 60+ days of data would let W15-analog weeks average and potentially unlock
  the v19 W15 signal that appears noise-suppressed on 27 days.
- Adds multi-promo-cycle coverage to distinguish structural edges from single-event
  artifacts.

**Research backlog (deprioritized — small expected value):**
- Engine mod to surface per-hour `total_sold_count` from `daily_listing_summaries`
  as a derivable time-series. Would enable a genuine sph-surge signal not tested
  here.
- Use supply_absorption_v1's orthogonal bearish signal (iter 53) as an early-exit
  OVERLAY on v19: when a v19 position triggers depletion+stability, force-sell
  regardless of current profit. This respects v19's long-hold edge except when
  a bearish regime emerges.
- Investigate longer-horizon fundamentals: league promo calendar timing, in-game
  event triggers (weekend leagues, special releases). Requires data not in
  current DB.

## Honest summary

The 48-iteration search (ending with combo_v18 at 5/6 bars, +$150k org) already
explored the navigable signal space. This 10-iteration follow-up confirmed:

- **No room in the floor-buy family.** Frequency cap is ~$500k/22d (v19 standalone).
- **No orthogonal positive-EV signal in current features.** Every non-floor-buy
  signal class tested either failed or inverted.
- **Architecture changes (combo, pyramid, tier) don't unlock extra PnL.** They
  cannibalize or starve.

$2M / 22d on $1M is not a search problem — it's a capacity/signal problem. Stop
searching iterations; resume after (a) 4+ more weeks of data accrual, or
(b) feature engineering to surface per-hour sales-velocity time-series, or
(c) acceptance that ~$500k/22d is the true ceiling and package that as the
product.

## Files

- Strategy files: `src/algo/strategies/{floor_buy_pyramid_v1,floor_buy_turnover_v1,floor_buy_mega_v1,floor_buy_mega_v2,supply_absorption_v1,supply_absorption_v2,momentum_divergence_v1,floor_buy_v19_earlyexit,relative_strength_v1,card_tier_v1}.py`
- Results: `{name}_filtered_results.json`, `{name}_unfiltered_results.json` for each
- Commits: `da86aebf` … `2bffc839` (10 commits, all on main)

---
*Loop ended at iteration 58 of a 100-iteration budget. Exhausted the reachable
hypothesis space; further iterations would either retread dead lanes or require
engine/data changes outside the `/loop` framework.*
