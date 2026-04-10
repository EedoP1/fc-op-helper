"""Weekday swing strategy — exploit day-of-week price patterns on volatile cards.

FC market has weekly cycles: prices often dip Thu-Fri (reward dumps, promo
anticipation) and rise Tue-Wed (Weekend League prep, demand peaks). But
the 5% tax means we can only trade cards with strong enough weekly swings.

This strategy tracks per-card day-of-week average returns, then only trades
cards where the historical cheap-day-to-expensive-day spread exceeds a
minimum threshold.
"""
from datetime import datetime
from collections import defaultdict
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class WeekdaySwingStrategy(Strategy):
    """Buy on historically cheap day, sell on expensive day, for volatile cards only."""

    name = "weekday_swing"

    def __init__(self, params: dict):
        self.params = params
        self.buy_day: int = params.get("buy_day", 3)  # 0=Mon..6=Sun, default Thu
        self.sell_day: int = params.get("sell_day", 1)  # default Tue
        self.min_history: int = params.get("min_history", 28)  # days before trading
        self.min_swing: float = params.get("min_swing", 0.08)  # min avg spread to qualify
        self.profit_target: float = params.get("profit_target", 0.10)
        self.stop_loss: float = params.get("stop_loss", 0.10)
        self.position_pct: float = params.get("position_pct", 0.02)
        self._history: dict[int, list[tuple[int, int]]] = defaultdict(list)  # (weekday, price)
        self._buy_prices: dict[int, int] = {}

    def _avg_price_by_day(self, ea_id: int) -> dict[int, float]:
        """Compute average price per weekday for a card."""
        by_day: dict[int, list[int]] = defaultdict(list)
        for wd, price in self._history[ea_id]:
            if price > 0:
                by_day[wd].append(price)
        return {wd: sum(prices) / len(prices) for wd, prices in by_day.items() if prices}

    def on_tick(
        self, ea_id: int, price: int, timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        weekday = timestamp.weekday()
        self._history[ea_id].append((weekday, price))

        holding = portfolio.holdings(ea_id)
        signals = []

        if holding > 0:
            buy_price = self._buy_prices.get(ea_id, price)
            pct_change = (price - buy_price) / buy_price if buy_price > 0 else 0

            # Sell on target day, profit target, or stop loss
            if pct_change >= self.profit_target or pct_change <= -self.stop_loss:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
            elif weekday == self.sell_day and pct_change > 0:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
        else:
            if weekday == self.buy_day and len(self._history[ea_id]) >= self.min_history:
                avg_by_day = self._avg_price_by_day(ea_id)
                buy_avg = avg_by_day.get(self.buy_day, 0)
                sell_avg = avg_by_day.get(self.sell_day, 0)

                if buy_avg > 0 and sell_avg > 0:
                    expected_swing = (sell_avg - buy_avg) / buy_avg
                    if expected_swing >= self.min_swing:
                        buy_budget = int(portfolio.cash * self.position_pct)
                        quantity = buy_budget // price if price > 0 else 0
                        if quantity > 0:
                            signals.append(Signal(action="BUY", ea_id=ea_id, quantity=quantity))
                            self._buy_prices[ea_id] = price

        return signals

    def param_grid(self) -> list[dict]:
        combos = []
        # Thu→Tue, Thu→Wed, Fri→Tue, Fri→Wed, Sat→Tue, Sun→Wed
        day_pairs = [
            (3, 1), (3, 2),  # Thu→Tue, Thu→Wed
            (4, 1), (4, 2),  # Fri→Tue, Fri→Wed
            (5, 1), (6, 2),  # Sat→Tue, Sun→Wed
        ]
        for buy_day, sell_day in day_pairs:
            for min_swing in [0.06, 0.08, 0.12]:
                for profit_target in [0.08, 0.12]:
                    combos.append({
                        "buy_day": buy_day,
                        "sell_day": sell_day,
                        "min_history": 28,
                        "min_swing": min_swing,
                        "profit_target": profit_target,
                        "stop_loss": 0.10,
                        "position_pct": 0.02,
                    })
        return combos
