# Iter 83 — high-band ($50-200k) specialist NULL

## Hypothesis
Build a specialist that targets the $50-200k buy-price band on non-Friday entries
to fill the silent gap revealed by iter 82 footprint analysis (stack deploys $0
at any band ≥$20k on Monday and almost nothing on Mon/Tue/Wed/Thu/Sat in $50k+).

## Pre-validation findings

### High-band opportunity space
- **526 pessimistic opps** at $50k <= buy <= $200k across 4-week window (W13-W17).
- **434 non-Friday** of those.
- Whitelist filter (rating 86-91 + 12 promo card_types): **215 opps** survive.
- Median net ROI: **29.5%**. Median hold: **38h**. Median buy: **$86k**.
- Total notional: **$20.0M** — sizable cake.
- Stack overlap (existing 6 strategies vs NF high-band opps): **6/434 = 1.4%**.
  Confirmed orthogonality.

### Pre-buy signature (sample N=79 NF whitelist opps)
- dd_24h median: -8.5%, p25 -13.3%
- dd_72h median: -16.7%, p25 -22.8%
- dd_168h median: -37.8%, p25 -50.8%
- lc_avg_24h median: 19.1, p25 16.7, p75 20.8

Strong drawdown profile — these opps consistently follow a 3-day price slide.

### Gate sweep (catalog precision)
Target: ≥80 fires across 27 days at ≥40% precision.
| fire_h | dd_max | lc_min | fires | catalog_precision |
|--------|--------|--------|-------|--------------------|
| 11     | -0.10  | 14     | 1122  | 27.2%             |
| 11     | -0.15  | 14     | 804   | 28.0%             |
| 11     | -0.20  | 14     | 542   | 29.5%             |
| 11     | -0.25  | 14     | 388   | **31.4%**         |
| 7      | -0.15  | 14     | 746   | 29.4%             |
| 7      | -0.25  | 14     | 334   | ~32%              |

Catalog precision tops out at ~31%. The catalog's `top_k_per_card=3` only
records the BEST 3 trades per card per window, so this isn't a fair precision
test — many gate fires could still be profitable but missed by the catalog.

### Pre-sim with concurrency cap (8 slots, $125k notional, no loader drag)
| fire_h | dd_max | pt   | mh  | basket | trades | win | pnl       |
|--------|--------|------|-----|--------|--------|-----|-----------|
| 7      | -0.20  | 0.15 | 72  | 4      | 60     | 62% | +$127k    |
| 7      | -0.20  | 0.10 | 96  | 4      | 61     | 75% | +$101k    |
| 11     | -0.20  | 0.15 | 96  | 4      | 57     | 63% | -$27k     |
| 11     | -0.25  | 0.15 | 96  | 4      | 50     | 58% | -$137k    |

Marginal positive in the no-drag case.

### Pre-sim with realistic loader drag (4% buy slippage + 4% sell slippage)
| fire_h | dd_max | pt   | mh  | lc_min | trades | win | pnl       |
|--------|--------|------|-----|--------|--------|-----|-----------|
| 7      | -0.20  | 0.20 | 96  | 14     | 55     | 49% | -$100k    |
| 7      | -0.25  | 0.20 | 96  | 14     | 44     | 50% | -$124k    |
| 7      | -0.30  | 0.25 | 120 | 14     | 39     | 56% | -$109k    |
| 7      | -0.25  | 0.20 | 96  | 18     | 40     | 58% | -$57k     |
| 3      | -0.30  | 0.20 | 96  | 14     | 34     | 47% | -$250k    |

**Every drag-adjusted config goes negative.** Best is -$57k (lc>=18).

## Root cause: high-band ROI is too thin to absorb loader drag

The $50-200k band has median 29.5% catalog ROI per opp; subtract:
- ~9.6% pessimistic loader drag (BUY@max, SELL@min adjacent samples)
- 5% EA tax
- Concurrency penalty (only ~50 trades possible vs 215 opps)

Net ROI per trade collapses to ~+10% on wins, but losers with stops or
unrecovered cards eat -10 to -25%. Win rate at 50-58% × +10% – 50% × -15% = -2.5%
per trade × 50 trades × $125k notional = ~ -$150k.

The fundamental issue: **the same +20% catalog ROI that gives floor_buy_v19 a
huge edge (because 20% of $13k buy = $2.6k profit, drag = $1.2k drag, net $1.4k)
collapses at high band (20% of $100k buy = $20k profit, drag = $9.6k drag, net
$10.4k — but then a single -25% stop loses $25k). Asymmetry crushes you.**

## Verdict: NULL

Predicted PnL after loader drag: **-$50k to -$150k** across all tested configs.
This is well below the +$100k threshold for activation.

Per the iter prompt's honesty rule: writing this null and not committing the
strategy.

## Lessons / next direction
- **High-band is structurally hard** — each trade carries ~$10k loader cost; you
  need consistent +25%+ wins to offset losers. Catalog opps average +29% but
  realized after concurrency/stops drops to +10% on the median.
- **Better next direction**: Don't fight loader drag at high band. Instead:
  - **High-band SCALPING** with very tight stops (-5% smoothed) and small
    profit_target (+8%) — turn over slots fast, tolerate many small losses,
    take many small wins. Risk: stop-thrashing through the loader drag floor.
  - **Mid-band ($20-50k) specialist** — same drawdown signature, but drag is
    ~7% break-even vs ~9.6%, so 25% catalog ROI clears more easily.
  - **Card-tier filtering inside high band** — only TOTW/icon promos with
    historic >35% rebound rates, drop the broad whitelist. Smaller universe but
    higher per-trade ROI.

## Stack stays at $1.136M (was $1.145M per prompt; recalc shows $1.136M from
6 result files: v19=$480k, v19_ext=$215k, post_dump_v15=-$50k,
daily_trend_dip_v5=$143k, v24=$257k, monday_rebound_v1=$91k).
Gap to $2M: $864k.
