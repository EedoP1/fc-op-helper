---
phase: 07-portfolio-management
verified: 2026-03-27T12:10:00Z
status: human_needed
score: 7/8 must-haves verified
re_verification: false
human_verification:
  - test: "Open EA Web App (https://www.ea.com/ea-sports-fc/ultimate-team/web-app/), load the extension, verify the 'OP' toggle tab appears on the right edge of the screen, click it to open the 320px dark sidebar, type a budget, click Generate, verify player rows appear with name/rating/position/buy price/sell price/margin, click X on a player to remove it and see a replacement appear, click Confirm and verify the panel switches to read-only confirmed state, close and reopen the tab and confirm the portfolio reloads automatically"
    expected: "Panel slides in smoothly from right. Player list renders. Swap replaces removed player. Confirmed state persists across browser sessions. Panel survives SPA navigation between EA Web App pages."
    why_human: "Visual appearance, SPA navigation survival, and browser-session persistence cannot be verified without running the extension in a real Chrome instance against the live EA Web App."
---

# Phase 7: Portfolio Management Verification Report

**Phase Goal:** User can generate an OP sell portfolio from the extension, view it in an overlay panel on the EA Web App, and swap out players — the foundation that automation builds on.

**Verified:** 2026-03-27T12:10:00Z
**Status:** human_needed — All automated checks passed; one blocking behavior requires human verification (visual overlay on EA Web App)
**Re-verification:** No — initial verification

## Goal Achievement

### Observable Truths (derived from ROADMAP Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | POST /api/v1/portfolio/generate accepts budget, runs optimizer, returns player list | VERIFIED | `generate_portfolio()` at portfolio.py:200 — reads viable scores, calls `optimize_portfolio()`, returns `{data, count, budget, budget_used, budget_remaining}`. 6 passing tests in test_portfolio_generate.py |
| 2 | POST /api/v1/portfolio/confirm seeds portfolio_slots (clean-slate) | VERIFIED | `confirm_portfolio()` at portfolio.py:296 — `delete(PortfolioSlot)` then loop-insert, commit. 3 passing tests in test_portfolio_confirm.py |
| 3 | POST /api/v1/portfolio/swap-preview returns replacement candidates excluding specified ea_ids | VERIFIED | `swap_preview()` at portfolio.py:335 — filters `excluded_ea_ids`, runs optimizer on `freed_budget`. 5 passing tests in test_portfolio_swap_preview.py |
| 4 | GET /api/v1/portfolio/confirmed returns current portfolio_slots with player metadata | VERIFIED | `get_confirmed_portfolio()` at portfolio.py:409 — joins PortfolioSlot with PlayerRecord. 3 passing tests in test_portfolio_confirmed.py |
| 5 | PORTFOLIO_* message types exist and service worker proxies them to backend | VERIFIED | messages.ts: 8 PORTFOLIO_* variants in discriminated union. background.ts: 4 case handlers calling backend, returning true for async response. portfolioItem.setValue called on confirm success |
| 6 | Content script switch is exhaustive — no assertNever compile errors | VERIFIED | ea-webapp.content.ts: all 8 PORTFOLIO_* types handled explicitly before `default: assertNever(msg)`. `npx tsc --noEmit` exits 0 |
| 7 | Overlay panel injected with three states (empty/draft/confirmed), toggle, swap, and confirmed persistence | VERIFIED | panel.ts: 614-line createOverlayPanel() factory. Three render functions (renderEmpty, renderDraft, renderConfirmed). PORTFOLIO_SWAP, PORTFOLIO_CONFIRM, PORTFOLIO_GENERATE messages sent. PORTFOLIO_LOAD on mount. 12 passing overlay tests |
| 8 | Overlay panel visible on EA Web App, survives SPA navigation, persists across browser sessions | ? NEEDS HUMAN | Cannot verify visual rendering, real SPA navigation, or chrome.storage persistence without a running Chrome instance on the live EA Web App |

