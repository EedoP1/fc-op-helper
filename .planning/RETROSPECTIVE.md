# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v1.0 — FC26 OP Sell Platform MVP

**Shipped:** 2026-03-26
**Phases:** 4 | **Plans:** 10 | **Timeline:** 2 days (2026-03-24 → 2026-03-26)

### What Was Built
- Persistent scanner backend (FastAPI + SQLite WAL + APScheduler) scanning ~1800 players every 5 minutes
- Full REST API — portfolio optimization, player detail with trends, top players, health endpoint
- CLI rewritten as thin API client consuming the backend
- Listing-tracking scoring system with fingerprint-based observation, outcome resolution, and D-10 expected_profit_per_hour formula
- 10 post-milestone quick tasks for formula refinement, dead code cleanup, and scan optimization

### What Worked
- Phase-based execution with clear goals and success criteria kept scope tight
- Protocol-based abstraction (MarketDataClient) made it easy to swap data layers
- Quick tasks (`/gsd:quick`) were effective for rapid iteration on scoring formula after initial phases shipped
- SQLite WAL mode with async sessions scaled well for single-user 24/7 operation

### What Was Inefficient
- Phase 2 roadmap tracking showed 0/2 plans while execution was actually complete — state sync gap
- Scoring formula went through multiple iterations (v1 → v2, then 5+ quick tasks refining v2) — could have spent more time in discuss phase defining the scoring model upfront
- Adaptive scan scheduling was built in Phase 2 then replaced with fixed 5-min interval in quick tasks — wasted effort on a feature that was removed

### Patterns Established
- `expire_on_commit=False` required on all async session factories to prevent MissingGreenlet errors
- WAL mode enabled via sync_engine connect event listener for reliability
- Fingerprint strategy: tradeId when present, fallback to (ea_id:buyNowPrice:10min-bucket)
- Proportional outcome resolution for same-price listing ambiguity

### Key Lessons
1. Scoring model design deserves deep upfront research — the v1→v2 transition and subsequent formula iterations consumed significant effort
2. Fixed scan intervals beat adaptive scheduling for simplicity and predictability at this scale
3. Quick tasks are ideal for post-phase refinement but should be tracked in roadmap state to avoid sync gaps
4. Integration testing (server + CLI together) catches issues that unit tests miss — always run the full stack

### Cost Observations
- Rapid 2-day execution from CLI tool to persistent platform
- Heavy use of parallel subagents for phase execution
- Quick tasks used balanced model profile effectively

---

## Cross-Milestone Trends

| Metric | v1.0 |
|--------|------|
| Phases | 4 |
| Plans | 10 |
| Timeline | 2 days |
| Quick tasks | 10 |
| Commits | 127 |
| LOC | ~18k |
