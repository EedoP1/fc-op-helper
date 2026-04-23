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

## Addendum — iters 59–62 (user re-opened loop)

User invoked /loop again after the first close. Four more iterations tested the
remaining combinations of iter 53's orthogonal bearish signal with v19, and audited
three calendar strategies that were untested in the original 48-iter search.

| Iter | Strategy                   | Org PnL   | Win%  | Bars | Key finding |
|------|----------------------------|-----------|-------|------|-------------|
| 59   | v19_absorb_exit            | -$117k    | 23%   | 1/6  | Bearish overlay as exit fires on 61% of trades — kills v19 winners |
| 60   | v19_absorb_exit_v2         | +$310k    | 67%   | 3/6  | Underwater-only guard halved overlay fires, still -$192k vs v19 |
| 61   | v19_entry_filter           | +$502k    | 94%   | 4/6  | Byte-identical to v19 — filter never fires (v19 gates already exclude) |
| 62   | audit 3 calendar strategies | — | — | 0/6 ea. | weekday_swing/weekly_cycle/saturday_massacre all fail all bars |

### Iters 59-61 — exhaustive test of bearish-signal × v19 combination

Three formulations attempted:
1. **As exit trigger**: fires on 61% of trades, force-sells positions v19 was waiting
   on. -$117k org.
2. **As guarded exit (underwater-only)**: halved fire rate but still cannibalizes —
   per-card bearish signal fires on the same dumping-bottom cards v19 specifically
   BUYS. +$310k vs +$502k baseline = -$192k cost.
3. **As entry filter (buy-rejection)**: never fires. v19's existing gates (floor
   ≤13k, floor_stable ≤14.5k/24h, week_range ≤25%, week_max ≤18k) already exclude
   the depletion+stability population.

**Conclusion**: iter 53's bearish signal (the only orthogonal signal discovered)
cannot add value to v19 in any formulation. v19's floor-band gates are sufficient
and already prevent entering the signal's population; overlays just cut winners.

### Iter 62 — calendar strategies audit

Three untested strategies: all fail every bar.

| Strategy          | Org PnL | Win%  | |corr| | Verdict |
|-------------------|---------|-------|--------|---------|
| weekday_swing     | -$30k   | 48%   | 0.60   | DEAD — not even orthogonal |
| weekly_cycle      | -$225k  | 26%   | 0.85   | DEAD — heavy correlation |
| saturday_massacre | -$389k  | 21%   | 0.53   | DEAD — worst of the three |

Calendar-time signals show no edge in this 27-day window.

### Final convergence

Total iterations: **62** (48 original + 14 this session). Budget was 100.
Stopping early because:

- **All 6 directions from brief tested**: (A) sales-velocity/listings proxy, (B)
  mega-recycling, (C) cross-card cohort, (D) card tier, (E) early-exit, (G)
  multi-cohort pyramid. Each either capped or failed.
- **Every overlay combination with v19 tested**: peak-drawdown, bearish-exit,
  bearish-exit-guarded, bearish-entry-filter. All either fail to add value or
  actively cannibalize v19.
- **Calendar-based signals dead**: 3 untested strategies audited, all fail all bars.

Further iterations would either retread dead hypothesis classes or require
engine/data changes outside the `/loop` framework. **Recommendation from iter 58
still stands**: ship combo_v18, await more data, re-evaluate in 2-3 weeks.

### Files added in addendum

- `src/algo/strategies/floor_buy_v19_absorb_exit.py`, `floor_buy_v19_absorb_exit_v2.py`, `floor_buy_v19_entry_filter.py`
- `floor_buy_v19_absorb_exit{,_v2}_filtered_results.json`, `floor_buy_v19_entry_filter_filtered_results.json` (+ unfiltered)
- `weekday_swing_filtered_results.json`, `weekly_cycle_filtered_results.json`, `saturday_massacre_filtered_results.json`
- Commits `873bd640`, `94216979`, `24312137`, `7df2f7ca`

---

## Second addendum — iters 63–64 (user re-opened loop, second re-open)

User invoked /loop a third time. Used remaining budget on the two final untapped
attack vectors: (A) a genuinely-new data signal and (D) the execution model
itself. Both explored definitively. Neither unlocks $2M.

### Iter 63 — daily sales-velocity signal (direction A done properly)

`daily_listing_summaries.total_sold_count` is daily-granularity sales data never
surfaced to strategies. Tested it as a signal.

