"""Floor buy pyramid v1 — 5 parallel floor_buy_v19 arms on disjoint price bands.

Hypothesis (iter 49): combo_v* combos cannibalize because arms overlap on the
same floor-band cards. A single multi-cohort pyramid with 5 non-overlapping
price bands (each with $200k dedicated capital) eliminates that cross-arm
contention. Every card lives in exactly one band determined by its smoothed
price; buy/sell bookkeeping splits by band, so one band running hot cannot
starve another of capital.

Band structure (all bands reuse floor_buy_v19 logic; only band-specific
min_price/floor_ceiling/floor_stable/week_max_ceiling differ):

  band | min_price | floor_ceiling | floor_stable | week_max_ceiling | capital
  -----|-----------|---------------|--------------|------------------|--------
   1   |   10000   |     11000     |    12000     |      15000       | 200000
   2   |   11000   |     12000     |    13000     |      16000       | 200000
   3   |   12000   |     13000     |    14500     |      18000       | 200000
   4   |   13000   |     14000     |    15500     |      19000       | 200000
   5   |   14000   |     15000     |    16500     |      20000       | 200000

Each band caps at max_positions_per_band=2 open positions (10 total across the
pyramid). Band capital is tracked by `_band_cash[B]`: BUY decrements, SELL
credits proceeds*0.95. The engine still enforces `portfolio.cash` globally as
a sanity check.
"""
import logging
from datetime import datetime, timedelta
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


# Band definitions: (min_price, floor_ceiling, floor_stable, week_max_ceiling, capital)
BANDS = {
    1: {"min_price": 10000, "floor_ceiling": 11000, "floor_stable": 12000, "week_max_ceiling": 15000, "capital": 200_000},
    2: {"min_price": 11000, "floor_ceiling": 12000, "floor_stable": 13000, "week_max_ceiling": 16000, "capital": 200_000},
    3: {"min_price": 12000, "floor_ceiling": 13000, "floor_stable": 14500, "week_max_ceiling": 18000, "capital": 200_000},
    4: {"min_price": 13000, "floor_ceiling": 14000, "floor_stable": 15500, "week_max_ceiling": 19000, "capital": 200_000},
    5: {"min_price": 14000, "floor_ceiling": 15000, "floor_stable": 16500, "week_max_ceiling": 20000, "capital": 200_000},
}


