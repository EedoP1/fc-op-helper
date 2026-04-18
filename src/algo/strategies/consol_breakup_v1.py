"""Consolidation breakup v1 — NO-STOP cycle-gated mid-tier buyer.

All prior iters (cycle_bottom v1/v2, oscillator, cohort_chase) failed
because the smoothed -10/-12% stop_loss fires at hourly_min ≈ -15-18%
real loss. With 30-50% win rates, the asymmetric loss size dominates.

Hypothesis: REMOVE the stop entirely. Use a 24h consolidation pattern
(price range ≤ 4% over last 24h) as the entry — this acts as a SOFT floor:
the card has already proven it can hold this price level for a day.
Pair with a cycle-rally gate (global G must be in recovery phase) to
avoid Thu/Fri/Sat dump entries.

Risk control by SIZING + DIVERSIFICATION instead of stops:
  - qty_cap=4, max_positions=10 → each position ≤ 12% of budget
  - Long max-hold=96h, but most exits expected on profit_target inside
    the data window (post-burn-in we have ~14d of trading)

This is STRUCTURALLY DIFFERENT from prior failures because:
  - No stop_loss at all (consolidation = soft floor)
  - Mid-tier price band ($11-35k) — wider than floor_buy's $10-13k
  - Cycle-derived entry gate (not weekday hardcoding)
  - Cohort detection NOT used (we trust consolidation alone)
"""
import logging
from datetime import datetime
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class ConsolBreakupV1Strategy(Strategy):
    name = "consol_breakup_v1"

    def __init__(self, params: dict):
        self.params = params
        # Cycle gate
        self.g_short_h: int = params.get("g_short_h", 12)
        self.g_long_h: int = params.get("g_long_h", 36)
        self.g_rally_min: float = params.get("g_rally_min", 0.0)
        # Consolidation: range over consol_h must be <= consol_max
        self.consol_h: int = params.get("consol_h", 24)
        self.consol_max: float = params.get("consol_max", 0.04)
        # Smoothing
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.05)
        # Profit / hold (NO stop)
        self.profit_target: float = params.get("profit_target", 0.12)
        self.max_hold_h: int = params.get("max_hold_h", 96)
        # Universe gates
        self.min_price: int = params.get("min_price", 11000)
        self.max_price: int = params.get("max_price", 35000)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 96)
        # Sizing
        self.qty_cap: int = params.get("qty_cap", 4)
        self.max_positions: int = params.get("max_positions", 10)

        hist_len = max(168, self.consol_h + self.g_long_h) + 8
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

        # Exits — only profit_target or max_hold (no stop)
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
                pct = (smooth - buy_price) / buy_price
                if pct >= self.profit_target:
                    sell = True
            if not sell and hold_hours >= self.max_hold_h:
                sell = True

            if sell:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._buy_ts.pop(ea_id, None)

        # Burn-in
        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        # Cycle gate
        if self._g_delta(self.g_short_h) < self.g_rally_min:
            return signals
        if self._g_delta(self.g_long_h) < self.g_rally_min:
            return signals

        # Entry: consolidation + price band
        candidates: list[tuple[int, int, int, float]] = []
        for ea_id, price in ticks:
            hist = self._history[ea_id]
            if len(hist) < self.consol_h + self.smooth_window_h:
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

            window = list(hist)[-self.consol_h:]
            w_lo, w_hi = min(window), max(window)
            w_med = self._median(window)
            if w_lo <= 0 or w_med <= 0:
                continue
            rng = (w_hi - w_lo) / w_med
            if rng > self.consol_max:
                continue

            candidates.append((ea_id, price, smooth, rng))

        if not candidates or len(portfolio.positions) >= self.max_positions:
            return signals

        # Sort by tightest consolidation first, then by smoothed asc
        candidates.sort(key=lambda x: (x[3], x[2]))

        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                p = next((pp for eid, pp in ticks if eid == s.ea_id), 0)
                sell_rev += (p * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

        open_slots = self.max_positions - len(portfolio.positions)
        buys_made = 0
        for ea_id, price, _, _ in candidates:
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
            "consol_h": 24,
            "consol_max": 0.04,
            "smooth_window_h": 3,
            "outlier_tol": 0.05,
            "profit_target": 0.12,
            "max_hold_h": 96,
            "min_price": 11000,
            "max_price": 35000,
            "min_age_days": 7,
            "burn_in_h": 96,
            "qty_cap": 4,
            "max_positions": 10,
        }]
