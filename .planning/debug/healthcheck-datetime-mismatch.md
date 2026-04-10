---
status: awaiting_human_verify
trigger: "Health check returns 0 sold/expired due to datetime format mismatch between DB storage and query parameters."
created: 2026-03-27T00:00:00
updated: 2026-03-27T00:00:00
---

## Current Focus

hypothesis: DB stores naive datetimes with space separator ("2026-03-26 21:13:34.516638") but overlap_cutoff uses ISO 'T' separator ("2026-03-26T20:00:00+00:00"). SQLite string comparison fails because ' ' (ASCII 32) < 'T' (ASCII 84), so `first_seen_at > cutoff` always returns FALSE.
test: Normalize cutoff_iso in _get_our_data() and overlap_cutoff computation to DB format (replace 'T' with space, strip timezone suffix)
expecting: sold/expired counts will match actual DB data
next_action: Apply fix to src/health_check.py — two locations

## Symptoms

expected: Health check should show sold/expired counts matching what's in the listing_observations table (DB has 6198 sold, 10074 expired)
actual: Health check returns 0 sold and 0 expired for every player checked
errors: No errors — just wrong results (silent data bug)
reproduction: Run `python -m src.health_check` after server has been running 30+ minutes
started: Since the overlap_cutoff logic was added to health_check.py

## Eliminated

(none — root cause pre-confirmed via DB queries)

## Evidence

- timestamp: 2026-03-27T00:00:00
  checked: SQLite string comparison behavior
  found: SELECT '2026-03-26 21:13:34' > '2026-03-26T20:00:00+00:00' returns 0; SELECT '2026-03-26 21:13:34' > '2026-03-26 20:00:00' returns 1
  implication: Space separator vs T separator makes all DB rows appear "earlier than" the cutoff

- timestamp: 2026-03-27T00:00:00
  checked: listing_observations row count with both cutoff formats
  found: COUNT with T-format cutoff = 0; COUNT with space-format cutoff = 136332
  implication: Confirms the format mismatch is the sole cause of 0 results

- timestamp: 2026-03-27T00:00:00
  checked: _get_our_data() in src/health_check.py lines 126-128
  found: cutoff_iso passed in may be "2026-03-26T20:00:00+00:00" (T-format with timezone); used directly in SQL WHERE clause without normalization
  implication: Fix must normalize before passing to SQL

- timestamp: 2026-03-27T00:00:00
  checked: overlap_cutoff computation lines 394-401
  found: fb_iso = futbin_all["futbin_earliest"].isoformat() produces T-format; our_earliest comes from DB as space-format; max(fb_iso, our_earliest) compares mixed formats unreliably
  implication: Both values must be normalized to same format before max() comparison

## Resolution

root_cause: DateTime format mismatch — DB stores "YYYY-MM-DD HH:MM:SS" (space separator, naive) but cutoff is passed as ISO 8601 "YYYY-MM-DDTHH:MM:SS+00:00" (T separator, timezone-aware). SQLite lexicographic string comparison treats the T-format string as always less than space-format, yielding 0 rows.
fix: |
  Two locations in src/health_check.py:
  1. _get_our_data() (line 133): added normalization of cutoff_iso to DB format
     before using it in SQL — replaces 'T' with space, strips timezone suffix.
  2. overlap_cutoff computation (lines 402-414): added _to_db_fmt() helper that
     normalises both fb_iso and our_earliest to DB format before max() comparison,
     ensuring consistent lexicographic ordering and a DB-safe value for _get_our_data().
verification: (pending human confirmation)
files_changed: [src/health_check.py]
