---
phase: quick-260418-kzb
plan: 01
subsystem: portfolio-api + extension-overlay
tags: [portfolio, dropdown, card-types, backend-read-endpoint, extension-wiring]
requires:
  - active PlayerRecord rows in players table (is_active=True)
provides:
  - "GET /api/v1/portfolio/card-types returning sorted [{card_type, count}] from active rows"
  - "PORTFOLIO_CARD_TYPES_REQUEST / PORTFOLIO_CARD_TYPES_RESULT message variants"
  - "handlePortfolioCardTypes() background handler proxying fetch to backend"
  - "Dynamic excludeSelect population in renderEmpty() (no more hardcoded CARD_TYPES)"
affects:
  - extension overlay panel renderEmpty() flow
  - content-script exhaustive-switch compile-time contract
tech-stack:
  added: []
  patterns:
    - "SQLAlchemy select + group_by + order_by for count aggregation over an ORM column"
    - "Pydantic-free raw list return to match neighboring portfolio_read endpoints"
    - "Best-effort async dropdown population — renderEmpty returns sync, fetch populates after microtask"
key-files:
  created:
    - tests/test_portfolio_card_types.py
  modified:
    - src/server/api/portfolio_read.py
    - extension/src/messages.ts
    - extension/entrypoints/background.ts
    - extension/entrypoints/ea-webapp.content.ts
    - extension/src/overlay/panel.ts
    - extension/tests/overlay.test.ts
decisions:
  - "Raw list return (no Pydantic response_model) matches existing portfolio_read.py style"
  - "Dropdown fetch is best-effort — on error, select retains just the placeholder; Generate still works"
  - "Content script exhaustive switch gets pass-through cases for the two new variants (required by assertNever)"
metrics:
  duration-minutes: ~12
  completed: "2026-04-18T15:17:00Z"
  tasks: 3
  files: 6
---

# Quick Task 260418-kzb: Dynamic Card-Types Dropdown Summary

Replaced the 37-entry hardcoded `CARD_TYPES` list in the overlay panel with a live read from a new `GET /api/v1/portfolio/card-types` endpoint so the "Exclude card types" dropdown reflects all ~73 distinct card types actually present in the active players table (previously missing ~36 of them, including TOTS, TOTY, POTM variants).

## What was delivered

1. **Backend endpoint** — `GET /api/v1/portfolio/card-types` returns `[{card_type: str, count: int}]` sorted by count DESC, filtered to `is_active=True` rows. Live smoke test against running server returns 73 entries with `Team of the Week` (count 690) at the top.
2. **Extension message channel** — New `PORTFOLIO_CARD_TYPES_REQUEST` / `PORTFOLIO_CARD_TYPES_RESULT` variants in the `ExtensionMessage` discriminated union, with `handlePortfolioCardTypes()` in `background.ts` proxying the fetch.
3. **Panel wiring** — `renderEmpty()` deletes the hardcoded 37-entry array and issues a fire-and-forget `chrome.runtime.sendMessage` on mount; the `.then()` appends each returned `card_type` as an `<option>`. On error/null, the select keeps only the `+ Add exclusion...` placeholder and `Generate` still functions normally.
4. **Tests** — 2 backend integration tests (sorted-DESC shape + empty-DB empty-list) + 2 extension tests (populated-from-result + error-empty). All 8 backend + 29 extension tests green.

## Commits

| Task | Commit | Description |
| ---- | ------ | ----------- |
| 1 (RED) | `05eb1472` | `test(quick-260418-kzb): add failing test for GET /portfolio/card-types` |
| 1 (GREEN) | `3a298ee8` | `feat(quick-260418-kzb): add GET /api/v1/portfolio/card-types endpoint` |
| 2 | `c26b698f` | Extension message + background handler (see deviation note below) |
| 3 | `9ed03dac` | `feat(quick-260418-kzb): wire panel exclude dropdown to card-types endpoint` |

## Verification

### Backend
```
python -m pytest tests/test_portfolio_card_types.py tests/test_portfolio_generate.py -x -v
→ 8 passed, 17 warnings in 2.33s
```

### Extension
```
cd extension && npx vitest run tests/overlay.test.ts tests/background.test.ts
→ Test Files  2 passed (2) ; Tests  29 passed (29)
```

### TypeScript
```
cd extension && npx tsc --noEmit
→ clean (exit 0)
```

