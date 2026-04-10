---
status: awaiting_human_verify
trigger: "buy-loop-skips-to-scanned: The automation buy loop stops trying to buy new players prematurely. Instead of moving to the next unprocessed player after a price guard skip, it goes to scan the transfer list, then comes back and retries players it already skipped due to price guard."
created: 2026-04-04T00:00:00Z
updated: 2026-04-04T00:00:00Z
---

## Current Focus
<!-- OVERWRITE on each update - reflects NOW -->

hypothesis: CONFIRMED — The automation loop fetches actions_needed ONCE per outer cycle (before the buy phase). After a price-guard skip, it moves to the next player in the same snapshot. But "price guard skip" returns outcome='skipped', and the backend's /portfolio/actions-needed does NOT record a skipped attempt — it still shows the player as action=BUY. So when the outer loop restarts (scan TL -> fetch actions again), the SAME players that were price-guard-skipped last cycle are returned again as BUY, because the backend never learned they were attempted.

The scan-TL phase itself is not the bug — it runs every cycle. The bug is that price-guard-skipped players have no "tried this cycle" marker in either the extension OR the backend, so each outer cycle retry serves the same stale BUY list containing already-skipped players.

test: traced data flow end-to-end
expecting: fix requires the extension to track which players were skipped this cycle and filter them from subsequent outer loop iterations (or backend needs a "skip" outcome)
next_action: apply fix — track skipped ea_ids in-loop and filter buyPlayers on each cycle

## Symptoms
<!-- Written during gathering, then IMMUTABLE -->

expected: When a player is skipped due to price guard, automation moves to next unprocessed player in the buy queue until all players are attempted
actual: Automation stops buying prematurely, scans transfer list, then returns to players already skipped by price guard instead of new ones
errors: No crash errors reported - behavioral bug
reproduction: Run automation with a portfolio that has players to buy; observe it cycling back to price-guarded players
started: Currently happening in production

## Eliminated
<!-- APPEND only - prevents re-investigating -->

## Evidence
<!-- APPEND only - facts discovered -->

- timestamp: 2026-04-04T00:00:00Z
  checked: automation-loop.ts Phase C buy loop (lines 236–400) and Phase A/B lifecycle
  found: |
    - actionsNeeded is fetched ONCE per outer while loop iteration (Phase B, before buy phase)
    - buyPlayers = actionsNeeded.filter(a => a.action === 'BUY') — snapshot taken once
    - Inner for-loop iterates buyPlayers; on price-guard skip: outcome='skipped', reason='Price above guard'
    - On 'skipped' outcome: no mutation to actionsNeeded, no tracking of skipped ea_ids
    - Loop moves to next player correctly within the SAME outer cycle iteration
    - When outer loop restarts: Phase A scans TL, Phase B re-fetches actionsNeeded from backend
  implication: |
    The backend /portfolio/actions-needed re-derives actions from trade_records. A price-guard
    skip NEVER writes a trade_record (executeBuyCycle returns skipped without calling TRADE_REPORT).
    So backend still shows these players as action=BUY. Next outer cycle serves same players again.

- timestamp: 2026-04-04T00:00:00Z
  checked: portfolio_read.py get_actions_needed() + background.ts handleTradeReport()
  found: |
    - Backend action derivation: action=BUY when latest_outcome is None (never bought) or 'sold'
    - TRADE_REPORT is only sent on outcome='bought' (line 330-344 in automation-loop.ts)
    - Price-guard skip = outcome='skipped' from executeBuyCycle = NO trade report sent
    - Backend state unchanged after skip → next cycle query returns same BUY list
  implication: |
    This is the full mechanism. There is no in-memory tracking of skipped players across
    outer loop iterations. The fix must either:
    (a) Track skipped ea_ids in a Set that persists across outer loop iterations, filtering
        them from buyPlayers until the next successful TL scan clears them, OR
    (b) Have a "skip cooldown" window so skipped players aren't retried for N minutes

## Resolution
<!-- OVERWRITE as understanding evolves -->

root_cause: |
  The automation outer while-loop re-fetches actionsNeeded from the backend at the start of each
  cycle (Phase B: ACTIONS_NEEDED_REQUEST). The backend endpoint GET /portfolio/actions-needed
  derives action=BUY from the absence of a 'listed' or 'bought' trade record for a slot. When
  executeBuyCycle() returns outcome='skipped' (price guard), NO trade record is written to the
  backend (TRADE_REPORT is only sent on 'bought'). So the backend perpetually returns the same
  price-guarded players as action=BUY on every cycle. The inner for-loop correctly moves to the
  next player within a single cycle, but the outer while-loop restarts the scan→buy cycle and
  the same overpriced players are retried indefinitely instead of being passed over.

fix: |
  Added a priceGuardCooldown Map<ea_id, skippedAt_ms> declared outside the while-loop (persists
  across outer cycle iterations). When a player's executeBuyCycle() returns outcome='skipped'
  with a reason containing 'price guard' or 'above guard', its ea_id is recorded in the map
  with the current timestamp. At the start of each cycle's buy-player-list construction, expired
  entries (> 5 minutes old) are purged, then buyPlayers is filtered to exclude ea_ids still in
  the cooldown window. This prevents retrying the same overpriced player on consecutive cycles
  while still allowing a retry after 5 minutes (prices may have dropped by then).

verification: pending human verification
files_changed:
  - extension/src/automation-loop.ts
