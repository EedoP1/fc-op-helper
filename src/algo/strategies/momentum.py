# src/algo/strategies/momentum.py
"""Momentum strategy — buy on uptrends, sell on trailing stop."""
from datetime import datetime
from collections import defaultdict
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class MomentumStrategy(Strategy):
    """Detect rising trends and ride them. Exit on trailing stop loss."""

    name = "momentum"

    def __init__(self, params: dict):
        self.params = params
        self.trend_length: int = params.get("trend_length", 12)
        self.trailing_stop: float = params.get("trailing_stop", 0.05)
        self.position_pct: float = params.get("position_pct", 0.02)
        self._history: dict[int, list[int]] = defaultdict(list)
        self._peak_since_buy: dict[int, int] = {}

    def on_tick(
        self, ea_id: int, price: int, timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        history = self._history[ea_id]
        history.append(price)

        signals = []

        if portfolio.holdings(ea_id) > 0:
            # Track peak price since buying
            if ea_id in self._peak_since_buy:
                if price > self._peak_since_buy[ea_id]:
                    self._peak_since_buy[ea_id] = price
                peak = self._peak_since_buy[ea_id]
                drop_from_peak = (peak - price) / peak if peak > 0 else 0
                if drop_from_peak >= self.trailing_stop:
                    signals.append(Signal(
                        action="SELL", ea_id=ea_id,
                        quantity=portfolio.holdings(ea_id),
                    ))
                    del self._peak_since_buy[ea_id]
        else:
            # Check for N consecutive rising prices
            if len(history) >= self.trend_length:
                recent = history[-self.trend_length:]
                is_rising = all(recent[i] > recent[i - 1] for i in range(1, len(recent)))
                if is_rising:
                    buy_budget = portfolio.cash * int(self.position_pct * 1000) // 1000
                    quantity = max(1, buy_budget // price) if price > 0 else 0
                    if quantity > 0:
                        signals.append(Signal(action="BUY", ea_id=ea_id, quantity=quantity))
                        self._peak_since_buy[ea_id] = price

        return signals

    def param_grid(self) -> list[dict]:
        combos = []
        for trend_length in [6, 12, 24]:
            for trailing_stop in [0.03, 0.05, 0.10]:
                for position_pct in [0.01, 0.02, 0.05]:
                    combos.append({
                        "trend_length": trend_length,
                        "trailing_stop": trailing_stop,
                        "position_pct": position_pct,
                    })
        return combos
