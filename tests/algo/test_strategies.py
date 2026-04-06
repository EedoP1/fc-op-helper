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


from src.algo.models import Signal, Portfolio
from datetime import datetime, timedelta


def test_mean_reversion_buys_on_dip():
    from src.algo.strategies.mean_reversion import MeanReversionStrategy

    s = MeanReversionStrategy({"window": 4, "threshold": 0.10, "position_pct": 0.05})
    portfolio = Portfolio(cash=100_000)

    # Feed 4 hours of stable prices to build the window
    base = datetime(2026, 1, 1)
    for h in range(4):
        s.on_tick(1, 10_000, base + timedelta(hours=h), portfolio)

    # Price drops 15% — should trigger buy
    signals = s.on_tick(1, 8_500, base + timedelta(hours=4), portfolio)
    assert len(signals) == 1
    assert signals[0].action == "BUY"


def test_mean_reversion_no_buy_when_stable():
    from src.algo.strategies.mean_reversion import MeanReversionStrategy

    s = MeanReversionStrategy({"window": 4, "threshold": 0.10, "position_pct": 0.05})
    portfolio = Portfolio(cash=100_000)

    base = datetime(2026, 1, 1)
    for h in range(4):
        s.on_tick(1, 10_000, base + timedelta(hours=h), portfolio)

    # Price is stable — no buy
    signals = s.on_tick(1, 10_000, base + timedelta(hours=4), portfolio)
    assert signals == []


def test_discover_finds_mean_reversion():
    from src.algo.strategies import discover_strategies
    strategies = discover_strategies()
    assert "mean_reversion" in strategies
