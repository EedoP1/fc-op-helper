"""monday_floor_v1 — Monday/Tue morning buy near rolling 27d floor.

Iter 70 angle: Data-first, time-causal pre-buy signal.

EDA on .planning/profit_opportunities.json (1,850 pessimistic-mode opps,
W13-W17 covering 27d):
  - 33% of all opportunities BUY on Monday (DOW=0; 619/1850).
    Tue (374) and Fri (249) are the next biggest — Mon+Tue+Fri = 67%.
  - 44.5% of opps have buy_price <= 1.05x that card's window minimum
    (824/1850), median ROI 32.8% net.
  - 309 opps (16.7%) hold 96-168h with median ROI 51.6% — long-tail
    floor recoveries.

Hypothesis: at Monday/Tuesday morning, scan for cards trading within 5%
of their trailing-27d minimum (>= 7d of history), buy a small basket,
hold to profit target or 168h max. Pre-buy detectable: only uses
trailing-window min + day-of-week + tick price.

Key differences from killed strategies:
  - drawdown_reversion v1-v4 used median(last-N) vs current — that's
    median-vs-max drag (~9.6% break-even). Here we use ABSOLUTE rolling
    min, not smoothed mid-fall midpoint. Entry is at-or-near actual floor.
  - Calendar-only (weekend_bottom) ignored price level. Here calendar
    only OPENS the window — price-vs-floor is the actual gate.
  - spike_crash/stab_bottom required hourly-shape gates. Here we don't
    care about shape; just that current price is within tol of trailing min.

Scope: read-only of .planning/profit_opportunities.json was used to pick
the angle. Strategy uses no look-ahead — only data observable at tick.
"""
import logging
from datetime import datetime, timedelta
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class MondayFloorV1Strategy(Strategy):
    name = "monday_floor_v1"

    def __init__(self, params: dict):
        self.params = params
        # Calendar window (UTC)
        self.buy_dows: tuple[int, ...] = tuple(params.get("buy_dows", (0, 1)))  # Mon, Tue
        self.buy_hour_min: int = params.get("buy_hour_min", 6)
        self.buy_hour_max: int = params.get("buy_hour_max", 14)
        # Floor proximity
        self.floor_window_h: int = params.get("floor_window_h", 168)  # 7d trailing
        self.floor_tol: float = params.get("floor_tol", 0.05)  # within 5% of min
        # Sanity: floor must be a real floor, not a free-fall midpoint —
        # require some minimum age of the historical low
        self.min_floor_history_h: int = params.get("min_floor_history_h", 96)
        # Exits
        self.profit_target: float = params.get("profit_target", 0.20)
        self.hard_stop: float = params.get("hard_stop", 0.15)
        self.max_hold_h: int = params.get("max_hold_h", 168)
        # Universe
        self.min_price: int = params.get("min_price", 11000)
        self.max_price: int = params.get("max_price", 100000)
        self.min_age_days: int = params.get("min_age_days", 14)
        self.burn_in_h: int = params.get("burn_in_h", 168)
        # Sizing
        self.qty_cap: int = params.get("qty_cap", 8)
        self.max_positions: int = params.get("max_positions", 8)
        self.basket_size: int = params.get("basket_size", 4)
        # Cooldown to prevent firing every hour during the window
        self.fire_cooldown_h: int = params.get("fire_cooldown_h", 96)
        # Per-card cooldown after a stop
        self.stop_cooldown_h: int = params.get("stop_cooldown_h", 168)
        # Smoothing
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.08)

        hist_len = max(self.floor_window_h, 168) + 8
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._created_at: dict[int, datetime] = {}
        self._promo_ids: set[int] = set()
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._first_ts: datetime | None = None
        self._stopped_until: dict[int, datetime] = {}
        self._last_fire_ts: datetime | None = None

    def set_created_at_map(self, created_at_map: dict):
        self._created_at = created_at_map
        # Detect Friday promo bursts (>= 10 cards in same hour) to exclude
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

        # Update history
        for ea_id, price in ticks:
            self._history[ea_id].append(price)

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
            stopped = False
            if buy_price > 0 and price <= buy_price * (1.0 - self.hard_stop):
                sell = True
                stopped = True
            elif hold_hours >= self.max_hold_h:
                sell = True
            elif smooth > 0 and buy_price > 0:
                pct = (smooth - buy_price) / buy_price
                if pct >= self.profit_target:
                    sell = True
            if sell:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._buy_ts.pop(ea_id, None)
                if stopped:
                    self._stopped_until[ea_id] = ts_clean + timedelta(hours=self.stop_cooldown_h)

        # Burn-in
        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        # Calendar gate
        if ts_clean.weekday() not in self.buy_dows:
            return signals
        if not (self.buy_hour_min <= ts_clean.hour <= self.buy_hour_max):
            return signals

        # Fire cooldown — only one buy event per ~4 days
        if self._last_fire_ts:
            since = (ts_clean - self._last_fire_ts).total_seconds() / 3600
            if since < self.fire_cooldown_h:
                return signals

        # Build candidates: cards within floor_tol of trailing-window min,
        # with a real-looking floor (window covers >= min_floor_history_h)
        candidates: list[tuple[int, int, int, float]] = []  # (ea_id, price, smooth, pct_above_floor)
        for ea_id, price in ticks:
            hist = self._history[ea_id]
            if len(hist) < self.min_floor_history_h:
                continue
            smooth = self._smooth(hist)
            if smooth <= 0 or self._is_outlier(price, smooth):
                continue
            if not (self.min_price <= price <= self.max_price):
                continue

            cooldown_end = self._stopped_until.get(ea_id)
            if cooldown_end and ts_clean < cooldown_end:
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

            window = list(hist)[-self.floor_window_h:]
            if not window:
                continue
            wmin = min(window)
            if wmin <= 0:
                continue
            pct_above_floor = (price - wmin) / wmin
            if pct_above_floor > self.floor_tol:
                continue

            # Avoid mid-fall: require that the window min is NOT in the very
            # last few hours (i.e. floor was seen at least min_floor_age_h ago)
            min_idx = window.index(wmin)
            min_age_h = len(window) - 1 - min_idx
            if min_age_h < 6:
                # The "floor" was just made — could still be falling
                continue

            candidates.append((ea_id, price, smooth, pct_above_floor))

        if not candidates:
            return signals
        if len(portfolio.positions) >= self.max_positions:
            return signals

        # Sort by floor proximity (tightest first), then cheapest
        candidates.sort(key=lambda x: (x[3], x[1]))
        candidates = candidates[:self.basket_size]

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

        if buys_made > 0:
            self._last_fire_ts = ts_clean

        return signals

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        return [{
            "buy_dows": (0, 1),
            "buy_hour_min": 6,
            "buy_hour_max": 14,
            "floor_window_h": 168,
            "floor_tol": 0.05,
            "min_floor_history_h": 96,
            "profit_target": 0.20,
            "hard_stop": 0.15,
            "max_hold_h": 168,
            "min_price": 11000,
            "max_price": 100000,
            "min_age_days": 14,
            "burn_in_h": 168,
            "qty_cap": 8,
            "max_positions": 8,
            "basket_size": 4,
            "fire_cooldown_h": 96,
            "stop_cooldown_h": 168,
            "smooth_window_h": 3,
            "outlier_tol": 0.08,
        }]
