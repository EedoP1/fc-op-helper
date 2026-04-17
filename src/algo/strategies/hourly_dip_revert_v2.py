"""Hourly dip reversion v2 — fixes iter 1 cold-start + empty week 15.

Iter 1 made +9.9M in W14 (with cold-start leakage from Mar 28 — 49k snapshots vs
~300k/day normal) but zero in W15. Causes:
  - Cold-start: 24h of history was enough to trigger "dips" on Mar 28 lows.
  - Ballooning: +13M portfolio + 15% per-trade = 2M/card → filter out nothing but
    liquidity is still capped implicitly; later dips may have been too small to
    register as ≥5% below a 48h median.
  - Friday skip: threw away all Friday dips → reduced signal density.

Fixes:
  - Burn-in: no buys until 72h after strategy's first observed timestamp.
  - Allow Fridays (but still exclude promo_id cards → correlation stays low).
  - Looser dip threshold (3%) + shorter median window (24h) to catch
    weekday intraday dips that 48h medians would smooth out.
  - Cap position size to a fixed coin amount so we keep firing even after
    early wins.
"""
import logging
from datetime import datetime
from collections import defaultdict, deque

from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


class HourlyDipRevertV2Strategy(Strategy):
    """Non-promo intraday dip buyer, burn-in + fixed sizing."""

    name = "hourly_dip_revert_v2"

    def __init__(self, params: dict):
        self.params = params
        self.median_window_h: int = params.get("median_window_h", 24)
        self.dip_pct: float = params.get("dip_pct", 0.05)
        self.profit_target: float = params.get("profit_target", 0.08)
        self.stop_loss: float = params.get("stop_loss", 0.12)
        self.max_hold_h: int = params.get("max_hold_h", 36)
        self.min_price: int = params.get("min_price", 10000)
        self.max_price: int = params.get("max_price", 150000)
        self.max_positions: int = params.get("max_positions", 6)
        self.min_age_days: int = params.get("min_age_days", 7)
        self.burn_in_h: int = params.get("burn_in_h", 72)
        self.position_cap: int = params.get("position_cap", 200_000)
        self.position_pct: float = params.get("position_pct", 0.15)

        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=self.median_window_h + 4))
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

    def _median(self, history: deque) -> int:
        if not history:
            return 0
        sorted_prices = sorted(history)
        return sorted_prices[len(sorted_prices) // 2]

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

        if self._first_ts is None:
            self._first_ts = ts_clean

        # ── Update history and check sells ──

        for ea_id, price in ticks:
            self._history[ea_id].append(price)

            holding = portfolio.holdings(ea_id)
            if holding > 0:
                buy_price = self._buy_prices.get(ea_id, price)
                buy_ts = self._buy_ts.get(ea_id, ts_clean)
                bt_clean = buy_ts.replace(tzinfo=None) if buy_ts.tzinfo else buy_ts
                hold_hours = (ts_clean - bt_clean).total_seconds() / 3600
                pct = (price - buy_price) / buy_price if buy_price > 0 else 0

                sell = False
                if pct >= self.profit_target:
                    sell = True
                elif pct <= -self.stop_loss:
                    sell = True
                elif hold_hours >= self.max_hold_h:
                    sell = True
                else:
                    med = self._median(self._history[ea_id])
                    if price >= med and price >= int(buy_price * 1.07):
                        sell = True

                if sell:
                    signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                    self._buy_prices.pop(ea_id, None)
                    self._buy_ts.pop(ea_id, None)

        # ── Burn-in ──
        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600
        if elapsed_h < self.burn_in_h:
            return signals

        if len(portfolio.positions) >= self.max_positions:
            return signals

        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                price = next((p for eid, p in ticks if eid == s.ea_id), 0)
                sell_rev += (price * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

        candidates: list[tuple[int, int, float]] = []
        for ea_id, price in ticks:
            if portfolio.holdings(ea_id) > 0:
                continue
            if ea_id in self._promo_ids:
                continue
            if not (self.min_price <= price <= self.max_price):
                continue
            created = self._created_at.get(ea_id)
            if created:
                cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
                age_days = (ts_clean - cr_clean).days
                if age_days < self.min_age_days:
                    continue
            hist = self._history[ea_id]
            if len(hist) < self.median_window_h:
                continue
            med = self._median(hist)
            if med <= 0:
                continue
            dip = (med - price) / med
            if dip >= self.dip_pct:
                candidates.append((ea_id, price, dip))

        if not candidates:
            return signals

        candidates.sort(key=lambda x: x[2], reverse=True)

        price_map = {eid: p for eid, p in ticks}
        held_value = sum(
            price_map.get(pos.ea_id, pos.buy_price) * pos.quantity
            for pos in portfolio.positions
        )
        portfolio_value = available + held_value
        per_card_budget = min(
            int(portfolio_value * self.position_pct),
            self.position_cap,
        )

        open_slots = self.max_positions - len(portfolio.positions)
        buys_made = 0
        for ea_id, price, dip in candidates:
            if buys_made >= open_slots:
                break
            if available <= 0:
                break
            spend = min(per_card_budget, available)
            qty = spend // price if price > 0 else 0
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
        base = {
            "median_window_h": 24,
            "profit_target": 0.08,
            "stop_loss": 0.12,
            "max_hold_h": 36,
            "min_price": 10000,
            "max_price": 150000,
            "max_positions": 6,
            "min_age_days": 7,
            "burn_in_h": 72,
            "position_cap": 200_000,
        }
        combos = []
        for dip_pct in [0.03, 0.05, 0.08]:
            for median_window_h in [12, 24, 48]:
                for position_pct in [0.15, 0.25]:
                    combos.append({
                        **base,
                        "dip_pct": dip_pct,
                        "median_window_h": median_window_h,
                        "position_pct": position_pct,
                    })
        return combos
