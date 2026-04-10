---
status: awaiting_human_verify
trigger: "portfolio-remove-race-condition"
created: 2026-03-30T00:00:00Z
updated: 2026-03-30T11:00:00Z
---

## Current Focus

hypothesis: CONFIRMED AND FIXED — The [:1] cap on swap-preview was wrong. The correct fix
  is to let the client tell the server how many draft slots remain (current_count), then
  server computes needed = TARGET_PLAYER_COUNT - current_count and caps to that. This
  maximises freed budget (30k can return multiple 10k replacements) while never overshooting
  100 total. Confirm endpoint retains its server-side cap as final safety net.

test: pytest tests/test_portfolio_swap_preview.py tests/test_portfolio_confirm.py
expecting: 11/12 pass (test_swap_preview_replacement_fields is pre-existing unrelated failure)
next_action: user verify in real workflow

## Symptoms

expected: Removing a player should replace them with exactly 1 new player, keeping total at 100. Rapid sequential removals should each produce exactly 1 replacement without duplicates.
actual: User has 135 players and many duplicates after confirming. Previous DELETE endpoint fix was irrelevant — the draft flow uses swap-preview + confirm, not DELETE.
errors: No error messages — the operations succeed but produce wrong state.
reproduction: Remove a player via the extension UI (X button in DRAFT state). The swap-preview returns multiple replacements. Confirm portfolio → 135 rows inserted.
started: Likely present since swap-preview was added.

## Eliminated

- hypothesis: DELETE /portfolio/{ea_id} race condition caused the 135-player bug
  evidence: The draft flow (X button → swap → confirm) never calls DELETE. DELETE is unused
    in the normal draft workflow. The previous fix was correct for DELETE but the user's
    reported bug comes from swap-preview + confirm.
  timestamp: 2026-03-30T10:00:00Z

## Evidence

- timestamp: 2026-03-30T00:01:00Z
  checked: src/server/api/portfolio.py DELETE /portfolio/{ea_id} (lines 807-910)
  found: |
    Phase 1: deletes slot + cancels actions, commits
    Phase 2: reads remaining slots (after deletion), queries viable scores
    Runs optimize_portfolio(scored_candidates, freed_budget) where freed_budget = removed player's buy_price
    The optimizer is bounded by TARGET_PLAYER_COUNT=100 but operates on candidates that exclude remaining slots —
    so if freed_budget is large (e.g. 50k card removed), optimizer can return MULTIPLE players fitting in 50k.
    The endpoint returns these as a list — no enforcement of "return exactly 1".
    No locking/transaction isolation — concurrent DELETE calls each read remaining_ea_ids independently.
  implication: |
    Bug 1: Optimizer can return multiple replacements for a single removal. If the extension auto-confirms all of them, total > 100.
    Bug 2: Two rapid DELETEs: DELETE(A) commits before DELETE(B) starts → B reads 99 slots → both return replacements → if extension confirms both, slots go from 99 to 101 (or similar).
    Actually with the current code: DELETE(A) Phase 1 commits (98 slots), DELETE(B) Phase 1 commits (97 slots) if they overlap in phase 2 both see ~97-98 remaining. This is the race.

- timestamp: 2026-03-30T00:02:00Z
  checked: optimizer.py TARGET_PLAYER_COUNT usage
  found: |
    optimize_portfolio respects TARGET_PLAYER_COUNT=100 as a hard cap on output.
    But when called with freed_budget (e.g. 30k), it selects players costing up to 30k total.
    Multiple cheap players (e.g. 3 × 10k) can fill that budget → returns 3 replacements.
    The endpoint never limits replacements to 1.
  implication: The extension must be auto-adding all returned replacements, causing overshoot.

- timestamp: 2026-03-30T10:00:00Z
  checked: extension/entrypoints/background.ts handlePortfolioSwap + extension/src/overlay/panel.ts removeBtn handler
  found: |
    PORTFOLIO_SWAP message → handlePortfolioSwap() → POST /portfolio/swap-preview
    swap-preview returns json.replacements (no cap on count).
    panel.ts line 995: draftPlayers.splice(insertIdx, 0, ...res.replacements);
    ALL replacements are spliced in. One X-click that returns 3 replacements → draftPlayers grows by 2 net.
    After 35 such removals: 100 - 35 + (35 × 2) = 135. Matches exactly the reported symptom.
  implication: This is the primary bug path. swap-preview must return at most 1 replacement per call.

