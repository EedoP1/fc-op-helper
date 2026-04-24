"""Iter 78 pre-analysis: count promo-dip-catch opportunities & check overlap."""
from __future__ import annotations
import asyncio
import json
import statistics
from collections import defaultdict
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from src.config import DATABASE_URL


PROMO_FRESH = {
    "fut birthday",
    "fantasy ut",
    "fantasy ut hero",
    "future stars",
    "fof: answer the call",
    "star performer",
    "knockout royalty icon",
    "festival of football: captains",
    "ultimate scream",
    "fc pro live",
}
RATING_LO, RATING_HI = 88, 91
DRAW_72H = 0.40
DRAW_168H = 0.50
LC_FLOOR = 15  # listings count >=15 in 24h average


async def main():
    eng = create_async_engine(DATABASE_URL, pool_size=2)
    async with eng.connect() as c:
        # Players whitelist
        r = await c.execute(text("SELECT ea_id, rating, card_type FROM players"))
        attrs = {}
        for row in r.fetchall():
            ea, rating, ctype = int(row[0]), int(row[1] or 0), (row[2] or "").lower()
            attrs[ea] = (rating, ctype)
        wl = {
            ea for ea, (rt, ct) in attrs.items()
            if RATING_LO <= rt <= RATING_HI and ct in PROMO_FRESH
        }
        print(f"[wl] {len(wl)} promo-fresh 88-91 cards in DB")

        # Snapshot date range
        r = await c.execute(text(
            "SELECT MIN(captured_at), MAX(captured_at) FROM market_snapshots"
        ))
        lo, hi = r.fetchone()
        print(f"[snap] range: {lo} .. {hi}")

        # Pull hourly median price + listings count for whitelist cards
        # Build per-card hourly series limited to whitelist for memory.
        if not wl:
            print("[abort] empty whitelist")
            await eng.dispose()
            return

        wl_list = list(wl)
        # batches of 500
        history: dict[int, list[tuple[datetime, float, float]]] = defaultdict(list)
        for i in range(0, len(wl_list), 500):
            batch = wl_list[i:i+500]
            qry = text(
                "SELECT ea_id, captured_at, current_lowest_bin, listing_count "
                "FROM market_snapshots WHERE ea_id = ANY(:ids) "
                "ORDER BY ea_id, captured_at"
            )
            r = await c.execute(qry, {"ids": batch})
            for row in r.fetchall():
                ea, t, p, lc = int(row[0]), row[1], row[2], row[3]
                if p is None or p <= 0:
                    continue
                history[ea].append((t, float(p), float(lc or 0)))
        n_cards = len(history)
        print(f"[hist] {n_cards} cards have any snapshots")

    await eng.dispose()

    # Bucket to hourly (use raw — already ~hourly).
    # For each card, walk forward; at each ts compute drawdown_from_max_72h, _168h, lc_avg_24h.
    # Daily snapshot: sample 12:00 UTC only.
    promo_fires = []  # (ea_id, ts, price, dd72, dd168, lc24)
    for ea, series in history.items():
        # Sort by time
        series.sort(key=lambda x: x[0])
        ts_arr = [s[0] for s in series]
        p_arr = [s[1] for s in series]
        lc_arr = [s[2] for s in series]
        n = len(series)
        # iterate at indexes whose hour==12
        for i in range(n):
            ts = ts_arr[i]
            if ts.hour != 12:
                continue
            # 72h max & 168h max lookback
            t_72 = ts - timedelta(hours=72)
            t_168 = ts - timedelta(hours=168)
            t_24 = ts - timedelta(hours=24)
            max72 = 0.0
            max168 = 0.0
            lc_sum = 0.0
            lc_n = 0
            for j in range(i, -1, -1):
                tj = ts_arr[j]
                if tj < t_168:
                    break
                pj = p_arr[j]
                if tj >= t_168 and pj > max168:
                    max168 = pj
                if tj >= t_72 and pj > max72:
                    max72 = pj
                if tj >= t_24:
                    lc_sum += lc_arr[j]
                    lc_n += 1
            cur = p_arr[i]
            if cur <= 0:
                continue
            dd72 = 0.0 if max72 <= 0 else (max72 - cur) / max72
            dd168 = 0.0 if max168 <= 0 else (max168 - cur) / max168
            lc24 = lc_sum / lc_n if lc_n > 0 else 0.0
            if (dd72 >= DRAW_72H or dd168 >= DRAW_168H) and lc24 >= LC_FLOOR:
                promo_fires.append((ea, ts, cur, dd72, dd168, lc24))

    print(f"[fires] {len(promo_fires)} daily-12UTC fire events meeting gate")
    if promo_fires:
        prices = [x[2] for x in promo_fires]
        print(f"  price band: median={statistics.median(prices):.0f}  "
              f"p25={statistics.quantiles(prices,n=4)[0]:.0f}  "
              f"p75={statistics.quantiles(prices,n=4)[2]:.0f}  "
              f"min={min(prices):.0f}  max={max(prices):.0f}")
        ea_set = {x[0] for x in promo_fires}
        print(f"  unique cards: {len(ea_set)}")
        # how many >=11k tradable
        tradable = [x for x in promo_fires if x[2] >= 11000]
        print(f"  tradable (>=11k): {len(tradable)}")

    # Save
    out = {
        "n_fires": len(promo_fires),
        "n_unique_cards": len({x[0] for x in promo_fires}),
        "fires": [
            {"ea_id": x[0], "ts": x[1].isoformat(), "price": x[2],
             "dd72": x[3], "dd168": x[4], "lc24": x[5]}
            for x in promo_fires[:200]
        ],
    }
    with open(".planning/iter78_promo_dip_fires.json", "w") as f:
        json.dump(out, f, indent=2, default=str)
    print("[wrote] .planning/iter78_promo_dip_fires.json")

    # Profit join: for each fire, look forward up to 168h, exit at first hour
    # where price >= buy*1.25 (target) else at +168h close.
    pnl_records = []
    for ea, ts, buy, dd72, dd168, lc24 in promo_fires:
        series = history[ea]
        # find slice [ts, ts+168h]
        end_ts = ts + timedelta(hours=168)
        sell_price = None
        for tj, pj, _lc in series:
            if tj <= ts:
                continue
            if tj > end_ts:
                break
            if pj >= buy * 1.25:
                sell_price = pj
                break
        if sell_price is None:
            # exit at last available within window
            tail = [(tj, pj) for tj, pj, _ in series if ts < tj <= end_ts]
            if tail:
                sell_price = tail[-1][1]
            else:
                continue
        # pessimistic: buy at the price we saw (cur), sell at min within forward; tax 5%
        # use simple sell_price * 0.95 - buy
        net = sell_price * 0.95 - buy
        roi = net / buy
        pnl_records.append((ea, ts, buy, sell_price, net, roi))
    print(f"[pnl] {len(pnl_records)} forward-walk results")
    if pnl_records:
        wins = [r for r in pnl_records if r[4] > 0]
        rois = [r[5] for r in pnl_records]
        print(f"  win rate: {len(wins)/len(pnl_records):.1%}")
        print(f"  median ROI: {statistics.median(rois):.2%}")
        print(f"  mean ROI:   {statistics.mean(rois):.2%}")
        # estimate qty=8 cap -> notional ~100k per fire
        # predicted PnL using same sizing logic as v5
        total_pred = 0.0
        for ea, ts, buy, sell, net, roi in pnl_records:
            qty = max(1, min(8, int(100_000 // buy)))
            total_pred += net * qty
        print(f"  predicted summed PnL (qty cap 8 / $100k notional): ${total_pred:,.0f}")

    # Overlap check vs v5 + v19 trades
    with open("daily_trend_dip_v5_filtered_results.json") as f:
        v5 = json.load(f)[0]["trades"]
    with open("floor_buy_v19_filtered_results.json") as f:
        v19 = json.load(f)[0]["trades"]
    v5_ids = {t["ea_id"] for t in v5}
    v19_ids = {t["ea_id"] for t in v19}
    fire_ids = {x[0] for x in promo_fires}
    print(f"[overlap] v5={len(v5_ids)} v19={len(v19_ids)} fires={len(fire_ids)}")
    print(f"  fires AND v5:  {len(fire_ids & v5_ids)}  ({len(fire_ids & v5_ids)/max(len(fire_ids),1):.1%})")
    print(f"  fires AND v19: {len(fire_ids & v19_ids)} ({len(fire_ids & v19_ids)/max(len(fire_ids),1):.1%})")
    # Cooldown-aware fires: per ea_id, only fire once every 168h
    sorted_fires = sorted(promo_fires, key=lambda x: (x[0], x[1]))
    cooldown_fires = []
    last_fire_per_ea = {}
    for f in sorted_fires:
        ea, ts = f[0], f[1]
        last = last_fire_per_ea.get(ea)
        if last is None or (ts - last).total_seconds() / 3600 >= 168:
            cooldown_fires.append(f)
            last_fire_per_ea[ea] = ts
    print(f"[cooldown] dedup-per-card 168h: {len(cooldown_fires)} fires across {len({f[0] for f in cooldown_fires})} cards")
    pnl_cd = []
    for ea, ts, buy, dd72, dd168, lc24 in cooldown_fires:
        rec = next((r for r in pnl_records if r[0]==ea and r[1]==ts), None)
        if rec is None: continue
        pnl_cd.append(rec)
    if pnl_cd:
        wins = [r for r in pnl_cd if r[4]>0]
        rois = [r[5] for r in pnl_cd]
        print(f"[cooldown-pnl] win={len(wins)/len(pnl_cd):.1%} median_roi={statistics.median(rois):.2%}")
        total = 0.0
        for ea, ts, buy, sell, net, roi in pnl_cd:
            qty = max(1, min(8, int(100_000 // buy)))
            total += net * qty
        print(f"  predicted summed PnL with cooldown: ${total:,.0f}")
    # Engine cap: 8 slots, ~28 days, dailyfire => max ~30 trades sequenced
    # Estimate realistic by sampling top 60 fires
    top60 = sorted(pnl_cd, key=lambda r: -r[5])[:60]
    if top60:
        total60 = 0.0
        for ea, ts, buy, sell, net, roi in top60:
            qty = max(1, min(8, int(100_000 // buy)))
            total60 += net * qty
        print(f"  top-60 (engine-capacity-realistic) PnL: ${total60:,.0f}")


if __name__ == "__main__":
    asyncio.run(main())
