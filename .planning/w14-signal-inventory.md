# W14 Signal Inventory (Iter 37 Diagnostic)

**Question.** After 36 iterations, every attempt to unlock tradable W14 profit has
failed. Before designing iter 38, prove from the DB whether W14 contains a
tradable signal at all, and if so where.

**Windows (user-defined, UTC).**

| Label | Start               | End                  |
|-------|---------------------|----------------------|
| W13   | 2026-03-30 00:00    | 2026-04-05 23:59     |
| W14   | 2026-04-06 00:00    | 2026-04-12 23:59     |
| W15   | 2026-04-13 00:00    | 2026-04-19 23:59     |
| W16   | 2026-04-20 00:00    | 2026-04-23 06:19 (partial, data cutoff) |

DB: `postgresql://op_seller@localhost/op_seller`, table `market_snapshots`,
8.07M rows total. `captured_at` indexed.

---

## Step 1 — Card inventory per window

| Metric                                        | W13    | W14    | W15    | W16   |
|-----------------------------------------------|--------|--------|--------|-------|
| snapshot rows                                 | 2.37M  | 2.18M  | 1.94M  | 0.93M |
| distinct ea_ids                               | 2,094  | 1,888  | 2,229  | 2,236 |
| market-wide median current_lowest_bin         | 28,000 | 28,000 | 28,000 | 30,000|
| per-card median p10                           | 13,250 | 13,500 | 14,500 | 14,275|
| per-card median p25                           | 17,513 | 17,000 | 18,750 | 20,000|
| per-card median p50                           | 30,950 | 28,500 | 32,850 | 29,500|
| per-card median p75                           | 87,913 | 77,925 |120,900 |113,000|
| per-card median p90                           |228,550 |209,860 |429,200 |419,250|
| cards with >=1 hour at BIN <= 13,000 (floor access) | 591 | **637** | 646 | 313 (partial) |
| cards with floor access AND per-card median <= 15,000 (stable-floor proxy) | 397 | **358** | 243 | 256 |
| cards with >=3% median drop 1st-half vs 2nd-half (48h proxy) | 619 | **622** | 747 | 916 |

**Observation.** W14 is NOT structurally card-starved. It has 637 floor-access
cards and 358 stable-floor cards — higher than W15 or W16 on the stable-floor
metric. The `price_at_time` gate of v19/v21 is satisfiable in W14.

---

## Step 2 — v21 firing pattern (`floor_buy_v21_filtered_results.json`)

```
pre-W13 (<2026-03-30): 1 buy  (2026-03-29 23:00)
W13:  7 buys  first=2026-03-30 02:00  — clusters at W13 start
W14:  8 buys  first=2026-04-09 08:00  — clusters mid-W14, after 3.33-day burn-in
W15:  3 buys  first=2026-04-19 09:00  — edge of W15
W16:  0 buys
W17:  0 buys
```

Total: 19 trades, earliest 2026-03-29 23:00.

**First v21 buy inside W14 arrived 3.33 days after W14 start** (2026-04-09 08:00).
This is driven by v21's `burn_in_h=72` plus `recent_h_min=24` stability gate —
the strategy deliberately sits out the early portion of a week while it
re-qualifies the floor band.

Per-week PnL (bucketed by buy_time):

| Bucket | Buys | Profit     |
|--------|------|-----------:|
| W13    |    7 |    +33,475 |
| W14    |    8 |   +473,500 |
| W15    |    3 |    -22,500 |
| W16    |    0 |          0 |

v21 DOES profit on W14 buys — heavily. The earlier context note "W14 $0" for
combo_v10 refers to combo_v10's arm-gating, not to v21 or to the raw opportunity
set.

---

## Step 3 — Global market health per week

Market-wide median BIN (percentile_cont 0.5 across every snapshot in window):

| Week | Median | WoW delta |
|------|-------:|----------:|
| W13  | 28,000 |    —      |
| W14  | 28,000 | **+0.00%** |
| W15  | 28,000 | +0.00%    |
| W16  | 30,000 | +7.14%    |

W14 is **sideways at the aggregate level** — not a downtrend and not an uptrend.
This is consistent with organic chop: no broad promo tailwind, no broad crash.
Under-the-hood activity is much higher than the aggregate median suggests (see
dump/bounce table below).

---

## Step 4 — Where the tradable signal actually lives in W14

### A. Dump-and-continue regime (W13 -> W14)

Cards starting at W13 median 10k-30k, dropped >=20% into W14. Do they bounce
the next week?

| Dump window -> Recovery window | n | avg fwd | median fwd | +5% | +10% | +20% | +30% |
|--------------------------------|---|--------:|-----------:|----:|-----:|-----:|-----:|
| **W14 dumpers -> W15**          | 38 | -4.6% | **-7.2%**  |  7  |   4  |   1  |   1  |
| W15 dumpers -> W16              | 51 | +5.8% | **+2.9%**  | 20  |  13  |   7  |   5  |

**W14 is the only recovery window where dumpers keep dumping.** Median recovery
is *negative*. Post-dump buy-the-dip strategies have no positive edge here — the
dump continues. This is the structural reason every post_dump variant has
failed in W14.

