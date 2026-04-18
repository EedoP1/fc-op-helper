"""Range-trade v1 — buy low / sell high inside detected consolidation zones.

Different mechanic from prior failures:
  - No global cycle gate (consol_breakup_v1: cycle gate too restrictive,
    fired 14 trades only)
  - No amplitude filter (oscillator_v1: caught decaying premium cards)
  - No cohort rally signal (cohort_chase_v1: still hit by stops)
  - No 168h low entry alone (cycle_bottom_v1/v2: falling-knife problem)

Hypothesis: identify CONSOLIDATION ZONES in each card's recent history
(rolling 72h period where range ≤ 6% of median). When a card is in an
active consolidation, BUY at the lower 25% of the zone and SELL at the
upper 80%. The zone IS the soft floor — we're not betting on
breakout, we're trading the range.

Hard stop only if price BREAKS BELOW the zone low by 5% (real
breakdown, regime change). Otherwise rely on zone-bound exit.

Universe: $11-50k (skip illiquid premium tier where Vini Jr lost $50k+).
Sizing: qty 5, max 8 positions.
"""
import logging
from datetime import datetime
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class RangeTradeV1Strategy(Strategy):
    name = "range_trade_v1"

    def __init__(self, params: dict):
        self.params = params
        self.zone_window_h: int = params.get("zone_window_h", 72)
        self.zone_max_range: float = params.get("zone_max_range", 0.06)
        self.zone_min_amp: float = params.get("zone_min_amp", 0.025)  # need some range
        self.entry_zone_quantile: float = params.get("entry_zone_quantile", 0.25)
        self.exit_zone_quantile: float = params.get("exit_zone_quantile", 0.80)
        # Zone-break stop
        self.zone_break_pct: float = params.get("zone_break_pct", 0.05)
        # Smoothing
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.05)
        # Backup profit / hold
        self.profit_target: float = params.get("profit_target", 0.12)
        self.max_hold_h: int = params.get("max_hold_h", 96)
        # Universe
        self.min_price: int = params.get("min_price", 11000)
        self.max_price: int = params.get("max_price", 50000)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 96)
        self.qty_cap: int = params.get("qty_cap", 5)
        self.max_positions: int = params.get("max_positions", 8)

        hist_len = max(168, self.zone_window_h) + 8
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._created_at: dict[int, datetime] = {}
        self._promo_ids: set[int] = set()
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        # Snapshot the zone at buy time
        self._buy_zone_low: dict[int, int] = {}
        self._buy_zone_high: dict[int, int] = {}
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
            # Zone-target exit (sell at upper-80% of original zone)
            zone_high = self._buy_zone_high.get(ea_id, 0)
            zone_low = self._buy_zone_low.get(ea_id, 0)
            if zone_high > 0 and smooth >= zone_high and hold_hours >= 6:
                sell = True
            # Zone-break stop
            if not sell and zone_low > 0 and price < zone_low * (1.0 - self.zone_break_pct):
                sell = True
            # Backup profit target
            if not sell and smooth > 0 and buy_price > 0:
                pct = (smooth - buy_price) / buy_price
                if pct >= self.profit_target:
                    sell = True
            # Max-hold
            if not sell and hold_hours >= self.max_hold_h:
                sell = True

            if sell:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._buy_ts.pop(ea_id, None)
                self._buy_zone_low.pop(ea_id, None)
                self._buy_zone_high.pop(ea_id, None)

        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        # Entry: detect consolidation zone + buy at lower quartile
        candidates: list[tuple[int, int, int, int, int]] = []  # ea_id, price, smooth, z_lo, z_hi
        for ea_id, price in ticks:
            hist = self._history[ea_id]
            if len(hist) < self.zone_window_h:
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

            window = list(hist)[-self.zone_window_h:]
            z_lo, z_hi = min(window), max(window)
            z_med = self._median(window)
            if z_lo <= 0 or z_med <= 0:
                continue
            rng = (z_hi - z_lo) / z_med
            if rng > self.zone_max_range or rng < self.zone_min_amp:
                continue

            # Entry: smoothed in lower 25% of zone
            entry_thresh = z_lo + (z_hi - z_lo) * self.entry_zone_quantile
            if smooth > entry_thresh:
                continue

            candidates.append((ea_id, price, smooth, z_lo, z_hi))

        if not candidates or len(portfolio.positions) >= self.max_positions:
            return signals

        # Sort by zone height (more upside) then by smoothed asc
        candidates.sort(key=lambda x: (-(x[4] - x[3]), x[2]))

        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                p = next((pp for eid, pp in ticks if eid == s.ea_id), 0)
                sell_rev += (p * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

        open_slots = self.max_positions - len(portfolio.positions)
        buys_made = 0
        for ea_id, price, _, z_lo, z_hi in candidates:
            if buys_made >= open_slots:
                break
            if available <= 0:
                break
            qty = min(self.qty_cap, available // price if price > 0 else 0)
            if qty > 0:
                signals.append(Signal(action="BUY", ea_id=ea_id, quantity=qty))
                self._buy_prices[ea_id] = price
                self._buy_ts[ea_id] = timestamp
                # Sell target = upper 80% of zone × 0.98 (so smoothed has to clearly enter top)
                target_high = z_lo + (z_hi - z_lo) * self.exit_zone_quantile
                self._buy_zone_low[ea_id] = z_lo
                self._buy_zone_high[ea_id] = target_high
                available -= qty * price
                buys_made += 1

        return signals

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        return [{
            "zone_window_h": 72,
            "zone_max_range": 0.10,
            "zone_min_amp": 0.020,
            "entry_zone_quantile": 0.40,
            "exit_zone_quantile": 0.80,
            "zone_break_pct": 0.05,
            "smooth_window_h": 3,
            "outlier_tol": 0.05,
            "profit_target": 0.12,
            "max_hold_h": 96,
            "min_price": 11000,
            "max_price": 50000,
            "min_age_days": 7,
            "burn_in_h": 96,
            "qty_cap": 5,
            "max_positions": 8,
        }]
