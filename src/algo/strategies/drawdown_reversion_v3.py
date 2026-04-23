"""Drawdown-reversion v3 — v1 + supply-contraction structural gate (iter 3).

v1 (-$395.6k org, 48.8% win) bought falling knives in W14-W16. v2's weak
price-bounce gate (-$642.9k, 42.1% win) didn't fix it — normal noise
trivially satisfied the 3%-off-12h-low / 0-sum 6h momentum triggers.

Structural insight from iter 1/2 recap: the signature AUC features in the
catalog are survivorship-biased — they describe price paths conditional
on reversal, so price-only gates can't filter knives from bottoms. We
need a signal about market MICROSTRUCTURE, not price shape.

v3 hypothesis: REAL capitulation bottoms show SELLER CAPITULATION via
listing-count contraction. If price is down 20% off 72h high AND the
order book is thinning (sellers pulling listings they won't dump at
these prices), the selling pressure is ending. If listings stay fat
while price falls, sellers are still feeding the dump — keep catching
knives.

Note: prior `supply_absorption_v1` (depletion + price STABILITY) failed
at -$773k because stability predicted sideways→down (sellers resting a
stale book). v3 is different — it's depletion + price ALREADY DOWN 20%,
which is end-of-dump conditioning, not stale-book conditioning.

Entry (ALL must hold, evaluated at tick):
- v1's base: drawdown_72h >= 0.20, rel_pos_24h <= 0.25, pc_72h <= -0.10
- v1's whitelist (rating 86-91 + premium card types)
- price in [$13k, $100k]
- NEW: lc_now <= 0.85 * mean(lc over last 72h, EXCLUDING most recent 12h)
  Requires >= 72h of listing history; skip otherwise.

Exit unchanged (25% target / -15% stop / 96h max hold).
"""
import logging
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta

from src.algo.models import Portfolio, Signal
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


# Whitelist: Section B repeater card types + Section C premium typology (v1)
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


# Cache attrs across multiple instantiations in one process.
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
            logger.error(f"drawdown_reversion_v3: DB load failed: {result_holder['err']}")
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


