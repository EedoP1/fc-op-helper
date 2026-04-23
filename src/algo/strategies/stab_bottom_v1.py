"""Stab-bottom v1 — stabilization-confirmed drawdown reversion (iter 69).

Motivation: iters 66-68 were all killed by the SAME root cause — CONTINUATION
(buying mid-fall, price keeps dropping). Current signals catch drawdowns but
can't tell if the drop is OVER. This iteration adds a "stabilization confirmed"
gate: the buy hour must be at or near the local 6h minimum (i.e., the crash
has actually bottomed, not just dipped further).

Step-1 data-first validation on .planning/profit_opportunities.json (1,850
pessimistic opps) vs 8,000 liquidity-filtered random entries in the same
window, both restricted to whitelist cards:

  Gate                                                      pos    neg  prec   lift
  dd72h>=25%                                                520   1787  22.5%  2.55x
  dd72h>=30%                                                415   1218  25.4%  2.88x
  dd72h>=30% AND local_bottom_6h                            369    475  43.7%  4.95x
  dd72h>=35% AND local_bottom_6h                            271    329  45.2%  5.11x
  dd72h>=40% AND local_bottom_6h                            200    221  47.5%  5.37x
  dd72h>=30% AND local_bottom_6h AND lc_24h>=20             220    192  53.4%  6.04x  <-- winner
  dd72h>=30% AND local_bottom_6h AND lc_24h>=30              27     25  51.9%  5.87x

The combination `dd72h>=30% AND local_bottom_6h AND lc_24h>=20` is the first
gate we've found that clears 50% precision at >= 100 fires (412 fires, 53.4%).
Adding a liquidity floor (>=20 avg listings) was essential — it cuts false
positives more than true positives, suggesting the continuation tail is
concentrated in thinly-listed cards where the floor is still finding a buyer.

Entry rule:
  - Whitelisted card (rating 86-91 AND repeater card_type from signatures)
  - price in [$13k, $80k] (excludes v19 floor band; prevents stacking overlap)
  - 72h drawdown >= 30% from 72h max
  - local_bottom_6h: buy_price is the minimum of the prior 6 hours
    (THIS is the iter-69 innovation — stabilization confirmation)
  - lc_avg_24h >= 20 (thick listings = tradeable, not orphaned card)
  - standard age / burn-in gates

Exit (tuned from iter-66-68 lessons):
  - 25% smooth profit target (leave room for reversion from deeper dd)
  - 12% smooth stop (tighter than v4 to cut the tail when local bottom fails)
  - 15% hard stop
  - 120h max hold
  - 48h cooldown on stops

Sizing: 8 slots, $125k/slot, qty_cap 50.

Orthogonality:
  - v19 buys $10-13k floor band; this is $13k+ -> zero price overlap.
  - post_dump_v15 triggers on GLOBAL dump signal (market-wide); this is
    per-card local-bottom shape.
  - Differs from spike_crash_v1 (iter 68) which required a PEAK in first
    half of 12h window; this only requires local_bottom_6h which is a
    strictly different (and simpler) microstructure.
"""
import logging
import statistics
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta

from src.algo.models import Portfolio, Signal
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


_WHITELIST_CARD_TYPES = {
    "fut birthday",
    "fantasy ut",
    "fantasy ut hero",
    "future stars",
    "fof: answer the call",
    "star performer",
    "unbreakables",
    "unbreakables icon",
    "knockout royalty icon",
    "fc pro live",
    "festival of football: captains",
    "winter wildcards",
}
_WHITELIST_RATINGS = {86, 87, 88, 89, 90, 91}


_ATTRS_CACHE: dict[int, tuple[int, str]] | None = None
_ATTRS_LOCK = threading.Lock()


def _load_attrs_sync() -> dict[int, tuple[int, str]]:
    """Load (ea_id -> (rating, card_type)) once per process, cached."""
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

        holder: dict = {}

        def target():
            try:
                holder["data"] = asyncio.run(_run())
            except Exception as exc:  # pragma: no cover
                holder["err"] = exc

        t = threading.Thread(target=target, daemon=True)
        t.start()
        t.join()

        if "err" in holder:
            logger.error(f"stab_bottom_v1: DB load failed: {holder['err']}")
            _ATTRS_CACHE = {}
            return _ATTRS_CACHE

        _ATTRS_CACHE = holder.get("data", {})
        return _ATTRS_CACHE


