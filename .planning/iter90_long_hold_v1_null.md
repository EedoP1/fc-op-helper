# Iter 90 — long_hold_v1 NULL

## Pre-analysis (held up)
LH cluster (96-168h, n=309 pessim opps): median ROI net 51.6% (highest of any
hold bucket), $10-13k=124, $13-20k=95, **Mon=240/309 (78%)**, **W16=179 (58%)**.
Stack overlap with LH ea pool: 5.2-8.0% per stack member, 5.5% v19. After
removing stack-covered ea_ids: **160 open LH opps in $10-20k**, 131/160 (82%)
in W16. v19_ext's biggest winners (94k/98k/99k) are all Thu/Fri buys, so
"Mon-only" is a structurally orthogonal cut.

## Strategy
- v19_ext clone with: dow gate (Mon/Tue), profit_target 0.30 (vs 0.50),
  max_hold 168 (vs 240), band $13-17k, week_dd_min 0.18 (require real dd from
  168h max), smaller qty 5/8/12, stop_cooldown 96h.

## Result
- Filtered: -$26.5k, 18 trades, 50% win.
- Unfiltered: -$50.6k, 20 trades, 55% win (worse → no liquidity edge).
- Organic: -$12.4k. ALL bars FAIL. Force-sell share 53% (max_hold expiries
  outpaced profit-target hits).

## Why it failed
1. **Pessimistic loader drag dominates**: BUY@max + SELL@min imposes ~9.6%
   break-even (per memory). Lowering profit_target to 0.30 net leaves <21%
   headroom; many holds expire near break-even or stop out.
2. **dd_min 0.18 vs vol_tight 0.10 are mutually exclusive**: qty_large/medium
   tiers never fired (need flat-floor for size, but flat-floor cards have no
   drawdown). All 18 trades capped at qty 5 → tiny notional.
3. **Loader fills at $19-21k** despite $17k floor_ceiling: a few cards
   spiked into the buy hour and got filled at the high (Carpenter -$47k,
   Alisson -$27.5k). Strategy gates on smoothed price, not the actual fill.
4. **Mon entry catches the dump, not the bottom**: catalog Mon opps fire on
   the Mon LOW, but pessimistic loader buys Mon HIGH. The catalog's 51.6%
   ROI is BUY@min — a regime the loader cannot enter.

## Conclusion
The 96-168h cluster is real but is **not directly tradable under pessimistic
fills**. v19's edge in this regime depends on hour-of-day micro-stability
(stable floor that doesn't spike intra-hour); extending to $13-17k breaks
that property because $13-17k cards are more volatile intra-hour.

Next direction: instead of widening band, try a **promo-aware Mon entry on
$10-13k cards filtered by listings_count growth** — capture the Mon
mass-buy regime via supply-side signal rather than price-band shift.
