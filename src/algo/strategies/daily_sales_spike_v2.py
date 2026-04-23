"""Daily sales-velocity spike v2 (iter 65).

Hypothesis (continuation of iter 63/64): the next-day alpha from a 2.0x
daily-sales spike is real (median +5.75%, 60.8% of spike-days return >+2%)
but concentrated in the first 12-24h after spike-day close. v1's 48h hold +
6%/-4% exits let the alpha fade before targets triggered, producing -$204k
org at slip=0.0 and -$162k at slip=0.02.

v2 compresses hold + tightens exits to catch alpha BEFORE it decays:
  profit_target 0.06 -> 0.03  (60.8% of spikes already reach +2%)
  stop_loss     0.04 -> 0.025 (tighter for shorter hold)
  max_hold_h    48   -> 12    (stay inside 24h alpha window)

Also ships a param_grid variant at 2.5x threshold (fewer spike-days, stronger
signal) with profit_target 0.04 / stop_loss 0.03 / max_hold 18h.

Shares spike-day cache with v1 via module-level _SALES_CACHE.
"""
from __future__ import annotations

import logging
import threading
from collections import defaultdict, deque
from datetime import date, datetime, timedelta

from src.algo.models import Portfolio, Signal
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


# Cache DB sales across multiple instantiations inside one process
_SALES_CACHE: dict[int, dict[date, int]] | None = None
_SALES_LOCK = threading.Lock()


def _load_sales_sync() -> dict[int, dict[date, int]]:
    """Load {ea_id: {date: total_sold_count}} from daily_listing_summaries.

    Runs DB call in a background thread to avoid clashing with the
    backtester's asyncio loop.
    """
    global _SALES_CACHE
    with _SALES_LOCK:
        if _SALES_CACHE is not None:
            return _SALES_CACHE

        import asyncio
        from sqlalchemy import text
        from sqlalchemy.ext.asyncio import create_async_engine

        from src.config import DATABASE_URL

        async def _run():
            eng = create_async_engine(DATABASE_URL, pool_size=1)
            async with eng.connect() as c:
                r = await c.execute(
                    text(
                        """
                        SELECT ea_id, date, total_sold_count
                        FROM daily_listing_summaries
                        WHERE margin_pct = 3
                        ORDER BY ea_id, date
                        """
                    )
                )
                out: dict[int, dict[date, int]] = defaultdict(dict)
                for row in r.fetchall():
                    ea = int(row[0])
                    d = row[1] if isinstance(row[1], date) else datetime.strptime(
                        row[1], "%Y-%m-%d"
                    ).date()
                    out[ea][d] = int(row[2] or 0)
            await eng.dispose()
            return dict(out)

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
            logger.error(f"daily_sales_spike_v2: DB load failed: {holder['err']}")
            _SALES_CACHE = {}
            return _SALES_CACHE

        _SALES_CACHE = holder.get("data", {})
        return _SALES_CACHE


def _compute_spike_days(
    ea_sales: dict[date, int],
    rolling_window: int,
    spike_mult: float,
    noise_floor: int,
) -> set[date]:
    """Return the set of dates classified as spike days for one ea_id."""
    sorted_dates = sorted(ea_sales.keys())
    spike_days: set[date] = set()
    for idx, d in enumerate(sorted_dates):
        if idx < rolling_window:
            continue
        prior = sorted_dates[idx - rolling_window : idx]
        prior_sales = [ea_sales[p] for p in prior]
        mean_sales = sum(prior_sales) / len(prior_sales)
        if mean_sales < noise_floor:
            continue
        if ea_sales[d] >= spike_mult * mean_sales:
            spike_days.add(d)
    return spike_days


