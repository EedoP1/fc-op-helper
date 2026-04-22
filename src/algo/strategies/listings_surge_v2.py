"""Listings Surge v2 — squeeze entry via listings-drop + price-UP.

v1 hypothesis (listings-drop + flat price) lost -$486.8k with 16.3% win
rate. That confirmed the listing_count signal IS real but read it the
wrong way: supply drying up with flat price turns out to be distressed
withdrawal during declines, not pre-spike accumulation.

v2 flips the direction: require supply drying up AND price already
rallying. That's a classic squeeze — few sellers left, rising demand,
short-term momentum continuation.

Entry (per card, per tick):
  - listing_count_now <= 0.70 * listing_count_24h_ago  (supply -30%)
  - price_now >= 1.05 * price_24h_ago                  (price +5% same window)
  - price in [$10k, $50k], age >= 7d, no existing holding
  - qty = 5, max_positions = 10

Exit:
  - +15% smooth profit
  - -10% stop (after 24h stop delay)
  - 48h max_hold force

Uses the hourly avg listing_count data via set_listing_counts() —
the first "squeeze" read of the supply signal.
"""
import logging
from datetime import datetime
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class ListingsSurgeV2Strategy(Strategy):
    name = "listings_surge_v2"

    def __init__(self, params: dict):
        self.params = params
        # Signal thresholds
        self.lookback_h: int = params.get("lookback_h", 24)
        self.supply_drop_max: float = params.get("supply_drop_max", 0.70)
        self.price_rise_min: float = params.get("price_rise_min", 1.05)
        # Exits
        self.profit_target: float = params.get("profit_target", 0.15)
        self.stop_loss: float = params.get("stop_loss", 0.10)
        self.stop_delay_h: int = params.get("stop_delay_h", 24)
        self.max_hold_h: int = params.get("max_hold_h", 48)
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        # Universe gates
        self.min_price: int = params.get("min_price", 10000)
        self.max_price: int = params.get("max_price", 50000)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 48)
        self.qty_cap: int = params.get("qty_cap", 5)
        self.max_positions: int = params.get("max_positions", 10)

        hist_len = max(self.lookback_h * 2, 72) + 8
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._created_at: dict[int, datetime] = {}
        self._listing_counts: dict[tuple[int, datetime], int] = {}
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._first_ts: datetime | None = None
        self._signal_fire_count: int = 0

    def set_created_at_map(self, created_at_map: dict):
        self._created_at = created_at_map

    def set_listing_counts(self, listing_counts: dict):
        # Normalize keys to tzinfo-stripped timestamps for robust lookup
        normalized: dict[tuple[int, datetime], int] = {}
        for (ea_id, ts), count in listing_counts.items():
            ts_clean = ts.replace(tzinfo=None) if ts.tzinfo else ts
            normalized[(ea_id, ts_clean)] = count
        self._listing_counts = normalized

    @staticmethod
    def _median(values) -> int:
        s = sorted(values)
        return s[len(s) // 2] if s else 0

    def _smooth(self, history: deque) -> int:
        if len(history) < self.smooth_window_h:
            return 0
        return self._median(list(history)[-self.smooth_window_h:])

    def _lc_at(self, ea_id: int, ts: datetime) -> int | None:
        from datetime import timedelta
        key = (ea_id, ts)
        if key in self._listing_counts:
            return self._listing_counts[key]
        # Tolerate +/-1h drift in case ticks don't land exactly on bucket boundaries
        for offset_m in (0, 60, -60):
            alt_ts = ts.replace(minute=0, second=0, microsecond=0) + timedelta(minutes=offset_m)
            alt_key = (ea_id, alt_ts)
            if alt_key in self._listing_counts:
                return self._listing_counts[alt_key]
        return None

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        from datetime import timedelta
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

        if self._first_ts is None:
            self._first_ts = ts_clean

        # Record price history as (ts, price) tuples
        for ea_id, price in ticks:
            self._history[ea_id].append((ts_clean, price))

        # Exits first
        for ea_id, price in ticks:
            holding = portfolio.holdings(ea_id)
            if holding <= 0:
                continue
            buy_price = self._buy_prices.get(ea_id, price)
            buy_ts = self._buy_ts.get(ea_id, ts_clean)
            bt_clean = buy_ts.replace(tzinfo=None) if buy_ts.tzinfo else buy_ts
            hold_hours = (ts_clean - bt_clean).total_seconds() / 3600

            hist_prices = [p for _, p in self._history[ea_id]]
            smooth = self._median(hist_prices[-self.smooth_window_h:]) if len(hist_prices) >= self.smooth_window_h else 0
            sell = False
            if smooth > 0 and buy_price > 0:
                pct = (smooth - buy_price) / buy_price
                if pct >= self.profit_target:
                    sell = True
                elif hold_hours >= self.stop_delay_h and pct <= -self.stop_loss:
                    sell = True
            if not sell and hold_hours >= self.max_hold_h:
                sell = True
            if sell:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._buy_ts.pop(ea_id, None)

        # Burn-in
        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        # Cap positions
        if len(portfolio.positions) >= self.max_positions:
            return signals

        # Entry scan
        ts_past = ts_clean - timedelta(hours=self.lookback_h)
        candidates: list[tuple[int, int]] = []
        for ea_id, price in ticks:
            if portfolio.holdings(ea_id) > 0:
                continue
            if not (self.min_price <= price <= self.max_price):
                continue
            # Age
            created = self._created_at.get(ea_id)
            if created:
                cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
                if (ts_clean - cr_clean).days < self.min_age_days:
                    continue

            # Supply drop signal
            lc_now = self._lc_at(ea_id, ts_clean)
            lc_past = self._lc_at(ea_id, ts_past)
            if lc_now is None or lc_past is None:
                continue
            if lc_past <= 0:
                continue
            if lc_now > self.supply_drop_max * lc_past:
                continue

            # Price rise check — need past price
            past_price = None
            for (hts, hp) in reversed(self._history[ea_id]):
                if hts <= ts_past:
                    past_price = hp
                    break
            if past_price is None or past_price <= 0:
                continue
            # v2: require price UP +5%+ (squeeze), not flat (v1 was <= 1.03)
            if price < self.price_rise_min * past_price:
                continue

            candidates.append((ea_id, price))

        if not candidates:
            return signals

        self._signal_fire_count += len(candidates)

        # Available cash (account for sells in this tick)
        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                p = next((pp for eid, pp in ticks if eid == s.ea_id), 0)
                sell_rev += (p * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

        open_slots = self.max_positions - len(portfolio.positions)
        # Prefer cheapest so budget spreads across more cards
        candidates.sort(key=lambda x: x[1])
        buys_made = 0
        for ea_id, price in candidates:
            if buys_made >= open_slots:
                break
            if available <= 0 or price <= 0:
                break
            qty = min(self.qty_cap, available // price)
            if qty > 0:
                signals.append(Signal(action="BUY", ea_id=ea_id, quantity=qty))
                self._buy_prices[ea_id] = price
                self._buy_ts[ea_id] = timestamp
                available -= qty * price
                buys_made += 1

        return signals

    def param_grid(self) -> list[dict]:
        return [{
            "lookback_h": 24,
            "supply_drop_max": 0.70,
            "price_rise_min": 1.05,
            "profit_target": 0.15,
            "stop_loss": 0.10,
            "stop_delay_h": 24,
            "max_hold_h": 48,
            "smooth_window_h": 3,
            "min_price": 10000,
            "max_price": 50000,
            "min_age_days": 7,
            "burn_in_h": 48,
            "qty_cap": 5,
            "max_positions": 10,
        }]
