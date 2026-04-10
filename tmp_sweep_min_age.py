"""Sweep min_buy_hours for relative promo_dip_buy."""
import asyncio, sys, io, json
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.config import DATABASE_URL
from src.algo.models import Signal, Portfolio
from src.algo.strategies.promo_dip_buy import PromoDipBuyStrategy
from src.algo.engine import run_backtest, load_market_snapshot_data

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


class RelativePromoDipBuy(PromoDipBuyStrategy):
    """Buy top N risers per batch with minimum age gate."""

    name = "promo_dip_buy_relative"

    def __init__(self, params: dict):
        super().__init__(params)
        self.top_n: int = params.get("top_n", 5)
        self.min_trend_floor: float = params.get("min_trend_floor", 0.10)
        self.min_buy_hours: int = params.get("min_buy_hours", 0)
        self._batch_map: dict[int, str] = {}
        self._batch_bought_count: dict[str, int] = defaultdict(int)

    def set_created_at_map(self, created_at_map: dict):
        super().set_created_at_map(created_at_map)
        hour_buckets: dict[tuple, list[int]] = defaultdict(list)
        for ea_id, created in created_at_map.items():
            cr = created.replace(tzinfo=None) if created.tzinfo else created
            if cr.weekday() == 4:
                bucket = (cr.year, cr.month, cr.day, cr.hour)
                hour_buckets[bucket].append(ea_id)
        for bucket, ids in hour_buckets.items():
            if len(ids) >= 10:
                key = f"{bucket[0]}-{bucket[1]:02d}-{bucket[2]:02d}"
                for ea_id in ids:
                    self._batch_map[ea_id] = key

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals = []

        # --- Track state + SELL logic (identical to parent) ---
        for ea_id, price in ticks:
            if ea_id not in self._created_at_map:
                continue
            if ea_id not in self._first_seen_ts:
                self._first_seen_ts[ea_id] = timestamp
                self._first_seen_price[ea_id] = price
                self._tracked_low[ea_id] = price
            self._history[ea_id].append((timestamp, price))
            if price < self._tracked_low[ea_id]:
                self._tracked_low[ea_id] = price

            holding = portfolio.holdings(ea_id)
            if holding > 0:
                if price > self._peak_prices.get(ea_id, 0):
                    self._peak_prices[ea_id] = price
                buy_ts = self._buy_ts.get(ea_id)
                hold_hours = 0
                if buy_ts:
                    ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp
                    bt_clean = buy_ts.replace(tzinfo=None) if buy_ts.tzinfo else buy_ts
                    hold_hours = (ts_clean - bt_clean).total_seconds() / 3600

                sell = False
                sell_history = self._history[ea_id]
                sell_lb = self.trend_lookback
                if hold_hours >= self.stop_delay_hours and len(sell_history) >= sell_lb * 2:
                    sell_recent = sorted([p for _, p in sell_history[-sell_lb:]])
                    sell_older = sorted([p for _, p in sell_history[-sell_lb*2:-sell_lb]])
                    sell_recent_med = sell_recent[len(sell_recent) // 2]
                    sell_older_med = sell_older[len(sell_older) // 2]
                    if sell_older_med > 0:
                        sell_trend = (sell_recent_med - sell_older_med) / sell_older_med
                        if sell_trend <= self.sell_trend_pct:
                            sell = True
                if hold_hours >= self.max_hold_hours:
                    sell = True
                if sell:
                    signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                    self._peak_prices.pop(ea_id, None)
                    self._buy_ts.pop(ea_id, None)

        # --- BUY logic: relative ranking per batch with min age ---
        batch_candidates: dict[str, list[tuple[int, int, float]]] = defaultdict(list)

        for ea_id, price in ticks:
            if ea_id not in self._created_at_map:
                continue
            if ea_id in self._bought or portfolio.holdings(ea_id) > 0:
                continue
            if ea_id not in self._promo_ids or ea_id not in self._batch_map:
                continue

            created = self._created_at_map[ea_id]
            cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
            ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp
            hours_since = (ts_clean - cr_clean).total_seconds() / 3600

            # Min age gate
            if hours_since < self.min_buy_hours:
                continue
            # Max age (13 days)
            if hours_since > 13 * 24:
                continue

            if not (self.min_price <= price <= self.max_price):
                continue

            history = self._history[ea_id]
            lb = self.trend_lookback
            if len(history) < lb * 2:
                continue

            recent_prices = sorted([p for _, p in history[-lb:]])
            older_prices = sorted([p for _, p in history[-lb*2:-lb]])
            recent_med = recent_prices[len(recent_prices) // 2]
            older_med = older_prices[len(older_prices) // 2]
            if older_med <= 0:
                continue
            trend = (recent_med - older_med) / older_med

            if trend < self.min_trend_floor:
                continue

            batch_key = self._batch_map[ea_id]
            batch_candidates[batch_key].append((ea_id, price, trend))

        potential_buys = []
        for batch_key, candidates in batch_candidates.items():
            already_bought = self._batch_bought_count[batch_key]
            remaining = self.top_n - already_bought
            if remaining <= 0:
                continue
            candidates.sort(key=lambda x: x[2], reverse=True)
            for ea_id, price, trend in candidates[:remaining]:
                potential_buys.append((ea_id, price))

        if potential_buys:
            sell_revenue = sum(
                next((p * sig.quantity * 95 // 100 for eid, p in ticks if eid == sig.ea_id), 0)
                for sig in signals if sig.action == "SELL"
            )
            available_cash = portfolio.cash + sell_revenue
            per_card = available_cash // len(potential_buys)
            if self.max_position_pct > 0:
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
                    self._buy_ts[ea_id] = timestamp
                    self._bought.add(ea_id)
                    self._batch_bought_count[self._batch_map[ea_id]] += 1

        return signals


async def main():
    engine = create_async_engine(DATABASE_URL)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    price_data, created_at_map = await load_market_snapshot_data(sf)

    async with sf() as s:
        r = await s.execute(text("SELECT ea_id, name FROM players WHERE name IS NOT NULL"))
        names = {row[0]: row[1] for row in r.fetchall()}

    budget = 1_000_000
    base_params = {
        "trend_lookback": 12, "sell_trend_pct": 0.02,
        "min_day": 0, "max_day": 999, "min_crash": 0.05, "trailing_stop": 1.0,
        "stop_delay_hours": 96, "max_hold_hours": 336,
        "min_price": 12000, "max_price": 61000, "max_position_pct": 0.10,
        "trend_pct": 0.20,
    }

    # Also run original for reference
    print(f"{'Config':<45} {'PnL':>10} {'Trades':>7} {'Win%':>6} {'Sharpe':>7}  Mar20    Mar27    Apr3")
    print("-" * 130)

    # Original
    orig = PromoDipBuyStrategy({**base_params})
    r = run_backtest(orig, price_data, budget, created_at_map=created_at_map)
    batch_pnl = _batch_pnl(r["trades"], created_at_map)
    print(f"{'Original 20% absolute':<45} {r['total_pnl']:>+10,} {r['total_trades']:>7} {r['win_rate']:>5.0%} {r['sharpe_ratio']:>7.3f}  {batch_pnl.get('03-20',0):>+7,}  {batch_pnl.get('03-27',0):>+7,}  {batch_pnl.get('04-03',0):>+7,}")

    # Sweep
    for top_n in [5, 8]:
        for floor in [0.10, 0.15]:
            for min_hrs in [0, 48, 72, 96, 120, 144, 168]:
                label = f"top{top_n} floor{floor:.0%} min{min_hrs}h"
                params = {
                    **base_params,
                    "top_n": top_n, "min_trend_floor": floor,
                    "min_buy_hours": min_hrs,
                }
                strategy = RelativePromoDipBuy(params)
                r = run_backtest(strategy, price_data, budget, created_at_map=created_at_map)
                batch_pnl = _batch_pnl(r["trades"], created_at_map)
                print(f"{label:<45} {r['total_pnl']:>+10,} {r['total_trades']:>7} {r['win_rate']:>5.0%} {r['sharpe_ratio']:>7.3f}  {batch_pnl.get('03-20',0):>+7,}  {batch_pnl.get('03-27',0):>+7,}  {batch_pnl.get('04-03',0):>+7,}")

    await engine.dispose()


def _batch_pnl(trades, created_at_map):
    batches = defaultdict(int)
    for t in trades:
        created = created_at_map.get(t["ea_id"])
        if created:
            batches[created.strftime("%m-%d")] += t["net_profit"]
    return batches


asyncio.run(main())
