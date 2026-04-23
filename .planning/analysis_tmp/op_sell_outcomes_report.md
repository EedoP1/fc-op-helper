# OP Sell Outcomes Analysis — 7-day window

Generated 2026-04-23 18:34 UTC from the live PostgreSQL DB (`op_seller@localhost:5432`). The SQLite file `op_seller.db` is 0 bytes / abandoned.

## TL;DR — 5 findings ordered by actionability

1. **Cap buy_price at 150k → 85% precision filter**, drops 39 slots of which 33 are never-sold. Only 6 sold slots (3% of winners) are collateral. 150k+ band has 16% sell-through vs 68% overall, and 11× more never-sold share than in the sold-bucket. Highest-precision filter in the dataset.

2. **Raise scorer MIN_SALES_PER_HOUR from 7 → 25 → 76% precision**, drops 34 slots (26 never-sold, only 8 sold). The current 7-SPH floor lets through illiquid cards: s_sph 10–25 has 25% sell-through vs 75% for 50+.

3. **Sell-through and PPH rank attributes differently.** Highest sell-through by buy_price is 30–75k (78%, PPH 4.7k), but highest mean PPH is 75–150k (67% sell-through, PPH 6.5k). op_ratio bucket is the opposite story: 20–40% op_ratio sells best (81%) but <20% earns most per-hour (PPH 4.5k). Simple sell-through optimization would starve the high-PPH tail. LM position has PPH 10.1k (vs 4.1k for ST) despite similar sell-through.

4. **None of the DOA warning flags discriminate well.** `low_op_ratio` triggers on 96% of never-sold AND 92% of sold — it's a description of the whole portfolio, not a warning. `high_listings_vs_sph` and `recent_price_spike_48h` never fire (thresholds too loose for the data). The actionable signal is price-band and SPH (findings 1–2), not a DOA flag.

5. **Suspected scorer bug: `player_scores.expected_profit` is ~60× inflated.** Sum of expected_profit across sold slots is 196M vs realized 3.2M. CLAUDE.md's formula `net_profit × op_ratio` doesn't match stored values (e.g. ea 67300559: stored 37M, formula gives 7.6k). Worth an afternoon to check `src/server/scorer_v3.py` — any efficiency ranking using expected_profit is compromised until fixed.

Baseline state: 47 fast-sell / 178 eventual-sell / 109 never-sold across 334 slot instances. Net realized 3,220,575 coins with 11,545,750 stuck in never-sold. Overall sell-through 67%, which sounds ok until you notice the sold ones take a median of 13h if they needed to relist.

## 0. Data map

**Reporting path (extension → backend):**

- Extension: `extension/src/transfer-list-cycle.ts` polls the EA Web App Transfer List every cycle, categorises into `sold` / `expired` / `listed`, and sends a `TRADE_REPORT_BATCH` message to the service worker (`extension/entrypoints/background.ts`).
- Service worker: `handleTradeReportBatch()` POSTs to `${BACKEND_URL}/api/v1/trade-records/batch` (falls back to `/api/v1/trade-records/direct` for single records).
- Backend handler: `src/server/api/actions.py::batch_trade_records` and `direct_trade_record`, which validate the ea_id against `portfolio_slots`, dedupe same-outcome within 5 minutes, and insert into the `trade_records` table.
- When the automation loop finishes a BUY/LIST action it uses `POST /api/v1/actions/{id}/complete` (same `actions.py`), which inserts the matching `trade_records` row.

**Tables / columns used:**

| Table | Columns used | Purpose |
|---|---|---|
| `trade_records` | `ea_id`, `action_type`, `price`, `outcome`, `recorded_at` | one row per lifecycle event |
| `portfolio_slots` | `ea_id`, `buy_price`, `sell_price`, `added_at`, `is_leftover` | the slots the extension is currently trading |
| `player_scores` | `ea_id`, `scored_at`, `buy_price`, `sell_price`, `margin_pct`, `op_sales`, `total_sales`, `op_ratio`, `expected_profit`, `efficiency`, `sales_per_hour`, `expected_profit_per_hour`, `scorer_version`, `max_sell_price` | scan-time rationale |
| `players` | `name`, `rating`, `position`, `league`, `nation`, `card_type`, `listing_count`, `listings_per_hour`, `sales_per_hour` | card metadata |
| `market_snapshots` | `ea_id`, `captured_at`, `current_lowest_bin`, `listing_count` | 48h pre-buy market context |

