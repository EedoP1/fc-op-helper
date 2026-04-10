"""Sweep snapshot hour: at hour X after release, rank all cards by trend, buy top N."""
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


class SnapshotPromoDipBuy(PromoDipBuyStrategy):
    """At exactly snapshot_hour after release, rank batch by trend, buy top N."""

    name = "snapshot_promo_dip"

    def __init__(self, params: dict):
        super().__init__(params)
        self.top_n: int = params.get("top_n", 5)
        self.min_trend_floor: float = params.get("min_trend_floor", 0.0)
        self.snapshot_hour: int = params.get("snapshot_hour", 174)
        self._batch_map: dict[int, str] = {}
        self._batch_bought: dict[str, bool] = {}
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
                cr = datetime(*bucket[:3], bucket[3])
                self._batch_created[key] = cr

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

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

        # --- BUY: one-shot snapshot at exact hour ---
        tick_price = {eid: p for eid, p in ticks}

        for batch_key, batch_created in self._batch_created.items():
            if self._batch_bought.get(batch_key):
                continue

            hours_since = (ts_clean - batch_created).total_seconds() / 3600
            # Fire on the first tick at or after snapshot_hour
            if hours_since < self.snapshot_hour:
                continue

            # This is the snapshot tick — rank all batch cards now
            self._batch_bought[batch_key] = True

            candidates = []
            for ea_id, price in ticks:
                if self._batch_map.get(ea_id) != batch_key:
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

                if trend < self.min_trend_floor:
                    continue

                candidates.append((ea_id, price, trend))

            # Rank by trend, buy top N
            candidates.sort(key=lambda x: x[2], reverse=True)
            potential_buys = [(eid, p) for eid, p, t in candidates[:self.top_n]]

            if potential_buys:
                sell_revenue = sum(
                    next((p * sig.quantity * 95 // 100 for eid, p in ticks if eid == sig.ea_id), 0)
                    for sig in signals if sig.action == "SELL"
                )
                available_cash = portfolio.cash + sell_revenue
                per_card = available_cash // len(potential_buys)
                if self.max_position_pct > 0:
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
    base_params = {
        "trend_lookback": 12, "sell_trend_pct": 0.02,
        "min_day": 0, "max_day": 999, "min_crash": 0.05, "trailing_stop": 1.0,
        "stop_delay_hours": 96, "max_hold_hours": 336,
        "min_price": 12000, "max_price": 61000, "max_position_pct": 0.10,
        "trend_pct": 0.20,
    }

    def batch_pnl(trades):
        batches = defaultdict(lambda: {"pnl": 0, "trades": 0, "wins": 0, "force_sold": 0})
        for t in trades:
            created = created_at_map.get(t["ea_id"])
            if created:
                key = created.strftime("%m-%d")
            else:
                key = "?"
            batches[key]["pnl"] += t["net_profit"]
            batches[key]["trades"] += 1
            if t["net_profit"] > 0:
                batches[key]["wins"] += 1
            # Check if force-sold (sell_time is last data point)
            if t["sell_time"][:10] == "2026-04-08":
                batches[key]["force_sold"] += 1
        return batches

    # Original for reference
    orig = PromoDipBuyStrategy({**base_params})
    r = run_backtest(orig, price_data, budget, created_at_map=created_at_map)
    bp = batch_pnl(r["trades"])
    print(f"{'Config':<40} {'PnL':>10} {'#':>3} {'W%':>5} {'Sharpe':>7}  {'Mar20':>12} {'Mar27':>12} {'Apr3':>12}")
    print("-" * 115)
    m20 = f"{bp['03-20']['pnl']:>+7,}({bp['03-20']['wins']}/{bp['03-20']['trades']})"
    m27 = f"{bp['03-27']['pnl']:>+7,}({bp['03-27']['wins']}/{bp['03-27']['trades']})"
    a03 = f"{bp['04-03']['pnl']:>+7,}({bp['04-03']['wins']}/{bp['04-03']['trades']})"
    print(f"{'Original 20% (reference)':<40} {r['total_pnl']:>+10,} {r['total_trades']:>3} {r['win_rate']:>4.0%} {r['sharpe_ratio']:>7.3f}  {m20:>12} {m27:>12} {a03:>12}")
    print()

    # Sweep snapshot_hour x top_n x min_floor
    for top_n in [3, 5, 8]:
        for floor in [0.0, 0.05, 0.10]:
            for snap_h in range(170, 201, 2):
                params = {
                    **base_params,
                    "top_n": top_n,
                    "min_trend_floor": floor,
                    "snapshot_hour": snap_h,
                }
                strategy = SnapshotPromoDipBuy(params)
                r = run_backtest(strategy, price_data, budget, created_at_map=created_at_map)
                bp = batch_pnl(r["trades"])

                m20 = f"{bp['03-20']['pnl']:>+7,}({bp['03-20']['wins']}/{bp['03-20']['trades']})"
                m27 = f"{bp['03-27']['pnl']:>+7,}({bp['03-27']['wins']}/{bp['03-27']['trades']})"
                a03 = f"{bp['04-03']['pnl']:>+7,}({bp['04-03']['wins']}/{bp['04-03']['trades']})"
                # Mark if Apr3 has force-sells
                fs = bp["04-03"]["force_sold"]
                a03_mark = "*" if fs else ""

                label = f"snap={snap_h}h top{top_n} floor{floor:.0%}"
                print(f"{label:<40} {r['total_pnl']:>+10,} {r['total_trades']:>3} {r['win_rate']:>4.0%} {r['sharpe_ratio']:>7.3f}  {m20:>12} {m27:>12} {a03:>12}{a03_mark}")

    # Show trades for best combos
    print(f"\n\n{'='*120}")
    print("TRADE DETAILS FOR BEST COMBOS")
    print(f"{'='*120}")

    best_combos = [
        ("snap=178h top5 floor0%", {"top_n": 5, "min_trend_floor": 0.0, "snapshot_hour": 178}),
        ("snap=180h top5 floor0%", {"top_n": 5, "min_trend_floor": 0.0, "snapshot_hour": 180}),
        ("snap=180h top5 floor5%", {"top_n": 5, "min_trend_floor": 0.05, "snapshot_hour": 180}),
        ("snap=178h top5 floor5%", {"top_n": 5, "min_trend_floor": 0.05, "snapshot_hour": 178}),
    ]
    for label, extra in best_combos:
        params = {**base_params, **extra}
        strategy = SnapshotPromoDipBuy(params)
        r = run_backtest(strategy, price_data, budget, created_at_map=created_at_map)

        print(f"\n--- {label} | PnL: {r['total_pnl']:+,} | Win: {r['win_rate']:.0%} | Trades: {r['total_trades']} ---")
        trades_sorted = sorted(r["trades"], key=lambda t: t["buy_time"])
        for t in trades_sorted:
            name = names.get(t["ea_id"], str(t["ea_id"]))
            fs = " [FORCE-SELL]" if t["sell_time"][:10] == "2026-04-08" else ""
            print(f"  {name[:24]:<25} {t['qty']:>4}x  buy {t['buy_price']:>7,} @ {t['buy_time'][:13]}  sell {t['sell_price']:>7,} @ {t['sell_time'][:13]}  pnl {t['net_profit']:>+9,}{fs}")

    await engine.dispose()

asyncio.run(main())
