"""Six-axis EDA pulse for the autonomous algo-trading research loop.

Run targeted DB questions that aren't already answered by prior research docs.
Outputs to stdout; iterations append focused follow-ups via separate scripts.

Usage:  python scripts/eda_pulse.py [a|b|c|d|e|f|all]
"""
import asyncio
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from src.config import DATABASE_URL


async def get_liquid_universe(session_factory) -> set[int]:
    """Cards passing --min-sph 5 filter."""
    async with session_factory() as s:
        rows = (await s.execute(text(
            "SELECT ea_id, AVG(total_sold_count / 24.0) AS sph "
            "FROM (SELECT DISTINCT ea_id, date, total_sold_count "
            "      FROM daily_listing_summaries "
            "      WHERE total_sold_count IS NOT NULL) d "
            "GROUP BY ea_id "
            "HAVING AVG(total_sold_count / 24.0) >= 5"
        ))).fetchall()
    return {r[0] for r in rows}


async def query_a_trade_records(session_factory):
    """A: Aggregate list→sold events per ea_id from trade_records.
    Ground truth from our bot's actual sales."""
    print("\n=== A: trade_records ground truth ===")
    async with session_factory() as s:
        # See schema first
        cols = (await s.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name='trade_records' ORDER BY ordinal_position"
        ))).fetchall()
        print("trade_records cols:", [(c, t) for c, t in cols])

        rows = (await s.execute(text(
            "SELECT outcome, COUNT(*) FROM trade_records GROUP BY outcome"
        ))).fetchall()
        print("Outcomes:", rows)

        # Try to assemble buy→sell pairs by ea_id for our bot
        # Many schemas have action_type, outcome, price, recorded_at
        pair_rows = (await s.execute(text("""
            WITH buys AS (
              SELECT ea_id, price AS buy_price, recorded_at AS buy_time
              FROM trade_records
              WHERE outcome = 'bought' OR action_type = 'buy'
            ),
            sells AS (
              SELECT ea_id, price AS sell_price, recorded_at AS sell_time
              FROM trade_records
              WHERE outcome = 'sold'
            ),
            stats AS (
              SELECT s.ea_id,
                     COUNT(*) AS sold_count,
                     AVG(s.sell_price) AS avg_sell,
                     MIN(s.sell_price) AS min_sell,
                     MAX(s.sell_price) AS max_sell
              FROM sells s
              GROUP BY s.ea_id
            )
            SELECT ea_id, sold_count, avg_sell, min_sell, max_sell
            FROM stats
            ORDER BY sold_count DESC
            LIMIT 25
        """))).fetchall()
        print("\nTop 25 cards by sold count (our bot):")
        print(f"  {'ea_id':>10} {'sold':>6} {'avg':>10} {'min':>10} {'max':>10}")
        for r in pair_rows:
            print(f"  {r[0]:>10} {r[1]:>6} {r[2] or 0:>10.0f} {r[3] or 0:>10} {r[4] or 0:>10}")


async def query_b_daily_shapes(session_factory, liquid_ids: set[int]):
    """B: Per-card 22-day daily-median timeline; cluster shapes."""
    print("\n=== B: daily-median shape clusters ===")
    if not liquid_ids:
        print("No liquid cards.")
        return
    id_list = ",".join(str(i) for i in liquid_ids)
    async with session_factory() as s:
        rows = (await s.execute(text(
            f"SELECT ea_id, date_trunc('day', captured_at) AS day, "
            f"       percentile_disc(0.5) WITHIN GROUP (ORDER BY current_lowest_bin) AS p "
            f"FROM market_snapshots WHERE ea_id IN ({id_list}) "
            f"GROUP BY ea_id, date_trunc('day', captured_at) "
            f"ORDER BY ea_id, day"
        ))).fetchall()

    by_card: dict[int, list[tuple[datetime, int]]] = defaultdict(list)
    for ea_id, day, p in rows:
        by_card[ea_id].append((day, int(p)))

    # Classify shape: monotone up/down, U, ∩, flat, oscillating
    shapes = defaultdict(list)
    for ea_id, pts in by_card.items():
        if len(pts) < 5:
            continue
        prices = [p for _, p in pts]
        first, last = prices[0], prices[-1]
        peak = max(prices)
        trough = min(prices)
        peak_i = prices.index(peak)
        trough_i = prices.index(trough)
        rng = (peak - trough) / max(1, trough)
        # crude classification
        if rng < 0.05:
            shapes["flat"].append((ea_id, first, last, rng))
        elif last / first - 1 > 0.20 and trough_i < peak_i:
            shapes["mono_up"].append((ea_id, first, last, rng))
        elif first / last - 1 > 0.20 and peak_i < trough_i:
            shapes["mono_down"].append((ea_id, first, last, rng))
        elif trough_i not in (0, len(prices) - 1):
            shapes["U"].append((ea_id, first, last, rng))
        elif peak_i not in (0, len(prices) - 1):
            shapes["cap"].append((ea_id, first, last, rng))
        else:
            shapes["osc"].append((ea_id, first, last, rng))

    print(f"Shape distribution among {len(by_card)} liquid cards:")
    for sh, items in shapes.items():
        avg_rng = sum(r for _, _, _, r in items) / max(1, len(items))
        avg_first = sum(f for _, f, _, _ in items) / max(1, len(items))
        avg_last = sum(l for _, _, l, _ in items) / max(1, len(items))
        print(f"  {sh:10s}: n={len(items):3d}  avg_first=${avg_first:>8,.0f}  avg_last=${avg_last:>8,.0f}  avg_range={avg_rng:.1%}")


