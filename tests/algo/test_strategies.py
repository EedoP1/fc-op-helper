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


def test_momentum_buys_on_uptrend():
    from src.algo.strategies.momentum import MomentumStrategy

    s = MomentumStrategy({"trend_length": 3, "trailing_stop": 0.05, "position_pct": 0.05})
    portfolio = Portfolio(cash=100_000)

    base = datetime(2026, 1, 1)
    # 3 consecutive rising prices
    s.on_tick(1, 10_000, base + timedelta(hours=0), portfolio)
    s.on_tick(1, 10_100, base + timedelta(hours=1), portfolio)
    signals = s.on_tick(1, 10_200, base + timedelta(hours=2), portfolio)
    assert len(signals) == 1
    assert signals[0].action == "BUY"


def test_momentum_sells_on_trailing_stop():
    from src.algo.strategies.momentum import MomentumStrategy

    s = MomentumStrategy({"trend_length": 2, "trailing_stop": 0.05, "position_pct": 0.05})
    portfolio = Portfolio(cash=100_000)

    base = datetime(2026, 1, 1)
    # Build uptrend and buy
    s.on_tick(1, 10_000, base + timedelta(hours=0), portfolio)
    buy_signals = s.on_tick(1, 10_100, base + timedelta(hours=1), portfolio)
    # Simulate backtester executing the BUY
    assert len(buy_signals) == 1 and buy_signals[0].action == "BUY"
    portfolio.buy(1, buy_signals[0].quantity, 10_100, base + timedelta(hours=1))
    # Now holding — price rises then drops 6% from peak
    s.on_tick(1, 11_000, base + timedelta(hours=2), portfolio)
    # Peak was 11000, trailing stop at 5% = sell below 10450
    signals = s.on_tick(1, 10_400, base + timedelta(hours=3), portfolio)
    assert len(signals) == 1
    assert signals[0].action == "SELL"


def test_weekly_cycle_buys_on_buy_day():
    from src.algo.strategies.weekly_cycle import WeeklyCycleStrategy

    # Thursday = weekday 3, buy_hour = 18
    s = WeeklyCycleStrategy({
        "buy_day": 3, "buy_hour": 18,
        "sell_day": 5, "sell_hour": 12,
        "position_pct": 0.05,
    })
    portfolio = Portfolio(cash=100_000)

    # Thursday 18:00
    ts = datetime(2026, 1, 1, 18, 0)  # 2026-01-01 is a Thursday
    signals = s.on_tick(1, 10_000, ts, portfolio)
    assert len(signals) == 1
    assert signals[0].action == "BUY"


def test_weekly_cycle_sells_on_sell_day():
    from src.algo.strategies.weekly_cycle import WeeklyCycleStrategy

    s = WeeklyCycleStrategy({
        "buy_day": 3, "buy_hour": 18,
        "sell_day": 5, "sell_hour": 12,
        "position_pct": 0.05,
    })
    portfolio = Portfolio(cash=100_000)

    # Buy on Thursday
    ts_buy = datetime(2026, 1, 1, 18, 0)
    signals = s.on_tick(1, 10_000, ts_buy, portfolio)
    # Execute the buy so portfolio has holdings
    portfolio.buy(1, signals[0].quantity, 10_000, ts_buy)

    # Saturday 12:00 — should sell
    ts_sell = datetime(2026, 1, 3, 12, 0)
    signals = s.on_tick(1, 11_000, ts_sell, portfolio)
    assert len(signals) == 1
    assert signals[0].action == "SELL"
