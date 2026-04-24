"""daily_trend_dip_v1 — DAILY-bar regime detection (iter 71).

Hypothesis:
    Hourly signals can't tell when a fall is over because hour-level
    median-vs-max loader noise swamps the signal (the killer of every
    hourly-shape gate so far). DAILY-bar signals smooth that noise.

EDA on daily_listing_summaries (2026-04-12 .. 2026-04-24, 12d window),
restricted to whitelist (rating 86-91 + repeater card_types) and liquid
(sph >= 2). 1,120 (ea, day) feature rows analyzed.

Realistic-execution analysis (BUY@max(hour) at next-day 00 UTC, SELL@min
across next 96h, EA tax 5%):
    - Base rate net_roi >= 20%: 23.0%
    - Mean net ROI: +8.9%

Top gate: 3-day trend (close_today / close_3d_ago - 1) <= -0.10
    fires=311, precision(>=20% net)=35.4%, mean_roi=+16.4%, cont=92.6%

Combo: trend_3d <= -0.05 AND op_demand_ratio (op_sold@10 / 7d-median) >= 1.5
    fires=126, precision=30.2%, mean_roi=+12.8%, cont=92.9%

Note: continuation rate of ~85% is the universal pessimistic-loader-drag
floor (any 24h window has min ~5-10% below max naturally). The gate's
edge is in the +16% MEAN ROI — that's positive realized expectancy.

Strategy:
    1. Once per day at FIRE_HOUR_UTC (00:00 UTC by default), examine
       each whitelist card and compute its daily features.
    2. If trend_3d <= trend_max AND (optional) volume gate fires, BUY
       a basket of the most "extreme dip" cards (most negative trend_3d).
    3. Hold 96h. Exit on profit target, smoothed stop, or max hold.

Cards must be:
    - whitelist (rating 86-91 + repeater card_types)
    - liquid (we rely on engine's --min-sph filter, but also gate by hist length)
    - price band $11k - $100k
"""
from __future__ import annotations

import logging
import statistics
import threading
from collections import defaultdict, deque
from datetime import date, datetime, timedelta

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


# Process-level caches (multiple instances share)
_DAILY_CACHE: tuple[dict, dict] | None = None  # (totals_by_ea_date, op10_by_ea_date)
_DAILY_LOCK = threading.Lock()
_ATTRS_CACHE: dict[int, tuple[int, str]] | None = None
_ATTRS_LOCK = threading.Lock()


def _load_daily_sync() -> tuple[dict, dict]:
    """Load (totals, op@10) keyed (ea_id, date_str)."""
    global _DAILY_CACHE
    with _DAILY_LOCK:
        if _DAILY_CACHE is not None:
            return _DAILY_CACHE
        import asyncio
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from src.config import DATABASE_URL

        async def _run():
            eng = create_async_engine(DATABASE_URL, pool_size=1)
            async with eng.connect() as c:
                r = await c.execute(text(
                    "SELECT ea_id, date, margin_pct, total_sold_count, "
                    "total_listed_count, op_sold_count, op_listed_count "
                    "FROM daily_listing_summaries WHERE margin_pct IN (3, 10) "
                    "ORDER BY ea_id, date"
                ))
                totals: dict = defaultdict(dict)
                op10: dict = defaultdict(dict)
                for row in r.fetchall():
                    ea, d, mp, ts, tl, os_, ol = row
                    ds = d if isinstance(d, str) else d.strftime("%Y-%m-%d")
                    ea = int(ea)
                    totals[ea][ds] = (int(ts or 0), int(tl or 0))
                    if int(mp) == 10:
                        op10[ea][ds] = (int(os_ or 0), int(ol or 0))
            await eng.dispose()
            return dict(totals), dict(op10)

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

        if "err" in holder:
            logger.error(f"daily_trend_dip_v1: DB load failed: {holder['err']}")
            _DAILY_CACHE = ({}, {})
        else:
            _DAILY_CACHE = holder.get("data", ({}, {}))
        return _DAILY_CACHE


def _load_attrs_sync() -> dict[int, tuple[int, str]]:
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
    return rating in _WHITELIST_RATINGS and ctype in _WHITELIST_CARD_TYPES