**Outcome values (distinct, live DB):** `bought` (662), `listed` (96,705), `sold` (8,461), `expired` (23,130). `action_type` is always `buy` (when outcome=bought) or `list` (all other outcomes).

**Example row** from `trade_records` for ea_id=67342264 (Kolo Touré):

```
recorded_at=2026-04-20 17:24:05, action_type=buy,  outcome=bought, price=16750
recorded_at=2026-04-20 17:24:05, action_type=list, outcome=listed, price=21250
recorded_at=2026-04-20 18:29:03, action_type=list, outcome=sold,   price=21250
```

**Slot-instance definition used throughout:** one instance per `bought` event. The instance covers everything from that `bought` up to the next `bought` for the same `ea_id` (or NOW() if no next buy). The instance's `sold_at` / `sold_price` is the first `sold` event inside that window; `expires_count` is the number of `expired` events inside that window.

**Empirical event semantics (verified on the live DB):**

- A `bought` event is followed by a `listed` event within 1 second (**307 of 333** cases; the remaining 26 have no tracked initial list but still produce sold/expired outcomes).
- Relists after an expire are sometimes reported as new `listed` events and sometimes not (`transfer-list-cycle.ts` calls `relistAll()` without reporting; the action-queue RELIST path does report). **Therefore the `expired` count, not the `listed` count, is the reliable proxy for how many relist cycles a slot has been through.**
- A slot can be bought again (rebought) after sold; each bought is its own instance. Over the 7-day window there are 334 instances across 148 distinct ea_ids.

## 1. Baseline summary

### Slot counts by bucket

| Bucket | N | % | Median hours-to-sell | Mean PPH (sold) |
|---|---|---|---|---|
| fast_sell (sold, 0 expires) | 47 | 14% | 1.00h | 11,437 |
| eventual_sell (sold, ≥1 expire) | 178 | 53% | 13.12h | 1,695 |
| never_sold (still active / leftover) | 109 | 33% | — | 0 |
| **Total** | **334** | 100% | — | — |

### Coin flow

| Metric | Value |
|---|---|
| Total coin invested (all slots) | 23,758,400 |
| Coin invested in sold slots | 12,212,650 |
| Gross revenue (sold, pre-tax) | 16,245,500 |
| **Realized net profit (sold, after 5% EA tax)** | **3,220,575** |
| Capital currently stuck in never-sold slots | 11,545,750 |
| Capital-hours wasted on never-sold slots | 4,254 |
| Overall sell-through | 225/334 = **67%** |

### Realized vs scorer-predicted profit (sold slots only)

- Sum of `expected_profit` at scan time (sold slots, scorer rationale available): 195,711,393
- Sum of realized net profit after EA tax: 3,220,575
- Ratio realized/expected: 0.016

> **Flagged finding — `player_scores.expected_profit` is ~60× inflated.** CLAUDE.md says `expected_profit = net_profit × op_ratio`, but the stored column p50 ≈ 89k / p90 ≈ 691k / max ≈ 63M on cards whose realized net profit is 15k-40k. The inflation is large enough that this column cannot be used as-is for portfolio optimization. Check `src/server/scorer_v3.py` for the formula actually applied (top example: ea_id=67300559 stored expected_profit=37M on net_profit=262k × op_ratio=0.029 which should be 7,604). This bug is orthogonal to the OP-sell filter question but worth fixing before trusting any efficiency ranking.

## 2. PPH distribution (sold slots only)

N sold with valid PPH = 225.

| Percentile | PPH (coins/hour) |
|---|---|
| p25 | 457 |
| median | 1,277 |
| p75 | 3,775 |
| p90 | 8,310 |
| mean | 3,730 |

### PPH histogram

| PPH range | Count |
|---|---|
| 75.81 – 10,240 | 210 |
| 10,240 – 20,404 | 8 |
| 20,404 – 30,568 | 4 |
| 30,568 – 40,731 | 2 |
| 40,731 – 50,895 | 0 |
| 50,895 – 61,059 | 0 |
| 61,059 – 71,223 | 0 |
| 71,223 – 81,387 | 0 |
| 81,387 – 91,551 | 0 |
| 91,551 – 101,715 | 1 |

### Top 20 sold slots by PPH (fastest-earning)

