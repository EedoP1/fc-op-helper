---
status: awaiting_human_verify
trigger: "resolve_outcomes matches disappeared listings to completed sales by price only, without checking sale timestamps"
created: 2026-03-27T00:00:00Z
updated: 2026-03-27T00:01:00Z
---

## Current Focus

hypothesis: price-only matching in resolve_outcomes counts completed sales that predate the observation window, inflating OP sell rates on bootstrap/fresh-start
test: read resolve_outcomes logic, trace bootstrap path, confirm stale sales are included
expecting: lines 251-262 build sale_count_by_price with no timestamp guard; bootstrap case at 235-238 keeps ALL sales when last_resolved_at is None
next_action: apply fix — filter by sold_at >= min(first_seen_at) per price bucket; remove unguarded bootstrap pass-through

## Symptoms

expected: Only sales where sold_at >= the earliest first_seen_at of the disappeared observations at that price should count as matching.
actual: Laurienté 65k listing first_seen_at=21:15 UTC matched to completedAuction sale sold_at=18:31 UTC. Similarly 49,750 (sold ~15:26-16:26) and 49,500 (sold ~17:15) were matched. Result: false 3/4 OP sell rate at 40% margin.
errors: No runtime errors — silent data corruption producing inflated OP sell rates.
reproduction: |
  1. Fresh server start — scanner first observes listings
  2. Listings disappear by next scan
  3. resolve_outcomes finds price matches in completedAuctions (100 most recent, spanning ~10 hrs)
  4. last_resolved_at=None (bootstrap) so ALL completedAuctions are kept (line 235-238)
  5. Price-only matching (lines 251-262) assigns old sales to newly-observed listings
started: every fresh server start / bootstrap

## Eliminated

- hypothesis: Bug is in scorer_v2, not listing_tracker
  evidence: resolve_outcomes writes the outcome="sold" to ListingObservation rows; scorer just reads them. The incorrect "sold" label originates in resolve_outcomes.
  timestamp: 2026-03-27T00:00:00Z

## Evidence

- timestamp: 2026-03-27T00:00:00Z
  checked: listing_tracker.py lines 233-239
  found: |
    Bootstrap case: when last_resolved_at is None, completed_sales list is passed through unfiltered.
    All 100 completed sales (spanning ~10 hours historically) are available for matching.
  implication: A listing first seen at 21:15 can be matched to a sale at 18:31 — 2h44m before first observation.

- timestamp: 2026-03-27T00:00:00Z
  checked: listing_tracker.py lines 251-262
  found: |
    sale_count_by_price aggregates ALL remaining completed_sales with no timestamp filter.
    Matching loop uses only price as the key: sale_count_by_price.get(price, 0).
    No check against obs.first_seen_at whatsoever.
  implication: Any historical sale at the same price is counted as evidence the listing sold.

- timestamp: 2026-03-27T00:00:00Z
  checked: SaleRecord model (src/models.py line 29-33)
  found: SaleRecord has sold_at: datetime field. ListingObservation has first_seen_at: datetime field.
  implication: All required timestamps are available; fix is purely logic in resolve_outcomes.

## Resolution

root_cause: |
  resolve_outcomes performs price-only matching when attributing completed sales to disappeared listings.
  Two compounding flaws:
  1. Bootstrap path (last_resolved_at=None) keeps ALL completed sales with no lower-bound cutoff,
     including sales from hours before the scanner ever observed those listings.
  2. Per-price matching counts any sale at that price regardless of when it occurred relative
     to the observation window. A sale at 18:31 is counted for a listing first seen at 21:15.

fix: |
  Instead of building a simple count dict (sale_count_by_price), build a dict of sorted sale
  timestamps per price (sale_times_by_price). When matching per price bucket, count only sales
  where sold_at >= min(obs.first_seen_at for obs in obs_list). This ensures only sales that
  could have happened during or after the observation window are matched.
  The last_resolved_at filter is kept for its original purpose (inter-scan dedup) but is no
  longer the sole temporal guard.

verification: "Code review complete. Fix applied and reviewed. Awaiting human confirmation that live scoring no longer inflates OP sell rates on fresh start."
files_changed:
  - src/server/listing_tracker.py
