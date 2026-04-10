---
status: awaiting_human_verify
trigger: "healthcheck-listing-overlap — overlap very low (8-40%) between resolved listings and FUTBIN completed sales"
created: 2026-03-27T00:00:00
updated: 2026-03-27T00:01:00
---

## Current Focus

hypothesis: CONFIRMED. The overlap_cutoff was computed as max(fb_earliest, our_earliest_first_seen_at) but these represent DIFFERENT events. fb_earliest is when a FUTBIN auction ENDED; first_seen_at is when we FIRST OBSERVED the listing — potentially hours earlier. This caused asymmetric window trimming: listings seen early fall below the cutoff on our side, while FUTBIN sales that ended late fall below the cutoff on their side.
test: Fix applied — _get_our_listings now filters by resolved_at >= cutoff; main() now uses MIN(resolved_at) for our_earliest. resolved_at is set when the listing disappears from scans, making it a close approximation of auction end time.
expecting: Overlap % should improve significantly after running health check
next_action: human verification — run python -m src.health_check --count 3 --verbose and confirm overlap is higher

## Symptoms

expected: High overlap (70%+) between our resolved listings and FUTBIN's completed sales at the same prices in the same time window
actual: Very low overlap (8-40%). Many prices show up as "only ours" or "only FUTBIN" when they should match. Example: Raúl has ours=5 fb=13 matched=1
errors: No errors — just low overlap numbers
reproduction: Run `python -m src.health_check --count 3 --verbose`
started: Since listing comparison was added to health_check.py

## Eliminated

(none yet)

## Evidence

- timestamp: 2026-03-27T00:00:00
  checked: listing_tracker.py record_listings()
  found: first_seen_at is set to NOW (UTC naive) when WE first observe the listing. It is NOT the auction end time. Same listing is upserted with same first_seen_at on each scan.
  implication: first_seen_at can be hours BEFORE the auction actually ends/sells.

- timestamp: 2026-03-27T00:00:00
  checked: listing_tracker.py resolve_outcomes()
  found: resolved_at = NOW when the listing disappears from scans. outcome assigned as sold/expired based on completed_sales matching. The DB has resolved_at available.
  implication: resolved_at is much closer to the actual auction end time — but health_check.py uses first_seen_at, not resolved_at.

- timestamp: 2026-03-27T00:00:00
  checked: health_check.py _get_our_listings() line 90-94
  found: Query filters WHERE first_seen_at >= cutoff. Returns first_seen_at of listing, not resolved_at.
  implication: A listing first seen at 21:13 but resolved at 23:00 will be compared to FUTBIN's 23:00 sale date using 21:13 as the timestamp.

- timestamp: 2026-03-27T00:00:00
  checked: health_check.py main() lines 249-280
  found: overlap_cutoff = max(fb_earliest_date, our_earliest_first_seen_at). Then our_listings filtered by first_seen_at >= cutoff, fb_listings filtered by s["date"] >= cutoff.
  implication: Two problems: (1) first_seen_at vs auction end date are different events — wrong field used. (2) The cutoff boundary means a listing with first_seen_at=21:13 that FUTBIN shows ended at 22:45 — if cutoff=22:00, our listing is excluded (21:13 < 22:00) but FUTBIN's is included (22:45 >= 22:00). This is the core mismatch causing low overlap.

- timestamp: 2026-03-27T00:00:00
  checked: listing_tracker.py resolve_outcomes() — resolved_at field
  found: resolved_at is set to NOW when the listing disappears. This is much closer to the actual auction end time than first_seen_at.
  implication: The fix should use resolved_at instead of first_seen_at in BOTH the DB query and the overlap_cutoff computation.

## Resolution

root_cause: health_check.py uses first_seen_at (when we FIRST SAW the listing) to compute the overlap cutoff and filter our DB listings. But FUTBIN's date field is when the auction ENDED. A listing first seen at 21:13 that sold at 22:45 will have first_seen_at=21:13 but FUTBIN date=22:45. When the cutoff falls between these times, the listing appears in only one source. The fix: use resolved_at (when the listing DISAPPEARED from our scans, i.e., approximately when it ended) instead of first_seen_at.

fix: In health_check.py:
  1. _get_our_listings(): change query to filter by resolved_at >= cutoff instead of first_seen_at >= cutoff. Also SELECT resolved_at for cutoff computation.
  2. main(): compute our_earliest from MIN(resolved_at) instead of MIN(first_seen_at).

verification: awaiting human confirmation from running health check
files_changed: [src/health_check.py]
