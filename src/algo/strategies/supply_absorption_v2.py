"""Supply absorption v2 — listing depletion WITH price ALREADY RISING.

Hypothesis (inverted from v1): v1's 22.5% win rate meant the
depletion+stability signal was a reliable FADE predictor, not a
pre-spike one. v2 tests the symmetric bullish analog: when
`listing_count` drops sharply (>=30% in 6h) AND price has already
risen 2-10% in the same window, depletion is genuine demand
absorption (pre-breakout), not sellers waiting out a stale book.

Only the price-direction gate differs from v1.

Entry (BUY):
- lc_6h >= 20 AND (lc_6h - lc_now)/lc_6h >= 0.30
- 6h price rise = (current - median_6h) / median_6h in [0.02, 0.10]
- 10k <= price <= 50k
- min_age_days = 7
- burn_in 24h
- qty = 15, max_positions = 10

Exit (SELL): unchanged from v1
- +8% profit target (vs buy_price)
- -5% stop loss
- 72h max hold
"""
import logging
from datetime import datetime, timedelta
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class SupplyAbsorptionV2Strategy(Strategy):
    name = "supply_absorption_v2"

    def __init__(self, params: dict):
        self.params = params
        self.depletion_threshold: float = params.get("depletion_threshold", 0.30)
        self.min_lc_6h: int = params.get("min_lc_6h", 20)
        self.price_rise_min: float = params.get("price_rise_min", 0.02)
        self.price_rise_max: float = params.get("price_rise_max", 0.10)
        self.lookback_h: int = params.get("lookback_h", 6)
        self.min_price: int = params.get("min_price", 10000)
        self.max_price: int = params.get("max_price", 50000)
        self.profit_target: float = params.get("profit_target", 0.08)
        self.stop_loss: float = params.get("stop_loss", 0.05)
        self.max_hold_h: int = params.get("max_hold_h", 72)
        self.max_positions: int = params.get("max_positions", 10)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 24)
        self.qty: int = params.get("qty", 15)

        # Rolling price history per card for 6h price-rise check
        hist_len = self.lookback_h + 4
        self._price_hist: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._created_at: dict[int, datetime] = {}
        self._listings: dict[tuple[int, datetime], int] = {}
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._first_ts: datetime | None = None

    def set_created_at_map(self, created_at_map: dict):
        self._created_at = created_at_map

    def set_listing_counts(self, listing_counts: dict):
        # Normalize keys: strip tzinfo, round to hour
        normalized: dict[tuple[int, datetime], int] = {}
        for (ea_id, ts), count in listing_counts.items():
            ts_clean = ts.replace(tzinfo=None) if ts.tzinfo else ts
            ts_hr = ts_clean.replace(minute=0, second=0, microsecond=0)
            normalized[(ea_id, ts_hr)] = count
        self._listings = normalized

    @staticmethod
    def _median(values) -> int:
        s = sorted(values)
        return s[len(s) // 2] if s else 0

    def _price_rise_6h(self, ea_id: int, current_price: int) -> float | None:
        """Return (current - median_6h) / median_6h, or None if insufficient history."""
        hist = list(self._price_hist[ea_id])
        if len(hist) < self.lookback_h:
            return None
        window = hist[-self.lookback_h:]
        med = self._median(window)
        if med <= 0:
            return None
        return (current_price - med) / med

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp
        ts_hr = ts_clean.replace(minute=0, second=0, microsecond=0)
        ts_hr_6 = ts_hr - timedelta(hours=self.lookback_h)

        if self._first_ts is None:
            self._first_ts = ts_clean

        # 1) Update price history
        for ea_id, price in ticks:
            self._price_hist[ea_id].append(price)

        # 2) Exits first
        for ea_id, price in ticks:
            holding = portfolio.holdings(ea_id)
            if holding <= 0:
                continue
            buy_price = self._buy_prices.get(ea_id, price)
            buy_ts = self._buy_ts.get(ea_id, ts_clean)
            bt_clean = buy_ts.replace(tzinfo=None) if buy_ts.tzinfo else buy_ts
            hold_hours = (ts_clean - bt_clean).total_seconds() / 3600

            sell = False
            if buy_price > 0:
                pct = (price - buy_price) / buy_price
                if pct >= self.profit_target:
                    sell = True
                elif pct <= -self.stop_loss:
                    sell = True
            if not sell and hold_hours >= self.max_hold_h:
                sell = True

            if sell:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._buy_ts.pop(ea_id, None)

        # 3) Burn-in
        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        # 4) Cap positions
        if len(portfolio.positions) >= self.max_positions:
            return signals

        # 5) Candidate scan
        candidates: list[tuple[int, int, float]] = []  # (ea_id, price, depletion_pct)
        for ea_id, price in ticks:
            if portfolio.holdings(ea_id) > 0:
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

            # Listing depletion gate
            lc_now = self._listings.get((ea_id, ts_hr))
            lc_6h = self._listings.get((ea_id, ts_hr_6))
            if lc_now is None or lc_6h is None:
                continue
            if lc_6h < self.min_lc_6h:
                continue
            if lc_6h <= 0:
                continue
            depletion = (lc_6h - lc_now) / lc_6h
            if depletion < self.depletion_threshold:
                continue

            # Price-rise gate (inverted from v1's stability gate)
            rise = self._price_rise_6h(ea_id, price)
            if rise is None:
                continue
            if not (self.price_rise_min <= rise <= self.price_rise_max):
                continue

            candidates.append((ea_id, price, depletion))

        if not candidates:
            return signals

        # Sort by strongest depletion (highest % drop first)
        candidates.sort(key=lambda x: -x[2])

        # Account for pending sell revenue
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
            if available <= 0 or price <= 0:
                break
            qty = min(self.qty, available // price)
            if qty > 0:
                signals.append(Signal(action="BUY", ea_id=ea_id, quantity=qty))
                self._buy_prices[ea_id] = price
                self._buy_ts[ea_id] = timestamp
                available -= qty * price
                buys_made += 1

        return signals

    def param_grid(self) -> list[dict]:
        return [{
            "depletion_threshold": 0.30,
            "min_lc_6h": 20,
            "price_rise_min": 0.02,
            "price_rise_max": 0.10,
            "lookback_h": 6,
            "min_price": 10000,
            "max_price": 50000,
            "profit_target": 0.08,
            "stop_loss": 0.05,
            "max_hold_h": 72,
            "max_positions": 10,
            "min_age_days": 7,
            "burn_in_h": 24,
            "qty": 15,
        }]
