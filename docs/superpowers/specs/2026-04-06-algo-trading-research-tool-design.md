# FC26 Algo Trading Research Tool — Design Spec

## Purpose

A backtesting engine that simulates algorithmic trading strategies against historical FC26 Ultimate Team player price data. The goal is to discover which strategies and parameters are profitable before risking real coins.

The pipeline: **backtest on historical data → paper trade on live data → real coins.** This spec covers the backtesting engine (step 1). Paper trading and live execution are future work.

## Data Layer

### New table: `price_history`

Stores hourly price data per player from game launch to present, populated by a one-time fut.gg scrape.

| Column    | Type     | Description                    |
|-----------|----------|--------------------------------|
| ea_id     | int      | FK to players                  |
| timestamp | datetime | Hour of the price point        |
| price     | int      | Market price at that hour      |

- Composite index on `(ea_id, timestamp)`
- Source: fut.gg `/api/fut/player-prices/26/{eaId}/` endpoint (hourly history field)

### New table: `backtest_results`

Stores the output of every backtest run for comparison and analysis.

| Column         | Type     | Description                          |
|----------------|----------|--------------------------------------|
| id             | int      | PK, auto-increment                   |
| strategy_name  | str      | e.g. "mean_reversion"                |
| params         | json     | Full parameter set used              |
| started_budget | int      | Starting coins                       |
| final_budget   | int      | Ending coins                         |
| total_pnl      | int      | Net profit/loss after 5% EA tax      |
| total_trades   | int      | Number of completed round trips      |
| win_rate       | float    | % of trades that were profitable     |
| max_drawdown   | float    | Worst peak-to-trough balance loss    |
| sharpe_ratio   | float    | Risk-adjusted return                 |
| run_at         | datetime | When this backtest was executed       |

## Strategy Interface

Every strategy is a Python class implementing this contract:

```python
class Strategy:
    name: str

    def __init__(self, params: dict):
        """Initialize with a specific parameter combination."""

    def on_tick(self, ea_id: int, price: int, timestamp: datetime, portfolio: Portfolio) -> list[Signal]:
        """Called once per player per hour. Returns BUY/SELL signals or empty list.
        
        The strategy can only see current and past data — never future prices.
        """

    def param_grid(self) -> list[dict]:
        """Returns all parameter combinations to sweep."""
```

### Key types

- **Signal** — `BUY(ea_id, quantity)` or `SELL(ea_id, quantity)`. The engine handles execution.
- **Portfolio** — Read-only view of current state: cash balance, open positions (ea_id, quantity, buy price, buy time), and trade history. Strategies use this for decisions like "don't buy if I already hold 10 of this player."
- **Position** — A held asset: ea_id, quantity, buy price, buy timestamp.

### Strategy location

Strategies live in `src/algo/strategies/`, one file per strategy. The `__init__.py` auto-discovers all strategy classes so adding a new one requires no registration — just create the file.

## Backtesting Engine

### Run loop

1. Load all `price_history` data into memory: `{ea_id: [(timestamp, price), ...]}`
2. Collect all strategy+params combos to run
3. Walk through time hour by hour
4. At each hour, for each player with a price at that hour, call every strategy's `on_tick()`
5. Process returned signals:
   - **BUY**: Deduct `price × quantity` from cash. Create position.
   - **SELL**: Add `price × quantity × 0.95` to cash (5% EA tax). Close position.
   - If insufficient cash for a BUY, skip it.
6. After the last hour, force-sell all open positions at their final known price (so P&L reflects everything)
7. Save results to `backtest_results` table

### Single data pass

The engine iterates through time once. All strategies see each tick in the same pass. Each strategy+params combo has its own isolated Portfolio instance — no shared state between runs.

### Metrics tracked during a run

- Cash balance at every tick (for drawdown and sharpe calculation)
- Every trade: ea_id, buy price, sell price, buy time, sell time, net profit
- Peak balance and worst drop from peak (max drawdown)

