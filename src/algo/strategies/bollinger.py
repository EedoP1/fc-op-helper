# src/algo/strategies/bollinger.py
"""Bollinger Bands strategy — buy at lower band, sell at upper band."""
import math
from datetime import datetime
from collections import defaultdict
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class BollingerStrategy(Strategy):
    """Buy when price touches lower Bollinger Band, sell at upper band."""

    name = "bollinger"

    def __init__(self, params: dict):
        self.params = params
        self.window: int = params.get("window", 24)
        self.num_std: float = params.get("num_std", 2.0)
        self.position_pct: float = params.get("position_pct", 0.02)
        self._history: dict[int, list[int]] = defaultdict(list)

    def on_tick(
        self, ea_id: int, price: int, timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        history = self._history[ea_id]
        history.append(price)

        if len(history) < self.window:
            return []

        window_prices = history[-self.window:]
        mean = sum(window_prices) / len(window_prices)
        variance = sum((p - mean) ** 2 for p in window_prices) / len(window_prices)
        std = math.sqrt(variance)

        upper_band = mean + self.num_std * std
        lower_band = mean - self.num_std * std

        signals = []

        if portfolio.holdings(ea_id) > 0:
            if price >= upper_band:
                signals.append(Signal(
                    action="SELL", ea_id=ea_id,
                    quantity=portfolio.holdings(ea_id),
                ))
        else:
            if price <= lower_band:
                buy_budget = portfolio.cash * int(self.position_pct * 1000) // 1000
                quantity = max(1, buy_budget // price) if price > 0 else 0
                if quantity > 0:
                    signals.append(Signal(action="BUY", ea_id=ea_id, quantity=quantity))

        return signals

    def param_grid(self) -> list[dict]:
        combos = []
        for window in [12, 24, 48]:
            for num_std in [1.0, 1.5, 2.0]:
                for position_pct in [0.01, 0.02, 0.05]:
                    combos.append({
                        "window": window,
                        "num_std": num_std,
                        "position_pct": position_pct,
                    })
        return combos
