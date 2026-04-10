"""Bracket-optimized volatility trading — target the 20-50K sweet spot.

Data shows 20-50K cards have the best risk/reward: highest daily volatility (10.2%),
20.1% chance of >10% moves, and only moderate negative drift (-0.39%/day).
Meanwhile 500K+ cards have worst drift (-1.39%/day) and lower volatility.
10-20K cards actually have positive drift (+0.86%/day) but lower vol (7.4%).

The edge: restrict trading to the optimal price bracket. Use volatility-gated
mean reversion but tuned for the bracket characteristics. Cards in 20-50K
bracket make larger swings relative to their price, creating more opportunities
that clear the 5% tax threshold.

Tax survival: 10.2% daily vol and 20.1% chance of >10% moves means frequent
swings of 10-20%, well above the 5.26% breakeven after tax.
"""
from datetime import datetime
from collections import defaultdict
import math
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class BracketVolStrategy(Strategy):
    """Vol mean reversion restricted to optimal price brackets."""

    name = "bracket_vol"

    def __init__(self, params: dict):
        self.params = params
        self.min_price: int = params.get("min_price", 20000)
        self.max_price: int = params.get("max_price", 50000)
        self.window: int = params.get("window", 14)
        self.entry_std: float = params.get("entry_std", 1.5)
        self.min_cv: float = params.get("min_cv", 0.08)
        self.profit_target: float = params.get("profit_target", 0.10)
        self.stop_loss: float = params.get("stop_loss", 0.12)
        self.max_hold_days: int = params.get("max_hold_days", 14)
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

            # Sell: mean reversion target, profit target, stop loss, time stop
            if len(history) >= self.window:
                window = history[-self.window:]
                mean = sum(window) / len(window)
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
            # Price bracket filter — only trade cards in target range
            if not (self.min_price <= price <= self.max_price):
                return signals

            if len(history) >= self.window:
                window = history[-self.window:]
                mean = sum(window) / len(window)
                if mean <= 0:
                    return signals

                variance = sum((p - mean) ** 2 for p in window) / len(window)
                std = math.sqrt(variance)
                cv = std / mean

                # Volatility gate + mean reversion entry
                if cv >= self.min_cv:
                    threshold = mean - self.entry_std * std
                    if price <= threshold:
                        buy_budget = int(portfolio.cash * self.position_pct)
                        quantity = buy_budget // price
                        if quantity > 0:
                            signals.append(Signal(
                                action="BUY", ea_id=ea_id, quantity=quantity,
                            ))
                            self._buy_prices[ea_id] = price
                            self._hold_days[ea_id] = 0

        return signals

    def param_grid(self) -> list[dict]:
        combos = []
        # Test different brackets + entry aggressiveness
        brackets = [
            (10000, 25000),   # cheap cards (positive drift)
            (20000, 50000),   # sweet spot (highest vol)
            (15000, 60000),   # wider sweet spot
            (10000, 50000),   # broad low-mid
        ]
        for min_price, max_price in brackets:
            for entry_std in [1.0, 1.5, 2.0]:
                for profit_target in [0.08, 0.12, 0.18]:
                    combos.append({
                        "min_price": min_price,
                        "max_price": max_price,
                        "window": 14,
                        "entry_std": entry_std,
                        "min_cv": 0.08,
                        "profit_target": profit_target,
                        "stop_loss": 0.12,
                        "max_hold_days": 14,
                        "position_pct": 0.02,
                    })
        return combos