async def query_c_scanner_vs_futbin(session_factory, liquid_ids: set[int]):
    """C: Scanner vs FUTBIN divergence and what happens next 24-48h."""
    print("\n=== C: scanner vs FUTBIN divergence forward returns ===")
    if not liquid_ids:
        return
    id_list = ",".join(str(i) for i in liquid_ids)
    async with session_factory() as s:
        # Get FUTBIN data — need to map ea_id to futbin_id via players?
        cols = (await s.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name='players' ORDER BY ordinal_position"
        ))).fetchall()
        print("players cols:", [(c, t) for c, t in cols])
        cols2 = (await s.execute(text(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name='price_history' ORDER BY ordinal_position"
        ))).fetchall()
        print("price_history cols:", [(c, t) for c, t in cols2])

        # See how many liquid cards have FUTBIN data
        cnt = (await s.execute(text(
            f"SELECT COUNT(DISTINCT ph.ea_id) FROM price_history ph "
            f"WHERE ph.ea_id IN ({id_list})"
        ))).scalar() if False else None
        # ph might key on futbin_id only. Try a join attempt.
        try:
            cnt = (await s.execute(text(
                f"SELECT COUNT(DISTINCT ea_id) FROM price_history "
                f"WHERE ea_id IN ({id_list})"
            ))).scalar()
            print(f"Liquid cards with FUTBIN price_history rows: {cnt} of {len(liquid_ids)}")
        except Exception as e:
            print(f"price_history.ea_id query failed: {e}")
            return

        # Sample comparison: scanner median vs FUTBIN median per (ea_id, day)
        rows = (await s.execute(text(f"""
            WITH scan AS (
              SELECT ea_id, date_trunc('hour', captured_at) AS h,
                     percentile_disc(0.5) WITHIN GROUP (ORDER BY current_lowest_bin) AS sp
              FROM market_snapshots
              WHERE ea_id IN ({id_list})
              GROUP BY ea_id, date_trunc('hour', captured_at)
            ),
            fb AS (
              SELECT ea_id, date_trunc('hour', timestamp) AS h,
                     AVG(price) AS fp
              FROM price_history
              WHERE ea_id IN ({id_list})
              GROUP BY ea_id, date_trunc('hour', timestamp)
            )
            SELECT scan.ea_id, scan.h, scan.sp, fb.fp,
                   (scan.sp::float - fb.fp) / NULLIF(fb.fp, 0) AS div
            FROM scan JOIN fb USING (ea_id, h)
            WHERE fb.fp > 0
            ORDER BY scan.ea_id, scan.h
        """))).fetchall()

        if not rows:
            print("No overlap between scanner and FUTBIN.")
            return

        print(f"Overlap rows (ea_id × hour): {len(rows)}")
        # Forward 24h return analysis: when divergence > X%, what's price 24h later?
        by_card: dict[int, list[tuple[datetime, int, float, float]]] = defaultdict(list)
        for ea_id, h, sp, fp, div in rows:
            by_card[ea_id].append((h, int(sp), float(fp), float(div) if div is not None else 0.0))

        # bin divergence and forward return
        bins = defaultdict(list)
        for ea_id, pts in by_card.items():
            for i, (h, sp, fp, div) in enumerate(pts):
                # find a point ~24h later
                future = next((p for p in pts[i+1:] if (p[0] - h).total_seconds() / 3600 >= 24), None)
                if not future:
                    continue
                fwd_ret = (future[1] - sp) / max(1, sp)
                # bin divergence
                if div > 0.10:
                    bins["scanner > futbin +10%"].append(fwd_ret)
                elif div > 0.05:
                    bins["scanner > futbin +5%"].append(fwd_ret)
                elif div < -0.10:
                    bins["scanner < futbin -10%"].append(fwd_ret)
                elif div < -0.05:
                    bins["scanner < futbin -5%"].append(fwd_ret)
                else:
                    bins["near-flat"].append(fwd_ret)

        print(f"\nForward 24h scanner return when divergence is...:")
        print(f"  {'bin':<28} {'n':>6} {'mean':>8} {'median':>8}")
        for k, vals in bins.items():
            if not vals:
                continue
            mean = sum(vals) / len(vals)
            med = sorted(vals)[len(vals) // 2]
            print(f"  {k:<28} {len(vals):>6} {mean:>8.2%} {med:>8.2%}")


async def query_d_listing_count_leading(session_factory, liquid_ids: set[int]):
    """D: corr(delta_listing_count, delta_smoothed_price at t+6/12/24h)."""
    print("\n=== D: listing_count as leading indicator ===")
    if not liquid_ids:
        return
    id_list = ",".join(str(i) for i in liquid_ids)
    async with session_factory() as s:
        rows = (await s.execute(text(f"""
            SELECT ea_id, date_trunc('hour', captured_at) AS h,
                   percentile_disc(0.5) WITHIN GROUP (ORDER BY current_lowest_bin) AS p,
                   AVG(COALESCE(listing_count, 0)) AS lc
            FROM market_snapshots
            WHERE ea_id IN ({id_list})
            GROUP BY ea_id, date_trunc('hour', captured_at)
            ORDER BY ea_id, h
        """))).fetchall()

    by_card: dict[int, list[tuple[datetime, int, float]]] = defaultdict(list)
    for ea_id, h, p, lc in rows:
        by_card[ea_id].append((h, int(p), float(lc)))

    # Pool changes across cards
    deltas = []  # (delta_lc_pct over 6h, fwd_24h_price_ret)
    for ea_id, pts in by_card.items():
        if len(pts) < 30:
            continue
        for i in range(6, len(pts) - 24):
            now_lc = pts[i][2]
            past_lc = pts[i-6][2]
            if past_lc <= 0:
                continue
            d_lc = (now_lc - past_lc) / past_lc

            now_p = pts[i][1]
            fwd_p = pts[i+24][1]
            if now_p <= 0:
                continue
            d_p = (fwd_p - now_p) / now_p
            deltas.append((d_lc, d_p))

    if not deltas:
        print("Not enough data.")
        return
    print(f"n samples: {len(deltas)}")
    # Bin by delta_lc
    bins = [
        ("d_lc < -25%", lambda d: d < -0.25),
        ("d_lc -25..-10%", lambda d: -0.25 <= d < -0.10),
        ("d_lc -10..+10%", lambda d: -0.10 <= d < 0.10),
        ("d_lc +10..+25%", lambda d: 0.10 <= d < 0.25),
        ("d_lc > +25%", lambda d: d >= 0.25),
    ]
    print(f"  {'bin':<22} {'n':>6} {'fwd24h_mean':>12} {'fwd24h_med':>11}")
    for label, pred in bins:
        sel = [d_p for d_lc, d_p in deltas if pred(d_lc)]
        if not sel:
            print(f"  {label:<22} {0:>6}")
            continue
        m = sum(sel) / len(sel)
        med = sorted(sel)[len(sel) // 2]
        print(f"  {label:<22} {len(sel):>6} {m:>12.2%} {med:>11.2%}")


async def query_e_dow_hour(session_factory, liquid_ids: set[int]):
    """E: Day-of-week × hour-of-day mean forward 24h return."""
    print("\n=== E: day-of-week × hour-of-day forward returns ===")
    if not liquid_ids:
        return
    id_list = ",".join(str(i) for i in liquid_ids)
    async with session_factory() as s:
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

    # For each (weekday, hour), sample fwd 24h and fwd 12h returns
    bucket: dict[tuple[int, int], list[float]] = defaultdict(list)
    bucket12: dict[tuple[int, int], list[float]] = defaultdict(list)
    for ea_id, pts in by_card.items():
        for i in range(len(pts) - 24):
            h, p = pts[i]
            wd = h.weekday()
            hr = h.hour
            fwd_p = pts[i + 24][1]
            if p > 0:
                bucket[(wd, hr)].append((fwd_p - p) / p)
            if i + 12 < len(pts):
                fwd12 = pts[i + 12][1]
                if p > 0:
                    bucket12[(wd, hr)].append((fwd12 - p) / p)

    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    print("Mean forward 24h return %, by weekday × hour (only nonempty):")
    print(f"  {'wd hr':<8} {'n':>6} {'mean24':>8} {'mean12':>8}")
    # Top 30 most positive
    flat = []
    for (wd, hr), vals in bucket.items():
        if len(vals) < 50:
            continue
        m = sum(vals) / len(vals)
        v12 = bucket12[(wd, hr)]
        m12 = sum(v12) / len(v12) if v12 else 0
        flat.append((wd, hr, len(vals), m, m12))
    flat.sort(key=lambda r: r[3], reverse=True)
    print("Top 15 best fwd24:")
    for wd, hr, n, m, m12 in flat[:15]:
        print(f"  {days[wd]} {hr:02d}  {n:>6} {m:>8.2%} {m12:>8.2%}")
    print("Top 15 worst fwd24:")
    for wd, hr, n, m, m12 in flat[-15:]:
        print(f"  {days[wd]} {hr:02d}  {n:>6} {m:>8.2%} {m12:>8.2%}")

    # And aggregate per weekday
    wd_only: dict[int, list[float]] = defaultdict(list)
    for (wd, _hr), vals in bucket.items():
        wd_only[wd].extend(vals)
    print("\nAggregate by weekday (fwd 24h):")
    for wd in range(7):
        vals = wd_only[wd]
        if not vals:
            continue
        m = sum(vals) / len(vals)
        med = sorted(vals)[len(vals) // 2]
        print(f"  {days[wd]}: n={len(vals)} mean={m:>8.2%} median={med:>8.2%}")


async def query_f_intraday_vol(session_factory, liquid_ids: set[int]):
    """F: cards with intraday range >= 5%; does next day have predictable direction?"""
    print("\n=== F: intraday vol → next-day direction ===")
    if not liquid_ids:
        return
    id_list = ",".join(str(i) for i in liquid_ids)
    async with session_factory() as s:
        rows = (await s.execute(text(f"""
            SELECT ea_id, date_trunc('day', captured_at) AS day,
                   MIN(current_lowest_bin) AS lo,
                   MAX(current_lowest_bin) AS hi,
                   percentile_disc(0.5) WITHIN GROUP (ORDER BY current_lowest_bin) AS med
            FROM market_snapshots
            WHERE ea_id IN ({id_list})
            GROUP BY ea_id, date_trunc('day', captured_at)
            ORDER BY ea_id, day
        """))).fetchall()

    by_card: dict[int, list[tuple[datetime, int, int, int]]] = defaultdict(list)
    for ea_id, day, lo, hi, med in rows:
        by_card[ea_id].append((day, int(lo), int(hi), int(med)))

    # For each card: classify intraday and look at fwd-day direction
    bins = defaultdict(list)
    for ea_id, pts in by_card.items():
        for i in range(len(pts) - 1):
            day, lo, hi, med = pts[i]
            if med <= 0 or lo <= 0:
                continue
            rng = (hi - lo) / lo
            next_med = pts[i + 1][3]
            if med <= 0:
                continue
            fwd_ret = (next_med - med) / med
            # close near top of range vs bottom?
            if rng >= 0.05:
                # we don't have OHLC, but bias by where today's median sits
                pos_in_range = (med - lo) / (hi - lo) if hi > lo else 0.5
                if pos_in_range >= 0.7:
                    bins["wide+closehi"].append(fwd_ret)
                elif pos_in_range <= 0.3:
                    bins["wide+closelo"].append(fwd_ret)
                else:
                    bins["wide+mid"].append(fwd_ret)
            elif rng >= 0.02:
                bins["med_vol"].append(fwd_ret)
            else:
                bins["tight"].append(fwd_ret)

    print(f"  {'bin':<18} {'n':>6} {'mean_fwd':>10} {'median_fwd':>11}")
    for k, vals in bins.items():
        if not vals:
            continue
        m = sum(vals) / len(vals)
        med = sorted(vals)[len(vals) // 2]
        print(f"  {k:<18} {len(vals):>6} {m:>10.2%} {med:>11.2%}")


async def main():
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    engine = create_async_engine(DATABASE_URL)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    liquid = await get_liquid_universe(sf)
    print(f"Liquid universe (>=5 sph): {len(liquid)} cards")

    runs = {
        "a": query_a_trade_records,
        "b": lambda s: query_b_daily_shapes(s, liquid),
        "c": lambda s: query_c_scanner_vs_futbin(s, liquid),
        "d": lambda s: query_d_listing_count_leading(s, liquid),
        "e": lambda s: query_e_dow_hour(s, liquid),
        "f": lambda s: query_f_intraday_vol(s, liquid),
    }
    if which == "all":
        for k, fn in runs.items():
            try:
                await fn(sf)
            except Exception as e:
                print(f"\n!! {k} failed: {e}")
    else:
        for k in which:
            try:
                await runs[k](sf)
            except Exception as e:
                print(f"\n!! {k} failed: {e}")

    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
