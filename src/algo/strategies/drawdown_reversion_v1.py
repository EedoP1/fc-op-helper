"""Drawdown-reversion v1 — broad-band data-driven drawdown-reversion (iter 1).

Hypothesis: The three highest-AUC opportunity signatures from
`.planning/opportunity_signatures_report.md` Section A generalize beyond
the $10-13k floor-buy band. Enter on deep 72h drawdown with bottom-of-range
recent price, exit on 25% mean reversion.

Entry triggers (all three must hold at buy hour, using only data available
BEFORE the entry tick):
- drawdown_from_max_72h >= 0.20  (price is 20%+ below 72h max)
- rel_pos_24h <= 0.25            (current in bottom 25% of 24h range)
- price_change_72h <= -0.10      (down at least 10% over 72h)

Whitelist (Section B + Section C typology):
- rating IN [86, 87, 88, 89, 90, 91]
- card_type whitelisted to the premium-repeater set plus the floor-buy /
  news-shock / promo-dip premium typology seen in Section C.

Price band: $13,000 - $100,000 (per catalog, 71% of unit-profit lives
here; prior $10-13k signal capped at ~$500k organic).

Exit:
- Profit target: smoothed +25% gross (~+19% net after 5% tax)
- Hard stop:     -15% from buy on tick price
- Smoothed stop: -15% from buy on smoothed price
- Time stop:     96h max hold

Position sizing:
- 8 concurrent slots (brief's $1M / 8 slots)
- ~$125k per slot, qty capped at 50 per the brief's notional ceiling

DB load pattern copied from card_tier_v1 (threaded sync loader to avoid
nested asyncio.run).
"""
import logging
import threading
from collections import defaultdict, deque
from datetime import datetime, timedelta

from src.algo.models import Portfolio, Signal
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


# Whitelist: Section B repeater card types + Section C premium typology
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
    """Load {ea_id: (rating, card_type_lower)} from the players table.

    Copied from card_tier_v1: runs on a background thread so a running
    asyncio loop doesn't break asyncio.run.
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
            logger.error(f"drawdown_reversion_v1: DB load failed: {result_holder['err']}")
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


class DrawdownReversionV1Strategy(Strategy):
    name = "drawdown_reversion_v1"

    def __init__(self, params: dict):
        self.params = params
        # Smoothing / outlier
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.10)

        # Entry signature thresholds
        self.drawdown_min: float = params.get("drawdown_min", 0.20)
        self.rel_pos_max: float = params.get("rel_pos_max", 0.25)
        self.price_change_72h_max: float = params.get("price_change_72h_max", -0.10)

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
        # Pre-compute whitelist set for speed
        self._whitelist_ids: set[int] = {
            ea for ea, attrs in self._attrs.items() if _in_whitelist(attrs)
        }
        logger.info(
            f"drawdown_reversion_v1: loaded {len(self._attrs)} players, "
            f"whitelist size = {len(self._whitelist_ids)}"
        )

        # State
        hist_len = self.dd_window_h + 8
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

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

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

            # Compute signature features over history (exclude the just-appended
            # price? No — we use tick price AS the buy price, so signature must
            # reflect the current moment. That is: drawdown from 72h max
            # INCLUDING the current tick; rel_pos_24h within 24h range
            # including the current tick; price_change_72h from 72h ago to now.
            hist_list = list(hist)
            dd_window = hist_list[-self.dd_window_h:]
            dd_max = max(dd_window)
            if dd_max <= 0:
                continue
            drawdown = (dd_max - price) / dd_max
            if drawdown < self.drawdown_min:
                continue

            # price_change_72h = (price - price_72h_ago) / price_72h_ago
            price_72h_ago = hist_list[-self.dd_window_h]
            if price_72h_ago <= 0:
                continue
            pc_72h = (price - price_72h_ago) / price_72h_ago
            if pc_72h > self.price_change_72h_max:
                continue

            # rel_pos_24h = (price - min_24h) / (max_24h - min_24h)
            rp_window = hist_list[-self.rel_pos_window_h:] if len(hist_list) >= self.rel_pos_window_h else hist_list
            rp_min = min(rp_window)
            rp_max = max(rp_window)
            if rp_max == rp_min:
                rel_pos = 0.0
            else:
                rel_pos = (price - rp_min) / (rp_max - rp_min)
            if rel_pos > self.rel_pos_max:
                continue

            # Rank: deeper drawdown first
            candidates.append((ea_id, price, smooth, drawdown))

        if not candidates:
            return signals
        if len(portfolio.positions) >= self.max_positions:
            return signals

        # Prefer deepest drawdown (most reversion room)
        candidates.sort(key=lambda x: -x[3])

        # Available cash + pending sell proceeds
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
            # Target ~per_slot_budget per position; cap at qty_cap.
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
