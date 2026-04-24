"""Pre-analysis for iter 86 — $13-20k band specialist."""
import json, statistics
from collections import defaultdict
from datetime import datetime

opps = json.load(open('.planning/profit_opportunities.json'))['pessimistic']

WHITE_TYPES = {"fut birthday","fantasy ut","fantasy ut hero","future stars","fof: answer the call","star performer","unbreakables","unbreakables icon","knockout royalty icon","fc pro live","festival of football: captains","winter wildcards"}
WHITE_RATINGS = {86,87,88,89,90,91}

attrs = {}
try:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from src.config import DATABASE_URL
    import asyncio
    async def _r():
        eng = create_async_engine(DATABASE_URL, pool_size=1)
        out = {}
        async with eng.connect() as c:
            r = await c.execute(text("SELECT ea_id, rating, card_type FROM players"))
            for row in r.fetchall():
                out[int(row[0])] = (int(row[1] or 0), (row[2] or "").lower())
        await eng.dispose()
        return out
    attrs = asyncio.run(_r())
except Exception as e:
    print('DB load err:', e)
print('attrs loaded:', len(attrs))

def in_white(ea):
    a = attrs.get(ea)
    if not a:
        return False
    return a[0] in WHITE_RATINGS and a[1] in WHITE_TYPES

band_opps = [o for o in opps if 13000 <= o['buy_price'] <= 20000 and o['liquidity_sph'] >= 2 and in_white(o['ea_id'])]
print('total band opps (whitelist+sph>=2):', len(band_opps))

per_wk = defaultdict(list)
for o in band_opps:
    per_wk[o['week']].append(o)
for w in sorted(per_wk):
    rs = per_wk[w]
    rois = [x['roi_net']*100 for x in rs]
    holds = [x['hold_hours'] for x in rs]
    notional = sum(x['buy_price'] for x in rs)
    print(f'  {w}: n={len(rs)} medROI={statistics.median(rois):.1f}% medHold={statistics.median(holds):.0f}h notional=${notional/1000:.0f}k')

rois_all = [x['roi_net']*100 for x in band_opps]
holds_all = [x['hold_hours'] for x in band_opps]
print(f'overall medROI={statistics.median(rois_all):.1f}% medHold={statistics.median(holds_all):.0f}h')

dow = defaultdict(list)
for o in band_opps:
    bt = datetime.fromisoformat(o['buy_hour'])
    dow[bt.weekday()].append(o)
names = ['Mon','Tue','Wed','Thu','Fri','Sat','Sun']
print()
print('By buy weekday:')
for d in range(7):
    rs = dow[d]
    if rs:
        med = statistics.median([x['roi_net']*100 for x in rs])
        print(f'  {names[d]}: n={len(rs)} medROI={med:.1f}%')

# Overlap with stack: load each strategy's filtered_results, extract (ea_id,buy_day) pairs
def extract_trades(path):
    try:
        d = json.load(open(path))
    except Exception:
        return set()
    if isinstance(d, list) and d:
        d = d[0]
    trades = d.get('trades') or d.get('positions') or []
    out = set()
    for t in trades:
        ea = t.get('ea_id')
        bt = t.get('buy_time') or t.get('entry_time') or t.get('buy_ts')
        if ea is None or bt is None:
            continue
        try:
            day = datetime.fromisoformat(str(bt).replace('Z','+00:00')).date().isoformat()
        except Exception:
            day = str(bt)[:10]
        out.add((int(ea), day))
    return out

stack_files = [
    'floor_buy_v19_filtered_results.json',
    'floor_buy_v19_ext_filtered_results.json',
    'floor_buy_v24_filtered_results.json',
    'daily_trend_dip_v5_filtered_results.json',
    'post_dump_v15_filtered_results.json',
    'monday_rebound_v1_filtered_results.json',
    'mid_dip_v2_filtered_results.json',
]

stack_buys = {}
for sf in stack_files:
    s = extract_trades(sf)
    stack_buys[sf] = s
    print(f'  {sf}: trades={len(s)}')

# Build set of (ea_id, day) for our band opps
band_set = set()
for o in band_opps:
    bt = datetime.fromisoformat(o['buy_hour'])
    band_set.add((int(o['ea_id']), bt.date().isoformat()))
print()
print(f'unique (ea,day) band opps: {len(band_set)}')

# overlap
all_stack = set()
for sf, s in stack_buys.items():
    o = band_set & s
    pct = 100.0 * len(o) / max(1, len(band_set))
    print(f'  overlap with {sf}: {len(o)} / {len(band_set)} = {pct:.1f}%')
    all_stack |= s

ov_all = band_set & all_stack
print(f'\nUNION overlap (any stack member): {len(ov_all)}/{len(band_set)} = {100.0*len(ov_all)/max(1,len(band_set)):.1f}%')

# uncovered
uncovered = band_set - all_stack
print(f'UNCOVERED opps: {len(uncovered)}')

# Predicted PnL (uncovered notional × precision × (medROI - 9% drag))
unc_opps = [o for o in band_opps if (int(o['ea_id']), datetime.fromisoformat(o['buy_hour']).date().isoformat()) in uncovered]
unc_rois = [x['roi_net']*100 for x in unc_opps]
unc_notional = sum(x['buy_price'] for x in unc_opps)
print(f'UNCOVERED medROI={statistics.median(unc_rois):.1f}% notional=${unc_notional/1000:.0f}k count={len(unc_opps)}')

# Per-day uncovered breakdown
unc_dow = defaultdict(int)
for o in unc_opps:
    bt = datetime.fromisoformat(o['buy_hour'])
    unc_dow[bt.weekday()] += 1
print('UNCOVERED by weekday:')
for d in range(7):
    if unc_dow[d]:
        print(f'  {names[d]}: n={unc_dow[d]}')
