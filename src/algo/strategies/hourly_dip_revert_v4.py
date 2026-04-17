"""Hourly dip reversion v4 — tight outlier filter + persistence requirement.

Iter 3 with outlier_tol=0.15 still booked many 90-114% margin trades because
the hour-bucketing picks last-of-hour prints, so both buy and sell ticks are
biased toward extremes. Iter 4 enforces:
  - outlier_tol 0.05 (tick within ±5% of 3h smoothed)
  - 2-consecutive-hour persistence: buy signal must hold for two consecutive
    hours (this hour AND last hour both had dip>=threshold against smoothed)
  - same 2-hour persistence for profit_target exits
  - qty cap 3
  - 72h burn-in kept

If even this tight version books 20%+ avg margins, the fault is structural in
the data and we need to go wider-timescale (daily/weekly) rather than hourly.
"""
import logging
from datetime import datetime
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class HourlyDipRevertV4Strategy(Strategy):
    """2-hour-confirmed dip reversion with tight outlier filter."""

    name = "hourly_dip_revert_v4"

    def __init__(self, params: dict):
        self.params = params
        self.median_window_h: int = params.get("median_window_h", 24)
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.05)
        self.dip_pct: float = params.get("dip_pct", 0.05)
        self.profit_target: float = params.get("profit_target", 0.10)
        self.stop_loss: float = params.get("stop_loss", 0.12)
        self.max_hold_h: int = params.get("max_hold_h", 48)
        self.min_price: int = params.get("min_price", 10000)
        self.max_price: int = params.get("max_price", 150000)
        self.max_positions: int = params.get("max_positions", 6)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 72)
        self.qty_cap: int = params.get("qty_cap", 3)
        self.confirm_hours: int = params.get("confirm_hours", 2)

        self._history: dict[int, deque] = defaultdict(
            lambda: deque(maxlen=self.median_window_h + 8)
        )
        self._created_at: dict[int, datetime] = {}
        self._promo_ids: set[int] = set()
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._first_ts: datetime | None = None
        # Track consecutive dip/profit hours per ea_id
        self._dip_streak: dict[int, int] = defaultdict(int)
        self._profit_streak: dict[int, int] = defaultdict(int)

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
        if not s:
            return 0
        return s[len(s) // 2]

    def _smooth(self, history: deque) -> int:
        if len(history) < self.smooth_window_h:
            return 0
        recent = list(history)[-self.smooth_window_h:]
        return self._median(recent)

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
                outlier = self._is_outlier(price, smooth) if smooth > 0 else True

                sell = False
                if hold_hours >= self.max_hold_h:
                    sell = True
                elif not outlier:
                    pct = (smooth - buy_price) / buy_price if buy_price > 0 else 0
                    if pct >= self.profit_target:
                        self._profit_streak[ea_id] += 1
                        if self._profit_streak[ea_id] >= self.confirm_hours:
                            sell = True
                    else:
                        self._profit_streak[ea_id] = 0
                    if not sell:
                        tick_pct = (price - buy_price) / buy_price if buy_price > 0 else 0
                        if tick_pct <= -self.stop_loss:
                            sell = True

                if sell:
                    signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                    self._buy_prices.pop(ea_id, None)
                    self._buy_ts.pop(ea_id, None)
                    self._profit_streak.pop(ea_id, None)

        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        # Update dip streaks + collect candidates
        candidates: list[tuple[int, int, float]] = []
        for ea_id, price in ticks:
            hist = self._history[ea_id]
            if len(hist) < self.median_window_h:
                self._dip_streak[ea_id] = 0
                continue
            smooth = self._smooth(hist)
            if smooth <= 0 or self._is_outlier(price, smooth):
                self._dip_streak[ea_id] = 0
                continue
            med = self._median(hist)
            if med <= 0:
                self._dip_streak[ea_id] = 0
                continue
            dip = (med - smooth) / med
            if dip >= self.dip_pct:
                self._dip_streak[ea_id] += 1
                if self._dip_streak[ea_id] >= self.confirm_hours:
                    # Confirmed dip — add as candidate
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
                self._dip_streak[ea_id] = 0

        if len(portfolio.positions) >= self.max_positions:
            return signals
        if not candidates:
            return signals

        candidates.sort(key=lambda x: x[2], reverse=True)

        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                price = next((p for eid, p in ticks if eid == s.ea_id), 0)
                sell_rev += (price * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

        open_slots = self.max_positions - len(portfolio.positions)
        buys_made = 0
        for ea_id, price, dip in candidates:
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
                # Reset streak after buying to avoid re-triggers
                self._dip_streak[ea_id] = 0

        return signals

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        """Winning config from autonomous research: 31.9%/43.2% weekly PnL,
        7-weekday coverage, -0.25 correlation with promo_dip_buy."""
        return [{
            "median_window_h": 24,
            "smooth_window_h": 3,
            "outlier_tol": 0.03,
            "dip_pct": 0.08,
            "profit_target": 0.10,
            "stop_loss": 0.12,
            "max_hold_h": 48,
            "min_price": 10000,
            "max_price": 150000,
            "max_positions": 6,
            "min_age_days": 7,
            "burn_in_h": 72,
            "qty_cap": 3,
            "confirm_hours": 3,
        }]
