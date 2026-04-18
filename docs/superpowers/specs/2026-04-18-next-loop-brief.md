# Next Research Loop — Find a Real Edge on Tradeable Cards

## TL;DR

The prior "champions" (`hourly_dip_revert_v12`, `v19`, `v20`) reported
huge PnL (+$1.25M to +$9.93M) on the pessimistic loader but those
numbers were **fictional**. They came from illiquid cards where the
scanner saw occasional anomalous hours (all samples in an unexplained
high price band, e.g. Nahuel Losada bouncing between $19k and $49k
hour-to-hour). The "pessimistic" loader's `MIN(current_lowest_bin)`
fill picked those hours as sell prices, letting the backtest book
imaginary 150%+ gross margin trades.

When we filter the universe to cards that can actually absorb
position sizes (`--min-sph 5`, ≥5 sales/hour from `daily_listing_summaries`),
every prior strategy collapses:

| strategy | unfiltered | filter ≥5 sph |
| -------- | ---------- | ------------- |
| v12      | +$1.25M / 61% win | **−$629k / 20% win** |
| v19      | +$7.12M / 64% win | **−$782k / 14% win** |
| v20      | +$9.93M / 69% win | **−$701k / 21% win** |

Dip-reversion as formulated doesn't work on liquid cards. Your job:
**find a real edge that survives the liquidity filter.**

## Read these first

1. `docs/superpowers/specs/2026-04-18-hourly-dip-revert-v19-research.md`
   — the fictional-champion research doc. Shows what was tried, the
   iteration log, and the outlier-trade breakdown that revealed the
   problem.
2. `docs/superpowers/specs/2026-04-18-hourly-dip-revert-v12-research.md`
   — the predecessor loop.
3. `src/algo/engine.py` — loader now supports `min_sales_per_hour`
   (CLI flag `--min-sph`). Pulls from `daily_listing_summaries.total_sold_count`
   and filters the universe before the strategies see it.
4. `src/algo/strategies/base.py` — already has `set_listing_counts()`
   hook (the infra exists). Add more hooks as you need them.
