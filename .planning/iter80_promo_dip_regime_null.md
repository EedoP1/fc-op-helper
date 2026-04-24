# iter80 — promo_dip_catch v3 regime gate — NULL

## Hypothesis
Bad weeks (W15-W16) are regime-driven; pause entries during "falling whitelist EMA" / "negative breadth" regimes; expect to clear ~$200k of W15 drains while keeping W16 winners.

## Pre-analysis findings

### v1 trade PnL by entry-week (filtered, --min-sph 2)
| Entry ISO Week | n  | PnL      | wins |
|----------------|----|----------|------|
| W14            | 8  | -$46,901 | 3    |
| W15            | 9  | -$215,750| 3    |
| W16            | 13 | +$180,274| 11   |
| W17            | 6  | +$65,725 | 5    |

By **realized sell-week** the swing is even sharper: W15 sells -$34k, W16 sells -$85k, W17 sells +$91k. This makes "W15-W16 bad / W17 good" a fair characterization on cash-realization basis — but on *entry-day* basis the inflection is mid-W16, not at a week boundary.

### Daily whitelist median price (regime metric candidate)
Whitelist (rating 88-91, promo card_types) daily median LBIN (308 cards):

```
2026-03-31  med=50500   (peak)
2026-04-04  med=43250   -14% from peak  -> v1 entry day, lost
2026-04-09  med=41750   stable          -> v1 entry day, lost worst (-$87k)
2026-04-12  med=36850   sliding lower   -> v1 entry day, lost
2026-04-14  med=36000   trough          -> v1 entry day, WON +$37k
2026-04-15  med=39750   bouncing        -> v1 entry day, WON +$41k
2026-04-17  med=38750   slight pullback -> v1 entry day, lost (TOTS RELEASE day)
2026-04-18  med=36250   post-TOTS       -> v1 entry day, WON +$46k
2026-04-19  med=36000   stable          -> v1 entry day, WON +$61k
```

### Tested regime rules

1. **`med >= ma7 AND slope3 >= 0`** — would suppress 11/16 entry days INCLUDING the W17 winners (Apr 18, 19, 20, 21 all flagged "suppress"). Net: would kill +$158k of profits to avoid -$153k of losses. **Worse than no gate.**

2. **`drawdown_from_7d_peak < 5%`** — Apr 18 has dd=8.8% AND wins +$46k; Apr 4 has dd=16% AND loses. No clean separation. Apr 14 (dd=14% trough) is the BIGGEST winner.

3. **Days since last major promo release** — would correctly mark Apr 17 as TOTS day and "trade only after Apr 18 + N days." But this is a **single calendar-event observation** (TOTS release), not a generalizable price-driven signal. Threshold would be tuned to ONE event.

### Hold-out validation (strict)
Tuning on W14-W16 only, the cleanest separator is "after Apr 14 trough." But Apr 12 (W15) was a $43k loss and Apr 14 (W16) was a $37k win — same regime metric, opposite outcome. Any threshold that lets in Apr 14 also lets in Apr 12. Any threshold that blocks Apr 12 also blocks Apr 14.

Out-of-sample on W17: rules tuned on the trough/recovery pattern correctly predict W17 wins, but ONLY because the inflection was a one-time TOTS event. With 5 weeks of data and one inflection point, the "regime gate" collapses to "trade only post-TOTS-2026," which is data snooping on a single event.

## Verdict: NULL

The bad weeks are not regime-driven in any market-signal sense; they are driven by a calendar event (TOTS release) that crashed promo card prices. A regime gate cannot detect this without explicit calendar knowledge, and tuning to the one TOTS release in our corpus is overfit by definition.

**No v3 implementation written.** Position-level controls (already attempted in v2 with stop loss) and regime-level controls (this iter) both fail. The strategy needs a fundamentally different signal — likely calendar-aware (gate around major EA promo releases) — and that requires a multi-year corpus to validate, which we do not have.

## Predicted v3 PnL
Any of the tested regime rules: **net negative** vs v1's -$17k filtered, because they kill more winners than losers.

## Next direction
Move on. Don't waste another iter on promo_dip_catch line. Try a different orthogonal signal next iteration (e.g., the floor_buy v24 line or a fresh idea outside the promo cohort).
