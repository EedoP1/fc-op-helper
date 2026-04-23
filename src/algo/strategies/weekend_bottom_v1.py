"""Weekend-bottom v1 — TIME-OF-WEEK buying, no drawdown gate.

Data-first findings (iter66):
- Global median price (normalized per week) shows a repeatable macro cycle:
  LOW: Sat 23 UTC (0.942) → Sun 00-01 (0.957) → Sun 15-20 (0.968-0.972)
  HIGH: Mon 21 (1.032) → Tue 00-01 (1.026-1.030) → Tue 10-13 (1.026-1.029)
  Edge magnitude ~6% global median peak-to-trough.
- Monday buy-entries are 33% of 1,850 pessimistic opps (619); Sunday is 4% (65)
  — empirical opps cluster in the RECOVERY phase (Mon onward) not at the trough.
- $14-28k band is orthogonal to v19 (floor_ceiling=13k) and above post_dump's
  max_price range when SPH>=8.
- drawdown_reversion_v1-v4 (3-feature drawdown AND) all failed — they fired on
  deep drops (continuation trap). This strategy uses NO drawdown signal; it
  relies purely on the time-of-week regime.

Strategy:
- Whitelist: Section B repeaters (rating 86-91, promo card types).
- Buy window: Sat 22 UTC → Sun 22 UTC (trough phase, 24h span).
- Price band: $14,000-$28,000 (above v19, inside post_dump gap).
- SPH filter is enforced by the engine's --min-sph 2 flag; strategy itself
  has no built-in SPH floor but relies on repeater whitelist.
- No drawdown / rel_pos gate — explicit departure from failed v1-v4 approach.
- Exit: +8% target (matches macro edge) OR Tue 22 UTC hard-sell (end of
  recovery phase) OR 72h max hold OR -10% smoothed stop.
- Sizing: 8 slots × $125k each, qty_cap 50 (per brief's built-in cap).
"""
import logging
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta

from src.algo.models import Portfolio, Signal
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


_WHITELIST_CARD_TYPES = {
    "fut birthday",
    "fantasy ut",
    "fof: answer the call",
    "future stars",
    "fantasy ut hero",
    "star performer",
    "ultimate scream",
    "knockout royalty icon",
    "unbreakables",
    "unbreakables icon",
    "fc pro live",
    "festival of football: captains",
    "winter wildcards",
    "time warp",
}
_WHITELIST_RATINGS = {86, 87, 88, 89, 90, 91}


_ATTRS_CACHE: dict[int, tuple[int, str]] | None = None
_ATTRS_LOCK = threading.Lock()


def _load_attrs_sync() -> dict[int, tuple[int, str]]:
    """Load {ea_id: (rating, card_type_lower)} from the players table."""
    global _ATTRS_CACHE
    with _ATTRS_LOCK:
        if _ATTRS_CACHE is not None:
            return _ATTRS_CACHE

        import asyncio
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from src.config import DATABASE_URL

        async def _run():
            eng = create_async_engine(DATABASE_URL, pool_size=1)
            async with eng.connect() as c:
                r = await c.execute(
                    text("SELECT ea_id, rating, card_type FROM players")
                )
                out: dict[int, tuple[int, str]] = {}
                for row in r.fetchall():
                    ea = int(row[0])
                    rating = int(row[1] or 0)
                    ctype = (row[2] or "").lower()
                    out[ea] = (rating, ctype)
            await eng.dispose()
            return out

        result_holder: dict = {}

        def target():
            try:
                result_holder["data"] = asyncio.run(_run())
            except Exception as exc:  # pragma: no cover - best effort
                result_holder["err"] = exc

        t = threading.Thread(target=target, daemon=True)
        t.start()
        t.join()

        if "err" in result_holder:
            logger.error(f"weekend_bottom_v1: DB load failed: {result_holder['err']}")
            _ATTRS_CACHE = {}
            return _ATTRS_CACHE

        _ATTRS_CACHE = result_holder.get("data", {})
        return _ATTRS_CACHE


def _in_whitelist(attrs: tuple[int, str] | None) -> bool:
    if attrs is None:
        return False
    rating, ctype = attrs
    if rating not in _WHITELIST_RATINGS:
        return False
    return ctype in _WHITELIST_CARD_TYPES