| ea_id | name | card_type | rating | buy | sold | hrs | expires | PPH | margin% | op_ratio |
|---|---|---|---|---|---|---|---|---|---|---|
| 50407181 | Kerim Alajbegović | TOTS Breakthrough | 94 | 73,500 | 105,000 | 0.3 | 0 | 101,715 | 40.00 | 2% |
| 50331898 | David Beckham | Thunderstruck ICON | 89 | 115,000 | 161,000 | 1.0 | 0 | 37,869 | 40.00 | 2% |
| 67376395 | Romée Leuchter | Fantasy UT | 90 | 87,500 | 125,000 | 1.0 | 0 | 31,247 | 40.00 | 3% |
| 67373516 | Bradley Barcola | FUT Birthday | 90 | 85,000 | 121,000 | 1.0 | 0 | 29,913 | 40.00 | 1% |
| 67376395 | Romée Leuchter | Fantasy UT | 90 | 58,500 | 91,000 | 1.0 | 0 | 27,718 | 40.00 | 2% |
| 50493488 | Fernando Hierro | Trophy Titans ICON | 93 | 139,000 | 203,000 | 2.0 | 0 | 27,295 | 40.00 | 1% |
| 67121992 | Andriy Shevchenko | Trophy Titans ICON | 93 | 112,000 | 167,000 | 2.0 | 1 | 23,535 | 15.00 | 4% |
| 67247313 | Kaká | Trophy Titans ICON | 90 | 132,000 | 157,000 | 1.0 | 0 | 17,628 | 10.00 | 6% |
| 67247313 | Kaká | Trophy Titans ICON | 90 | 143,000 | 168,000 | 1.0 | 0 | 16,613 | 15.00 | 4% |
| 67247313 | Kaká | Trophy Titans ICON | 90 | 143,000 | 168,000 | 1.1 | 0 | 15,579 | 15.00 | 4% |
| 67373516 | Bradley Barcola | FUT Birthday | 90 | 107,000 | 126,000 | 1.0 | 0 | 12,709 | 40.00 | 1% |
| 100839972 | Marcelo | Trophy Titans ICON | 90 | 39,500 | 54,500 | 1.0 | 0 | 12,281 | 35.00 | 24% |
| 67247313 | Kaká | Trophy Titans ICON | 90 | 137,000 | 157,000 | 1.0 | 0 | 12,234 | 10.00 | 6% |
| 67376395 | Romée Leuchter | Fantasy UT | 90 | 64,000 | 91,000 | 2.0 | 1 | 11,248 | 40.00 | 2% |
| 50561528 | Aaron Wan-Bissaka | FUT Birthday | 89 | 34,000 | 47,250 | 1.0 | 0 | 11,034 | 40.00 | 2% |
| 67324562 | Mike Maignan | Team of the Season | 93 | 36,500 | 48,750 | 1.0 | 0 | 9,865 | 30.00 | 25% |
| 67373516 | Bradley Barcola | FUT Birthday | 90 | 95,500 | 121,000 | 2.0 | 1 | 9,686 | 25.00 | 1% |
| 67247313 | Kaká | Trophy Titans ICON | 90 | 140,000 | 157,000 | 1.0 | 0 | 9,133 | 10.00 | 6% |
| 67247313 | Kaká | Trophy Titans ICON | 90 | 140,000 | 157,000 | 1.0 | 0 | 9,115 | 10.00 | 6% |
| 67247313 | Kaká | Trophy Titans ICON | 90 | 138,000 | 157,000 | 1.2 | 0 | 9,018 | 10.00 | 6% |

### Bottom 20 sold slots by PPH (slowest-earning)

