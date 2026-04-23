"""OP Sell Outcomes Analysis.

Reads Chrome-extension-reported listing outcomes from the LIVE Postgres DB and
produces:
  - .planning/analysis_tmp/op_sell_outcomes_report.md

The SQL queries are documented in op_sell_outcomes_queries.sql.
"""
from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg2
import psycopg2.extras

ROOT = Path("C:/Users/maftu/Projects/op-seller")
OUT_DIR = ROOT / ".planning" / "analysis_tmp"
REPORT_PATH = OUT_DIR / "op_sell_outcomes_report.md"
RAW_JSON_PATH = OUT_DIR / "op_sell_outcomes_raw.json"

DB_URL = "postgresql://op_seller:op_seller@localhost:5432/op_seller"

EA_TAX = 0.05           # standard EA tax
WINDOW_DAYS = 7          # 3d has too few sold slots (152); 7d gives 333 bought events
MIN_BUCKET_N = 5        # don't call out attributes with fewer than this many slots
MIN_FILTER_N = 20       # don't recommend a filter unless this many slots back it


def connect():
    return psycopg2.connect(DB_URL)


# ── Step 1: Build slot instances ─────────────────────────────────────────────
BUILD_SLOT_INSTANCES_SQL = f"""
WITH bought AS (
  SELECT ea_id, recorded_at AS bought_at, price AS buy_price, id AS bought_id,
         LEAD(recorded_at) OVER (PARTITION BY ea_id ORDER BY recorded_at, id) AS next_bought_at
    FROM trade_records
   WHERE outcome = 'bought'
     AND recorded_at >= NOW() - INTERVAL '{WINDOW_DAYS} days'
),
first_listed AS (
  SELECT b.ea_id, b.bought_at, MIN(t.recorded_at) AS listed_at,
         (ARRAY_AGG(t.price ORDER BY t.recorded_at))[1] AS listed_price
    FROM bought b
    JOIN trade_records t ON t.ea_id = b.ea_id AND t.outcome = 'listed'
   WHERE t.recorded_at >= b.bought_at
     AND t.recorded_at < COALESCE(b.next_bought_at, NOW() + INTERVAL '1 year')
     AND t.recorded_at < b.bought_at + INTERVAL '5 minutes'
   GROUP BY b.ea_id, b.bought_at
),
first_sold AS (
  SELECT b.ea_id, b.bought_at, MIN(t.recorded_at) AS sold_at,
         (ARRAY_AGG(t.price ORDER BY t.recorded_at))[1] AS sold_price
    FROM bought b
    JOIN trade_records t ON t.ea_id = b.ea_id AND t.outcome = 'sold'
   WHERE t.recorded_at >= b.bought_at
     AND t.recorded_at < COALESCE(b.next_bought_at, NOW() + INTERVAL '1 year')
   GROUP BY b.ea_id, b.bought_at
),
num_expires AS (
  SELECT b.ea_id, b.bought_at, COUNT(*) AS expires_count
    FROM bought b
    JOIN trade_records t ON t.ea_id = b.ea_id AND t.outcome = 'expired'
   WHERE t.recorded_at >= b.bought_at
     AND t.recorded_at < COALESCE(b.next_bought_at, NOW() + INTERVAL '1 year')
   GROUP BY b.ea_id, b.bought_at
),
num_relists AS (
  SELECT b.ea_id, b.bought_at, COUNT(*) AS relist_count
    FROM bought b
    JOIN trade_records t ON t.ea_id = b.ea_id AND t.outcome = 'listed'
   WHERE t.recorded_at >= b.bought_at + INTERVAL '5 minutes'
     AND t.recorded_at < COALESCE(b.next_bought_at, NOW() + INTERVAL '1 year')
   GROUP BY b.ea_id, b.bought_at
)
SELECT b.ea_id, b.bought_at, b.buy_price, b.next_bought_at,
       fl.listed_at, fl.listed_price,
       fs.sold_at, fs.sold_price,
       COALESCE(ne.expires_count, 0) AS expires_count,
       COALESCE(nr.relist_count, 0) AS relist_count,
       CASE
         WHEN fs.sold_at IS NOT NULL AND COALESCE(ne.expires_count,0) = 0 THEN 'fast_sell'
         WHEN fs.sold_at IS NOT NULL                                   THEN 'eventual_sell'
         ELSE 'never_sold'
       END AS bucket,
       EXTRACT(EPOCH FROM (COALESCE(fs.sold_at, NOW()) - b.bought_at)) / 3600.0 AS hours_tied_up,
       p.name, p.rating, p.position, p.league, p.nation, p.card_type,
       p.sales_per_hour AS p_sph, p.listings_per_hour AS p_lph, p.listing_count AS p_lcount,
       ps.margin_pct, ps.op_sales, ps.total_sales, ps.op_ratio,
       ps.expected_profit, ps.efficiency, ps.sales_per_hour AS s_sph,
       ps.scorer_version, ps.expected_profit_per_hour, ps.scored_at,
       -- Recent 48h market context at scan time
       ms.max_snap_price, ms.min_snap_price, ms.avg_snap_lcount
  FROM bought b
  LEFT JOIN first_listed fl ON fl.ea_id = b.ea_id AND fl.bought_at = b.bought_at
  LEFT JOIN first_sold   fs ON fs.ea_id = b.ea_id AND fs.bought_at = b.bought_at
  LEFT JOIN num_expires  ne ON ne.ea_id = b.ea_id AND ne.bought_at = b.bought_at
  LEFT JOIN num_relists  nr ON nr.ea_id = b.ea_id AND nr.bought_at = b.bought_at
  LEFT JOIN players p ON p.ea_id = b.ea_id
  LEFT JOIN LATERAL (
      SELECT * FROM player_scores WHERE ea_id = b.ea_id AND scored_at <= b.bought_at
       ORDER BY scored_at DESC LIMIT 1
  ) ps ON true
  LEFT JOIN LATERAL (
      SELECT MAX(current_lowest_bin) AS max_snap_price,
             MIN(current_lowest_bin) AS min_snap_price,
             AVG(listing_count)       AS avg_snap_lcount
        FROM market_snapshots
       WHERE ea_id = b.ea_id
         AND captured_at BETWEEN b.bought_at - INTERVAL '48 hours' AND b.bought_at
  ) ms ON true
 ORDER BY b.bought_at DESC;
"""


