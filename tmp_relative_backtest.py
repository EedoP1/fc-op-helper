"""Backtest promo_dip_buy with relative ranking instead of absolute threshold."""
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
    """Same as promo_dip_buy but buys top N risers per batch instead of fixed threshold."""

    name = "promo_dip_buy_relative"

    def __init__(self, params: dict):
        super().__init__(params)
        self.top_n: int = params.get("top_n", 5)
        self.min_trend_floor: float = params.get("min_trend_floor", 0.10)
        # Track which batch each card belongs to
        self._batch_map: dict[int, str] = {}  # ea_id -> batch_key
        self._batch_bought_count: dict[str, int] = defaultdict(int)

    def set_created_at_map(self, created_at_map: dict):
        super().set_created_at_map(created_at_map)
        # Build batch map
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

        # --- SELL logic: identical to parent ---
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

                peak = self._peak_prices[ea_id]

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

        # --- BUY logic: relative ranking per batch ---
        # Compute trend for all eligible promo cards, group by batch, buy top N
        batch_candidates: dict[str, list[tuple[int, int, float]]] = defaultdict(list)

        for ea_id, price in ticks:
            if ea_id not in self._created_at_map:
                continue
            if ea_id in self._bought:
                continue
            if portfolio.holdings(ea_id) > 0:
                continue
            if ea_id not in self._promo_ids:
                continue
            if ea_id not in self._batch_map:
                continue

            # Age check
            created = self._created_at_map[ea_id]
            cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
            ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp
            days_since = (ts_clean - cr_clean).days
            if days_since < 0 or days_since > 13:
                continue

            # Price filter
            if not (self.min_price <= price <= self.max_price):
                continue

            # Compute median trend (same as parent)
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

            # Must be above minimum floor
            if trend < self.min_trend_floor:
                continue

            batch_key = self._batch_map[ea_id]
            batch_candidates[batch_key].append((ea_id, price, trend))

        # For each batch, take top N by trend (that haven't been bought yet from this batch)
        potential_buys = []
        for batch_key, candidates in batch_candidates.items():
            already_bought = self._batch_bought_count[batch_key]
            remaining = self.top_n - already_bought
            if remaining <= 0:
                continue
            # Sort by trend descending, take top remaining
            candidates.sort(key=lambda x: x[2], reverse=True)
            for ea_id, price, trend in candidates[:remaining]:
                potential_buys.append((ea_id, price))

        # Size buys (same as parent)
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
                    batch_key = self._batch_map[ea_id]
                    self._batch_bought_count[batch_key] += 1

        return signals


async def main():
    engine = create_async_engine(DATABASE_URL)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    price_data, created_at_map = await load_market_snapshot_data(sf, min_price=0, max_price=0)

    # Load names
    async with sf() as s:
        r = await s.execute(text("SELECT ea_id, name FROM players WHERE name IS NOT NULL"))
        names = {row[0]: row[1] for row in r.fetchall()}

    budget = 1_000_000

    # Run both strategies
    configs = [
        ("ORIGINAL (20% absolute)", PromoDipBuyStrategy, {
            "trend_pct": 0.20, "trend_lookback": 12, "sell_trend_pct": 0.02,
            "min_day": 0, "max_day": 999, "min_crash": 0.05, "trailing_stop": 1.0,
            "stop_delay_hours": 96, "max_hold_hours": 336,
            "min_price": 12000, "max_price": 61000, "max_position_pct": 0.10,
        }),
        ("RELATIVE (top 5, 10% floor)", RelativePromoDipBuy, {
            "trend_pct": 0.20, "trend_lookback": 12, "sell_trend_pct": 0.02,
            "min_day": 0, "max_day": 999, "min_crash": 0.05, "trailing_stop": 1.0,
            "stop_delay_hours": 96, "max_hold_hours": 336,
            "min_price": 12000, "max_price": 61000, "max_position_pct": 0.10,
            "top_n": 5, "min_trend_floor": 0.10,
        }),
        ("RELATIVE (top 5, 5% floor)", RelativePromoDipBuy, {
            "trend_pct": 0.20, "trend_lookback": 12, "sell_trend_pct": 0.02,
            "min_day": 0, "max_day": 999, "min_crash": 0.05, "trailing_stop": 1.0,
            "stop_delay_hours": 96, "max_hold_hours": 336,
            "min_price": 12000, "max_price": 61000, "max_position_pct": 0.10,
            "top_n": 5, "min_trend_floor": 0.05,
        }),
        ("RELATIVE (top 8, 10% floor)", RelativePromoDipBuy, {
            "trend_pct": 0.20, "trend_lookback": 12, "sell_trend_pct": 0.02,
            "min_day": 0, "max_day": 999, "min_crash": 0.05, "trailing_stop": 1.0,
            "stop_delay_hours": 96, "max_hold_hours": 336,
            "min_price": 12000, "max_price": 61000, "max_position_pct": 0.10,
            "top_n": 8, "min_trend_floor": 0.10,
        }),
    ]

    for label, cls, params in configs:
        strategy = cls(params)
        result = run_backtest(strategy, price_data, budget, created_at_map=created_at_map)

        trades = result["trades"]
        closed = [t for t in trades]
        winning = [t for t in closed if t["net_profit"] > 0]
        losing = [t for t in closed if t["net_profit"] <= 0]

        print(f"\n{'='*120}")
        print(f"{label}")
        print(f"PnL: {result['total_pnl']:+,} | Trades: {result['total_trades']} | Win: {result['win_rate']:.0%} | Sharpe: {result['sharpe_ratio']:.3f}")
        print(f"{'='*120}")

        # Group trades by promo batch
        trade_batches = defaultdict(list)
        for t in sorted(closed, key=lambda x: x["buy_time"]):
            ea_id = t["ea_id"]
            created = created_at_map.get(ea_id)
            if created:
                batch = created.strftime("%m-%d")
            else:
                batch = "?"
            trade_batches[batch].append(t)

        for batch, batch_trades in sorted(trade_batches.items()):
            batch_pnl = sum(t["net_profit"] for t in batch_trades)
            print(f"\n  Promo {batch} ({len(batch_trades)} trades, PnL: {batch_pnl:+,})")
            print(f"  {'Card':<25} {'Qty':>4} {'Buy':>7} {'Sell':>7} {'PnL':>9} {'Buy Time':<16} {'Sell Time':<16}")
            print(f"  {'-'*95}")
            for t in batch_trades:
                name = names.get(t["ea_id"], str(t["ea_id"]))
                print(f"  {name[:24]:<25} {t['qty']:>4} {t['buy_price']:>7,} {t['sell_price']:>7,} {t['net_profit']:>+9,} {t['buy_time'][:16]:<16} {t['sell_time'][:16]:<16}")

    await engine.dispose()

asyncio.run(main())
