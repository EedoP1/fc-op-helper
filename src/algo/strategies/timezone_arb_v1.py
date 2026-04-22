"""Timezone arbitrage v1 — circadian price bias hypothesis.

All prior iterations (mean-reversion, floor-buy, combo, listings-surge, post-dump)
have failed or plateaued. This one pivots to a completely untried dimension:
TIME OF DAY.

Hypothesis: FC26 cards have systematic circadian price bias driven by player
activity patterns. UTC nights (01:00-05:00 — EU asleep, US late evening) see
lower listing/buying volume → slight dip. UTC primetime (18:00-22:00 — EU
primetime overlapping with US afternoon) sees peak activity → slight lift.

So: buy during low-activity night when price is near a recent low, then sell
into primetime volume.

Entry gate: timestamp.hour in [1,2,3,4,5] AND price <= 1.02 * trailing 24h-min
Exit preference: hour in [18..22] + hold >= 12h
Fallback exits: +5% smooth profit / -8% stop (after 24h) / 36h hard max
"""
import logging
from datetime import datetime
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class TimezoneArbV1Strategy(Strategy):
    name = "timezone_arb_v1"

    def __init__(self, params: dict):
        self.params = params
        # Entry window (UTC hours)
        self.entry_hours: set[int] = set(params.get("entry_hours", [1, 2, 3, 4, 5]))
        # Exit preferred window (UTC hours)
        self.exit_hours: set[int] = set(params.get("exit_hours", [18, 19, 20, 21, 22]))
        # Price ceiling relative to trailing 24h min
        self.dip_tol: float = params.get("dip_tol", 1.02)
        # Profit / stop
        self.profit_target: float = params.get("profit_target", 0.05)
        self.stop_loss: float = params.get("stop_loss", 0.08)
        self.stop_delay_h: int = params.get("stop_delay_h", 24)
        self.min_hold_h: int = params.get("min_hold_h", 12)
        self.max_hold_h: int = params.get("max_hold_h", 36)
        # Smoothing
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        # Universe
        self.min_price: int = params.get("min_price", 10000)
        self.max_price: int = params.get("max_price", 40000)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 48)
        self.qty_cap: int = params.get("qty_cap", 6)
        self.max_positions: int = params.get("max_positions", 15)

        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=72))
        self._created_at: dict[int, datetime] = {}
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._first_ts: datetime | None = None

    def set_created_at_map(self, created_at_map: dict):
        self._created_at = created_at_map

    @staticmethod
    def _median(values) -> int:
        s = sorted(values)
        return s[len(s) // 2] if s else 0

    def _smooth(self, history: deque) -> int:
        if len(history) < self.smooth_window_h:
            return 0
        return self._median(list(history)[-self.smooth_window_h:])

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

        hour = ts_clean.hour

        # ── Exits (every tick, any hour) ──
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

            # Preferred: exit-window + min hold
            if hold_hours >= self.min_hold_h and hour in self.exit_hours:
                sell = True
            # Profit target
            elif smooth > 0 and buy_price > 0 and (smooth - buy_price) / buy_price >= self.profit_target:
                sell = True
            # Stop (only after stop_delay)
            elif (
                hold_hours >= self.stop_delay_h
                and smooth > 0
                and buy_price > 0
                and (smooth - buy_price) / buy_price <= -self.stop_loss
            ):
                sell = True
            # Hard max
            elif hold_hours >= self.max_hold_h:
                sell = True

            if sell:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._buy_ts.pop(ea_id, None)

        # ── Burn-in ──
        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        # ── Entries (only in entry window) ──
        if hour not in self.entry_hours:
            return signals

        if len(portfolio.positions) >= self.max_positions:
            return signals

        # Build candidate list
        candidates: list[tuple[int, int, int]] = []
        for ea_id, price in ticks:
            hist = self._history[ea_id]
            if len(hist) < 24:
                continue
            trailing_min = min(list(hist)[-24:])
            if trailing_min <= 0:
                continue
            if price > trailing_min * self.dip_tol:
                continue
            if not (self.min_price <= price <= self.max_price):
                continue
            if portfolio.holdings(ea_id) > 0:
                continue
            created = self._created_at.get(ea_id)
            if created:
                cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
                if (ts_clean - cr_clean).days < self.min_age_days:
                    continue
            candidates.append((ea_id, price, trailing_min))

        if not candidates:
            return signals

        # Sort by how close to the trailing min (most dipped first)
        candidates.sort(key=lambda x: x[1] / max(x[2], 1))

        # Account for sell-side cash coming in this tick
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
            "entry_hours": [1, 2, 3, 4, 5],
            "exit_hours": [18, 19, 20, 21, 22],
            "dip_tol": 1.02,
            "profit_target": 0.05,
            "stop_loss": 0.08,
            "stop_delay_h": 24,
            "min_hold_h": 12,
            "max_hold_h": 36,
            "smooth_window_h": 3,
            "min_price": 10000,
            "max_price": 40000,
            "min_age_days": 7,
            "burn_in_h": 48,
            "qty_cap": 6,
            "max_positions": 15,
        }]