### B. Floor-band forward motion

Cards whose late-W13 / late-W14 state is "floor-band" (min BIN <= 13,000 AND
per-card median <= 14,500):

| Setup -> Forward | n   | median forward | +10% | +30% |
|------------------|-----|---------------:|-----:|-----:|
| Late-W13 -> W14  | 315 | **+0.0%**      |   25 |    5 |
| Late-W14 -> W15  | 348 | **+11.3%**     |  177 |    6 |
| Late-W14 -> W16  | 348 | **+30.0%**     |  n/a |  n/a |

**The tradable signal in W14 exists at the END of W14, not the start.**
- Buying a floor-band card at the start of W14 based on late-W13 qualification
  yields 0% forward median — flat chop.
- Buying a floor-band card at the END of W14 yields +11.3% median into W15 and
  +30% median into W16 — this is exactly what v21 exploits.

### C. Regime-change inflection

The transition from 0% forward (late-W13 -> W14) to +11.3% forward
(late-W14 -> W15) happens somewhere inside W14. v21's 8 W14 buys land at
2026-04-09 (Thursday), which is approximately the hinge point.

---

## Step 5 — Candidate signals that MIGHT fire profitably in W14

Given the inventory shows W14 is a *continuation of a downtrend that has not
yet bottomed*, the only signals that can plausibly fire in W14 are ones that
explicitly wait for the regime change:

1. **Floor-band re-entry gate (strict stability).** A card must hold min BIN
   below the floor ceiling for N consecutive hours with low volatility, AND the
   card's previous-week median must have already dropped >=10% (proof of
   capitulation). This rejects early-W14 signals (still dumping) and accepts
   mid/late-W14 (dump completed). v21 already approximates this via
   `recent_h_min=24` + `week_range_max=0.25` + 72h burn-in — that's why v21 has
   8 profitable W14 buys.

2. **Sideways-chop mean reversion.** During a flat-market week (WoW=0% at the
   aggregate), buy floor-band cards that dipped 5-10% below their 7-day median
   and exit when they revert to the median. Not a 25%/week strategy, but
   plausible source of small, consistent W14 P&L.

3. **Market-median breakout confirmation.** Only arm buying once the
   market-wide 24h-rolling median has risen 1% off its weekly low. This defers
   entry into W14 until the week turns, avoiding early-W14 dump continuation.
   Acts as a macro filter on top of per-card signals.

### Signals that are NOT plausible

- Any strategy buying floor-band cards at W14 START based on late-W13 or
  earlier qualification. Forward median is 0% and only 25/315 cards gain +10%.
- Buy-the-dip in W14 based on a fresh dump trigger (like post_dump_v15 with
  48h-drop gate). Forward median is NEGATIVE (-7.2%).
- Any strategy that cannot incorporate a "has the dump bottomed?" test —
  without that test you cannot distinguish "buy here" from "catch a falling
  knife" in W14.

---

## Step 6 — Honest conclusion

**W14 has few tradable-signal opportunities relative to W15/W16, because:**

1. The dump regime continued through W14 (W14 dumpers lose another 7.2%
   median into W15). Post-dump buyers lose.
2. Early-W14 floor-band signals (inherited from W13) have 0% forward median
   through W14. Floor-band early buyers tread water.
3. The only tradable edge inside W14 appears after the hinge point
   (~2026-04-09) — late-W14 floor-band cards gain +11.3% median into W15
   and +30% into W16. v21 captures exactly this with 8 profitable buys
   totalling +$473.5k.

**Is 25%/week structurally achievable?**

- **Measured against early-W14 or "dump-signal W14" strategies: NO.** No amount
  of parameter tuning on post_dump or early-floor-buy variants will unlock W14
  organic profit — the underlying price action is an unbounced continuation.
- **Measured against late-W14 floor re-entry (v21-style): already achieved.**
  v21 delivers +$473.5k on 8 W14 buys — that is far above the 25%/week bar on
  capital deployed.
- **For combo_v10's "W14 $0" specifically: the combo's W14 arm is starved by
  its own gating,** not by absent opportunities. v21 proves the signal exists
  at the hinge. The path forward is NOT "find new W14 signals" — it is
  "loosen combo_v10's W14 arm to match v21's burn-in plus floor-stability
  gates."

**Proposed path forward.**

1. Do not invest further iterations searching for a "new" W14 signal class.
   The data confirms W14's signal IS late-floor-buy and v21 already exploits
   it.
2. Next iter should adapt combo_v10's W14 arm to the v21 gate (burn_in=72h,
   recent_h_min=24, week_range_max<=0.25) rather than chase post_dump or
   early-floor variants.
3. Leave the 4/6-bars combo as the current best and explicitly flag W14 as
   "solved by v21-style arm, pending re-merge" rather than "unreachable."

---

## Query provenance

All numbers above come from direct queries against `market_snapshots`. Cached
results saved to `/tmp/w14_fast.json`, `/tmp/w14_perc.json`,
`/tmp/w14_combined.json` during the investigation; not checked in.

v21 trade analysis comes from `floor_buy_v21_filtered_results.json` (git
tracked).