| ea_id | name | card_type | rating | buy | sold | hrs | expires | PPH | margin% | op_ratio |
|---|---|---|---|---|---|---|---|---|---|---|
| 50606697 | Enzo Francescoli | Trophy Titans Hero | 90 | 11,250 | 14,750 | 36.4 | 2 | 75.81 | 35.00 | 47% |
| 50593126 | Tasos Douvikas | Team of the Season | 90 | 12,250 | 17,750 | 58.1 | 5 | 79.35 | 40.00 | 20% |
| 50562517 | Unai Simón | Fantasy UT | 90 | 12,000 | 19,500 | 81.5 | 1 | 80.10 | 40.00 | 9% |
| 67109952 | Alessandro Nesta | Trophy Titans ICON | 88 | 12,750 | 18,500 | 46.1 | 1 | 105 | 40.00 | 22% |
| 67356092 | Dayne St. Clair | Team of the Season | 92 | 14,750 | 20,750 | 45.1 | 3 | 110 | 40.00 | 12% |
| 50563391 | Keinan Davis | TOTS Breakthrough | 90 | 11,500 | 15,000 | 23.7 | 2 | 116 | 30.00 | 50% |
| 84156597 | Diego Luna | TOTS Breakthrough | 93 | 16,750 | 23,000 | 36.2 | 3 | 141 | 30.00 | 22% |
| 50561528 | Aaron Wan-Bissaka | FUT Birthday | 89 | 32,000 | 47,250 | 87.3 | 1 | 148 | 40.00 | 2% |
| 67335839 | Caroline Seger | TOTY ICON | 91 | 90,000 | 112,000 | 100.8 | 2 | 163 | 20.00 | 1% |
| 67340944 | Jack Harrison | UECL Road to the Final | 89 | 14,000 | 24,500 | 55.1 | 1 | 168 | 40.00 | 8% |
| 50594228 | Darko Nejašmić | Team of the Season | 93 | 19,000 | 29,250 | 51.5 | 4 | 171 | 35.00 | 11% |
| 50606697 | Enzo Francescoli | Trophy Titans Hero | 90 | 11,250 | 14,750 | 15.8 | 3 | 174 | 30.00 | 67% |
| 84138242 | Ayase Ueda | Team of the Season | 92 | 21,750 | 28,250 | 28.8 | 1 | 177 | 30.00 | 13% |
| 67355824 | Mohamed Ihattaren | TOTS Breakthrough | 91 | 11,500 | 16,250 | 22.1 | 4 | 178 | 35.00 | 53% |
| 50567119 | Bartosz Nowak | Team of the Season | 93 | 16,500 | 25,250 | 38.6 | 4 | 194 | 40.00 | 23% |
| 67342564 | Gianluca Vialli | Trophy Titans Hero | 91 | 15,000 | 24,500 | 41.8 | 1 | 198 | 40.00 | 16% |
| 67371085 | Luciano Valente | TOTS Breakthrough | 92 | 13,000 | 17,500 | 17.9 | 1 | 202 | 35.00 | 17% |
| 67345360 | Mattéo Guendouzi | Fantasy UT | 91 | 30,000 | 49,000 | 81.5 | 1 | 203 | 40.00 | 1% |
| 67371085 | Luciano Valente | TOTS Breakthrough | 92 | 14,000 | 20,000 | 24.3 | 4 | 206 | 35.00 | 18% |
| 67356092 | Dayne St. Clair | Team of the Season | 92 | 13,500 | 19,250 | 23.2 | 2 | 207 | 40.00 | 8% |

## 3. Attribute-level lift (sold slots vs overall)

### rating_bucket

| value | n | sold | sell-through | lift vs overall | mean PPH (sold) | capital-wtd PPH |
|---|---|---|---|---|---|---|
| 89-91 | 176 | 126 | 72% | 1.06× | 4,027 | 2,883 |
| 92+ | 135 | 93 | 69% | 1.02× | 3,528 | 2,430 |
| 86-88 | 19 | 5 | 26% | 0.39× | 705 | 185 |

### buy_price_bucket

| value | n | sold | sell-through | lift vs overall | mean PPH (sold) | capital-wtd PPH |
|---|---|---|---|---|---|---|
| 30-75k | 76 | 60 | 79% | 1.17× | 4,703 | 3,713 |
| 15-30k | 64 | 50 | 78% | 1.16× | 1,931 | 1,509 |
| 5-15k | 68 | 51 | 75% | 1.11× | 1,238 | 929 |
| 75-150k | 87 | 58 | 67% | 0.99× | 6,522 | 4,348 |
| 150k+ | 39 | 6 | 15% | 0.23× | 3,198 | 492 |

### margin_bucket

| value | n | sold | sell-through | lift vs overall | mean PPH (sold) | capital-wtd PPH |
|---|---|---|---|---|---|---|
| 10-15% | 14 | 14 | 100% | 1.48× | 6,199 | 6,199 |
| 15-25% | 46 | 35 | 76% | 1.13× | 3,649 | 2,777 |
| 25%+ | 273 | 176 | 64% | 0.96× | 3,550 | 2,289 |

### sph_bucket (scorer)

