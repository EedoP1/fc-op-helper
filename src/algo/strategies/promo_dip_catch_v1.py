"""promo_dip_catch_v1 — orthogonal premium-card promo dip strategy (iter 78).

Pre-analysis (scripts/iter78_promo_dip_analysis.py, 2026-04-23 corpus):
    Whitelist: 308 cards (rating 88-91, card_type IN promo-fresh set)
    Daily 12:00 UTC fire gate:
        drawdown_from_max_72h >= 0.40 OR drawdown_from_max_168h >= 0.50
        AND lc_avg_24h >= 15 (thick floor — no panic dumping)
    Raw fires: 4157 over 28d window across 174 unique cards
    With 168h-per-card cooldown: 255 fires across 174 cards
    Forward-walk pessimistic PnL (qty cap 8 / $100k notional) on cooldown set:
        win_rate 89.0%   median ROI 22.2%
        full sum (no engine cap)        $5,115,088
        top-60 by ROI (engine-realistic) $2,336,112
    Overlap with daily_trend_dip_v5: 12.1% of fire ea_ids — orthogonal enough
    Overlap with floor_buy_v19:       0.0% — fully orthogonal

Why orthogonal to daily_trend_dip_v5:
    - v5 fires on trend_3d <= -0.05 (mild downtrend) over the cheap+mid bands
      ($<=20k OR $40-60k) with op_demand >= 1.5x.
    - v1 fires on a much DEEPER drawdown (>=40% over 72h or >=50% over 168h)
      with no trend smoothing — we're catching cards that already crashed,
      not cards mid-fall. Listings-floor confirmation (lc_avg_24h >= 15)
      replaces v5's op_demand gate as the "this is real demand, not free-fall"
      signal. Different sub-population: v5 grabs gentle dips; promo_dip
      grabs after-the-crash pulls.

Strategy spec (matches v5/v19 patterns):
    - Daily snapshot at 12:00 UTC (one fire/day, prevents hourly noise).
    - Whitelist promo-fresh 88-91 only.
    - Drawdown gate (chosen 50%/168h primary, 40%/72h fallback).
    - lc24 floor confirmation.
    - 168h cooldown per card (don't re-buy a card we just bought).
    - profit_target +25%, max_hold 168h, NO stop loss (per task spec —
      v2/v5 working pattern of letting losers ride to max_hold).
    - Sizing: 8 slots, $125k notional, qty_cap 50 (engine-spec values).
    - min_age_days 7, burn_in 96h.
"""
from __future__ import annotations

import logging
import statistics
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta

from src.algo.models import Portfolio, Signal
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


_PROMO_FRESH_TYPES = {
    "fut birthday",
    "fantasy ut",
    "fantasy ut hero",
    "future stars",
    "fof: answer the call",
    "star performer",
    "knockout royalty icon",
    "festival of football: captains",
    "ultimate scream",
    "fc pro live",
}
_RATING_LO = 88
_RATING_HI = 91


_ATTRS_CACHE: dict[int, tuple[int, str]] | None = None
_ATTRS_LOCK = threading.Lock()


def _load_attrs_sync() -> dict[int, tuple[int, str]]:
    global _ATTRS_CACHE
    with _ATTRS_LOCK:
        if _ATTRS_CACHE is not None:
            return _ATTRS_CACHE
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine
        from src.config import DATABASE_URL

        async def _run():
            eng = create_async_engine(DATABASE_URL, pool_size=1)
            async with eng.connect() as c:
                r = await c.execute(text(
                    "SELECT ea_id, rating, card_type FROM players"
                ))
                out: dict = {}
                for row in r.fetchall():
                    out[int(row[0])] = (int(row[1] or 0), (row[2] or "").lower())
            await eng.dispose()
            return out

        holder: dict = {}

        def target():
            try:
                import asyncio as _a
                holder["data"] = _a.run(_run())
            except Exception as exc:
                holder["err"] = exc

        t = threading.Thread(target=target, daemon=True)
        t.start()
        t.join()

        _ATTRS_CACHE = holder.get("data", {}) if "err" not in holder else {}
        return _ATTRS_CACHE


def _in_whitelist(attrs: tuple[int, str] | None) -> bool:
    if attrs is None:
        return False
    rating, ctype = attrs
    return _RATING_LO <= rating <= _RATING_HI and ctype in _PROMO_FRESH_TYPES


