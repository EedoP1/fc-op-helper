"""Cohort rally chaser v1 — when N+ peer cards rally simultaneously, buy
the laggard in the same price tier.

Insight from floor_buy_v19 W16 result: 8 cards in the $11-14k tier all
rallied to $18,500 within the same window. The rally is a CROWD DYNAMIC
— when meta shifts (new promo, content drop), all cards in a price tier
move together. floor_buy_v15 captures one slice of this; the broader
phenomenon is universal across price tiers.

Hypothesis: monitor each tier's 24h cohort rally count. When ≥3 cards
in a tier have rallied ≥6% in the last 18h AND the tier's median 12h
return is positive, BUY any card in that tier that has NOT yet rallied
(still trading near its 168h low). Hold for follow-through.

This is STRUCTURALLY DIFFERENT from floor_buy because:
  - Tier boundaries are dynamic (any 5,000-coin band, not just $10-13k)
  - Entry trigger is PEER MOVEMENT (not floor proximity alone)
  - Doesn't require structural floor mechanic; works at any price level
    where a cohort exists

Exit: smoothed +15% target, smoothed -10% stop, max-hold 72h.
"""
import logging
from datetime import datetime
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class CohortChaseV1Strategy(Strategy):
    name = "cohort_chase_v1"

    def __init__(self, params: dict):
        self.params = params
        # Tier definition: every TIER_W coins is a tier (e.g., 5000 = $11-16k, $16-21k, ...)
        self.tier_width: int = params.get("tier_width", 5000)
        self.cohort_lookback_h: int = params.get("cohort_lookback_h", 18)
        self.cohort_min_rally_pct: float = params.get("cohort_min_rally_pct", 0.06)
        self.cohort_min_count: int = params.get("cohort_min_count", 3)
        self.tier_g_min: float = params.get("tier_g_min", 0.0)
        # Laggard entry: card in bottom X% of 168h range, NOT yet rallied
        self.range_window_h: int = params.get("range_window_h", 168)
        self.laggard_quantile: float = params.get("laggard_quantile", 0.30)
        self.laggard_max_24h_rise: float = params.get("laggard_max_24h_rise", 0.03)
        # Smoothing / outlier
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.05)
        # Profit / loss / hold
        self.profit_target: float = params.get("profit_target", 0.15)
        self.stop_loss: float = params.get("stop_loss", 0.10)
        self.max_hold_h: int = params.get("max_hold_h", 72)
        # Universe gates
        self.min_price: int = params.get("min_price", 11000)
        self.max_price: int = params.get("max_price", 50000)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 168)
        # Sizing
        self.qty_cap: int = params.get("qty_cap", 6)
        self.max_positions: int = params.get("max_positions", 8)

        hist_len = self.range_window_h + 24
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

    def _delta(self, hist: deque, lookback_h: int) -> float:
        if len(hist) < lookback_h + self.smooth_window_h:
            return 0.0
        recent = self._median(list(hist)[-self.smooth_window_h:])
        past = self._median(list(hist)[-lookback_h - self.smooth_window_h:-lookback_h])
        if past <= 0:
            return 0.0
        return (recent - past) / past

    def _tier_of(self, smooth: int) -> int:
        return smooth // self.tier_width

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
            if not sell and hold_hours >= self.max_hold_h:
                sell = True

            if sell:
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._buy_ts.pop(ea_id, None)

        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        # Compute per-tier cohort rally state
        # tier -> list of (ea_id, smooth, 18h_delta, 24h_delta, in_bottom_quantile)
        tier_data: dict[int, list[tuple[int, int, float, float, bool, int]]] = defaultdict(list)
        for ea_id, price in ticks:
            hist = self._history[ea_id]
            if len(hist) < self.range_window_h:
                continue
            smooth = self._smooth(hist)
            if smooth <= 0 or self._is_outlier(price, smooth):
                continue
            if not (self.min_price <= smooth <= self.max_price):
                continue
            # tier
            t = self._tier_of(smooth)
            d18 = self._delta(hist, self.cohort_lookback_h)
            d24 = self._delta(hist, 24)
            window = list(hist)[-self.range_window_h:]
            sorted_w = sorted(window)
            lag_idx = int((len(sorted_w) - 1) * self.laggard_quantile)
            lag_thresh = sorted_w[lag_idx]
            in_bottom = smooth <= lag_thresh
            tier_data[t].append((ea_id, smooth, d18, d24, in_bottom, price))

        # For each tier, count cohort rallies and tier median 12h delta
        candidates: list[tuple[int, int, int, int, int]] = []  # (ea_id, price, smooth, tier_strength, neg_d24)
        for tier, items in tier_data.items():
            if len(items) < 4:
                continue
            rally_count = sum(1 for (_, _, d18, _, _, _) in items if d18 >= self.cohort_min_rally_pct)
            tier_d12 = sorted(self._delta(self._history[eid], 12) for (eid, _, _, _, _, _) in items)
            tier_d12_med = tier_d12[len(tier_d12) // 2] if tier_d12 else 0
            if rally_count < self.cohort_min_count:
                continue
            if tier_d12_med < self.tier_g_min:
                continue

            # Find laggards in this tier
            for ea_id, smooth, d18, d24, in_bottom, price in items:
                if not in_bottom:
                    continue
                if d24 > self.laggard_max_24h_rise:
                    continue
                # not already holding
                if portfolio.holdings(ea_id) > 0:
                    continue
                if ea_id in self._promo_ids:
                    continue
                created = self._created_at.get(ea_id)
                if created:
                    cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
                    if (ts_clean - cr_clean).days < self.min_age_days:
                        continue
                candidates.append((ea_id, price, smooth, rally_count, -int(d24 * 1000)))

        if not candidates or len(portfolio.positions) >= self.max_positions:
            return signals

        # Sort by tier strength desc, then by 24h drop (the more it dropped, the better the laggard)
        candidates.sort(key=lambda x: (-x[3], -x[4], x[2]))

        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                p = next((pp for eid, pp in ticks if eid == s.ea_id), 0)
                sell_rev += (p * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

        open_slots = self.max_positions - len(portfolio.positions)
        buys_made = 0
        for ea_id, price, _, _, _ in candidates:
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
            "tier_width": 5000,
            "cohort_lookback_h": 18,
            "cohort_min_rally_pct": 0.06,
            "cohort_min_count": 3,
            "tier_g_min": 0.0,
            "range_window_h": 168,
            "laggard_quantile": 0.30,
            "laggard_max_24h_rise": 0.03,
            "smooth_window_h": 3,
            "outlier_tol": 0.05,
            "profit_target": 0.15,
            "stop_loss": 0.10,
            "max_hold_h": 72,
            "min_price": 11000,
            "max_price": 50000,
            "min_age_days": 7,
            "burn_in_h": 168,
            "qty_cap": 6,
            "max_positions": 8,
        }]