5. `src/algo/strategies/hourly_dip_revert_v12.py`, `v19.py`, `v20.py`
   — prior strategies (kept for reference; don't modify them).
6. `src/algo/strategies/promo_dip_buy.py` — an independent strategy
   targeting Friday promo releases. It survived the prior loop on
   merit (not artifact) and will be your anti-correlation reference.
7. `scripts/analyze_backtest.py` — per-ISO-week + correlation analyzer.

## The data available (see it, don't assume)

```sql
-- Per-card per-hour price snapshots (your main feed)
-- Note: current_lowest_bin is a SCANNER SNAPSHOT of the cheapest BIN
-- listing at that moment — NOT a transaction price.
SELECT ea_id, captured_at, current_lowest_bin, listing_count
FROM market_snapshots;   -- 6.7M rows, 2108 cards, 2026-03-26 to 2026-04-18

-- Real sales (OUR BOT's own executed listings)
-- Narrow: ~200 distinct cards we've traded, 8211 list→sold events.
-- Whatever is here is GROUND TRUTH. Use it to cross-check any
-- backtest claim that a card sold at price X.
SELECT ea_id, action_type, price, outcome, recorded_at
FROM trade_records;      -- 127k rows, outcomes in {listed, sold, bought}

-- Pre-aggregated per-card-per-day sale rates by margin bin
-- IMPORTANT: daily_listing_summaries has DUPLICATE rows per
-- (ea_id, date) — use DISTINCT or aggregate when querying.
-- "margin" here is buy_now_price vs market_price_at_obs, which
-- both come from the same scanner data, so this table inherits
-- the same artifact-vulnerability as market_snapshots. Useful for
-- RELATIVE liquidity comparisons across cards, less useful as
-- an independent validation oracle.
SELECT ea_id, date, margin_pct,
       op_listed_count, op_sold_count, op_expired_count,
       total_listed_count, total_sold_count, total_expired_count
FROM daily_listing_summaries;

-- Raw listing observations (unresolved — outcome IS NULL)
-- 75k rows. Has first_seen_at, last_seen_at, expected_expiry_at,
-- but outcome is NULL across the board. You could infer outcomes
-- from timing, but the distinction (sold vs expired) isn't reliable
-- given the scanner-based source.
SELECT * FROM listing_observations;

-- FUTBIN price history scraped from external source
-- Different source! Independent of our scanner. Could be useful
-- as a corroboration signal for the scanner's prices.
SELECT ea_id, futbin_id, timestamp, price FROM price_history;  -- 727k rows
```

## The liquidity universe (`--min-sph 5`)

Filter pulls from `daily_listing_summaries.total_sold_count / 24` per
(ea_id, date), averaged per card. Current distribution:

| sales/hour | # cards |
| ---------- | ------- |
| < 0.5      | 413     |
| 0.5–1      | 312     |
| 1–2        | 269     |
| 2–5        | 372     |
| **≥ 5**    | **234** |
| ≥ 10       | 73      |
| ≥ 20       | 16      |

The ≥5 sph universe is **~15% of the raw card set** (234/1608). Start
there. If nothing works, try ≥2 sph (615 cards). Do NOT go back to the
unfiltered universe to declare victory — those numbers aren't real.

## Success criteria (revised for realistic data)

The old `≥$1.62M PnL` bar was pegged to a fake baseline. Replace with:

1. **Positive total PnL** (≥ +$100k, i.e. +10% over the window — a
   real edge, not cash-equivalent). **Stretch: +$500k (+50%) over
   the ~18 trading days post-burn-in.**
2. **Both full ISO weeks (W14 = Mar 30 – Apr 5, W15 = Apr 6 – Apr 12)
   profitable.** 25%/week is ambitious for a liquid universe; +5%/week
   is the starting bar, +15%/week the stretch. Name your bar explicitly
   in the research doc.
3. **Win rate ≥ 55%** (with the liquidity filter, below 50% means the
   signal has no edge — chance or worse).
4. **Correlation with `promo_dip_buy` ≤ 0.30** (same rule as before).

Show all four numbers in every iteration's report. Walk away from any
strategy that can't hit #1 + #3 after a reasonable sweep.

## Run command (both required)

```bash
# Primary run — liquidity filter ON
python -m src.algo run --strategy <name> --budget 1000000 --days 0 --min-sph 5

# Secondary check (DO NOT use this for the success bar — it has the
# fake-PnL problem — but keep it as a sanity check that the strategy
# doesn't depend entirely on artifact trades)
python -m src.algo run --strategy <name> --budget 1000000 --days 0

# Analysis
python scripts/analyze_backtest.py <name>
```

Also **promo_dip_buy needs a rebaseline** on the filtered universe —
refresh `promo_dip_buy_results.json` so correlation reference is
apples-to-apples:

```bash
python -m src.algo run --strategy promo_dip_buy --budget 1000000 --days 0 --min-sph 5
cp backtest_results.json promo_dip_buy_filtered_results.json
```

(And update `scripts/analyze_backtest.py` or your new strategy's
correlation code to read from `promo_dip_buy_filtered_results.json`
when analyzing filtered runs.)

## What's been tried and failed — don't redo

These died in the prior loop OR in this loop. The failure mode is
the useful signal; don't repeat the attempt expecting a different
outcome.

- **Deep-dip reversion with +25% target** (v12 family and derivatives).
  Doesn't work on liquid cards; 5–10% smoothed dips rarely reverse 25%
  on cards people actually trade.
- **Trailing stops** (v7, v16 variants). Under any hourly-min fill
  model, trailing stops exit winners at break-even. Fundamental
  incompatibility.
- **Wait-for-bounce** (v9). Entry while already rising pays a higher
  max-fill — worse than entering mid-dip.
- **Low-volatility entry filter** (v10). Removes exactly the cards
  with enough price range to clear spread+tax.
- **Support-level (7d floor) entry** (support_bounce_v1). Longer
  windows dilute the signal; cards near their 7d floor are often
  in downtrends, not at proven support.
- **Listing-count trend filter** (v17). Neither "listings rising"
  nor "listings falling" carried predictive signal in this dataset.
  The infrastructure exists; the signal doesn't.
- **Multi-target partial sells** (v18). Locks in small partial
  profits but caps winners; net negative under pessimistic execution
  and likely still neutral under cleaner execution.
- **Market-breadth regime filter** (v15). Blocks legit dips along
  with sympathy dumps.
- **Range-stability filter** (v6). Kills the big reversions.
- **Z-score entry** (v8). Liquid cards rarely trigger ≥2σ.
- **Hard-code "skip Saturday" / "skip Friday"** — forbidden
  (overfitting to a 22-day window).
- **Bigger positions on illiquid cards** (v19, v20's real source of
  fake PnL). Position sizes >1–2 on cards with <1 sale/hour are
  unsellable. The liquidity filter now blocks this structurally.

## Directions worth exploring (pick one that matches the data)

Liquid cards have tighter spreads and more efficient pricing. The
reversion signal that dominated v12/v19/v20 is weaker or absent here,
so you probably need a structurally different hypothesis:

1. **Momentum on liquid cards.** Cards with genuinely rising median
   over the last 12–24h may continue rising (broader-market demand
   flowing in). Buy on momentum, exit on smoothed peak. Inverse of
   v12 (which bet on reversion). Test with a trend filter like
   "median last 6h ≥ median prior 6h × 1.03."

2. **Session-based intraday.** FUT's trading day peaks when major
   regions come online. Look at hour-of-day statistics for the ≥5 sph
   universe — liquid cards may have predictable session spreads. Buy
   at the lull, sell at the peak. Exit within 12h.

3. **Volume-spike entry.** When `listing_count` SURGES (supply
   suddenly increasing) while price holds, sellers are trying to
   exit — buyers often haven't noticed yet. A few hours later,
   prices typically drop. Short the surge (sell-first) — but this
   requires inverting the strategy model (entry on expected drop,
   exit on realized drop). Consider whether the framework supports
   this.

4. **Pair trading on same-position cards.** Two similar-rated GKs.
   Both should move roughly together. When one diverges ≥X% from
   the other within 6h, bet on convergence. Requires computing
   cross-card correlations offline, which you can do from
   `market_snapshots` directly.

5. **FUTBIN anchor.** `price_history` is an independent source from
   FUTBIN (not our scanner). For each card, check the divergence
   between `market_snapshots.current_lowest_bin` and the FUTBIN
   price. Large divergences → one source is stale. Trade toward
   whichever is likelier to be right.

6. **Liquidity as the signal, not just a filter.** Rank cards in
   the ≥5 sph universe by sales/hour, bet only on the top quartile
   (≥10 sph = 73 cards). Those are genuinely transactable. Focus
   the strategy research there rather than trying to salvage the
   2–5 sph middle zone.

7. **Scale up promo_dip_buy.** It already works on Friday promos.
   On the filtered universe its absolute PnL drops but the edge
   (promo dynamics are distinct from regular trading) survives the
   filter better than dip-reversion does. Test it on `--min-sph 5`
   first. If the mechanism holds, deploy MORE capital into it
   instead of mean reversion.

8. **Profit target calibrated per card.** Different cards have
   different typical intraday ranges. Instead of fixed `profit_target
   = 0.25`, use `profit_target = 0.5 × card.typical_7d_range`. Then
   cards with tighter ranges trigger at smaller moves, and cards with
   wider ranges hold for bigger ones. Robust to liquidity mix.

9. **Very short holds with 1–3% target.** Tight execution on liquid
   cards. Buy at median, sell if smoothed moves +3% within 6h, else
   exit at break-even. High turnover, small margins, scaled by
   qty_cap to the card's sales-per-hour cap. Market-making lite.

## Loop rules

- 30 iteration budget. Stop after 3 consecutive iterations with the
  same failure mode and pivot structurally.
- Each new strategy = new file `src/algo/strategies/<name>.py`.
  Don't modify v12/v19/v20/promo_dip_buy (keep as reference).
- After each backtest, analyze: per-week PnL, weekday distribution,
  correlation with `promo_dip_buy_filtered_results.json`, top/bottom
  trades. Flag any individual trade with gross margin >30% — on the
  liquidity-filtered universe these should be rarer than before.
- Commit each iteration with `wip(algo): <name> — <result>` so the
  trail is reproducible.

## Final deliverable

When you hit the bar (or exhaust the budget), write
`docs/superpowers/specs/<date>-<strategy-name>-research.md` with:

- Strategy name, one-paragraph summary, signal pseudocode.
- Hyperparameters, locked.
- Per-week PnL table on the FILTERED universe (primary bar).
- Side-by-side unfiltered PnL (sanity check, not the bar).
- Weekday + hour-of-day trade distribution.
- Correlation with promo_dip_buy (filtered reference).
- Outlier-trade share (# of trades with gross margin >30% and their
  PnL contribution). Flag if it's >20%.
- What you tried and why earlier iterations failed.
- Honest known risks for live deployment.

Commit: `feat(algo): <strategy-name> — <filtered-PnL>, <multiplier>x v12 filtered baseline`.

The filtered v12 baseline is **−$629k**, so any profitable strategy
is infinitely better; quote absolute PnL rather than a multiplier
when the baseline is negative.

Start now.
