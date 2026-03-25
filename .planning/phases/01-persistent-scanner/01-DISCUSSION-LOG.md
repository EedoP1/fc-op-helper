# Phase 1: Persistent Scanner - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md -- this log preserves the alternatives considered.

**Date:** 2026-03-25
**Phase:** 01-persistent-scanner
**Areas discussed:** Top-players endpoint, Scan priority logic, Failure transparency, Staleness threshold

---

## Top-players Endpoint

### Default player count

| Option | Description | Selected |
|--------|-------------|----------|
| 50 players | Good default for quick overview, matches transfer list capacity | |
| 100 players | Matches TARGET_PLAYER_COUNT, full list in one call | ✓ |
| 25 players | Lightweight, fast, best for frequent polling | |

**User's choice:** 100 players
**Notes:** None

### Filtering params

| Option | Description | Selected |
|--------|-------------|----------|
| Basic filters | price_min, price_max, limit params | ✓ |
| Full filters | Price range + min_margin + min_op_ratio + sort_by | |
| No filters | Just top 100 sorted by efficiency | |

**User's choice:** Basic filters
**Notes:** None

### Pagination

| Option | Description | Selected |
|--------|-------------|----------|
| Offset/limit | Simple ?offset=0&limit=100 params | ✓ |
| No pagination | Always returns up to limit results | |
| Cursor-based | Keyset pagination with ?after=<id> | |

**User's choice:** Offset/limit
**Notes:** None

### Response shape

| Option | Description | Selected |
|--------|-------------|----------|
| Score summary | name, ea_id, price, margin_pct, op_ratio, expected_profit, efficiency, last_scanned | ✓ |
| Minimal | name, ea_id, price, efficiency, last_scanned | |
| Full detail | Everything plus sales array, price history, live listings count | |

**User's choice:** Score summary
**Notes:** None

---

## Scan Priority Logic

### Priority signal

| Option | Description | Selected |
|--------|-------------|----------|
| Listing activity | Players with more live listings and recent sales get scanned more often | ✓ |
| Score-based | High-scoring OP sell candidates get priority | |
| Hybrid | Combine listing activity + current score | |

**User's choice:** Listing activity
**Notes:** None

### Frequency tiers

| Option | Description | Selected |
|--------|-------------|----------|
| 3 tiers | Hot (30 min), Normal (1 hr), Cold (2-3 hrs) | ✓ |
| Continuous adaptive | Next scan = time since last listing activity | |
| 2 tiers | Active (1 hr), Inactive (4 hrs) | |

**User's choice:** 3 tiers
**Notes:** None

### Tier reclassification

| Option | Description | Selected |
|--------|-------------|----------|
| Every scan | Check listing activity and reassign tier after each scan | ✓ |
| Periodic batch | Reclassify all players every 6 hours in separate job | |

**User's choice:** Every scan
**Notes:** None

### Bootstrap strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Full discovery scan | Run discover_players() across 11k-200k on startup | ✓ |
| Gradual discovery | Discover a few pages at a time alongside normal ops | |
| Import from CSV | Seed from existing CSV, discover rest over time | |

**User's choice:** Full discovery scan
**Notes:** None

---

## Failure Transparency

### Visibility level

| Option | Description | Selected |
|--------|-------------|----------|
| Health endpoint + logs | Health shows scanner status, success rate, circuit breaker state. Errors to log file | ✓ |
| Logs only | All failure info to log files. Health shows basic up/down | |
| Health + console alerts | Health + logs + real-time console warnings on circuit breaker trips | |

**User's choice:** Health endpoint + logs
**Notes:** None

### Health endpoint detail

| Option | Description | Selected |
|--------|-------------|----------|
| Operational summary | Running/stopped, success rate, CB state, last scan, player count, queue depth | ✓ |
| Minimal | Running/stopped, last scan timestamp | |
| Detailed diagnostics | All summary fields plus per-tier counts, avg duration, error breakdown | |

**User's choice:** Operational summary
**Notes:** None

### Circuit breaker API behavior

| Option | Description | Selected |
|--------|-------------|----------|
| Serve stale data | Return last-known scores with is_stale flag and last_scanned timestamp | ✓ |
| Degrade gracefully | Return results with warning header about reduced freshness | |
| Return error | 503 with circuit breaker status when data too old | |

**User's choice:** Serve stale data
**Notes:** None

---

## Staleness Threshold

### Stale after

| Option | Description | Selected |
|--------|-------------|----------|
| 4 hours | 2-4 scan cycles to refresh, balances freshness with scanning capacity | ✓ |
| 2 hours | Strict freshness, may reduce result count during load | |
| 8 hours | Lenient, more players but less actionable data | |

**User's choice:** 4 hours
**Notes:** None

### Stale player handling

| Option | Description | Selected |
|--------|-------------|----------|
| Include with flag | Return stale players with is_stale: true, let consumer decide | ✓ |
| Exclude entirely | Only return players within freshness window | |
| Separate section | Fresh players first, then separate stale array | |

**User's choice:** Include with flag
**Notes:** None

### Target pool coverage

| Option | Description | Selected |
|--------|-------------|----------|
| 80% fresh | 80% of discovered players scanned within staleness window | ✓ |
| 95% fresh | Near-complete, may risk rate limiting | |
| 60% fresh | Conservative, prioritize top performers | |

**User's choice:** 80% fresh
**Notes:** None

---

## Claude's Discretion

- Database schema design
- Rate limit backoff parameters and jitter
- Circuit breaker implementation details
- APScheduler job configuration
- Server startup/shutdown lifecycle
- Project layout for server code

## Deferred Ideas

None -- discussion stayed within phase scope.
