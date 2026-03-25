# Testing Patterns

**Analysis Date:** 2026-03-25

## Test Framework

**Runner:**
- pytest 9.0.2
- Config: Auto-discovered (no explicit pytest.ini or pyproject.toml; uses defaults)

**Assertion Library:**
- Python's built-in `assert` statements (no explicit assertion library)

**Run Commands:**
```bash
python -m pytest                    # Run all tests
python -m pytest -v                 # Verbose output
python -m pytest tests/test_scorer.py    # Run specific test file
python -m pytest tests/test_scorer.py::test_player_with_strong_op_sales_scores  # Run specific test
python -m pytest --asyncio-mode=strict   # Run async tests (configured in pytest)
```

**Additional Dependencies:**
- pytest-asyncio (installed, version 1.3.0) for async test support
- Configured with `asyncio_mode = Mode.STRICT`

## Test File Organization

**Location:**
- Co-located in separate `tests/` directory (not alongside source in `src/`)
- Structure mirrors source organization: `tests/test_scorer.py` for `src/scorer.py`

**Naming:**
- Test files: `test_*.py` prefix pattern
- Test functions: `test_*` lowercase with underscores
- Semantic naming that describes what is being tested:
  - `test_player_with_strong_op_sales_scores()`
  - `test_fills_budget()`
  - `test_swap_replaces_expensive_with_cheaper()`

**Structure:**
```
tests/
├── __init__.py                 # Empty
├── mock_client.py              # Test fixtures and mocks
├── test_integration.py         # Full pipeline tests
├── test_optimizer.py           # Portfolio optimizer tests
└── test_scorer.py              # OP sell scoring tests
```

## Test Structure

**Suite Organization:**
Tests use pytest's default collection without explicit test classes. Each test file contains multiple test functions at module level.

```python
def test_player_with_strong_op_sales_scores():
    """A player with 15% OP sales at 40% margin should score."""
    md = make_player(
        price=20000, num_sales=100, op_sales_pct=0.15, op_margin=0.40,
    )
    result = score_player(md)
    assert result is not None
    assert result["margin_pct"] == 40
    assert result["op_sales"] >= 3
```

**Patterns:**

- **Setup pattern:** Inline setup within test functions or via helper functions (`make_player()`, `_make_scored()`)
- **Teardown pattern:** Uses pytest's `capsys` fixture for output capture; `MockClient` handles resource cleanup via `async def start()` and `async def stop()`
- **Assertion pattern:** Multiple assertions per test validating specific expectations; f-strings in assertion messages
  ```python
  assert result["expected_profit"] > 0
  assert len(result) <= 100
  assert total_cost <= 300000
  ```

## Mocking

**Framework:**
- No external mocking library (unittest.mock not imported)
- Manual mock implementations: `MockClient` class in `tests/mock_client.py`
- Test fixtures: `make_player()` factory function

**Patterns:**
```python
# Factory function for creating test data
def make_player(
    ea_id: int = 1,
    name: str = "Test Player",
    rating: int = 88,
    price: int = 20000,
    num_sales: int = 100,
    op_sales_pct: float = 0.10,
    op_margin: float = 0.40,
    num_listings: int = 30,
    hours_of_data: float = 10.0,
) -> PlayerMarketData:
    """Create a PlayerMarketData with controllable parameters."""
    # ... detailed setup code
```

```python
# Protocol-based mock implementation
class MockClient:
    """Mock market data client that returns predefined players."""

    def __init__(self, players: list[PlayerMarketData]):
        self._players = {p.player.resource_id: p for p in players}

    async def start(self) -> None:
        pass

    async def get_batch_market_data(
        self, ea_ids: list[int], concurrency: int = 5,
    ) -> list[Optional[PlayerMarketData]]:
        return [self._players.get(eid) for eid in ea_ids]
```

**What to Mock:**
- Market data client: `MockClient` implements `MarketDataClient` protocol
- Player data: Factory function `make_player()` creates synthetic `PlayerMarketData`
- External HTTP calls: Entirely avoided via mock client (no httpx mocking needed)

**What NOT to Mock:**
- Pure business logic functions (`score_player()`, `optimize_portfolio()`)
- Pydantic models (use actual instances)
- Standard library functions (datetime, etc.)
- Test assertions against returned data structures

## Fixtures and Factories

