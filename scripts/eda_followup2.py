"""Iter 4 EDA: ground-truth analysis from trade_records.

Q: which cards has our bot actually made money on? At what buy and sell
   prices? What's the CONSISTENT spread per card? That's the real
   tradeable opportunity.

Q: also — the EDA showed 91 mono_up cards in liquid universe with
   avg first $14k → last $22k. They DID drift up. What characterizes them
   vs the 118 mono_down (premium decay)?
"""
import asyncio
from collections import defaultdict
from datetime import datetime
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.config import DATABASE_URL


async def main():
    engine = create_async_engine(DATABASE_URL)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    # Liquid universe
    async with sf() as s:
        rows = (await s.execute(text(
            "SELECT ea_id FROM (SELECT ea_id, AVG(total_sold_count / 24.0) AS sph "
            "FROM (SELECT DISTINCT ea_id, date, total_sold_count "
            "      FROM daily_listing_summaries WHERE total_sold_count IS NOT NULL) d "
            "GROUP BY ea_id) x WHERE sph >= 5"
        ))).fetchall()
    liquid = {r[0] for r in rows}
    id_list = ",".join(str(i) for i in liquid)

    # 1. trade_records: per-card buy and sell stats — what spread does our bot capture?
    print("=== 1: trade_records per-card buy vs sell stats (top 30 by sold) ===")
    async with sf() as s:
        rows = (await s.execute(text("""
            WITH buys AS (
              SELECT ea_id, AVG(price) AS avg_buy, MIN(price) AS min_buy,
                     MAX(price) AS max_buy, COUNT(*) AS bcnt
              FROM trade_records WHERE outcome='bought' OR action_type='buy'
              GROUP BY ea_id
            ),
            sells AS (
              SELECT ea_id, AVG(price) AS avg_sell, MIN(price) AS min_sell,
                     MAX(price) AS max_sell, COUNT(*) AS scnt
              FROM trade_records WHERE outcome='sold'
              GROUP BY ea_id
            )
            SELECT s.ea_id, s.scnt, b.bcnt, b.avg_buy, s.avg_sell,
                   (s.avg_sell - b.avg_buy) AS gross_spread,
                   ((s.avg_sell - b.avg_buy) / NULLIF(b.avg_buy, 0)) AS gross_pct
            FROM sells s LEFT JOIN buys b USING (ea_id)
            WHERE s.scnt > 5 AND b.bcnt > 0
            ORDER BY s.scnt DESC LIMIT 30
        """))).fetchall()

    print(f"  {'ea_id':>10} {'sold':>6} {'bought':>7} {'avg_buy':>8} {'avg_sell':>9} {'spread':>8} {'pct':>7}")
    for r in rows:
        print(f"  {r[0]:>10} {r[1]:>6} {r[2]:>7} {r[3] or 0:>8.0f} {r[4] or 0:>9.0f} {r[5] or 0:>8.0f} {(r[6] or 0):>7.1%}")

    # 2. mono_up cards: get IDs to inspect
    print("\n\n=== 2: mono_up cards in liquid universe — what makes them rise? ===")
    async with sf() as s:
        rows = (await s.execute(text(f"""
            SELECT ea_id, date_trunc('day', captured_at) AS day,
                   percentile_disc(0.5) WITHIN GROUP (ORDER BY current_lowest_bin) AS p
            FROM market_snapshots WHERE ea_id IN ({id_list})
            GROUP BY ea_id, date_trunc('day', captured_at)
            ORDER BY ea_id, day
        """))).fetchall()
    by_card = defaultdict(list)
    for ea_id, day, p in rows:
        by_card[ea_id].append((day, int(p)))

    mono_up_ids = []
    for ea_id, pts in by_card.items():
        if len(pts) < 5:
            continue
        prices = [p for _, p in pts]
        first, last = prices[0], prices[-1]
        peak = max(prices)
        trough = min(prices)
        peak_i = prices.index(peak)
        trough_i = prices.index(trough)
        if last / first - 1 > 0.20 and trough_i < peak_i:
            mono_up_ids.append((ea_id, first, last, last / first - 1))
    mono_up_ids.sort(key=lambda r: -r[3])

    print(f"  Top 30 mono_up by % rise:")
    print(f"  {'ea_id':>10} {'first':>8} {'last':>8} {'rise':>7}")
    for ea_id, first, last, rise in mono_up_ids[:30]:
        print(f"  {ea_id:>10} {first:>8} {last:>8} {rise:>7.1%}")

    # 3. For top 10 mono_up: What was the rise PATTERN over time? Check if it was
    #    smooth or burst. Identify the "burst day" — the day with highest fwd-1d return.
    print("\n=== 3: For top 10 mono_up cards, the burst-day (max fwd 1d) profile ===")
    print(f"  {'ea_id':>10} {'burst_day':<11} {'p_before':>8} {'p_after':>8} {'jump':>7} {'final_pct_of_peak':>17}")
    for ea_id, _, _, _ in mono_up_ids[:10]:
        pts = by_card[ea_id]
        prices = [p for _, p in pts]
        days = [d for d, _ in pts]
        peak = max(prices)
        peak_idx = prices.index(peak)
        # Find largest 1-day jump up to peak
        best_jump = (0, 0, 0, None)
        for i in range(1, len(prices)):
            if prices[i - 1] <= 0:
                continue
            jump = (prices[i] - prices[i-1]) / prices[i-1]
            if jump > best_jump[0]:
                best_jump = (jump, prices[i-1], prices[i], days[i])
        if best_jump[3]:
            print(f"  {ea_id:>10} {str(best_jump[3])[:10]:<11} {best_jump[1]:>8} {best_jump[2]:>8} {best_jump[0]:>7.1%} {prices[-1]/peak:>17.1%}")

    # 4. Per-card oscillation amplitude over a 7-day window
    print("\n\n=== 4: Per-card weekly amplitude (mean (max-min)/median over 7d windows) ===")
    async with sf() as s:
        rows = (await s.execute(text(f"""
            SELECT ea_id, date_trunc('hour', captured_at) AS h,
                   percentile_disc(0.5) WITHIN GROUP (ORDER BY current_lowest_bin) AS p
            FROM market_snapshots WHERE ea_id IN ({id_list})
            GROUP BY ea_id, date_trunc('hour', captured_at)
            ORDER BY ea_id, h
        """))).fetchall()
    by_card_h = defaultdict(list)
    for ea_id, h, p in rows:
        by_card_h[ea_id].append((h, int(p)))

    amps = []
    for ea_id, pts in by_card_h.items():
        if len(pts) < 168:
            continue
        amp_samples = []
        for i in range(0, len(pts) - 168, 24):
            window = [p for _, p in pts[i:i+168]]
            if not window:
                continue
            lo, hi = min(window), max(window)
            med = sorted(window)[len(window) // 2]
            if med > 0:
                amp_samples.append((hi - lo) / med)
        if amp_samples:
            avg_amp = sum(amp_samples) / len(amp_samples)
            avg_med = sum(p for _, p in pts) / len(pts)
            amps.append((ea_id, avg_amp, avg_med, len(amp_samples)))
    amps.sort(key=lambda r: -r[1])
    print(f"  Top 30 highest weekly amplitude (the oscillating cards — best for buy-low/sell-high):")
    print(f"  {'ea_id':>10} {'amp':>7} {'avg_p':>8} {'samples':>8}")
    for ea_id, amp, avg_p, n in amps[:30]:
        print(f"  {ea_id:>10} {amp:>7.1%} {avg_p:>8.0f} {n:>8}")
    print(f"\n  Bottom 10 (most stable — bad for trading):")
    for ea_id, amp, avg_p, n in amps[-10:]:
        print(f"  {ea_id:>10} {amp:>7.1%} {avg_p:>8.0f} {n:>8}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
