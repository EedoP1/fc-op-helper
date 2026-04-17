"""Hourly dip reversion — buy intraday dips below 48h median, sell on recovery.

Hypothesis: non-promo cards oscillate around a 2-day median. Sharp intraday
dips (5-10% below the median) typically revert within 12-24 hours because
underlying demand (WL building, SBC demand) is steady. We want to catch these
dips on liquid cards, not follow price trends.

Why this should hit the weekly bar: dips happen daily (WL-ending dump Sun,
random listing spikes Tue-Thu, promo-day sympathy drops Fri), so the strategy
can fire on 4+ weekdays. Target 5-15% per trade, 2-4x turnover/week = ~25% net.

Differentiated from promo_dip_buy: promo_dip_buy buys on RISING trend after
Friday promo. This buys on FALLING price vs median on NON-promo cards, avoiding
Fridays for buys so correlation with promo_dip_buy stays low.
"""
import logging
from datetime import datetime, timedelta
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class HourlyDipRevertStrategy(Strategy):
    """Buy N% dips below 48h median on non-promo cards, sell on recovery or target."""

    name = "hourly_dip_revert"

    def __init__(self, params: dict):
        self.params = params
        self.median_window_h: int = params.get("median_window_h", 48)
        self.dip_pct: float = params.get("dip_pct", 0.07)
        self.profit_target: float = params.get("profit_target", 0.10)
        self.stop_loss: float = params.get("stop_loss", 0.10)
        self.max_hold_h: int = params.get("max_hold_h", 48)
        self.min_price: int = params.get("min_price", 10000)
        self.max_price: int = params.get("max_price", 150000)
        self.per_trade_pct: float = params.get("per_trade_pct", 0.15)
        self.max_positions: int = params.get("max_positions", 5)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.skip_fridays: bool = params.get("skip_fridays", True)

        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=self.median_window_h + 4))
        self._created_at: dict[int, datetime] = {}
        self._promo_ids: set[int] = set()
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}

    def set_created_at_map(self, created_at_map: dict):
        self._created_at = created_at_map
        # Flag cards created in Friday batches of 10+ (promo cards) — we skip buying these
        # so we don't compete with promo_dip_buy and don't correlate with it.
        hour_buckets: dict[tuple, list[int]] = defaultdict(list)
        for ea_id, created in created_at_map.items():
            cr = created.replace(tzinfo=None) if created.tzinfo else created
            if cr.weekday() == 4:  # Friday
                bucket = (cr.year, cr.month, cr.day, cr.hour)
                hour_buckets[bucket].append(ea_id)
        for bucket, ids in hour_buckets.items():
            if len(ids) >= 10:
                self._promo_ids.update(ids)

    def _median(self, history: deque) -> int:
        if not history:
            return 0
        sorted_prices = sorted(history)
        return sorted_prices[len(sorted_prices) // 2]

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

        # ── Update history and check sells ──

        for ea_id, price in ticks:
            self._history[ea_id].append(price)

            holding = portfolio.holdings(ea_id)
            if holding > 0:
                buy_price = self._buy_prices.get(ea_id, price)
                buy_ts = self._buy_ts.get(ea_id, ts_clean)
                bt_clean = buy_ts.replace(tzinfo=None) if buy_ts.tzinfo else buy_ts
                hold_hours = (ts_clean - bt_clean).total_seconds() / 3600
                pct = (price - buy_price) / buy_price if buy_price > 0 else 0

                sell = False
                if pct >= self.profit_target:
                    sell = True
                elif pct <= -self.stop_loss:
                    sell = True
                elif hold_hours >= self.max_hold_h:
                    sell = True
                else:
                    # Recovery to median exit
                    med = self._median(self._history[ea_id])
                    # need net profit after tax. Net = price*0.95 - buy_price
                    # break-even: price >= buy_price / 0.95 ≈ 1.0526 * buy_price
                    if price >= med and price >= int(buy_price * 1.07):
                        sell = True

                if sell:
                    signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                    self._buy_prices.pop(ea_id, None)
                    self._buy_ts.pop(ea_id, None)

        # ── Check buys ──

        if self.skip_fridays and ts_clean.weekday() == 4:
            return signals

        if len(portfolio.positions) >= self.max_positions:
            return signals

        # Compute current cash available after any queued sells
        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                price = next((p for eid, p in ticks if eid == s.ea_id), 0)
                sell_rev += (price * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

        # Rank candidates by dip magnitude
        candidates: list[tuple[int, int, float]] = []
        for ea_id, price in ticks:
            if portfolio.holdings(ea_id) > 0:
                continue
            if ea_id in self._promo_ids:
                continue
            if not (self.min_price <= price <= self.max_price):
                continue
            created = self._created_at.get(ea_id)
            if created:
                cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
                age_days = (ts_clean - cr_clean).days
                if age_days < self.min_age_days:
                    continue
            hist = self._history[ea_id]
            if len(hist) < self.median_window_h // 2:
                continue
            med = self._median(hist)
            if med <= 0:
                continue
            dip = (med - price) / med
            if dip >= self.dip_pct:
                candidates.append((ea_id, price, dip))

        if not candidates:
            return signals

        candidates.sort(key=lambda x: x[2], reverse=True)

        # Compute portfolio value for position sizing
        price_map = {eid: p for eid, p in ticks}
        held_value = sum(
            price_map.get(pos.ea_id, pos.buy_price) * pos.quantity
            for pos in portfolio.positions
        )
        portfolio_value = available + held_value
        per_card_budget = int(portfolio_value * self.per_trade_pct)

        open_slots = self.max_positions - len(portfolio.positions)
        buys_made = 0
        for ea_id, price, dip in candidates:
            if buys_made >= open_slots:
                break
            if available <= 0:
                break
            spend = min(per_card_budget, available)
            qty = spend // price if price > 0 else 0
            if qty > 0:
                signals.append(Signal(action="BUY", ea_id=ea_id, quantity=qty))
                self._buy_prices[ea_id] = price
                self._buy_ts[ea_id] = timestamp
                available -= qty * price
                buys_made += 1
                logger.debug(
                    f"[{ts_clean}] DIP BUY: ea_id={ea_id} price={price:,} "
                    f"dip={dip:.1%} qty={qty} spend={qty*price:,}"
                )

        return signals

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        base = {
            "median_window_h": 48,
            "profit_target": 0.10,
            "stop_loss": 0.10,
            "max_hold_h": 48,
            "min_price": 10000,
            "max_price": 150000,
            "max_positions": 5,
            "min_age_days": 7,
            "skip_fridays": True,
        }
        combos = []
        for dip_pct in [0.05, 0.07, 0.10]:
            for per_trade_pct in [0.15, 0.25]:
                for max_positions in [4, 6]:
                    combos.append({
                        **base,
                        "dip_pct": dip_pct,
                        "per_trade_pct": per_trade_pct,
                        "max_positions": max_positions,
                    })
        return combos
