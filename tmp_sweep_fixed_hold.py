"""Sweep fixed hold time: buy, hold exactly N hours, sell at market. No trend check."""
import asyncio, sys, io
from datetime import datetime, timedelta
from collections import defaultdict
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from src.config import DATABASE_URL
from src.algo.models import Signal, Portfolio
from src.algo.strategies.promo_dip_buy import PromoDipBuyStrategy
from src.algo.engine import run_backtest, load_market_snapshot_data

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


class FixedHoldPromoDipBuy(PromoDipBuyStrategy):
    """21% strong + snap176 top3. Sell after exactly fixed_hold_hours."""

    name = "fixed_hold_promo_dip"

    def __init__(self, params: dict):
        super().__init__(params)
        self.snapshot_top_n: int = params.get("snapshot_top_n", 3)
        self.snapshot_hour: int = params.get("snapshot_hour", 176)
        self.snapshot_floor: float = params.get("snapshot_floor", 0.0)
        self.fixed_hold_hours: int = params.get("fixed_hold_hours", 96)
        self._batch_map: dict[int, str] = {}
        self._batch_snapshot_done: dict[str, bool] = {}
        self._batch_created: dict[str, datetime] = {}

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
                self._batch_created[key] = datetime(*bucket[:3], bucket[3])

    def on_tick_batch(self, ticks, timestamp, portfolio):
        signals = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

        # Track state
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

            # SELL: fixed hold time only
            holding = portfolio.holdings(ea_id)
            if holding > 0:
                if price > self._peak_prices.get(ea_id, 0):
                    self._peak_prices[ea_id] = price
                buy_ts = self._buy_ts.get(ea_id)
                if buy_ts:
                    bt_clean = buy_ts.replace(tzinfo=None) if buy_ts.tzinfo else buy_ts
                    hold_hours = (ts_clean - bt_clean).total_seconds() / 3600
                    if hold_hours >= self.fixed_hold_hours:
                        signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                        self._peak_prices.pop(ea_id, None)
                        self._buy_ts.pop(ea_id, None)

        # BUY LAYER 1: 21%+ strong
        strong_buys = []
        for ea_id, price in ticks:
            if ea_id not in self._created_at_map:
                continue
            if ea_id in self._bought or portfolio.holdings(ea_id) > 0:
                continue
            if ea_id not in self._promo_ids:
                continue
            created = self._created_at_map[ea_id]
            cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
            days_since = (ts_clean - cr_clean).days
            if days_since < 0 or days_since > 13:
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
            if trend >= self.trend_pct:
                strong_buys.append((ea_id, price))

        # BUY LAYER 2: snapshot top N
        snapshot_buys = []
        for batch_key, batch_created in self._batch_created.items():
            if self._batch_snapshot_done.get(batch_key):
                continue
            hours_since = (ts_clean - batch_created).total_seconds() / 3600
            if hours_since < self.snapshot_hour:
                continue
            self._batch_snapshot_done[batch_key] = True
            already_bought_ids = self._bought | {eid for eid, _ in strong_buys}
            candidates = []
            for ea_id, price in ticks:
                if self._batch_map.get(ea_id) != batch_key:
                    continue
                if ea_id in already_bought_ids or portfolio.holdings(ea_id) > 0:
                    continue
                if ea_id not in self._promo_ids:
                    continue
                if not (self.min_price <= price <= self.max_price):
                    continue
                history = self._history.get(ea_id, [])
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
                if trend < self.snapshot_floor:
                    continue
                candidates.append((ea_id, price, trend))
            candidates.sort(key=lambda x: x[2], reverse=True)
            for ea_id, price, trend in candidates[:self.snapshot_top_n]:
                snapshot_buys.append((ea_id, price))

        potential_buys = strong_buys + snapshot_buys
        if potential_buys:
            sell_revenue = sum(
                next((p * sig.quantity * 95 // 100 for eid, p in ticks if eid == sig.ea_id), 0)
                for sig in signals if sig.action == "SELL"
            )
            available_cash = portfolio.cash + sell_revenue
            per_card = available_cash // len(potential_buys)
            if self.max_position_pct > 0:
                tick_price = {eid: p for eid, p in ticks}
                portfolio_value = portfolio.cash + sell_revenue + sum(
                    tick_price.get(pos.ea_id, pos.buy_price) * pos.quantity
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

        return signals


async def main():
    engine = create_async_engine(DATABASE_URL)
    sf = async_sessionmaker(engine, expire_on_commit=False)

    price_data, created_at_map = await load_market_snapshot_data(sf)

    async with sf() as s:
        r = await s.execute(text("SELECT ea_id, name FROM players WHERE name IS NOT NULL"))
        names = {row[0]: row[1] for row in r.fetchall()}

    budget = 1_000_000

    def batch_pnl(trades):
        batches = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0, "fs": 0})
        for t in trades:
            created = created_at_map.get(t["ea_id"])
            key = created.strftime("%m-%d") if created else "?"
            batches[key]["pnl"] += t["net_profit"]
            batches[key]["trades"] += 1
            if t["net_profit"] > 0:
                batches[key]["wins"] += 1
            if t["sell_time"][:10] == "2026-04-08":
                batches[key]["fs"] += 1
        return batches

    def fmt_batch(bp, key):
        b = bp[key]
        fs = "~" if b["fs"] else ""
        return f"{b['pnl']:>+8,}({b['wins']}/{b['trades']}){fs}"

    print(f"{'hold_h':>6} {'PnL':>10} {'#':>3} {'W%':>5} {'Sharpe':>7}  {'Mar20':>14} {'Mar27':>14} {'Apr3':>14}  {'Open':>4}")
    print("-" * 100)

    for hold_h in range(24, 241, 6):
        params = {
            "trend_lookback": 12, "min_day": 0, "max_day": 999, "min_crash": 0.05,
            "trailing_stop": 1.0, "stop_delay_hours": 0, "sell_trend_pct": -999,
            "max_hold_hours": 9999,
            "min_price": 12000, "max_price": 61000, "max_position_pct": 0.10,
            "trend_pct": 0.21,
            "snapshot_top_n": 3, "snapshot_hour": 176, "snapshot_floor": 0.0,
            "fixed_hold_hours": hold_h,
        }
        strategy = FixedHoldPromoDipBuy(params)
        r = run_backtest(strategy, price_data, budget, created_at_map=created_at_map)
        bp = batch_pnl(r["trades"])
        open_count = sum(1 for t in r["trades"] if t["sell_time"][:10] == "2026-04-08")
        print(f"{hold_h:>6} {r['total_pnl']:>+10,} {r['total_trades']:>3} {r['win_rate']:>4.0%} {r['sharpe_ratio']:>7.3f}  {fmt_batch(bp,'03-20'):>14} {fmt_batch(bp,'03-27'):>14} {fmt_batch(bp,'04-03'):>14}  {open_count:>4}")

    await engine.dispose()

asyncio.run(main())