**Test Data:**
```python
def make_player(
    ea_id: int = 1,
    name: str = "Test Player",
    price: int = 20000,
    num_sales: int = 100,
    op_sales_pct: float = 0.10,
    op_margin: float = 0.40,
    hours_of_data: float = 10.0,
) -> PlayerMarketData:
    """Create a PlayerMarketData with controllable parameters."""
    now = datetime.now(timezone.utc)
    op_count = int(num_sales * op_sales_pct)

    # Build sales spread over hours_of_data
    sales = []
    for i in range(num_sales):
        t = now - timedelta(hours=hours_of_data * (i / max(num_sales, 1)))
        if i < normal_count:
            sales.append(SaleRecord(resource_id=ea_id, sold_at=t, sold_price=price - 100))
        else:
            op_price = int(price * (1 + op_margin))
            sales.append(SaleRecord(resource_id=ea_id, sold_at=t, sold_price=op_price))
```

Helper factory for optimizer tests:
```python
def _make_scored(ea_id, buy_price, net_profit, op_ratio):
    """Helper to create a scored player dict."""
    return {
        "player": Player(resource_id=ea_id, ...),
        "buy_price": buy_price,
        "expected_profit": net_profit * op_ratio,
        # ... other fields
    }
```

**Location:**
- `tests/mock_client.py` contains `make_player()` factory and `MockClient` class
- Imported in test files: `from tests.mock_client import make_player, MockClient`
- Imported in integration test: `from tests.mock_client import MockClient, make_player`

## Coverage

**Requirements:** No explicit coverage target enforced (no pytest-cov plugin visible)

**View Coverage:**
```bash
python -m pytest --cov=src --cov-report=html    # If pytest-cov installed
```

**Current Status:** 21 tests across 3 modules (test_scorer.py: 10, test_optimizer.py: 8, test_integration.py: 3)

## Test Types

**Unit Tests:** (17 tests)
- **Scope:** Individual functions in isolation
- **Approach:** Direct function calls with synthetic data, no I/O
- Examples: `test_scorer.py` tests `score_player()` with various `PlayerMarketData` configurations
- Examples: `test_optimizer.py` tests `optimize_portfolio()` with synthetic scored player lists
- Fast, deterministic, no external dependencies

**Integration Tests:** (3 async tests in test_integration.py)
- **Scope:** Full pipeline end-to-end
- **Approach:** `run()` function from `main.py` with mock data client
- Examples:
  - `test_full_pipeline_with_mock_client()` - validates complete flow produces output
  - `test_pipeline_with_no_viable_players()` - error handling when no players pass scoring
  - `test_pipeline_respects_budget()` - validates budget constraints and CSV export
- Uses `capsys` fixture to capture console output
- Validates output files and CSV contents

**E2E Tests:** Not present. Manual testing against live fut.gg API would be required (see `main()` CLI entry point)

## Common Patterns

**Async Testing:**
```python
@pytest.mark.asyncio
async def test_full_pipeline_with_mock_client(capsys):
    """Run the full pipeline with mock data and verify it produces output."""
    client = MockClient(players)
    await run(budget=500000, verbose=False, client=client)
    captured = capsys.readouterr()
    assert "OP Sell Portfolio" in captured.out
```
- Decorated with `@pytest.mark.asyncio`
- Async functions can `await` async calls
- Use `capsys` fixture for output capture (rich console output)

**Error Testing:**
```python
def test_player_with_no_op_sales_rejected():
    """A player with 0 OP sales should be rejected."""
    md = make_player(price=20000, num_sales=100, op_sales_pct=0.0, op_margin=0.40)
    result = score_player(md)
    assert result is None
```
- Validation functions tested to return None/empty on invalid input
- Test name clearly states expected behavior

**Data Validation:**
```python
def test_net_profit_accounts_for_ea_tax():
    """net_profit should be sell_price - 5% tax - buy_price."""
    md = make_player(price=10000, num_sales=100, op_sales_pct=0.20, op_margin=0.40)
    result = score_player(md)
    sell = int(10000 * 1.40)
    tax = int(sell * 0.05)
    assert result["net_profit"] == sell - tax - 10000
```
- Calculation-heavy logic tested with explicit expected values
- Docstring explains the formula being validated

**Fixture Override (Dynamic Test Data):**
```python
def test_op_detection_uses_price_at_time():
    """OP sales checked against price at time of sale, not current."""
    md = make_player(price=20000, num_sales=100, op_sales_pct=0.0, op_margin=0.40)
    # Override price history to show higher historical price
    from datetime import datetime, timezone, timedelta
    from src.models import PricePoint
    now = datetime.now(timezone.utc)
    md.price_history = [
        PricePoint(resource_id=1, recorded_at=now - timedelta(hours=h), lowest_bin=30000)
        for h in range(11)
    ]
    result = score_player(md)
    assert result is None
```
- Start with factory-generated data, then override specific fields
- Tests price-at-time logic edge cases

---

*Testing analysis: 2026-03-25*