### Player filtering

The engine accepts filters to skip irrelevant players:
- Min/max price range
- Min data points (skip players with sparse price history)

### Execution model

Sequential — one strategy+params combo at a time. Parallelization (multiprocessing) can be added later if runs are too slow.

## Starter Strategies

### 1. Mean Reversion

Buy when price drops X% below its rolling N-hour average. Sell when price returns to the average.

Parameter grid:
- Window: 12h, 24h, 48h, 72h
- Drop threshold: 5%, 10%, 15%, 20%
- Position size (% of budget per trade): 1%, 2%, 5%
- 48 combinations

### 2. Momentum

Detect upward trends — price rising for N consecutive hours. Buy and ride the trend. Sell when price drops X% from the peak reached after buying (trailing stop).

Parameter grid:
- Trend length: 6h, 12h, 24h
- Trailing stop: 3%, 5%, 10%
- Position size (% of budget per trade): 1%, 2%, 5%
- 27 combinations

### 3. Weekly Cycle

Exploit predictable weekly price patterns. Buy at a specific day/hour (e.g., Thursday evening when rewards flood supply) and sell at another (e.g., Saturday when weekend demand peaks).

Parameter grid:
- Buy day+hour: Thursday 18:00, Thursday 21:00, Friday 00:00
- Sell day+hour: Saturday 12:00, Saturday 18:00, Sunday 12:00
- Position size (% of budget per trade): 1%, 2%, 5%
- 27 combinations

### 4. Bollinger Bands

Price oscillates between statistical bands — upper and lower bounds calculated from standard deviations of a moving average. Buy when price touches the lower band, sell at the upper band.

Parameter grid:
- Window: 12h, 24h, 48h
- Band width: 1σ, 1.5σ, 2σ
- Position size (% of budget per trade): 1%, 2%, 5%
- 27 combinations

**Total starter combos: 129** — runs in a few minutes sequentially.

## Project Structure

```
src/algo/
├── __init__.py
├── engine.py            — Backtesting engine (main loop, signal execution)
├── models.py            — Signal, Portfolio, Position, TradeLog
├── report.py            — CLI for viewing/comparing backtest results
├── scraper.py           — One-time fut.gg price history scrape
├── strategies/
│   ├── __init__.py      — Auto-discovery of strategy files
│   ├── base.py          — Strategy base class / interface
│   ├── mean_reversion.py
│   ├── momentum.py
│   ├── weekly_cycle.py
│   └── bollinger.py
```

Lives entirely in `src/algo/`, independent from the existing server/scanner/extension code. Reuses `src/server/db.py` for the database connection but does not touch existing tables.

## CLI Commands

| Command | Description |
|---------|-------------|
| `python -m src.algo.scraper` | Fetch full price history from fut.gg, populate `price_history` table |
| `python -m src.algo.engine --strategy mean_reversion` | Run one strategy with all its param combos |
| `python -m src.algo.engine --all` | Run every strategy with all param combos |
| `python -m src.algo.engine --strategy mean_reversion --params '{"window": 24, "threshold": 0.10}'` | Run one specific combo |
| `python -m src.algo.report` | Show ranked results table (sorted by P&L) |
| `python -m src.algo.report --strategy mean_reversion` | Filter results by strategy |

## Constraints

- **5% EA tax** on every simulated sell — no exceptions
- **No future data leakage** — `on_tick()` only receives current timestamp and price; strategies must track their own history from past ticks
- **Position sizing is a parameter** — not hardcoded; strategies include it in their param grid
- **Budget is configurable** — passed as CLI arg, default 1M coins
- **No position limits** — strategies can hold unlimited quantity of any player

## Out of Scope

- Paper trading (live simulation with fake budget) — future phase
- Live execution (real coins via extension automation) — future phase
- Event-driven strategies (require content drop data we don't have) — future phase
- Parallel execution (multiprocessing) — add later if needed