class DailyTrendDipV1Strategy(Strategy):
    name = "daily_trend_dip_v1"

    def __init__(self, params: dict):
        self.params = params

        # Fire schedule
        self.fire_hour_utc: int = params.get("fire_hour_utc", 0)
        # Daily gates
        self.trend_max: float = params.get("trend_max", -0.05)  # close_today/close_3d_ago - 1
        self.use_op_demand_gate: bool = params.get("use_op_demand_gate", True)
        self.op_demand_min: float = params.get("op_demand_min", 1.5)
        self.lookback_days: int = params.get("lookback_days", 7)
        # Price band
        self.min_price: int = params.get("min_price", 11000)
        self.max_price: int = params.get("max_price", 100000)
        # Hourly smoothing
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.08)
        # Exits
        self.profit_target: float = params.get("profit_target", 0.20)
        self.smoothed_stop: float = params.get("smoothed_stop", 0.10)
        self.hard_stop: float = params.get("hard_stop", 0.15)
        self.max_hold_h: int = params.get("max_hold_h", 96)
        # Sizing
        self.basket_size: int = params.get("basket_size", 6)
        self.qty_cap: int = params.get("qty_cap", 8)
        self.notional_per_trade: int = params.get("notional_per_trade", 100_000)
        self.max_positions: int = params.get("max_positions", 12)
        # Universe gates
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 96)

        # Load DB caches
        self._daily_t, self._daily_op10 = _load_daily_sync()
        self._attrs = _load_attrs_sync()
        self._whitelist_ids: set[int] = {
            ea for ea, attrs in self._attrs.items() if _in_whitelist(attrs)
        }
        logger.info(
            f"daily_trend_dip_v1: daily totals for {len(self._daily_t)}, "
            f"op@10 for {len(self._daily_op10)}, whitelist {len(self._whitelist_ids)}"
        )

        # Per-card hourly history (for daily close + smoothed exits)
        hist_len = max(self.lookback_days * 24 + 24, 168)
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        # Daily close lookups built from hourly: {ea_id: {ds: last_median}}
        self._daily_close: dict[int, dict[str, int]] = defaultdict(dict)

        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._created_at: dict[int, datetime] = {}
        self._first_ts: datetime | None = None
        # Per-day fire tracking so we only fire once per day
        self._last_fire_day: str | None = None

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

    def _trend_3d(self, ea_id: int, today_str: str) -> float | None:
        """3-day price trend from daily close."""
        closes = self._daily_close.get(ea_id, {})
        today_close = closes.get(today_str)
        if today_close is None or today_close <= 0:
            return None
        d_today = datetime.strptime(today_str, "%Y-%m-%d")
        d_3 = (d_today - timedelta(days=3)).strftime("%Y-%m-%d")
        close_3 = closes.get(d_3)
        if close_3 is None or close_3 <= 0:
            return None
        return today_close / close_3 - 1.0

    def _op_demand_ratio(self, ea_id: int, today_str: str) -> float:
        """Today's op_sold@10 / median(prior 7d op_sold@10)."""
        op = self._daily_op10.get(ea_id)
        if not op:
            return 0.0
        today = op.get(today_str)
        if not today:
            return 0.0
        today_sold = today[0]
        d_today = datetime.strptime(today_str, "%Y-%m-%d")
        prior_sold = []
        for back in range(1, self.lookback_days + 1):
            d_prev = (d_today - timedelta(days=back)).strftime("%Y-%m-%d")
            row = op.get(d_prev)
            if row is not None:
                prior_sold.append(row[0])
        if not prior_sold:
            return 0.0
        med_prior = statistics.median(prior_sold)
        if med_prior <= 0:
            # avoid div0; return today_sold (flag as anomaly)
            return float(today_sold) if today_sold > 0 else 0.0
        return today_sold / med_prior

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp
        if self._first_ts is None:
            self._first_ts = ts_clean

        # Append history + update daily_close (last median per day)
        ts_day = ts_clean.strftime("%Y-%m-%d")
        for ea_id, price in ticks:
            self._history[ea_id].append(price)
        # Update daily_close from current smoothed price (live)
        for ea_id, price in ticks:
            sm = self._smooth(self._history[ea_id])
            if sm > 0:
                self._daily_close[ea_id][ts_day] = sm

        # ---- Exits ----
        for ea_id, price in ticks:
            holding = portfolio.holdings(ea_id)
            if holding <= 0:
                continue
            buy_price = self._buy_prices.get(ea_id, price)
            buy_ts = self._buy_ts.get(ea_id, ts_clean)
            bt_clean = buy_ts.replace(tzinfo=None) if buy_ts.tzinfo else buy_ts
            hold_hours = (ts_clean - bt_clean).total_seconds() / 3600

            sell = False
            # Hard stop on tick (loader pessimism)
            if buy_price > 0 and price <= buy_price * (1.0 - self.hard_stop):
                sell = True
            elif hold_hours >= self.max_hold_h:
                sell = True
            else:
                sm = self._smooth(self._history[ea_id])
                if sm > 0 and buy_price > 0:
                    pct = (sm - buy_price) / buy_price
                    if pct >= self.profit_target:
                        sell = True
                    elif pct <= -self.smoothed_stop:
                        sell = True

            if sell:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._buy_ts.pop(ea_id, None)

        # ---- Burn-in ----
        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        # ---- Fire only once per day at FIRE_HOUR_UTC ----
        if ts_clean.hour != self.fire_hour_utc:
            return signals
        if self._last_fire_day == ts_day:
            return signals
        # Mark fire-day attempt regardless of whether we buy
        self._last_fire_day = ts_day

        if len(portfolio.positions) >= self.max_positions:
            return signals

        # ---- Build daily-gated candidates ----
        candidates: list[tuple[int, int, float]] = []  # (ea_id, price, trend_3d)
        for ea_id, price in ticks:
            if ea_id not in self._whitelist_ids:
                continue
            if portfolio.holdings(ea_id) > 0:
                continue
            if not (self.min_price <= price <= self.max_price):
                continue
            sm = self._smooth(self._history[ea_id])
            if sm <= 0 or self._is_outlier(price, sm):
                continue
            if not (self.min_price <= sm <= self.max_price):
                continue
            # Age gate
            created = self._created_at.get(ea_id)
            if created:
                cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
                if (ts_clean - cr_clean).days < self.min_age_days:
                    continue

            # Daily gates
            t3 = self._trend_3d(ea_id, ts_day)
            if t3 is None or t3 > self.trend_max:
                continue
            if self.use_op_demand_gate:
                od = self._op_demand_ratio(ea_id, ts_day)
                if od < self.op_demand_min:
                    continue

            candidates.append((ea_id, price, t3))

        if not candidates:
            return signals

        # Sort by most negative trend (deepest dips first)
        candidates.sort(key=lambda x: x[2])
        candidates = candidates[:self.basket_size]

        # Estimate available cash w/ pending sell rev
        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                p = next((pp for eid, pp in ticks if eid == s.ea_id), 0)
                sell_rev += (p * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

        open_slots = self.max_positions - len(portfolio.positions)
        buys_made = 0
        for ea_id, price, _t3 in candidates:
            if buys_made >= open_slots:
                break
            if available <= 0 or price <= 0:
                break
            target_qty = max(1, self.notional_per_trade // price)
            qty = min(self.qty_cap, target_qty, available // price)
            if qty <= 0:
                continue
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
            "fire_hour_utc": 0,
            "trend_max": -0.05,
            "use_op_demand_gate": True,
            "op_demand_min": 1.5,
            "lookback_days": 7,
            "min_price": 11000,
            "max_price": 100000,
            "smooth_window_h": 3,
            "outlier_tol": 0.08,
            "profit_target": 0.20,
            "smoothed_stop": 0.10,
            "hard_stop": 0.15,
            "max_hold_h": 96,
            "basket_size": 6,
            "qty_cap": 8,
            "notional_per_trade": 100_000,
            "max_positions": 12,
            "min_age_days": 7,
            "burn_in_h": 96,
        }]
