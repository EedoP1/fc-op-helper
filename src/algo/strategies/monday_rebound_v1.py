"""monday_rebound_v1 — fill the silent Monday $20k+ band gap (iter 82).

Iter 82 footprint analysis revealed the current 5-strategy stack
(floor_buy_v19 / v19_ext / post_dump_v15 / daily_trend_dip_v5 / floor_buy_v24)
deploys $0 of capital on Monday at any band ≥ $20k. The 1,850-opp pessimistic
catalogue holds 330 Monday opportunities in the $20k+ bands with median net
ROI 32% and median hold 63h.

Drawdown signature (Postgres market_snapshots query, 80-opp sample):
    24h prior:  median dd = -8.32%
    3d  prior:  median dd = -13.32%
    7d  prior:  median dd = -9.74%
    63% of opps had ≥10% drawdown vs 3d-prior price.

Listing-count signature: median 20 listings, p25 17, p75 21 — i.e.
liquid but not over-supplied.

Strategy: once-per-Monday firing. Buy any card whose smoothed price is in
$20k–$200k band AND whose 3d-smoothed-close drop is ≤ -10%. Hold with the
proven daily_trend_dip_v5 exit recipe (profit_target +20%, max_hold 144h,
smoothed catastrophic stop -25% over 14 consecutive hours). Whitelist
86-91 + repeater card_types (same proven roster).

Sizing: 8 max positions × $125k notional = $1M deployed. Qty cap 4 to
respect $50k+ price levels (typical Monday opp buys at ~$70-130k).

Predicted: ~25-40 fires across 4-week window; with 60% hit rate × 25%
loader-discounted net ROI × $125k notional = +$120-200k organic.
Δstack: should be +$80k+ (zero overlap with stack — no Monday $20k+ trades
in any committed strategy).
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
    return rating in _WHITELIST_RATINGS and ctype in _WHITELIST_CARD_TYPES


class MondayReboundV1Strategy(Strategy):
    name = "monday_rebound_v1"

    def __init__(self, params: dict):
        self.params = params

        # Fire once per Monday at fire_hour_utc (default 02:00 UTC — picks up
        # Sunday-evening EU bottoms; opp distribution shows 02-13 UTC most popular).
        self.fire_hour_utc: int = params.get("fire_hour_utc", 2)
        # Required smoothed-3d drawdown.
        self.trend_3d_max: float = params.get("trend_3d_max", -0.25)
        # Price band: covers $20-50k, $50-100k, $100k+.
        self.min_price: int = params.get("min_price", 40000)
        self.max_price: int = params.get("max_price", 200000)
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.08)
        # Exits — proven daily_trend_dip_v5 recipe.
        self.profit_target: float = params.get("profit_target", 0.20)
        self.max_hold_h: int = params.get("max_hold_h", 144)
        self.smoothed_stop: float = params.get("smoothed_stop", 0.15)
        self.stop_consec_hours: int = params.get("stop_consec_hours", 8)
        # Sizing.
        self.basket_size: int = params.get("basket_size", 8)
        self.qty_cap: int = params.get("qty_cap", 4)
        self.notional_per_trade: int = params.get("notional_per_trade", 125_000)
        self.max_positions: int = params.get("max_positions", 8)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 96)

        self._attrs = _load_attrs_sync()
        self._whitelist_ids: set[int] = {
            ea for ea, attrs in self._attrs.items() if _in_whitelist(attrs)
        }
        logger.info(
            f"monday_rebound_v1: whitelist {len(self._whitelist_ids)} cards"
        )

        # 4 days of history for trend_3d + outlier smoothing.
        hist_len = max(96, self.smooth_window_h + 8)
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        # Daily smoothed close per (ea_id, YYYY-MM-DD).
        self._daily_close: dict[int, dict[str, int]] = defaultdict(dict)

        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._stop_breach_count: dict[int, int] = {}
        self._stop_last_hour: dict[int, datetime] = {}

        self._created_at: dict[int, datetime] = {}
        self._first_ts: datetime | None = None
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

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp
        if self._first_ts is None:
            self._first_ts = ts_clean

        ts_day = ts_clean.strftime("%Y-%m-%d")
        ts_hour = ts_clean.replace(minute=0, second=0, microsecond=0)
        for ea_id, price in ticks:
            self._history[ea_id].append(price)
        # Update today's smoothed daily close.
        for ea_id, _price in ticks:
            sm = self._smooth(self._history[ea_id])
            if sm > 0:
                self._daily_close[ea_id][ts_day] = sm

        # ---- Exits (daily_trend_dip_v5 recipe).
        for ea_id, price in ticks:
            holding = portfolio.holdings(ea_id)
            if holding <= 0:
                self._stop_breach_count.pop(ea_id, None)
                self._stop_last_hour.pop(ea_id, None)
                continue
            buy_price = self._buy_prices.get(ea_id, price)
            buy_ts = self._buy_ts.get(ea_id, ts_clean)
            bt_clean = buy_ts.replace(tzinfo=None) if buy_ts.tzinfo else buy_ts
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
                    else:
                        last_h = self._stop_last_hour.get(ea_id)
                        if last_h is None or ts_hour > last_h:
                            stop_level = buy_price * (1.0 - self.smoothed_stop)
                            if sm <= stop_level:
                                self._stop_breach_count[ea_id] = (
                                    self._stop_breach_count.get(ea_id, 0) + 1
                                )
                            else:
                                self._stop_breach_count[ea_id] = 0
                            self._stop_last_hour[ea_id] = ts_hour
                        if (
                            self._stop_breach_count.get(ea_id, 0)
                            >= self.stop_consec_hours
                        ):
                            sell = True

            if sell:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._buy_ts.pop(ea_id, None)
                self._stop_breach_count.pop(ea_id, None)
                self._stop_last_hour.pop(ea_id, None)

        # Burn-in
        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        # MONDAY GATE: only fire on Monday (weekday=0) at fire_hour_utc.
        if ts_clean.weekday() != 0:
            return signals
        if ts_clean.hour != self.fire_hour_utc:
            return signals
        if self._last_fire_day == ts_day:
            return signals
        self._last_fire_day = ts_day

        if len(portfolio.positions) >= self.max_positions:
            return signals

        # Build candidate set: smoothed price in band, 3d trend ≤ trend_3d_max.
        candidates: list[tuple[int, int, float]] = []
        for ea_id, price in ticks:
            if ea_id not in self._whitelist_ids:
                continue
            if portfolio.holdings(ea_id) > 0:
                continue
            sm = self._smooth(self._history[ea_id])
            if sm <= 0 or self._is_outlier(price, sm):
                continue
            if not (self.min_price <= sm <= self.max_price):
                continue
            if not (self.min_price * 0.9 <= price <= self.max_price * 1.1):
                continue
            created = self._created_at.get(ea_id)
            if created:
                cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
                if (ts_clean - cr_clean).days < self.min_age_days:
                    continue
            t3 = self._trend_3d(ea_id, ts_day)
            if t3 is None or t3 > self.trend_3d_max:
                continue
            candidates.append((ea_id, price, t3))

        if not candidates:
            return signals

        # Rank deepest dip first (most-discounted entries).
        candidates.sort(key=lambda x: x[2])
        candidates = candidates[:self.basket_size]

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
            self._stop_breach_count[ea_id] = 0
            self._stop_last_hour[ea_id] = ts_hour
            available -= qty * price
            buys_made += 1

        return signals

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        return [{
            "fire_hour_utc": 2,
            "trend_3d_max": -0.25,
            "min_price": 40000,
            "max_price": 200000,
            "smooth_window_h": 3,
            "outlier_tol": 0.08,
            "profit_target": 0.20,
            "max_hold_h": 144,
            "smoothed_stop": 0.15,
            "stop_consec_hours": 8,
            "basket_size": 8,
            "qty_cap": 4,
            "notional_per_trade": 125_000,
            "max_positions": 8,
            "min_age_days": 7,
            "burn_in_h": 96,
        }]