| value | n | sold | sell-through | lift vs overall | mean PPH (sold) | capital-wtd PPH |
|---|---|---|---|---|---|---|
| 50+ | 244 | 184 | 75% | 1.12× | 3,848 | 2,902 |
| 25-50 | 56 | 33 | 59% | 0.87× | 3,477 | 2,049 |
| 10-25 | 32 | 8 | 25% | 0.37× | 2,062 | 515 |

### lcount_bucket (players.listing_count at scan)

| value | n | sold | sell-through | lift vs overall | mean PPH (sold) | capital-wtd PPH |
|---|---|---|---|---|---|---|
| 20-50 | 266 | 191 | 72% | 1.07× | 3,143 | 2,257 |
| 50-100 | 12 | 7 | 58% | 0.87× | 751 | 438 |
| <20 | 56 | 27 | 48% | 0.72× | 8,658 | 4,175 |

### position

| value | n | sold | sell-through | lift vs overall | mean PPH (sold) | capital-wtd PPH |
|---|---|---|---|---|---|---|
| GK | 27 | 21 | 78% | 1.15× | 1,908 | 1,484 |
| CAM | 50 | 37 | 74% | 1.10× | 4,351 | 3,220 |
| RM | 26 | 19 | 73% | 1.08× | 4,404 | 3,218 |
| CB | 49 | 35 | 71% | 1.06× | 2,530 | 1,807 |
| LM | 17 | 12 | 71% | 1.05× | 10,129 | 7,150 |
| ST | 62 | 42 | 68% | 1.01× | 4,078 | 2,763 |
| LW | 22 | 14 | 64% | 0.94× | 6,287 | 4,001 |
| RB | 22 | 14 | 64% | 0.94× | 2,096 | 1,334 |
| CM | 25 | 13 | 52% | 0.77× | 1,222 | 635 |
| LB | 12 | 6 | 50% | 0.74× | 3,257 | 1,629 |
| CDM | 18 | 9 | 50% | 0.74× | 1,645 | 823 |

### card_type

| value | n | sold | sell-through | lift vs overall | mean PPH (sold) | capital-wtd PPH |
|---|---|---|---|---|---|---|
| FUT Birthday Icon | 5 | 5 | 100% | 1.48× | 1,337 | 1,337 |
| Trophy Titans Hero | 11 | 10 | 91% | 1.35× | 833 | 757 |
| UEL Road to the Final | 8 | 7 | 88% | 1.30× | 2,283 | 1,998 |
| Fantasy UT | 17 | 14 | 82% | 1.22× | 6,689 | 5,509 |
| Trophy Titans ICON | 61 | 49 | 80% | 1.19× | 4,956 | 3,981 |
| UECL Road to the Final | 5 | 4 | 80% | 1.19× | 2,879 | 2,303 |
| TOTS Breakthrough | 39 | 30 | 77% | 1.14× | 5,378 | 4,137 |
| Rare | 14 | 10 | 71% | 1.06× | 2,206 | 1,576 |
| FUT Birthday | 30 | 21 | 70% | 1.04× | 4,739 | 3,317 |
| Team of the Season | 95 | 63 | 66% | 0.98× | 2,020 | 1,340 |
| Knockout Royalty | 6 | 2 | 33% | 0.49× | 2,719 | 906 |
| FoF: Answer the Call | 6 | 2 | 33% | 0.49× | 770 | 257 |
| Team of the Week | 8 | 1 | 12% | 0.19× | 375 | 46.92 |

### nation

| value | n | sold | sell-through | lift vs overall | mean PPH (sold) | capital-wtd PPH |
|---|---|---|---|---|---|---|
| unknown | 334 | 225 | 67% | 1.00× | 3,730 | 2,513 |

### op_ratio bucket

| value | n | sold | sell-through | lift vs overall | mean PPH (sold) | capital-wtd PPH |
|---|---|---|---|---|---|---|
| 20-40% | 69 | 56 | 81% | 1.20× | 1,999 | 1,623 |
| 40-60% | 9 | 7 | 78% | 1.15× | 862 | 671 |
| <20% | 253 | 159 | 63% | 0.93× | 4,515 | 2,838 |

### total_sales bucket

| value | n | sold | sell-through | lift vs overall | mean PPH (sold) | capital-wtd PPH |
|---|---|---|---|---|---|---|
| 20-50 | 12 | 9 | 75% | 1.11× | 1,842 | 1,382 |
| <20 | 29 | 20 | 69% | 1.02× | 1,042 | 719 |
| 100+ | 265 | 180 | 68% | 1.01× | 4,277 | 2,905 |
| 50-100 | 28 | 16 | 57% | 0.85× | 2,000 | 1,143 |

