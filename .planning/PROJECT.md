# FC26 OP Sell Platform

## What This Is

A platform that finds the best FC26 Ultimate Team players to OP sell (list above market price), automates the buy/list/relist cycle via a Chrome extension, and tracks profit performance — all powered by a persistent backend that monitors the market 24/7. Starting as a personal tool, evolving toward a paid product.

## Core Value

Always-fresh, data-driven OP sell recommendations — the server continuously scores every player in the 11k–200k range so you never miss a profitable opportunity.

## Requirements

### Validated

- ✓ OP sell scoring with price-at-time verification — existing
- ✓ fut.gg API integration (player discovery, prices, sales, history) — existing
- ✓ Portfolio optimization (efficiency sorting, swap loop, backfill) — existing
- ✓ CLI output with Rich tables and CSV export — existing
- ✓ Protocol-based data source abstraction (MarketDataClient) — existing
- ✓ Pydantic data models (Player, SaleRecord, PricePoint) — existing
- ✓ Persistent backend server (FastAPI + SQLite) running 24/7 — Validated in Phase 1: Persistent Scanner
- ✓ Scheduled scanning per player with tier-based priority (11k–200k range) — Validated in Phase 1: Persistent Scanner
- ✓ REST API top-players endpoint with scores, margins, ratios — Validated in Phase 1: Persistent Scanner

### Active

- ✓ REST API exposing player details, score history, budget portfolio — Validated in Phase 2: Full API Surface
- [ ] CLI thin client that queries the API (replaces direct scoring)
- [ ] Chrome extension for EA Web App automation (buy, list, relist)
- [ ] Profit tracking and performance analytics
- [ ] Separate web dashboard for analytics and monitoring
- [ ] User accounts and paid tiers (future)

### Out of Scope

- FUTBIN integration — removed previously, adds complexity and rate limiting issues
- Mobile app — web-first approach
- Console/in-game automation — Chrome extension on EA Web App only
- Multi-game support — FC26 only

## Context

- Existing Python 3.12 CLI tool with working OP scoring engine
- Data source: fut.gg API (player discovery, prices, 100 recent sales, hourly price history)
- Scoring approach: price-at-time verified OP detection across margin tiers (40% down to 3%), minimum 3 OP sales required
- Phase 1 complete: persistent backend with FastAPI, SQLite WAL, APScheduler, circuit breaker, tier-based scanning
- Phase 2 complete: full API surface — portfolio endpoint, player detail with trends, adaptive scan scheduling
- fut.gg updates hourly price history, so hourly scanning per player is the right cadence
- Price range 11k–200k keeps the player pool manageable and focused on liquid cards
- Architecture already has protocol-based abstraction — good foundation for adding persistence layer

## Constraints

- **Data source**: fut.gg API only — no FUTBIN, no EA API direct access for data
- **Rate limiting**: Must respect fut.gg rate limits with smart throttling for 24/7 operation
- **Tech stack**: Python backend (keep existing scoring), TypeScript for Chrome extension
- **Hosting**: Local machine initially, but architecture must support cloud deployment later
- **Storage**: SQLite for now, designed to migrate to PostgreSQL when scaling to multi-user

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Chrome extension for automation | Overlays on EA Web App, avoids reverse-engineering EA APIs | — Pending |
| Python backend with FastAPI | Keeps existing scoring logic, adds API + scheduler naturally | Validated Phase 1 |
| SQLite initially | Simple, no infrastructure needed for personal use, migrateable later | Validated Phase 1 |
| CLI becomes API client | All logic on server (needs DB for proper scoring), CLI just displays results | — Pending |
| Hourly scan cadence | Matches fut.gg price history granularity, respects rate limits | — Pending |
| 11k–200k price range | Focused on liquid, profitable cards; avoids scanning entire market | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-03-25 after Phase 1 completion*
