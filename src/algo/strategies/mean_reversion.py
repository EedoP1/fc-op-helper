"""Mean reversion strategy — buy when price drops below rolling average."""
from datetime import datetime
from collections import defaultdict
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class MeanReversionStrategy(Strategy):
    """Buy when price drops X% below N-hour rolling average. Sell when it recovers."""

    name = "mean_reversion"

    def __init__(self, params: dict):
        self.params = params
        self.window: int = params.get("window", 24)
        self.threshold: float = params.get("threshold", 0.10)
        self.position_pct: float = params.get("position_pct", 0.02)
        self._history: dict[int, list[int]] = defaultdict(list)

    def on_tick(
        self, ea_id: int, price: int, timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        history = self._history[ea_id]
        history.append(price)

        # Not enough data to compute average yet
        if len(history) < self.window:
            return []

        # Rolling average over the last N prices
        window_prices = history[-self.window:]
        avg = sum(window_prices) / len(window_prices)

        signals = []

        if portfolio.holdings(ea_id) > 0:
            # Sell when price recovers to the average
            if price >= avg:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=portfolio.holdings(ea_id)))
        else:
            # Buy when price drops below threshold
            drop_pct = (avg - price) / avg if avg > 0 else 0
            if drop_pct >= self.threshold:
                buy_budget = int(portfolio.cash * self.position_pct)
                quantity = max(1, buy_budget // price) if price > 0 else 0
                if quantity > 0:
                    signals.append(Signal(action="BUY", ea_id=ea_id, quantity=quantity))

        return signals

    def param_grid(self) -> list[dict]:
        combos = []
        for window in [12, 24, 48, 72]:
            for threshold in [0.05, 0.10, 0.15, 0.20]:
                for position_pct in [0.01, 0.02, 0.05]:
                    combos.append({
                        "window": window,
                        "threshold": threshold,
                        "position_pct": position_pct,
                    })
        return combos
