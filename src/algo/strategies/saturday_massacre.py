"""Saturday massacre recovery — buy cards that crash hard on Saturday, sell mid-week.

Data shows Saturday averages -3.14% (by far worst day), Wednesday averages +1.83%
(best day). Every major market crash is a Saturday. But Fri→Wed net is -0.67% on
average, so blindly buying Sat and selling Wed loses money after 5% tax.

The edge: only buy cards that dropped MUCH harder than average on Saturday (>X%
single-day drop). These outliers have stronger mean-reversion. Combined with a
minimum volatility filter to ensure the card's normal swings can beat the tax.

Tax survival: filtering for >7% Saturday drops means we're targeting 10%+ recoveries.
At 62% recovery rate for big drops, expected value is positive even after 5% tax.
"""
from datetime import datetime
from collections import defaultdict
import math
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class SaturdayMassacreStrategy(Strategy):
    """Buy Saturday crash outliers, sell on mid-week recovery."""

    name = "saturday_massacre"

    def __init__(self, params: dict):
        self.params = params
        self.min_sat_drop: float = params.get("min_sat_drop", 0.05)
        self.sell_day: int = params.get("sell_day", 2)  # 0=Mon..6=Sun, 2=Tue, 3=Wed
        self.profit_target: float = params.get("profit_target", 0.10)
        self.stop_loss: float = params.get("stop_loss", 0.12)
        self.max_hold_days: int = params.get("max_hold_days", 7)
        self.min_cv: float = params.get("min_cv", 0.06)
        self.vol_window: int = params.get("vol_window", 14)
        self.position_pct: float = params.get("position_pct", 0.02)
        self._history: dict[int, list[int]] = defaultdict(list)
        self._buy_prices: dict[int, int] = {}
        self._buy_days: dict[int, int] = defaultdict(int)

    def on_tick(
        self, ea_id: int, price: int, timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        history = self._history[ea_id]
        history.append(price)

        holding = portfolio.holdings(ea_id)
        signals = []
        weekday = timestamp.weekday()  # 0=Mon, 5=Sat, 6=Sun

        if holding > 0:
            self._buy_days[ea_id] += 1
            buy_price = self._buy_prices.get(ea_id, price)
            pct_change = (price - buy_price) / buy_price if buy_price > 0 else 0

            # Sell on target day, or at profit target, stop loss, time stop
            sell_now = False
            if pct_change >= self.profit_target:
                sell_now = True
            elif pct_change <= -self.stop_loss:
                sell_now = True
            elif self._buy_days[ea_id] >= self.max_hold_days:
                sell_now = True
            elif weekday == self.sell_day and pct_change > 0:
                # Sell on target weekday if in profit (even small)
                sell_now = True

            if sell_now:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._buy_days.pop(ea_id, None)
        else:
            # Buy on Saturday or Sunday if card crashed hard on Saturday
            if weekday in (5, 6) and len(history) >= 2:
                prev_price = history[-2]
                if prev_price > 0:
                    day_drop = (prev_price - price) / prev_price

                    # Only if this card dropped more than threshold
                    if day_drop >= self.min_sat_drop:
                        # Volatility gate: check CV over recent window
                        if len(history) >= self.vol_window:
                            window = history[-self.vol_window:]
                            mean = sum(window) / len(window)
                            if mean > 0:
                                variance = sum((p - mean) ** 2 for p in window) / len(window)
                                cv = math.sqrt(variance) / mean
                                if cv < self.min_cv:
                                    return signals  # not volatile enough
                        elif len(history) < 7:
                            return signals  # not enough data

                        buy_budget = int(portfolio.cash * self.position_pct)
                        quantity = buy_budget // price if price > 0 else 0
                        if quantity > 0:
                            signals.append(Signal(
                                action="BUY", ea_id=ea_id, quantity=quantity,
                            ))
                            self._buy_prices[ea_id] = price
                            self._buy_days[ea_id] = 0

        return signals

    def param_grid(self) -> list[dict]:
        combos = []
        for min_sat_drop in [0.03, 0.05, 0.07, 0.10]:
            for sell_day in [1, 2, 3]:  # Mon, Tue, Wed
                for profit_target in [0.06, 0.08, 0.12]:
                    combos.append({
                        "min_sat_drop": min_sat_drop,
                        "sell_day": sell_day,
                        "profit_target": profit_target,
                        "stop_loss": 0.12,
                        "max_hold_days": 7,
                        "min_cv": 0.06,
                        "vol_window": 14,
                        "position_pct": 0.02,
                    })
        return combos
