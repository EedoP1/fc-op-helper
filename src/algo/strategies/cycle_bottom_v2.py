"""Cycle-bottom buyer v2 — v1 + asymmetric R/R + falling-knife reject.

v1 traded 98 times for -$292k / 33% win. Wins were small (+$2-12k), losses
large (-$20-38k). Failure modes:
  1. profit_target=0.06 smoothed → fills at hourly_min ~3% below smoothed →
     net wins of just +$2-3k after spread+tax.
  2. stop_loss=0.10 smoothed → fills at hourly_min ~3% below smoothed → real
     losses of -13% × qty=6 × $20k ≈ -$23k per stop.
  3. Many stops fired SAME DAY: bought a falling knife near 168h low while
     it kept falling.

v2 changes:
  - profit_target 0.06 → 0.12 (clears spread+tax with margin to spare)
  - stop_loss 0.10 → 0.15 (slightly looser; fewer false-signal stops)
  - require 12h smoothed delta >= -0.02 on the card (NOT actively falling)
  - require global g36 > 0 too (sustained rally, not blip)
  - tighten floor_prox 0.05 → 0.03 (only true local lows)
  - max_positions 8 → 6 (more conviction per position)
"""
import logging
from datetime import datetime
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class CycleBottomV2Strategy(Strategy):
    name = "cycle_bottom_v2"

    def __init__(self, params: dict):
        self.params = params
        self.g_short_h: int = params.get("g_short_h", 12)
        self.g_long_h: int = params.get("g_long_h", 36)
        self.g_rally_min: float = params.get("g_rally_min", 0.0)
        self.g_peak_drop: float = params.get("g_peak_drop", -0.005)
        self.floor_window_h: int = params.get("floor_window_h", 168)
        self.floor_prox: float = params.get("floor_prox", 0.03)
        self.below_med_only: bool = params.get("below_med_only", True)
        self.card_drop_max: float = params.get("card_drop_max", -0.02)
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.05)
        self.profit_target: float = params.get("profit_target", 0.12)
        self.stop_loss: float = params.get("stop_loss", 0.15)
        self.max_hold_h: int = params.get("max_hold_h", 60)
        self.min_hold_for_cycle_exit_h: int = params.get("min_hold_for_cycle_exit_h", 18)
        self.min_price: int = params.get("min_price", 11000)
        self.max_price: int = params.get("max_price", 80000)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 72)
        self.qty_cap: int = params.get("qty_cap", 6)
        self.max_positions: int = params.get("max_positions", 6)

        hist_len = self.floor_window_h + 8
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._g_history: deque = deque(maxlen=self.g_long_h * 4 + 8)
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

    def _g_delta(self, lookback_h: int) -> float:
        if len(self._g_history) < lookback_h + 1:
            return 0.0
        now = self._g_history[-1][1]
        past = self._g_history[-1 - lookback_h][1]
        if past <= 0:
            return 0.0
        return (now - past) / past

    def _card_12h_delta(self, hist: deque) -> float:
        if len(hist) < self.g_short_h + self.smooth_window_h:
            return 0.0
        recent = self._median(list(hist)[-self.smooth_window_h:])
        past_window = list(hist)[-self.g_short_h - self.smooth_window_h:-self.g_short_h]
        if not past_window:
            return 0.0
        past = self._median(past_window)
        if past <= 0:
            return 0.0
        return (recent - past) / past

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

        if self._first_ts is None:
            self._first_ts = ts_clean

        for ea_id, price in ticks:
            self._history[ea_id].append(price)

        if ticks:
            prices = sorted(p for _, p in ticks)
            g_now = prices[len(prices) // 2]
            self._g_history.append((ts_clean, g_now))

        # Exits
        for ea_id, price in ticks:
            holding = portfolio.holdings(ea_id)
            if holding <= 0:
                continue
            buy_price = self._buy_prices.get(ea_id, price)
            buy_ts = self._buy_ts.get(ea_id, ts_clean)
            bt_clean = buy_ts.replace(tzinfo=None) if buy_ts.tzinfo else buy_ts
            hold_hours = (ts_clean - bt_clean).total_seconds() / 3600

            smooth = self._smooth(self._history[ea_id])
            sell = False
            if smooth > 0 and buy_price > 0:
                smooth_pct = (smooth - buy_price) / buy_price
                if smooth_pct >= self.profit_target:
                    sell = True
                elif smooth_pct <= -self.stop_loss:
                    sell = True
            if not sell and hold_hours >= self.max_hold_h:
                sell = True
            if (not sell
                and hold_hours >= self.min_hold_for_cycle_exit_h
                and self._g_delta(self.g_short_h) <= self.g_peak_drop):
                sell = True

            if sell:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._buy_ts.pop(ea_id, None)

        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        # Cycle gate: BOTH g_short and g_long must be in rally
        g_s = self._g_delta(self.g_short_h)
        g_l = self._g_delta(self.g_long_h)
        if g_s < self.g_rally_min or g_l < self.g_rally_min:
            return signals

        candidates: list[tuple[int, int, int]] = []
        for ea_id, price in ticks:
            hist = self._history[ea_id]
            if len(hist) < self.floor_window_h:
                continue
            smooth = self._smooth(hist)
            if smooth <= 0 or self._is_outlier(price, smooth):
                continue
            if not (self.min_price <= price <= self.max_price):
                continue
            if portfolio.holdings(ea_id) > 0:
                continue
            if ea_id in self._promo_ids:
                continue

            created = self._created_at.get(ea_id)
            if created:
                cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
                if (ts_clean - cr_clean).days < self.min_age_days:
                    continue

            window = list(hist)[-self.floor_window_h:]
            w_low = min(window)
            w_med = self._median(window)
            if w_low <= 0 or w_med <= 0:
                continue
            if smooth > w_low * (1.0 + self.floor_prox):
                continue
            if self.below_med_only and smooth >= w_med:
                continue
            # Falling-knife reject: 12h smoothed delta must be >= card_drop_max
            cd = self._card_12h_delta(hist)
            if cd < self.card_drop_max:
                continue

            candidates.append((ea_id, price, smooth))

        if not candidates or len(portfolio.positions) >= self.max_positions:
            return signals

        candidates.sort(key=lambda x: x[2])

        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                p = next((pp for eid, pp in ticks if eid == s.ea_id), 0)
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

        return signals

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        return [{
            "g_short_h": 12,
            "g_long_h": 36,
            "g_rally_min": 0.0,
            "g_peak_drop": -0.005,
            "floor_window_h": 168,
            "floor_prox": 0.03,
            "below_med_only": True,
            "card_drop_max": -0.02,
            "smooth_window_h": 3,
            "outlier_tol": 0.05,
            "profit_target": 0.12,
            "stop_loss": 0.15,
            "max_hold_h": 60,
            "min_hold_for_cycle_exit_h": 18,
            "min_price": 11000,
            "max_price": 80000,
            "min_age_days": 7,
            "burn_in_h": 72,
            "qty_cap": 6,
            "max_positions": 6,
        }]
