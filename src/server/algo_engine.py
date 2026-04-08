"""Live signal engine — wraps PromoDipBuyStrategy for real-time signal generation.

Produces identical signals to the backtester given the same data.
The engine owns a strategy instance and a Portfolio, processes ticks,
and returns (action, ea_id, quantity, reference_price) tuples.
"""
from datetime import datetime

from src.algo.strategies.promo_dip_buy import PromoDipBuyStrategy
from src.algo.models import Portfolio


class AlgoSignalEngine:
    """Wraps PromoDipBuyStrategy + Portfolio for live signal generation.

    Produces the exact same signals as run_backtest() in engine.py when
    fed the same tick data in the same order.
    """

    def __init__(
        self,
        budget: int,
        created_at_map: dict[int, datetime] | None = None,
        params: dict | None = None,
    ):
        self.budget = budget
        grid = PromoDipBuyStrategy({}).param_grid_hourly()
        self.params = params or grid[0]
        self.strategy = PromoDipBuyStrategy(self.params)
        self.portfolio = Portfolio(cash=budget)
        self._initialized_existing = False

        if created_at_map:
            self.strategy.set_created_at_map(created_at_map)

    def process_tick(
        self,
        ticks: list[tuple[int, int]],
        timestamp: datetime,
    ) -> list[tuple[str, int, int, int]]:
        """Process one timestamp of price data. Returns list of (action, ea_id, quantity, price).

        Mirrors the backtester's engine.py loop exactly:
        1. Call strategy.on_tick_batch() to get signals
        2. Execute each signal on the portfolio (buy/sell)
        3. Return the signals with their prices
        """
        if not self._initialized_existing:
            existing_ids = {ea_id for ea_id, _ in ticks}
            self.strategy.set_existing_ids(existing_ids)
            self._initialized_existing = True

        signals = self.strategy.on_tick_batch(ticks, timestamp, self.portfolio)

        results = []
        for signal in signals:
            sig_price = next((p for eid, p in ticks if eid == signal.ea_id), 0)
            if signal.action == "BUY":
                self.portfolio.buy(signal.ea_id, signal.quantity, sig_price, timestamp)
            elif signal.action == "SELL":
                self.portfolio.sell(signal.ea_id, signal.quantity, sig_price, timestamp)
            results.append((signal.action, signal.ea_id, signal.quantity, sig_price))

        return results

    @property
    def cash(self) -> int:
        return self.portfolio.cash

    @property
    def positions(self):
        return self.portfolio.positions

    @property
    def trades(self):
        return self.portfolio.trades
