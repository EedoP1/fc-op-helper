"""Hourly dip reversion v3 — realistic execution filter.

Iter 2 hit 34M PnL but it was dominated by outlier BINs. Investigation of one
+588k trade showed the hour bucket captured a single 14k listing at 05:58 on
2026-04-01 while the sustained market was 45-62k. Backtester treats the 14k
tick as infinite-liquidity, so strategies that latch onto it book fake gains.

Iter 3 enforces realistic execution:
  1. Outlier rejection: skip signal if |current_tick - 3h_median| / 3h_median > 15%.
     (In our motivating trade: the 14k tick was 71% below the 3h median — rejected.)
  2. Quantity cap: max 3 cards per buy (typical real listing depth at lowest BIN).
  3. Sell logic also outlier-rejected (no fake spike-up exits).

If iter 3 still hits 25%/week with realistic constraints, it's a candidate.
Otherwise we learn that non-promo dip reversion doesn't pass the bar with the
current data granularity.
"""
import logging
from datetime import datetime
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class HourlyDipRevertV3Strategy(Strategy):
    """Outlier-filtered non-promo dip buyer with realistic quantity cap."""

    name = "hourly_dip_revert_v3"

    def __init__(self, params: dict):
        self.params = params
        self.median_window_h: int = params.get("median_window_h", 24)
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.15)
        self.dip_pct: float = params.get("dip_pct", 0.05)
        self.profit_target: float = params.get("profit_target", 0.10)
        self.stop_loss: float = params.get("stop_loss", 0.12)
        self.max_hold_h: int = params.get("max_hold_h", 36)
        self.min_price: int = params.get("min_price", 10000)
        self.max_price: int = params.get("max_price", 150000)
        self.max_positions: int = params.get("max_positions", 6)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 72)
        self.qty_cap: int = params.get("qty_cap", 3)

        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=self.median_window_h + 4))
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
                # Force-exit on max hold regardless of outlier (we must exit)
                if hold_hours >= self.max_hold_h:
                    sell = True
                elif not outlier:
                    pct = (price - buy_price) / buy_price if buy_price > 0 else 0
                    if pct >= self.profit_target:
                        sell = True
                    elif pct <= -self.stop_loss:
                        sell = True
                    else:
                        med = self._median(self._history[ea_id])
                        if price >= med and price >= int(buy_price * 1.07):
                            sell = True

                if sell:
                    signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                    self._buy_prices.pop(ea_id, None)
                    self._buy_ts.pop(ea_id, None)

        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        if len(portfolio.positions) >= self.max_positions:
            return signals

        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                price = next((p for eid, p in ticks if eid == s.ea_id), 0)
                sell_rev += (price * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

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
            if len(hist) < self.median_window_h:
                continue
            smooth = self._smooth(hist)
            if smooth <= 0:
                continue
            # Reject outlier-deviation tick — we can't trust this print
            if self._is_outlier(price, smooth):
                continue
            med = self._median(hist)
            if med <= 0:
                continue
            # Use SMOOTHED price vs median for signal (ignore tick spikes)
            dip = (med - smooth) / med
            if dip >= self.dip_pct:
                candidates.append((ea_id, price, dip))

        if not candidates:
            return signals

        candidates.sort(key=lambda x: x[2], reverse=True)

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

        return signals

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        base = {
            "median_window_h": 24,
            "smooth_window_h": 3,
            "outlier_tol": 0.15,
            "profit_target": 0.10,
            "stop_loss": 0.12,
            "max_hold_h": 36,
            "min_price": 10000,
            "max_price": 150000,
            "max_positions": 6,
            "min_age_days": 7,
            "burn_in_h": 72,
        }
        combos = []
        for dip_pct in [0.03, 0.05, 0.08]:
            for qty_cap in [3, 5, 10]:
                for profit_target in [0.08, 0.12]:
                    combos.append({
                        **base,
                        "dip_pct": dip_pct,
                        "qty_cap": qty_cap,
                        "profit_target": profit_target,
                    })
        return combos
