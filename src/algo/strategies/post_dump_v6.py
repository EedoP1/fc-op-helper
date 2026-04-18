"""Post-dump v1 — buy MARKET-WIDE the day after a global dump.

Pivots away from per-card entry signals (which all 9 prior iterations
have struggled to make work). Bets on a global market regime instead.

EDA finding (followup1): every full ISO week shows mean fwd 24h
returns of -2 to -8% on Thu/Fri/Sat (the global dump phase) and +1 to
+5% on Sun/Mon/Tue/Wed (recovery phase). This means:
  - When the GLOBAL median has dropped >= X% over the past 24-48h
  - AND has just turned UP in the last 6h
  - It's a market-bottom signal — buy a basket of liquid cheap cards

Hypothesis: rather than waiting for individual card signals, BUY THE
MARKET (a basket of N cheapest-tier liquid cards) at confirmed
market-trough turning points. Hold for the full recovery (~48-72h).

Key differences from prior failures:
  - Entry gate is GLOBAL (one trigger fires N buys at once), not
    per-card filtering down to nothing
  - No floor proximity required (we're betting on the whole market)
  - Soft stop only after 36h (recovery has had a chance to start)

Different from cycle_bottom (which required per-card 168h floor proximity
AND gate). Different from cohort_chase (which used tier-specific peer
rallies). Different from consol_breakup (which required individual
card consolidation).
"""
import logging
from datetime import datetime
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class PostDumpV6Strategy(Strategy):
    name = "post_dump_v6"

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

        # Trigger cooldown
        if self._last_trigger_ts:
            since = (ts_clean - self._last_trigger_ts).total_seconds() / 3600
            if since < self.trigger_cooldown_h:
                return signals

        # Trigger condition: dumped X% over 48h AND just turned up over 6h
        d48 = self._g_delta(self.dump_lookback_h)
        d6 = self._g_delta(self.recovery_short_h)
        if d48 > self.dump_min_pct or d6 < self.recovery_min_pct:
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
            "max_hold_h": 120,
            "min_price": 11000,
            "max_price": 35000,
            "min_age_days": 7,
            "burn_in_h": 72,
            "qty_cap": 6,
            "max_positions": 12,
        }]
