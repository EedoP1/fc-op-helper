from datetime import datetime
from src.algo.strategies.base import Strategy
from src.algo.models import Portfolio


class DummyStrategy(Strategy):
    name = "dummy"

    def __init__(self, params: dict):
        self.params = params

    def on_tick(self, ea_id, price, timestamp, portfolio):
        return []

    def param_grid(self):
        return [{"x": 1}, {"x": 2}]


def test_strategy_interface():
    s = DummyStrategy({"x": 1})
    assert s.name == "dummy"
    signals = s.on_tick(1, 50000, datetime(2026, 1, 1), Portfolio(100000))
    assert signals == []


def test_param_grid():
    s = DummyStrategy({})
    grid = s.param_grid()
    assert len(grid) == 2


from src.algo.strategies import discover_strategies


def test_discover_finds_nothing_initially():
    # Only base.py exists, no concrete strategies yet
    strategies = discover_strategies()
    assert isinstance(strategies, dict)
    # Will find strategies once we add them in later tasks
