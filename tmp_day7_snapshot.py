"""Snapshot all promo cards at exact hours after release to find the rally day."""
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

    # Load promo batches
    async with sf() as s:
        r = await s.execute(text(
            "SELECT ea_id, name, created_at FROM players "
            "WHERE created_at IS NOT NULL AND EXTRACT(DOW FROM created_at) = 5 "
            "ORDER BY created_at"
        ))
        all_friday = r.fetchall()

    hour_buckets = defaultdict(list)
    for ea_id, name, created_at in all_friday:
        bucket = created_at.strftime("%Y-%m-%d %H:00")
        hour_buckets[bucket].append((ea_id, name, created_at))
    promo_batches = {k: v for k, v in hour_buckets.items() if len(v) >= 10 and k >= "2026-03-20"}

    for batch_time, cards in sorted(promo_batches.items()):
        ea_ids = [c[0] for c in cards]
        name_map = {c[0]: c[1] for c in cards}
        created = cards[0][2].replace(tzinfo=None)

        # Load hourly snapshots
        async with sf() as s:
            r = await s.execute(text(
                "SELECT DISTINCT ON (ea_id, date_trunc('hour', captured_at)) "
                "ea_id, date_trunc('hour', captured_at) AS hour_ts, current_lowest_bin "
                "FROM market_snapshots "
                "WHERE ea_id = ANY(:ids) AND current_lowest_bin > 0 "
                "ORDER BY ea_id, date_trunc('hour', captured_at), captured_at DESC"
            ), {"ids": ea_ids})
            rows = r.fetchall()

        # Build {ea_id: [(ts, price), ...]} sorted
        prices = defaultdict(list)
        for ea_id, hour_ts, price in rows:
            prices[ea_id].append((hour_ts.replace(tzinfo=None), price))
        for ea_id in prices:
            prices[ea_id].sort(key=lambda x: x[0])

        print(f"\n{'='*140}")
        print(f"PROMO: {batch_time} ({len(cards)} cards) — release = Friday 18:00 UTC")
        print(f"{'='*140}")

        # Snapshot at various hours after release
        # 168h = exactly 7 days (next Friday 18:00)
        snapshot_hours = [48, 72, 96, 120, 144, 156, 162, 168, 174, 180, 192, 216, 240]

        # For each card, compute median trend at each snapshot hour
        # Trend = median of last 12h vs median of 12h before that
        header = f"  {'Card':<25} {'Price':>6}"
        for sh in snapshot_hours:
            day = sh / 24
            header += f" {sh}h({day:.0f}d)"
        print(header)
        print(f"  {'-'*25} {'-'*6}" + " -------" * len(snapshot_hours))

        for ea_id in ea_ids:
            if ea_id not in prices or len(prices[ea_id]) < 24:
                continue

            pts = prices[ea_id]
            name = name_map[ea_id]

            # Current price (latest)
            cur_price = pts[-1][1]

            # Price filter: only show 12K-61K cards
            if not (12000 <= cur_price <= 61000 or any(12000 <= p <= 61000 for _, p in pts[-48:])):
                continue

            row = f"  {name[:24]:<25} {cur_price:>6,}"

            for sh in snapshot_hours:
                target = created + timedelta(hours=sh)
                # Get prices in [target-12h, target] and [target-24h, target-12h]
                recent = sorted([
                    p for ts, p in pts
                    if 0 >= (ts - target).total_seconds() / 3600 >= -12
                ])
                older = sorted([
                    p for ts, p in pts
                    if -12 > (ts - target).total_seconds() / 3600 >= -24
                ])

                if len(recent) >= 3 and len(older) >= 3:
                    med_r = recent[len(recent) // 2]
                    med_o = older[len(older) // 2]
                    if med_o > 0:
                        trend = (med_r - med_o) / med_o
                        row += f"  {trend:>+5.0%} "
                    else:
                        row += "    n/a "
                else:
                    row += "    n/a "

            print(row)

        # Summary: what % of in-range cards have positive/10%+/20%+ trend at each hour
        print(f"\n  SUMMARY (12K-61K cards only):")
        summary_header = f"  {'Metric':<25} {'':>6}"
        for sh in snapshot_hours:
            summary_header += f" {sh:>4}h  "
        print(summary_header)

        for threshold, label in [(0.0, "% positive trend"), (0.05, "% trend >= 5%"),
                                  (0.10, "% trend >= 10%"), (0.15, "% trend >= 15%"),
                                  (0.20, "% trend >= 20%")]:
            row = f"  {label:<25} {'':>6}"
            for sh in snapshot_hours:
                target = created + timedelta(hours=sh)
                total = 0
                above = 0
                for ea_id in ea_ids:
                    if ea_id not in prices or len(prices[ea_id]) < 24:
                        continue
                    pts = prices[ea_id]
                    cur = pts[-1][1]
                    if not (12000 <= cur <= 61000 or any(12000 <= p <= 61000 for _, p in pts[-48:])):
                        continue

                    recent = sorted([p for ts, p in pts if 0 >= (ts - target).total_seconds() / 3600 >= -12])
                    older = sorted([p for ts, p in pts if -12 > (ts - target).total_seconds() / 3600 >= -24])
                    if len(recent) >= 3 and len(older) >= 3:
                        med_r = recent[len(recent) // 2]
                        med_o = older[len(older) // 2]
                        if med_o > 0:
                            trend = (med_r - med_o) / med_o
                            total += 1
                            if trend >= threshold:
                                above += 1
                            continue
                    # no data at this hour
                if total > 0:
                    row += f"  {above}/{total:<3} "
                else:
                    row += "   n/a  "
            print(row)

    await engine.dispose()

asyncio.run(main())
