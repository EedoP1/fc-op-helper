# iter 79 — promo_dip_catch_v2 (NULL)

## Premise
v1 booked -$16.7k filtered / 36 trades / 61% win. Eight large drains
(|net|>$24k each) summed to -$357k of the -$437k loser pile. Hypothesis:
adapting daily_trend_dip_v5's proven smoothed-stop pattern to v1 should
clip the falling-knife continuations and free capital for higher-quality
re-entries.

## Pre-analysis (per-trade simulation on v1's 36 trades)
Hourly medians from market_snapshots, smooth_window=3, exit at trigger-bar
median, EA tax 5%:

    threshold N    predicted_pnl  clips  winners_clipped
    -0.15    8     -8.1k          11     2  (too tight)
    -0.15    10    +62.0k         9      1
    -0.15    12    +62.7k         8      0  *** PICKED
    -0.15    14    +52.9k         8      0
    -0.18    10    +52.9k         8      0
    -0.20    10    +35.8k         8      0
    -0.25    14    +17.5k         5      0
    -0.30    14    -5.3k          2      0
    v1 orig  -     -16.7k         --     --

## Live engine results (filtered, --min-sph 2)
- v2 with -0.15 / N=12:  PnL -$31.0k  Win 58.1%  Trades 43
- v2 with -0.25 / N=14:  PnL -$32.3k  Win 60.5%  Trades 38

Both stop variants UNDERPERFORMED v1 in the engine despite the per-trade
pre-analysis predicting +$79k for the picked variant.

## Verdict bars (v2 with -0.25 / N=14, the conservative pick that survived)
    [FAIL] 1. Organic PnL >= +$100k: -27,228
    [FAIL] 2a. W14 organic >= +$20k: +10,900
    [FAIL] 2b. W15 organic >= +$20k: -116,376
    [PASS] 3. Win rate organic >= 55%: 62.2%
    [PASS] 4. |corr| <= 0.30: -0.127

Per-week organic PnL:
    2026-W14: +10,900   (matches v1 — pre-stop period)
    2026-W15: -116,376  (worse than v1 — stop fires, freed cash chases new drains)
    2026-W16:  -93,464
    2026-W17: +171,712  (v1's W17 winners survive — confirms "stop spares winners")

## Why pre-analysis was wrong
1. Per-trade sim assumed each trade was independent, but the engine
   redeploys freed cash from stops into new daily-fire candidates. Many
   of those re-entries in W15-W16 turned into NEW drains (43 total
   trades vs v1's 36).
2. Per-trade sim used hourly-median exit price; engine sells at the
   live tick lowest_bin which can be lower in volatile bars.
3. Stop fires earlier than the conceptual "max_hold drain bottom",
   locking in losses on positions that mean-reverted by max_hold in v1.

## What this proves
- The signal IS real (W17 +$171k organic — same as v1's strong week).
- Adding ANY stop that frees cash mid-week into a daily-fire engine
  redeploys it into the same toxic week's continuation moves.
- The drain weeks (W15-W16) need a different fix: regime gating
  (suppress entries when broad market is falling) or a fixed-cooldown
  pause after consecutive losers, NOT an exit stop on the existing
  positions.

## Next direction
- v3: regime gate — pause entries when 3-day EMA of WHITELIST median
  price is < lookback by some threshold. This addresses the actual bug
  (entering a falling market en masse) instead of treating symptoms.
- Alternative: tighten dd168 floor to >=0.55 AND reduce basket_size
  to 4 so freed cash from stops is harder to redeploy in same week.
