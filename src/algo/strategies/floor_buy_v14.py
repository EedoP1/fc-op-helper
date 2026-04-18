"""Floor buy v14 — v13 + volatility-based qty downgrade + wider cooldown.

v13 W15 loss mostly came from one card (ea=252371) bought at qty_large=12
and crashed. That card had been sold profitably in W14 (qty=12, +$35k),
then re-bought in W15 (cooldown only covers stop-sales, not profit-sales)
and crashed (-$27.6k). Same card, same qty_large, opposite outcomes.

Also ea=239085 qty=12 lost -$7k (hit smoothed stop; cooldown would catch
its next entry but that's after W15 window).

v14 adds two refinements:
  1. POST-SALE COOLDOWN (72h, ALL sales): Don't re-buy any card within 72h
     of its last sale, regardless of sell reason. Prevents over-trading the
     same volatile card.
  2. VOLATILITY-BASED qty downgrade: If 72h price range > 15% (max/min > 1.15),
     force qty_small even if 72h dwell filter passes. Truly-stable floor
     cards have range <15% over 72h.

Volatile cards (like ea=252371) have big swings. qty_small caps loss
magnitude when these cards crash, without sacrificing the edge on calm
floor cards.
"""
import logging
from datetime import datetime, timedelta
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class FloorBuyV14Strategy(Strategy):
    name = "floor_buy_v14"

    def __init__(self, params: dict):
        self.params = params
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.08)
        self.floor_ceiling: int = params.get("floor_ceiling", 13000)
        self.floor_stable: int = params.get("floor_stable", 14500)
        self.recent_h_min: int = params.get("recent_h_min", 24)
        self.recent_h_large: int = params.get("recent_h_large", 72)
        self.week_window_h: int = params.get("week_window_h", 168)
        self.week_max_ceiling: int = params.get("week_max_ceiling", 18000)
        self.profit_target: float = params.get("profit_target", 0.30)
        self.stop_loss: float = params.get("stop_loss", 0.10)
        self.hard_stop: float = params.get("hard_stop", 0.15)
        self.sale_cooldown_h: int = params.get("sale_cooldown_h", 72)
        self.vol_range_threshold: float = params.get("vol_range_threshold", 0.15)
        self.max_hold_h: int = params.get("max_hold_h", 96)
        self.min_price: int = params.get("min_price", 10000)
        self.max_positions: int = params.get("max_positions", 12)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 72)
        self.qty_small: int = params.get("qty_small", 4)
        self.qty_large: int = params.get("qty_large", 12)

        hist_len = max(self.week_window_h, self.recent_h_large) + 8
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._created_at: dict[int, datetime] = {}
        self._promo_ids: set[int] = set()
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._first_ts: datetime | None = None
        self._last_sale_ts: dict[int, datetime] = {}

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
                if buy_price > 0 and price <= buy_price * (1.0 - self.hard_stop):
                    sell = True
                elif hold_hours >= self.max_hold_h:
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
                    self._last_sale_ts[ea_id] = ts_clean

        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        candidates: list[tuple[int, int, int, int]] = []
        for ea_id, price in ticks:
            hist = self._history[ea_id]
            if len(hist) < self.recent_h_min:
                continue
            smooth = self._smooth(hist)
            if smooth <= 0 or self._is_outlier(price, smooth):
                continue

            if smooth > self.floor_ceiling:
                continue
            if not (self.min_price <= price <= self.floor_ceiling):
                continue

            # Post-sale cooldown (any sale, not just stops)
            last_sale = self._last_sale_ts.get(ea_id)
            if last_sale is not None:
                elapsed = (ts_clean - last_sale).total_seconds() / 3600
                if elapsed < self.sale_cooldown_h:
                    continue

            recent_min = list(hist)[-self.recent_h_min:]
            if any(p > self.floor_stable for p in recent_min):
                continue
            if min(recent_min) < self.min_price * 0.9:
                continue

            if len(hist) >= self.week_window_h:
                week = list(hist)[-self.week_window_h:]
                if max(week) > self.week_max_ceiling:
                    continue

            if portfolio.holdings(ea_id) > 0:
                continue
            if ea_id in self._promo_ids:
                continue
            created = self._created_at.get(ea_id)
            if created:
                cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
                age_days = (ts_clean - cr_clean).days
                if age_days < self.min_age_days:
                    continue

            qty_cap = self.qty_small
            if len(hist) >= self.recent_h_large:
                recent_large = list(hist)[-self.recent_h_large:]
                if all(p <= self.floor_stable for p in recent_large) \
                   and min(recent_large) >= self.min_price * 0.9:
                    # Volatility guard: range > threshold → downgrade
                    rng = max(recent_large) / max(1, min(recent_large)) - 1.0
                    if rng <= self.vol_range_threshold:
                        qty_cap = self.qty_large

            candidates.append((ea_id, price, smooth, qty_cap))

        if len(portfolio.positions) >= self.max_positions:
            return signals
        if not candidates:
            return signals

        candidates.sort(key=lambda x: (-x[3], x[2]))

        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                p = next((p for eid, p in ticks if eid == s.ea_id), 0)
                sell_rev += (p * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

        open_slots = self.max_positions - len(portfolio.positions)
        buys_made = 0
        for ea_id, price, _, qty_cap in candidates:
            if buys_made >= open_slots:
                break
            if available <= 0:
                break
            qty = min(qty_cap, available // price if price > 0 else 0)
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
        return [{
            "smooth_window_h": 3,
            "outlier_tol": 0.08,
            "floor_ceiling": 13000,
            "floor_stable": 14500,
            "recent_h_min": 24,
            "recent_h_large": 72,
            "week_window_h": 168,
            "week_max_ceiling": 18000,
            "profit_target": 0.30,
            "stop_loss": 0.10,
            "hard_stop": 0.15,
            "sale_cooldown_h": 72,
            "vol_range_threshold": 0.15,
            "max_hold_h": 96,
            "min_price": 10000,
            "max_positions": 12,
            "min_age_days": 7,
            "burn_in_h": 72,
            "qty_small": 4,
            "qty_large": 12,
        }]
