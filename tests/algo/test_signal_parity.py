"""Signal parity test — live engine must match backtester exactly."""
import pytest
from datetime import datetime, timedelta
from collections import defaultdict

from src.algo.strategies.promo_dip_buy import PromoDipBuyStrategy
from src.algo.models import Signal, Portfolio
from src.algo.engine import run_backtest
from src.server.algo_engine import AlgoSignalEngine


def _make_promo_batch_data(
    num_cards: int = 12,
    base_price: int = 30000,
    hours: int = 400,
) -> tuple[dict[int, list[tuple[datetime, int]]], dict[int, datetime]]:
    """Generate synthetic promo batch data that triggers buy+sell signals.

    Creates a Friday promo batch of num_cards cards. Each card:
    - Starts at base_price
    - Crashes 50% over first 48h
    - Recovers with 25%+ trend by hour 72 (triggers Layer 1 buy)
    - Plateaus and stalls by hour 200 (triggers sell)
    """
    release = datetime(2026, 4, 3, 18, 0)  # A Friday
    assert release.weekday() == 4, "Must be Friday"

    price_data: dict[int, list[tuple[datetime, int]]] = {}
    created_at_map: dict[int, datetime] = {}

    for i in range(num_cards):
        ea_id = 100_000 + i
        created_at_map[ea_id] = release
        points = []

        for h in range(hours):
            ts = release + timedelta(hours=h)

            if h < 48:
                price = int(base_price * (1.0 - 0.5 * h / 48))
            elif h < 72:
                progress = (h - 48) / 24
                price = int(base_price * (0.5 + 0.25 * progress))
            elif h < 200:
                price = int(base_price * (0.75 + 0.02 * (h - 72) / 128))
            else:
                price = int(base_price * 0.77)

            points.append((ts, price))

        price_data[ea_id] = points

    return price_data, created_at_map


def _collect_backtester_signals(
    price_data: dict[int, list[tuple[datetime, int]]],
    created_at_map: dict[int, datetime],
    budget: int,
) -> list[tuple[datetime, str, int, int]]:
    """Run backtester and collect all signals as (timestamp, action, ea_id, quantity)."""
    params = PromoDipBuyStrategy({}).param_grid_hourly()[0]
    strategy = PromoDipBuyStrategy(params)
    strategy.set_created_at_map(created_at_map)

    portfolio = Portfolio(cash=budget)

    timeline: dict[datetime, list[tuple[int, int]]] = defaultdict(list)
    for ea_id, points in price_data.items():
        for ts, price in points:
            timeline[ts].append((ea_id, price))

    sorted_ts = sorted(timeline.keys())
    if sorted_ts:
        existing_ids = {ea_id for ea_id, _ in timeline[sorted_ts[0]]}
        strategy.set_existing_ids(existing_ids)

    all_signals = []
    for ts in sorted_ts:
        ticks = timeline[ts]
        signals = strategy.on_tick_batch(ticks, ts, portfolio)
        for sig in signals:
            all_signals.append((ts, sig.action, sig.ea_id, sig.quantity))
            sig_price = next((p for eid, p in ticks if eid == sig.ea_id), 0)
            if sig.action == "BUY":
                portfolio.buy(sig.ea_id, sig.quantity, sig_price, ts)
            elif sig.action == "SELL":
                portfolio.sell(sig.ea_id, sig.quantity, sig_price, ts)

    return all_signals


def _collect_engine_signals(
    price_data: dict[int, list[tuple[datetime, int]]],
    created_at_map: dict[int, datetime],
    budget: int,
) -> list[tuple[datetime, str, int, int]]:
    """Run the live signal engine tick-by-tick and collect all signals."""
    engine = AlgoSignalEngine(budget=budget, created_at_map=created_at_map)

    timeline: dict[datetime, list[tuple[int, int]]] = defaultdict(list)
    for ea_id, points in price_data.items():
        for ts, price in points:
            timeline[ts].append((ea_id, price))

    sorted_ts = sorted(timeline.keys())

    all_signals = []
    for ts in sorted_ts:
        ticks = timeline[ts]
        signals = engine.process_tick(ticks, ts)
        for action, ea_id, quantity, price in signals:
            all_signals.append((ts, action, ea_id, quantity))

    return all_signals


def test_signal_parity_buy_layer_1():
    """BUY Layer 1 (strong signal) fires for same ea_ids at same ticks."""
    price_data, created_at_map = _make_promo_batch_data()
    budget = 5_000_000

    bt_signals = _collect_backtester_signals(price_data, created_at_map, budget)
    eng_signals = _collect_engine_signals(price_data, created_at_map, budget)

    bt_buys = [(ts, eid, qty) for ts, action, eid, qty in bt_signals if action == "BUY"]
    eng_buys = [(ts, eid, qty) for ts, action, eid, qty in eng_signals if action == "BUY"]

    assert len(bt_buys) > 0, "Backtester should produce at least one BUY signal"
    assert bt_buys == eng_buys


def test_signal_parity_sell():
    """SELL signals fire for same ea_ids at same ticks."""
    price_data, created_at_map = _make_promo_batch_data()
    budget = 5_000_000

    bt_signals = _collect_backtester_signals(price_data, created_at_map, budget)
    eng_signals = _collect_engine_signals(price_data, created_at_map, budget)

    bt_sells = [(ts, eid, qty) for ts, action, eid, qty in bt_signals if action == "SELL"]
    eng_sells = [(ts, eid, qty) for ts, action, eid, qty in eng_signals if action == "SELL"]

    assert len(bt_sells) > 0, "Backtester should produce at least one SELL signal"
    assert bt_sells == eng_sells


def test_signal_parity_full_sequence():
    """Full signal sequence (all BUYs and SELLs in order) matches exactly."""
    price_data, created_at_map = _make_promo_batch_data()
    budget = 5_000_000

    bt_signals = _collect_backtester_signals(price_data, created_at_map, budget)
    eng_signals = _collect_engine_signals(price_data, created_at_map, budget)

    assert len(bt_signals) > 0, "Should produce signals"
    assert bt_signals == eng_signals


def test_signal_parity_position_sizing():
    """Quantities match exactly — same integer division, same max_position_pct cap."""
    price_data, created_at_map = _make_promo_batch_data(num_cards=15)
    budget = 2_000_000  # Smaller budget to stress position sizing

    bt_signals = _collect_backtester_signals(price_data, created_at_map, budget)
    eng_signals = _collect_engine_signals(price_data, created_at_map, budget)

    assert bt_signals == eng_signals


def test_signal_parity_snapshot_layer():
    """BUY Layer 2 (snapshot at 176h) fires for same cards."""
    price_data, created_at_map = _make_promo_batch_data(
        num_cards=12, base_price=30000, hours=400,
    )

    # Modify prices so only some cards have weak trend (below 21%) but positive
    for ea_id in list(price_data.keys())[:5]:
        points = price_data[ea_id]
        new_points = []
        for ts, price in points:
            h = int((ts - datetime(2026, 4, 3, 18, 0)).total_seconds() / 3600)
            if h < 48:
                price = int(30000 * (1.0 - 0.3 * h / 48))
            elif h < 200:
                progress = (h - 48) / 152
                price = int(30000 * (0.7 + 0.10 * progress))
            else:
                price = int(30000 * 0.80)
            new_points.append((ts, price))
        price_data[ea_id] = new_points

    budget = 5_000_000
    bt_signals = _collect_backtester_signals(price_data, created_at_map, budget)
    eng_signals = _collect_engine_signals(price_data, created_at_map, budget)

    assert bt_signals == eng_signals
