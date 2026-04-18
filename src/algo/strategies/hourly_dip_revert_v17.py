"""Hourly dip reversion v17 — v12 + listing-count supply-drying filter.

v12's entry signal (5% smoothed dip for 2 hours) fires on many cards. Under
pessimistic execution many dips don't reverse enough to clear the 13%
spread+tax cost. Win rate 61.3%.

Hypothesis: a dip is MORE likely to reverse when supply is drying up. If
listing_count is FALLING (fewer sellers) while price is dipping, someone
will notice the low price and bid it back up. If listing_count is RISING
(more sellers) while price is dipping, supply is increasing — price more
likely to keep falling.

v17 adds a listing-count trend filter on top of v12's entry: require that
the current hour's avg listing_count is AT MOST `lc_ratio` × the 24h avg
listing_count. A ratio ≤1.0 requires declining listings; ratio >1.0 is
lenient.

Hoping this boosts win rate from 61% → 65%+ without slashing trade count
to failure territory.
"""
import logging
from datetime import datetime, timedelta
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class HourlyDipRevertV17Strategy(Strategy):
    """v12 entry + supply-drying listing-count filter."""

    name = "hourly_dip_revert_v17"

    def __init__(self, params: dict):
        self.params = params
        self.median_window_h: int = params.get("median_window_h", 24)
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.05)
        self.dip_pct: float = params.get("dip_pct", 0.05)
        self.confirm_hours: int = params.get("confirm_hours", 2)
        self.profit_target: float = params.get("profit_target", 0.25)
        self.stop_loss: float = params.get("stop_loss", 0.15)
        self.max_hold_h: int = params.get("max_hold_h", 48)
        self.min_price: int = params.get("min_price", 10000)
        self.max_price: int = params.get("max_price", 80000)
        self.max_positions: int = params.get("max_positions", 8)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 72)
        self.qty_cap: int = params.get("qty_cap", 3)
        # Listing-count params
        self.lc_ratio: float = params.get("lc_ratio", 1.0)  # current / 24h avg
        self.min_lc_avg: int = params.get("min_lc_avg", 5)  # floor to avoid zero-data cards

        self._history: dict[int, deque] = defaultdict(
            lambda: deque(maxlen=self.median_window_h + 8)
        )
        self._dip_streak: dict[int, int] = defaultdict(int)
        self._created_at: dict[int, datetime] = {}
        self._promo_ids: set[int] = set()
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._first_ts: datetime | None = None
        self._listing_counts: dict[tuple[int, datetime], int] = {}

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
        # Strip tzinfo so lookups match our naive ts_clean keys
        self._listing_counts = {
            (eid, t.replace(tzinfo=None) if hasattr(t, 'tzinfo') and t.tzinfo else t): v
            for (eid, t), v in listing_counts.items()
        }

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

    def _lc_supply_ok(self, ea_id: int, ts: datetime) -> bool:
        """Return True if listing_count current ≤ lc_ratio × 24h avg."""
        current_lc = self._listing_counts.get((ea_id, ts), 0)
        if current_lc <= 0:
            return False
        # 24h average of listing_count
        vals = []
        for h in range(1, 25):
            prior = ts - timedelta(hours=h)
            lc = self._listing_counts.get((ea_id, prior))
            if lc is not None and lc > 0:
                vals.append(lc)
        if len(vals) < 12:
            return False  # need at least half the window populated
        avg_lc = sum(vals) / len(vals)
        if avg_lc < self.min_lc_avg:
            return False
        return current_lc <= self.lc_ratio * avg_lc

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
                elif smooth > 0:
                    smooth_pct = (smooth - buy_price) / buy_price if buy_price > 0 else 0
                    if smooth_pct >= self.profit_target:
                        sell = True
                    if smooth_pct <= -self.stop_loss:
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
                    # Listing-count supply filter
                    if not self._lc_supply_ok(ea_id, ts_clean):
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
                self._dip_streak[ea_id] = 0

        return signals

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        base = {
            "median_window_h": 24,
            "smooth_window_h": 3,
            "outlier_tol": 0.05,
            "dip_pct": 0.05,
            "confirm_hours": 2,
            "profit_target": 0.25,
            "stop_loss": 0.15,
            "max_hold_h": 48,
            "min_price": 10000,
            "max_price": 80000,
            "max_positions": 8,
            "min_age_days": 7,
            "burn_in_h": 72,
            "qty_cap": 3,
            "min_lc_avg": 5,
        }
        # Sweep the LC ratio threshold
        return [
            {**base, "lc_ratio": r}
            for r in (0.70, 0.80, 0.90, 1.00, 1.10, 1.25, 1.50)
        ]