### Where sell-through and PPH disagree

- **buy_price_bucket**: highest sell-through = `30-75k` (lift 1.17×, PPH 4,703); highest mean PPH = `75-150k` (lift 0.99×, PPH 6,522).
- **lcount_bucket (players.listing_count at scan)**: highest sell-through = `20-50` (lift 1.07×, PPH 3,143); highest mean PPH = `<20` (lift 0.72×, PPH 8,658).
- **position**: highest sell-through = `GK` (lift 1.15×, PPH 1,908); highest mean PPH = `LM` (lift 1.05×, PPH 10,129).
- **card_type**: highest sell-through = `FUT Birthday Icon` (lift 1.48×, PPH 1,337); highest mean PPH = `Fantasy UT` (lift 1.22×, PPH 6,689).
- **op_ratio bucket**: highest sell-through = `20-40%` (lift 1.20×, PPH 1,999); highest mean PPH = `<20%` (lift 0.93×, PPH 4,515).
- **total_sales bucket**: highest sell-through = `20-50` (lift 1.11×, PPH 1,842); highest mean PPH = `100+` (lift 1.01×, PPH 4,277).

## 4. Never-sold profile

N never-sold = 109 (33% of slots).

### rating_bucket

| value | never-sold % | sold % | lift (never/sold) |
|---|---|---|---|
| 89-91 | 46% | 56% | 0.82 |
| 92+ | 39% | 41% | 0.93 |
| 86-88 | 13% | 2% | 5.78 |
| 83-85 | 2% | 0% | 4.13 |
| 80-82 | 1% | 0% | n/a |

### buy_price_bucket

| value | never-sold % | sold % | lift (never/sold) |
|---|---|---|---|
| 150k+ | 30% | 3% | 11.35 |
| 75-150k | 27% | 26% | 1.03 |
| 5-15k | 16% | 23% | 0.69 |
| 30-75k | 15% | 27% | 0.55 |
| 15-30k | 13% | 22% | 0.58 |

### sph_bucket (scorer)

| value | never-sold % | sold % | lift (never/sold) |
|---|---|---|---|
| 50+ | 55% | 82% | 0.67 |
| 10-25 | 22% | 4% | 6.19 |
| 25-50 | 21% | 15% | 1.44 |
| 5-10 | 2% | 0% | n/a |

## 5. Realized vs predicted margin

| scorer margin bucket | n | mean predicted margin | mean realized margin | delta (realized - predicted) |
|---|---|---|---|---|
| 10-15% | 14 | 10.0% | 8.9% | -1.1pp |
| 15-25% | 35 | 16.9% | 21.6% | +4.8pp |
| 25%+ | 176 | 35.9% | 38.4% | +2.6pp |

### By slot bucket

| slot bucket | n | mean realized margin | mean predicted margin |
|---|---|---|---|
| fast_sell | 47 | 26.2% | 28.1% |
| eventual_sell | 178 | 36.0% | 32.1% |

## 6. Dead-on-arrival signals (never-sold slots)

For each never-sold slot, check warning signs that were visible at scan time.

| warning flag | never-sold triggering | share of never-sold |
|---|---|---|
| low_total_sales | 9 | 8% |
| high_listings_vs_sph | 0 | 0% |
| recent_price_spike_48h | 0 | 0% |
| low_op_ratio | 105 | 96% |
| low_margin | 1 | 1% |
| scan_was_stale | 3 | 3% |

Slots with **at least one** warning flag: **108** (99%).
Slots with **zero** warning flags (genuinely bad luck): **1** (1%).

### Flag rate comparison (never-sold vs sold)

| flag | never-sold rate | sold rate | lift |
|---|---|---|---|
| low_total_sales | 8% | 9% | 0.93× |
| high_listings_vs_sph | 0% | 0% | n/a× |
| recent_price_spike_48h | 0% | 0% | n/a× |
| low_op_ratio | 96% | 92% | 1.05× |
| low_margin | 1% | 0% | n/a× |
| scan_was_stale | 3% | 2% | 1.24× |

## 7. Repeat-offender slots (by capital-hours wasted)