class DrawdownReversionV3Strategy(Strategy):
    name = "drawdown_reversion_v3"

    def __init__(self, params: dict):
        self.params = params
        # Smoothing / outlier
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.10)

        # Entry signature thresholds (v1)
        self.drawdown_min: float = params.get("drawdown_min", 0.20)
        self.rel_pos_max: float = params.get("rel_pos_max", 0.25)
        self.price_change_72h_max: float = params.get("price_change_72h_max", -0.10)

        # NEW: listing-count contraction gate
        self.lc_contraction_ratio: float = params.get("lc_contraction_ratio", 0.85)
        self.lc_baseline_h: int = params.get("lc_baseline_h", 72)
        self.lc_recent_exclude_h: int = params.get("lc_recent_exclude_h", 12)

        # Windows
        self.dd_window_h: int = params.get("dd_window_h", 72)
        self.rel_pos_window_h: int = params.get("rel_pos_window_h", 24)

        # Price band
        self.min_price: int = params.get("min_price", 13000)
        self.max_price: int = params.get("max_price", 100000)

        # Exit
        self.profit_target: float = params.get("profit_target", 0.25)
        self.hard_stop: float = params.get("hard_stop", 0.15)
        self.smoothed_stop: float = params.get("smoothed_stop", 0.15)
        self.max_hold_h: int = params.get("max_hold_h", 96)
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
            ea for ea, attrs in self._attrs.items() if _in_whitelist(attrs)
        }
        logger.info(
            f"drawdown_reversion_v3: loaded {len(self._attrs)} players, "
            f"whitelist size = {len(self._whitelist_ids)}"
        )

        # State
        hist_len = self.dd_window_h + 8
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._created_at: dict[int, datetime] = {}
        self._listings: dict[tuple[int, datetime], int] = {}
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._stopped_until: dict[int, datetime] = {}
        self._first_ts: datetime | None = None

    def set_created_at_map(self, created_at_map: dict):
        self._created_at = created_at_map

    def set_listing_counts(self, listing_counts: dict):
        # Normalize keys: strip tzinfo, round to hour (mirrors supply_absorption_v1)
        normalized: dict[tuple[int, datetime], int] = {}
        for (ea_id, ts), count in listing_counts.items():
            ts_clean = ts.replace(tzinfo=None) if ts.tzinfo else ts
            ts_hr = ts_clean.replace(minute=0, second=0, microsecond=0)
            normalized[(ea_id, ts_hr)] = count
        self._listings = normalized
        logger.info(f"drawdown_reversion_v3: loaded {len(self._listings)} listing points")

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

    def _lc_contraction_ok(self, ea_id: int, ts_hr: datetime) -> bool:
        """Returns True iff lc_now <= ratio * mean(lc over last baseline_h hours,
        EXCLUDING most recent exclude_h hours). Requires all hours present.

        Baseline window: [ts_hr - baseline_h, ts_hr - exclude_h)
        Current: ts_hr
        """
        lc_now = self._listings.get((ea_id, ts_hr))
        if lc_now is None or lc_now <= 0:
            return False

        # Baseline: hours from (baseline_h) back up to (exclude_h + 1) back, inclusive.
        baseline_values: list[int] = []
        missing = 0
        for h_back in range(self.lc_recent_exclude_h + 1, self.lc_baseline_h + 1):
            probe_ts = ts_hr - timedelta(hours=h_back)
            v = self._listings.get((ea_id, probe_ts))
            if v is None:
                missing += 1
            elif v > 0:
                baseline_values.append(v)

        # Demand ~>=75% of baseline hours present for robustness.
        needed = self.lc_baseline_h - self.lc_recent_exclude_h
        if len(baseline_values) < int(needed * 0.75):
            return False

        baseline_mean = sum(baseline_values) / len(baseline_values)
        if baseline_mean <= 0:
            return False

        return lc_now <= self.lc_contraction_ratio * baseline_mean

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp
        ts_hr = ts_clean.replace(minute=0, second=0, microsecond=0)

        if self._first_ts is None:
            self._first_ts = ts_clean

        # --- Append history + exits ---
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
        candidates: list[tuple[int, int, int, float]] = []
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
            dd_window = hist_list[-self.dd_window_h:]
            dd_max = max(dd_window)
            if dd_max <= 0:
                continue
            drawdown = (dd_max - price) / dd_max
            if drawdown < self.drawdown_min:
                continue

            price_72h_ago = hist_list[-self.dd_window_h]
            if price_72h_ago <= 0:
                continue
            pc_72h = (price - price_72h_ago) / price_72h_ago
            if pc_72h > self.price_change_72h_max:
                continue

            rp_window = hist_list[-self.rel_pos_window_h:] if len(hist_list) >= self.rel_pos_window_h else hist_list
            rp_min = min(rp_window)
            rp_max = max(rp_window)
            if rp_max == rp_min:
                rel_pos = 0.0
            else:
                rel_pos = (price - rp_min) / (rp_max - rp_min)
            if rel_pos > self.rel_pos_max:
                continue

            # NEW: listing-count contraction gate
            if not self._lc_contraction_ok(ea_id, ts_hr):
                continue

            candidates.append((ea_id, price, smooth, drawdown))

        if not candidates:
            return signals
        if len(portfolio.positions) >= self.max_positions:
            return signals

        candidates.sort(key=lambda x: -x[3])

        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                p = next((pp for eid, pp in ticks if eid == s.ea_id), 0)
                sell_rev += (p * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

        open_slots = self.max_positions - len(portfolio.positions)
        buys_made = 0
        for ea_id, price, _smooth, _dd in candidates:
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
            "drawdown_min": 0.20,
            "rel_pos_max": 0.25,
            "price_change_72h_max": -0.10,
            "lc_contraction_ratio": 0.85,
            "lc_baseline_h": 72,
            "lc_recent_exclude_h": 12,
            "dd_window_h": 72,
            "rel_pos_window_h": 24,
            "min_price": 13000,
            "max_price": 100000,
            "profit_target": 0.25,
            "hard_stop": 0.15,
            "smoothed_stop": 0.15,
            "max_hold_h": 96,
            "stop_cooldown_h": 48,
            "max_positions": 8,
            "per_slot_budget": 125_000,
            "qty_cap": 50,
            "min_age_days": 7,
            "burn_in_h": 72,
        }]
