"""Long-hold v1 (iter 90) — Monday-entry $13-17k floor specialist.

Hypothesis: the 96-168h hold cluster (305 opps in pessimistic catalog, median
ROI net 51.6%, the highest ROI of any hold bucket) is dominated by Monday buys
in the $10-20k band recovering by Friday/weekend. v19/v19_ext already harvest
the $10-13k Mon mass-buy regime AND the $13-18k Thu/Fri post-promo regime, but
do NOT cover the $13-17k Monday-mass-buy LH regime.

Pre-analysis evidence:
  long-hold cluster (96-168h, n=309): median ROI net 51.6%, $10-13k=124,
    $13-20k=95, Mon=240/309 (78%), W16=179 (58% of all LH opps).
  v19_ext stack overlap with LH ea pool: 5.2%. Open LH opps in $10-20k bands
    not covered by stack: 160; W16 share = 131/160 (82%).
  v19_ext biggest winners (94k/98k/99k) are all Thu/Fri buys, not Mon — so
    Mon-only is structurally orthogonal to v19_ext.

Differences from floor_buy_v19_ext (which covers $13-18k):
  * Day-of-week gate: BUY only on Mon/Tue (UTC). v19_ext's wins are Thu/Fri.
  * Lower profit_target 0.30 (Mon LH winners cap around $17-18k from $13k buy
    median, so 50% target frequently expires unfilled).
  * Shorter max_hold 168 (long-hold ceiling — recovery by next weekend or out).
  * Tighter band: $13-17k (avoid $14.25k+ entries that lost in v19_ext).
  * Stronger drawdown gate: require 168h price was at least 18% above current
    (real dd, not just "below $20k week ceiling").
  * Smaller per-position qty (5/8/12) — long holds tie up cash, leave room
    for stack daily-trigger strategies.
  * Longer stop_cooldown 96h — recoveries take days, no point retrying soon.
"""
import logging
from datetime import datetime, timedelta
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class LongHoldV1Strategy(Strategy):
    name = "long_hold_v1"

    def __init__(self, params: dict):
        self.params = params
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.08)
        self.floor_ceiling: int = params.get("floor_ceiling", 17000)
        self.floor_stable: int = params.get("floor_stable", 18500)
        self.recent_h_min: int = params.get("recent_h_min", 24)
        self.recent_h_large: int = params.get("recent_h_large", 72)
        self.week_window_h: int = params.get("week_window_h", 168)
        self.week_max_ceiling: int = params.get("week_max_ceiling", 22000)
        self.week_dd_min: float = params.get("week_dd_min", 0.18)
        self.profit_target: float = params.get("profit_target", 0.30)
        self.stop_loss: float = params.get("stop_loss", 0.10)
        self.hard_stop: float = params.get("hard_stop", 0.15)
        self.stop_cooldown_h: int = params.get("stop_cooldown_h", 96)
        self.max_hold_h: int = params.get("max_hold_h", 168)
        self.min_hold_h: int = params.get("min_hold_h", 24)
        self.min_price: int = params.get("min_price", 13000)
        self.max_positions: int = params.get("max_positions", 8)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 72)
        # entry day-of-week filter: 0=Mon..6=Sun. Mon/Tue only.
        self.entry_dows: tuple = tuple(params.get("entry_dows", (0, 1)))
        # entry hour-of-day window (UTC) — wide allow, but skip late evening dump zone
        self.entry_hour_min: int = params.get("entry_hour_min", 0)
        self.entry_hour_max: int = params.get("entry_hour_max", 14)
        # min sales-per-hour proxy: require listings depth via week stability
        self.qty_small: int = params.get("qty_small", 5)
        self.qty_medium: int = params.get("qty_medium", 8)
        self.qty_large: int = params.get("qty_large", 12)
        self.vol_range_tight: float = params.get("vol_range_tight", 0.10)
        self.vol_range_loose: float = params.get("vol_range_loose", 0.18)

        hist_len = max(self.week_window_h, self.recent_h_large) + 8
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._created_at: dict[int, datetime] = {}
        self._promo_ids: set[int] = set()
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._first_ts: datetime | None = None
        self._stopped_until: dict[int, datetime] = {}

    def set_created_at_map(self, created_at_map: dict):
        self._created_at = created_at_map
        # Tag promo-launch IDs (Friday-launch hour-clusters >=10 cards) — skip them.
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

        # Update history & process exits.
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
                stopped = False
                # hard stop
                if buy_price > 0 and price <= buy_price * (1.0 - self.hard_stop):
                    sell = True
                    stopped = True
                elif hold_hours >= self.max_hold_h:
                    sell = True
                elif hold_hours >= self.min_hold_h and smooth > 0 and buy_price > 0:
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

        # Burn-in.
        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        # Day-of-week + hour entry gate.
        dow = ts_clean.weekday()
        if dow not in self.entry_dows:
            return signals
        hod = ts_clean.hour
        if not (self.entry_hour_min <= hod <= self.entry_hour_max):
            return signals

        # Build candidates.
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
            if min(recent_min) < self.min_price * 0.85:
                continue

            # Drawdown gate: 168h max must be at least week_dd_min above current.
            # This selects cards that ACTUALLY dipped (the LH regime) and avoids
            # cards that have been flat-floored the whole week (no recovery edge).
            if len(hist) >= self.week_window_h:
                week = list(hist)[-self.week_window_h:]
                wk_max = max(week)
                if wk_max > self.week_max_ceiling:
                    continue
                # require real drawdown from week max
                if wk_max <= 0:
                    continue
                dd = (wk_max - price) / wk_max
                if dd < self.week_dd_min:
                    continue
            else:
                # not enough history to qualify drawdown — skip
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

            # Sizing tier from 72h volatility.
            qty_cap = self.qty_small
            if len(hist) >= self.recent_h_large:
                recent_large = list(hist)[-self.recent_h_large:]
                if all(p <= self.floor_stable for p in recent_large) \
                   and min(recent_large) >= self.min_price * 0.85:
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

        # Prefer larger qty_cap (more conviction), then lower smoothed price.
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
            "floor_ceiling": 17000,
            "floor_stable": 18500,
            "recent_h_min": 24,
            "recent_h_large": 72,
            "week_window_h": 168,
            "week_max_ceiling": 22000,
            "week_dd_min": 0.18,
            "profit_target": 0.30,
            "stop_loss": 0.10,
            "hard_stop": 0.15,
            "stop_cooldown_h": 96,
            "max_hold_h": 168,
            "min_hold_h": 24,
            "min_price": 13000,
            "max_positions": 8,
            "min_age_days": 7,
            "burn_in_h": 72,
            "entry_dows": (0, 1),
            "entry_hour_min": 0,
            "entry_hour_max": 14,
            "qty_small": 5,
            "qty_medium": 8,
            "qty_large": 12,
            "vol_range_tight": 0.10,
            "vol_range_loose": 0.18,
        }]
