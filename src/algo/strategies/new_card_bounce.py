"""New card bounce — buy new cards on first strong bounce, sell on trailing stop.

EA releases new cards on Friday. They crash ~25% over days 1-7, then rally
days 8-14 as supply dries up.

Strategy: wait for the first day with a 5-18% price jump (the bounce signal),
buy immediately, sell when price drops 5% from its peak since buying.
Uses on_tick_batch to see all cards at once and size positions as cash / num_buys.
"""
from datetime import datetime
from collections import defaultdict
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class NewCardBounceStrategy(Strategy):
    """Buy new cards on first strong bounce, sell on trailing stop."""

    name = "new_card_bounce"

    def __init__(self, params: dict):
        self.params = params
        self.min_bounce: float = params.get("min_bounce", 0.05)
        self.max_bounce: float = params.get("max_bounce", 0.18)
        self.min_day: int = params.get("min_day", 3)
        self.max_day: int = params.get("max_day", 10)
        self.min_price: int = params.get("min_price", 13000)
        self.max_price: int = params.get("max_price", 100000)
        self.max_hold_days: int = params.get("max_hold_days", 14)
        self.trailing_stop: float = params.get("trailing_stop", 0.05)
        self.friday_only: bool = params.get("friday_only", False)
        self._old_cards: set[int] = set()
        self._history: dict[int, list[int]] = defaultdict(list)
        self._first_seen_day: dict[int, int] = {}
        self._bought: set[int] = set()
        self._peak_prices: dict[int, int] = {}
        self._hold_ticks: dict[int, int] = defaultdict(int)

    def set_existing_ids(self, existing_ids: set[int]):
        self._old_cards = existing_ids

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals = []
        potential_buys = []

        for ea_id, price in ticks:
            if ea_id in self._old_cards:
                continue

            if ea_id not in self._first_seen_day:
                self._first_seen_day[ea_id] = timestamp.weekday()

            if self.friday_only and self._first_seen_day[ea_id] != 4:
                continue

            history = self._history[ea_id]
            history.append(price)
            card_age = len(history)

            holding = portfolio.holdings(ea_id)

            if holding > 0:
                self._hold_ticks[ea_id] += 1

                if price > self._peak_prices.get(ea_id, 0):
                    self._peak_prices[ea_id] = price

                peak = self._peak_prices[ea_id]
                drop_from_peak = (peak - price) / peak if peak > 0 else 0

                sell = False
                if drop_from_peak >= self.trailing_stop:
                    sell = True
                if self._hold_ticks[ea_id] >= self.max_hold_days:
                    sell = True

                if sell:
                    signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                    self._peak_prices.pop(ea_id, None)
                    self._hold_ticks.pop(ea_id, None)
            else:
                if self.min_day <= card_age <= self.max_day and ea_id not in self._bought:
                    if self.min_price <= price <= self.max_price:
                        if len(history) >= 2:
                            prev = history[-2]
                            if prev > 0:
                                daily_ret = (price - prev) / prev
                                if self.min_bounce <= daily_ret <= self.max_bounce:
                                    potential_buys.append((ea_id, price))

        # Size buys: split available cash equally
        if potential_buys:
            sell_revenue = 0
            for sig in signals:
                if sig.action == "SELL":
                    for eid, p in ticks:
                        if eid == sig.ea_id:
                            sell_revenue += (p * sig.quantity * 95) // 100
                            break

            available_cash = portfolio.cash + sell_revenue
            per_card = available_cash // len(potential_buys)

            for ea_id, price in potential_buys:
                quantity = per_card // price if price > 0 else 0
                if quantity > 0:
                    signals.append(Signal(action="BUY", ea_id=ea_id, quantity=quantity))
                    self._peak_prices[ea_id] = price
                    self._hold_ticks[ea_id] = 0
                    self._bought.add(ea_id)

        return signals

    def param_grid(self) -> list[dict]:
        combos = []
        for min_bounce in [0.03, 0.05, 0.07]:
            for max_bounce in [0.15, 0.18, 0.25]:
                for trailing_stop in [0.05, 0.10]:
                    for max_price in [50000, 100000]:
                        combos.append({
                            "min_bounce": min_bounce,
                            "max_bounce": max_bounce,
                            "min_day": 3,
                            "max_day": 10,
                            "min_price": 13000,
                            "max_price": max_price,
                            "trailing_stop": trailing_stop,
                            "max_hold_days": 14,
                            "friday_only": True,
                        })
        return combos
