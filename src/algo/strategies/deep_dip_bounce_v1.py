"""Deep dip + bounce confirmation v1.

Pessimistic loader imposes ~13% round-trip break-even (median hourly spread
4.2% per side × 2 + 5% tax). v12's 5% dip threshold catches shallow dips
that on liquid cards usually keep falling; v9's "wait for bounce" failed in
the prior loop because it paid higher max-fill on rising prices.

This strategy combines BOTH: require a DEEP dip (>=15% below 24h median)
AND confirm the trough is past before entry (smoothed_3h has risen >=2%
from its recent low within the dip). The depth filter screens noise; the
bounce filter avoids buying on the way down.

Entry:  smoothed drops >= min_dip from 24h median, then trough-tracker
        records the deepest smoothed value, then smoothed >= trough × 1.02
        for at least 1 hour. Buy on bounce.
Exit:   profit_target=0.15, stop_loss=0.10, max_hold=36h, smoothed-based.
"""
import logging
from datetime import datetime
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class DeepDipBounceV1Strategy(Strategy):
    """Deep dip + bounce confirmation entry; fixed-target/stop exit."""

    name = "deep_dip_bounce_v1"

    def __init__(self, params: dict):
        self.params = params
        self.median_window_h: int = params.get("median_window_h", 24)
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.10)
        self.min_dip: float = params.get("min_dip", 0.15)
        self.bounce_pct: float = params.get("bounce_pct", 0.02)
        self.dip_window_h: int = params.get("dip_window_h", 24)
        self.profit_target: float = params.get("profit_target", 0.15)
        self.stop_loss: float = params.get("stop_loss", 0.10)
        self.max_hold_h: int = params.get("max_hold_h", 36)
        self.min_price: int = params.get("min_price", 10000)
        self.max_price: int = params.get("max_price", 80000)
        self.max_positions: int = params.get("max_positions", 8)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 72)
        self.qty_cap: int = params.get("qty_cap", 5)

        hist_len = self.median_window_h + self.dip_window_h + 8
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._trough: dict[int, int] = {}
        self._trough_age: dict[int, int] = defaultdict(int)
        self._created_at: dict[int, datetime] = {}
        self._promo_ids: set[int] = set()
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._first_ts: datetime | None = None

    def set_created_at_map(self, created_at_map: dict):
        self._created_at = created_at_map
        hour_buckets: dict[tuple, list[int]] = defaultdict(list)
        for ea_id, created in created_at_map.items():
            cr = created.replace(tzinfo=None) if created.tzinfo else created
            if cr.weekday() == 4:
                bucket = (cr.year, cr.month, cr.day, cr.hour)
                hour_buckets[bucket].append(ea_id)
        for bucket, ids in hour_buckets.items():
            if len(ids) >= 10:
                self._promo_ids.update(ids)

    @staticmethod
    def _median(values) -> int:
        s = sorted(values)
        return s[len(s) // 2] if s else 0

    def _smooth(self, history: deque) -> int:
        if len(history) < self.smooth_window_h:
            return 0
        return self._median(list(history)[-self.smooth_window_h:])

    def _is_outlier(self, tick: int, smooth: int) -> bool:
        if smooth <= 0:
            return True
        return abs(tick - smooth) / smooth > self.outlier_tol

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

        if self._first_ts is None:
            self._first_ts = ts_clean

        for ea_id, price in ticks:
            self._history[ea_id].append(price)

            holding = portfolio.holdings(ea_id)
            if holding > 0:
                buy_price = self._buy_prices.get(ea_id, price)
                buy_ts = self._buy_ts.get(ea_id, ts_clean)
                bt_clean = buy_ts.replace(tzinfo=None) if buy_ts.tzinfo else buy_ts
                hold_hours = (ts_clean - bt_clean).total_seconds() / 3600

                smooth = self._smooth(self._history[ea_id])

                sell = False
                if hold_hours >= self.max_hold_h:
                    sell = True
                elif smooth > 0 and buy_price > 0:
                    smooth_pct = (smooth - buy_price) / buy_price
                    if smooth_pct >= self.profit_target:
                        sell = True
                    elif smooth_pct <= -self.stop_loss:
                        sell = True

                if sell:
                    signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                    self._buy_prices.pop(ea_id, None)
                    self._buy_ts.pop(ea_id, None)

        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        candidates: list[tuple[int, int, float]] = []
        for ea_id, price in ticks:
            hist = self._history[ea_id]
            if len(hist) < self.median_window_h:
                continue

            smooth = self._smooth(hist)
            if smooth <= 0 or self._is_outlier(price, smooth):
                continue

            med = self._median(list(hist)[-self.median_window_h:])
            if med <= 0:
                continue

            dip = (med - smooth) / med

            trough = self._trough.get(ea_id, 0)

            if dip >= self.min_dip:
                # Currently in deep-dip territory; track the trough
                if trough == 0 or smooth < trough:
                    self._trough[ea_id] = smooth
                    self._trough_age[ea_id] = 0
                else:
                    self._trough_age[ea_id] += 1

                # Check for bounce off the trough
                if trough > 0 and smooth >= trough * (1.0 + self.bounce_pct):
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
                    candidates.append((ea_id, price, dip))
            else:
                # Dip gone; reset trough tracker
                if ea_id in self._trough:
                    self._trough.pop(ea_id, None)
                    self._trough_age.pop(ea_id, None)

        if len(portfolio.positions) >= self.max_positions:
            return signals
        if not candidates:
            return signals

        candidates.sort(key=lambda x: x[2], reverse=True)

        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                p = next((p for eid, p in ticks if eid == s.ea_id), 0)
                sell_rev += (p * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

        open_slots = self.max_positions - len(portfolio.positions)
        buys_made = 0
        for ea_id, price, _ in candidates:
            if buys_made >= open_slots:
                break
            if available <= 0:
                break
            qty = min(self.qty_cap, available // price if price > 0 else 0)
            if qty > 0:
                signals.append(Signal(action="BUY", ea_id=ea_id, quantity=qty))
                self._buy_prices[ea_id] = price
                self._buy_ts[ea_id] = timestamp
                available -= qty * price
                buys_made += 1
                # Clear trough so we don't re-buy immediately
                self._trough.pop(ea_id, None)
                self._trough_age.pop(ea_id, None)

        return signals

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        return [{
            "median_window_h": 24,
            "smooth_window_h": 3,
            "outlier_tol": 0.10,
            "min_dip": 0.15,
            "bounce_pct": 0.02,
            "dip_window_h": 24,
            "profit_target": 0.15,
            "stop_loss": 0.10,
            "max_hold_h": 36,
            "min_price": 10000,
            "max_price": 80000,
            "max_positions": 8,
            "min_age_days": 7,
            "burn_in_h": 72,
            "qty_cap": 5,
        }]
