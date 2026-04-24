# iter87 — low_dip_v2 burn-in reduction was a no-op (NULL)

## Hypothesis (from iter 86)
low_dip_v1 had `burn_in_h=96` (4d) and zero W13/W14 trades. Reducing to
72h would unlock ~$30-80k of W13/W14 captures.

## Pre-analysis
- Catalog (`profit_opportunities.json` pessimistic, $13-20k band):
  W13 n=45, W14 n=93, W15 n=96, W16 n=191, W17 n=87.
- W13/W14 Tue/Wed/Thu in band: 18 opps, medROI 26.7%, max 88.6%.
- Earliest catalog buy_hour: **2026-03-26T23:00 UTC** (W13 Thu late evening).

## Engine data start (decisive constraint)
- `market_snapshots` MIN(captured_at) = **2026-03-26 23:51 UTC** (W13 Thu).
- This is AFTER the W13 catalog opps (which require pre-W13 history) and
  exactly at W13 end. Effectively no W13 data, partial W14 data.

## Math: burn-in is not binding
- v1 (burn_in=96): first eligible fire at hour 0 = Mar 31 00:00 (W14 Tue)
- v2 (burn_in=72): first eligible fire at hour 0 = Mar 30 00:00 (W14 Mon, SKIPPED by skip_monday) → Mar 31 00:00 (W14 Tue)
- **Same first eligible weekday for both.**

To unlock earlier W14 weekdays would require burn_in ≤ ~24h, but
`dd_window_h=72` requires 72h of per-ea_id history regardless — so the
gate physically cannot fire on Mar 31 with full drawdown signal anyway
(only ~96h of data exists). The catalog opps in W14 came from cards with
longer history that simply wasn't captured by our scraper before Mar 26.

## Result
- **v1 filtered**: $171,297 / 41 trades / 60.97% win, first buy 2026-04-07
- **v2 filtered**: $171,297 / 41 trades / 60.97% win, first buy 2026-04-07
- **v1 unfiltered**: $377,734 / 42 trades / 69.0% win, 1 W14 trade (+$10.8k Apr 1 Schick)
- **v2 unfiltered**: $377,734 / 42 trades / 69.0% win, 1 W14 trade (+$10.8k Apr 1 Schick)

**Byte-identical.** v1 already captured the only W14 opp the gate could see.

## Verdict
NULL — burn-in was not the binding constraint. The W13/W14 gap is a
**data availability issue**, not a strategy parameter issue. Subagent's
iter-86 hypothesis was wrong: it assumed reducing burn-in would expose
new opps, but our DB only starts Mar 26 23:51 — we have no actual W13
data and only fragmentary late-W14 data. The 1 W14 trade v1 caught is
the only one possible given current data coverage.

## Next direction
- DO NOT iterate burn-in further (no signal to be had).
- W13/W14 capture would require **scraping older data** (out of scope here).
- For stack growth from this band: try v3 with **lc threshold relaxed**
  (lc≥10 from 15) to widen W15-W17 candidate pool, or **dd≥0.15** to
  catch shallower dips. Both modify the working window where data exists.
- Or pivot to a different band — $20-30k mid_dip variant or $30-50k high.
