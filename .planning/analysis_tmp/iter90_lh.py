import json
import statistics
from collections import Counter
from datetime import datetime

d = json.load(open('.planning/profit_opportunities.json'))
opps = d['pessimistic']
lh = [o for o in opps if 96 <= o['hold_hours'] <= 168]
fast = [o for o in opps if o['hold_hours'] < 24]
mid = [o for o in opps if 24 <= o['hold_hours'] < 96]
print('total pessim:', len(opps))
print('long-hold (96-168h):', len(lh))
print('mid (24-96h):', len(mid))
print('fast (<24h):', len(fast))

def stats(rows, label):
    if not rows: return
    rois = [o['roi_net'] for o in rows]
    bps = [o['buy_price'] for o in rows]
    sphs = [o['liquidity_sph'] for o in rows]
    print(f'\n{label} (n={len(rows)}):')
    print(f'  ROI net  median={statistics.median(rois):.3f} mean={statistics.mean(rois):.3f} p25={sorted(rois)[len(rois)//4]:.3f} p75={sorted(rois)[3*len(rois)//4]:.3f}')
    print(f'  buy      median=${statistics.median(bps):,.0f} mean=${statistics.mean(bps):,.0f}')
    print(f'  sph      median={statistics.median(sphs):.2f}')

stats(lh, 'LONG-HOLD')
stats(mid, 'MID')
stats(fast, 'FAST')

print('\n-- price band --')
print('long-hold:', Counter(o['price_band'] for o in lh).most_common())
print('fast:     ', Counter(o['price_band'] for o in fast).most_common())

print('\n-- week --')
print('long-hold:', sorted(Counter(o['week'] for o in lh).items()))

print('\n-- buy day-of-week --')
def dow(o):
    try: return datetime.fromisoformat(o['buy_hour']).strftime('%a')
    except: return '?'
print('long-hold:', Counter(dow(o) for o in lh).most_common())
print('fast:     ', Counter(dow(o) for o in fast).most_common())

print('\n-- buy hour-of-day --')
def hod(o):
    try: return datetime.fromisoformat(o['buy_hour']).hour
    except: return -1
print('long-hold hod:', sorted(Counter(hod(o) for o in lh).items()))

# unique players in lh
print('\n-- ea_id uniqueness --')
print('long-hold unique ea_ids:', len(set(o['ea_id'] for o in lh)), '/', len(lh))
print('fast       unique ea_ids:', len(set(o['ea_id'] for o in fast)), '/', len(fast))

# overlap with mid_dip_v2 + low_dip_v3 + floor_buy_v19_ext
import os
def load_trades(p):
    if not os.path.exists(p): return []
    try:
        r = json.load(open(p))
        return r.get('trades', []) or r.get('all_trades', []) or []
    except: return []

stack_files = ['mid_dip_v2_filtered_results.json','low_dip_v3_filtered_results.json',
               'floor_buy_v19_ext_filtered_results.json','floor_buy_v19_filtered_results.json',
               'post_dump_v15_filtered_results.json','promo_dip_buy_filtered_results.json']
stack_keys = set()
for f in stack_files:
    ts = load_trades(f)
    for t in ts:
        ea = t.get('ea_id') or t.get('player_ea_id') or t.get('eaId')
        if ea is not None:
            stack_keys.add(int(ea))
print('\nstack ea_id pool:', len(stack_keys))
lh_ids = set(o['ea_id'] for o in lh)
print('long-hold ∩ stack ea_ids:', len(lh_ids & stack_keys), '/', len(lh_ids))
