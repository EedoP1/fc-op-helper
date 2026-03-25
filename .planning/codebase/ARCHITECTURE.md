# Architecture

**Analysis Date:** 2026-03-25

## Pattern Overview

**Overall:** Data Pipeline with Dependency Inversion

The OP Seller codebase follows a **layered, unidirectional data flow** pattern where each component has a single responsibility:
1. **Discovery** - identify candidate players within a price range
2. **Data Fetching** - retrieve market data from external API
3. **Scoring** - evaluate each player's OP selling potential
4. **Optimization** - select the best portfolio within budget constraints
5. **Display & Export** - present results to the user

**Key Characteristics:**
- **Protocol-based abstraction** - `MarketDataClient` protocol allows swapping data sources without changing business logic
- **Stateless scoring** - Pure functions in scorer and optimizer, no side effects
- **Async/concurrent HTTP** - Efficient batch data fetching with configurable concurrency
- **Separation of concerns** - CLI, display, business logic, and data access are isolated

## Layers

**Presentation Layer:**
- Purpose: Display results and export data
- Location: `src/main.py` (lines 83-156)
- Contains: `display_results()` function with Rich tables, `export_csv()` for output
- Depends on: Scored player dicts from scorer
- Used by: CLI entry point

**Business Logic Layer:**
- Purpose: Implement domain algorithms (OP detection, portfolio optimization)
- Location: `src/scorer.py`, `src/optimizer.py`
- Contains: Pure scoring functions and optimization logic
- Depends on: `PlayerMarketData` models and config constants
- Used by: Main pipeline

**Data Access Layer:**
- Purpose: Fetch and assemble data from external APIs
- Location: `src/futgg_client.py` (implements `MarketDataClient` protocol)
- Contains: HTTP client, API endpoint methods, data parsing
- Depends on: httpx, Pydantic models
- Used by: Main pipeline (via protocol)

**Infrastructure Layer:**
- Purpose: Define contracts and data structures
- Location: `src/models.py`, `src/protocols.py`, `src/config.py`
- Contains: Pydantic models, Protocol interfaces, configuration constants
- Depends on: Standard library, Pydantic
- Used by: All other layers

**CLI Layer:**
- Purpose: Accept user input and orchestrate the pipeline
- Location: `src/main.py` (lines 159-175)
- Contains: Click decorators, logging setup, asyncio entrypoint
- Depends on: Business logic and data access layers
- Used by: Users via `python -m src.main`

## Data Flow

**Main Pipeline (run function):**

1. **Initialize** → Create client, start HTTP session
2. **Discover** → `client.discover_players(budget, min_price, max_price)` returns candidate list with ea_id
3. **Fetch** → `client.get_batch_market_data(ea_ids, concurrency=10)` returns `PlayerMarketData` list
4. **Score** → `score_player(md)` processes each player, returns scored dict or None
5. **Optimize** → `optimize_portfolio(scored, budget)` selects best players within budget
6. **Display** → `display_results()` renders table via Rich
7. **Export** → `export_csv()` writes timestamped CSV
8. **Cleanup** → `client.stop()` closes HTTP session

**Scoring Pipeline (internal):**

1. Get current buy price from `PlayerMarketData.current_lowest_bin`
2. Build price-at-time lookup from `price_history` (hourly granularity)
3. For each margin (40% → 3%):
   - Count sales where `sold_price >= price_at_time × (1 + margin)`
   - If count >= 3: accept this margin
   - If count < 3: try next lower margin
4. Calculate metrics: `net_profit`, `op_ratio`, `expected_profit`
5. Return scored dict or None if no viable margin found

**Optimization Pipeline (internal):**

1. Calculate `efficiency = expected_profit / buy_price` for each player
2. Sort by efficiency descending
3. Greedy fill up to TARGET_PLAYER_COUNT until budget exhausted
4. Swap loop: Replace most expensive card with multiple cheaper alternatives if profitable
5. Backfill remaining budget with unsorted candidates
6. Sort final selection by expected_profit descending

**State Management:**

- **No persistent state** - All computation is stateless
- **Temporary state in functions** - Local variables for price lookups, efficiency tracking
- **Client state** - `FutGGClient` holds HTTP session (created at start, closed at stop)
- **User input** - Only `--budget` flag required; discovery auto-ranges it

## Key Abstractions

**MarketDataClient Protocol:**
- Purpose: Define contract for any data source (fut.gg, FUTBIN, mock, etc.)
- Examples: `FutGGClient` implements full protocol, tests use `MockMarketDataClient`
- Pattern: Structural subtyping - any class with required async methods matches

**PlayerMarketData:**
- Purpose: Aggregate object containing all data needed to score a player
- Instances: One per player fetched from API
- Pattern: Pydantic model for validation + serialization

**Scored Dict:**
- Purpose: Carry player metadata + scoring results through pipeline
- Keys: `player`, `buy_price`, `sell_price`, `net_profit`, `margin_pct`, `op_sales`, `total_sales`, `op_ratio`, `expected_profit`, `efficiency`
- Used by: Display and export functions

## Entry Points

**CLI Entry Point:**
- Location: `src/main.py:159-175`
- Triggers: `python -m src.main --budget 1000000`
- Responsibilities:
  - Parse command-line arguments (--budget, --verbose)
  - Configure logging
  - Call async `run()` function

**Async Main Pipeline:**
- Location: `src/main.py:33-80`
- Triggers: Called by `main()` via `asyncio.run()`
- Responsibilities:
  - Orchestrate five-step pipeline
  - Handle client lifecycle (start/stop)
  - Catch exceptions in finally block

## Error Handling

**Strategy:** Graceful degradation with logging

**Patterns:**

- **HTTP Errors** - `FutGGClient._get()` catches `HTTPStatusError` and `Exception`, logs at ERROR level, returns None
- **Missing Data** - Scorer and optimizer check for None/empty collections before processing
- **API Pagination** - `discover_players()` breaks pagination loop if response is empty or has no "next" field
- **Parsing Failures** - `_parse_sales()` and `_parse_price_history()` catch exceptions per-record and skip malformed entries
- **Budget Exhaustion** - Greedy fill and swap loop naturally handle budget overflow by stopping selection

No exceptions propagate to CLI unless critical (e.g., client not started). Most failures result in reduced results or empty output.

## Cross-Cutting Concerns

**Logging:** `logging` module via `logger = logging.getLogger(__name__)` in modules
- `futgg_client.py` logs HTTP errors, page discovery progress
- `main.py` configures logging level (DEBUG if --verbose, INFO otherwise)
- Suppresses verbose httpx/httpcore logs unless DEBUG enabled

**Validation:** Pydantic models in `models.py` validate data types and structure
- `Player`, `SaleRecord`, `PricePoint`, `PlayerMarketData` all use BaseModel
- Invalid data rejected at model instantiation time
- Scorer and optimizer check logical constraints (min sales, margins, budget)

**Authentication:** Not required
- fut.gg API is public (no API key needed)
- Standard User-Agent header mimics browser traffic
- No cookie/session management

**Rate Limiting:** Soft delays only
- `FutGGClient._get()` adds 0.15s delay after each request
- Batch endpoints used where available to reduce request count
- `get_batch_market_data()` uses semaphore with `concurrency=10` to limit simultaneous requests

---

*Architecture analysis: 2026-03-25*
