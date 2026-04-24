"""Pre-analysis sim for iter84 mid-band specialist."""
import asyncio, json, statistics
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
import sys
sys.path.insert(0, '.')
from src.config import DATABASE_URL

WHITELIST_CARD_TYPES = {
    "fut birthday", "fantasy ut", "fantasy ut hero", "future stars",
    "fof: answer the call", "star performer", "unbreakables",
    "unbreakables icon", "knockout royalty icon", "fc pro live",
    "festival of football: captains", "winter wildcards",
}
WHITELIST_RATINGS = {86, 87, 88, 89, 90, 91}


async def get_attrs():
    eng = create_async_engine(DATABASE_URL, pool_size=1)
    out = {}
    async with eng.connect() as c:
        r = await c.execute(text("SELECT ea_id, rating, card_type FROM players"))
        for row in r.fetchall():
            out[int(row[0])] = (int(row[1] or 0), (row[2] or '').lower())
    await eng.dispose()
    return out


async def get_history(ea_ids):
    eng = create_async_engine(DATABASE_URL, pool_size=1)
    hist = defaultdict(list)
    async with eng.connect() as c:
        for i in range(0, len(ea_ids), 100):
            chunk = ea_ids[i:i+100]
            placeholders = ','.join([f':id{j}' for j in range(len(chunk))])
            params = {f'id{j}': chunk[j] for j in range(len(chunk))}
            r = await c.execute(text(
                f"SELECT ea_id, captured_at, current_lowest_bin, listing_count "
                f"FROM market_snapshots WHERE ea_id IN ({placeholders}) "
                f"ORDER BY ea_id, captured_at"
            ), params)
            for row in r.fetchall():
                hist[int(row[0])].append((row[1], int(row[2] or 0), int(row[3] or 0)))
    await eng.dispose()
    return hist


def bucket_hourly(records):
    by_hour = {}
    for ts, price, lc in records:
        h = ts.replace(minute=0, second=0, microsecond=0, tzinfo=None)
        if h not in by_hour:
            by_hour[h] = (price, lc)
    return by_hour


def main():
    attrs = asyncio.run(get_attrs())
    whitelist = {ea for ea,(r,ct) in attrs.items() if r in WHITELIST_RATINGS and ct in WHITELIST_CARD_TYPES}
    print(f"Whitelist: {len(whitelist)}")
    hist = asyncio.run(get_history(list(whitelist)))
    print(f"Loaded history for {len(hist)} ea_ids")

    fires = []
    for ea in whitelist:
        records = hist.get(ea, [])
        if len(records) < 100:
            continue
        by_hour = bucket_hourly(records)
        hours = sorted(by_hour.keys())
        if not hours:
            continue
        cur = hours[0].replace(hour=0)
        end = hours[-1]
        while cur <= end:
            if cur.weekday() == 4:
                cur += timedelta(days=1)
                continue
            if cur not in by_hour:
                cur += timedelta(days=1)
                continue
            price, lc_now = by_hour[cur]
            if not (20000 <= price <= 50000):
                cur += timedelta(days=1)
                continue
            prices_72 = []
            lcs_24 = []
            for k in range(72):
                h = cur - timedelta(hours=k+1)
                if h in by_hour:
                    prices_72.append(by_hour[h][0])
                    if k < 24:
                        lcs_24.append(by_hour[h][1])
            if not prices_72:
                cur += timedelta(days=1)
                continue
            max_72 = max(prices_72)
            if max_72 <= 0:
                cur += timedelta(days=1)
                continue
            dd = 1 - price/max_72
            lc_avg = statistics.mean(lcs_24) if lcs_24 else 0
            if dd >= 0.25 and lc_avg >= 15:
                buy_price = price
                target_sell = buy_price * 1.20
                stop_level = buy_price * 0.75
                consec_breach = 0
                sell_price = None
                sell_h = None
                for k in range(1, 145):
                    h = cur + timedelta(hours=k)
                    if h not in by_hour:
                        continue
                    p_h = by_hour[h][0]
                    if p_h <= 0:
                        continue
                    if p_h >= target_sell:
                        sell_price = p_h
                        sell_h = k
                        break
                    if p_h <= stop_level:
                        consec_breach += 1
                        if consec_breach >= 14:
                            sell_price = p_h
                            sell_h = k
                            break
                    else:
                        consec_breach = 0
                if sell_price is None:
                    for k in range(144, 0, -1):
                        h = cur + timedelta(hours=k)
                        if h in by_hour:
                            sell_price = by_hour[h][0]
                            sell_h = k
                            break
                    if sell_price is None:
                        cur += timedelta(days=1)
                        continue
                net = sell_price * 0.95 - buy_price
                roi = net / buy_price
                fires.append({
                    'ea_id': ea, 'day': cur.strftime('%Y-%m-%d'),
                    'buy_price': buy_price, 'sell_price': sell_price,
                    'net': net, 'roi': roi, 'hold': sell_h,
                })
            cur += timedelta(days=1)

    print(f"\nTotal gate fires: {len(fires)}")
    if not fires:
        return
    rois = [f['roi'] for f in fires]
    nets = [f['net'] for f in fires]
    holds = [f['hold'] for f in fires]
    wins = sum(1 for n in nets if n > 0)
    print(f"  Win rate: {100*wins/len(fires):.1f}%")
    print(f"  Mean ROI: {statistics.mean(rois):.3f}")
    print(f"  Median ROI: {statistics.median(rois):.3f}")
    print(f"  Median hold: {statistics.median(holds):.0f}h")
    print(f"  Mean net @ qty1: ${statistics.mean(nets):.0f}")

    # Slot-aware sim: 8 slots, $125k notional
    fires_by_day = defaultdict(list)
    for f in fires:
        fires_by_day[f['day']].append(f)
    realised_net = 0
    realised_trades = 0
    realised_wins = 0
    slots_in_use = []
    for d in sorted(fires_by_day.keys()):
        slots_in_use = [r for r in slots_in_use if r > d]
        opps = sorted(fires_by_day[d], key=lambda f: -f['roi'])
        for f in opps:
            if len(slots_in_use) >= 8:
                break
            qty = max(1, 125000 // f['buy_price'])
            realised_net += f['net'] * qty
            realised_trades += 1
            if f['net'] > 0:
                realised_wins += 1
            d_release = (datetime.strptime(d, '%Y-%m-%d') + timedelta(hours=f['hold'])).strftime('%Y-%m-%d')
            slots_in_use.append(d_release)
    print(f"\nRealised PnL @ 8-slot/$125k cap: ${realised_net:,.0f}")
    print(f"  Trades: {realised_trades}, win {100*realised_wins/max(1,realised_trades):.1f}%")

    # Tighter gate (dd>=0.30) test
    print(f"\n--- dd>=0.30 ---")
    f30 = [f for f in fires if (1 - f['buy_price']/(f['buy_price']/(1-0.30) if False else f['buy_price']))]
    # Just count: dd was already >=0.25, can compute looser - need to recheck threshold
    # Skip - main result above is enough


if __name__ == "__main__":
    main()