**Phase 1 statistical analysis**:
- At 1.5x threshold (brief's suggestion): median next-day return +0%, 46.8% hit +2% → **SIGNAL DEAD at this threshold**
- At 2.0x threshold (sensitivity check): median next-day return **+5.75%**,
  60.8% hit +2% → **REAL statistical edge**

**Phase 2 strategy `daily_sales_spike_v1`** (buy post-spike day, $10-30k band,
48h max hold, +6%/-4%): **-$204k org, 33% win, 0/6 bars**. |corr|=0.53 (not
orthogonal). Loader drag + 48h hold decay + promo-day clustering destroy the
statistical edge entirely.

### Iter 64 — engine exec-slip flag (direction D / brief's Pivot D)

The brief flags loader pessimism ("BUY@max/SELL@min is worst-case ~9.6% drag")
as potentially load-bearing. Added `--exec-slip PCT` CLI flag to `src/algo/engine.py`:
when set, uses BUY @ median×(1+slip) / SELL @ median×(1-slip) instead of max/min.
Verified slip=0 preserves baseline exactly (regression-free additive change).

Re-ran 4 strategies at realistic slip=0.03:

| Strategy            | Baseline Org | Slip 0.03 Org | Delta | Interpretation |
|---------------------|-------------:|--------------:|------:|----------------|
| floor_buy_v19       | +$502,850   | +$390,922    | -$112k | Slip=3% is MORE pessimistic than actual hourly tick range for tight-floor cards. Slip made it worse. |
| combo_v18           | +$150,574   | +$35,207     | -$115k | Collapses 77% — combo arms over-trade marginal edges |
| daily_sales_spike_v1 | -$204,430  | -$162,279    | +$42k  | Still deeply negative; slip was not the cap |
| post_dump_v15       | -$30,150    | -$73,300     | -$43k  | Already unprofitable; slip worsens |

**Definitive finding**: **signal quality, not execution friction, is the
$2M-blocking constraint**. Even daily_sales_spike_v1 — the one strategy with a
proven statistical edge — recovered only $42k when drag was relaxed, far short
of profitability. Pivot D is partially-validated (execution matters) but does
NOT unlock $2M.

Additionally: for floor-band cards with tight hourly ranges, the baseline
max/min loader is CLOSER to real execution than slip=0.03. v19's +$502k baseline
is likely close to its real-world number, not an under-estimate.

### Final final convergence

Total iterations: **64**. Budget was 100. Stopping definitively because:

1. **All 6 brief directions tested (A, B, C, D, E, F, G)**. Each either capped
   by signal frequency, failed outright, or produced inverted anti-signals.
2. **All v19-overlay formulations tested** (peak-drawdown, bearish-exit,
   bearish-exit-guarded, bearish-entry-filter). None add value.
3. **All calendar strategies audited** (weekday_swing, weekly_cycle,
   saturday_massacre). All dead, none orthogonal.
4. **Engine's pessimistic loader is NOT the cap**. Iter 64 proves realistic
   execution does not unlock $2M on any tested strategy.
5. **Signal library is saturated**. 150+ strategies tried (across 64
   iterations + 48 prior), no signal in current features produces >+$502k.

### Concrete paths forward for the user

The $2M / 22d target is **not achievable through more /loop iterations**. The
options are:

1. **Extend the data window.** Scrape more history (60+ days) so W15-analog
   weeks average. Most promising of the real-world paths because current 27-day
   window is dominated by single-event artifacts.
2. **Add a new data source.** Per-hour sales counts (not just daily) would
   enable real sph-surge signals. Requires changing the scanner to record
   `total_sold_count` hourly, not daily.
3. **Change the scoring framework**. The 6-bar framework demands W14+W15+W16
   each ≥+$20k, which punishes any strategy with structural dead weeks.
   Replace with rolling-4-week metric.
4. **Ship combo_v18** (+$150k/~15% per week, ~5/6 bars) and accept that ~15%/wk
   compounded is the achievable product, not 65%/wk.

**Recommendation**: Ship combo_v18. Resume iterations only after (1) or (2)
produces new feature data. Further /loop runs without those inputs will not
change the outcome.

### Files added in second addendum

- `scripts/phase1_sales_spike_signal.py` (the statistical validator)
- `src/algo/strategies/daily_sales_spike_v1.py`
- `src/algo/engine.py` (added `--exec-slip` flag, additive change, slip=0 preserves baseline)
- `daily_sales_spike_v1_{filtered,unfiltered}_results.json`
- `{floor_buy_v19,combo_v18,post_dump_v15,daily_sales_spike_v1}_slip3_results.json`
- Commits `f3ad2174` (iter 63), `c9a5bec8` (iter 64)

---
*Loop ended at iteration 64 of a 100-iteration budget. All available hypothesis
directions exhausted including engine-level execution modeling. $2M / 22d on
$1M is unreachable with current data and feature set; further iterations
without new data or feature inputs cannot change this.*
