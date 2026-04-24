# Iter 93 — Dynamic per-card whitelist (had_opp_in_prior_7d) — NULL

## Hypothesis
Repeater cards (242 cards with opps in 3+ weeks) are predictable AT BUY TIME by their PAST appearances. A time-causal whitelist gating on `had_opp_in_prior_7d` (≥1 opp in [t-168h, t-24h)) should beat a static rating+card_type whitelist by selecting cards in their "active phase".

## Pre-analysis (all numbers from `.planning/profit_opportunities.json`, pessimistic)

Base universe: 1850 opps across 673 unique cards in 27d window.

### Daily-fire hit rate (gate evaluated at 00:00 each day)

| Prior window | Gated card-days | Opps caught | Hit rate |
|---|---|---|---|
| 48h | 1,645 | 168 | 10.21% |
| 72h | 3,118 | 352 | 11.29% |
| 168h (target) | 7,252 | 842 | 11.61% |
| 240h | 8,787 | 1005 | 11.44% |
| 336h | 9,662 | 1089 | 11.27% |

**Base rate** (any card with at least 1 opp in window, picked on a random day): 1850 / (673 * 27) = **10.20%**.

**Lift of 7d gate over base rate: 1.14x**. Gate provides almost no information.

### Repeater-only subset (292 cards with ≥3 opps)

Base rate among repeaters: 17.06%. With 168h prior gate: 16.36%. **Lift = 0.96x — gate is anti-predictive on repeaters.** This makes sense: repeaters' opps are scattered across the 27d window, not temporally clustered.

### Tue/Wed/Thu day filter

With 168h gate AND Tue/Wed/Thu only: hit rate drops to 9.11% (lift 0.93x vs base).

## Why the gate fails

Section B of the signatures report identified repeaters by **rating + card_type** (3x over-representation in 89-91 promo cards). That's a STRUCTURAL property — invariant across weeks. The "active phase" hypothesis (repeaters cluster their opps temporally) is FALSE: repeater opps appear roughly uniformly across the window, so knowing a card had an opp 7d ago tells you almost nothing extra.

The current stack already uses the static rating+card_type whitelist (mid_dip_v2, low_dip_v3, daily_trend_dip_v5, etc). Adding the dynamic prior-opp gate would just shrink the candidate set without improving precision — net effect would be similar PnL but fewer trades, and overlap with stack ≈ 80% (same whitelist subset of same days).

## Verdict

Hit rate 11.6% (< 15% weak threshold). Lift 1.14x. Predicted PnL would be ~$80-130k IF every gated opp were caught at full ROI, but realistic capture rate (after price/lc/dd gates) would be 15-25% of that = $12-32k, and overlap with existing stack would erase most of it. **Below honesty bar — null this iteration.**

## Next direction
The "structural repeater" angle is already exhausted by the static whitelist. Worth exploring instead:
- ROI-weighted card scoring: cards with HIGHER MEDIAN ROI on past opps (not just count) — may produce a tighter precision gate
- Cross-card co-movement: when one repeater dumps, do correlated repeaters dump too? (basket signal)
- Promo-cycle relative timing: the 14d post-release window vs steady-state (Section C noted "promo-dip catch" was the dominant typology)
