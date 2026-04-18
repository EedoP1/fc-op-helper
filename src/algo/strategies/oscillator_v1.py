"""High-amplitude oscillator v1 — buy low / sell high on cards that
demonstrably oscillate 50%+ over 7-day windows.

EDA finding: 30+ liquid cards exhibit weekly amplitude of 60-108%
(measured as (max-min)/median over rolling 168h windows). These cards
oscillate around a stable mean rather than trending; ea=67109952 (the
bot's most-traded card, 6,267 sales) has 71.5% amplitude. The trade_records
ground truth shows our bot makes 20-40% gross margins by buying these
near floor and selling near peak.

This is STRUCTURALLY different from floor_buy (which clamps to $10-13k
absolute floor) and dip_revert (which uses 24h dip from 24h median).
Oscillator strategy works on the FULL liquid price range ($11k-$200k)
and uses the card's OWN 168h range as reference instead of a static
floor.

Entry: smoothed price within bottom 20% of 168h range AND
       card has shown at least one historical bounce (max(168h) >=
       median(168h) × 1.2) AND card is not in an active down-leg
       (12h smoothed delta >= -0.03).

Exit: smoothed price reaches top 20% of 168h range, OR
      smoothed +20% gain from buy_price, OR
      smoothed -12% from buy_price (stop), OR
      max-hold 96h.

Target/stop math: cards with 60%+ amplitude regularly move 20-30% from
trough to peak. Pessimistic-loader BUY-at-max + SELL-at-min adds ~9.6%
break-even. +20% smoothed target → real net ~+13% after spread+tax.
−12% smoothed stop → real net ~-15% loss. Win rate of 60%+ pushes EV
positive.
"""
import logging
from datetime import datetime
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class OscillatorV1Strategy(Strategy):
    name = "oscillator_v1"

    def __init__(self, params: dict):
        self.params = params
        # Universe gate: amplitude over 168h must clear this
        self.min_amplitude: float = params.get("min_amplitude", 0.40)
        # 168h range bottom %ile to enter, top %ile to exit
        self.range_window_h: int = params.get("range_window_h", 168)
        self.entry_quantile: float = params.get("entry_quantile", 0.20)
        self.exit_quantile: float = params.get("exit_quantile", 0.80)
        # Card not actively falling
        self.card_drop_max: float = params.get("card_drop_max", -0.03)
        # Smoothing / outlier
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.05)
        self.short_h: int = params.get("short_h", 12)
        # Profit / loss / hold
        self.profit_target: float = params.get("profit_target", 0.20)
        self.stop_loss: float = params.get("stop_loss", 0.12)
        self.max_hold_h: int = params.get("max_hold_h", 96)
        # Universe gates
        self.min_price: int = params.get("min_price", 11000)
        self.max_price: int = params.get("max_price", 200000)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 168)
        # Sizing
        self.qty_cap: int = params.get("qty_cap", 6)
        self.max_positions: int = params.get("max_positions", 8)

        hist_len = self.range_window_h + 8
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._created_at: dict[int, datetime] = {}
        self._promo_ids: set[int] = set()
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._first_ts: datetime | None = None

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

    def _card_short_delta(self, hist: deque) -> float:
        if len(hist) < self.short_h + self.smooth_window_h:
            return 0.0
        recent = self._median(list(hist)[-self.smooth_window_h:])
        past = self._median(list(hist)[-self.short_h - self.smooth_window_h:-self.short_h])
        if past <= 0:
            return 0.0
        return (recent - past) / past

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

        if self._first_ts is None:
            self._first_ts = ts_clean

        for ea_id, price in ticks:
            self._history[ea_id].append(price)

        # Exits FIRST
        for ea_id, price in ticks:
            holding = portfolio.holdings(ea_id)
            if holding <= 0:
                continue
            buy_price = self._buy_prices.get(ea_id, price)
            buy_ts = self._buy_ts.get(ea_id, ts_clean)
            bt_clean = buy_ts.replace(tzinfo=None) if buy_ts.tzinfo else buy_ts
            hold_hours = (ts_clean - bt_clean).total_seconds() / 3600

            smooth = self._smooth(self._history[ea_id])
            sell = False

            if smooth > 0 and buy_price > 0:
                pct = (smooth - buy_price) / buy_price
                if pct >= self.profit_target:
                    sell = True
                elif pct <= -self.stop_loss:
                    sell = True

            # Range-top exit (smoothed reaches top quantile of 168h range)
            hist = self._history[ea_id]
            if not sell and len(hist) >= self.range_window_h:
                window = sorted(list(hist)[-self.range_window_h:])
                top_idx = int((len(window) - 1) * self.exit_quantile)
                top_thresh = window[top_idx]
                if smooth >= top_thresh and hold_hours >= 12:
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

        # Entry candidates
        candidates: list[tuple[int, int, int, float]] = []
        for ea_id, price in ticks:
            hist = self._history[ea_id]
            if len(hist) < self.range_window_h:
                continue
            smooth = self._smooth(hist)
            if smooth <= 0 or self._is_outlier(price, smooth):
                continue
            if not (self.min_price <= price <= self.max_price):
                continue
            if portfolio.holdings(ea_id) > 0:
                continue
            if ea_id in self._promo_ids:
                continue

            created = self._created_at.get(ea_id)
            if created:
                cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
                if (ts_clean - cr_clean).days < self.min_age_days:
                    continue

            window = list(hist)[-self.range_window_h:]
            w_lo, w_hi = min(window), max(window)
            w_med = self._median(window)
            if w_lo <= 0 or w_med <= 0:
                continue

            # Universe gate: amplitude
            amp = (w_hi - w_lo) / max(1, w_med)
            if amp < self.min_amplitude:
                continue

            # Bottom quantile entry
            sorted_w = sorted(window)
            entry_idx = int((len(sorted_w) - 1) * self.entry_quantile)
            entry_thresh = sorted_w[entry_idx]
            if smooth > entry_thresh:
                continue

            # Card not actively falling
            cd = self._card_short_delta(hist)
            if cd < self.card_drop_max:
                continue

            candidates.append((ea_id, price, smooth, amp))

        if not candidates or len(portfolio.positions) >= self.max_positions:
            return signals

        # Sort by amplitude desc (highest oscillation first), then by smoothed asc
        candidates.sort(key=lambda x: (-x[3], x[2]))

        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                p = next((pp for eid, pp in ticks if eid == s.ea_id), 0)
                sell_rev += (p * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

        open_slots = self.max_positions - len(portfolio.positions)
        buys_made = 0
        for ea_id, price, _, _ in candidates:
            if buys_made >= open_slots:
                break
            if available <= 0:
                break
            qty = min(self.qty_cap, available // price if price > 0 else 0)
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
            "min_amplitude": 0.40,
            "range_window_h": 168,
            "entry_quantile": 0.20,
            "exit_quantile": 0.80,
            "card_drop_max": -0.03,
            "smooth_window_h": 3,
            "outlier_tol": 0.05,
            "short_h": 12,
            "profit_target": 0.20,
            "stop_loss": 0.12,
            "max_hold_h": 96,
            "min_price": 11000,
            "max_price": 200000,
            "min_age_days": 7,
            "burn_in_h": 168,
            "qty_cap": 6,
            "max_positions": 8,
        }]
