"""Monday bottom v1 — Mon UTC buy on $13-20k stable-floor cards, Sat 22 UTC TP exit.

DATA-DRIVEN DESIGN from .planning/profit_opportunities.json (pessimistic):

Cluster: buy_price in [13k, 20k] AND hold 96-168h
  - 95 pessimistic opps
  - median ROI 48.9%, mean 48.9%, all > 20% ROI
  - nominal net $649k across 95 trades
  - 77/95 (81%) bought Monday UTC
  - 57/95 (60%) sold Saturday, sell_hour 22 UTC dominant
  - weeks: W16=69, W14=15, W15=11 (W16-heavy but present all weeks)

Why this is ORTHOGONAL to the current stack:
  - floor_buy_v19 caps `floor_ceiling=13000` and `week_max_ceiling=18000`. A
    card sitting at 14-20k with a 72h stable floor is REJECTED by v19 today.
  - post_dump_v15 requires a global-dump trigger (regime event), not a
    calendar gate — most W16 Mon-UTC bottoms do NOT coincide with a 4% dump.
  - floor_buy_v19_ext targets a different band/exit — no DoW / timed-exit.

Pre-buy signature (no look-ahead):
  1) Tick timestamp is Monday UTC, hour in [0, 8]
  2) Current price in [13000, 20000]
  3) 72h price window: max <= 21000 AND range <= 20%
       (i.e., card has been floor-stable at cheap-mid tier, no spike)
  4) 168h week window: max <= 23000 (no prior run-up to fade)
  5) NOT in promo_ids (card wasn't released this promo cycle)
  6) min_age_days >= 7

Exit (dual):
  - CALENDAR: on Saturday UTC hour >= 20 (target Sat 22 UTC window)
  - PROFIT: smooth ROI >= +30%
  - STOP: smooth ROI <= -12% after 48h hold
  - TIMEOUT: 168h

Sizing: qty=12 per position (capped by $125k per-slot → at $15k buy = 8 units
actual cap; engine enforces). 8 max positions.
"""
import logging
from datetime import datetime, timedelta
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class MondayBottomV1Strategy(Strategy):
    name = "monday_bottom_v1"

    def __init__(self, params: dict):
        self.params = params
        # Entry gate
        self.min_price: int = params.get("min_price", 13000)
        self.max_price: int = params.get("max_price", 20000)
        self.buy_dow: int = params.get("buy_dow", 0)          # Monday
        self.buy_hour_start: int = params.get("buy_hour_start", 0)
        self.buy_hour_end: int = params.get("buy_hour_end", 8)
        # Floor-stability
        self.recent_h: int = params.get("recent_h", 72)
        self.recent_max: int = params.get("recent_max", 21000)
        self.recent_range_max: float = params.get("recent_range_max", 0.20)
        self.week_h: int = params.get("week_h", 168)
        self.week_max: int = params.get("week_max", 23000)
        # Smoothing
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.08)
        # Age
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 72)
        # Exit
        self.sell_dow: int = params.get("sell_dow", 5)        # Saturday
        self.sell_hour_start: int = params.get("sell_hour_start", 20)
        self.profit_target: float = params.get("profit_target", 0.30)
        self.stop_loss: float = params.get("stop_loss", 0.12)
        self.stop_delay_h: int = params.get("stop_delay_h", 48)
        self.max_hold_h: int = params.get("max_hold_h", 168)
        # Sizing
        self.qty_cap: int = params.get("qty_cap", 12)
        self.max_positions: int = params.get("max_positions", 8)

        hist_len = max(self.week_h, self.recent_h) + 8
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._created_at: dict[int, datetime] = {}
        self._promo_ids: set[int] = set()
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._first_ts: datetime | None = None

    def set_created_at_map(self, created_at_map: dict):
        self._created_at = created_at_map
        # Reuse v19's promo-detection: Fridays with >=10 same-hour releases
        hour_buckets: dict[tuple, list[int]] = defaultdict(list)
        for ea_id, created in created_at_map.items():
            cr = created.replace(tzinfo=None) if created.tzinfo else created
            if cr.weekday() == 4:  # Friday
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

            # Profit target
            if smooth > 0 and buy_price > 0:
                pct = (smooth - buy_price) / buy_price
                if pct >= self.profit_target:
                    sell = True
                elif hold_hours >= self.stop_delay_h and pct <= -self.stop_loss:
                    sell = True

            # Saturday evening calendar exit
            if (not sell
                and ts_clean.weekday() == self.sell_dow
                and ts_clean.hour >= self.sell_hour_start
                and hold_hours >= 48):
                sell = True

            # Hard max hold
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

        # Entry gate: only on Monday UTC 0-8
        if ts_clean.weekday() != self.buy_dow:
            return signals
        if not (self.buy_hour_start <= ts_clean.hour <= self.buy_hour_end):
            return signals
        if len(portfolio.positions) >= self.max_positions:
            return signals

        # Build candidates
        candidates: list[tuple[int, int, int]] = []
        for ea_id, price in ticks:
            hist = self._history[ea_id]
            if len(hist) < self.recent_h:
                continue
            smooth = self._smooth(hist)
            if smooth <= 0 or self._is_outlier(price, smooth):
                continue

            if not (self.min_price <= price <= self.max_price):
                continue
            if not (self.min_price <= smooth <= self.max_price):
                continue

            recent = list(hist)[-self.recent_h:]
            if max(recent) > self.recent_max:
                continue
            rng = max(recent) / max(1, min(recent)) - 1.0
            if rng > self.recent_range_max:
                continue

            if len(hist) >= self.week_h:
                week = list(hist)[-self.week_h:]
                if max(week) > self.week_max:
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

            candidates.append((ea_id, price, smooth))

        if not candidates:
            return signals

        # Cheapest-smooth first (aligns with 77% of opps in $10-13k/$13-20k band)
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
            "min_price": 13000,
            "max_price": 20000,
            "buy_dow": 0,
            "buy_hour_start": 0,
            "buy_hour_end": 8,
            "recent_h": 72,
            "recent_max": 21000,
            "recent_range_max": 0.20,
            "week_h": 168,
            "week_max": 23000,
            "smooth_window_h": 3,
            "outlier_tol": 0.08,
            "min_age_days": 7,
            "burn_in_h": 72,
            "sell_dow": 5,
            "sell_hour_start": 20,
            "profit_target": 0.30,
            "stop_loss": 0.12,
            "stop_delay_h": 48,
            "max_hold_h": 168,
            "qty_cap": 12,
            "max_positions": 8,
        }]
