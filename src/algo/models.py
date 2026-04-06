"""Domain models for the algo trading backtester."""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Signal:
    """A trading signal emitted by a strategy."""

    action: str  # "BUY" or "SELL"
    ea_id: int
    quantity: int


@dataclass
class Position:
    """An open position (player cards held)."""

    ea_id: int
    quantity: int
    buy_price: int
    buy_time: datetime


@dataclass
class Trade:
    """A completed round-trip trade."""

    ea_id: int
    quantity: int
    buy_price: int
    sell_price: int
    buy_time: datetime
    sell_time: datetime
    net_profit: int  # after 5% EA tax


class Portfolio:
    """Read-only view of current trading state, passed to strategies."""

    def __init__(self, cash: int):
        self._cash = cash
        self._positions: list[Position] = []
        self._trades: list[Trade] = []
        self._balance_history: list[tuple[datetime, int]] = []

    @property
    def cash(self) -> int:
        return self._cash

    @property
    def positions(self) -> list[Position]:
        return list(self._positions)

    @property
    def trades(self) -> list[Trade]:
        return list(self._trades)

    @property
    def balance_history(self) -> list[tuple[datetime, int]]:
        return list(self._balance_history)

    def total_value(self, current_prices: dict[int, int]) -> int:
        """Cash + market value of all open positions."""
        held_value = sum(
            current_prices.get(p.ea_id, p.buy_price) * p.quantity
            for p in self._positions
        )
        return self._cash + held_value

    def holdings(self, ea_id: int) -> int:
        """Total quantity held for a given player."""
        return sum(p.quantity for p in self._positions if p.ea_id == ea_id)

    def buy(self, ea_id: int, quantity: int, price: int, timestamp: datetime):
        """Execute a buy. Deducts cash, creates position."""
        cost = price * quantity
        if cost > self._cash:
            return  # skip if insufficient funds
        self._cash -= cost
        self._positions.append(Position(
            ea_id=ea_id, quantity=quantity, buy_price=price, buy_time=timestamp,
        ))
        self._balance_history.append((timestamp, self._cash))

    def sell(self, ea_id: int, quantity: int, price: int, timestamp: datetime):
        """Execute a sell. Adds cash (after 5% tax), records trade."""
        remaining = quantity
        to_remove = []
        for i, pos in enumerate(self._positions):
            if pos.ea_id != ea_id or remaining <= 0:
                continue
            sold_qty = min(pos.quantity, remaining)
            revenue = int(price * sold_qty * 0.95)  # 5% EA tax
            net_profit = revenue - (pos.buy_price * sold_qty)
            self._cash += revenue
            self._trades.append(Trade(
                ea_id=ea_id,
                quantity=sold_qty,
                buy_price=pos.buy_price,
                sell_price=price,
                buy_time=pos.buy_time,
                sell_time=timestamp,
                net_profit=net_profit,
            ))
            remaining -= sold_qty
            if sold_qty >= pos.quantity:
                to_remove.append(i)
            else:
                pos.quantity -= sold_qty
        for i in reversed(to_remove):
            self._positions.pop(i)
        self._balance_history.append((timestamp, self._cash))
