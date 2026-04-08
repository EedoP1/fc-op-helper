"""New card bounce — buy new cards on first strong bounce, sell on trailing stop.

EA releases new cards on Friday. They crash ~25% over days 1-7, then rally
days 8-14 as supply dries up.

Strategy: wait for the first day with a 5-18% price jump (the bounce signal),
buy immediately, sell when price drops 5% from its peak since buying.
Uses on_tick_batch to see all cards at once and size positions as cash / num_buys.

Supports both daily (FUTBIN) and hourly (market_snapshots) data via lookback_hours.
When created_at_map is provided, card age is computed in real days instead of tick count.
"""
from datetime import datetime, timezone
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
        self.min_price: int = params.get("min_price", 12000)
        self.max_price: int = params.get("max_price", 61000)
        self.max_hold_days: int = params.get("max_hold_days", 14)
        self.trailing_stop: float = params.get("trailing_stop", 0.05)
        self.friday_only: bool = params.get("friday_only", False)
        # How many ticks back to compare for bounce detection.
        # For daily data: 1 = yesterday. For hourly data: 24 = 24 hours ago.
        self.lookback_hours: int = params.get("lookback_hours", 1)
        # How many ticks = 1 day for max_hold. Daily data: 1. Hourly: 24.
        self.ticks_per_day: int = params.get("ticks_per_day", 1)
        # Max % of portfolio value to allocate to a single card (0 = no limit)
        self.max_position_pct: float = params.get("max_position_pct", 0)
        self._old_cards: set[int] = set()
        self._created_at_map: dict[int, datetime] = {}
        self._history: dict[int, list[tuple[datetime, int]]] = defaultdict(list)
        self._first_seen_day: dict[int, int] = {}
        self._bought: set[int] = set()
        self._peak_prices: dict[int, int] = {}
        self._hold_ticks: dict[int, int] = defaultdict(int)

    def set_existing_ids(self, existing_ids: set[int]):
        self._old_cards = existing_ids

    def set_created_at_map(self, created_at_map: dict):
        self._created_at_map = created_at_map

    def _card_age_days(self, ea_id: int, timestamp: datetime) -> int | None:
        """Card age in days. Uses created_at if available, else tick count."""
        if ea_id in self._created_at_map:
            created = self._created_at_map[ea_id]
            # Make both offset-naive for subtraction
            ts = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp
            cr = created.replace(tzinfo=None) if created.tzinfo else created
            return (ts - cr).days
        # Fallback: count ticks (only meaningful for daily data)
        history = self._history[ea_id]
        if self.ticks_per_day > 1:
            return len(history) // self.ticks_per_day
        return len(history)

    def _is_friday_card(self, ea_id: int) -> bool:
        """Check if card was released on a Friday."""
        if ea_id in self._created_at_map:
            return self._created_at_map[ea_id].weekday() == 4
        # Fallback: first seen day
        return self._first_seen_day.get(ea_id) == 4

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals = []
        potential_buys = []

        for ea_id, price in ticks:
            # When we have real created_at data, use age instead of existing_ids
            if self._created_at_map:
                if ea_id not in self._created_at_map:
                    continue
            elif ea_id in self._old_cards:
                continue

            if ea_id not in self._first_seen_day:
                self._first_seen_day[ea_id] = timestamp.weekday()

            if self.friday_only and not self._is_friday_card(ea_id):
                continue

            history = self._history[ea_id]
            history.append((timestamp, price))
            card_age = self._card_age_days(ea_id, timestamp)
            if card_age is None:
                continue

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
                hold_days = self._hold_ticks[ea_id] / self.ticks_per_day
                if hold_days >= self.max_hold_days:
                    sell = True

                if sell:
                    signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                    self._peak_prices.pop(ea_id, None)
                    self._hold_ticks.pop(ea_id, None)
            else:
                if self.min_day <= card_age <= self.max_day and ea_id not in self._bought:
                    if self.min_price <= price <= self.max_price:
                        if len(history) > self.lookback_hours:
                            _, prev_price = history[-(self.lookback_hours + 1)]
                            if prev_price > 0:
                                bounce = (price - prev_price) / prev_price
                                if self.min_bounce <= bounce <= self.max_bounce:
                                    potential_buys.append((ea_id, price))

        # Size buys: split available cash equally, capped by max_position_pct
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

            # Cap per-card spend by max_position_pct of starting + current portfolio
            if self.max_position_pct > 0:
                # Estimate portfolio value (cash + held positions at current prices)
                price_map = {eid: p for eid, p in ticks}
                portfolio_value = portfolio.cash + sell_revenue + sum(
                    price_map.get(pos.ea_id, pos.buy_price) * pos.quantity
                    for pos in portfolio.positions
                )
                max_spend = int(portfolio_value * self.max_position_pct)
                per_card = min(per_card, max_spend)

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
                    for max_price in [40000, 61000]:
                        combos.append({
                            "min_bounce": min_bounce,
                            "max_bounce": max_bounce,
                            "min_day": 3,
                            "max_day": 10,
                            "min_price": 12000,
                            "max_price": max_price,
                            "trailing_stop": trailing_stop,
                            "max_hold_days": 14,
                            "friday_only": True,
                            "lookback_hours": 1,
                            "ticks_per_day": 1,
                        })
        return combos

    def param_grid_hourly(self) -> list[dict]:
        """Parameter grid for hourly market_snapshots data."""
        combos = []
        for min_price in [14000, 16000]:
            for max_price in [40000, 61000]:
                for max_position_pct in [0.10, 0.20, 0.50]:
                    combos.append({
                        "min_bounce": 0.03,
                        "max_bounce": 0.15,
                        "min_day": 3,
                        "max_day": 10,
                        "min_price": min_price,
                        "max_price": max_price,
                        "trailing_stop": 0.10,
                        "max_hold_days": 14,
                        "friday_only": True,
                        "lookback_hours": 6,
                        "ticks_per_day": 24,
                        "max_position_pct": max_position_pct,
                    })
        return combos
