import json, os
from collections import Counter

d = json.load(open('.planning/profit_opportunities.json'))
opps = d['pessimistic']
lh = [o for o in opps if 96 <= o['hold_hours'] <= 168]
lh_ids = set(o['ea_id'] for o in lh)
print('long-hold unique ea_ids:', len(lh_ids))

stack_files = ['mid_dip_v2_filtered_results.json','low_dip_v3_filtered_results.json',
               'floor_buy_v19_ext_filtered_results.json','floor_buy_v19_filtered_results.json',
               'post_dump_v15_filtered_results.json','promo_dip_buy_filtered_results.json']
for f in stack_files:
    if not os.path.exists(f):
        print(f, 'MISSING'); continue
    r = json.load(open(f))
    ts = r[0]['trades'] if isinstance(r,list) else r.get('trades',[])
    eaids = set(int(t['ea_id']) for t in ts if 'ea_id' in t)
    inter = lh_ids & eaids
    print(f'{f}: {len(ts)} trades, {len(eaids)} unique ea, overlap with long-hold ea pool: {len(inter)}/{len(lh_ids)} = {len(inter)/max(1,len(lh_ids))*100:.1f}%')
    # find LH opps for these ea_ids
    if inter:
        match_opps = [o for o in lh if o['ea_id'] in inter]
        print(f'  -> covers {len(match_opps)} LH opps')

# now: among LH opps in $10-13k & $13-20k, how many are NOT in v19_ext stack?
v19 = json.load(open('floor_buy_v19_ext_filtered_results.json'))
v19_ts = v19[0]['trades'] if isinstance(v19,list) else v19.get('trades',[])
v19_eaids = set(int(t['ea_id']) for t in v19_ts)
not_in_v19 = [o for o in lh if o['ea_id'] not in v19_eaids]
print(f'\nLH opps NOT covered by v19_ext: {len(not_in_v19)}/{len(lh)}')
print('band dist not-in-v19:', Counter(o['price_band'] for o in not_in_v19).most_common())

# v19_ext also limits to $10-13k. So mid+ ($20k+) and Mon entries at higher bands are OPEN.
mid_plus = [o for o in not_in_v19 if o['price_band'] in ('$20-50k','$50-100k','$100k+')]
print(f'\nMID+ band ($20k+), not in v19, long-hold: {len(mid_plus)}')
print('week:', Counter(o['week'] for o in mid_plus).most_common())
print('day:', Counter(__import__('datetime').datetime.fromisoformat(o['buy_hour']).strftime('%a') for o in mid_plus).most_common())
import statistics
print('median ROI:', statistics.median([o['roi_net'] for o in mid_plus]))
print('median buy:', statistics.median([o['buy_price'] for o in mid_plus]))
print('median sph:', statistics.median([o['liquidity_sph'] for o in mid_plus]))
print('hold dist:', Counter(int(o['hold_hours']/24) for o in mid_plus).most_common())
