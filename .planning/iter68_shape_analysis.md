# Iter 68 — Approach-shape gate (spike_crash) — NULL

## Hypothesis
Opportunity signatures report classifies last-12h price path into
choppy/spike_crash/monotone_decline/flat. spike_crash is 1.5x enriched
in opps (23% vs 15% random); monotone_decline 7x enriched (14% vs 2%).
The gate is shape-only — no prior iter used shape as a primary gate.

## Step 1 — Opp population shape distribution

Of 1,515 pessimistic opps classified (335 had <10h of prior history):

| Shape             | n     | median ROI |
|-------------------|-------|------------|
| choppy            | 1,120 | 29.9%      |
| spike_crash       |   299 | 30.9%      |
| monotone_decline  |    87 | 30.6%      |
| flat              |     9 | 32.4%      |

Opps are NOT concentrated in spike_crash (20% of opps). They are mostly
choppy (74%), with shape giving a small but visible enrichment.

## Step 2 — Hit rate on whitelisted universe

Whitelist: rating 86-91 AND card_type IN {fut birthday, fantasy ut,
fantasy ut hero, future stars, fof:atc, star performer, unbreakables icon,
unbreakables, knockout royalty icon, fc pro live, festival of football:
captains, time warp, winter wildcards, ultimate scream}, lph >= 2.
Liquidity gate: avg listings last 24h >= 15.

| Shape            | n       | hits   | hit_rate |
|------------------|---------|--------|----------|
| spike_crash      | 4,868   | 2,011  | **41.3%** |
| monotone_decline |   817   |   261  | 31.9%    |
| choppy           | 107,154 | 20,485 | 19.1%    |
| flat             | 3,726   |    49  | 1.3%     |

spike_crash passed the >=40% gate — proceeded to strategy build.

## Step 3 — Continuation after spike_crash

6h forward from spike_crash classification:
- 49.4% went UP (>3%)
- 21.5% flat
- 29.0% kept FALLING (<-3%)

70% stabilize or recover — looked promising.

## Strategy: spike_crash_v1

Gate: whitelist + $13k-$80k + 12h spike_crash shape + 72h drawdown >=25%
+ 3h stabilization (last 3h within 3% of recent mini-trend) + standard
outlier/age/burn-in/cooldown gates. Exit 25%/15%/96h, 8 slots × $125k.

## Result — KILLED

### Filtered (min-sph 2)
- Organic PnL: **-$437,782** (121 trades, 49% win)
- W14: -$234k, W15: -$45k, W16: -$209k, W17: +$50k
- Boundary share: 14% (clean)
- |corr| vs promo_dip_buy: -0.28 (orthogonal)

### Unfiltered
- Total PnL: **-$346,215** (unfiltered > filtered is a bad sign:
  liquidity gate does not help — the losers come from the liquid pool too)

### All bars: FAIL

## Diagnosis — why the shape hit rate doesn't convert

1. **41% hit rate is not an edge after loader drag**: pessimistic loader
   imposes ~9.6% break-even. For a 20%+ ROI opp, a 41% hit rate means
   59% of trades lose. When the losers lose more than 10%, the winners'
   20%+ can't cover. Average worst trades in backtest: -$40-140k on single
   positions.

2. **The 29% falling-knife tail is catastrophic**: Oskar Pietuszewski
   (-$143k on one position), Clint Dempsey (-$60k), Gonçalo Inácio (-$60k).
   These are the spike_crash cards that kept falling; the stabilization
   gate (3h, 3% tolerance) is too loose — a 3h pause mid-crash is common.

3. **W14 -$234k and W16 -$209k**: regime-sensitive. Loses hardest during
   peak promo-crash weeks — exactly when the shape appears most often.

4. **Choppy is 96% of shape population but only 51% of opps**:
   the shape gate filters volume down ~22x (107k → 4.8k candidates) but
   the hit rate lift (19% → 41%) isn't enough to overcome selection
   bias — the filtered population is exactly the kind of chaotic crash
   that breeds failures.

## Lessons

- Shape alone is too coarse. Spike_crash at $13-80k looks like an opp
  signature but the population includes far more continuation crashes
  than stabilizing bottoms.
- A "confirmation of stabilization" gate needs to be stronger — either
  wait longer (e.g., 6-12h of flatness) or combine with absolute price
  level tests (below 7d/14d floor).
- Shape may still be useful as a SECONDARY confirmation layered onto
  an existing strategy (e.g., v19 + shape-gate boost), but not as a
  primary standalone gate.

## Next direction

Abandon shape-only. Consider combining spike_crash shape with:
- multi-day floor test (price at 14d low)
- listing-count surge (sellers dumping = absorb)
- or use shape as an orthogonal EXIT signal on existing strategies