### Live smoke (post-implementation)
```
curl -s http://localhost:8000/api/v1/portfolio/card-types | jq 'length'
→ 73

curl -s http://localhost:8000/api/v1/portfolio/card-types | jq '.[0:3]'
→ [
    {"card_type": "Team of the Week", "count": 690},
    {"card_type": "Icon", "count": 110},
    {"card_type": "FUT Birthday", "count": 106}
  ]
```

### Deps load
```
python -c "import asyncio, asyncpg; print('smoke')"
→ smoke
```

## Deviations from Plan

### 1. [Rule 3 - Blocking Issue] Content-script exhaustive-switch update

- **Found during:** Task 2
- **Issue:** `extension/entrypoints/ea-webapp.content.ts` uses `assertNever(msg)` in its `default` branch over `msg.type`. Adding two new union variants without handling them would cause `tsc` to emit type errors (`never` argument mismatch).
- **Fix:** Added explicit `case 'PORTFOLIO_CARD_TYPES_REQUEST':` and `case 'PORTFOLIO_CARD_TYPES_RESULT':` pass-throughs that `return false` — same pattern already applied for the `DASHBOARD_STATUS_*` and `ACTIONS_NEEDED_*` pairs that are also not consumed by the content script. Plan explicitly anticipated this ("If a content-script switch uses assertNever, add explicit cases that return false"), so logging as documentation rather than a surprise deviation.
- **Files modified:** `extension/entrypoints/ea-webapp.content.ts`
- **Commit:** `c26b698f` (see note below)

### 2. [Process] Task 2 changes absorbed into an unrelated commit message

- **Found during:** Attempting to create the Task 2 atomic commit
- **Issue:** Another process/agent on this machine (there are ~10 `.claude/worktrees/agent-*` directories, several with active background work) committed my staged-but-unstaged Task 2 edits as part of commit `c26b698f`, labelled `wip(algo): oscillator_v1 — ...`. That commit contains both unrelated algo backtest work AND this task's changes to `extension/src/messages.ts`, `extension/entrypoints/background.ts`, `extension/entrypoints/ea-webapp.content.ts`, plus the `260418-kzb-PLAN.md` file.
- **Fix:** No destructive rewrite attempted. The code is correct (verified via `git show c26b698f -- extension/src/messages.ts` — exact content of the plan's proposed diff is present). Task 1 (backend) and Task 3 (panel) kept clean atomic commits.
- **Impact:** Commit graph message for Task 2 is misleading, but content is correct and tests pass. A later `git log --grep=PORTFOLIO_CARD_TYPES` will find the changes. Future agents should be aware that parallel workers on this repo may opportunistically commit staged files.

## Known Stubs

None. The endpoint has a real data source, the handler has a real fetch, and the panel has a real render path. When the backend is down, the dropdown degrades to placeholder-only (documented behavior in the plan's `must_haves.truths`).

## Notes

- The live DB currently has `Icon` (count 110) and `UT Heroes` (count 91) as active card types, so they appear in the dropdown. The plan's `constraints` note said "they're filtered at discovery time so they'll naturally be absent" — this appears not to hold today. That's a scanner discovery-filter concern orthogonal to this task; logging as an out-of-scope deferred observation rather than a fix. If the user wants them filtered out, add them to the scanner's `EXCLUDED_CARD_TYPES` in discovery.
- Hardcoded `CARD_TYPES` array is gone — `grep -n "CARD_TYPES" extension/src/overlay/panel.ts` returns only comment/type-name references (lines 834, 837, 839).

## Self-Check: PASSED

**Files verified present:**
- FOUND: `src/server/api/portfolio_read.py` (modified — `get_card_types` handler added at line 336)
- FOUND: `tests/test_portfolio_card_types.py` (created, 93 lines, 2 test functions)
- FOUND: `extension/src/messages.ts` (modified — new variants at lines 131–133)
- FOUND: `extension/entrypoints/background.ts` (modified — switch case + handler)
- FOUND: `extension/entrypoints/ea-webapp.content.ts` (modified — exhaustive-switch pass-through)
- FOUND: `extension/src/overlay/panel.ts` (modified — hardcoded array removed, fetch wired)
- FOUND: `extension/tests/overlay.test.ts` (modified — 2 new tests)

**Commits verified present:**
- FOUND: `05eb1472` (RED test for Task 1)
- FOUND: `3a298ee8` (Task 1 GREEN — backend endpoint)
- FOUND: `c26b698f` (contains Task 2 changes — message types + background handler + content script pass-through)
- FOUND: `9ed03dac` (Task 3 — panel wiring + tests)
