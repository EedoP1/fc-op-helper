# Iter94 Audit — Committed-but-Unstacked Strategies

## Method
1. Enumerated every `*_filtered_results.json` (126 files) on disk.
2. Excluded the 8 active stack members.
3. For each remaining strategy: computed organic PnL (excl. force-sell-at-boundary), filtered to org >= $80k.
4. For top 19: computed actual novel PnL = sum of `net_profit` on (ea_id, buy_day) keys NOT already in stack.
5. Re-ran the #1 candidate's backtest fresh (boundary 2026-04-24) since prior JSON was 2026-04-22.

## Stack reference (key universe = 163 (ea_id, buy_day) pairs)
floor_buy_v19 + v19_ext, post_dump_v15, daily_trend_dip_v5, floor_buy_v24, monday_rebound_v1, mid_dip_v2, low_dip_v3.

## Top 10 candidates by Δstack (novel PnL)

| Strategy | Org PnL | Novel PnL | Novel trades | Overlap |
|---|---:|---:|---:|---:|
| **floor_buy_v27** | $427,875 | **$359,625** | 10 | 28.6% |
| floor_buy_v33 | $202,187 | $202,187 | 12 | 0.0% |
| floor_buy_v19_absorb_exit_v2 | $310,450 | $199,600 | 8 | 61.9% |
| floor_buy_v25 | $180,424 | $142,399 | 12 | 58.6% |
| floor_buy_v29 | $124,349 | $129,862 | 8 | 57.9% |
| monday_bottom_v1 | $121,350 | $121,350 | 8 | 0.0% |
| post_dump_v11 | $131,250 | $117,675 | 17 | 10.5% |
| post_dump_v5 | $123,525 | $109,950 | 16 | 11.1% |
| combo_v13 | $263,812 | $82,462 | 16 | 52.9% |
| post_dump_v8 | $94,275 | $80,700 | 18 | 10.0% |

Plus 9 more between $11k-$74k novel (mostly combo_v8/v9/v10/v12 high org but high overlap, mid_dip_v1 actually negative novel PnL).

## Decision: register floor_buy_v27

- Fresh backtest 2026-04-24: +$364,999 total ($427,875 organic / $-62,876 boundary), 17 trades, 85.7% win.
- Δstack = +$359,625 (10 of 14 organic trades novel, 71.4% non-overlap).
- Bars: PASS 1 (org), 3 (win), 4 (|corr|=0.052), force-sell. FAIL W14/W15 (concentrated in W16=+$422k single-week burst).
- Concentration risk noted but matches loop's bar-FAIL-with-strong-org pattern (post_dump_v15, floor_buy_v24 added under similar conditions).
- Auto-discovery means no `__main__.py` edit needed; strategy file already in git, JSON refreshed.

## Runners-up (skipped this iter)
- floor_buy_v33 (+$202k novel, 0% overlap, fresh): worth a follow-up audit — fails win rate (33%) and corr (-0.77 vs promo_dip_buy).
- post_dump_v11 (+$117k novel, fresh-needed): 100% win, all 3 weeks pass, only fails corr; strong second-pick.
- monday_bottom_v1 (+$121k, 0% overlap): worth checking after v27 lands.
