# FC26 OP Sell Platform

## What This Is

A platform that finds the best FC26 Ultimate Team players to OP sell (list above market price), powered by a persistent backend that monitors the market 24/7, scores every player using listing-tracking-based outcome data, and serves recommendations via REST API and CLI. Starting as a personal tool, evolving toward a paid product with Chrome extension automation.

## Core Value

Always-fresh, data-driven OP sell recommendations — the server continuously scores every player in the 11k–200k range so you never miss a profitable opportunity.

## Requirements

### Validated

- ✓ Persistent backend server (FastAPI + SQLite) running 24/7 — v1.0
- ✓ Scheduled scanning of all players in 11k–200k range every 5 minutes — v1.0
- ✓ REST API top-players endpoint with scores, margins, ratios — v1.0
- ✓ REST API portfolio endpoint with budget-aware optimization — v1.0
- ✓ REST API player detail endpoint with trend indicators and score history — v1.0
- ✓ Adaptive scan scheduling based on listing activity — v1.0
- ✓ Historical score accumulation per player for trend analysis — v1.0
- ✓ CLI thin client querying the API (no direct fut.gg calls) — v1.0
- ✓ Listing-tracking scoring with fingerprint-based observation and outcome resolution — v1.0
- ✓ D-10 expected_profit_per_hour formula replacing snapshot-based scoring — v1.0
- ✓ Circuit breaker and retry logic for fut.gg API resilience — v1.0
- ✓ Protocol-based data source abstraction (MarketDataClient) — pre-v1.0

### Active

- [ ] Chrome extension for EA Web App automation (buy, list, relist)
- [ ] Profit tracking and performance analytics
- [ ] Separate web dashboard for analytics and monitoring
- [ ] User accounts and paid tiers

### Out of Scope

- FUTBIN integration — removed previously, adds complexity and rate limiting issues
- Mobile app — web-first approach
- Console/in-game automation — Chrome extension on EA Web App only
- Multi-game support — FC26 only
- Sniping / buying underpriced cards — different strategy entirely
- Mass bidding automation — high EA detection surface
- SBC solver — zero relation to OP sell profitability

## Context

Shipped v1.0 with ~18k LOC Python across 115 files, 127 commits over 2 days.

**Tech stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 async, aiosqlite, APScheduler, httpx, Pydantic, Rich, Click

**Current state:**
- Backend scans ~1800 players every 5 minutes with circuit breaker protection
- Listing-tracking scorer (v2) computes expected_profit_per_hour from D-10 observation window
- CLI displays portfolio ranked by expected_profit_per_hour
- 10 quick tasks completed post-phase-4 for scoring formula refinement and cleanup

**Known issues:**
- fut.gg has no published rate limits; 24/7 scanning behavior is empirically tuned
- Phase 2 ROADMAP showed 0/2 plans but execution was complete on disk (tracking inconsistency)

## Constraints

- **Data source**: fut.gg API only — no FUTBIN, no EA API direct access for data
- **Rate limiting**: Must respect fut.gg rate limits with smart throttling for 24/7 operation
- **Tech stack**: Python backend (keep existing scoring), TypeScript for Chrome extension
- **Hosting**: Local machine initially, but architecture must support cloud deployment later
- **Storage**: SQLite for now, designed to migrate to PostgreSQL when scaling to multi-user

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Python backend with FastAPI | Keeps existing scoring logic, adds API + scheduler naturally | ✓ Good — v1.0 |
| SQLite with WAL mode | Simple, no infrastructure needed, async via aiosqlite | ✓ Good — v1.0 |
| CLI becomes API client | All logic on server, CLI just displays results | ✓ Good — v1.0 |
| Listing-tracking replaces snapshot scoring | Fingerprint-based observation + outcome resolution gives true sell rates | ✓ Good — v1.0 |
| D-10 expected_profit_per_hour formula | Uses 10-day observation window for scoring accuracy | ✓ Good — v1.0 |
| Fixed 5-min scan interval | Replaced adaptive scheduling — simpler and ensures coverage | ✓ Good — v1.0 |
| Proportional outcome resolution | min(matching_sales, n_listings) sold, rest expired — handles price ambiguity | ✓ Good — v1.0 |
| Chrome extension for automation | Overlays on EA Web App, avoids reverse-engineering EA APIs | — Pending (v2) |
| 11k–200k price range | Focused on liquid, profitable cards; avoids scanning entire market | ✓ Good — v1.0 |

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
*Last updated: 2026-03-26 after v1.0 milestone*