| ea_id | name | rating | card_type | instances | never-sold | total expires | capital-hours | stuck now | net profit |
|---|---|---|---|---|---|---|---|---|---|
| 67110469 | Robert Pirès | 93 | Trophy Titans ICON | 1 | 1 | 3 | 23,953,284 | 337,000 | 0 |
| 100941142 | Nico Paz | 90 | Future Stars | 1 | 1 | 5 | 20,769,950 | 166,000 | 0 |
| 50408335 | Estêvão | 88 | UCL Road to the Knockouts | 2 | 1 | 7 | 18,554,184 | 157,000 | 69,400 |
| 84094910 | Jamie Vardy | 90 | FUT Birthday | 2 | 0 | 5 | 17,369,225 | 0 | 117,000 |
| 50602612 | Jobe Bellingham | 89 | FUT Birthday | 1 | 1 | 1 | 15,775,950 | 151,000 | 0 |
| 67322820 | Adama Traoré | 89 | Fantasy UT | 3 | 1 | 9 | 15,225,498 | 147,000 | 104,850 |
| 67377302 | Alejandro Garnacho | 89 | FC Pro Live | 1 | 1 | 5 | 14,060,562 | 140,000 | 0 |
| 84157887 | Ethan Nwaneri | 91 | Fantasy UT | 1 | 1 | 8 | 13,867,304 | 110,000 | 0 |
| 67122607 | Steven Gerrard | 90 | Knockout Royalty Icon | 1 | 1 | 3 | 13,826,050 | 142,000 | 0 |
| 67344654 | Kai Havertz | 91 | Knockout Royalty | 1 | 1 | 3 | 13,520,456 | 161,000 | 0 |

### Top 10 by coins lost (capital stuck + negative net)

| ea_id | name | stuck now | net profit (sold instances) | loss (stuck − net) |
|---|---|---|---|---|
| 84112348 | Federico Dimarco | 455,000 | 0 | 455,000 |
| 83887194 | Roberto Baggio | 362,000 | 0 | 362,000 |
| 67110469 | Robert Pirès | 337,000 | 0 | 337,000 |
| 67110043 | Gianluigi Buffon | 320,000 | 0 | 320,000 |
| 67350373 | Mauro Júnior | 304,000 | 0 | 304,000 |
| 84113326 | Lucy Bronze | 301,000 | 0 | 301,000 |
| 231747 | Kylian Mbappé | 262,000 | 0 | 262,000 |
| 67301045 | Marco van Basten | 222,000 | 0 | 222,000 |
| 50596300 | Bradley Barcola | 205,000 | 0 | 205,000 |
| 100941250 | Kenan Yıldız | 204,000 | 0 | 204,000 |

## 8. Scorer version comparison

| scorer_version | n slots | sold | sell-through | mean PPH (sold) | mean realized profit/slot |
|---|---|---|---|---|---|
| v3 | 333 | 225 | 68% | 3,730 | 14,314 |
| unknown | 1 | 0 | 0% | n/a | n/a |

> Only one non-null scorer_version is represented in the window — no meaningful side-by-side comparison possible. Older scores (v1/v2) are deleted on server startup.

## 9. Filter recommendations (two-axis impact)

Candidates only considered if backed by ≥20 slots in the 7-day window.

Columns: **precision** = % of dropped slots that were actually never-sold (higher = more surgical). **recall** = % of total never-sold captured. **winner loss** = % of total sold slots dropped. **Δ PPH** = portfolio profit-per-coin-hour change.

| filter | dropped | precision | recall (never) | winner loss | coins saved | profit lost | Δ PPH |
|---|---|---|---|---|---|---|---|
| drop slots with scorer op_ratio < 10% (extreme optimism) | 207 | 38% | 72% | 57% | 10,627,250 | 2,600,937 | +0.008 |
| cap buy_price at 75k AND raise sph to 25 (combined) | 127 | 49% | 57% | 29% | 10,232,500 | 1,759,800 | +0.006 |
| cap buy_price at 75k (drop >=75k slots) | 126 | 49% | 57% | 28% | 10,232,500 | 1,737,100 | +0.006 |
| cap buy_price at 150k (drop >=150k slots) | 39 | 85% | 30% | 3% | 6,921,000 | 306,950 | +0.002 |
| raise MIN_SALES_PER_HOUR 7 -> 25 (scorer s_sph) | 34 | 76% | 24% | 4% | 4,424,000 | 325,200 | +0.001 |
| raise MIN_LIVE_LISTINGS 20 -> 50 (players.listing_count) | 322 | 32% | 95% | 97% | 11,464,000 | 3,172,862 | +0.001 |
| raise MIN_OP_SALES 3 -> 5 (scorer op_sales) | 70 | 49% | 31% | 16% | 4,033,000 | 864,300 | -0.000 |
| drop slots with scorer total_sales < 20 | 29 | 31% | 8% | 9% | 145,500 | 109,062 | -0.000 |

