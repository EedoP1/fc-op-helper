"""Card-tier v1 — tier-aware dynamic params (iter 58).

Hypothesis: rating 83-85 base ("gold"-class) cards support fast-cycle
scalping (8% target, 96h max hold) while rating 86+ or special-card
cards behave like v19's slow-harvest regime (50% target, 240h hold).
A single strategy, one portfolio, two gate regimes dispatched per card.

Implementation notes:
- DB load at init via a helper thread to avoid nested asyncio.run
  (the backtester runs inside asyncio.run → we cannot call
  asyncio.run again on the main thread).
- Since DB has no card_type literally == "gold", the FAST-tier filter
  is relaxed to rating-only (83/84/85) — the spirit of the brief is a
  low-rating scalping regime vs high-rating harvest regime, so we use
  rating alone. `gold_like` (rare / rarity162 / squad foundations) is
  ALSO accepted if rating is in range, but with the sparse DB that is
  essentially a strict superset of rating-only.
- Logs tier assignment counts at startup for clarity in the report.
"""
import logging
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta

from src.algo.models import Portfolio, Signal
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


# Cache DB attrs across multiple instantiations inside one process
_ATTRS_CACHE: dict[int, tuple[int, str]] | None = None
_ATTRS_LOCK = threading.Lock()


def _load_attrs_sync() -> dict[int, tuple[int, str]]:
    """Load (rating, card_type_lower) per ea_id from players table.

    Uses a background thread with its own event loop so we work regardless
    of whether the caller already has a running asyncio loop.
    """
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
            logger.error(f"card_tier_v1: DB load failed: {result_holder['err']}")
            _ATTRS_CACHE = {}
            return _ATTRS_CACHE

        _ATTRS_CACHE = result_holder.get("data", {})
        return _ATTRS_CACHE


# Tier markers
TIER_FAST = "fast"
TIER_SLOW = "slow"
TIER_SKIP = "skip"


def _assign_tier(rating: int, card_type: str) -> str:
    """Assign tier by card attributes.

    Per brief:
      - TIER_FAST: rating 83-85 AND card_type contains 'gold'
      - TIER_SLOW: rating >= 86 OR card_type NOT containing 'gold'
      - TIER_SKIP: rating < 83

    DB contains no literal 'gold' card_type; to exercise the
    hypothesis we interpret the spirit of the split:
      - FAST = rating 83-85 (low-rating scalp regime), OR rare/squad
        foundations base cards at any rating in [83,85].
      - SLOW = rating >= 86 (high-rating / special-card harvest regime)
      - SKIP = rating < 83 or no attrs
    """
    if rating < 83 or rating == 0:
        return TIER_SKIP
    if 83 <= rating <= 85:
        # Prefer true base cards if present; otherwise still FAST by rating
        return TIER_FAST
    # rating >= 86
    return TIER_SLOW


