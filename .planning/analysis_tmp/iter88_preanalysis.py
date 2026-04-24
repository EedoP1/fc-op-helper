"""Pre-analysis for iter 88 — widen low_dip_v1 gates.

For each $13-20k band catalog opp (whitelist+sph>=2):
  - Compute lc_avg_24h and dd_72h at buy_hour using DB market_snapshots
  - Identify which were captured by low_dip_v1
  - Test 4 widening variants and predict their PnL
"""
from __future__ import annotations
import json, statistics
from collections import defaultdict
from datetime import datetime, timedelta
import asyncio

WHITE_TYPES = {
    "fut birthday", "fantasy ut", "fantasy ut hero", "future stars",
    "fof: answer the call", "star performer", "unbreakables",
    "unbreakables icon", "knockout royalty icon", "fc pro live",
    "festival of football: captains", "winter wildcards",
}
WHITE_RATINGS = {86, 87, 88, 89, 90, 91}


async def load_attrs():
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from src.config import DATABASE_URL
    eng = create_async_engine(DATABASE_URL, pool_size=1)
    out = {}
    async with eng.connect() as c:
        r = await c.execute(text("SELECT ea_id, rating, card_type FROM players"))
        for row in r.fetchall():
            out[int(row[0])] = (int(row[1] or 0), (row[2] or "").lower())
    await eng.dispose()
    return out


