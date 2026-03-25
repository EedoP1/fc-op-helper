# FC26 OP Sell List Generator

## What this does

Given a coin budget, finds the best players to OP sell (list above market price) on the FC26 Ultimate Team transfer market. Outputs a ranked list of ~100 players optimized for expected profit.

## How to run

```bash
python -m src.main --budget 1000000
```

## Project structure

```
src/
├── __init__.py
├── config.py        — Constants (EA 5% tax, target 100 players)
├── futgg_client.py  — fut.gg API client (discovery, prices, sales, history)
├── scorer.py        — OP sell scoring (price-at-time verified OP detection)
├── optimizer.py     — Portfolio optimizer (efficiency sorting, swap loop, backfill)
├── main.py          — CLI entry point, display, CSV export
└── models.py        — Pydantic data models (Player, SaleRecord, PricePoint, etc.)
```

## Data source: fut.gg API

- `/api/fut/players/v2/26/?page=N&price__gte=X&price__lte=Y` — paginated player list with price filtering
- `/api/fut/player-prices/26/{eaId}/` — current price, completedAuctions (100 sales), liveAuctions (all visible), hourly price history, momentum, overview
- `/api/fut/player-item-definitions/26/{eaId}/` — card definition (stats, club, league, nation, rarity)

## Scoring approach

For each player:
1. Get 100 most recent completed sales from fut.gg
2. Build price-at-time lookup from hourly price history
3. For each margin (40% down to 3%): count sales where `sold_price >= price_at_that_hour × (1 + margin)`
4. Pick the highest margin that has 3+ verified OP sales
5. `expected_profit = net_profit × op_ratio` (where op_ratio = op_sales / total_sales)
6. `efficiency = expected_profit / buy_price`
7. Sort by efficiency, fill budget greedily, swap loop replaces expensive cards with cheaper alternatives

## Filters

- Minimum 3 OP sales at the chosen margin (filters out lucky one-offs)
- Minimum 7 sales/hr (only liquid cards)
- Minimum 20 live listings (active market presence)
- Price range: 0.5%–10% of budget (prevents one card consuming the whole budget)

## Key design decisions

- **Price-at-time verification**: OP sales checked against market price when the sale happened, not current BIN. Prevents false OP detection from price drops.
- **Efficiency sorting**: `expected_profit / buy_price` naturally favors cheaper cards with decent OP ratios over expensive cards with tiny OP ratios. This fills ~60-70 slots instead of 14.
- **No FUTBIN dependency**: All scoring uses fut.gg API only. FUTBIN integration exists in git history but was removed — the sold/expired data was useful but added complexity and rate limiting issues.

## Python setup

Python 3.12. Dependencies: httpx, pydantic, rich, click (`pip install -r requirements.txt`).

<!-- GSD:project-start source:PROJECT.md -->
## Project

**FC26 OP Sell Platform**

A platform that finds the best FC26 Ultimate Team players to OP sell (list above market price), automates the buy/list/relist cycle via a Chrome extension, and tracks profit performance — all powered by a persistent backend that monitors the market 24/7. Starting as a personal tool, evolving toward a paid product.

**Core Value:** Always-fresh, data-driven OP sell recommendations — the server continuously scores every player in the 11k–200k range so you never miss a profitable opportunity.

### Constraints