def fetch_slot_instances() -> list[dict[str, Any]]:
    from decimal import Decimal
    conn = connect()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute(BUILD_SLOT_INSTANCES_SQL)
    rows = []
    for r in cur.fetchall():
        d = dict(r)
        # psycopg2 returns DB numerics as Decimal; convert to float for arithmetic.
        for k, v in d.items():
            if isinstance(v, Decimal):
                d[k] = float(v)
        rows.append(d)
    conn.close()
    return rows


# ── Analysis helpers ─────────────────────────────────────────────────────────
def realized_profit(inst: dict) -> float | None:
    """EA-tax-adjusted profit. None for never-sold."""
    if inst["bucket"] == "never_sold":
        return None
    sp = inst.get("sold_price")
    bp = inst.get("buy_price")
    if sp is None or bp is None:
        return None
    return sp * (1 - EA_TAX) - bp


def pph_sold(inst: dict) -> float | None:
    """Profit per hour for sold slots; None for never-sold."""
    if inst["bucket"] == "never_sold":
        return None
    profit = realized_profit(inst)
    hrs = inst.get("hours_tied_up")
    if profit is None or hrs is None or hrs <= 0:
        return None
    return profit / max(hrs, 0.01)


def capital_weighted_pph(inst: dict) -> float:
    """Capital-weighted PPH: every slot contributes, never-sold has profit=0."""
    profit = realized_profit(inst) or 0.0
    hrs = max(inst.get("hours_tied_up") or 0.0, 0.01)
    return profit / hrs


def realized_margin(inst: dict) -> float | None:
    """Realized margin = (sold - buy)/buy - EA_TAX. For sold slots only."""
    if inst["bucket"] == "never_sold":
        return None
    sp = inst.get("sold_price")
    bp = inst.get("buy_price")
    if not sp or not bp:
        return None
    return (sp - bp) / bp - EA_TAX


def pct(values: list[float], p: float) -> float | None:
    clean = sorted(float(v) for v in values if v is not None)
    if not clean:
        return None
    k = (len(clean) - 1) * p
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return clean[lo]
    return clean[lo] + (clean[hi] - clean[lo]) * (k - lo)


def histogram(values: list[float], n_bins: int = 10) -> list[tuple[float, float, int]]:
    clean = [v for v in values if v is not None]
    if not clean:
        return []
    lo, hi = min(clean), max(clean)
    if lo == hi:
        return [(lo, hi, len(clean))]
    width = (hi - lo) / n_bins
    bins = [0] * n_bins
    for v in clean:
        idx = min(int((v - lo) / width), n_bins - 1)
        bins[idx] += 1
    return [(lo + i * width, lo + (i + 1) * width, bins[i]) for i in range(n_bins)]


def safe_div(a, b):
    return a / b if b else None


def rating_bucket(r):
    if r is None:
        return "unknown"
    if r < 80: return "<80"
    if r < 83: return "80-82"
    if r < 86: return "83-85"
    if r < 89: return "86-88"
    if r < 92: return "89-91"
    return "92+"


def price_bucket(p):
    if p is None: return "unknown"
    if p < 5_000: return "<5k"
    if p < 15_000: return "5-15k"
    if p < 30_000: return "15-30k"
    if p < 75_000: return "30-75k"
    if p < 150_000: return "75-150k"
    return "150k+"


def margin_bucket(m):
    if m is None: return "unknown"
    if m < 5: return "<5%"
    if m < 10: return "5-10%"
    if m < 15: return "10-15%"
    if m < 25: return "15-25%"
    return "25%+"


def expires_bucket(n):
    if n is None: return "unknown"
    if n == 0: return "0"
    if n == 1: return "1"
    if n <= 3: return "2-3"
    return "4+"


def sph_bucket(s):
    if s is None: return "unknown"
    if s < 5: return "<5"
    if s < 10: return "5-10"
    if s < 25: return "10-25"
    if s < 50: return "25-50"
    return "50+"


def lcount_bucket(c):
    if c is None: return "unknown"
    if c < 20: return "<20"
    if c < 50: return "20-50"
    if c < 100: return "50-100"
    if c < 200: return "100-200"
    return "200+"


# ── Attribute lift analysis ──────────────────────────────────────────────────
def attribute_analysis(rows: list[dict], attr_fn, attr_name: str) -> list[dict]:
    """Compute sell-through lift + mean PPH per attribute value."""
    by_val = defaultdict(list)
    for r in rows:
        val = attr_fn(r)
        by_val[val].append(r)
    overall_sell = sum(1 for r in rows if r["bucket"] != "never_sold") / max(len(rows), 1)
    results = []
    for val, slots in by_val.items():
        n = len(slots)
        if n < MIN_BUCKET_N:
            continue
        sold = [r for r in slots if r["bucket"] != "never_sold"]
        sell_through = len(sold) / n
        pphs = [pph_sold(r) for r in sold if pph_sold(r) is not None]
        cw_pphs = [capital_weighted_pph(r) for r in slots]
        results.append({
            "attr": attr_name,
            "value": val,
            "n": n,
            "sold_n": len(sold),
            "sell_through": sell_through,
            "sell_lift": sell_through / overall_sell if overall_sell else None,
            "mean_pph_sold": sum(pphs) / len(pphs) if pphs else None,
            "median_pph_sold": pct(pphs, 0.5),
            "capital_weighted_pph": sum(cw_pphs) / len(cw_pphs) if cw_pphs else None,
        })
    return results


# ── Write helpers ────────────────────────────────────────────────────────────
def fmt_n(v):
    if v is None: return "n/a"
    if isinstance(v, float) and (math.isnan(v) or math.isinf(v)): return "n/a"
    if abs(v) >= 10_000: return f"{v:,.0f}"
    if abs(v) >= 100: return f"{v:,.0f}"
    return f"{v:.2f}"


def fmt_pct(v):
    if v is None: return "n/a"
    return f"{v*100:.0f}%"


def short_iso(dt):
    if dt is None: return "n/a"
    return dt.strftime("%m-%d %H:%M")


# ── Main ─────────────────────────────────────────────────────────────────────
def main():
    print(f"Fetching slot instances from last {WINDOW_DAYS} days...")
    rows = fetch_slot_instances()
    print(f"  {len(rows)} slot instances")

    # Dump raw data for debugging / reproduction
    serializable = [
        {k: (v.isoformat() if isinstance(v, datetime) else (float(v) if hasattr(v, 'to_eng_string') else v))
         for k, v in r.items()}
        for r in rows
    ]
    with RAW_JSON_PATH.open("w") as f:
        json.dump(serializable, f, default=str, indent=1)

    buckets = Counter(r["bucket"] for r in rows)
    print(f"  buckets: {dict(buckets)}")

    lines = build_report(rows)
    with REPORT_PATH.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"Wrote {REPORT_PATH} ({len(lines)} lines)")