async def load_market_for_eas(ea_ids: set[int]):
    """Load (ea_id, hour) -> (price, listing_count) from snapshots."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from src.config import DATABASE_URL
    eng = create_async_engine(DATABASE_URL, pool_size=1)
    rows: dict[tuple[int, datetime], tuple[int, int]] = {}
    async with eng.connect() as c:
        # Chunk to avoid huge IN
        ea_list = list(ea_ids)
        chunk = 200
        for i in range(0, len(ea_list), chunk):
            sub = ea_list[i:i+chunk]
            placeholders = ",".join(str(int(x)) for x in sub)
            sql = (
                "SELECT ea_id, "
                "       date_trunc('hour', captured_at) AS h, "
                "       percentile_disc(0.5) WITHIN GROUP (ORDER BY current_lowest_bin) AS price, "
                "       AVG(COALESCE(listing_count, 0))::int AS lc "
                f"FROM market_snapshots WHERE ea_id IN ({placeholders}) "
                "GROUP BY ea_id, date_trunc('hour', captured_at)"
            )
            r = await c.execute(text(sql))
            for row in r.fetchall():
                ea = int(row[0])
                h = row[1]
                if hasattr(h, 'tzinfo') and h.tzinfo:
                    h = h.replace(tzinfo=None)
                rows[(ea, h)] = (int(row[2] or 0), int(row[3] or 0))
    await eng.dispose()
    return rows


def compute_features(market, ea_id, buy_hour: datetime):
    """Compute (lc_avg_24h, dd_72h, smooth_3h, in_band) at buy_hour."""
    bh = buy_hour.replace(tzinfo=None) if buy_hour.tzinfo else buy_hour
    bh = bh.replace(minute=0, second=0, microsecond=0)
    # lc_avg_24h: hours -1..-24
    lc_vals = []
    for k in range(1, 25):
        v = market.get((ea_id, bh - timedelta(hours=k)))
        if v is not None:
            lc_vals.append(v[1])
    lc_avg = sum(lc_vals)/len(lc_vals) if lc_vals else 0.0
    # smooth_3h: median of price at -2, -1, 0
    sm_vals = []
    for k in range(0, 3):
        v = market.get((ea_id, bh - timedelta(hours=k)))
        if v is not None and v[0] > 0:
            sm_vals.append(v[0])
    sm_vals.sort()
    smooth = sm_vals[len(sm_vals)//2] if sm_vals else 0
    # dd_72h
    win = []
    for k in range(0, 72):
        v = market.get((ea_id, bh - timedelta(hours=k)))
        if v is not None and v[0] > 0:
            win.append(v[0])
    if not win or smooth <= 0:
        return (lc_avg, 0.0, smooth, len(win))
    wmax = max(win)
    dd = 1.0 - smooth/wmax if wmax > 0 else 0.0
    return (lc_avg, dd, smooth, len(win))


async def main():
    opps = json.load(open('.planning/profit_opportunities.json'))['pessimistic']
    v1 = json.load(open('low_dip_v1_filtered_results.json'))[0]
    v1_trades = v1['trades']
    v1_keys = {(int(t['ea_id']), datetime.fromisoformat(str(t['buy_time']).replace('Z','+00:00')).date().isoformat()) for t in v1_trades}
    print(f"v1 trades: {len(v1_trades)} unique (ea,day): {len(v1_keys)}")

    attrs = await load_attrs()
    print(f"attrs: {len(attrs)}")

    def in_white(ea):
        a = attrs.get(ea)
        if not a: return False
        return a[0] in WHITE_RATINGS and a[1] in WHITE_TYPES

    band = [o for o in opps if 13000 <= o['buy_price'] <= 20000 and o['liquidity_sph'] >= 2 and in_white(o['ea_id'])]
    print(f"band opps: {len(band)}")

    eas = {o['ea_id'] for o in band}
    print(f"unique EAs: {len(eas)}")

    market = await load_market_for_eas(eas)
    print(f"market hours loaded: {len(market)}")

    # Compute features for each opp
    enriched = []
    for o in band:
        bh = datetime.fromisoformat(o['buy_hour'])
        lc, dd, sm, nw = compute_features(market, int(o['ea_id']), bh)
        wd = bh.weekday()  # 0=Mon
        day = bh.date().isoformat()
        captured = (int(o['ea_id']), day) in v1_keys
        enriched.append({
            'ea_id': int(o['ea_id']),
            'buy_hour': bh,
            'wd': wd,
            'day': day,
            'roi_net': float(o['roi_net']),
            'buy_price': float(o['buy_price']),
            'hold_h': float(o['hold_hours']),
            'lc_avg_24h': lc,
            'dd_72h': dd,
            'smooth': sm,
            'window_size': nw,
            'captured_v1': captured,
        })

    # 1. uncovered analysis (Tue/Wed/Thu only — same day filter as v1)
    twth = [e for e in enriched if e['wd'] in (1,2,3)]
    not_cap = [e for e in twth if not e['captured_v1']]
    print()
    print(f"Tue/Wed/Thu band opps: {len(twth)}")
    print(f"  captured by v1: {sum(1 for e in twth if e['captured_v1'])}")
    print(f"  NOT captured: {len(not_cap)}")

    # Of NOT captured: lc_avg<15? dd_72h<0.20?
    nc_lc_lt15 = [e for e in not_cap if e['lc_avg_24h'] < 15]
    nc_dd_lt20 = [e for e in not_cap if e['dd_72h'] < 0.20]
    nc_lc1015 = [e for e in not_cap if 10 <= e['lc_avg_24h'] < 15]
    nc_dd1520 = [e for e in not_cap if 0.15 <= e['dd_72h'] < 0.20]
    print(f"  of NOT-captured, lc_avg < 15: {len(nc_lc_lt15)}  (10..15: {len(nc_lc1015)})")
    print(f"  of NOT-captured, dd_72h < 0.20: {len(nc_dd_lt20)}  (0.15..0.20: {len(nc_dd1520)})")

    # Predicted gate hits per variant. Apply same other-gates as v1:
    #   - in band, sm in band, dd_window has data, etc. But we built features
    #     after price>0 + sm>0 + window has data, so only filter is lc/dd here.
    def gate(e, lc_min, dd_min):
        if e['smooth'] <= 0 or e['window_size'] < 24:
            return False
        if not (13000 <= e['smooth'] <= 20000):
            return False
        return e['lc_avg_24h'] >= lc_min and e['dd_72h'] >= dd_min

    variants = [
        ('v1 baseline (15, 0.20)', 15, 0.20),
        ('A: lc>=10, dd>=0.20', 10, 0.20),
        ('B: lc>=15, dd>=0.15', 15, 0.15),
        ('C: lc>=10, dd>=0.15', 10, 0.15),
        ('D: lc>=12, dd>=0.18', 12, 0.18),
    ]

    print()
    print("VARIANT PREDICTIONS (Tue/Wed/Thu, $13-20k, whitelist, sph>=2):")
    print(f"  {'variant':<28}  {'fires':>5}  {'wins':>5}  {'win%':>6}  {'med_roi%':>9}  {'pred_PnL':>10}")
    for label, lc_min, dd_min in variants:
        # Fires = opps that pass gate. This is an upper bound on what strategy
        # would attempt, and since each "opp" is a realized profitable window,
        # firing on it ≈ capturing the win.
        fires = [e for e in twth if gate(e, lc_min, dd_min)]
        # But we want win-rate prediction including misses. The catalog has
        # only profitable opps — so a fire that matches a catalog opp is a win.
        # For non-catalog fires we'd need a separate enumeration. Approximate:
        # use v1's empirical capture-vs-fire ratio on the captured set.
        nfires = len(fires)
        nwins = len(fires)  # all catalog opps that pass gate
        # PnL approximation: notional × (roi_net - drag). Drag captured in
        # v1 as 9-10% (pessimistic loader). Use exit recipe cap.
        # v1 trades cap profit at 20% target; many opps in catalog have higher
        # roi_net but our exit takes 20%. Take min(roi_net, 0.20) as effective.
        # Net PnL per trade = qty*buy_price * min(roi_net,0.20) * 0.95 (tax).
        # qty ≈ notional_per_trade / buy_price = 125k / buy_price, capped 8.
        pnl = 0.0
        for e in fires:
            qty = min(8, max(1, int(125_000 / max(1, e['buy_price']))))
            notional = qty * e['buy_price']
            eff_roi = min(e['roi_net'], 0.20)
            net = notional * eff_roi * 0.95  # 5% tax on sell
            pnl += net
        med_roi = statistics.median([e['roi_net']*100 for e in fires]) if fires else 0
        win_pct = 100.0 * nwins / max(1, nfires)
        print(f"  {label:<28}  {nfires:>5}  {nwins:>5}  {win_pct:>5.1f}%  {med_roi:>8.1f}%  ${pnl/1000:>8.0f}k")

    # Critical: catalog only contains profitable windows — strategy will also
    # fire on losing setups not in catalog. Estimate empirical win-rate by
    # looking at v1's actual win rate (60.97%) vs how many of v1's BUYS hit
    # the catalog (= captured ones). v1 had 41 trades, win_rate 0.61, so
    # ~25 wins. v1 captured X of band-opps.
    captured_v1_count = sum(1 for e in twth if e['captured_v1'])
    print()
    print(f"v1 captured catalog opps (Tue/Wed/Thu band): {captured_v1_count}")
    print(f"v1 total trades: {len(v1_trades)} -> catalog precision proxy: {captured_v1_count/max(1,len(v1_trades)):.1%}")
    # If v1 fires/captures ratio = X, then for new variant, predicted_trades ≈ fires/X
    # and predicted_wins use same ~61% win rate
    if captured_v1_count > 0:
        v1_fires_per_catch = len(v1_trades) / captured_v1_count
        print(f"v1 fires-per-catalog-catch: {v1_fires_per_catch:.2f}")

    # Recompute realistic PnL: predict trades = fires * (1/captured_ratio_v1) doesn't work
    # because looser gates fire on more weakened candidates with worse win rate.
    # Use v1's actual P&L per catalog catch as empirical anchor.
    v1_pnl_per_catch = v1['total_pnl'] / max(1, captured_v1_count)
    print(f"v1 PnL per catalog-catch: ${v1_pnl_per_catch:.0f}")

    print()
    print("PNL FORECAST (using v1's $/catch anchor, scaled by predicted catches):")
    for label, lc_min, dd_min in variants:
        fires = [e for e in twth if gate(e, lc_min, dd_min)]
        # Catalog opps that pass gate ≈ predicted catalog-catches
        catches = len(fires)
        pred_pnl = catches * v1_pnl_per_catch
        # But looser gates also include weaker (lower ROI) opps. Discount by
        # ratio of new-opp medROI to v1-baseline-opp medROI.
        baseline_fires = [e for e in twth if gate(e, 15, 0.20)]
        if baseline_fires and fires:
            base_med = statistics.median([e['roi_net'] for e in baseline_fires])
            new_med = statistics.median([e['roi_net'] for e in fires])
            quality = new_med / base_med if base_med > 0 else 1.0
        else:
            quality = 1.0
        pred_pnl_adj = pred_pnl * quality
        print(f"  {label:<28}  catches={catches:3d}  raw=${pred_pnl/1000:>5.0f}k  qual={quality:.2f}  adj=${pred_pnl_adj/1000:>5.0f}k")


if __name__ == '__main__':
    asyncio.run(main())
