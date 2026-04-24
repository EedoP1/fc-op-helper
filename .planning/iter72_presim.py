"""Iter 72 pre-simulation: re-simulate v1's 45 entries with no stop, hold to N hours.

For each (ea_id, buy_time, qty, buy_price) from v1 trades:
  - Load market_snapshots for that ea_id from buy_time .. buy_time + max_hold_h
  - Bucket by hour. For each hour, compute MIN(current_lowest_bin) (sell-side
    floor — what we'd actually receive at OP@market exit, conservative).
  - Walk forward hour by hour. Profit-target trigger: hour median >= buy_price * 1.20 / 0.95
    i.e. net (after 5% tax) >= +20%. Use median as decision price (smoothed
    realized exit). On trigger SELL at hour median (loader pessimism baked in).
  - If max_hold reached, SELL at last hour median.
  - net_profit = (sell * 0.95 - buy) * qty
"""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import median

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import DATABASE_URL


PROFIT_TARGET = 0.20  # net (after tax) target


async def load_hourly_min_med(eng, ea_ids: list[int], start: datetime, end: datetime):
    """Returns {ea_id: {hour_dt: (min_price, median_price)}}."""
    out: dict[int, dict[datetime, tuple[int, int]]] = defaultdict(dict)
    if not ea_ids:
        return out
    async with eng.connect() as c:
        # Use min and a percentile_cont(0.5) approximation via array_agg
        rows = await c.execute(
            text(
                "SELECT ea_id, date_trunc('hour', captured_at) AS h, "
                "       MIN(current_lowest_bin) AS p_min, "
                "       (percentile_cont(0.5) WITHIN GROUP (ORDER BY current_lowest_bin))::int AS p_med "
                "FROM market_snapshots "
                "WHERE ea_id = ANY(:ids) "
                "  AND current_lowest_bin > 0 "
                "  AND captured_at >= :s "
                "  AND captured_at <= :e "
                "GROUP BY ea_id, h "
                "ORDER BY ea_id, h"
            ),
            {"ids": list(ea_ids), "s": start, "e": end},
        )
        for ea, h, p_min, p_med in rows.fetchall():
            out[int(ea)][h.replace(tzinfo=None)] = (int(p_min), int(p_med))
    return out


def simulate_trade(
    qty: int,
    buy_price: int,
    buy_ts: datetime,
    hours: list[tuple[datetime, int, int]],  # sorted by ts asc, (h, p_min, p_med)
    max_hold_h: int,
    use_min_for_pt: bool = False,
):
    """Return (sell_price, hours_held, hit_pt)."""
    target_gross = buy_price * (1 + PROFIT_TARGET) / 0.95  # need this gross to net +20%
    last_med = buy_price
    end_ts = buy_ts + timedelta(hours=max_hold_h)
    for h, p_min, p_med in hours:
        if h <= buy_ts:
            continue
        if h > end_ts:
            break
        last_med = p_med
        decision_price = p_min if use_min_for_pt else p_med
        if decision_price >= target_gross:
            return (decision_price, int((h - buy_ts).total_seconds() / 3600), True)
    # Max hold — sell at last min (pessimistic exit)
    if hours:
        # find last hour <= end_ts
        last = None
        for h, p_min, p_med in hours:
            if h > buy_ts and h <= end_ts:
                last = (h, p_min, p_med)
        if last:
            return (last[1], int((last[0] - buy_ts).total_seconds() / 3600), False)
    return (buy_price, 0, False)


async def run():
    with open("daily_trend_dip_v1_filtered_results.json") as f:
        d = json.load(f)
    trades = d[0]["trades"]
    entries = []
    for t in trades:
        bt = datetime.fromisoformat(t["buy_time"].replace("Z", "+00:00")).replace(tzinfo=None)
        entries.append({
            "ea_id": t["ea_id"],
            "qty": t["qty"],
            "buy_price": t["buy_price"],
            "buy_ts": bt,
            "v1_net": t["net_profit"],
        })
    print(f"Loaded {len(entries)} v1 entries")
    print(f"v1 total net: {sum(e['v1_net'] for e in entries):,}")

    eng = create_async_engine(DATABASE_URL, pool_size=2)
    ea_ids = sorted({e["ea_id"] for e in entries})
    min_buy = min(e["buy_ts"] for e in entries)
    max_buy = max(e["buy_ts"] for e in entries)
    end_window = max_buy + timedelta(hours=200)
    print(f"Loading snapshots {min_buy} .. {end_window} for {len(ea_ids)} ea_ids")
    snap = await load_hourly_min_med(eng, ea_ids, min_buy, end_window)
    await eng.dispose()
    print(f"Loaded snapshots for {len(snap)} ea_ids")

    for max_hold in (96, 144):
        for label, use_min in (("med-decision", False), ("min-decision (very conservative)", True)):
            total = 0
            wins = 0
            pt_hits = 0
            hold_hours = []
            net_per = []
            for e in entries:
                hours_data = sorted(snap.get(e["ea_id"], {}).items())
                hours = [(h, mn, md) for h, (mn, md) in hours_data]
                sell, held, hit = simulate_trade(
                    e["qty"], e["buy_price"], e["buy_ts"], hours, max_hold, use_min
                )
                # Net: sell * 0.95 - buy, * qty
                net = (sell * 0.95 - e["buy_price"]) * e["qty"]
                total += net
                if net > 0:
                    wins += 1
                if hit:
                    pt_hits += 1
                hold_hours.append(held)
                net_per.append(net)
            print(f"\n--- max_hold={max_hold}h, exit_price={label} ---")
            print(f"  total net: {total:,.0f}")
            print(f"  trades: {len(entries)}, wins: {wins} ({wins/len(entries):.1%})")
            print(f"  PT hits: {pt_hits} ({pt_hits/len(entries):.1%})")
            print(f"  median hold: {median(hold_hours):.0f}h, max: {max(hold_hours)}h")


if __name__ == "__main__":
    asyncio.run(run())
