# Algo 25%/wk Search — Strategic Pivot Report

17 iterations of diverse, informed hypotheses. Every iteration failed the
combined bar (all 6 verdict bars passing AND ≥25% organic PnL on W14/W15/W16).
Only baseline post_dump_v15 passes the 6 bars — but at ~5%/wk, far below target.

This is a *findings* doc and pivot proposal, not a final verdict. The
underlying target may still be achievable with one of the changes below.

## What was tried (with root cause per lane)

| # | Strategy | Filtered PnL | Bars | Root cause |
|---|---|---|---|---|
| 1 | post_dump_v17 (scale all) | -$49k | 1/6 | scale-everything broke the edge |
| 2 | post_dump_v18 (qty_cap 6→8) | -$48k | 3/6 | sizing alone shifts fills into worse basket ticks |
| 3 | post_dump_v19 (cooldown 48→24) | -$138k | 0/6 | trade-count scaling fires on weaker signal |
| 4 | floor_buy_v25 (max_hold 240→96) | +$158k | 3/6 | 96h too short for 50% target; W14 still flat |
| 5 | combo_v3 (v15+v19 shared cap) | org -$190k | 0/6 | universe race cannibalizes; boundary 171% |
| 6 | floor_buy_v26 (target 0.25 + hold 72h) | -$29k | 2/6 | tight target converts winners to break-evens |
| 7 | hourly_dip_revert_v20 (verified) | org -$259k | 2/6 | mega-PnL was liquidity artifact; uf/f -33x |
| 8 | hourly_dip_revert_v21 (15% deep dip) | org -$211k | 3/6 | deep dips don't recover enough in W14 |
| 9 | batch audit (bollinger + 5 others) | best -$63k | 1/5 | all mean-rev variants absorb pessimistic drag |
| 10 | listings_surge_v1 (drop + flat) | -$487k | 2/6 | listings drop + flat = distressed withdrawal |
| 11 | listings_surge_v2 (drop + up) | -$870k | 3/6 | drop + rally = distribution TOP, not squeeze |
| 12 | timezone_arb_v1 (night dip buy) | -$859k | 3/6 | no circadian edge; caught Thu/Fri dump legs |
| 13 | timezone_arb_v2 (+ DoW gate) | -$471k | 2/6 | dip-tol entries still catch falling knives |
| 14 | floor_buy_v27 (+ 12h uptick filter) | org -$26k | 3/6 | momentum filter selects dead-cat bounces |
| 15 | global_rally_v1 (mirror of post_dump) | -$186k | 1/6 | rallies are selective; laggards stay weak |
| 16 | pre_promo_buy_v1 (Wed/Thu entry) | -$198k | 1/6 | pre-promo hype doesn't exist as a pattern |
| 17 | dual_trigger_leader_v1 | -$663k | 1/6 | v15's cheap-basket is load-bearing, not swappable |

## Key findings (market-structure level)

1. **post_dump_v15 is a local optimum that resists all perturbation.** Every
   attempt to scale on size, count, or basket selection destroys the edge.
   Its +$144k ceiling is real, not a parameter artifact.

2. **floor_buy_v19's edge is W16-concentrated and the 50% profit_target is
   load-bearing.** Any shortening of hold or target collapses the edge.
   W14 flatness is NOT a max_hold issue — both v22 (target 0.30 alone) and v25
   (max_hold 96 alone) independently fail to populate W14.

3. **Combos don't compose additively.** Overlapping universes cannibalize
   (v3); disjoint bands starve one arm (v2); 50/50 capital splits halve
   compounding (v1). Three distinct combo architectures all failed.

4. **Historical mega-PnL strategies were liquidity artifacts.**
   hourly_dip_revert_v20's old +$9.93M collapses to -$259k organic under
   --min-sph 2. uf/f ratio = -33. Bar 6 exists specifically to filter these.

5. **Bar 4 (|corr| vs promo_dip_buy) is a binding constraint.** Any
   profitable momentum-like signal correlates with promo_dip_buy and fails
   bar 4. Only orthogonal signals (global regime timing) survive — and
   they saturate at post_dump_v15's $144k ceiling.

6. **Rallies and dumps are structurally asymmetric in this market.** Dumps
   are indiscriminate (everything drops together) → post_dump works.
   Rallies are selective (only strong cards rally) → the laggards are
   genuinely weak, not catch-up candidates.