**Score:** 7/8 truths verified

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `src/server/api/portfolio.py` | Four new endpoints: generate, confirm, swap-preview, confirmed | VERIFIED | Contains `generate_portfolio`, `confirm_portfolio`, `swap_preview`, `get_confirmed_portfolio`. Also `GenerateRequest`, `ConfirmRequest`, `ConfirmPlayer`, `SwapPreviewRequest` Pydantic models |
| `tests/test_portfolio_generate.py` | Integration tests for POST /generate | VERIFIED | 6 test functions present and passing |
| `tests/test_portfolio_confirm.py` | Integration tests for POST /confirm | VERIFIED | 3 test functions present and passing |
| `tests/test_portfolio_swap_preview.py` | Integration tests for POST /swap-preview | VERIFIED | 5 test functions present and passing |
| `tests/test_portfolio_confirmed.py` | Integration tests for GET /confirmed | VERIFIED | 3 test functions present and passing |
| `extension/src/messages.ts` | PORTFOLIO_* message types in discriminated union | VERIFIED | 8 new variants: PORTFOLIO_GENERATE, PORTFOLIO_GENERATE_RESULT, PORTFOLIO_CONFIRM, PORTFOLIO_CONFIRM_RESULT, PORTFOLIO_SWAP, PORTFOLIO_SWAP_RESULT, PORTFOLIO_LOAD, PORTFOLIO_LOAD_RESULT |
| `extension/src/storage.ts` | portfolioItem storage item + PortfolioPlayer type | VERIFIED | `PortfolioPlayer` type, `ConfirmedPortfolio` type, `portfolioItem` at `'local:portfolio'` |
| `extension/entrypoints/background.ts` | Service worker handlers for PORTFOLIO_* messages | VERIFIED | `case 'PORTFOLIO_GENERATE'`, `case 'PORTFOLIO_CONFIRM'`, `case 'PORTFOLIO_SWAP'`, `case 'PORTFOLIO_LOAD'` — all returning `true` for async response |
| `extension/entrypoints/ea-webapp.content.ts` | Panel injection, PORTFOLIO_LOAD on mount, SPA re-injection | VERIFIED | `createOverlayPanel` imported and called, panel appended to body, PORTFOLIO_LOAD sent on mount (guarded by `ctx.isInvalid` check), re-injection in locationchange handler |
| `extension/src/overlay/panel.ts` | Overlay panel DOM module with three states | VERIFIED | 614 lines, exports `createOverlayPanel()`, container class `op-seller-panel`, toggle class `op-seller-toggle`, z-index 999999, three render functions, PORTFOLIO_SWAP/CONFIRM/GENERATE messages wired |
| `extension/tests/overlay.test.ts` | Tests for panel state transitions and swap | VERIFIED | 12 test cases covering container creation, z-index, empty state, draft with players, confirmed without X buttons, destroy, toggle, swap message, regenerate, generate |

### Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `src/server/api/portfolio.py` | `src/optimizer.py` | `optimize_portfolio()` call | WIRED | Called at lines 156, 257, 387, 539 in portfolio.py |
| `src/server/api/portfolio.py` | `src/server/models_db.py` | `PortfolioSlot` insert/delete in confirm | WIRED | `delete(PortfolioSlot)` at line 317, `PortfolioSlot(...)` inserts in loop |
| `extension/entrypoints/background.ts` | `http://localhost:8000/api/v1/portfolio` | fetch calls for generate/confirm/swap-preview | WIRED | `handlePortfolioGenerate` (line 94), `handlePortfolioConfirm` (line 133), `handlePortfolioSwap` (line 169) |
| `extension/entrypoints/background.ts` | `extension/src/storage.ts` | `portfolioItem.setValue` on confirm success | WIRED | line 148 — awaited before sendResponse |
| `extension/src/messages.ts` | `extension/entrypoints/ea-webapp.content.ts` | `assertNever` exhaustiveness | WIRED | `assertNever(msg)` in default case, TypeScript compiles without error |
| `extension/src/overlay/panel.ts` | `extension/src/messages.ts` | `chrome.runtime.sendMessage` with PORTFOLIO_* types | WIRED | `PORTFOLIO_GENERATE` (line 218), `PORTFOLIO_SWAP` (line 402), `PORTFOLIO_CONFIRM` (line 441) |
| `extension/entrypoints/ea-webapp.content.ts` | `extension/src/overlay/panel.ts` | `createOverlayPanel` import and mount | WIRED | import at line 17, `createOverlayPanel()` at line 71, `document.body.appendChild` |

### Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|-------------------|--------|
| `src/server/api/portfolio.py` (generate endpoint) | `scored_list` | SQLAlchemy query joining `PlayerScore` + `PlayerRecord` on `latest_subq` | Yes — real DB query against `player_scores` and `player_records` tables | FLOWING |
| `extension/entrypoints/background.ts` (handlePortfolioGenerate) | `json.data` | `fetch` to `/api/v1/portfolio/generate` | Yes — real HTTP fetch, response mapped via `mapToPortfolioPlayer` | FLOWING |
| `extension/src/overlay/panel.ts` (renderDraft) | `draftPlayers` | Set by `setState('draft', {players: res.data, ...})` from PORTFOLIO_GENERATE_RESULT | Yes — populated from real service worker response | FLOWING |
| `extension/entrypoints/background.ts` (PORTFOLIO_LOAD) | `portfolio` | `portfolioItem.getValue()` — chrome.storage.local | Yes — reads real stored ConfirmedPortfolio written on confirm | FLOWING |

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| Backend portfolio tests pass | `python -m pytest tests/test_portfolio_generate.py tests/test_portfolio_confirm.py tests/test_portfolio_swap_preview.py tests/test_portfolio_confirmed.py -x` | 17 passed | PASS |
| All original portfolio tests unbroken | `python -m pytest tests/test_portfolio.py tests/test_portfolio_swap.py -x` | 12 passed | PASS |
| Extension TypeScript compiles cleanly | `npx tsc --noEmit` | Exit 0, no output | PASS |
| All extension tests pass | `npm test -- --run` | 30 passed (3 test files) | PASS |
| Overlay panel renders on EA Web App | Requires Chrome + live EA Web App | Not runnable programmatically | SKIP (human) |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| PORT-01 | 07-01-PLAN.md | Backend exposes endpoint to generate OP sell portfolio and seeds portfolio_slots | SATISFIED | POST /generate (read-only preview) + POST /confirm (clean-slate seed). Two-step flow fulfills the spirit of PORT-01 — user confirms before seeding |
| UI-01 | 07-02-PLAN.md, 07-03-PLAN.md | Overlay panel injected into EA Web App showing portfolio (player name, buy price, OP price, margin) | PARTIAL — automated checks pass, visual confirmation is human-only | panel.ts renders name, buy price (price field), sell_price, margin_pct in all three states; tests verify DOM elements exist |
| UI-03 | 07-02-PLAN.md, 07-03-PLAN.md | User can remove a player and receive replacement player(s) from the backend | PARTIAL — automated checks pass, visual confirmation is human-only | X button in renderDraft sends PORTFOLIO_SWAP, splice replacements into draftPlayers, re-render; overlay test `test_swap_preview_returns_replacements` passing |

**Note on PORT-01 and ROADMAP wording:** The ROADMAP Success Criterion #1 states POST /generate "seeds portfolio_slots", but the PLAN correctly implements a two-step flow where /generate is read-only and /confirm does the seeding. All three plans are consistent with this design. The intent of PORT-01 — user can generate a portfolio that seeds the DB — is fulfilled by the generate+confirm flow together.

**Orphaned requirements check:** All three phase-7 requirements (PORT-01, UI-01, UI-03) appear in PLAN frontmatter. No orphaned requirements.

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| `src/server/api/portfolio.py` | 325 | `datetime.utcnow()` — deprecated in Python 3.12 | Info | No functional impact; test warnings only. Not a stub |
| `extension/entrypoints/background.ts` | 199, 202 | `return null` in `pingActiveTab()` | Info | Legitimate error path in ping helper — not in portfolio flow |

No blocking anti-patterns found. No TODO/FIXME/placeholder/stub patterns in portfolio implementation code.

### Human Verification Required

#### 1. Overlay Panel on EA Web App

**Test:**
1. Start the backend: `cd C:/Users/maftu/Projects/op-seller && python -m src.server.main` (ensure scanner has scored players)
2. Build the extension: `cd C:/Users/maftu/Projects/op-seller/extension && npm run build`
3. Load in Chrome: chrome://extensions -> Load unpacked -> `extension/.output/chrome-mv3`
4. Navigate to `https://www.ea.com/ea-sports-fc/ultimate-team/web-app/`
5. Verify: "OP" toggle tab visible on right edge
6. Click toggle — 320px dark sidebar slides in from right
7. Type a budget (e.g., 200000), click Generate
8. Verify: player list with name, rating, position, buy price, sell price, margin %, profit, OP ratio
9. Click X on a player — verify it disappears and replacement appears
10. Click Confirm — verify panel shows read-only confirmed state (no X buttons, Regenerate visible)
11. Close and reopen the EA Web App tab — verify confirmed portfolio loads automatically
12. Click Regenerate — panel returns to empty state with budget input
13. Navigate between EA Web App pages — verify panel persists

**Expected:** Smooth slide-in, dark theme matches EA Web App, all player fields display, swap replaces player in same position, confirmed state persists across tab close/reopen, panel survives SPA navigation.

**Why human:** Visual rendering, SPA navigation survival, and chrome.storage.local cross-session persistence cannot be verified without a running Chrome instance. Test environment uses jsdom, not a real browser.

---

## Gaps Summary

No automated gaps. The phase goal is verified at all four levels (exists, substantive, wired, data-flowing) for all programmatically checkable behaviors. One truth (visual overlay on EA Web App) requires human verification and is flagged as the only remaining blocker before phase completion.

---

_Verified: 2026-03-27T12:10:00Z_
_Verifier: Claude (gsd-verifier)_