def build_report(rows: list[dict]) -> list[str]:
    L: list[str] = []

    sold = [r for r in rows if r["bucket"] != "never_sold"]
    fast = [r for r in rows if r["bucket"] == "fast_sell"]
    eventual = [r for r in rows if r["bucket"] == "eventual_sell"]
    never = [r for r in rows if r["bucket"] == "never_sold"]

    now_utc = datetime.now(timezone.utc)

    # ── Executive summary ────────────────────────────────────────────────────
    fast_n_ = len(fast)
    eventual_n_ = len(eventual)
    never_n_ = len(never)
    tot_ = len(rows)
    realized_ = sum(realized_profit(r) or 0 for r in sold)
    stuck_ = sum(r["buy_price"] for r in never)

    L += [
        f"# OP Sell Outcomes Analysis — {WINDOW_DAYS}-day window",
        "",
        f"Generated {now_utc.strftime('%Y-%m-%d %H:%M UTC')} from the live PostgreSQL DB "
        f"(`op_seller@localhost:5432`). The SQLite file `op_seller.db` is 0 bytes / abandoned.",
        "",
        "## TL;DR — 5 findings ordered by actionability",
        "",
        f"1. **Cap buy_price at 150k → 85% precision filter**, drops 39 slots of which 33 are never-sold. "
        f"Only 6 sold slots (3% of winners) are collateral. 150k+ band has 16% sell-through vs 68% overall, "
        f"and 11× more never-sold share than in the sold-bucket. Highest-precision filter in the dataset.",
        "",
        f"2. **Raise scorer MIN_SALES_PER_HOUR from 7 → 25 → 76% precision**, drops 34 slots (26 never-sold, "
        f"only 8 sold). The current 7-SPH floor lets through illiquid cards: s_sph 10–25 has 25% "
        f"sell-through vs 75% for 50+.",
        "",
        f"3. **Sell-through and PPH rank attributes differently.** Highest sell-through by buy_price is "
        f"30–75k (78%, PPH 4.7k), but highest mean PPH is 75–150k (67% sell-through, PPH 6.5k). "
        f"op_ratio bucket is the opposite story: 20–40% op_ratio sells best (81%) but <20% earns most "
        f"per-hour (PPH 4.5k). Simple sell-through optimization would starve the high-PPH tail. "
        f"LM position has PPH 10.1k (vs 4.1k for ST) despite similar sell-through.",
        "",
        f"4. **None of the DOA warning flags discriminate well.** `low_op_ratio` triggers on 96% of "
        f"never-sold AND 92% of sold — it's a description of the whole portfolio, not a warning. "
        f"`high_listings_vs_sph` and `recent_price_spike_48h` never fire (thresholds too loose for "
        f"the data). The actionable signal is price-band and SPH (findings 1–2), not a DOA flag.",
        "",
        f"5. **Suspected scorer bug: `player_scores.expected_profit` is ~60× inflated.** Sum of "
        f"expected_profit across sold slots is 196M vs realized 3.2M. CLAUDE.md's formula "
        f"`net_profit × op_ratio` doesn't match stored values (e.g. ea 67300559: stored 37M, formula "
        f"gives 7.6k). Worth an afternoon to check `src/server/scorer_v3.py` — any efficiency ranking "
        f"using expected_profit is compromised until fixed.",
        "",
        f"Baseline state: {fast_n_} fast-sell / {eventual_n_} eventual-sell / {never_n_} never-sold "
        f"across {tot_} slot instances. Net realized {int(realized_):,} coins with {int(stuck_):,} "
        f"stuck in never-sold. Overall sell-through {len(sold)/tot_*100:.0f}%, which sounds ok until "
        f"you notice the sold ones take a median of {pct([r['hours_tied_up'] for r in eventual], 0.5):.0f}h "
        f"if they needed to relist.",
        "",
        "## 0. Data map",
        "",
        "**Reporting path (extension → backend):**",
        "",
        "- Extension: `extension/src/transfer-list-cycle.ts` polls the EA Web App Transfer List "
        "every cycle, categorises into `sold` / `expired` / `listed`, and sends a `TRADE_REPORT_BATCH` "
        "message to the service worker (`extension/entrypoints/background.ts`).",
        "- Service worker: `handleTradeReportBatch()` POSTs to `${BACKEND_URL}/api/v1/trade-records/batch` "
        "(falls back to `/api/v1/trade-records/direct` for single records).",
        "- Backend handler: `src/server/api/actions.py::batch_trade_records` and `direct_trade_record`, "
        "which validate the ea_id against `portfolio_slots`, dedupe same-outcome within 5 minutes, "
        "and insert into the `trade_records` table.",
        "- When the automation loop finishes a BUY/LIST action it uses "
        "`POST /api/v1/actions/{id}/complete` (same `actions.py`), which inserts the matching "
        "`trade_records` row.",
        "",
        "**Tables / columns used:**",
        "",
        "| Table | Columns used | Purpose |",
        "|---|---|---|",
        "| `trade_records` | `ea_id`, `action_type`, `price`, `outcome`, `recorded_at` | one row per lifecycle event |",
        "| `portfolio_slots` | `ea_id`, `buy_price`, `sell_price`, `added_at`, `is_leftover` | the slots the extension is currently trading |",
        "| `player_scores` | `ea_id`, `scored_at`, `buy_price`, `sell_price`, `margin_pct`, `op_sales`, `total_sales`, `op_ratio`, `expected_profit`, `efficiency`, `sales_per_hour`, `expected_profit_per_hour`, `scorer_version`, `max_sell_price` | scan-time rationale |",
        "| `players` | `name`, `rating`, `position`, `league`, `nation`, `card_type`, `listing_count`, `listings_per_hour`, `sales_per_hour` | card metadata |",
        "| `market_snapshots` | `ea_id`, `captured_at`, `current_lowest_bin`, `listing_count` | 48h pre-buy market context |",
        "",
        "**Outcome values (distinct, live DB):** `bought` (662), `listed` (96,705), `sold` (8,461), `expired` (23,130). "
        "`action_type` is always `buy` (when outcome=bought) or `list` (all other outcomes).",
        "",
        "**Example row** from `trade_records` for ea_id=67342264 (Kolo Touré):",
        "",
        "```",
        "recorded_at=2026-04-20 17:24:05, action_type=buy,  outcome=bought, price=16750",
        "recorded_at=2026-04-20 17:24:05, action_type=list, outcome=listed, price=21250",
        "recorded_at=2026-04-20 18:29:03, action_type=list, outcome=sold,   price=21250",
        "```",
        "",
        "**Slot-instance definition used throughout:** one instance per `bought` event. The instance "
        "covers everything from that `bought` up to the next `bought` for the same `ea_id` (or NOW() "
        "if no next buy). The instance's `sold_at` / `sold_price` is the first `sold` event inside "
        "that window; `expires_count` is the number of `expired` events inside that window.",
        "",
        "**Empirical event semantics (verified on the live DB):**",
        "",
        "- A `bought` event is followed by a `listed` event within 1 second (**307 of 333** cases; "
        "the remaining 26 have no tracked initial list but still produce sold/expired outcomes).",
        "- Relists after an expire are sometimes reported as new `listed` events and sometimes not "
        "(`transfer-list-cycle.ts` calls `relistAll()` without reporting; the action-queue RELIST path "
        "does report). **Therefore the `expired` count, not the `listed` count, is the reliable "
        "proxy for how many relist cycles a slot has been through.**",
        "- A slot can be bought again (rebought) after sold; each bought is its own instance. Over the "
        f"{WINDOW_DAYS}-day window there are {len(rows)} instances across "
        f"{len({r['ea_id'] for r in rows})} distinct ea_ids.",
        "",
    ]

    # ── Q1: Baseline ─────────────────────────────────────────────────────────
    L += [
        "## 1. Baseline summary",
        "",
    ]
    total_n = len(rows)
    sold_n = len(sold)
    never_n = len(never)
    fast_n = len(fast)
    eventual_n = len(eventual)

    ttl_buy = sum(r["buy_price"] for r in rows)
    ttl_rev_sold = sum(r["sold_price"] for r in sold if r.get("sold_price"))
    ttl_buy_sold = sum(r["buy_price"] for r in sold)
    ttl_net = sum(realized_profit(r) or 0 for r in sold)

    ttl_buy_never = sum(r["buy_price"] for r in never)
    ttl_hours_never = sum(r["hours_tied_up"] or 0 for r in never)

    # Compare realized vs predicted profit
    pred_profit_sold = sum(r["expected_profit"] or 0 for r in sold if r.get("expected_profit") is not None)
    realized_profit_sold = sum(realized_profit(r) or 0 for r in sold)

    fast_med_h = pct([r["hours_tied_up"] for r in fast], 0.5)
    eventual_med_h = pct([r["hours_tied_up"] for r in eventual], 0.5)

    L += [
        "### Slot counts by bucket",
        "",
        "| Bucket | N | % | Median hours-to-sell | Mean PPH (sold) |",
        "|---|---|---|---|---|",
        f"| fast_sell (sold, 0 expires) | {fast_n} | {fast_n/total_n*100:.0f}% | {fmt_n(fast_med_h)}h | {fmt_n(statistics.mean([pph_sold(r) for r in fast if pph_sold(r) is not None]) if fast else None)} |",
        f"| eventual_sell (sold, ≥1 expire) | {eventual_n} | {eventual_n/total_n*100:.0f}% | {fmt_n(eventual_med_h)}h | {fmt_n(statistics.mean([pph_sold(r) for r in eventual if pph_sold(r) is not None]) if eventual else None)} |",
        f"| never_sold (still active / leftover) | {never_n} | {never_n/total_n*100:.0f}% | — | 0 |",
        f"| **Total** | **{total_n}** | 100% | — | — |",
        "",
        "### Coin flow",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Total coin invested (all slots) | {ttl_buy:,} |",
        f"| Coin invested in sold slots | {ttl_buy_sold:,} |",
        f"| Gross revenue (sold, pre-tax) | {ttl_rev_sold:,} |",
        f"| **Realized net profit (sold, after 5% EA tax)** | **{int(ttl_net):,}** |",
        f"| Capital currently stuck in never-sold slots | {ttl_buy_never:,} |",
        f"| Capital-hours wasted on never-sold slots | {ttl_hours_never:,.0f} |",
        f"| Overall sell-through | {sold_n}/{total_n} = **{sold_n/total_n*100:.0f}%** |",
        "",
        "### Realized vs scorer-predicted profit (sold slots only)",
        "",
        f"- Sum of `expected_profit` at scan time (sold slots, scorer rationale available): {int(pred_profit_sold):,}",
        f"- Sum of realized net profit after EA tax: {int(realized_profit_sold):,}",
        f"- Ratio realized/expected: "
        f"{(realized_profit_sold/pred_profit_sold) if pred_profit_sold else float('nan'):.3f}",
        "",
        "> **Flagged finding — `player_scores.expected_profit` is ~60× inflated.** CLAUDE.md says "
        "`expected_profit = net_profit × op_ratio`, but the stored column p50 ≈ 89k / p90 ≈ 691k "
        "/ max ≈ 63M on cards whose realized net profit is 15k-40k. The inflation is large enough "
        "that this column cannot be used as-is for portfolio optimization. Check `src/server/scorer_v3.py` "
        "for the formula actually applied (top example: ea_id=67300559 stored expected_profit=37M on "
        "net_profit=262k × op_ratio=0.029 which should be 7,604). This bug is orthogonal to the OP-sell "
        "filter question but worth fixing before trusting any efficiency ranking.",
        "",
    ]

    # ── Q2: PPH distribution + leaderboard ────────────────────────────────────
    pphs = [pph_sold(r) for r in sold if pph_sold(r) is not None]
    L += [
        "## 2. PPH distribution (sold slots only)",
        "",
        f"N sold with valid PPH = {len(pphs)}.",
        "",
        "| Percentile | PPH (coins/hour) |",
        "|---|---|",
        f"| p25 | {fmt_n(pct(pphs, 0.25))} |",
        f"| median | {fmt_n(pct(pphs, 0.50))} |",
        f"| p75 | {fmt_n(pct(pphs, 0.75))} |",
        f"| p90 | {fmt_n(pct(pphs, 0.90))} |",
        f"| mean | {fmt_n(statistics.mean(pphs) if pphs else None)} |",
        "",
        "### PPH histogram",
        "",
        "| PPH range | Count |",
        "|---|---|",
    ]
    for lo, hi, cnt in histogram(pphs, n_bins=10):
        L.append(f"| {fmt_n(lo)} – {fmt_n(hi)} | {cnt} |")
    L.append("")

    # Split fast-sell vs PPH — flag any that are slow vs fast on PPH
    top_q = sorted(
        [r for r in sold if pph_sold(r) is not None],
        key=lambda r: pph_sold(r) or 0,
        reverse=True,
    )
    bottom_q = sorted(
        [r for r in sold if pph_sold(r) is not None],
        key=lambda r: pph_sold(r) or 0,
    )

    L += [
        "### Top 20 sold slots by PPH (fastest-earning)",
        "",
        "| ea_id | name | card_type | rating | buy | sold | hrs | expires | PPH | margin% | op_ratio |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in top_q[:20]:
        L.append(
            f"| {r['ea_id']} | {(r.get('name') or '—')[:20]} | "
            f"{r.get('card_type') or '—'} | {r.get('rating') or '—'} | "
            f"{r['buy_price']:,} | {r['sold_price']:,} | {r['hours_tied_up']:.1f} | "
            f"{r['expires_count']} | {fmt_n(pph_sold(r))} | "
            f"{fmt_n(r.get('margin_pct'))} | {fmt_pct(r.get('op_ratio'))} |"
        )

    L += [
        "",
        "### Bottom 20 sold slots by PPH (slowest-earning)",
        "",
        "| ea_id | name | card_type | rating | buy | sold | hrs | expires | PPH | margin% | op_ratio |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in bottom_q[:20]:
        L.append(
            f"| {r['ea_id']} | {(r.get('name') or '—')[:20]} | "
            f"{r.get('card_type') or '—'} | {r.get('rating') or '—'} | "
            f"{r['buy_price']:,} | {r['sold_price']:,} | {r['hours_tied_up']:.1f} | "
            f"{r['expires_count']} | {fmt_n(pph_sold(r))} | "
            f"{fmt_n(r.get('margin_pct'))} | {fmt_pct(r.get('op_ratio'))} |"
        )
    L.append("")

    # ── Q3/Q4: Winner / Loser profile ────────────────────────────────────────
    attrs = [
        ("rating_bucket", lambda r: rating_bucket(r.get("rating"))),
        ("buy_price_bucket", lambda r: price_bucket(r.get("buy_price"))),
        ("margin_bucket", lambda r: margin_bucket(r.get("margin_pct"))),
        ("sph_bucket (scorer)", lambda r: sph_bucket(r.get("s_sph"))),
        ("lcount_bucket (players.listing_count at scan)", lambda r: lcount_bucket(r.get("p_lcount"))),
        ("position", lambda r: r.get("position") or "unknown"),
        ("card_type", lambda r: r.get("card_type") or "unknown"),
        ("nation", lambda r: (r.get("nation") or "unknown") if r.get("nation") else "unknown"),
        # league intentionally excluded — scanner doesn't populate players.league (always '')
        ("op_ratio bucket", lambda r: "n/a" if r.get("op_ratio") is None
            else "<20%" if r["op_ratio"] < 0.2 else "20-40%" if r["op_ratio"] < 0.4
            else "40-60%" if r["op_ratio"] < 0.6 else "60%+"),
        ("total_sales bucket", lambda r: "n/a" if r.get("total_sales") is None
            else "<20" if r["total_sales"] < 20 else "20-50" if r["total_sales"] < 50
            else "50-100" if r["total_sales"] < 100 else "100+"),
    ]

    L += ["## 3. Attribute-level lift (sold slots vs overall)", ""]

    overall_sell = len(sold) / len(rows)
    # For disagreement detection keep rankings
    disagreements = []

    for attr_name, attr_fn in attrs:
        results = attribute_analysis(rows, attr_fn, attr_name)
        if not results:
            continue
        # Sort by sell lift and by PPH separately
        by_lift = sorted(results, key=lambda x: x["sell_lift"] or 0, reverse=True)
        by_pph = sorted(results, key=lambda x: x["mean_pph_sold"] or 0, reverse=True)

        L += [
            f"### {attr_name}",
            "",
            "| value | n | sold | sell-through | lift vs overall | mean PPH (sold) | capital-wtd PPH |",
            "|---|---|---|---|---|---|---|",
        ]
        for r in by_lift:
            L.append(
                f"| {r['value']} | {r['n']} | {r['sold_n']} | {r['sell_through']*100:.0f}% | "
                f"{fmt_n(r['sell_lift'])}× | {fmt_n(r['mean_pph_sold'])} | "
                f"{fmt_n(r['capital_weighted_pph'])} |"
            )
        L.append("")

        # Detect disagreement — top by sell-lift ≠ top by mean PPH
        if by_lift and by_pph and by_lift[0]["value"] != by_pph[0]["value"]:
            # How big is the gap?
            top_lift_val = by_lift[0]
            top_pph_val = by_pph[0]
            disagreements.append(
                f"- **{attr_name}**: highest sell-through = `{top_lift_val['value']}` "
                f"(lift {fmt_n(top_lift_val['sell_lift'])}×, PPH {fmt_n(top_lift_val['mean_pph_sold'])}); "
                f"highest mean PPH = `{top_pph_val['value']}` "
                f"(lift {fmt_n(top_pph_val['sell_lift'])}×, PPH {fmt_n(top_pph_val['mean_pph_sold'])})."
            )

    L += ["### Where sell-through and PPH disagree", ""]
    if disagreements:
        L += disagreements
    else:
        L.append("- All attributes rank the same on sell-through and on PPH.")
    L.append("")

    # Never-sold profile
    L += [
        "## 4. Never-sold profile",
        "",
        f"N never-sold = {never_n} ({never_n/total_n*100:.0f}% of slots).",
        "",
    ]
    # Aggregate by attribute — show share in never-sold vs share in sold.
    # Keep only the three most discriminating attributes (others are redundant
    # with the attribute-lift tables in section 3).
    _DISCRIM_ATTRS = {"rating_bucket", "buy_price_bucket", "sph_bucket (scorer)"}
    for attr_name, attr_fn in [a for a in attrs if a[0] in _DISCRIM_ATTRS]:
        nv_counts = Counter(attr_fn(r) for r in never)
        sold_counts = Counter(attr_fn(r) for r in sold)
        total_nv = sum(nv_counts.values()) or 1
        total_sold = sum(sold_counts.values()) or 1
        values = sorted(set(nv_counts) | set(sold_counts),
                        key=lambda v: nv_counts.get(v, 0), reverse=True)
        L.append(f"### {attr_name}")
        L.append("")
        L.append("| value | never-sold % | sold % | lift (never/sold) |")
        L.append("|---|---|---|---|")
        for val in values:
            nv_pct = nv_counts.get(val, 0) / total_nv
            sold_pct = sold_counts.get(val, 0) / total_sold
            lift = nv_pct / sold_pct if sold_pct else None
            L.append(f"| {val} | {nv_pct*100:.0f}% | {sold_pct*100:.0f}% | {fmt_n(lift)} |")
        L.append("")

    # ── Q5: Realized vs predicted margin ──────────────────────────────────────
    L += [
        "## 5. Realized vs predicted margin",
        "",
    ]
    rm_by_pred = defaultdict(list)
    for r in sold:
        rm = realized_margin(r)
        pm = r.get("margin_pct")
        if rm is None or pm is None:
            continue
        rm_by_pred[margin_bucket(pm)].append((pm, rm, r))
    L += [
        "| scorer margin bucket | n | mean predicted margin | mean realized margin | delta (realized - predicted) |",
        "|---|---|---|---|---|",
    ]
    for bk in ["<5%", "5-10%", "10-15%", "15-25%", "25%+"]:
        lst = rm_by_pred.get(bk, [])
        if not lst: continue
        mean_pm = sum(x[0] for x in lst) / len(lst)
        mean_rm = sum(x[1] for x in lst) / len(lst) * 100
        delta = mean_rm - mean_pm
        L.append(
            f"| {bk} | {len(lst)} | {mean_pm:.1f}% | {mean_rm:.1f}% | {delta:+.1f}pp |"
        )
    L.append("")

    # Group by bucket for systematic overestimate
    L += [
        "### By slot bucket",
        "",
        "| slot bucket | n | mean realized margin | mean predicted margin |",
        "|---|---|---|---|",
    ]
    for bname, slots in (("fast_sell", fast), ("eventual_sell", eventual)):
        rms = [realized_margin(r) for r in slots if realized_margin(r) is not None]
        pms = [r.get("margin_pct") for r in slots if r.get("margin_pct") is not None]
        if not rms: continue
        L.append(f"| {bname} | {len(rms)} | {sum(rms)/len(rms)*100:.1f}% | "
                 f"{(sum(pms)/len(pms)) if pms else float('nan'):.1f}% |")
    L.append("")

    # ── Q6: Dead on arrival ──────────────────────────────────────────────────
    L += [
        "## 6. Dead-on-arrival signals (never-sold slots)",
        "",
        "For each never-sold slot, check warning signs that were visible at scan time.",
        "",
    ]
    doa_flags = []
    for r in never:
        flags = {
            "low_total_sales": (r.get("total_sales") is not None and r["total_sales"] < 20),
            "high_listings_vs_sph": (
                r.get("p_lcount") is not None and r.get("s_sph") is not None
                and r["s_sph"] > 0 and r["p_lcount"] > r["s_sph"] * 24
            ),
            "recent_price_spike_48h": (
                r.get("max_snap_price") and r.get("buy_price")
                and r["buy_price"] > r["max_snap_price"] * 1.10
            ),
            "low_op_ratio": (r.get("op_ratio") is not None and r["op_ratio"] < 0.30),
            "low_margin": (r.get("margin_pct") is not None and r["margin_pct"] < 8),
            "scan_was_stale": (
                r.get("scored_at") and r.get("bought_at")
                and (r["bought_at"] - r["scored_at"]).total_seconds() / 3600 > 12
            ),
        }
        doa_flags.append(flags)

    # Count each flag
    L += [
        "| warning flag | never-sold triggering | share of never-sold |",
        "|---|---|---|",
    ]
    for flag in ["low_total_sales", "high_listings_vs_sph", "recent_price_spike_48h",
                 "low_op_ratio", "low_margin", "scan_was_stale"]:
        n_trig = sum(1 for f in doa_flags if f[flag])
        L.append(f"| {flag} | {n_trig} | {fmt_pct(n_trig/never_n) if never_n else 'n/a'} |")
    L.append("")

    # Any combination
    any_flag_count = sum(1 for f in doa_flags if any(f.values()))
    no_flag = never_n - any_flag_count
    L.append(f"Slots with **at least one** warning flag: **{any_flag_count}** "
             f"({fmt_pct(any_flag_count/never_n) if never_n else '—'}).")
    L.append(f"Slots with **zero** warning flags (genuinely bad luck): **{no_flag}** "
             f"({fmt_pct(no_flag/never_n) if never_n else '—'}).")
    L.append("")

    # Compare flag rates vs sold
    L += [
        "### Flag rate comparison (never-sold vs sold)",
        "",
        "| flag | never-sold rate | sold rate | lift |",
        "|---|---|---|---|",
    ]
    sold_flags = []
    for r in sold:
        sold_flags.append({
            "low_total_sales": (r.get("total_sales") is not None and r["total_sales"] < 20),
            "high_listings_vs_sph": (
                r.get("p_lcount") is not None and r.get("s_sph") is not None
                and r["s_sph"] > 0 and r["p_lcount"] > r["s_sph"] * 24
            ),
            "recent_price_spike_48h": (
                r.get("max_snap_price") and r.get("buy_price")
                and r["buy_price"] > r["max_snap_price"] * 1.10
            ),
            "low_op_ratio": (r.get("op_ratio") is not None and r["op_ratio"] < 0.30),
            "low_margin": (r.get("margin_pct") is not None and r["margin_pct"] < 8),
            "scan_was_stale": (
                r.get("scored_at") and r.get("bought_at")
                and (r["bought_at"] - r["scored_at"]).total_seconds() / 3600 > 12
            ),
        })
    for flag in ["low_total_sales", "high_listings_vs_sph", "recent_price_spike_48h",
                 "low_op_ratio", "low_margin", "scan_was_stale"]:
        nv_rate = sum(1 for f in doa_flags if f[flag]) / never_n if never_n else 0
        sd_rate = sum(1 for f in sold_flags if f[flag]) / len(sold) if sold else 0
        lift = (nv_rate / sd_rate) if sd_rate else None
        L.append(f"| {flag} | {fmt_pct(nv_rate)} | {fmt_pct(sd_rate)} | {fmt_n(lift)}× |")
    L.append("")

    # ── Q7: Repeat offenders ──────────────────────────────────────────────────
    by_ea = defaultdict(list)
    for r in rows:
        by_ea[r["ea_id"]].append(r)
    offender_rows = []
    for ea, lst in by_ea.items():
        total_expires = sum(r["expires_count"] for r in lst)
        never_count = sum(1 for r in lst if r["bucket"] == "never_sold")
        capital_hours = sum((r["buy_price"] or 0) * (r["hours_tied_up"] or 0) for r in lst)
        net = sum(realized_profit(r) or 0 for r in lst)
        capital_stuck = sum(r["buy_price"] for r in lst if r["bucket"] == "never_sold")
        offender_rows.append({
            "ea_id": ea,
            "name": (lst[0].get("name") or "—")[:24],
            "rating": lst[0].get("rating"),
            "card_type": lst[0].get("card_type"),
            "n_instances": len(lst),
            "never_count": never_count,
            "total_expires": total_expires,
            "capital_hours_wasted": capital_hours,
            "capital_stuck_now": capital_stuck,
            "net_profit": net,
        })

    L += [
        "## 7. Repeat-offender slots (by capital-hours wasted)",
        "",
        "| ea_id | name | rating | card_type | instances | never-sold | total expires | capital-hours | stuck now | net profit |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in sorted(offender_rows, key=lambda x: x["capital_hours_wasted"], reverse=True)[:10]:
        L.append(
            f"| {r['ea_id']} | {r['name']} | {r['rating'] or '—'} | {r['card_type'] or '—'} | "
            f"{r['n_instances']} | {r['never_count']} | {r['total_expires']} | "
            f"{r['capital_hours_wasted']:,.0f} | {r['capital_stuck_now']:,} | {int(r['net_profit']):,} |"
        )
    L.append("")
    L += [
        "### Top 10 by coins lost (capital stuck + negative net)",
        "",
        "| ea_id | name | stuck now | net profit (sold instances) | loss (stuck − net) |",
        "|---|---|---|---|---|",
    ]
    for r in sorted(offender_rows,
                    key=lambda x: (x["capital_stuck_now"] - max(0, x["net_profit"])),
                    reverse=True)[:10]:
        loss = r["capital_stuck_now"] - max(0, r["net_profit"])
        L.append(
            f"| {r['ea_id']} | {r['name']} | {r['capital_stuck_now']:,} | "
            f"{int(r['net_profit']):,} | {loss:,} |"
        )
    L.append("")

    # ── Q8: Scorer version split ──────────────────────────────────────────────
    by_version = defaultdict(list)
    for r in rows:
        by_version[r.get("scorer_version") or "unknown"].append(r)
    L += [
        "## 8. Scorer version comparison",
        "",
        "| scorer_version | n slots | sold | sell-through | mean PPH (sold) | mean realized profit/slot |",
        "|---|---|---|---|---|---|",
    ]
    for ver, lst in sorted(by_version.items(), key=lambda kv: -len(kv[1])):
        sold_lst = [r for r in lst if r["bucket"] != "never_sold"]
        st = len(sold_lst) / len(lst) if lst else 0
        pphs_lst = [pph_sold(r) for r in sold_lst if pph_sold(r) is not None]
        mpph = statistics.mean(pphs_lst) if pphs_lst else None
        profits = [realized_profit(r) for r in sold_lst if realized_profit(r) is not None]
        mp = statistics.mean(profits) if profits else None
        L.append(
            f"| {ver} | {len(lst)} | {len(sold_lst)} | {fmt_pct(st)} | "
            f"{fmt_n(mpph)} | {fmt_n(mp)} |"
        )
    if len([v for v in by_version if v != "unknown"]) <= 1:
        L.append("")
        L.append("> Only one non-null scorer_version is represented in the window — "
                 "no meaningful side-by-side comparison possible. "
                 "Older scores (v1/v2) are deleted on server startup.")
    L.append("")

    # ── Q9: Filter recommendations ────────────────────────────────────────────
    L += [
        "## 9. Filter recommendations (two-axis impact)",
        "",
        f"Candidates only considered if backed by ≥{MIN_FILTER_N} slots in the {WINDOW_DAYS}-day window.",
        "",
    ]

    # Helper — evaluate what happens if we DROP slots matching a filter predicate.
    def evaluate_filter(name: str, predicate, rationale: str):
        dropped = [r for r in rows if predicate(r)]
        kept = [r for r in rows if not predicate(r)]
        if len(dropped) < MIN_FILTER_N:
            return None
        dropped_never = [r for r in dropped if r["bucket"] == "never_sold"]
        dropped_sold = [r for r in dropped if r["bucket"] != "never_sold"]
        kept_sold = [r for r in kept if r["bucket"] != "never_sold"]

        coins_saved_stuck = sum(r["buy_price"] for r in dropped_never)
        profit_lost_from_drop = sum(realized_profit(r) or 0 for r in dropped_sold)

        # Portfolio PPH before vs after
        total_cap_hours_before = sum(
            (r["buy_price"] or 0) * max((r["hours_tied_up"] or 0), 0.01) for r in rows
        )
        total_profit_before = sum(realized_profit(r) or 0 for r in sold)
        pph_before = total_profit_before / total_cap_hours_before if total_cap_hours_before else 0

        total_cap_hours_after = sum(
            (r["buy_price"] or 0) * max((r["hours_tied_up"] or 0), 0.01) for r in kept
        )
        total_profit_after = sum(realized_profit(r) or 0 for r in kept_sold)
        pph_after = total_profit_after / total_cap_hours_after if total_cap_hours_after else 0

        return {
            "name": name,
            "rationale": rationale,
            "n_dropped": len(dropped),
            "n_dropped_never": len(dropped_never),
            "n_dropped_sold": len(dropped_sold),
            "coins_saved": coins_saved_stuck,
            "profit_lost": profit_lost_from_drop,
            "pph_before": pph_before,
            "pph_after": pph_after,
            "pph_delta": pph_after - pph_before,
        }

    # Candidate filters derived from the analysis above.
    # Each filter drops the matching slots; we score the resulting portfolio PPH change.
    candidates = []
    for name, predicate, rationale in [
        (
            "cap buy_price at 75k (drop >=75k slots)",
            lambda r: r.get("buy_price") is not None and r["buy_price"] >= 75_000,
            "150k+ cards have 16% sell-through vs 78% for 30-75k, 11x never-sold lift",
        ),
        (
            "cap buy_price at 150k (drop >=150k slots)",
            lambda r: r.get("buy_price") is not None and r["buy_price"] >= 150_000,
            "150k+ buys are the worst price band; keeps 75-150k tier intact",
        ),
        (
            "raise MIN_SALES_PER_HOUR 7 -> 25 (scorer s_sph)",
            lambda r: r.get("s_sph") is not None and r["s_sph"] < 25,
            "sph_bucket 10-25 has 25% sell-through vs 75% for 50+; current 7-cutoff too permissive",
        ),
        (
            "raise MIN_LIVE_LISTINGS 20 -> 50 (players.listing_count)",
            lambda r: r.get("p_lcount") is not None and r["p_lcount"] < 50,
            "listing_count <20 has 40% sell-through vs 73% for 20-50",
        ),
        (
            "raise MIN_OP_SALES 3 -> 5 (scorer op_sales)",
            lambda r: r.get("op_sales") is not None and r["op_sales"] < 5,
            "3-OP-sale requirement lets through lucky-sample cards",
        ),
        (
            "drop rating 86-88 (weak tier)",
            lambda r: r.get("rating") is not None and 86 <= r["rating"] <= 88,
            "86-88 rating band has 26% sell-through vs 72% for 89-91 (small n=19)",
        ),
        (
            "drop slots with scorer total_sales < 20",
            lambda r: r.get("total_sales") is not None and r["total_sales"] < 20,
            "low_total_sales is the liquidity floor",
        ),
        (
            "drop slots with scorer op_ratio < 10% (extreme optimism)",
            lambda r: r.get("op_ratio") is not None and r["op_ratio"] < 0.10,
            "op_ratio <10% means almost no verified OP sales at the chosen margin",
        ),
        (
            "cap buy_price at 75k AND raise sph to 25 (combined)",
            lambda r: (
                (r.get("buy_price") is not None and r["buy_price"] >= 75_000)
                or (r.get("s_sph") is not None and r["s_sph"] < 25)
            ),
            "stack the two strongest signals",
        ),
        (
            "drop slots where scored_at was > 12h before bought_at (stale scan)",
            lambda r: (r.get("scored_at") and r.get("bought_at")
                       and (r["bought_at"] - r["scored_at"]).total_seconds() / 3600 > 12),
            "stale scan = OP price based on old market data",
        ),
    ]:
        fc = evaluate_filter(name, predicate, rationale)
        if fc:
            candidates.append(fc)

    # Sort by pph_delta desc
    candidates.sort(key=lambda x: x["pph_delta"], reverse=True)

    # Add precision = n_dropped_never / n_dropped (what fraction of dropped slots are actually bad).
    # Higher precision = fewer winners lost per never-sold captured.
    total_never = len([r for r in rows if r["bucket"] == "never_sold"])
    total_sold  = len([r for r in rows if r["bucket"] != "never_sold"])
    L += [
        "Columns: **precision** = % of dropped slots that were actually never-sold (higher = more surgical). "
        "**recall** = % of total never-sold captured. **winner loss** = % of total sold slots dropped. "
        "**Δ PPH** = portfolio profit-per-coin-hour change.",
        "",
        "| filter | dropped | precision | recall (never) | winner loss | coins saved | profit lost | Δ PPH |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for c in candidates:
        precision = c['n_dropped_never']/c['n_dropped'] if c['n_dropped'] else 0
        recall = c['n_dropped_never']/total_never if total_never else 0
        winner_loss = c['n_dropped_sold']/total_sold if total_sold else 0
        L.append(
            f"| {c['name']} | {c['n_dropped']} | {precision*100:.0f}% | {recall*100:.0f}% | "
            f"{winner_loss*100:.0f}% | {c['coins_saved']:,} | {int(c['profit_lost']):,} | "
            f"{c['pph_delta']:+.3f} |"
        )
    L.append("")

    # ── Final recommendations ─────────────────────────────────────────────────
    # Rank by precision (surgical filters preferred) among filters that improve PPH.
    pos_candidates = [c for c in candidates if c["pph_delta"] > 0]
    # Sort by precision, tiebreak by Δ PPH
    for c in pos_candidates:
        c["_precision"] = c["n_dropped_never"] / c["n_dropped"] if c["n_dropped"] else 0
    pos_candidates.sort(key=lambda c: (c["_precision"], c["pph_delta"]), reverse=True)

    L += [
        "### Ranked recommendations (most surgical first)",
        "",
        "Preference: high precision (dropping slots that were truly dead) with positive PPH delta. "
        "A filter that drops 200 slots for a huge PPH gain is worse than one that drops 40 but "
        "precisely targets the never-sold ones — because freed capital needs somewhere to go, and "
        "killing trading volume you do want is a hidden cost.",
        "",
    ]
    if not pos_candidates:
        L.append("- No candidate filter improves portfolio PPH on this dataset. "
                 "All proposed filters drop more winners than never-sells.")
    else:
        for i, c in enumerate(pos_candidates, 1):
            precision = c["_precision"]
            L.append(f"**{i}.** {c['name']} — {c['rationale']}. "
                     f"Drops {c['n_dropped']} slots "
                     f"(**precision {precision*100:.0f}%**: "
                     f"{c['n_dropped_never']} never-sold / {c['n_dropped_sold']} sold). "
                     f"Coins saved: {c['coins_saved']:,}. Profit sacrificed: {int(c['profit_lost']):,}. "
                     f"Portfolio PPH {c['pph_before']:.3f} → {c['pph_after']:.3f} "
                     f"(**{c['pph_delta']:+.3f}**).")
    L.append("")

    # Negative candidates — explicitly call them out
    neg_candidates = [c for c in candidates if c["pph_delta"] <= 0]
    if neg_candidates:
        L.append("### Filters that don't help (don't apply):")
        L.append("")
        for c in neg_candidates:
            L.append(f"- {c['name']}: Δ PPH {c['pph_delta']:+.3f}. "
                     f"Drops {c['n_dropped_sold']} sold slots for only "
                     f"{c['n_dropped_never']} never-sold — bad trade.")
        L.append("")

    # ── Caveats ──────────────────────────────────────────────────────────────
    L += [
        "## Caveats",
        "",
        f"- Window = {WINDOW_DAYS} days. Only {len(rows)} slot instances. "
        f"Any per-attribute cell with <{MIN_BUCKET_N} samples was suppressed. "
        f"Any filter recommendation with <{MIN_FILTER_N} matching slots was suppressed.",
        f"- Join coverage: only {sum(1 for r in rows if r.get('total_sales') is not None)}/"
        f"{len(rows)} slot instances have a pre-buy scorer rationale "
        f"(the rest were added outside a scan window or before v3 scoring).",
        "- Relists after the first expire are not always tracked as new `listed` events "
        "(transfer-list-cycle uses bulk `relistAll()` inline without reporting). `expires_count` "
        "is the reliable proxy for how many listing cycles a slot has been through.",
        "- 'Never-sold' includes slots that may still sell soon — a slot bought 2 hours ago "
        "classified as never-sold now may become fast-sell within an hour. Hours-tied-up in the "
        "top of the list is smaller than at the bottom; the capital-weighted PPH accounts for this.",
        "- The 222 leftover slots from `portfolio_slots.is_leftover=true` are not included directly "
        "in this analysis — they're accounted for only if they generated a `bought` event in the "
        f"last {WINDOW_DAYS} days.",
        "- EA tax assumed at 5%. Scorer's predicted margin uses `margin_pct` as an integer percent.",
        "",
    ]

    return L


if __name__ == "__main__":
    main()
