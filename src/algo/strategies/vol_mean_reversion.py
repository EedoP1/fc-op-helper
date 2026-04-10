"""Volatility-filtered mean reversion — only trade cards volatile enough to beat the tax.

Most mean reversion fails because the 5% EA tax eats the small swings.
This strategy pre-filters: only trade cards whose historical standard
deviation is large enough that mean-reverting swings regularly exceed
the tax. Buy when price is >K standard deviations below the rolling
mean, sell when it returns to the mean (or hits targets).
"""
from datetime import datetime
from collections import defaultdict
import math
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class VolMeanReversionStrategy(Strategy):
    """Mean reversion gated on minimum volatility to overcome EA tax."""

    name = "vol_mean_reversion"

    def __init__(self, params: dict):
        self.params = params
        self.window: int = params.get("window", 20)  # rolling window in days
        self.entry_std: float = params.get("entry_std", 1.5)  # buy at mean - K*std
        self.min_cv: float = params.get("min_cv", 0.08)  # min coefficient of variation to trade
        self.profit_target: float = params.get("profit_target", 0.10)
        self.stop_loss: float = params.get("stop_loss", 0.12)
        self.max_hold_days: int = params.get("max_hold_days", 30)
        self.position_pct: float = params.get("position_pct", 0.02)
        self._history: dict[int, list[int]] = defaultdict(list)
        self._buy_prices: dict[int, int] = {}
        self._hold_days: dict[int, int] = defaultdict(int)

    def on_tick(
        self, ea_id: int, price: int, timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        history = self._history[ea_id]
        history.append(price)

        holding = portfolio.holdings(ea_id)
        signals = []

        if holding > 0:
            self._hold_days[ea_id] += 1
            buy_price = self._buy_prices.get(ea_id, price)
            pct_change = (price - buy_price) / buy_price if buy_price > 0 else 0

            # Sell at profit target, stop loss, time stop, or reversion to mean
            if len(history) >= self.window:
                window = history[-self.window:]
                mean = sum(window) / len(window)
                # Sell when price reaches the mean (the reversion)
                if price >= mean and pct_change > 0:
                    signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                    self._buy_prices.pop(ea_id, None)
                    self._hold_days.pop(ea_id, None)
                    return signals

            if (pct_change >= self.profit_target
                    or pct_change <= -self.stop_loss
                    or self._hold_days[ea_id] >= self.max_hold_days):
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._hold_days.pop(ea_id, None)
        else:
            if len(history) >= self.window:
                window = history[-self.window:]
                mean = sum(window) / len(window)
                if mean <= 0:
                    return signals

                variance = sum((p - mean) ** 2 for p in window) / len(window)
                std = math.sqrt(variance)
                cv = std / mean  # coefficient of variation

                # Only trade cards with enough volatility
                if cv >= self.min_cv:
                    threshold = mean - self.entry_std * std
                    if price <= threshold and price > 0:
                        buy_budget = int(portfolio.cash * self.position_pct)
                        quantity = buy_budget // price if price > 0 else 0
                        if quantity > 0:
                            signals.append(Signal(action="BUY", ea_id=ea_id, quantity=quantity))
                            self._buy_prices[ea_id] = price
                            self._hold_days[ea_id] = 0

        return signals

    def param_grid(self) -> list[dict]:
        combos = []
        for window in [14, 20, 30]:
            for entry_std in [1.0, 1.5, 2.0]:
                for min_cv in [0.06, 0.08, 0.12]:
                    for profit_target in [0.08, 0.12, 0.18]:
                        combos.append({
                            "window": window,
                            "entry_std": entry_std,
                            "min_cv": min_cv,
                            "profit_target": profit_target,
                            "stop_loss": 0.12,
                            "max_hold_days": 30,
                            "position_pct": 0.02,
                        })
        return combos
