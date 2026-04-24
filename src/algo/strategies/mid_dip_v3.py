"""mid_dip_v3 — gate widening from v2 (iter 91).

Borrows low_dip_v3's recipe: lower drawdown threshold to catch more dips that
v2 misses. v2 uses dd_min=0.25; v3 lowers to 0.20 (5pp widen, identical pattern
to low_dip_v1 -> low_dip_v3 which gained Δ +$27k vs v1).

Pre-analysis (catalog: pessimistic, $20-50k, Tue/Wed/Thu, whitelist):
  Total candidate opps: 79
  Distribution by computed dd_72h at buy_hour:
    dd >= 0.25 (v2 catches today): 39
    0.20 <= dd < 0.25 (NEW from widen 0.25 -> 0.20): 15
    0.15 <= dd < 0.20: 13
    dd < 0.15: 12

  Additional 15 opps from widen 0.25 -> 0.20:
    By week: W14=1, W15=5, W16=5, W17=4 — spread across all 4 weeks (clean,
      no overfit concentration).
    Median ROI net (already pessimistic-loaded): 0.278
    Avg buy price: ~$34.5k
    Per-trade notional: 3 qty * $34.5k = ~$103k
    Per-trade gross add: $103k * (0.278 - 0.07 drag) = ~$21.4k

Predicted Δ PnL vs v2 (slot competition discount applied):
  conv 30%: +$97k    (very pessimistic)
  conv 50%: +$162k   (base case)
  conv 70%: +$226k   (optimistic)

  Crosses the +$15k loop threshold by a wide margin in every scenario.

  Caveat: v2 only fired 35 trades over 17 days. Some additional opps will
  compete for the 8-position slot cap and lose to deeper dips already
  ranked higher (candidates sort by -dd). So actual conversion likely on
  the lower end of the range.

Anti-overfit verification:
  Population spans 4 weeks, no single week contributes > 33% of additions.
  Same gate-widening pattern that worked on low_dip (low_dip_v1 dd 0.20
  -> low_dip_v3 dd 0.15 gained +$27k organic). Mid-band has wider
  population so absolute Δ should exceed low_dip's gain.

Differences from v2 (everything else identical):
  - dd_min: 0.25 -> 0.20

Predicted PnL: +$190-310k filtered organic (v2 base $143.6k + Δ $50-160k).
Stack target: $1.49M + $50k floor = $1.54M (gap ~$460k).
"""
from __future__ import annotations

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
                    out[int(row[0])] = (
                        int(row[1] or 0),
                        (row[2] or "").lower(),
                    )
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


