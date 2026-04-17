---
phase: quick-260417-s8n
plan: 01
subsystem: cli
tags: [cli, portfolio, rich, fastapi, display-layer]

requires:
  - phase: 07.2-portfolio-dashboard-trade-tracking
    provides: expected_profit_per_hour stored on PlayerScore (canonical ranking metric since v1.0)
provides:
  - GET /api/v1/portfolio response now includes sell_price, net_profit, sales_per_hour, card_type per entry
  - Reworked CLI portfolio table matching an OP seller's workflow (Buy/Sell/Profit/Margin/EP/hr/Win%/OP Sales/Sales/hr)
  - CSV export aligned with the new columns plus a Stale boolean
  - Two latent bugs fixed: EP/hr column and summary panel now source expected_profit_per_hour (not per-flip expected_profit)
  - Visual staleness indicator on portfolio rows
affects: [extension, dashboard, future-cli-consumers]

tech-stack:
  added: []
  patterns:
    - "Stale-row rendering via Rich add_row(style='dim') + leading '*' on player name"
    - "Additive API response extension — consumers tolerate unknown fields"

key-files:
  created: []
  modified:
    - src/server/api/portfolio_read.py
    - src/main.py

key-decisions:
  - "EP/hr column and summary both source expected_profit_per_hour per D-10 (canonical scoring metric); the old wiring on expected_profit was a display-layer bug, not a metric change"
  - "Efficiency column dropped from CLI table (internal ranking input, not user-facing); EP/hr is the user-facing ranking metric"
  - "Sell% header renamed Win% to clarify semantics (share of sales that cleared OP margin)"
  - "Stale rows marked with dim row style and leading '*' on name; CSV uses a Stale boolean column for spreadsheet filtering"
  - "GET /portfolio response extended additively — POST /portfolio/generate already exposed sell_price, so shape is consistent with existing endpoints"

patterns-established:
  - "API responses expose the same field set consumers need; CLI does not re-derive (e.g., net_profit is sent, not computed from buy/sell/tax)"
  - "CLI stale indicator = row dim style + leading asterisk (visual + textual)"

requirements-completed:
  - Q-01-expose-missing-portfolio-fields
  - Q-02-rework-cli-table-columns
  - Q-03-fix-ep-per-hour-bugs

duration: 2min
completed: 2026-04-17
---

# Quick 260417-s8n: Portfolio CLI Rework + EP/hr Fix Summary

**Reworked the portfolio CLI to answer the four questions an OP seller actually asks (what to buy, what to list at, what each flip nets, coins/hr), fixed the EP/hr column and summary wiring (both were sourcing per-flip expected_profit), dropped the Efficiency column, renamed Sell% to Win%, and marked stale-score rows visually; added sell_price/net_profit/sales_per_hour/card_type to GET /portfolio.**

## Performance

- **Duration:** ~2 min (Tasks 1 and 2 only; Task 3 is a human checkpoint)
- **Started:** 2026-04-17T17:25:19Z
- **Completed:** 2026-04-17T17:26:54Z
- **Tasks:** 2 of 3 (Task 3 = human-verify checkpoint, deferred to orchestrator)
- **Files modified:** 2

## Accomplishments
- GET /api/v1/portfolio now surfaces the four fields the scorer already builds: `sell_price`, `net_profit`, `sales_per_hour`, `card_type` — additive, no existing keys touched
- Fixed the EP/hr column: it was reading `expected_profit` (per-flip) while labeled per-hour; now reads `expected_profit_per_hour`
- Fixed the summary panel's "Expected profit/hr" line: was summing per-flip `expected_profit`; now sums `expected_profit_per_hour`
- New 12-column CLI table: `# | Player | OVR | Pos | Buy | Sell | Profit | Margin | EP/hr | Win% | OP Sales | Sales/hr` (Efficiency dropped, Sell% -> Win%)
- CSV export realigned to match (13 columns — adds a `Stale` boolean)
- Stale rows (`is_stale=true`) rendered with dim row style and leading `*` on the player name

## Task Commits

Each task was committed atomically:

1. **Task 1: Expose sell_price, net_profit, sales_per_hour, card_type in GET /portfolio** — `8382759e` (feat)
2. **Task 2: Rework CLI table and CSV; fix EP/hr wiring; stale row indicator** — `df39b39c` (feat)
3. **Task 3: Human verification** — not executed by agent (checkpoint belongs to orchestrator per task constraints)

**Plan metadata:** to be appended by the orchestrator after human verification.

## Files Created/Modified
- `src/server/api/portfolio_read.py` — GET /portfolio response dict extended with 4 additive keys
- `src/main.py` — `run_portfolio` mapped dict extended; `display_results` rebuilt (new columns, stale rendering, EP/hr wiring fix, summary fix); `export_csv` mirrors new columns plus `Stale` boolean

## Decisions Made
- Kept `expected_profit` in the mapped dict even though it's no longer displayed — cheap to carry, and if a future caller wants per-flip it's already there.
- Chose `"-"` as the EP/hr and Sales/hr fallback in the table (visually neutral in a right-justified numeric column) and empty string as the CSV fallback (easier for spreadsheet arithmetic).
- Margin column width expanded from 5 to 6 to accommodate two-digit margins plus the `%` suffix without ellipsis.
- Implemented stale indicator using Rich's `add_row(style=...)` kwarg rather than per-cell `Text` objects (simpler, less code, full-row styling is the intent).

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None.

## User Setup Required
None.

## Next Phase Readiness
- Task 3 (human-verify) is pending — the user runs `python -m src.main --budget 1000000` against a running backend and confirms:
  1. Summary `Expected profit/hr` is materially smaller than the old per-flip-summed value (coins/hr vs summed-coins-per-flip).
  2. Column order matches the spec and Efficiency is gone.
  3. `Profit ≈ (Sell × 0.95) − Buy` on spot-checked rows (EA 5% tax).
  4. Stale rows render dim with a leading `*` when present (acceptable absence if scanner healthy).
  5. Emitted CSV header row is `Rank, Player, Rating, Position, Buy, Sell, Profit, Margin, EP/hr, Win%, OP Sales, Sales/hr, Stale`.

## Self-Check: PASSED

Verified after writing this summary:
- `src/server/api/portfolio_read.py` — modified, present (additive keys in GET /portfolio block only)
- `src/main.py` — modified, present (run_portfolio mapped dict + display_results + export_csv all updated)
- Commit `8382759e` (Task 1) — present in `git log`
- Commit `df39b39c` (Task 2) — present in `git log`
- Automated verify blocks for Task 1 and Task 2 both passed before commit.
- `python -c "import src.main"` succeeded (no syntax/import regressions).

---
*Phase: quick-260417-s8n*
*Completed: 2026-04-17*
