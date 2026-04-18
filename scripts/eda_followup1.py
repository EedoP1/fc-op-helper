"""Follow-ups after iter 1 EDA: verify weekly cycle is structural, not 1-off.
- Per ISO-week × weekday: mean fwd 24h return
- Per ISO-week × weekday: median (trim outliers)
- Best entry hour = weekday with lowest 168h-rolling-min proximity
"""
import asyncio
import sys
from collections import defaultdict
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.config import DATABASE_URL


async def main():
    engine = create_async_engine(DATABASE_URL)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    async with sf() as s:
        liq = (await s.execute(text(
            "SELECT ea_id FROM (SELECT ea_id, AVG(total_sold_count / 24.0) AS sph "
            "FROM (SELECT DISTINCT ea_id, date, total_sold_count "
            "      FROM daily_listing_summaries WHERE total_sold_count IS NOT NULL) d "
            "GROUP BY ea_id) x WHERE sph >= 5"
        ))).fetchall()
    liquid = {r[0] for r in liq}
    id_list = ",".join(str(i) for i in liquid)

    async with sf() as s:
        rows = (await s.execute(text(f"""
            SELECT ea_id, date_trunc('hour', captured_at) AS h,
                   percentile_disc(0.5) WITHIN GROUP (ORDER BY current_lowest_bin) AS p
            FROM market_snapshots
            WHERE ea_id IN ({id_list})
            GROUP BY ea_id, date_trunc('hour', captured_at)
            ORDER BY ea_id, h
        """))).fetchall()

    by_card: dict[int, list[tuple[datetime, int]]] = defaultdict(list)
    for ea_id, h, p in rows:
        by_card[ea_id].append((h, int(p)))

    # PER ISO-WEEK PER WEEKDAY: mean and median fwd 24h
    week_wd: dict[tuple[int, int], list[float]] = defaultdict(list)
    for ea_id, pts in by_card.items():
        for i in range(len(pts) - 24):
            h, p = pts[i]
            if p <= 0:
                continue
            wd = h.weekday()
            iy, iw, _ = h.isocalendar()
            fwd = pts[i + 24][1]
            ret = (fwd - p) / p
            week_wd[(iy, iw, wd)].append(ret)

    # Print as table
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    weeks_seen = sorted({(y, w) for (y, w, _) in week_wd.keys()})
    print(f"\nWEEKLY CYCLE TABLE (mean fwd 24h return per weekday per ISO-week)")
    print(f"liquid universe = {len(liquid)} cards")
    print(f"{'iso_wk':<10}", end="")
    for d in days:
        print(f"{d:>9}", end="")
    print()
    for (y, w) in weeks_seen:
        print(f"{y}-W{w:02d}   ", end="")
        for d in range(7):
            vals = week_wd.get((y, w, d), [])
            if vals:
                m = sum(vals) / len(vals)
                print(f"{m:>8.2%} ", end="")
            else:
                print(f"{'-':>8} ", end="")
        print()

    # Median version (less outlier-sensitive)
    print(f"\nWEEKLY CYCLE TABLE (MEDIAN fwd 24h return per weekday per ISO-week)")
    print(f"{'iso_wk':<10}", end="")
    for d in days:
        print(f"{d:>9}", end="")
    print()
    for (y, w) in weeks_seen:
        print(f"{y}-W{w:02d}   ", end="")
        for d in range(7):
            vals = week_wd.get((y, w, d), [])
            if vals:
                med = sorted(vals)[len(vals) // 2]
                print(f"{med:>8.2%} ", end="")
            else:
                print(f"{'-':>8} ", end="")
        print()

    # ALSO: mean fwd 48h per weekday (longer hold)
    week_wd48: dict[tuple[int, int, int], list[float]] = defaultdict(list)
    for ea_id, pts in by_card.items():
        for i in range(len(pts) - 48):
            h, p = pts[i]
            if p <= 0:
                continue
            wd = h.weekday()
            iy, iw, _ = h.isocalendar()
            fwd = pts[i + 48][1]
            week_wd48[(iy, iw, wd)].append((fwd - p) / p)

    print(f"\nMEDIAN fwd 48h return per weekday per ISO-week")
    print(f"{'iso_wk':<10}", end="")
    for d in days:
        print(f"{d:>9}", end="")
    print()
    for (y, w) in weeks_seen:
        print(f"{y}-W{w:02d}   ", end="")
        for d in range(7):
            vals = week_wd48.get((y, w, d), [])
            if vals:
                med = sorted(vals)[len(vals) // 2]
                print(f"{med:>8.2%} ", end="")
            else:
                print(f"{'-':>8} ", end="")
        print()

    # AND: per weekday × hour, MEDIAN fwd 24h, all weeks pooled
    by_wdh: dict[tuple[int, int], list[float]] = defaultdict(list)
    for ea_id, pts in by_card.items():
        for i in range(len(pts) - 24):
            h, p = pts[i]
            if p <= 0:
                continue
            wd = h.weekday()
            hr = h.hour
            fwd = pts[i + 24][1]
            by_wdh[(wd, hr)].append((fwd - p) / p)

    print(f"\nFULL TABLE: median fwd 24h return per (weekday, hour) — pooled over weeks")
    print(f"{'hr':<4}", end="")
    for d in days:
        print(f"{d:>9}", end="")
    print()
    for hr in range(24):
        print(f"{hr:>2}  ", end="")
        for d in range(7):
            vals = by_wdh.get((d, hr), [])
            if vals:
                med = sorted(vals)[len(vals) // 2]
                print(f"{med:>8.2%} ", end="")
            else:
                print(f"{'-':>8} ", end="")
        print()

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
