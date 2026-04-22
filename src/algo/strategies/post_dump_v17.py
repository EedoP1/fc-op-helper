"""Post-dump v17 — scale v15 champion on CAPACITY axes (not basket axis).

v15 passes all 6 bars (+$144.6k filtered, 83% win, corr +0.067) but
weekly return is only ~5%/wk, well below the 25%/wk target. v16 tried
to scale via basket_size (6→18) and max_positions (12→36) and blew up
(-$73.7k) because basket inflation caused the first trigger to eat all
cash on many small positions, starving later triggers.

v17 keeps basket_size=6 (do not inflate the trigger universe) and
scales on DIFFERENT axes:
  - max_positions: 12 → 20 (more concurrent holds when trig_a and
    trig_b both fire within the cooldown window)
  - qty_cap: 6 → 10 (bigger per-position sizing, pushes deployed
    capital without widening the basket)
  - max_hold_h: 120 → 144 (give winners extra time to reach the 15%
    profit target; stop_delay unchanged so losers still cut at 36h)

All other params identical to v15. Hypothesis: same high-conviction
basket, same trigger logic, but larger bet size and more simultaneous
positions lift total PnL toward the 25%/wk target without breaking
the edge.
"""
import logging
from datetime import datetime
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class PostDumpV17Strategy(Strategy):
    name = "post_dump_v17"

    def __init__(self, params: dict):
        self.params = params
        # Global dump detection
        self.dump_lookback_h: int = params.get("dump_lookback_h", 48)
        self.dump_min_pct: float = params.get("dump_min_pct", -0.04)
        self.recovery_short_h: int = params.get("recovery_short_h", 6)
        self.recovery_min_pct: float = params.get("recovery_min_pct", 0.005)
        # After triggering, throttle: don't fire again for X hours
        self.trigger_cooldown_h: int = params.get("trigger_cooldown_h", 96)
        # Card universe at trigger: cheapest N liquid cards
        self.basket_size: int = params.get("basket_size", 6)
        # Per-card filters
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.06)
        # Exits
        self.profit_target: float = params.get("profit_target", 0.12)
        self.stop_loss: float = params.get("stop_loss", 0.10)
        self.stop_delay_h: int = params.get("stop_delay_h", 36)
        self.max_hold_h: int = params.get("max_hold_h", 96)
        # Universe gates
        self.min_price: int = params.get("min_price", 11000)
        self.max_price: int = params.get("max_price", 30000)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 72)
        self.qty_cap: int = params.get("qty_cap", 6)
        self.max_positions: int = params.get("max_positions", 12)

        hist_len = max(self.dump_lookback_h * 2, 168) + 8
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._g_history: deque = deque(maxlen=hist_len)
        self._created_at: dict[int, datetime] = {}
        self._promo_ids: set[int] = set()
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._first_ts: datetime | None = None
        self._last_trigger_ts: datetime | None = None
        self._last_a_ts: datetime | None = None
        self._last_b_ts: datetime | None = None

    def set_created_at_map(self, created_at_map: dict):
        self._created_at = created_at_map
        hour_buckets: dict[tuple, list[int]] = defaultdict(list)
        for ea_id, created in created_at_map.items():
            cr = created.replace(tzinfo=None) if created.tzinfo else created
            if cr.weekday() == 4:
                bucket = (cr.year, cr.month, cr.day, cr.hour)
                hour_buckets[bucket].append(ea_id)
        self._promo_fridays: list[datetime] = []
        for bucket, ids in hour_buckets.items():
            if len(ids) >= 10:
                self._promo_ids.update(ids)
                self._promo_fridays.append(datetime(*bucket[:3], bucket[3]))
        self._promo_fridays.sort()

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
                pct = (smooth - buy_price) / buy_price
                if pct >= self.profit_target:
                    sell = True
                elif hold_hours >= self.stop_delay_h and pct <= -self.stop_loss:
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

        # Trigger A: rapid dump + recovery (the v5 winner) — independent cooldown
        d48 = self._g_delta(self.dump_lookback_h)
        d6 = self._g_delta(self.recovery_short_h)
        trig_a = (d48 <= self.dump_min_pct and d6 >= self.recovery_min_pct)
        if trig_a and self._last_a_ts:
            since_a = (ts_clean - self._last_a_ts).total_seconds() / 3600
            if since_a < self.trigger_cooldown_h:
                trig_a = False

        # Trigger B (promo-Sat alignment) — independent cooldown
        trig_b = False
        if self._promo_fridays:
            recent_promo = max((p for p in self._promo_fridays if p <= ts_clean), default=None)
            if recent_promo:
                hrs_since = (ts_clean - recent_promo).total_seconds() / 3600
                if 18 <= hrs_since <= 30:
                    d24 = self._g_delta(24)
                    if d24 <= -0.025:
                        trig_b = True
        if trig_b and self._last_b_ts:
            since_b = (ts_clean - self._last_b_ts).total_seconds() / 3600
            if since_b < self.trigger_cooldown_h:
                trig_b = False

        if not (trig_a or trig_b):
            return signals

        # Trigger fires! Build basket of cheapest liquid cards in price band
        basket: list[tuple[int, int, int]] = []
        for ea_id, price in ticks:
            hist = self._history[ea_id]
            if len(hist) < 24:
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
            basket.append((ea_id, price, smooth))

        if not basket or len(portfolio.positions) >= self.max_positions:
            return signals

        basket.sort(key=lambda x: x[2])
        basket = basket[:self.basket_size]

        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                p = next((pp for eid, pp in ticks if eid == s.ea_id), 0)
                sell_rev += (p * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

        open_slots = self.max_positions - len(portfolio.positions)
        buys_made = 0
        for ea_id, price, _ in basket:
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
            self._last_trigger_ts = ts_clean
            if trig_a:
                self._last_a_ts = ts_clean
            if trig_b:
                self._last_b_ts = ts_clean

        return signals

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        return [{
            "dump_lookback_h": 48,
            "dump_min_pct": -0.035,
            "recovery_short_h": 6,
            "recovery_min_pct": 0.004,
            "trigger_cooldown_h": 48,
            "basket_size": 6,
            "smooth_window_h": 3,
            "outlier_tol": 0.06,
            "profit_target": 0.15,
            "stop_loss": 0.10,
            "stop_delay_h": 36,
            "max_hold_h": 144,
            "min_price": 11000,
            "max_price": 35000,
            "min_age_days": 7,
            "burn_in_h": 72,
            "qty_cap": 10,
            "max_positions": 20,
        }]
