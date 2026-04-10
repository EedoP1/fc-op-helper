import asyncio, sys, io
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.config import DATABASE_URL

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

async def main():
    engine = create_async_engine(DATABASE_URL)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    async with sf() as s:
        r = await s.execute(text(
            "SELECT ea_id, name, created_at FROM players "
            "WHERE created_at IS NOT NULL "
            "AND EXTRACT(DOW FROM created_at) = 5 "
            "ORDER BY created_at"
        ))
        all_friday = r.fetchall()

    hour_buckets = defaultdict(list)
    for ea_id, name, created_at in all_friday:
        bucket = created_at.strftime("%Y-%m-%d %H:00")
        hour_buckets[bucket].append((ea_id, name, created_at))

    promo_batches = {k: v for k, v in hour_buckets.items() if len(v) >= 10}

    for batch_time, cards in sorted(promo_batches.items()):
        if batch_time < "2026-03":
            continue

        ea_ids = [c[0] for c in cards]
        name_map = {c[0]: c[1] for c in cards}
        created = cards[0][2]
        cr = created.replace(tzinfo=None)

        async with sf() as s:
            r = await s.execute(text(
                "SELECT DISTINCT ON (ea_id, date_trunc('hour', captured_at)) "
                "ea_id, date_trunc('hour', captured_at) AS hour_ts, current_lowest_bin "
                "FROM market_snapshots "
                "WHERE ea_id = ANY(:ids) AND current_lowest_bin > 0 "
                "ORDER BY ea_id, date_trunc('hour', captured_at), captured_at DESC"
            ), {"ids": ea_ids})
            rows = r.fetchall()

        prices = defaultdict(list)
        for ea_id, hour_ts, price in rows:
            prices[ea_id].append((hour_ts, price))
        for ea_id in prices:
            prices[ea_id].sort(key=lambda x: x[0])

        n_with_data = len([e for e in ea_ids if e in prices and len(prices[e]) >= 24])
        print(f"\n{'='*120}")
        print(f"PROMO: {batch_time} ({len(cards)} cards, {n_with_data} with 24+ hourly snapshots)")
        print(f"{'='*120}")

        card_data = []
        for ea_id in ea_ids:
            if ea_id not in prices or len(prices[ea_id]) < 24:
                continue

            pts = prices[ea_id]
            first_price = pts[0][1]
            low_price = min(p for _, p in pts)
            peak_price = max(p for _, p in pts)
            final_price = pts[-1][1]

            low_hour = None
            peak_hour = None
            for h, p in pts:
                hc = h.replace(tzinfo=None) if h.tzinfo else h
                hrs = (hc - cr).total_seconds() / 3600
                if p == low_price and low_hour is None:
                    low_hour = hrs
                if p == peak_price:
                    peak_hour = hrs

            # Compute 12h median trend at each tick
            trends_by_hour = {}
            for i in range(24, len(pts)):
                recent = sorted([p for _, p in pts[i-12:i]])
                older = sorted([p for _, p in pts[i-24:i-12]])
                if len(recent) >= 3 and len(older) >= 3:
                    med_r = recent[len(recent)//2]
                    med_o = older[len(older)//2]
                    if med_o > 0:
                        hc = pts[i][0].replace(tzinfo=None) if pts[i][0].tzinfo else pts[i][0]
                        hrs_after = (hc - cr).total_seconds() / 3600
                        trends_by_hour[hrs_after] = (med_r - med_o) / med_o

            # First hour trend >= 20%
            trigger_hour = None
            for h in sorted(trends_by_hour.keys()):
                if trends_by_hour[h] >= 0.20:
                    trigger_hour = h
                    break

            max_trend = max(trends_by_hour.values()) if trends_by_hour else 0
            max_trend_hour = None
            for h in sorted(trends_by_hour.keys()):
                if trends_by_hour[h] == max_trend:
                    max_trend_hour = h
                    break

            # Price at trigger, peak after trigger
            trigger_price = None
            peak_after = None
            if trigger_hour is not None:
                for h, p in pts:
                    hc = h.replace(tzinfo=None) if h.tzinfo else h
                    hrs = (hc - cr).total_seconds() / 3600
                    if trigger_price is None and hrs >= trigger_hour:
                        trigger_price = p
                    if hrs >= trigger_hour:
                        if peak_after is None or p > peak_after:
                            peak_after = p

            profit_pct = None
            if trigger_price and peak_after:
                profit_pct = (peak_after * 0.95 - trigger_price) / trigger_price

            card_data.append({
                "ea_id": ea_id, "name": name_map[ea_id],
                "first": first_price, "low": low_price, "low_h": low_hour,
                "peak": peak_price, "peak_h": peak_hour,
                "final": final_price,
                "max_trend": max_trend, "max_trend_h": max_trend_hour,
                "trigger_h": trigger_hour,
                "trigger_price": trigger_price, "peak_after": peak_after,
                "profit_pct": profit_pct,
                "in_range": 12000 <= final_price <= 61000,
            })

        card_data.sort(key=lambda c: c["max_trend"], reverse=True)

        hdr = f"  {'Card':<25} {'1stPrc':>7} {'Low':>7} {'Low@':>5} {'MaxTrend':>9} {'@hr':>5} {'Trig@':>6} {'TrigPrc':>8} {'PeakAft':>8} {'Profit':>7} {'Range':>5}"
        print(hdr)
        print(f"  {'-'*110}")

        for c in card_data:
            low_h = f"{c['low_h']:.0f}" if c["low_h"] is not None else "?"
            mt = f"{c['max_trend']:+.0%}"
            mt_h = f"{c['max_trend_h']:.0f}" if c["max_trend_h"] is not None else "?"
            trig = f"{c['trigger_h']:.0f}" if c["trigger_h"] is not None else " -"
            trig_p = f"{c['trigger_price']:,}" if c["trigger_price"] else "-"
            peak_a = f"{c['peak_after']:,}" if c["peak_after"] else "-"
            prof = f"{c['profit_pct']:+.0%}" if c["profit_pct"] is not None else "-"
            ir = "Y" if c["in_range"] else "n"
            print(f"  {c['name'][:24]:<25} {c['first']:>7,} {c['low']:>7,} {low_h:>5} {mt:>9} {mt_h:>5} {trig:>6} {trig_p:>8} {peak_a:>8} {prof:>7} {ir:>5}")

    await engine.dispose()

asyncio.run(main())