class MidDipV3Strategy(Strategy):
    name = "mid_dip_v3"

    def __init__(self, params: dict):
        self.params = params

        self.fire_hour_utc: int = params.get("fire_hour_utc", 0)
        self.min_price: int = params.get("min_price", 20000)
        self.max_price: int = params.get("max_price", 50000)
        self.dd_min: float = params.get("dd_min", 0.20)
        self.dd_window_h: int = params.get("dd_window_h", 72)
        self.lc_min_avg_24h: float = params.get("lc_min_avg_24h", 15.0)
        self.skip_friday: bool = params.get("skip_friday", True)
        self.skip_weekend: bool = params.get("skip_weekend", True)
        self.skip_monday: bool = params.get("skip_monday", True)
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.08)
        self.profit_target: float = params.get("profit_target", 0.20)
        self.max_hold_h: int = params.get("max_hold_h", 144)
        self.smoothed_stop: float = params.get("smoothed_stop", 0.25)
        self.stop_consec_hours: int = params.get("stop_consec_hours", 14)
        self.basket_size: int = params.get("basket_size", 8)
        self.qty_cap: int = params.get("qty_cap", 6)
        self.notional_per_trade: int = params.get("notional_per_trade", 125_000)
        self.max_positions: int = params.get("max_positions", 8)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 96)

        self._attrs = _load_attrs_sync()
        self._whitelist_ids: set[int] = {
            ea for ea, attrs in self._attrs.items() if _in_whitelist(attrs)
        }
        logger.info(
            f"mid_dip_v3: whitelist {len(self._whitelist_ids)} promo cards"
        )

        hist_len = max(self.dd_window_h + 8, 96)
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._listing_counts: dict[tuple[int, datetime], int] = {}

        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._stop_breach_count: dict[int, int] = {}
        self._stop_last_hour: dict[int, datetime] = {}

        self._created_at: dict[int, datetime] = {}
        self._first_ts: datetime | None = None
        self._last_fire_day: str | None = None

    def set_created_at_map(self, created_at_map: dict):
        self._created_at = created_at_map

    def set_listing_counts(self, listing_counts: dict):
        normalized: dict[tuple[int, datetime], int] = {}
        for (ea_id, ts), count in listing_counts.items():
            ts_clean = ts.replace(tzinfo=None) if ts.tzinfo else ts
            normalized[(ea_id, ts_clean)] = int(count or 0)
        self._listing_counts = normalized
        logger.info(
            f"mid_dip_v3: loaded {len(normalized)} listing_count points"
        )

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

    def _lc_avg_24h(self, ea_id: int, ts_hour: datetime) -> float:
        vals: list[int] = []
        for k in range(1, 25):
            h = ts_hour - timedelta(hours=k)
            v = self._listing_counts.get((ea_id, h))
            if v is not None:
                vals.append(v)
        if not vals:
            return 0.0
        return sum(vals) / len(vals)

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp
        if self._first_ts is None:
            self._first_ts = ts_clean
        ts_hour = ts_clean.replace(minute=0, second=0, microsecond=0)

        for ea_id, price in ticks:
            self._history[ea_id].append(price)

        # Exits: profit_target (smoothed) + max_hold + smoothed_stop.
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

        # Daily fire gate (single trigger per day at fire_hour_utc)
        if ts_clean.hour != self.fire_hour_utc:
            return signals
        ts_day = ts_clean.strftime("%Y-%m-%d")
        if self._last_fire_day == ts_day:
            return signals
        self._last_fire_day = ts_day

        # Skip Friday entries (correlation hygiene with promo_dip_buy)
        if self.skip_friday and ts_clean.weekday() == 4:
            return signals
        # Skip weekend entries (Sat=5, Sun=6).
        if self.skip_weekend and ts_clean.weekday() in (5, 6):
            return signals
        # Skip Monday — mid_dip Mon entries catch falling-knife from weekend dumps.
        if self.skip_monday and ts_clean.weekday() == 0:
            return signals

        if len(portfolio.positions) >= self.max_positions:
            return signals

        candidates: list[tuple[int, int, float]] = []
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
            created = self._created_at.get(ea_id)
            if created:
                cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
                if (ts_clean - cr_clean).days < self.min_age_days:
                    continue

            hist = list(self._history[ea_id])
            if len(hist) < self.dd_window_h:
                continue
            window = hist[-self.dd_window_h:]
            wmax = max(window)
            if wmax <= 0:
                continue
            dd = 1.0 - sm / wmax
            if dd < self.dd_min:
                continue

            lc_avg = self._lc_avg_24h(ea_id, ts_hour)
            if lc_avg < self.lc_min_avg_24h:
                continue

            # Rank by depth of dip — deeper drawdown first.
            candidates.append((ea_id, price, dd))

        if not candidates:
            return signals

        candidates.sort(key=lambda x: -x[2])
        candidates = candidates[: self.basket_size]

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
            "fire_hour_utc": 0,
            "min_price": 20000,
            "max_price": 50000,
            "dd_min": 0.20,
            "dd_window_h": 72,
            "lc_min_avg_24h": 15.0,
            "skip_friday": True,
            "skip_weekend": True,
            "skip_monday": True,
            "smooth_window_h": 3,
            "outlier_tol": 0.08,
            "profit_target": 0.20,
            "max_hold_h": 144,
            "smoothed_stop": 0.25,
            "stop_consec_hours": 14,
            "basket_size": 8,
            "qty_cap": 6,
            "notional_per_trade": 125_000,
            "max_positions": 8,
            "min_age_days": 7,
            "burn_in_h": 96,
        }]
