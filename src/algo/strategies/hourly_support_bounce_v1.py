"""Hourly support-bounce v1 — enter on proven-floor touches.

v12 catches any smoothed dip below 24h median. That includes new downtrends
with no support history. Win rate 61.3%.

Support-bounce replaces the entry signal: only buy when the card's smoothed
price is near a floor that has been TESTED AND HELD multiple times in the
prior 7 days. "Tested and held" means a prior touch, then a rebound above
floor*1.08, then another touch. At least 3 such testing cycles required.

Hypothesis: cards with proven floor history are MORE likely to reverse than
cards dipping below 24h median without prior support evidence. Higher win
rate under pessimistic execution directly lifts PnL because the pessimistic
fill cost per round-trip (~13%) dominates the edge.

Exit mechanics identical to v12: +25% smoothed target, -15% smoothed stop,
48h max-hold, smoothed-based only.
"""
import logging
from datetime import datetime
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class HourlySupportBounceV1Strategy(Strategy):
    """Enter only on cards at proven floor; same exits as v12."""

    name = "hourly_support_bounce_v1"

    def __init__(self, params: dict):
        self.params = params
        # Support window
        self.floor_window_h: int = params.get("floor_window_h", 168)
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        # Floor proximity (smoothed within X% of floor to count as "at support")
        self.floor_prox: float = params.get("floor_prox", 0.04)
        # Rebound threshold (smoothed above floor * (1+rebound) to count as "held")
        self.rebound_pct: float = params.get("rebound_pct", 0.08)
        # Minimum number of test cycles (touch + rebound + touch...)
        self.min_touches: int = params.get("min_touches", 3)
        # Required range: ceiling/floor ratio
        self.min_range_ratio: float = params.get("min_range_ratio", 1.15)
        # Outlier guard (same as v12)
        self.outlier_tol: float = params.get("outlier_tol", 0.05)
        # Exits (same as v12)
        self.profit_target: float = params.get("profit_target", 0.25)
        self.stop_loss: float = params.get("stop_loss", 0.15)
        self.max_hold_h: int = params.get("max_hold_h", 48)
        # Universe filters
        self.min_price: int = params.get("min_price", 10000)
        self.max_price: int = params.get("max_price", 80000)
        self.max_positions: int = params.get("max_positions", 8)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 72)
        self.qty_cap: int = params.get("qty_cap", 3)

        self._history: dict[int, deque] = defaultdict(
            lambda: deque(maxlen=self.floor_window_h + 4)
        )
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

    def _count_touch_cycles(self, history: deque, floor: int) -> int:
        """Count touch+rebound cycles in history.

        A cycle = smoothed within floor_prox of floor, then smoothed rises
        above floor*(1+rebound_pct) before another touch. Returns number of
        distinct "tested and held" cycles.
        """
        if floor <= 0:
            return 0
        touch_threshold = floor * (1 + self.floor_prox)
        rebound_threshold = floor * (1 + self.rebound_pct)

        vals = list(history)
        n = len(vals)
        if n < self.smooth_window_h:
            return 0

        cycles = 0
        in_touch = False
        rebounded_since_last_touch = True  # allow first touch to count

        for i in range(self.smooth_window_h - 1, n):
            window = vals[max(0, i - self.smooth_window_h + 1):i + 1]
            smooth = self._median(window)
            if smooth <= 0:
                continue

            if smooth <= touch_threshold:
                if not in_touch and rebounded_since_last_touch:
                    cycles += 1
                    rebounded_since_last_touch = False
                in_touch = True
            else:
                in_touch = False
                if smooth >= rebound_threshold:
                    rebounded_since_last_touch = True

        return cycles

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
            if len(hist) < self.floor_window_h:
                continue
            smooth = self._smooth(hist)
            if smooth <= 0 or self._is_outlier(price, smooth):
                continue

            # Compute floor and ceiling from smoothed series over window
            vals = list(hist)
            smooth_series = []
            for i in range(self.smooth_window_h - 1, len(vals)):
                w = vals[i - self.smooth_window_h + 1:i + 1]
                s = self._median(w)
                if s > 0:
                    smooth_series.append(s)
            if len(smooth_series) < 20:
                continue
            floor = min(smooth_series)
            ceiling = max(smooth_series)
            if floor <= 0:
                continue
            range_ratio = ceiling / floor
            if range_ratio < self.min_range_ratio:
                continue

            # Must currently be at support
            if smooth > floor * (1 + self.floor_prox):
                continue

            # Must have prior touch+rebound cycles
            touches = self._count_touch_cycles(hist, floor)
            if touches < self.min_touches:
                continue

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

            # Conviction score: more touches + bigger range = higher
            conviction = touches * (range_ratio - 1.0)
            candidates.append((ea_id, price, conviction))

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

        return signals

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        base = {
            "floor_window_h": 168,
            "smooth_window_h": 3,
            "outlier_tol": 0.05,
            "floor_prox": 0.04,
            "rebound_pct": 0.08,
            "min_range_ratio": 1.15,
            "profit_target": 0.25,
            "stop_loss": 0.15,
            "max_hold_h": 48,
            "min_price": 10000,
            "max_price": 80000,
            "max_positions": 8,
            "min_age_days": 7,
            "burn_in_h": 72,
            "qty_cap": 3,
        }
        combos = []
        for touches in (2, 3, 4):
            for prox in (0.03, 0.04, 0.05):
                combos.append({
                    **base,
                    "min_touches": touches,
                    "floor_prox": prox,
                })
        return combos