7. **Listings-count drops are BEARISH regardless of price direction.**
   Listings drop + price flat = distressed withdrawal. Listings drop + price
   up = distribution top. No valid long signal on listings-drop.

8. **Circadian / time-of-day bias doesn't exist cleanly** on this data.
   Night-dip entries caught dumps; day-of-week gates didn't rescue them.

9. **Pre-promo hype doesn't exist as a tradeable pattern.** Wed/Thu rallies
   largely revert before Friday.

10. **Structural W14 thinness is real.** W14 = 7 days of active strategy
    after burn_in. Strategies with 168h+ history requirements have almost
    no W14 organic closes. Only short-history signals (post_dump) can reach
    W14 — but they're capped at a low PnL scale.

## Why ≥25%/wk may be unreachable under current constraints

To hit +$250k organic on W14 requires:
- ~$35k/day PnL for 7 days on $1M budget (3.57%/day compound), which in turn needs
- Either (a) high-frequency scalping with real edge (ruled out: pessimistic
  loader's 9.6% drag eats small-move edges on liquid cards), or
- (b) Concentrated positions on confluence signals (ruled out: post_dump
  architecture is saturated at its current basket/sizing; can't be scaled),
  or
- (c) A strategy orthogonal to promo_dip_buy with materially larger edge
  than post_dump_v15 (not found in 17 iterations of diverse search).

The combination of pessimistic loader + --min-sph 2 + bar 4 + 22-day window
appears to **constrain achievable PnL below 25%/wk** for strategies that
pass all 6 bars.

## Proposed pivots (for user decision)

### Pivot A — Relax bar 4 (correlation bound)

If bar 4's |corr| ≤ 0.30 is binding (and iters 13-17 suggest it is), raise
it to ≤ 0.50 or ≤ 0.70. This would admit profitable momentum strategies
that genuinely capture edge but happen to correlate with promo_dip_buy's
timing. Tradeoff: less diversification between the shipped strategy and
the existing promo_dip_buy product.

### Pivot B — Adjust pessimistic loader

The current loader buys at each hour's max and sells at each hour's min.
In practice, listings priced at max rarely fill immediately and sellers
at min are rare. A more realistic loader (e.g., BUY@median+5%, SELL@median-5%,
representing the actual bid-ask cost) would likely unlock several previously
failing strategies without making the backtest optimistic.

### Pivot C — Accept post_dump_v15 at ~5%/wk

The only 6-bar-passing strategy is already production-ready. If the 25%/wk
target was aspirational and 5%/wk reliable is the achievable real number on
this market, ship post_dump_v15 and stop searching for an unreachable target.
Users making 5%/wk on a Chrome-extension-driven bot is already a compelling
product offering.

### Pivot D — Extend the backtest window

The 22-day window forces W14 to carry 7 days of active strategy at most.
Longer windows (6-8 weeks) would let long-hold strategies like floor_buy_v19
compound across multiple full weeks. This might not raise any single-week
return but would let us distinguish "strategy doesn't work" from "data
window too short for this strategy's cycle."

### Pivot E — Genuinely new data / signal source

Everything in src/algo/strategies/ uses price history (and listings for v1).
There's unused data: per-card trades log with sales timestamps (could drive
a sales-velocity surge signal that we couldn't test here without engine
modification). Integrating these would open new signal lanes.

## Recommendation

**Pivot A or B first.** They don't require new data collection or product
scope change — just acknowledge that the current test framework may be too
strict for the 25%/wk goal.

If neither A nor B unlock a passing strategy, **C** becomes the honest answer:
the 25%/wk target is not empirically achievable on this dataset, and
post_dump_v15's ~5%/wk is the real ceiling. Ship v15, set user expectation
at 5%/wk, move on.

## Reproducibility

All 17 strategy files and filtered/unfiltered result JSONs are committed
to main. Each iteration's commit message records PnL, win rate, bars passed,
and root cause. `scripts/verdict.py NAME` reproduces the bar analysis for
any strategy.

---
Generated after 17 iterations of `/loop` self-paced exploration.
Stopping the loop here; user can resume via `/loop ...` to continue past
iter 17 if new ideas surface.