class WeekendBottomV1Strategy(Strategy):
    name = "weekend_bottom_v1"

    def __init__(self, params: dict):
        self.params = params
        # Smoothing / outlier guard
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.08)

        # Time-of-week buy window: Sat 22 UTC (dow=5,hr=22) → Sun 22 UTC (dow=6,hr=22)
        self.buy_window_start_dow: int = params.get("buy_window_start_dow", 5)
        self.buy_window_start_hour: int = params.get("buy_window_start_hour", 22)
        self.buy_window_end_dow: int = params.get("buy_window_end_dow", 6)
        self.buy_window_end_hour: int = params.get("buy_window_end_hour", 22)

        # Hard-exit day/hour: Tue 22 UTC (dow=1,hr=22)
        self.hard_exit_dow: int = params.get("hard_exit_dow", 1)
        self.hard_exit_hour: int = params.get("hard_exit_hour", 22)

        # Price band — above v19 floor ($13k), below post_dump cap ($35k)
        self.min_price: int = params.get("min_price", 14000)
        self.max_price: int = params.get("max_price", 28000)

        # Exits
        self.profit_target: float = params.get("profit_target", 0.08)
        self.hard_stop: float = params.get("hard_stop", 0.12)
        self.smoothed_stop: float = params.get("smoothed_stop", 0.10)
        self.max_hold_h: int = params.get("max_hold_h", 72)
        self.stop_cooldown_h: int = params.get("stop_cooldown_h", 48)

        # Sizing — follow brief's built-in caps
        self.max_positions: int = params.get("max_positions", 8)
        self.per_slot_budget: int = params.get("per_slot_budget", 125_000)
        self.qty_cap: int = params.get("qty_cap", 50)

        # Runtime gates
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 72)

        # DB attrs
        self._attrs: dict[int, tuple[int, str]] = _load_attrs_sync()
        self._whitelist_ids: set[int] = {
            ea for ea, attrs in self._attrs.items() if _in_whitelist(attrs)
        }
        logger.info(
            f"weekend_bottom_v1: loaded {len(self._attrs)} players, "
            f"whitelist size = {len(self._whitelist_ids)}"
        )

        # State
        hist_len = max(self.max_hold_h, 72) + 8
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._created_at: dict[int, datetime] = {}
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._stopped_until: dict[int, datetime] = {}
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

    def _is_outlier(self, tick: int, smooth: int) -> bool:
        if smooth <= 0:
            return True
        return abs(tick - smooth) / smooth > self.outlier_tol

    def _in_buy_window(self, ts: datetime) -> bool:
        """Sat 22 UTC through Sun 22 UTC — 24h weekend trough window."""
        dow = ts.weekday()
        hr = ts.hour
        if self.buy_window_start_dow == self.buy_window_end_dow:
            return dow == self.buy_window_start_dow and \
                   self.buy_window_start_hour <= hr < self.buy_window_end_hour
        # Spans midnight boundary
        if dow == self.buy_window_start_dow and hr >= self.buy_window_start_hour:
            return True
        if dow == self.buy_window_end_dow and hr < self.buy_window_end_hour:
            return True
        return False

    def _is_hard_exit_time(self, ts: datetime) -> bool:
        return ts.weekday() == self.hard_exit_dow and ts.hour >= self.hard_exit_hour

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

        if self._first_ts is None:
            self._first_ts = ts_clean

        # Update histories
        for ea_id, price in ticks:
            self._history[ea_id].append(price)

        # EXIT logic
        hard_exit_now = self._is_hard_exit_time(ts_clean)
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
            stopped = False

            # Hard tick stop
            if buy_price > 0 and price <= buy_price * (1.0 - self.hard_stop):
                sell = True
                stopped = True
            # Max hold
            elif hold_hours >= self.max_hold_h:
                sell = True
            # Smoothed-based profit / loss
            elif smooth > 0 and buy_price > 0:
                pct = (smooth - buy_price) / buy_price
                if pct >= self.profit_target:
                    sell = True
                elif pct <= -self.smoothed_stop:
                    sell = True
                    stopped = True
            # Cycle-end hard exit (only if we've held for at least 12h)
            if not sell and hard_exit_now and hold_hours >= 12:
                sell = True

            if sell:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._buy_ts.pop(ea_id, None)
                if stopped:
                    self._stopped_until[ea_id] = ts_clean + timedelta(hours=self.stop_cooldown_h)

        # Burn-in
        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        # Entry only during buy window
        if not self._in_buy_window(ts_clean):
            return signals

        if len(portfolio.positions) >= self.max_positions:
            return signals

        # Build candidate list
        candidates: list[tuple[int, int, int]] = []
        for ea_id, price in ticks:
            if ea_id not in self._whitelist_ids:
                continue
            if portfolio.holdings(ea_id) > 0:
                continue
            cooldown_end = self._stopped_until.get(ea_id)
            if cooldown_end and ts_clean < cooldown_end:
                continue

            hist = self._history[ea_id]
            if len(hist) < 24:
                continue

            smooth = self._smooth(hist)
            if smooth <= 0 or self._is_outlier(price, smooth):
                continue

            if not (self.min_price <= price <= self.max_price):
                continue

            created = self._created_at.get(ea_id)
            if created:
                cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
                if (ts_clean - cr_clean).days < self.min_age_days:
                    continue

            candidates.append((ea_id, price, smooth))

        if not candidates:
            return signals

        # Prefer cheapest first (more qty per slot)
        candidates.sort(key=lambda x: x[1])

        # Compute available cash
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
            if available < price:
                break
            slot_cap = min(self.per_slot_budget, available)
            qty = min(self.qty_cap, slot_cap // price)
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
            "buy_window_start_dow": 5,
            "buy_window_start_hour": 22,
            "buy_window_end_dow": 6,
            "buy_window_end_hour": 22,
            "hard_exit_dow": 1,
            "hard_exit_hour": 22,
            "min_price": 14000,
            "max_price": 28000,
            "profit_target": 0.08,
            "hard_stop": 0.12,
            "smoothed_stop": 0.10,
            "max_hold_h": 72,
            "stop_cooldown_h": 48,
            "max_positions": 8,
            "per_slot_budget": 125_000,
            "qty_cap": 50,
            "min_age_days": 7,
            "burn_in_h": 72,
        }]
