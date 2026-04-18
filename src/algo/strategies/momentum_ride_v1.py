"""Momentum ride v1 — buy sustained upward trend on liquid cards.

v12/v19/v20 bet on reversion (buy the dip, wait for mean). On illiquid cards
that worked because scanner artifacts created fake "reversion" payoffs. On
the --min-sph 5 filtered universe that edge evaporates (−$629k).

Hypothesis: liquid cards with 6h median climbing ≥4% over the prior 6h
window reflect genuine demand flowing in. Ride the trend, exit on fixed
profit target or stop; DON'T trail the peak (pessimistic loader exits at
hourly min and erases locked gains — see v7/v16 failures).

Entry:  smoothed_6h median >= smoothed_6h_prior × 1.04, confirmed 2 hours.
Exit:   profit_target=0.10 on smoothed vs buy, stop_loss=0.08, max_hold=24h.
"""
import logging
from datetime import datetime
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class MomentumRideV1Strategy(Strategy):
    """Buy uptrending liquid cards; exit on fixed target/stop/max-hold."""

    name = "momentum_ride_v1"

    def __init__(self, params: dict):
        self.params = params
        self.recent_window_h: int = params.get("recent_window_h", 6)
        self.prior_window_h: int = params.get("prior_window_h", 6)
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.08)
        self.momentum_pct: float = params.get("momentum_pct", 0.04)
        self.confirm_hours: int = params.get("confirm_hours", 2)
        self.profit_target: float = params.get("profit_target", 0.10)
        self.stop_loss: float = params.get("stop_loss", 0.08)
        self.max_hold_h: int = params.get("max_hold_h", 24)
        self.min_price: int = params.get("min_price", 10000)
        self.max_price: int = params.get("max_price", 80000)
        self.max_positions: int = params.get("max_positions", 8)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 72)
        self.qty_cap: int = params.get("qty_cap", 5)

        hist_len = self.recent_window_h + self.prior_window_h + 4
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._mom_streak: dict[int, int] = defaultdict(int)
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
            need = self.recent_window_h + self.prior_window_h
            if len(hist) < need:
                self._mom_streak[ea_id] = 0
                continue

            smooth = self._smooth(hist)
            if smooth <= 0 or self._is_outlier(price, smooth):
                self._mom_streak[ea_id] = 0
                continue

            hist_list = list(hist)
            recent = hist_list[-self.recent_window_h:]
            prior = hist_list[-(self.recent_window_h + self.prior_window_h):-self.recent_window_h]
            if not recent or not prior:
                self._mom_streak[ea_id] = 0
                continue

            recent_med = self._median(recent)
            prior_med = self._median(prior)
            if prior_med <= 0:
                self._mom_streak[ea_id] = 0
                continue

            momentum = (recent_med - prior_med) / prior_med
            if momentum >= self.momentum_pct:
                self._mom_streak[ea_id] += 1
                if self._mom_streak[ea_id] >= self.confirm_hours:
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
                    candidates.append((ea_id, price, momentum))
            else:
                self._mom_streak[ea_id] = 0

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
                self._mom_streak[ea_id] = 0

        return signals

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        return [{
            "recent_window_h": 6,
            "prior_window_h": 6,
            "smooth_window_h": 3,
            "outlier_tol": 0.08,
            "momentum_pct": 0.04,
            "confirm_hours": 2,
            "profit_target": 0.10,
            "stop_loss": 0.08,
            "max_hold_h": 24,
            "min_price": 10000,
            "max_price": 80000,
            "max_positions": 8,
            "min_age_days": 7,
            "burn_in_h": 72,
            "qty_cap": 5,
        }]