class DailySalesSpikeV2Strategy(Strategy):
    name = "daily_sales_spike_v2"

    def __init__(self, params: dict):
        self.params = params

        # Signal params
        self.rolling_window_d: int = params.get("rolling_window_d", 5)
        self.spike_mult: float = params.get("spike_mult", 2.0)
        self.noise_floor_sales: int = params.get("noise_floor_sales", 5)

        # Floor-band gates
        self.min_price: int = params.get("min_price", 10_000)
        self.max_price: int = params.get("max_price", 30_000)
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.08)
        self.recent_h_min: int = params.get("recent_h_min", 24)
        self.burn_in_h: int = params.get("burn_in_h", 72)
        self.min_age_days: int = params.get("min_age_days", 7)

        # Exit params — tighter + shorter hold
        self.profit_target: float = params.get("profit_target", 0.03)
        self.stop_loss: float = params.get("stop_loss", 0.025)
        self.max_hold_h: int = params.get("max_hold_h", 12)
        self.hard_stop: float = params.get("hard_stop", 0.15)

        # Sizing
        self.notional_per_trade: int = params.get("notional_per_trade", 60_000)
        self.max_positions: int = params.get("max_positions", 12)

        # Post-spike entry window
        self.post_spike_valid_h: int = params.get("post_spike_valid_h", 36)

        # Load spike signal from DB
        self._ea_sales: dict[int, dict[date, int]] = _load_sales_sync()
        self._spike_days: dict[int, set[date]] = {}
        n_cards_with_spikes = 0
        total_spike_days = 0
        for ea, sales in self._ea_sales.items():
            spikes = _compute_spike_days(
                sales, self.rolling_window_d, self.spike_mult, self.noise_floor_sales,
            )
            if spikes:
                self._spike_days[ea] = spikes
                n_cards_with_spikes += 1
                total_spike_days += len(spikes)
        logger.info(
            f"daily_sales_spike_v2 (mult={self.spike_mult}): "
            f"loaded sales for {len(self._ea_sales)} ea_ids, "
            f"{n_cards_with_spikes} have spikes ({total_spike_days} spike-days total)"
        )

        # Per-position state
        self._history: dict[int, deque] = defaultdict(
            lambda: deque(maxlen=max(self.recent_h_min, 96))
        )
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._created_at: dict[int, datetime] = {}
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

    def _is_post_spike_now(self, ea_id: int, ts: datetime) -> bool:
        """True if yesterday (or within post_spike_valid_h hours) was a spike day."""
        spikes = self._spike_days.get(ea_id)
        if not spikes:
            return False
        cutoff = ts - timedelta(hours=self.post_spike_valid_h)
        for d in spikes:
            spike_end = datetime.combine(d + timedelta(days=1), datetime.min.time())
            if cutoff <= spike_end <= ts:
                return True
        return False

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

        if self._first_ts is None:
            self._first_ts = ts_clean

        # --- Exits ---
        for ea_id, price in ticks:
            self._history[ea_id].append(price)

            holding = portfolio.holdings(ea_id)
            if holding <= 0:
                continue

            buy_price = self._buy_prices.get(ea_id, price)
            buy_ts = self._buy_ts.get(ea_id, ts_clean)
            bt_clean = buy_ts.replace(tzinfo=None) if buy_ts.tzinfo else buy_ts
            hold_hours = (ts_clean - bt_clean).total_seconds() / 3600

            smooth = self._smooth(self._history[ea_id])

            sell = False
            if buy_price > 0 and price <= buy_price * (1.0 - self.hard_stop):
                sell = True
            elif hold_hours >= self.max_hold_h:
                sell = True
            elif smooth > 0 and buy_price > 0:
                pct = (smooth - buy_price) / buy_price
                if pct >= self.profit_target:
                    sell = True
                elif pct <= -self.stop_loss:
                    sell = True

            if sell:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._buy_ts.pop(ea_id, None)

        # --- Burn-in ---
        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        # --- Entry candidates ---
        if len(portfolio.positions) >= self.max_positions:
            return signals

        candidates: list[tuple[int, int, int]] = []
        for ea_id, price in ticks:
            if not self._is_post_spike_now(ea_id, ts_clean):
                continue
            if portfolio.holdings(ea_id) > 0:
                continue
            if not (self.min_price <= price <= self.max_price):
                continue

            hist = self._history[ea_id]
            if len(hist) < self.recent_h_min:
                continue
            smooth = self._smooth(hist)
            if smooth <= 0 or self._is_outlier(price, smooth):
                continue
            if not (self.min_price <= smooth <= self.max_price):
                continue

            created = self._created_at.get(ea_id)
            if created:
                cr_clean = (
                    created.replace(tzinfo=None) if created.tzinfo else created
                )
                age_days = (ts_clean - cr_clean).days
                if age_days < self.min_age_days:
                    continue

            candidates.append((ea_id, price, smooth))

        if not candidates:
            return signals

        candidates.sort(key=lambda x: x[1])

        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                p = next((p for eid, p in ticks if eid == s.ea_id), 0)
                sell_rev += (p * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

        open_slots = self.max_positions - len(portfolio.positions)
        buys_made = 0
        for ea_id, price, _smooth in candidates:
            if buys_made >= open_slots:
                break
            if available <= 0 or price <= 0:
                break
            target_qty = max(1, self.notional_per_trade // price)
            qty = min(target_qty, available // price)
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
        base = {
            "rolling_window_d": 5,
            "noise_floor_sales": 5,
            "min_price": 10_000,
            "max_price": 30_000,
            "smooth_window_h": 3,
            "outlier_tol": 0.08,
            "recent_h_min": 24,
            "burn_in_h": 72,
            "min_age_days": 7,
            "hard_stop": 0.15,
            "notional_per_trade": 60_000,
            "max_positions": 12,
            "post_spike_valid_h": 36,
        }
        return [
            # Primary: 2.0x with tight exits + short hold
            {
                **base,
                "spike_mult": 2.0,
                "profit_target": 0.03,
                "stop_loss": 0.025,
                "max_hold_h": 12,
            },
            # Higher-conviction variant: 2.5x with slightly wider exits / longer hold
            {
                **base,
                "spike_mult": 2.5,
                "profit_target": 0.04,
                "stop_loss": 0.03,
                "max_hold_h": 18,
            },
        ]
