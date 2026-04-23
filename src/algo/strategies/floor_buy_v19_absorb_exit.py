"""Floor buy v19 + absorb-exit overlay — iter 59.

Uses iter 53's bearish-signal (listings drop >=30% in 6h while price stays
within 5% of 6h median) as a SELL-side overlay on v19 positions. The
overlay preserves v19's long-hold option value EXCEPT when a statistically
reliable downturn is predicted (77.5% sell-side accuracy per iter 53).

Only functional change vs v19: one extra sell trigger fires BEFORE v19's
existing triggers (hard_stop, max_hold, smooth profit/stop). On trigger
we force-sell, clear buy bookkeeping, and arm stop_cooldown_h to avoid
an immediate rebuy.
"""
import logging
from datetime import datetime, timedelta
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class FloorBuyV19AbsorbExitStrategy(Strategy):
    name = "floor_buy_v19_absorb_exit"

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
        self.week_range_max: float = params.get("week_range_max", 0.25)
        self.profit_target: float = params.get("profit_target", 0.50)
        self.stop_loss: float = params.get("stop_loss", 0.10)
        self.hard_stop: float = params.get("hard_stop", 0.15)
        self.stop_cooldown_h: int = params.get("stop_cooldown_h", 48)
        self.vol_range_tight: float = params.get("vol_range_tight", 0.10)
        self.vol_range_loose: float = params.get("vol_range_loose", 0.20)
        self.max_hold_h: int = params.get("max_hold_h", 240)
        self.min_price: int = params.get("min_price", 10000)
        self.max_positions: int = params.get("max_positions", 8)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 72)
        self.qty_small: int = params.get("qty_small", 10)
        self.qty_medium: int = params.get("qty_medium", 18)
        self.qty_large: int = params.get("qty_large", 25)

        # Absorb-exit overlay knobs (from iter 53)
        self.absorb_lookback_h: int = params.get("absorb_lookback_h", 6)
        self.absorb_depletion: float = params.get("absorb_depletion", 0.30)
        self.absorb_min_listings: int = params.get("absorb_min_listings", 20)
        self.absorb_price_range_max: float = params.get("absorb_price_range_max", 0.05)
        self.absorb_cooldown_h: int = params.get("absorb_cooldown_h", 24)

        hist_len = max(self.week_window_h, self.recent_h_large) + 8
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._created_at: dict[int, datetime] = {}
        self._promo_ids: set[int] = set()
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._first_ts: datetime | None = None
        self._stopped_until: dict[int, datetime] = {}
        self._listings: dict[tuple[int, datetime], int] = {}

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

    def set_listing_counts(self, listing_counts: dict):
        # Normalize keys: strip tzinfo, round to hour
        normalized: dict[tuple[int, datetime], int] = {}
        for (ea_id, ts), count in listing_counts.items():
            ts_clean = ts.replace(tzinfo=None) if ts.tzinfo else ts
            ts_hr = ts_clean.replace(minute=0, second=0, microsecond=0)
            normalized[(ea_id, ts_hr)] = count
        self._listings = normalized

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

    def _bearish_signal(self, ea_id: int, ts_clean: datetime, current_price: int) -> bool:
        """Iter-53 bearish overlay: listings drop >=30% in 6h while price stable."""
        hour_ts = ts_clean.replace(minute=0, second=0, microsecond=0)
        hour_6h_ago = hour_ts - timedelta(hours=self.absorb_lookback_h)
        lc_now = self._listings.get((ea_id, hour_ts))
        lc_6h = self._listings.get((ea_id, hour_6h_ago))
        if lc_now is None or lc_6h is None:
            return False
        if lc_6h < self.absorb_min_listings:
            return False
        if (lc_6h - lc_now) / lc_6h < self.absorb_depletion:
            return False
        hist = self._history.get(ea_id)
        if not hist or len(hist) < self.absorb_lookback_h:
            return False
        last_6 = list(hist)[-self.absorb_lookback_h:]
        hi = max(last_6)
        lo = min(last_6)
        med = self._median(last_6)
        if med <= 0:
            return False
        if (hi - lo) / med >= self.absorb_price_range_max:
            return False
        return True

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
                # NEW: Absorb-exit overlay runs BEFORE v19's triggers
                if self._bearish_signal(ea_id, ts_clean, price):
                    signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                    self._buy_prices.pop(ea_id, None)
                    self._buy_ts.pop(ea_id, None)
                    self._stopped_until[ea_id] = ts_clean + timedelta(hours=self.absorb_cooldown_h)
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
                    smooth_pct = (smooth - buy_price) / buy_price
                    if smooth_pct >= self.profit_target:
                        sell = True
                    elif smooth_pct <= -self.stop_loss:
                        sell = True
                        stopped = True

                if sell:
                    signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                    self._buy_prices.pop(ea_id, None)
                    self._buy_ts.pop(ea_id, None)
                    if stopped:
                        self._stopped_until[ea_id] = ts_clean + timedelta(hours=self.stop_cooldown_h)

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

            cooldown_end = self._stopped_until.get(ea_id)
            if cooldown_end and ts_clean < cooldown_end:
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
                wk_rng = max(week) / max(1, min(week)) - 1.0
                if wk_rng > self.week_range_max:
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
                    rng = max(recent_large) / max(1, min(recent_large)) - 1.0
                    if rng <= self.vol_range_tight:
                        qty_cap = self.qty_large
                    elif rng <= self.vol_range_loose:
                        qty_cap = self.qty_medium

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
            "week_range_max": 0.25,
            "profit_target": 0.50,
            "stop_loss": 0.10,
            "hard_stop": 0.15,
            "stop_cooldown_h": 48,
            "vol_range_tight": 0.10,
            "vol_range_loose": 0.20,
            "max_hold_h": 240,
            "min_price": 10000,
            "max_positions": 8,
            "min_age_days": 7,
            "burn_in_h": 72,
            "qty_small": 10,
            "qty_medium": 18,
            "qty_large": 25,
            "absorb_lookback_h": 6,
            "absorb_depletion": 0.30,
            "absorb_min_listings": 20,
            "absorb_price_range_max": 0.05,
            "absorb_cooldown_h": 24,
        }]
