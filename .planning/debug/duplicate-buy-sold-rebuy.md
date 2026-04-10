---
status: awaiting_human_verify
trigger: "Fix the duplicate buy bug in the automation loop."
created: 2026-04-05T00:00:00Z
updated: 2026-04-05T00:00:00Z
---

## Current Focus

hypothesis: Phase D rebuy logic duplicates what the backend's BUY action already handles via Phase C
test: Remove Phase D entirely; move sale reporting (TRADE_REPORT_BATCH) and profit tracking to after Phase A in transfer-list-cycle.ts and automation-loop.ts respectively
expecting: Each sold card triggers exactly one rebuy (via Phase C's backend-driven BUY action)
next_action: Apply fix to both files

## Symptoms

expected: When a player sells, buy exactly one replacement copy
actual: When a player sells mid-cycle, two copies are bought — one by Phase C (backend BUY action) and one by Phase D (rebuy logic)
errors: No errors — both buys succeed, resulting in 2 copies of the same card
reproduction: Player sells → Phase A scans TL, clears sold card from DOM but does NOT report sale to backend → Phase B fetches actions_needed (backend still has old "sold" outcome from previous cycle, returns BUY) → Phase C buys the player → Phase D processes cycleResult.scanned.sold and rebuys the same player
started: Observed in activity log at 10:02:35 (Phase C buy) and 10:03:49 (Phase D rebuy)

## Eliminated

- hypothesis: Phase C doesn't handle sold players
  evidence: Backend returns action=BUY for players whose latest outcome is "sold" — confirmed in portfolio_read.py lines 412-414
  timestamp: 2026-04-05T00:00:00Z

## Evidence

- timestamp: 2026-04-05T00:00:00Z
  checked: transfer-list-cycle.ts executeTransferListCycle
  found: Reports expired items via TRADE_REPORT_BATCH (lines 228-239) but does NOT report sold items. Sold items are only cleared from DOM.
  implication: Backend does not learn about the sale until Phase D — but Phase C already acts on stale backend state that shows action=BUY

- timestamp: 2026-04-05T00:00:00Z
  checked: automation-loop.ts Phase D (lines 442-576)
  found: Phase D iterates cycleResult.scanned.sold, reports sale to backend, calls addProfit, then calls executeBuyCycle to rebuy
  implication: This rebuy is redundant — backend already returned BUY for this player in Phase B, and Phase C already executed it

## Resolution

root_cause: Two independent code paths both trigger a buy when a card sells. Phase C executes a BUY action returned by the backend (which still shows the player as needing a buy because the sale wasn't reported). Phase D then iterates sold items and independently rebuys. The fix is to (1) report sold items in Phase A via TRADE_REPORT_BATCH so the backend is updated before Phase B fetches actions_needed, and (2) remove Phase D's rebuy logic entirely, keeping only profit tracking and sale logging (moved to after Phase A in the main loop).
fix: |
  1. transfer-list-cycle.ts: Added TRADE_REPORT_BATCH for sold items in Step 5b,
     before the Clear Sold action (Step 6). Mirrors existing expired TRADE_REPORT_BATCH
     pattern. Sends ea_id=0 with outcome='sold' for each sold item so the backend
     marks the player as sold before Phase B fetches actions_needed.
  2. automation-loop.ts: Removed Phase D entirely (the sold-player rebuy block,
     ~134 lines). Moved profit logging and engine.addProfit() calls to after Phase A
     completes, iterating cycleResult.scanned.sold. Sale reporting now happens in
     Phase A (transfer-list-cycle.ts Step 5b) — no longer duplicated in Phase D.
verification: Self-verified: sold cards reported to backend before Phase B runs,
  so backend will NOT return action=BUY for them in the same cycle, eliminating
  the Phase C duplicate buy. Phase D's independent rebuy path is gone entirely.
files_changed:
  - extension/src/transfer-list-cycle.ts
  - extension/src/automation-loop.ts