class FloorBuyPyramidV1Strategy(Strategy):
    # Name matches file-prefix convention used by scripts/verdict.py.
    name = "floor_buy_pyramid_v1"

    def __init__(self, params: dict):
        self.params = params
        # Shared v19 params (band-invariant).
        self.smooth_window_h: int = params.get("smooth_window_h", 3)
        self.outlier_tol: float = params.get("outlier_tol", 0.08)
        self.recent_h_min: int = params.get("recent_h_min", 24)
        self.recent_h_large: int = params.get("recent_h_large", 72)
        self.week_window_h: int = params.get("week_window_h", 168)
        self.week_range_max: float = params.get("week_range_max", 0.25)
        self.profit_target: float = params.get("profit_target", 0.50)
        self.stop_loss: float = params.get("stop_loss", 0.10)
        self.hard_stop: float = params.get("hard_stop", 0.15)
        self.stop_cooldown_h: int = params.get("stop_cooldown_h", 48)
        self.vol_range_tight: float = params.get("vol_range_tight", 0.10)
        self.vol_range_loose: float = params.get("vol_range_loose", 0.20)
        self.max_hold_h: int = params.get("max_hold_h", 240)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 72)
        self.qty_small: int = params.get("qty_small", 10)
        self.qty_medium: int = params.get("qty_medium", 18)
        self.qty_large: int = params.get("qty_large", 25)
        self.max_positions_per_band: int = params.get("max_positions_per_band", 2)

        # Per-band overrides (allow params to override via e.g. "band1_floor_ceiling").
        self._bands: dict[int, dict] = {}
        for b_id, b_cfg in BANDS.items():
            self._bands[b_id] = {
                "min_price": params.get(f"band{b_id}_min_price", b_cfg["min_price"]),
                "floor_ceiling": params.get(f"band{b_id}_floor_ceiling", b_cfg["floor_ceiling"]),
                "floor_stable": params.get(f"band{b_id}_floor_stable", b_cfg["floor_stable"]),
                "week_max_ceiling": params.get(f"band{b_id}_week_max_ceiling", b_cfg["week_max_ceiling"]),
                "capital": params.get(f"band{b_id}_capital", b_cfg["capital"]),
            }

        hist_len = max(self.week_window_h, self.recent_h_large) + 8
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        self._created_at: dict[int, datetime] = {}
        self._promo_ids: set[int] = set()
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._first_ts: datetime | None = None
        self._stopped_until: dict[int, datetime] = {}
        # Pyramid-specific state.
        self._band_of: dict[int, int] = {}  # ea_id -> band that holds the position
        self._band_cash: dict[int, int] = {b_id: self._bands[b_id]["capital"] for b_id in self._bands}

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

    def _band_for_smooth(self, smooth: int) -> int | None:
        """Identify the band the given smoothed price belongs to. None if outside."""
        for b_id, cfg in self._bands.items():
            if cfg["min_price"] <= smooth < cfg["floor_ceiling"]:
                return b_id
        return None

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

        if self._first_ts is None:
            self._first_ts = ts_clean

        # ── SELL pass (unchanged v19 logic, band-aware bookkeeping) ──────────
        for ea_id, price in ticks:
            self._history[ea_id].append(price)

            holding = portfolio.holdings(ea_id)
            if holding > 0:
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
                    elif smooth_pct <= -self.stop_loss:
                        sell = True
                        stopped = True

                if sell:
                    signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                    # Credit the holding band's cash with net proceeds.
                    b_id = self._band_of.pop(ea_id, None)
                    if b_id is not None:
                        # 5% EA tax — matches engine accounting.
                        self._band_cash[b_id] += (price * holding * 95) // 100
                    self._buy_prices.pop(ea_id, None)
                    self._buy_ts.pop(ea_id, None)
                    if stopped:
                        self._stopped_until[ea_id] = ts_clean + timedelta(hours=self.stop_cooldown_h)

        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        # ── BUY pass: evaluate each card against its band's v19 gate ─────────
        # Count currently-held positions per band.
        band_open_count: dict[int, int] = defaultdict(int)
        for _, b_id in self._band_of.items():
            band_open_count[b_id] += 1

        # candidates: list of (ea_id, price, smooth, qty_cap, band_id)
        candidates: list[tuple[int, int, int, int, int]] = []

        for ea_id, price in ticks:
            hist = self._history[ea_id]
            if len(hist) < self.recent_h_min:
                continue
            smooth = self._smooth(hist)
            if smooth <= 0 or self._is_outlier(price, smooth):
                continue

            b_id = self._band_for_smooth(smooth)
            if b_id is None:
                continue
            cfg = self._bands[b_id]

            # Price must be inside the band's [min_price, floor_ceiling) window.
            if not (cfg["min_price"] <= price < cfg["floor_ceiling"]):
                continue

            cooldown_end = self._stopped_until.get(ea_id)
            if cooldown_end and ts_clean < cooldown_end:
                continue

            recent_min = list(hist)[-self.recent_h_min:]
            if any(p > cfg["floor_stable"] for p in recent_min):
                continue
            if min(recent_min) < cfg["min_price"] * 0.9:
                continue

            if len(hist) >= self.week_window_h:
                week = list(hist)[-self.week_window_h:]
                if max(week) > cfg["week_max_ceiling"]:
                    continue
                wk_rng = max(week) / max(1, min(week)) - 1.0
                if wk_rng > self.week_range_max:
                    continue

            if portfolio.holdings(ea_id) > 0:
                continue
            if ea_id in self._promo_ids:
                continue
            created = self._created_at.get(ea_id)
            if created:
                cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
                age_days = (ts_clean - cr_clean).days
                if age_days < self.min_age_days:
                    continue

            # Sizing — mirror v19 tiered qty using the band's own floor_stable/min_price.
            qty_cap = self.qty_small
            if len(hist) >= self.recent_h_large:
                recent_large = list(hist)[-self.recent_h_large:]
                if all(p <= cfg["floor_stable"] for p in recent_large) \
                   and min(recent_large) >= cfg["min_price"] * 0.9:
                    rng = max(recent_large) / max(1, min(recent_large)) - 1.0
                    if rng <= self.vol_range_tight:
                        qty_cap = self.qty_large
                    elif rng <= self.vol_range_loose:
                        qty_cap = self.qty_medium

            candidates.append((ea_id, price, smooth, qty_cap, b_id))

        if not candidates:
            return signals

        # Sort: prefer bigger-qty tiers first; within same qty, prefer cheapest smooth.
        candidates.sort(key=lambda x: (-x[3], x[2]))

        # Rebuild a running view of band cash that reflects this tick's sells
        # (already credited above when SELLs were emitted).
        available_by_band = dict(self._band_cash)
        global_cash_remaining = portfolio.cash
        # Sells credited this tick aren't reflected in portfolio.cash yet but
        # are in _band_cash — account for them at the global level too.
        for s in signals:
            if s.action == "SELL":
                p = next((pp for eid, pp in ticks if eid == s.ea_id), 0)
                global_cash_remaining += (p * s.quantity * 95) // 100

        for ea_id, price, _, qty_cap, b_id in candidates:
            if band_open_count[b_id] >= self.max_positions_per_band:
                continue
            band_cash = available_by_band.get(b_id, 0)
            if band_cash <= 0 or price <= 0:
                continue
            # Max qty we can afford: limited by band cash AND global portfolio cash.
            qty = min(qty_cap, band_cash // price, global_cash_remaining // price)
            if qty <= 0:
                continue
            cost = qty * price
            signals.append(Signal(action="BUY", ea_id=ea_id, quantity=qty))
            self._buy_prices[ea_id] = price
            self._buy_ts[ea_id] = timestamp
            self._band_of[ea_id] = b_id
            available_by_band[b_id] -= cost
            self._band_cash[b_id] -= cost
            global_cash_remaining -= cost
            band_open_count[b_id] += 1

        return signals

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        return [{
            "smooth_window_h": 3,
            "outlier_tol": 0.08,
            "recent_h_min": 24,
            "recent_h_large": 72,
            "week_window_h": 168,
            "week_range_max": 0.25,
            "profit_target": 0.50,
            "stop_loss": 0.10,
            "hard_stop": 0.15,
            "stop_cooldown_h": 48,
            "vol_range_tight": 0.10,
            "vol_range_loose": 0.20,
            "max_hold_h": 240,
            "min_age_days": 7,
            "burn_in_h": 72,
            "qty_small": 10,
            "qty_medium": 18,
            "qty_large": 25,
            "max_positions_per_band": 2,
        }]
