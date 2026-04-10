"""Trace promo_dip_buy strategy logic exactly as the code runs it."""
import asyncio, sys, io, json
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.config import DATABASE_URL
from src.algo.strategies.promo_dip_buy import PromoDipBuyStrategy
from src.algo.models import Portfolio

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

async def main():
    engine = create_async_engine(DATABASE_URL)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    # Load hourly market_snapshots (same as engine.load_market_snapshot_data)
    async with sf() as s:
        r = await s.execute(text(
            "SELECT DISTINCT ON (ea_id, date_trunc('hour', captured_at)) "
            "ea_id, date_trunc('hour', captured_at) AS hour_ts, current_lowest_bin "
            "FROM market_snapshots "
            "WHERE current_lowest_bin > 0 "
            "ORDER BY ea_id, date_trunc('hour', captured_at), captured_at DESC"
        ))
        rows = r.fetchall()

    print(f"Loaded {len(rows)} hourly price points")

    # Build price_data: {ea_id: [(ts, price), ...]}
    price_data = defaultdict(list)
    for ea_id, hour_ts, price in rows:
        price_data[ea_id].append((hour_ts.replace(tzinfo=None), price))
    for ea_id in price_data:
        price_data[ea_id].sort(key=lambda x: x[0])
    price_data = {eid: pts for eid, pts in price_data.items() if len(pts) >= 6}

    # Load created_at
    async with sf() as s:
        r = await s.execute(text(
            "SELECT ea_id, created_at FROM players WHERE created_at IS NOT NULL"
        ))
        created_at_map = {}
        for ea_id, created_at in r.fetchall():
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
            created_at_map[ea_id] = created_at.replace(tzinfo=None)

    # Load names
    async with sf() as s:
        r = await s.execute(text("SELECT ea_id, name FROM players WHERE name IS NOT NULL"))
        names = {row[0]: row[1] for row in r.fetchall()}

    # Build timeline
    timeline = defaultdict(list)
    for ea_id, points in price_data.items():
        for ts, price in points:
            timeline[ts].append((ea_id, price))
    sorted_ts = sorted(timeline.keys())

    # Identify promo batches (same logic as strategy)
    hour_buckets = defaultdict(list)
    for ea_id, created in created_at_map.items():
        cr = created.replace(tzinfo=None) if created.tzinfo else created
        if cr.weekday() == 4:  # Friday
            bucket = (cr.year, cr.month, cr.day, cr.hour)
            hour_buckets[bucket].append(ea_id)
    promo_ids = set()
    promo_batches = {}
    for bucket, ids in hour_buckets.items():
        if len(ids) >= 10:
            promo_ids.update(ids)
            batch_name = f"{bucket[0]}-{bucket[1]:02d}-{bucket[2]:02d} {bucket[3]:02d}:00"
            promo_batches[batch_name] = ids

    print(f"\nPromo batches with 10+ cards:")
    for batch, ids in sorted(promo_batches.items()):
        # Only show batches where created_at is after scanner start
        if batch >= "2026-03-20":
            in_data = [eid for eid in ids if eid in price_data]
            print(f"  {batch}: {len(ids)} cards ({len(in_data)} have price data)")

    # Use exact strategy params from param_grid_hourly
    params = {
        "trend_pct": 0.20,
        "trend_lookback": 12,
        "sell_trend_pct": 0.02,
        "min_day": 0,
        "max_day": 999,
        "min_crash": 0.05,
        "trailing_stop": 1.0,
        "stop_delay_hours": 96,
        "max_hold_hours": 336,
        "min_price": 12000,
        "max_price": 61000,
        "max_position_pct": 0.10,
    }
    strategy = PromoDipBuyStrategy(params)

    # Set existing IDs and created_at (same as engine)
    existing_ids = {ea_id for ea_id, _ in timeline[sorted_ts[0]]}
    strategy.set_existing_ids(existing_ids)
    strategy.set_created_at_map(created_at_map)

    portfolio = Portfolio(cash=1_000_000)

    # Walk timeline, log every signal
    print(f"\n{'='*120}")
    print("SIGNAL LOG (exact strategy execution)")
    print(f"{'='*120}")

    # Also track: for each promo card, what's the trend at each tick?
    # We'll collect this after running the strategy
    # First, let's just run and capture signals
    all_signals = []
    for ts in sorted_ts:
        ticks = timeline[ts]
        signals = strategy.on_tick_batch(ticks, ts, portfolio)
        for sig in signals:
            price = next((p for eid, p in ticks if eid == sig.ea_id), 0)
            if sig.action == "BUY":
                portfolio.buy(sig.ea_id, sig.quantity, price, ts)
                all_signals.append(("BUY", sig.ea_id, sig.quantity, price, ts))
                name = names.get(sig.ea_id, str(sig.ea_id))
                created = created_at_map.get(sig.ea_id)
                hrs = (ts - created).total_seconds() / 3600 if created else 0
                print(f"  BUY  {ts}  {name[:25]:<26} {sig.quantity}x @ {price:>7,}  (card age: {hrs:.0f}h)")
            elif sig.action == "SELL":
                portfolio.sell(sig.ea_id, sig.quantity, price, ts)
                all_signals.append(("SELL", sig.ea_id, sig.quantity, price, ts))
                name = names.get(sig.ea_id, str(sig.ea_id))
                print(f"  SELL {ts}  {name[:25]:<26} {sig.quantity}x @ {price:>7,}")

    # Now compute: for each promo card in Mar 27 and Apr 3 batches,
    # what was the max median trend the strategy computed?
    print(f"\n{'='*120}")
    print("PER-CARD TREND ANALYSIS (using strategy's exact median trend logic)")
    print(f"{'='*120}")

    for batch_name in sorted(promo_batches.keys()):
        if batch_name < "2026-03-20":
            continue
        batch_ids = promo_batches[batch_name]
        print(f"\n--- {batch_name} ({len(batch_ids)} cards) ---")

        card_results = []
        for ea_id in batch_ids:
            if ea_id not in price_data:
                continue
            pts = price_data[ea_id]
            name = names.get(ea_id, str(ea_id))
            created = created_at_map.get(ea_id)
            if not created:
                continue

            # Compute median trend at each tick (same as strategy code)
            lb = 12  # trend_lookback
            max_trend = None
            max_trend_ts = None
            first_20_ts = None
            first_20_trend = None

            history = []
            for ts, price in pts:
                history.append((ts, price))
                if len(history) < lb * 2:
                    continue
                recent = sorted([p for _, p in history[-lb:]])
                older = sorted([p for _, p in history[-lb*2:-lb]])
                if len(recent) >= 3 and len(older) >= 3:
                    med_r = recent[len(recent)//2]
                    med_o = older[len(older)//2]
                    if med_o > 0:
                        trend = (med_r - med_o) / med_o
                        if max_trend is None or trend > max_trend:
                            max_trend = trend
                            max_trend_ts = ts
                        if trend >= 0.20 and first_20_ts is None:
                            first_20_ts = ts
                            first_20_trend = trend

            # Check other filters: is it in promo_ids? price in range? age ok?
            in_promo = ea_id in promo_ids
            in_range = any(12000 <= p <= 61000 for _, p in pts[-24:]) if pts else False
            was_bought = any(s[0] == "BUY" and s[1] == ea_id for s in all_signals)

            hrs_at_max = (max_trend_ts - created).total_seconds() / 3600 if max_trend_ts and created else None
            hrs_at_20 = (first_20_ts - created).total_seconds() / 3600 if first_20_ts and created else None

            # Price at first_20 trigger
            trigger_price = None
            if first_20_ts:
                for ts, p in pts:
                    if ts >= first_20_ts:
                        trigger_price = p
                        break

            card_results.append({
                "ea_id": ea_id,
                "name": name,
                "max_trend": max_trend,
                "max_trend_hrs": hrs_at_max,
                "first_20_hrs": hrs_at_20,
                "first_20_trend": first_20_trend,
                "trigger_price": trigger_price,
                "in_promo": in_promo,
                "in_range": in_range,
                "was_bought": was_bought,
                "n_points": len(pts),
            })

        card_results.sort(key=lambda c: c["max_trend"] if c["max_trend"] is not None else -999, reverse=True)

        print(f"  {'Card':<25} {'MaxTrend':>9} {'@hrs':>6} {'1st20%':>7} {'@hrs':>6} {'TrigPrc':>8} {'Promo':>5} {'Range':>5} {'Bought':>6} {'Pts':>4}")
        print(f"  {'-'*95}")
        for c in card_results:
            mt = f"{c['max_trend']:+.0%}" if c["max_trend"] is not None else "n/a"
            mt_h = f"{c['max_trend_hrs']:.0f}" if c["max_trend_hrs"] else "?"
            f20 = f"{c['first_20_trend']:+.0%}" if c["first_20_trend"] is not None else "-"
            f20_h = f"{c['first_20_hrs']:.0f}" if c["first_20_hrs"] else "-"
            tp = f"{c['trigger_price']:,}" if c["trigger_price"] else "-"
            pr = "Y" if c["in_promo"] else "n"
            ir = "Y" if c["in_range"] else "n"
            wb = "YES" if c["was_bought"] else "-"
            print(f"  {c['name'][:24]:<25} {mt:>9} {mt_h:>6} {f20:>7} {f20_h:>6} {tp:>8} {pr:>5} {ir:>5} {wb:>6} {c['n_points']:>4}")

    # Final PnL
    print(f"\n{'='*120}")
    print(f"Final PnL: {portfolio.cash - 1_000_000:+,} | Trades: {len(portfolio.trades)}")
    for t in portfolio.trades:
        name = names.get(t.ea_id, str(t.ea_id))
        print(f"  {name[:25]:<26} {t.quantity}x  buy {t.buy_price:>7,} -> sell {t.sell_price:>7,}  pnl {t.net_profit:>+8,}  ({t.buy_time.strftime('%m-%d %H:%M')} -> {t.sell_time.strftime('%m-%d %H:%M')})")

    await engine.dispose()

asyncio.run(main())