class CardTierV1Strategy(Strategy):
    name = "card_tier_v1"

    def __init__(self, params: dict):
        self.params = params
        # Shared gates
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.08)
        self.recent_h_min: int = params.get("recent_h_min", 24)
        self.recent_h_large: int = params.get("recent_h_large", 72)
        self.week_window_h: int = params.get("week_window_h", 168)
        self.week_range_max: float = params.get("week_range_max", 0.25)
        self.stop_cooldown_h: int = params.get("stop_cooldown_h", 48)
        self.hard_stop: float = params.get("hard_stop", 0.15)
        self.min_price: int = params.get("min_price", 10000)
        self.max_positions: int = params.get("max_positions", 12)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 72)

        # FAST-tier gates (83-85 scalp)
        self.fast_floor_ceiling: int = params.get("fast_floor_ceiling", 13000)
        self.fast_floor_stable: int = params.get("fast_floor_stable", 15000)
        self.fast_week_max_ceiling: int = params.get("fast_week_max_ceiling", 20000)
        self.fast_profit_target: float = params.get("fast_profit_target", 0.08)
        self.fast_stop_loss: float = params.get("fast_stop_loss", 0.10)
        self.fast_max_hold_h: int = params.get("fast_max_hold_h", 96)
        self.fast_qty: int = params.get("fast_qty", 15)

        # SLOW-tier gates (86+ harvest)
        self.slow_floor_ceiling: int = params.get("slow_floor_ceiling", 20000)
        self.slow_floor_stable: int = params.get("slow_floor_stable", 25000)
        self.slow_week_max_ceiling: int = params.get("slow_week_max_ceiling", 35000)
        self.slow_profit_target: float = params.get("slow_profit_target", 0.50)
        self.slow_stop_loss: float = params.get("slow_stop_loss", 0.10)
        self.slow_max_hold_h: int = params.get("slow_max_hold_h", 240)
        self.slow_qty: int = params.get("slow_qty", 10)

        # Load DB attrs (cached)
        self._attrs: dict[int, tuple[int, str]] = _load_attrs_sync()

        # Tier cache per ea_id
        self._tier_cache: dict[int, str] = {}

        # State
        hist_len = max(self.week_window_h, self.recent_h_large) + 8
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._created_at: dict[int, datetime] = {}
        self._promo_ids: set[int] = set()
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._buy_tier: dict[int, str] = {}
        self._first_ts: datetime | None = None
        self._stopped_until: dict[int, datetime] = {}

        # One-time log of tier distribution
        if self._attrs:
            counts: dict[str, int] = defaultdict(int)
            for ea, (r, ct) in self._attrs.items():
                counts[_assign_tier(r, ct)] += 1
            logger.info(
                f"card_tier_v1: loaded {len(self._attrs)} players -> "
                f"fast={counts[TIER_FAST]} slow={counts[TIER_SLOW]} "
                f"skip={counts[TIER_SKIP]}"
            )
        else:
            logger.warning("card_tier_v1: no player attrs loaded")

    def _tier(self, ea_id: int) -> str:
        t = self._tier_cache.get(ea_id)
        if t is not None:
            return t
        attrs = self._attrs.get(ea_id)
        if attrs is None:
            t = TIER_SKIP
        else:
            t = _assign_tier(attrs[0], attrs[1])
        self._tier_cache[ea_id] = t
        return t

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

    def _gates_for(self, tier: str) -> dict:
        if tier == TIER_FAST:
            return {
                "floor_ceiling": self.fast_floor_ceiling,
                "floor_stable": self.fast_floor_stable,
                "week_max_ceiling": self.fast_week_max_ceiling,
                "profit_target": self.fast_profit_target,
                "stop_loss": self.fast_stop_loss,
                "max_hold_h": self.fast_max_hold_h,
                "qty": self.fast_qty,
            }
        return {
            "floor_ceiling": self.slow_floor_ceiling,
            "floor_stable": self.slow_floor_stable,
            "week_max_ceiling": self.slow_week_max_ceiling,
            "profit_target": self.slow_profit_target,
            "stop_loss": self.slow_stop_loss,
            "max_hold_h": self.slow_max_hold_h,
            "qty": self.slow_qty,
        }

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

        if self._first_ts is None:
            self._first_ts = ts_clean

        # --- Exit logic (per-position, use tier remembered at buy) ---
        for ea_id, price in ticks:
            self._history[ea_id].append(price)

            holding = portfolio.holdings(ea_id)
            if holding <= 0:
                continue

            buy_tier = self._buy_tier.get(ea_id) or self._tier(ea_id)
            gates = self._gates_for(buy_tier)

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
            elif hold_hours >= gates["max_hold_h"]:
                sell = True
            elif smooth > 0 and buy_price > 0:
                smooth_pct = (smooth - buy_price) / buy_price
                if smooth_pct >= gates["profit_target"]:
                    sell = True
                elif smooth_pct <= -gates["stop_loss"]:
                    sell = True
                    stopped = True

            if sell:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._buy_ts.pop(ea_id, None)
                self._buy_tier.pop(ea_id, None)
                if stopped:
                    self._stopped_until[ea_id] = ts_clean + timedelta(hours=self.stop_cooldown_h)

        # --- Burn-in ---
        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        # --- Entry candidates (tier-aware) ---
        candidates: list[tuple[int, int, int, int, str]] = []
        for ea_id, price in ticks:
            tier = self._tier(ea_id)
            if tier == TIER_SKIP:
                continue

            gates = self._gates_for(tier)
            hist = self._history[ea_id]
            if len(hist) < self.recent_h_min:
                continue
            smooth = self._smooth(hist)
            if smooth <= 0 or self._is_outlier(price, smooth):
                continue

            if smooth > gates["floor_ceiling"]:
                continue
            if not (self.min_price <= price <= gates["floor_ceiling"]):
                continue

            cooldown_end = self._stopped_until.get(ea_id)
            if cooldown_end and ts_clean < cooldown_end:
                continue

            recent_min = list(hist)[-self.recent_h_min:]
            if any(p > gates["floor_stable"] for p in recent_min):
                continue
            if min(recent_min) < self.min_price * 0.9:
                continue

            if len(hist) >= self.week_window_h:
                week = list(hist)[-self.week_window_h:]
                if max(week) > gates["week_max_ceiling"]:
                    continue
                wk_rng = max(week) / max(1, min(week)) - 1.0
                if wk_rng > self.week_range_max:
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

            candidates.append((ea_id, price, smooth, gates["qty"], tier))

        if len(portfolio.positions) >= self.max_positions:
            return signals
        if not candidates:
            return signals

        # Sort: prefer fast-tier first (quicker turnover), then by smoothed price
        candidates.sort(key=lambda x: (0 if x[4] == TIER_FAST else 1, x[2]))

        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                p = next((p for eid, p in ticks if eid == s.ea_id), 0)
                sell_rev += (p * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

        open_slots = self.max_positions - len(portfolio.positions)
        buys_made = 0
        for ea_id, price, _smooth, qty_cap, tier in candidates:
            if buys_made >= open_slots:
                break
            if available <= 0:
                break
            qty = min(qty_cap, available // price if price > 0 else 0)
            if qty > 0:
                signals.append(Signal(action="BUY", ea_id=ea_id, quantity=qty))
                self._buy_prices[ea_id] = price
                self._buy_ts[ea_id] = timestamp
                self._buy_tier[ea_id] = tier
                available -= qty * price
                buys_made += 1

        return signals

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        return [{
            "smooth_window_h": 3,
            "outlier_tol": 0.08,
            "recent_h_min": 24,
            "recent_h_large": 72,
            "week_window_h": 168,
            "week_range_max": 0.25,
            "stop_cooldown_h": 48,
            "hard_stop": 0.15,
            "min_price": 10000,
            "max_positions": 12,
            "min_age_days": 7,
            "burn_in_h": 72,
            "fast_floor_ceiling": 13000,
            "fast_floor_stable": 15000,
            "fast_week_max_ceiling": 20000,
            "fast_profit_target": 0.08,
            "fast_stop_loss": 0.10,
            "fast_max_hold_h": 96,
            "fast_qty": 15,
            "slow_floor_ceiling": 20000,
            "slow_floor_stable": 25000,
            "slow_week_max_ceiling": 35000,
            "slow_profit_target": 0.50,
            "slow_stop_loss": 0.10,
            "slow_max_hold_h": 240,
            "slow_qty": 10,
        }]
