---
gsd_state_version: 1.0
milestone: v1.1
milestone_name: Chrome Extension — Automated OP Sell Cycle
status: Ready to plan
stopped_at: Completed 07-portfolio-management plan 03 — awaiting checkpoint human-verify
last_updated: "2026-03-27T09:08:32.920Z"
last_activity: 2026-03-27
progress:
  total_phases: 4
  completed_phases: 3
  total_plans: 8
  completed_plans: 8
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-26)

**Core value:** Always-fresh, data-driven OP sell recommendations — the server continuously scores every player in the 11k–200k range so you never miss a profitable opportunity.
**Current focus:** Phase 07 — portfolio-management

## Current Position

Phase: 8
Plan: Not started

## Performance Metrics

**Velocity:**

- Total plans completed: 10 (v1.0)
- Average duration: ~5-10 min/plan
- Total execution time: ~2 days (v1.0)

**By Phase:**

| Phase | Plans | Duration | Notes |
|-------|-------|----------|-------|
| 1-4 (v1.0) | 10 | ~2 days | 127 commits, ~18k LOC |

**Recent Trend:**

- Last 5 plans: quick tasks (scoring cleanup)
- Trend: Strong velocity on backend Python work

*Updated after each plan completion*
| Phase 05-backend-infrastructure P01 | 3 | 3 tasks | 4 files |
| Phase 05-backend-infrastructure P02 | 18 | 1 tasks | 3 files |
| Phase 05-backend-infrastructure P03 | 3 | 2 tasks | 5 files |
| Phase 06-extension-architecture-foundation P01 | 8 | 2 tasks | 10 files |
| Phase 06-extension-architecture-foundation P02 | 6 | 3 tasks | 3 files |
| Phase 07-portfolio-management P02 | 3 | 2 tasks | 6 files |
| Phase 07-portfolio-management P01 | 15 | 2 tasks | 5 files |
| Phase 07-portfolio-management P03 | 20 | 1 tasks | 3 files |

## Accumulated Context

### Decisions

- [v1.0]: D-10 expected_profit_per_hour is canonical scoring metric; v1 scorer deleted
- [v1.0]: Fixed 5-min scan interval; adaptive scheduling removed
- [v1.0]: Proportional outcome resolution (min(matching_sales, n_listings) sold, rest expired)
- [v1.1 research]: WXT over Plasmo/CRXJS for extension build (Plasmo maintenance lag; CRXJS archival risk)
- [v1.1 research]: All backend calls route through service worker — content scripts never call backend directly (Chrome CORS constraint)
- [v1.1 research]: Relist price is locked at original margin — does not refresh on relist
- [Phase 05-backend-infrastructure]: Use allow_origin_regex for CORS — allow_origins wildcard does not cover chrome-extension:// scheme
- [Phase 05-backend-infrastructure]: PortfolioSlot.ea_id uses unique=True on column only — no __table_args__ Index (avoids duplicate index creation)
- [Phase 05-backend-infrastructure]: Idempotent GET /pending checks for existing IN_PROGRESS action before PENDING query to prevent duplicate action creation
- [Phase 05-backend-infrastructure]: player_name on derived TradeActions uses 'Player {ea_id}' placeholder — PortfolioSlot stores no name; extension provides real names via POST /portfolio/slots
- [Phase 05-backend-infrastructure]: Profit EA tax applied in Python after SQL group-by — avoids float precision in case() expressions
- [Phase 05-backend-infrastructure]: DELETE /portfolio/{ea_id} preserves TradeRecords — only removes active PortfolioSlot and cancels pending actions
- [Phase 05-backend-infrastructure]: Replacements via optimize_portfolio() on freed_budget — reuses existing optimizer with fresh _build_scored_entry dicts
- [Phase 06-extension-architecture-foundation]: Use Promise-based chrome.alarms.get() — fake-browser returns Promise, callback form receives undefined in tests
- [Phase 06-extension-architecture-foundation]: WXT defineBackground() returns config object, does not auto-execute main() — tests must call bg.main() directly
- [Phase 06-extension-architecture-foundation]: Add types: ['chrome'] to extension/tsconfig.json — WXT auto-generated tsconfig omits @types/chrome
- [Phase 06-extension-architecture-foundation]: Explicit case 'PONG' in content script switch — TypeScript requires all discriminated union variants handled for assertNever to receive never type
- [Phase 06-extension-architecture-foundation]: fakeBrowser.runtime.onMessage.trigger() does not pass sendResponse callback — content script tests use addListener spy + direct handler invocation
- [Phase 07-portfolio-management]: PORTFOLIO_* request types handled only in service worker, not content script — content script returns false for those types to maintain exhaustive switch without phantom handling
- [Phase 07-portfolio-management]: mapToPortfolioPlayer normalizes buy_price/price field name variants from different backend endpoints
- [Phase 07-portfolio-management]: Two-step portfolio flow: generate is read-only preview, confirm does clean-slate seed of portfolio_slots
- [Phase 07-portfolio-management]: swap-preview is stateless: caller provides excluded_ea_ids, no PortfolioSlot reads required
- [Phase 07-portfolio-management]: Panel declared before wxt:locationchange handler to avoid TDZ — const panel hoisted above ctx.addEventListener
- [Phase 07-portfolio-management]: PORTFOLIO_LOAD guarded by ctx.isInvalid check to preserve existing test contract (no sendMessage when ctx invalid)

### Roadmap Evolution

- Phase 07.1 inserted after Phase 07: Portfolio Dashboard & Trade Tracking (user request — manual visibility before automation)

### Pending Todos

None yet.

### Blockers/Concerns

- [Phase 8]: EA Web App DOM internals are LOW confidence. Selectors, ARIA attributes, and window.services method names for FC26 must be verified by live DevTools inspection before any automation code is written. Phase 8 planning must open with an exploration task.
- [Phase 8]: EA daily transaction cap threshold unpublished — set automation conservatively at 500/day initially, adjust empirically.

### Quick Tasks Completed

| # | Description | Date | Commit | Directory |
|---|-------------|------|--------|-----------|
| 260326-ufn | Fix all FUTBIN findings: deduplicate snapshot_sales, populate player names, set scorer_version | 2026-03-26 | ed8adc8 | [260326-ufn-fix-all-futbin-findings-deduplicate-snap](./quick/260326-ufn-fix-all-futbin-findings-deduplicate-snap/) |
| 260326-vkj | Fix resolve_outcomes double-counting: timestamp-filter completedAuctions by last_resolved_at | 2026-03-26 | 0af76c8 | [260326-vkj-fix-listing-tracker-resolve-outcomes-dou](./quick/260326-vkj-fix-listing-tracker-resolve-outcomes-dou/) |
| 260326-wac | Build FUTBIN health monitor CLI with audit report | 2026-03-26 | f45f78d | [260326-wac-build-futbin-health-monitor-hourly-sched](./quick/260326-wac-build-futbin-health-monitor-hourly-sched/) |

## Session Continuity

Last activity: 2026-03-27
Stopped at: Completed 07-portfolio-management plan 03 — awaiting checkpoint human-verify
Resume file: None
