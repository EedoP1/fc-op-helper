---
status: fixing
trigger: "Portfolio optimizer produces only 14 players with 2.5M budget instead of ~100"
created: 2026-03-30T00:00:00Z
updated: 2026-03-30T00:00:00Z
---

## Current Focus

hypothesis: Two confirmed bugs in src/optimizer.py: (1) greedy fill sorts by raw EPPH, selecting expensive players that exhaust budget in ~14 slots; (2) swap loop breaks on first failed swap instead of continuing to try other expensive cards.
test: Read code to confirm, then apply targeted fix.
expecting: After fix, greedy fill by efficiency (EPPH/buy_price) fills ~100 cheap high-ratio slots; swap loop continues past failures.
next_action: Apply fix to src/optimizer.py

## Symptoms

expected: Portfolio should contain ~100 players (TARGET_PLAYER_COUNT=100) that maximize total portfolio expected profit
actual: Only 14 players selected, all expensive, consuming entire 2.5M budget with 86 empty slots
errors: No errors — the optimizer completes successfully, it just produces a poor result
reproduction: GET /api/v1/portfolio?budget=2500000
started: Likely since the optimizer was changed to sort by raw EPPH instead of efficiency (EPPH/buy_price)

## Eliminated

- hypothesis: Bug is in portfolio endpoint or DB query
  evidence: portfolio.py correctly calls optimize_portfolio() and passes scored entries with expected_profit_per_hour; the bug is entirely in optimizer.py logic
  timestamp: 2026-03-30T00:00:00Z

## Evidence

- timestamp: 2026-03-30T00:00:00Z
  checked: src/optimizer.py line 39
  found: scored.sort(key=lambda s: s["_ranking_profit"], reverse=True) — sorts by raw EPPH, not EPPH/buy_price
  implication: With 2.5M budget and expensive players sorted first, only ~14 fit before budget is exhausted

- timestamp: 2026-03-30T00:00:00Z
  checked: src/optimizer.py line 94
  found: `else: break` — swap loop breaks immediately when the most expensive card can't be swapped
  implication: If the top earner can't be beaten by cheap alternatives (e.g. it's genuinely the best), the loop stops entirely, never trying the 2nd or 3rd most expensive card

- timestamp: 2026-03-30T00:00:00Z
  checked: src/optimizer.py lines 33-36
  found: efficiency = epph / buy_price is already computed but only stored on the entry, not used for sorting
  implication: The fix is a 1-line change: sort by s["efficiency"] instead of s["_ranking_profit"] in the greedy fill

- timestamp: 2026-03-30T00:00:00Z
  checked: src/optimizer.py lines 60-94 (swap loop)
  found: Loop picks the single most expensive card and attempts to replace it. On failure breaks entirely. No mechanism to skip that card and try the next most expensive.
  implication: Fix: instead of break, track which expensive cards have been tried and continue to the next one

## Resolution

root_cause: Two bugs in optimize_portfolio():
  1. Greedy fill sorts by raw EPPH (line 39), causing expensive players to fill the budget in ~14 slots instead of ~100.
  2. Swap loop breaks on first failed swap attempt (line 94), preventing iteration over other expensive cards.

fix:
  1. Change greedy fill sort key from s["_ranking_profit"] to s["efficiency"] (EPPH/buy_price).
  2. Change swap loop to continue past failed attempts (track tried expensive cards) instead of breaking.

verification: pending
files_changed: [src/optimizer.py]