class PromoDipCatchV1Strategy(Strategy):
    name = "promo_dip_catch_v1"

    def __init__(self, params: dict):
        self.params = params

        self.fire_hour_utc: int = params.get("fire_hour_utc", 12)
        # Drawdown gates (either condition triggers)
        self.dd72_min: float = params.get("dd72_min", 0.40)
        self.dd168_min: float = params.get("dd168_min", 0.50)
        # Listings-floor confirmation (24h avg listings >= floor)
        self.lc24_min: float = params.get("lc24_min", 15.0)
        # Smoothing for entry price assessment
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.08)
        # Price band (engine $125k notional implies usable up to ~$125k)
        self.min_price: int = params.get("min_price", 11000)
        self.max_price: int = params.get("max_price", 125000)
        # Exits
        self.profit_target: float = params.get("profit_target", 0.25)
        self.max_hold_h: int = params.get("max_hold_h", 168)
        # Cooldown per ea_id between consecutive buys
        self.cooldown_h: int = params.get("cooldown_h", 168)
        # Sizing — engine spec (8 slots / $125k / qty 50)
        self.basket_size: int = params.get("basket_size", 6)
        self.qty_cap: int = params.get("qty_cap", 50)
        self.notional_per_trade: int = params.get("notional_per_trade", 125_000)
        self.max_positions: int = params.get("max_positions", 8)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 96)

        self._attrs = _load_attrs_sync()
        self._whitelist_ids: set[int] = {
            ea for ea, attrs in self._attrs.items() if _in_whitelist(attrs)
        }
        logger.info(
            f"promo_dip_catch_v1: whitelist size {len(self._whitelist_ids)} "
            f"(rating {_RATING_LO}-{_RATING_HI} AND promo-fresh card_type)"
        )

        # Need 168h history for the deep-drawdown lookback
        hist_len = max(self.max_hold_h, 168) + 24
        self._history: dict[int, deque] = defaultdict(
            lambda: deque(maxlen=hist_len)
        )
        # Hourly listings count history (24h average for floor check)
        self._lc_history: dict[int, deque] = defaultdict(
            lambda: deque(maxlen=24)
        )
        # Track timestamps so drawdown lookback is wall-clock correct
        self._ts_history: dict[int, deque] = defaultdict(
            lambda: deque(maxlen=hist_len)
        )

        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._last_buy_ts: dict[int, datetime] = {}

        self._created_at: dict[int, datetime] = {}
        self._listing_counts: dict[tuple[int, datetime], int] = {}
        self._first_ts: datetime | None = None
        self._last_fire_day: str | None = None

    def set_created_at_map(self, created_at_map: dict):
        self._created_at = created_at_map

    def set_listing_counts(self, listing_counts: dict):
        # Engine sends a {(ea_id, hour_ts): count} map. Normalize tz so the
        # per-tick lookup matches the strategy's tz-stripped timestamps.
        normalized: dict[tuple[int, datetime], int] = {}
        for (ea_id, ts), count in listing_counts.items():
            ts_clean = ts.replace(tzinfo=None) if ts.tzinfo else ts
            normalized[(ea_id, ts_clean)] = int(count or 0)
        self._listing_counts = normalized

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

    def _drawdown(
        self, ea_id: int, ts_now: datetime, lookback_h: int
    ) -> float | None:
        """Return drawdown_from_max over lookback_h hours."""
        prices = self._history.get(ea_id)
        times = self._ts_history.get(ea_id)
        if not prices or not times or len(prices) < 6:
            return None
        cutoff = ts_now - timedelta(hours=lookback_h)
        max_p = 0
        cur = prices[-1]
        if cur <= 0:
            return None
        # Walk back
        for t, p in zip(reversed(times), reversed(prices)):
            if t < cutoff:
                break
            if p > max_p:
                max_p = p
        if max_p <= 0:
            return None
        return (max_p - cur) / max_p

    def _lc24_avg(self, ea_id: int) -> float:
        h = self._lc_history.get(ea_id)
        if not h:
            return 0.0
        vals = [v for v in h if v > 0]
        if not vals:
            return 0.0
        return sum(vals) / len(vals)

    def on_tick_batch(
        self,
        ticks: list[tuple[int, int]],
        timestamp: datetime,
        portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = (
            timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp
        )
        if self._first_ts is None:
            self._first_ts = ts_clean

        # Update price/ts/lc histories
        ts_hour = ts_clean.replace(minute=0, second=0, microsecond=0)
        for ea_id, price in ticks:
            self._history[ea_id].append(price)
            self._ts_history[ea_id].append(ts_clean)
            lc = self._listing_counts.get((ea_id, ts_hour), 0)
            self._lc_history[ea_id].append(lc)

        # ---- Exits: profit_target (smoothed) + max_hold_h (no stop loss).
        for ea_id, price in ticks:
            holding = portfolio.holdings(ea_id)
            if holding <= 0:
                continue
            buy_price = self._buy_prices.get(ea_id, price)
            buy_ts = self._buy_ts.get(ea_id, ts_clean)
            bt_clean = (
                buy_ts.replace(tzinfo=None) if buy_ts.tzinfo else buy_ts
            )
            hold_hours = (ts_clean - bt_clean).total_seconds() / 3600

            sell = False
            if hold_hours >= self.max_hold_h:
                sell = True
            else:
                sm = self._smooth(self._history[ea_id])
                if sm > 0 and buy_price > 0:
                    pct = (sm - buy_price) / buy_price
                    if pct >= self.profit_target:
                        sell = True

            if sell:
                signals.append(
                    Signal(action="SELL", ea_id=ea_id, quantity=holding)
                )
                self._buy_prices.pop(ea_id, None)
                self._buy_ts.pop(ea_id, None)

        # Burn-in
        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        # Daily fire gate (one fire per day at fire_hour_utc)
        if ts_clean.hour != self.fire_hour_utc:
            return signals
        ts_day = ts_clean.strftime("%Y-%m-%d")
        if self._last_fire_day == ts_day:
            return signals
        self._last_fire_day = ts_day

        if len(portfolio.positions) >= self.max_positions:
            return signals

        # Build candidate list
        candidates: list[tuple[int, int, float]] = []
        for ea_id, price in ticks:
            if ea_id not in self._whitelist_ids:
                continue
            if portfolio.holdings(ea_id) > 0:
                continue
            # Cooldown check
            last_buy = self._last_buy_ts.get(ea_id)
            if last_buy is not None:
                lb_clean = (
                    last_buy.replace(tzinfo=None)
                    if last_buy.tzinfo else last_buy
                )
                if (ts_clean - lb_clean).total_seconds() / 3600 < self.cooldown_h:
                    continue
            if not (self.min_price <= price <= self.max_price):
                continue
            sm = self._smooth(self._history[ea_id])
            if sm <= 0 or self._is_outlier(price, sm):
                continue
            created = self._created_at.get(ea_id)
            if created:
                cr_clean = (
                    created.replace(tzinfo=None)
                    if created.tzinfo else created
                )
                if (ts_clean - cr_clean).days < self.min_age_days:
                    continue

            # Drawdown gate: dd72 OR dd168
            dd72 = self._drawdown(ea_id, ts_clean, 72)
            dd168 = self._drawdown(ea_id, ts_clean, 168)
            if dd72 is None and dd168 is None:
                continue
            if not (
                (dd72 is not None and dd72 >= self.dd72_min)
                or (dd168 is not None and dd168 >= self.dd168_min)
            ):
                continue

            # Listings-floor confirmation
            lc24 = self._lc24_avg(ea_id)
            if lc24 < self.lc24_min:
                continue

            # Rank by deepest drawdown (largest dd168 wins)
            rank_dd = max(dd72 or 0.0, dd168 or 0.0)
            candidates.append((ea_id, price, rank_dd))

        if not candidates:
            return signals

        candidates.sort(key=lambda x: -x[2])  # deepest dip first
        candidates = candidates[: self.basket_size]

        # Cash bookkeeping (sells freed cash within this tick)
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
            target_qty = max(1, self.notional_per_trade // price)
            qty = min(self.qty_cap, target_qty, available // price)
            if qty <= 0:
                continue
            signals.append(
                Signal(action="BUY", ea_id=ea_id, quantity=qty)
            )
            self._buy_prices[ea_id] = price
            self._buy_ts[ea_id] = timestamp
            self._last_buy_ts[ea_id] = timestamp
            available -= qty * price
            buys_made += 1

        return signals

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        return [{
            "fire_hour_utc": 12,
            "dd72_min": 0.40,
            "dd168_min": 0.50,
            "lc24_min": 15.0,
            "smooth_window_h": 3,
            "outlier_tol": 0.08,
            "min_price": 11000,
            "max_price": 125000,
            "profit_target": 0.25,
            "max_hold_h": 168,
            "cooldown_h": 168,
            "basket_size": 6,
            "qty_cap": 50,
            "notional_per_trade": 125_000,
            "max_positions": 8,
            "min_age_days": 7,
            "burn_in_h": 96,
        }]
