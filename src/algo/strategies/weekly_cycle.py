# src/algo/strategies/weekly_cycle.py
"""Weekly cycle strategy — exploit predictable day-of-week price patterns."""
from datetime import datetime
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class WeeklyCycleStrategy(Strategy):
    """Buy on a specific day/hour, sell on another. Exploits weekly patterns."""

    name = "weekly_cycle"

    def __init__(self, params: dict):
        self.params = params
        self.buy_day: int = params.get("buy_day", 3)    # 0=Mon, 3=Thu
        self.buy_hour: int = params.get("buy_hour", 18)
        self.sell_day: int = params.get("sell_day", 5)   # 5=Sat
        self.sell_hour: int = params.get("sell_hour", 12)
        self.position_pct: float = params.get("position_pct", 0.02)

    def on_tick(
        self, ea_id: int, price: int, timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        weekday = timestamp.weekday()
        hour = timestamp.hour

        signals = []

        if portfolio.holdings(ea_id) > 0:
            # Sell window
            if weekday == self.sell_day and hour == self.sell_hour:
                signals.append(Signal(
                    action="SELL", ea_id=ea_id,
                    quantity=portfolio.holdings(ea_id),
                ))
        else:
            # Buy window
            if weekday == self.buy_day and hour == self.buy_hour:
                buy_budget = portfolio.cash * int(self.position_pct * 1000) // 1000
                quantity = max(1, buy_budget // price) if price > 0 else 0
                if quantity > 0:
                    signals.append(Signal(action="BUY", ea_id=ea_id, quantity=quantity))

        return signals

    def param_grid(self) -> list[dict]:
        combos = []
        buy_slots = [(3, 18), (3, 21), (4, 0)]   # Thu 18h, Thu 21h, Fri 0h
        sell_slots = [(5, 12), (5, 18), (6, 12)]  # Sat 12h, Sat 18h, Sun 12h
        for buy_day, buy_hour in buy_slots:
            for sell_day, sell_hour in sell_slots:
                for position_pct in [0.01, 0.02, 0.05]:
                    combos.append({
                        "buy_day": buy_day,
                        "buy_hour": buy_hour,
                        "sell_day": sell_day,
                        "sell_hour": sell_hour,
                        "position_pct": position_pct,
                    })
        return combos
