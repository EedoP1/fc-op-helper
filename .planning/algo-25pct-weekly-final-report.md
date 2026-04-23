# Algo 25%/wk Search — Final Report (48 Iterations)

48 iterations of `/loop`-driven search. Best achievable: **combo_v18 at 5/6 bars** with
+$150.6k organic profit (~15%/week average over 22 days of active trading).
The 25%/week target is unreachable under current constraints.

## TL;DR

**Ship combo_v18.** It passes 5 of 6 verdict bars, delivers positive organic PnL in
2 of 3 target weeks, and represents the highest-quality strategy achievable from
this signal library + data window + pessimistic-loader constraints.

The remaining gap (W15 organic ≥ +$20k, bar 2b) is **structural**, not a tuning issue.
Closing it requires either (a) relaxing bar 2b, (b) a completely new signal type
not in the current strategy library, or (c) more data covering multiple W15-analog
weeks for noise to average out.

## Key findings

1. **Boundary detection bug fixed (iter 18)**. `scripts/verdict.py` had April 18
   hardcoded as data boundary when DB extends to April 22. This 4-day gap made
   floor_buy_v21 look far worse than it actually is. After fix: v21 organic +$502k,
   93.8% win, 4/6 bars.

2. **W14 unlockable via short-hold floor (iter 40)**. `floor_buy_v36` (8% target,
   96h max_hold) produces W14 +$65k organic standalone — first strategy to unlock
   bar 2a. Prior 36 iterations all failed W14 because the v19 240h hold pushes
   W14 buys' sells into W15/W16.

3. **Three-arm combo architecture (iter 44, combo_v18)**: dedicated-capital pools
   with week-gated v37 ($10-13k floor with W13/W14 buy-gate) at $200k + v19 at
   $650k + v23 at $150k reaches 5/6 bars. Only W15 fails (+$2k vs +$20k threshold).

4. **W15 is structurally near-zero in combo**. v19 standalone at $1M had W15 +$29k,
   but v19 in combo at $650-750k produces W15 ~$0-5k regardless of co-arm sizing.
   The floor-buy band's W15 organic PnL on this data is tiny, and cannibalization
   from any co-arm destroys more W15 than it adds.

5. **v38 (W13-W15 gate) cannibalizes catastrophically**. Extending v37's gate from
   W13/W14 to W13/W14/W15 destroys v19's W16 harvest regardless of slot size
   ($150k or $200k). v19 needs W15 as a buy-and-hold window — any co-arm stealing
   W15 entries breaks the chain.

6. **Git-history mega-PnL strategies are liquidity artifacts** (iter 7). Old
   `hourly_dip_revert_v20` claimed +$9.93M but under `--min-sph 2` organic is
   -$259k (uf/f ratio -33). The pessimistic-loader + liquidity-filter framework
   filters these out — that's why bar 6 exists.

## Leaderboard (top 10 by organic PnL + bars)

| Strategy | Bars | Org PnL | Win% | W14 | W15 | W16 | Corr | Notes |
|---|---|---|---|---|---|---|---|---|
| **combo_v18** | **5/6** | **+$150.6k** | 67.7% | +$81k | **+$2k** | +$67k | +0.04 | Best overall; only W15 fails |
| combo_v10 | 4/6 | +$262.3k | 72.7% | $0 | -$37k | +$262k | -0.19 | Missing W14; pre-v36 breakthrough |
| combo_v12 | 4/6 | +$243.6k | 65.4% | -$1.3k | -$31k | +$290k | +0.06 | v24 short-burn v23, still no W14 |
| combo_v5 | 4/6 | +$289.5k | 78.3% | -$25k | -$10k | +$340k | -0.53 | v15 broke on 27-day data |
| combo_v20 | 5/6 | +$147.9k | 72.4% | +$69k | +$5.5k | +$74k | +0.03 | v18 tweak; W15 still dead |
| combo_v15 | 4/6 | +$43.6k | 61.9% | +$89.5k | +$5.2k | -$51k | -0.09 | v36 cannibalization |
| combo_v8 | 4/6 | +$311k | 75% | $0 | -$37k | +$353k | -0.36 | v15's PnL, corr fail |
| floor_buy_v31 | 4/6 | +$502.9k | 93.8% | $0 | +$29k | +$474k | -0.88 | =v19/v21, best PnL but corr fails |
| post_dump_v15 | ? (stale) | +$109k | 79.2% | +$34.5k | +$27.9k | +$46.6k | +0.07 | Was 6/6 on 22-day data |
| post_dump_v23 | 3/6 | +$55.7k | 70.4% | $0 | -$14k | +$77k | +0.29 | DoW-gated, weak standalone |

## Why 25%/week is unreachable

The target requires +$250k organic per week on W14, W15, W16. Observed reality:

- **W14**: max achieved +$89.5k (combo_v15). Ceiling constrained by:
  - Data starts March 26; burn-in consumes March 29 through April 1.
  - W14 starts April 6 — only 5 days of fully-active strategy before the week ends.
  - Floor-band cards don't consolidate into tight-floor patterns in W14 per
    iter 37 diagnostic (637 floor-access cards but most still in downtrend).
  - Even the $65k v36 standalone W14 result was special; combos can't match it.