def _in_whitelist(attrs: tuple[int, str] | None) -> bool:
    if attrs is None:
        return False
    rating, ctype = attrs
    if rating not in _WHITELIST_RATINGS:
        return False
    return ctype in _WHITELIST_CARD_TYPES


class StabBottomV1Strategy(Strategy):
    name = "stab_bottom_v1"

    def __init__(self, params: dict):
        self.params = params
        # Smoothing / outlier
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.10)

        # 72h drawdown gate (cheapness)
        self.dd_window_h: int = params.get("dd_window_h", 72)
        self.drawdown_min: float = params.get("drawdown_min", 0.30)

        # Stabilization: buy must be local bottom of last 6h
        self.stab_window_h: int = params.get("stab_window_h", 6)
        self.stab_tol: float = params.get("stab_tol", 0.0)  # 0 = strictly min

        # Liquidity: require thick listings (iter-69 discovery)
        self.lc_window_h: int = params.get("lc_window_h", 24)
        self.min_lc_avg: float = params.get("min_lc_avg", 20.0)

        # Price band
        self.min_price: int = params.get("min_price", 13000)
        self.max_price: int = params.get("max_price", 80000)

        # Exit
        self.profit_target: float = params.get("profit_target", 0.25)
        self.hard_stop: float = params.get("hard_stop", 0.15)
        self.smoothed_stop: float = params.get("smoothed_stop", 0.12)
        self.max_hold_h: int = params.get("max_hold_h", 120)
        self.stop_cooldown_h: int = params.get("stop_cooldown_h", 48)

        # Sizing
        self.max_positions: int = params.get("max_positions", 8)
        self.per_slot_budget: int = params.get("per_slot_budget", 125_000)
        self.qty_cap: int = params.get("qty_cap", 50)

        # Runtime gates
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 72)

        # Load DB attrs (cached)
        self._attrs: dict[int, tuple[int, str]] = _load_attrs_sync()
        self._whitelist_ids: set[int] = {
            ea for ea, a in self._attrs.items() if _in_whitelist(a)
        }
        logger.info(
            f"stab_bottom_v1: loaded {len(self._attrs)} players, "
            f"whitelist = {len(self._whitelist_ids)}"
        )

        # State
        hist_len = max(self.dd_window_h, self.lc_window_h) + 8
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._lc_history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._listing_counts: dict[tuple[int, datetime], float] = {}
        self._created_at: dict[int, datetime] = {}
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._stopped_until: dict[int, datetime] = {}
        self._first_ts: datetime | None = None

    def set_created_at_map(self, created_at_map: dict):
        self._created_at = created_at_map

    def set_listing_counts(self, listing_counts: dict):
        # Engine passes {(ea_id, hour_ts): avg_lc}; we'll track per-tick avg also.
        self._listing_counts = listing_counts or {}

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

    def _is_local_bottom(self, hist_list: list[int], price: int) -> bool:
        """Buy price is the minimum of the last stab_window_h hours (exclusive).

        Tolerance: allow price <= min(window) * (1 + stab_tol).
        """
        if len(hist_list) < self.stab_window_h + 1:
            return False
        # Prior stab_window_h hours (excluding current buy tick, which is
        # already appended to hist)
        prior = hist_list[-(self.stab_window_h + 1):-1]
        if not prior:
            return False
        floor = min(prior)
        if floor <= 0:
            return False
        return price <= floor * (1.0 + self.stab_tol)

    def _avg_lc(self, ea_id: int) -> float:
        lc_hist = self._lc_history.get(ea_id)
        if not lc_hist:
            return 0.0
        tail = list(lc_hist)[-self.lc_window_h:]
        if not tail:
            return 0.0
        return sum(tail) / len(tail)

    def record_lc(self, ea_id: int, lc: float):
        self._lc_history[ea_id].append(lc)

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

        if self._first_ts is None:
            self._first_ts = ts_clean

        # Pull listing counts for this tick if provided by engine
        hour_ts = ts_clean.replace(minute=0, second=0, microsecond=0)

        # --- Append history + exits ---
        for ea_id, price in ticks:
            self._history[ea_id].append(price)
            # Try engine-provided listing count
            lc_val = self._listing_counts.get((ea_id, hour_ts))
            if lc_val is None:
                # fallback: also check with tzinfo stripped variants
                lc_val = self._listing_counts.get((ea_id, timestamp.replace(minute=0, second=0, microsecond=0)))
            if lc_val is not None:
                self._lc_history[ea_id].append(float(lc_val))

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
            if buy_price > 0 and price <= buy_price * (1.0 - self.hard_stop):
                sell = True
                stopped = True
            elif hold_hours >= self.max_hold_h:
                sell = True
            elif smooth > 0 and buy_price > 0:
                smooth_pct = (smooth - buy_price) / buy_price
                if smooth_pct >= self.profit_target:
                    sell = True
                elif smooth_pct <= -self.smoothed_stop:
                    sell = True
                    stopped = True

            if sell:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._buy_ts.pop(ea_id, None)
                if stopped:
                    self._stopped_until[ea_id] = ts_clean + timedelta(hours=self.stop_cooldown_h)

        # --- Burn-in ---
        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        # --- Entry candidates ---
        candidates: list[tuple[int, int, float]] = []
        for ea_id, price in ticks:
            if ea_id not in self._whitelist_ids:
                continue
            if portfolio.holdings(ea_id) > 0:
                continue

            cooldown_end = self._stopped_until.get(ea_id)
            if cooldown_end and ts_clean < cooldown_end:
                continue

            hist = self._history[ea_id]
            if len(hist) < self.dd_window_h:
                continue

            smooth = self._smooth(hist)
            if smooth <= 0 or self._is_outlier(price, smooth):
                continue

            if not (self.min_price <= price <= self.max_price):
                continue

            # Age gate
            created = self._created_at.get(ea_id)
            if created:
                cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
                age_days = (ts_clean - cr_clean).days
                if age_days < self.min_age_days:
                    continue

            hist_list = list(hist)

            # 72h drawdown gate (cheapness)
            dd_window = hist_list[-self.dd_window_h:]
            dd_max = max(dd_window)
            if dd_max <= 0:
                continue
            drawdown = (dd_max - price) / dd_max
            if drawdown < self.drawdown_min:
                continue

            # Stabilization: buy must be local bottom of last 6h
            if not self._is_local_bottom(hist_list, price):
                continue

            # Liquidity: thick listings
            avg_lc = self._avg_lc(ea_id)
            if avg_lc < self.min_lc_avg:
                continue

            candidates.append((ea_id, price, drawdown))

        if not candidates:
            return signals
        if len(portfolio.positions) >= self.max_positions:
            return signals

        # Prefer deepest 72h drawdown (biggest reversion room)
        candidates.sort(key=lambda x: -x[2])

        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                p = next((pp for eid, pp in ticks if eid == s.ea_id), 0)
                sell_rev += (p * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

        open_slots = self.max_positions - len(portfolio.positions)
        buys_made = 0
        for ea_id, price, _dd in candidates:
            if buys_made >= open_slots:
                break
            if available <= 0 or price <= 0:
                break
            qty_budget = self.per_slot_budget // price
            qty = min(self.qty_cap, qty_budget, available // price)
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
            "outlier_tol": 0.10,
            "dd_window_h": 72,
            "drawdown_min": 0.30,
            "stab_window_h": 6,
            "stab_tol": 0.0,
            "lc_window_h": 24,
            "min_lc_avg": 20.0,
            "min_price": 13000,
            "max_price": 80000,
            "profit_target": 0.25,
            "hard_stop": 0.15,
            "smoothed_stop": 0.12,
            "max_hold_h": 120,
            "stop_cooldown_h": 48,
            "max_positions": 8,
            "per_slot_budget": 125_000,
            "qty_cap": 50,
            "min_age_days": 7,
            "burn_in_h": 72,
        }]