### Ranked recommendations (most surgical first)

Preference: high precision (dropping slots that were truly dead) with positive PPH delta. A filter that drops 200 slots for a huge PPH gain is worse than one that drops 40 but precisely targets the never-sold ones — because freed capital needs somewhere to go, and killing trading volume you do want is a hidden cost.

**1.** cap buy_price at 150k (drop >=150k slots) — 150k+ buys are the worst price band; keeps 75-150k tier intact. Drops 39 slots (**precision 85%**: 33 never-sold / 6 sold). Coins saved: 6,921,000. Profit sacrificed: 306,950. Portfolio PPH 0.005 → 0.007 (**+0.002**).
**2.** raise MIN_SALES_PER_HOUR 7 -> 25 (scorer s_sph) — sph_bucket 10-25 has 25% sell-through vs 75% for 50+; current 7-cutoff too permissive. Drops 34 slots (**precision 76%**: 26 never-sold / 8 sold). Coins saved: 4,424,000. Profit sacrificed: 325,200. Portfolio PPH 0.005 → 0.006 (**+0.001**).
**3.** cap buy_price at 75k (drop >=75k slots) — 150k+ cards have 16% sell-through vs 78% for 30-75k, 11x never-sold lift. Drops 126 slots (**precision 49%**: 62 never-sold / 64 sold). Coins saved: 10,232,500. Profit sacrificed: 1,737,100. Portfolio PPH 0.005 → 0.011 (**+0.006**).
**4.** cap buy_price at 75k AND raise sph to 25 (combined) — stack the two strongest signals. Drops 127 slots (**precision 49%**: 62 never-sold / 65 sold). Coins saved: 10,232,500. Profit sacrificed: 1,759,800. Portfolio PPH 0.005 → 0.011 (**+0.006**).
**5.** drop slots with scorer op_ratio < 10% (extreme optimism) — op_ratio <10% means almost no verified OP sales at the chosen margin. Drops 207 slots (**precision 38%**: 78 never-sold / 129 sold). Coins saved: 10,627,250. Profit sacrificed: 2,600,937. Portfolio PPH 0.005 → 0.013 (**+0.008**).
**6.** raise MIN_LIVE_LISTINGS 20 -> 50 (players.listing_count) — listing_count <20 has 40% sell-through vs 73% for 20-50. Drops 322 slots (**precision 32%**: 104 never-sold / 218 sold). Coins saved: 11,464,000. Profit sacrificed: 3,172,862. Portfolio PPH 0.005 → 0.006 (**+0.001**).

### Filters that don't help (don't apply):

- raise MIN_OP_SALES 3 -> 5 (scorer op_sales): Δ PPH -0.000. Drops 36 sold slots for only 34 never-sold — bad trade.
- drop slots with scorer total_sales < 20: Δ PPH -0.000. Drops 20 sold slots for only 9 never-sold — bad trade.

## Caveats

- Window = 7 days. Only 334 slot instances. Any per-attribute cell with <5 samples was suppressed. Any filter recommendation with <20 matching slots was suppressed.
- Join coverage: only 334/334 slot instances have a pre-buy scorer rationale (the rest were added outside a scan window or before v3 scoring).
- Relists after the first expire are not always tracked as new `listed` events (transfer-list-cycle uses bulk `relistAll()` inline without reporting). `expires_count` is the reliable proxy for how many listing cycles a slot has been through.
- 'Never-sold' includes slots that may still sell soon — a slot bought 2 hours ago classified as never-sold now may become fast-sell within an hour. Hours-tied-up in the top of the list is smaller than at the bottom; the capital-weighted PPH accounts for this.
- The 222 leftover slots from `portfolio_slots.is_leftover=true` are not included directly in this analysis — they're accounted for only if they generated a `bought` event in the last 7 days.
- EA tax assumed at 5%. Scorer's predicted margin uses `margin_pct` as an integer percent.
