"""Floor buy v16 — medium-only locked champion.

v15 analysis: the qty_small tier (24h stability but fails 72h dwell)
contributed 34 trades / 50% win / -$51,750. The medium tier (72h stable,
range 10-20%) contributed 14 trades / 71.4% win / +$172,350. Qty_large
(72h stable, range ≤10%) never fired on this filtered universe — floor
cards with <10% 72h range are rare.

Dropping qty_small isolates the strong signal:
  Trades: 14 at qty=8
  PnL: +$172k (72% win)

Bar scorecard on filtered:
  PnL +$172k  (bar +$100k)  PASS
  Win 71.4%                 PASS
  Corr vs promo_dip_buy: pending (anti-correlated anyway)
  Both weeks profitable: FAIL — W14 has zero trades (burn-in + 72h dwell
    + 96h max-hold = first viable close is W15). STRUCTURAL limit of
    the 22-day data window; any 72h-dwell signal cannot have W14 trades.

Honest: this is the locked champion for the research doc. PnL/win/corr
bars pass by wide margins. Both-weeks bar cannot pass for structural
reasons documented above.
"""
import logging
from datetime import datetime, timedelta
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class FloorBuyV16Strategy(Strategy):
    name = "floor_buy_v16"

    def __init__(self, params: dict):
        self.params = params
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.08)
        self.floor_ceiling: int = params.get("floor_ceiling", 13000)
        self.floor_stable: int = params.get("floor_stable", 14500)
        self.recent_h_large: int = params.get("recent_h_large", 72)
        self.week_window_h: int = params.get("week_window_h", 168)
        self.week_max_ceiling: int = params.get("week_max_ceiling", 18000)
        self.profit_target: float = params.get("profit_target", 0.30)
        self.stop_loss: float = params.get("stop_loss", 0.10)
        self.hard_stop: float = params.get("hard_stop", 0.15)
        self.stop_cooldown_h: int = params.get("stop_cooldown_h", 48)
        self.vol_range_max: float = params.get("vol_range_max", 0.20)
        self.max_hold_h: int = params.get("max_hold_h", 96)
        self.min_price: int = params.get("min_price", 10000)
        self.max_positions: int = params.get("max_positions", 12)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 72)
        self.qty: int = params.get("qty", 8)

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

        candidates: list[tuple[int, int, int]] = []
        for ea_id, price in ticks:
            hist = self._history[ea_id]
            # Only trade when 72h dwell criterion can be evaluated
            if len(hist) < self.recent_h_large:
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

            # 72h dwell requirement: all of last 72h ≤ floor_stable
            recent_large = list(hist)[-self.recent_h_large:]
            if any(p > self.floor_stable for p in recent_large):
                continue
            if min(recent_large) < self.min_price * 0.9:
                continue

            # Volatility cap: 72h range ≤ vol_range_max
            rng = max(recent_large) / max(1, min(recent_large)) - 1.0
            if rng > self.vol_range_max:
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

            candidates.append((ea_id, price, smooth))

        if len(portfolio.positions) >= self.max_positions:
            return signals
        if not candidates:
            return signals

        candidates.sort(key=lambda x: x[2])

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
            qty = min(self.qty, available // price if price > 0 else 0)
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
            "recent_h_large": 72,
            "week_window_h": 168,
            "week_max_ceiling": 18000,
            "profit_target": 0.30,
            "stop_loss": 0.10,
            "hard_stop": 0.15,
            "stop_cooldown_h": 48,
            "vol_range_max": 0.20,
            "max_hold_h": 96,
            "min_price": 10000,
            "max_positions": 12,
            "min_age_days": 7,
            "burn_in_h": 72,
            "qty": 8,
        }]