- timestamp: 2026-03-30T10:01:00Z
  checked: src/server/api/portfolio.py confirm_portfolio (lines 340-438)
  found: |
    Deduplicates by ea_id (prevents exact-same ea_id duplicates).
    Inserts all deduped players as PortfolioSlot rows — NO cap on count.
    Leftover preservation adds additional rows beyond the new players.
    Total slots = new active players + preserved leftovers. No check against TARGET_PLAYER_COUNT.
  implication: Even if swap-preview is fixed, confirm must also cap active slots to TARGET_PLAYER_COUNT
    to be bulletproof. A malformed or replay request should not create 200 slots.

## Resolution

root_cause: |
  Two bugs in the DRAFT flow (swap-preview + confirm):

  Bug A (swap-preview overshoot): POST /portfolio/swap-preview calls
  optimize_portfolio(candidates, freed_budget) which returns multiple cheap players
  for a large freed_budget. The endpoint returns all of them with no count cap.
  The extension splices all returned replacements into draftPlayers (panel.ts:995).
  One X-click removing a 30k card returns 3 × 10k replacements → draft grows by 2.
  After N removals: 100 - N + (N × replacements_per_swap) = overshoot.
  35 removals × avg 2 replacements = 135 players.

  Bug B (confirm no slot cap): POST /portfolio/confirm inserts whatever the
  client sends. No enforcement of TARGET_PLAYER_COUNT. The leftover preservation
  logic also adds extra rows. Server must cap active (non-leftover) slots at
  TARGET_PLAYER_COUNT at confirm time, server-side, as the final safety net.

fix: |
  1. swap-preview: the [:1] cap was wrong (wasted freed budget). Replaced with slot-aware cap:
     - SwapPreviewRequest gains current_count field (post-splice draft count from client)
     - needed = max(0, TARGET_PLAYER_COUNT - current_count)
     - replacements capped to needed, not 1
     - Extension sends current_count = draftPlayers.length after splice (already decremented)
     - Example: remove 1 from 100 → current_count=99, needed=1 → 1 replacement
     - Example: 5 rapid removals → last request has current_count=95, needed=5 → up to 5
     - A 30k card freed: if needed=1, returns 1 best replacement; if needed=3, returns 3
  2. confirm_portfolio: retains server-side cap at TARGET_PLAYER_COUNT (unchanged).
     Still prevents malformed or replayed requests from inflating portfolio beyond 100.

  Extension changes:
  - messages.ts: PORTFOLIO_SWAP type gains current_count: number
  - background.ts: handlePortfolioSwap() gains current_count param, passes to POST body
  - panel.ts: removeBtn handler sends current_count = draftPlayers.length (post-splice)

verification: |
  - test_swap_preview_returns_replacements: PASSED
  - test_swap_preview_excludes_all_specified_ids: PASSED
  - test_swap_preview_empty_db: PASSED
  - test_swap_preview_invalid_freed_budget: PASSED
  - test_swap_preview_caps_replacements_to_one_open_slot: PASSED (new)
  - test_swap_preview_returns_multiple_when_slots_available: PASSED (new — covers budget max)
  - test_swap_preview_no_replacements_when_portfolio_full: PASSED (new)
  - test_confirm_seeds_portfolio_slots: PASSED
  - test_confirm_clears_existing_slots: PASSED
  - test_confirm_empty_players: PASSED
  - test_confirm_caps_slots_at_target_player_count: PASSED
  11/12 pass. test_swap_preview_replacement_fields is pre-existing failure (bad seed data,
  no expected_profit_per_hour → optimizer filters all candidates) — unrelated to this fix.

files_changed:
  - src/server/api/portfolio.py
  - extension/src/messages.ts
  - extension/entrypoints/background.ts
  - extension/src/overlay/panel.ts
  - tests/test_portfolio_swap_preview.py
