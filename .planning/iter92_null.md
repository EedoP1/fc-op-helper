# Iter 92 — NULL (mid_dip_v4 hypothesis falsified)

## Hypothesis tested
Tighten dd_72h gate from 0.25 to 0.30+ to exclude shallow noise dips, keep
high-conviction deep reversions, expecting fewer trades at higher win rate
and net positive Δstack vs v2 (+$143.6k baseline).

## Pre-analysis result (v2 trades enriched with estimated dd_72h_at_entry)

Bucket analysis (n=35 trades, $143.6k pnl):

| dd_72h bucket | n  | pnl       | win%  | avg/trade |
|---------------|----|-----------|-------|-----------|
| 0.20-0.25     | 6  | +$62,837  | 83.3% | +$10,473  |
| 0.25-0.30     | 11 | -$9,663   | 63.6% | -$878     |
| 0.30-0.35     | 5  | -$18,013  | 60.0% | -$3,603   |
| 0.35-0.40     | 5  | +$42,787  | 100%  | +$8,557   |
| 0.40+         | 8  | +$65,649  | 75.0% | +$8,206   |

Cumulative `dd >= threshold` (what v4 dd-tighten would keep):

| thr  | n  | pnl       | win%  | dPnL vs v2 |
|------|----|-----------|-------|------------|
| 0.25 | 29 | +$80,760  | 72.4% | -$62,837   |
| 0.28 | 23 | +$85,760  | 73.9% | -$57,837   |
| 0.30 | 18 | +$90,423  | 77.8% | -$53,174   |
| 0.33 | 15 | +$65,636  | 73.3% | -$77,961   |
| 0.35 | 13 | +$108,436 | 84.6% | -$35,161   |
| 0.40 | 8  | +$65,649  | 75.0% | -$77,948   |

**Every dd-tightening loses.** The v4 hypothesis is falsified — best
single-bucket performers are SHALLOW (0.20-0.25) AND DEEP (0.35+);
tighter gate cuts the shallow winners.

## Bimodal alternative explored (skip middle 0.25-0.35)

| rule                    | n  | pnl       | win%  | dPnL    |
|-------------------------|----|-----------|-------|---------|
| skip 0.25-0.30          | 24 | +$153,260 | 79.2% | +$9,663 |
| skip 0.25-0.35          | 19 | +$171,273 | 84.2% | +$27,676|
| skip 0.30-0.35 only     | 30 | +$161,610 | 76.7% | +$18,013|

Per-week:
- W15: shallow n=0, middle -$19.7k, deep -$3.6k  (skip middle helps -19k)
- W16: shallow +$29.7k, middle -$50k, deep +$20.9k  (skip middle helps +50k)
- W17: shallow +$33.1k, middle +$42.0k, deep +$91.1k (skip middle COSTS -42k)

**The "skip middle 0.25-0.35" gain is W16-driven and would have COST
-$42k in W17.** No causal reason for the bimodal pattern to recur — it
fits 2/3 weeks but cuts a +$42k W17 chunk with no theory. Classic
2-week-overfit, fails anti-cherry-pick check.

## Microstructure alternative (lc_change_24h >= 0)

Per `opportunity_signatures_report.md`, lc_change_24h has AUC 0.489
(NO signal). Validated on actual v2 trades:

| lc_chg threshold | n  | pnl      | win%  | dPnL     |
|------------------|----|----------|-------|----------|
| >= -0.10         | 26 | +$77,935 | 73.1% | -$65,662 |
| >= -0.05         | 23 | +$96,335 | 73.9% | -$47,262 |
| >=  0.00         | 21 | +$77,298 | 71.4% | -$66,299 |
| >= +0.05         | 17 | +$51,898 | 70.6% | -$91,699 |
| >= +0.10         | 12 | +$44,424 | 75.0% | -$99,173 |

**Every lc_chg threshold loses.** Pre-report AUC was correct; lc_change
is noise on this sample.

## Verdict
NULL. No tested rule survives anti-overfit. The mid_dip_v2 trade
distribution does NOT have a clean dd_72h structure — winners come from
both shallow and deep dips, and the only positive-Δ bimodal rule is
W16-driven (would have hurt W17 in real time).

## Next direction
Mid_dip line is at v2 ceiling for dd-shape filters. Worth exploring:
- Per-card profitability (which ea_ids drive the wins/losses?)
- Time-of-week / hour-of-day instead of dd-shape
- Profit-target tuning (winners running past 20% might leave money on table)
- OR: pivot to a different price band entirely (low_dip variants)
