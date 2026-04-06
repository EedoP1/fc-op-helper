"""Tests for algo domain models: Signal, Position, Portfolio."""
from datetime import datetime

from src.algo.models import Portfolio, Signal


def test_buy_signal():
    sig = Signal(action="BUY", ea_id=12345, quantity=1)
    assert sig.action == "BUY"
    assert sig.ea_id == 12345
    assert sig.quantity == 1


def test_sell_signal():
    sig = Signal(action="SELL", ea_id=12345, quantity=1)
    assert sig.action == "SELL"


def test_portfolio_buy():
    p = Portfolio(cash=100_000)
    p.buy(ea_id=1, quantity=1, price=50_000, timestamp=datetime(2026, 1, 1))
    assert p.cash == 50_000
    assert p.holdings(1) == 1
    assert len(p.positions) == 1


def test_portfolio_buy_insufficient_funds():
    p = Portfolio(cash=10_000)
    p.buy(ea_id=1, quantity=1, price=50_000, timestamp=datetime(2026, 1, 1))
    assert p.cash == 10_000
    assert p.holdings(1) == 0


def test_portfolio_sell_with_tax():
    p = Portfolio(cash=100_000)
    p.buy(ea_id=1, quantity=1, price=50_000, timestamp=datetime(2026, 1, 1))
    p.sell(ea_id=1, quantity=1, price=60_000, timestamp=datetime(2026, 1, 2))
    # Revenue: 60000 * 0.95 = 57000
    assert p.cash == 50_000 + 57_000
    assert p.holdings(1) == 0
    assert len(p.trades) == 1
    assert p.trades[0].net_profit == 57_000 - 50_000  # 7000


def test_portfolio_sell_partial():
    p = Portfolio(cash=200_000)
    p.buy(ea_id=1, quantity=3, price=50_000, timestamp=datetime(2026, 1, 1))
    p.sell(ea_id=1, quantity=2, price=60_000, timestamp=datetime(2026, 1, 2))
    assert p.holdings(1) == 1
    assert len(p.trades) == 1
    assert p.trades[0].quantity == 2


def test_portfolio_total_value():
    p = Portfolio(cash=100_000)
    p.buy(ea_id=1, quantity=1, price=50_000, timestamp=datetime(2026, 1, 1))
    assert p.total_value({1: 60_000}) == 50_000 + 60_000
