---
gsd_state_version: 1.0
milestone: v1.1
milestone_name: Chrome Extension — Automated OP Sell Cycle
status: Ready to plan
stopped_at: Completed 07.1-03-PLAN.md
last_updated: "2026-03-27T10:51:42.938Z"
last_activity: 2026-03-27
progress:
  total_phases: 6
  completed_phases: 4
  total_plans: 11
  completed_plans: 11
---

# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-03-26)

**Core value:** Always-fresh, data-driven OP sell recommendations — the server continuously scores every player in the 11k–200k range so you never miss a profitable opportunity.
**Current focus:** Phase 07.1 — trade-reporting

## Current Position

Phase: 07.2
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
| Phase 07.1-trade-reporting P01 | 5 | 1 tasks | 1 files |
| Phase 07.1-trade-reporting P02 | 2 | 1 tasks | 2 files |
| Phase 07.1-trade-reporting P03 | 10 | 2 tasks | 8 files |

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
- [Phase 07.1-trade-reporting]: outcome-to-action_type mapping is static: bought->buy, listed/sold/expired->list — sold and expired both follow a list action
- [Phase 07.1-trade-reporting]: direct trade record endpoint validates ea_id in portfolio_slots before insert — only portfolio players tracked (D-03)
- [Phase 07.1-trade-reporting]: readTransferList() accepts Document|Element root parameter — enables unit testing with jsdom fixtures without patching globals
- [Phase 07.1-trade-reporting]: isTimeRemaining() helper detects EA time strings (55 Minutes, 1 Hour) as active listings — EA FC26 does not use a simple Active keyword
- [Phase 07.1-trade-reporting]: Composite dedup key {ea_id}:{outcome}:{price} in reportedOutcomesItem — uniquely identifies trade events without a backend round-trip

### Roadmap Evolution

- Phase 07.1 inserted after Phase 07: Trade Reporting — extension passively reads EA Web App DOM to detect and auto-report trade outcomes (user request — data pipeline for dashboard)
- Phase 07.2 inserted after Phase 07.1: Portfolio Dashboard & Trade Tracking — dashboard UI consuming trade data from 07.1 (split from original 07.1 — DOM reading and dashboard UI are different work)

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
| 260327-gxd | Add volatility filter to exclude players with >30% price increase over 3 days | 2026-03-27 | c55dc80 | [260327-gxd-add-volatility-filter-to-exclude-players](./quick/260327-gxd-add-volatility-filter-to-exclude-players/) |
| 260327-hus | Fix volatility filter to use SnapshotPricePoint MIN/MAX instead of MarketSnapshot earliest-vs-latest | 2026-03-27 | 8d4b8ea | [260327-hus-fix-volatility-filter-to-use-snapshotpri](./quick/260327-hus-fix-volatility-filter-to-use-snapshotpri/) |

## Session Continuity

Last activity: 2026-03-27
Stopped at: Completed 07.1-03-PLAN.md
Resume file: None