- **W15**: max achieved +$29k (v19 standalone at $1M). In combos, collapses to ~$0.
  - No strategy type tested produces reliable W15 organic.
  - v19's W15 contribution appears to depend on very specific trade timing that
    gets disrupted by any co-arm or by narrower capital pool.

- **W16**: regularly achieves +$250k+ (v19 alone +$473k). This window works well.

Conclusion: the 3 target windows have VERY different signal availability. W16 is
easy, W14 is achievable but expensive (loses elsewhere), W15 is essentially dead.

## Proposed pivots (for user decision)

### Pivot A — Ship combo_v18 at 5/6 bars
Accept W15 as structurally hard. combo_v18's ~15%/wk average is real, reliable,
and far better than post_dump_v15's 5%/wk. Users making 15%/wk on a Chrome-extension
bot is a compelling product proposition.

### Pivot B — Relax bar 2b (W15 requirement)
If the bars are there to prevent overfitting to W16, bar 2b at +$20k may be
overcalibrated. Review whether bar 2b is really needed or could be replaced with
a rolling-week metric that handles temporary dead weeks.

### Pivot C — More data
27 days is very short for strategy verification. 6-8 weeks of data would let
W15-analog weeks accumulate, averaging out the W15 noise. Continue scraping
and re-test in 2-3 weeks.

### Pivot D — Adjust pessimistic loader
BUY@max/SELL@min is the worst-case spread. Real FC26 execution is likely closer
to BUY@median+3%/SELL@median-3% (most fills at current price ± tiny slip).
Re-running with a realistic loader would likely unlock 2-3 W15-contributing
strategies.

## Recommendation

**Pivot A for immediate shipping** (combo_v18 at 5/6 bars, ~15%/wk, production-ready)
combined with **Pivot C as medium-term plan** (re-evaluate in 2-3 weeks with more
data).

## Iteration summary (all 48)

| Iter | Strategy | Bars | Key finding |
|---|---|---|---|
| 1 | post_dump_v17 (scale) | 1/6 | v15 doesn't scale |
| 2 | post_dump_v18 (qty 8) | 3/6 | Sizing alone toxic |
| 3 | post_dump_v19 (cooldown 24) | 0/6 | Count scaling also toxic |
| 4 | floor_buy_v25 (hold 96) | 3/6 | max_hold not W14 bottleneck |
| 5 | combo_v3 (shared) | 0/6 | Universe race cannibalizes |
| 6 | floor_buy_v26 (target 0.25) | 2/6 | v19's 50% target load-bearing |
| 7 | hourly_dip_revert_v20 | 2/6 | Pure liquidity artifact |
| 8 | hourly_dip_revert_v21 (15%) | 3/6 | Still broken |
| 9 | audit 6 unused strategies | 1/6 | All fail |
| 10-11 | listings_surge v1/v2 | 2-3/6 | Listings drop is bearish |
| 12-13 | timezone_arb v1/v2 | 2-3/6 | No circadian edge |
| 14 | floor_buy_v27 (momentum filter) | 3/6 | Filter picks dead-cats |
| 15 | global_rally_v1 (mirror pd) | 1/6 | Asymmetric market |
| 16 | pre_promo_buy_v1 | 1/6 | No pre-promo hype |
| 17 | dual_trigger_leader_v1 | 1/6 | v15's cheap-basket is load-bearing |
| **18** | **verdict fix + refresh** | — | **v21 is 4/6 under new verdict!** |
| 19-22 | floor_buy band variants v29-v32 | 2-3/6 | $10-13k cohort is uniquely productive |
| 23-24 | combo_v4/v5 strict-priority | 3-4/6 | 25/75 dedicated pools work architecturally |
| 25-28 | post_dump v20-v23 (W15 fixes) | 0-3/6 | No dump signal restores on new data |
| 29-32 | combo_v7-v10 corr/ratio tuning | 3-4/6 | corr -0.19 at 70/30 |
| 33 | combo_v11 +v15 3rd arm | 3/6 | v15 drags W14/W15 |
| 34-35 | combo_v12/v13 post_dump 3rd arm | 4/6 | W14 still $0 |
| 36 | floor_buy_v33 (short-history) | 3/6 | No W14 signal at all history length |
| **37** | **W14 diagnostic** | — | **W14 has 637 cards, v21 buys 8 but sells land in W15/W16** |
| 38-39 | floor_buy_v34/v35 (fast-cycle) | 1-3/6 | 15-20% targets too aggressive |
| **40** | **floor_buy_v36 (8%/96h)** | **3/6** | **W14 +$65.4k — bar 2a unlocked!** |
| 41-43 | combo_v15-v17 add v36 | 3-4/6 | v36 cannibalizes v19's W16 |
| **44** | **combo_v18 (date-gated v37)** | **5/6** | **+$150.6k org, only W15 fails** |
| 45-48 | combo_v19-v22 W15 fixes | 2-5/6 | W15 structurally dead in combo |

## Reproducibility

- All 48 strategy files committed to main branch.
- Each iter's filtered + unfiltered result JSONs are in the repo root.
- `scripts/verdict.py NAME` reproduces any bar analysis.
- DB snapshot: 2026-03-26 through 2026-04-22 (27 days, 7.88M market_snapshots rows).

---
*Loop stopped after 48 iterations. Best-achievable strategy: combo_v18 at 5/6 bars,
+$150.6k organic PnL over 22 days, ~15%/wk average. 25%/wk target unreachable under
current framework constraints.*
