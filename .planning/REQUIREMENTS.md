# Requirements: FC26 OP Sell Platform

**Defined:** 2026-03-25
**Core Value:** Always-fresh, data-driven OP sell recommendations — the server continuously scores every player in the 11k–200k range so you never miss a profitable opportunity.

## v1 Requirements

Requirements for initial release. Each maps to roadmap phases.

### Backend — Scanner

- [x] **SCAN-01**: Server runs a persistent scanner for all players in the 11k–200k price range
- [x] **SCAN-02**: Scanner stores player scores, market data, and price history in SQLite
- [x] **SCAN-03**: Scanner uses adaptive scheduling per player based on listing activity (e.g., if last listing was 32 mins ago, schedule next scan in ~32 mins)
- [x] **SCAN-04**: Scanner respects fut.gg rate limits with throttling, exponential backoff, and circuit breaker
- [x] **SCAN-05**: Historical score data accumulates over time per player for trend analysis

### Backend — API

- [x] **API-01**: REST API endpoint returns optimized OP sell portfolio for a given budget, built on-demand from accumulated historical data
- [x] **API-02**: REST API endpoint returns detailed score breakdown for a specific player (margin, op_ratio, expected_profit, efficiency, sales history)
- [x] **API-03**: REST API endpoint returns top OP sell players with scores, margins, and ratios
- [x] **API-04**: Scanner prioritizes request budget — more frequent scans for high-value/high-activity players, less frequent for stale ones

### CLI Client

- [x] **CLI-01**: CLI queries the server API instead of scoring directly
- [x] **CLI-02**: CLI accepts a budget and displays the optimized portfolio from the API
- [x] **CLI-03**: CLI can show detailed score breakdown for a specific player

## v2 Requirements

### Chrome Extension

- **EXT-01**: Auto-buy recommended players at or below target price on EA Web App
- **EXT-02**: Auto-list purchased players at the calculated OP sell price
- **EXT-03**: Auto-relist cards that didn't sell
- **EXT-04**: Show transfer list status (sold, expired, active) in extension popup

### Tracking & Analytics

- **TRCK-01**: Record buy price, sell price, and net profit for each transaction
- **TRCK-02**: Running total of profit/loss per trading session
- **TRCK-03**: Separate web dashboard showing analytics and performance

### Advanced Features

- **ADV-01**: Market momentum alerts when a player's OP score jumps significantly
- **ADV-02**: Player filter presets for quick session starts
- **ADV-03**: Scan coverage indicator showing % of pool scored in last N hours
- **ADV-04**: Score confidence indicator based on data point count
- **ADV-05**: User accounts and paid tiers

## Out of Scope

Explicitly excluded. Documented to prevent scope creep.

| Feature | Reason |
|---------|--------|
| Sniping / buying underpriced cards | Different strategy, different risk profile, 10 other tools exist |
| Mass bidding automation | High EA detection surface, not complementary to OP sell |
| SBC solver | Zero relation to OP sell profitability, massive scope |
| Multi-account management | Increases ban risk and complexity dramatically |
| In-game console automation | Chrome extension on EA Web App only |
| FUTBIN integration | Already removed once, adds fragility for no accuracy gain |
| Price manipulation detection | Speculative, legally grey, not needed |
| Social/community trading signals | Keep recommendations algorithmic, not social trust |
| Mobile app | Web dashboard accessible from mobile browser is sufficient |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| SCAN-01 | Phase 1 | Complete |
| SCAN-02 | Phase 1 | Complete |
| SCAN-03 | Phase 2 | Complete |
| SCAN-04 | Phase 1 | Complete |
| SCAN-05 | Phase 2 | Complete |
| API-01 | Phase 2 | Complete |
| API-02 | Phase 2 | Complete |
| API-03 | Phase 1 | Complete |
| API-04 | Phase 1 | Complete |
| CLI-01 | Phase 3 | Complete |
| CLI-02 | Phase 3 | Complete |
| CLI-03 | Phase 3 | Complete |

**Coverage:**
- v1 requirements: 12 total
- Mapped to phases: 12
- Unmapped: 0 ✓

---
*Requirements defined: 2026-03-25*
*Last updated: 2026-03-25 after roadmap creation*