- **Data source**: fut.gg API only — no FUTBIN, no EA API direct access for data
- **Rate limiting**: Must respect fut.gg rate limits with smart throttling for 24/7 operation
- **Tech stack**: Python backend (keep existing scoring), TypeScript for Chrome extension
- **Hosting**: Local machine initially, but architecture must support cloud deployment later
- **Storage**: SQLite for now, designed to migrate to PostgreSQL when scaling to multi-user
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Python 3.12 - Core application language
## Runtime
- Python 3.12.10 - Installed runtime
- pip - Dependency management
- Lockfile: requirements.txt (present)
## Frameworks
- httpx 0.28.1 - Async HTTP client for API requests
- pydantic 2.12.5 - Data validation and modeling
- click 8.3.1 - CLI command framework
- rich 14.3.3 - Terminal output formatting and display
- pytest 9.0.2 - Test runner
- pytest-asyncio 1.3.0 - Async test support
- None detected (no build framework required)
## Key Dependencies
- httpx 0.28.1 - Async HTTP client for fut.gg API calls, handles connection pooling and retry logic
- pydantic 2.12.5 - Data model validation for Player, PlayerMarketData, SaleRecord, PricePoint types
- click 8.3.1 - CLI argument parsing and command structure
- rich 14.3.3 - Colored table rendering, progress panels, terminal formatting for output
## Configuration
- Python virtual environment (.venv directory present)
- Configuration constants hardcoded in `src/config.py` (EA_TAX_RATE, TARGET_PLAYER_COUNT)
- No environment file required for operation
- No build configuration (pure Python, no compilation step)
## Platform Requirements
- Python 3.12+
- pip for dependency installation
- No OS-specific dependencies (cross-platform compatible)
- Python 3.12 runtime
- Network access to https://www.fut.gg API endpoints
- ~30-second timeout for API requests
- Async I/O support (requires modern Python asyncio capability)
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Naming Patterns
- Module files use lowercase with underscores: `futgg_client.py`, `test_scorer.py`, `mock_client.py`
- Test files use `test_*.py` or `*_test.py` prefix/suffix pattern
- Package files use lowercase underscores
- Use snake_case for all functions: `score_player()`, `optimize_portfolio()`, `get_batch_market_data()`, `_extract_ea_id()`, `_parse_sales()`
- Private/internal functions prefixed with single underscore: `_get_price_at_time()`, `_build_player()`, `_extract_current_bin()`
- Async functions use same naming as sync, with `async def`: `async def start()`, `async def discover_players()`
- Use snake_case for all variables: `buy_price`, `sell_price`, `current_lowest_bin`, `num_sales`, `op_ratio`, `price_by_hour`, `time_span_hrs`
- Loop variables are short and descriptive: `i`, `h`, `s`, `p`, `ea_id`, `pid`, `entry`, `point`, `auction`
- Dictionary keys are lowercase with underscores: `"buy_price"`, `"expected_profit"`, `"op_sales"`, `"ea_id"`
- Use PascalCase for all classes: `Player`, `PlayerMarketData`, `SaleRecord`, `PricePoint`, `FutGGClient`, `MockClient`
- Use PascalCase for Pydantic models: `BaseModel` subclasses
- Protocol definitions: `MarketDataClient` (PascalCase)
- Constants in UPPER_CASE: `EA_TAX_RATE`, `TARGET_PLAYER_COUNT`, `MIN_OP_SALES`, `MIN_LIVE_LISTINGS`, `POSITION_MAP`
## Code Style
- No explicit formatter (black/yapf) configured
- Lines are reasonably formatted, typically 80-100 characters
- Imports organized by: standard library, third-party, local imports (via `from src...`)
- Blank lines: two between module-level definitions, one between methods
- String formatting: f-strings preferred: `f"{value:,}"`, `f"{ratio:.1%}"`, `f"{value:,.0f}"`
- No explicit linter configuration found (.pylintrc, .flake8, etc.)
- Code follows PEP 8 conventions
- Type hints used but not enforced: `Optional[dict]`, `list[dict]`, `dict | None` (Python 3.10+ union syntax)
## Import Organization
- Imports use absolute paths from repo root: `from src.models import ...` (not relative `from .models import ...`)
- Direct module imports: `import src.config`, `import src.futgg_client`
## Error Handling
- Defensive validation with early returns: Check conditions first, return None/empty if invalid
- Try-except for API calls with logged errors: `HTTPStatusError` caught separately, generic `Exception` as fallback
- Silent exception handling in data parsing: `continue` on exception during iteration (lines 242, 259 in `futgg_client.py`)
- RuntimeError for state violations: `raise RuntimeError("Client not started. Call start() first.")` in `futgg_client.py:69`
## Logging
- Logger created per module: `logger = logging.getLogger(__name__)` (line 24 in futgg_client.py, line 30 in main.py)
- Configured in main entry point: `logging.basicConfig()` call in `main()` function (main.py:164)
- Log levels used: `INFO` for major progress steps, `DEBUG` for verbosity, `ERROR` for exceptions
- Log messages are descriptive:
- No logging in tests or utility modules (test files use `assert` only)
## Comments
- Docstrings on all public functions and classes (module, class, and function level)
- Inline comments for non-obvious logic or design decisions
- Section separators using comment blocks: `# ── API endpoints ──────────────────────────────────────────────` (futgg_client.py line 82)
- Explanatory comments before complex calculations: `# Build price-at-time lookup from hourly history` (scorer.py line 50)
- Pydantic models use docstrings: `"""Core player card data."""` (models.py line 12)
- Functions include docstrings explaining purpose, args, and return value:
- Longer docstrings follow Google-style format with Args/Returns sections (futgg_client.py line 22-35)
## Function Design
- Positional parameters for required data
- Optional parameters with defaults: `concurrency: int = 5`, `max_pages: int = 999`
- Use keyword arguments in calls for clarity: `await client.discover_players(budget, min_price=min_price, max_price=max_price)`
- Type hints on all parameters
- Explicit return types: `-> dict | None`, `-> list[PricePoint]`, `-> Optional[PlayerMarketData]`
- None returned for validation failures (early exit pattern)
- Dicts returned for scoring results with multiple fields: `{"player": ..., "buy_price": ..., "expected_profit": ...}`
- Lists returned from batch operations and discovery
## Module Design
- Modules export classes and public functions naturally
- No explicit `__all__` declarations found
- Private functions prefixed with underscore to signal internal use
- Protocols defined separately in `src/protocols.py` for dependency injection
- No barrel files used (no `__init__.py` re-exports)
- Direct imports: `from src.scorer import score_player` (not `from src import score_player`)
- `src/__init__.py` and `tests/__init__.py` are empty (v2.6+)
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## Pattern Overview
- **Protocol-based abstraction** - `MarketDataClient` protocol allows swapping data sources without changing business logic
- **Stateless scoring** - Pure functions in scorer and optimizer, no side effects
- **Async/concurrent HTTP** - Efficient batch data fetching with configurable concurrency
- **Separation of concerns** - CLI, display, business logic, and data access are isolated
## Layers
- Purpose: Display results and export data
- Location: `src/main.py` (lines 83-156)
- Contains: `display_results()` function with Rich tables, `export_csv()` for output
- Depends on: Scored player dicts from scorer
- Used by: CLI entry point
- Purpose: Implement domain algorithms (OP detection, portfolio optimization)
- Location: `src/scorer.py`, `src/optimizer.py`
- Contains: Pure scoring functions and optimization logic
- Depends on: `PlayerMarketData` models and config constants
- Used by: Main pipeline
- Purpose: Fetch and assemble data from external APIs
- Location: `src/futgg_client.py` (implements `MarketDataClient` protocol)
- Contains: HTTP client, API endpoint methods, data parsing
- Depends on: httpx, Pydantic models
- Used by: Main pipeline (via protocol)
- Purpose: Define contracts and data structures
- Location: `src/models.py`, `src/protocols.py`, `src/config.py`
- Contains: Pydantic models, Protocol interfaces, configuration constants
- Depends on: Standard library, Pydantic
- Used by: All other layers
- Purpose: Accept user input and orchestrate the pipeline
- Location: `src/main.py` (lines 159-175)
- Contains: Click decorators, logging setup, asyncio entrypoint
- Depends on: Business logic and data access layers
- Used by: Users via `python -m src.main`
## Data Flow
- **No persistent state** - All computation is stateless
- **Temporary state in functions** - Local variables for price lookups, efficiency tracking
- **Client state** - `FutGGClient` holds HTTP session (created at start, closed at stop)
- **User input** - Only `--budget` flag required; discovery auto-ranges it
## Key Abstractions
- Purpose: Define contract for any data source (fut.gg, FUTBIN, mock, etc.)
- Examples: `FutGGClient` implements full protocol, tests use `MockMarketDataClient`
- Pattern: Structural subtyping - any class with required async methods matches
- Purpose: Aggregate object containing all data needed to score a player
- Instances: One per player fetched from API
- Pattern: Pydantic model for validation + serialization
- Purpose: Carry player metadata + scoring results through pipeline
- Keys: `player`, `buy_price`, `sell_price`, `net_profit`, `margin_pct`, `op_sales`, `total_sales`, `op_ratio`, `expected_profit`, `efficiency`
- Used by: Display and export functions
## Entry Points
- Location: `src/main.py:159-175`
- Triggers: `python -m src.main --budget 1000000`
- Responsibilities:
- Location: `src/main.py:33-80`
- Triggers: Called by `main()` via `asyncio.run()`
- Responsibilities:
## Error Handling
- **HTTP Errors** - `FutGGClient._get()` catches `HTTPStatusError` and `Exception`, logs at ERROR level, returns None
- **Missing Data** - Scorer and optimizer check for None/empty collections before processing
- **API Pagination** - `discover_players()` breaks pagination loop if response is empty or has no "next" field
- **Parsing Failures** - `_parse_sales()` and `_parse_price_history()` catch exceptions per-record and skip malformed entries
- **Budget Exhaustion** - Greedy fill and swap loop naturally handle budget overflow by stopping selection
## Cross-Cutting Concerns
- `futgg_client.py` logs HTTP errors, page discovery progress
- `main.py` configures logging level (DEBUG if --verbose, INFO otherwise)
- Suppresses verbose httpx/httpcore logs unless DEBUG enabled
- `Player`, `SaleRecord`, `PricePoint`, `PlayerMarketData` all use BaseModel
- Invalid data rejected at model instantiation time
- Scorer and optimizer check logical constraints (min sales, margins, budget)
- fut.gg API is public (no API key needed)
- Standard User-Agent header mimics browser traffic
- No cookie/session management
- `FutGGClient._get()` adds 0.15s delay after each request
- Batch endpoints used where available to reduce request count
- `get_batch_market_data()` uses semaphore with `concurrency=10` to limit simultaneous requests
<!-- GSD:architecture-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
