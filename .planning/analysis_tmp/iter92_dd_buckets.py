"""Iter92 pre-analysis: bucket v2 trades by dd_72h_at_entry."""
from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.algo.engine import load_market_snapshot_data
from src.config import DATABASE_URL


async def main():
    with open("C:/Users/maftu/Projects/op-seller/mid_dip_v2_filtered_results.json") as f:
        d = json.load(f)
    trades = d[0]["trades"]
    print(f"Loaded {len(trades)} v2 trades")

    engine = create_async_engine(DATABASE_URL)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    price_data, _, _, _ = await load_market_snapshot_data(
        sf, min_price=15000, max_price=60000
    )
    print(f"Loaded price_data for {len(price_data)} players")

    # Build per-player {hour_ts: median_price}
    per_player_hourly: dict[int, dict[datetime, int]] = {}
    for ea_id, points in price_data.items():
        per_player_hourly[ea_id] = {ts.replace(tzinfo=None): p for ts, p in points}

    # Compute dd_72h for each trade
    enriched = []
    for t in trades:
        ea_id = t["ea_id"]
        bt = datetime.fromisoformat(t["buy_time"]).replace(tzinfo=None)
        bt_hour = bt.replace(minute=0, second=0, microsecond=0)
        hourly = per_player_hourly.get(ea_id, {})
        # smoothed price at entry: median over 3h ending at bt_hour
        recent_prices = []
        for k in range(3):
            v = hourly.get(bt_hour - timedelta(hours=k))
            if v is not None:
                recent_prices.append(v)
        if not recent_prices:
            sm = t["buy_price"]
        else:
            recent_prices.sort()
            sm = recent_prices[len(recent_prices) // 2]
        # max over 72h window ending at bt_hour
        window_vals = []
        for k in range(72):
            v = hourly.get(bt_hour - timedelta(hours=k))
            if v is not None:
                window_vals.append(v)
        if not window_vals:
            dd = 0.0
        else:
            wmax = max(window_vals)
            dd = 1.0 - sm / wmax if wmax > 0 else 0.0
        enriched.append({
            "ea_id": ea_id,
            "qty": t["qty"],
            "buy_price": t["buy_price"],
            "sell_price": t["sell_price"],
            "net_profit": t["net_profit"],
            "buy_time": t["buy_time"],
            "dd_72h_estimated": dd,
            "n_window_pts": len(window_vals),
        })

    # Bucketize
    buckets = {
        "0.20-0.25": [],
        "0.25-0.30": [],
        "0.30-0.35": [],
        "0.35-0.40": [],
        "0.40+": [],
    }
    for tr in enriched:
        dd = tr["dd_72h_estimated"]
        if dd < 0.25:
            buckets["0.20-0.25"].append(tr)
        elif dd < 0.30:
            buckets["0.25-0.30"].append(tr)
        elif dd < 0.35:
            buckets["0.30-0.35"].append(tr)
        elif dd < 0.40:
            buckets["0.35-0.40"].append(tr)
        else:
            buckets["0.40+"].append(tr)

    print("\nBucket analysis (dd_72h_estimated at entry):")
    print(f"{'bucket':<12} {'n':>4} {'pnl':>12} {'win%':>6} {'avg':>10}")
    cum_pnl = 0
    cum_n = 0
    for label, ts in buckets.items():
        if not ts:
            print(f"{label:<12} {0:>4} {'-':>12} {'-':>6} {'-':>10}")
            continue
        n = len(ts)
        pnl = sum(x["net_profit"] for x in ts)
        wins = sum(1 for x in ts if x["net_profit"] > 0)
        wr = wins / n
        avg = pnl / n
        cum_pnl += pnl
        cum_n += n
        print(f"{label:<12} {n:>4} {pnl:>12,.0f} {wr*100:>5.1f}% {avg:>10,.0f}")
    print(f"{'TOTAL':<12} {cum_n:>4} {cum_pnl:>12,.0f}")

    # Cumulative >= threshold analysis
    print("\nCumulative >= threshold (what v4 would keep):")
    print(f"{'thr':<6} {'n':>4} {'pnl':>12} {'win%':>6} {'dPnL':>10}")
    v2_pnl = sum(x["net_profit"] for x in enriched)
    for thr in [0.25, 0.28, 0.30, 0.33, 0.35, 0.40]:
        kept = [x for x in enriched if x["dd_72h_estimated"] >= thr]
        if not kept:
            print(f"{thr:<6} {0:>4} {'-':>12} {'-':>6} {-v2_pnl:>10,.0f}")
            continue
        n = len(kept)
        pnl = sum(x["net_profit"] for x in kept)
        wins = sum(1 for x in kept if x["net_profit"] > 0)
        wr = wins / n
        # Δstack = pnl_kept - v2_pnl  (the trades NOT kept released cash but we lose their pnl)
        delta = pnl - v2_pnl
        print(f"{thr:<6} {n:>4} {pnl:>12,.0f} {wr*100:>5.1f}% {delta:>10,.0f}")

    # Save enriched
    with open("C:/Users/maftu/Projects/op-seller/.planning/analysis_tmp/iter92_v2_trades_enriched.json", "w") as f:
        json.dump(enriched, f, indent=2, default=str)

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
