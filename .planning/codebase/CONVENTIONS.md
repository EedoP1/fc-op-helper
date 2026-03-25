# Coding Conventions

**Analysis Date:** 2026-03-25

## Naming Patterns

**Files:**
- Module files use lowercase with underscores: `futgg_client.py`, `test_scorer.py`, `mock_client.py`
- Test files use `test_*.py` or `*_test.py` prefix/suffix pattern
- Package files use lowercase underscores

**Functions:**
- Use snake_case for all functions: `score_player()`, `optimize_portfolio()`, `get_batch_market_data()`, `_extract_ea_id()`, `_parse_sales()`
- Private/internal functions prefixed with single underscore: `_get_price_at_time()`, `_build_player()`, `_extract_current_bin()`
- Async functions use same naming as sync, with `async def`: `async def start()`, `async def discover_players()`

**Variables:**
- Use snake_case for all variables: `buy_price`, `sell_price`, `current_lowest_bin`, `num_sales`, `op_ratio`, `price_by_hour`, `time_span_hrs`
- Loop variables are short and descriptive: `i`, `h`, `s`, `p`, `ea_id`, `pid`, `entry`, `point`, `auction`
- Dictionary keys are lowercase with underscores: `"buy_price"`, `"expected_profit"`, `"op_sales"`, `"ea_id"`

**Types/Classes:**
- Use PascalCase for all classes: `Player`, `PlayerMarketData`, `SaleRecord`, `PricePoint`, `FutGGClient`, `MockClient`
- Use PascalCase for Pydantic models: `BaseModel` subclasses
- Protocol definitions: `MarketDataClient` (PascalCase)
- Constants in UPPER_CASE: `EA_TAX_RATE`, `TARGET_PLAYER_COUNT`, `MIN_OP_SALES`, `MIN_LIVE_LISTINGS`, `POSITION_MAP`

## Code Style

**Formatting:**
- No explicit formatter (black/yapf) configured
- Lines are reasonably formatted, typically 80-100 characters
- Imports organized by: standard library, third-party, local imports (via `from src...`)
- Blank lines: two between module-level definitions, one between methods
- String formatting: f-strings preferred: `f"{value:,}"`, `f"{ratio:.1%}"`, `f"{value:,.0f}"`

**Linting:**
- No explicit linter configuration found (.pylintrc, .flake8, etc.)
- Code follows PEP 8 conventions
- Type hints used but not enforced: `Optional[dict]`, `list[dict]`, `dict | None` (Python 3.10+ union syntax)

## Import Organization

**Order:**
1. Future imports: `from __future__ import annotations` (present in all modules)
2. Standard library: `import asyncio`, `import csv`, `import logging`, `from datetime import datetime, timezone, timedelta`
3. Third-party: `import httpx`, `import click`, `from pydantic import BaseModel`, `from rich.console import Console`
4. Local imports: `from src.models import Player`, `from src.config import EA_TAX_RATE`, `from tests.mock_client import make_player`

**Path Aliases:**
- Imports use absolute paths from repo root: `from src.models import ...` (not relative `from .models import ...`)
- Direct module imports: `import src.config`, `import src.futgg_client`

## Error Handling

**Patterns:**
- Defensive validation with early returns: Check conditions first, return None/empty if invalid
  ```python
  if buy_price <= 0:
      return None
  if len(md.sales) < MIN_TOTAL_SALES:
      return None
  ```
- Try-except for API calls with logged errors: `HTTPStatusError` caught separately, generic `Exception` as fallback
  ```python
  try:
      resp = await self.client.get(path)
      resp.raise_for_status()
  except httpx.HTTPStatusError as e:
      logger.error(f"HTTP {e.response.status_code} for {path}")
      return None
  except Exception as e:
      logger.error(f"Request failed for {path}: {e}")
      return None
  ```
- Silent exception handling in data parsing: `continue` on exception during iteration (lines 242, 259 in `futgg_client.py`)
  ```python
  try:
      points.append(PricePoint(...))
  except Exception:
      continue
  ```
- RuntimeError for state violations: `raise RuntimeError("Client not started. Call start() first.")` in `futgg_client.py:69`

## Logging

**Framework:** Python's standard `logging` module

**Patterns:**
- Logger created per module: `logger = logging.getLogger(__name__)` (line 24 in futgg_client.py, line 30 in main.py)
- Configured in main entry point: `logging.basicConfig()` call in `main()` function (main.py:164)
- Log levels used: `INFO` for major progress steps, `DEBUG` for verbosity, `ERROR` for exceptions
- Log messages are descriptive:
  ```python
  logger.info("FutGG client started")
  logger.info(f"Fetching player list page {page_num}...")
  logger.error(f"HTTP {e.response.status_code} for {path}")
  ```
- No logging in tests or utility modules (test files use `assert` only)

## Comments

**When to Comment:**
- Docstrings on all public functions and classes (module, class, and function level)
- Inline comments for non-obvious logic or design decisions
- Section separators using comment blocks: `# ── API endpoints ──────────────────────────────────────────────` (futgg_client.py line 82)
- Explanatory comments before complex calculations: `# Build price-at-time lookup from hourly history` (scorer.py line 50)

**JSDoc/TSDoc:**
- Pydantic models use docstrings: `"""Core player card data."""` (models.py line 12)
- Functions include docstrings explaining purpose, args, and return value:
  ```python
  def score_player(md: PlayerMarketData) -> dict | None:
      """
      Score a player for OP selling.

      Returns a dict with scoring data, or None if the player isn't viable.
      """
  ```
- Longer docstrings follow Google-style format with Args/Returns sections (futgg_client.py line 22-35)

## Function Design

**Size:** Functions are typically 20-50 lines; larger functions (100+ lines) are algorithm-heavy but logically cohesive

**Parameters:**
- Positional parameters for required data
- Optional parameters with defaults: `concurrency: int = 5`, `max_pages: int = 999`
- Use keyword arguments in calls for clarity: `await client.discover_players(budget, min_price=min_price, max_price=max_price)`
- Type hints on all parameters

**Return Values:**
- Explicit return types: `-> dict | None`, `-> list[PricePoint]`, `-> Optional[PlayerMarketData]`
- None returned for validation failures (early exit pattern)
- Dicts returned for scoring results with multiple fields: `{"player": ..., "buy_price": ..., "expected_profit": ...}`
- Lists returned from batch operations and discovery

## Module Design

**Exports:**
- Modules export classes and public functions naturally
- No explicit `__all__` declarations found
- Private functions prefixed with underscore to signal internal use
- Protocols defined separately in `src/protocols.py` for dependency injection

**Barrel Files:**
- No barrel files used (no `__init__.py` re-exports)
- Direct imports: `from src.scorer import score_player` (not `from src import score_player`)
- `src/__init__.py` and `tests/__init__.py` are empty (v2.6+)

---

*Convention analysis: 2026-03-25*
