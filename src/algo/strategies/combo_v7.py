"""Combo v7 — inverted combo_v5 ratio with v23 as primary arm.

Hypothesis: v23 (DoW-gated post_dump) has a clean +0.286 corr and more trades
(30) than v19 (16). By giving v23 the $750k primary pool and v19 the $250k
secondary pool, v23's trades should dominate the merged log and pull the
combined corr toward +0.29 (passing bar 4, |corr| ≤ 0.30) while still
contributing W16 harvest from v19.

Pool split (inverted from combo_v5):
  - v23 (post_dump_v23 default params): $750k — primary (drives corr).
  - v19 (floor_buy_v19 default params, max_positions=8): $250k — secondary.

Architecture is identical to combo_v5: dedicated per-arm cash counters via
`_DedicatedCashArm`, with same-tick BUY dedupe (v23 wins on conflict since
it's the priority arm now).
"""
import logging
from datetime import datetime

from src.algo.models import Portfolio, Position, Signal
from src.algo.strategies.base import Strategy
from src.algo.strategies.floor_buy_v19 import FloorBuyV19Strategy
from src.algo.strategies.post_dump_v23 import PostDumpV23Strategy

logger = logging.getLogger(__name__)


V23_BUDGET = 750_000
V19_BUDGET = 250_000


class _DedicatedCashArm:
    """Per-arm proxy with its OWN cash counter (independent of shared).

    Same contract as combo_v5's _DedicatedCashArm: each arm operates on
    its local cash pool; shared portfolio ($1M) has headroom for both.
    """

    def __init__(self, shared: Portfolio, arm_ids: set[int], initial_cash: int):
        self._shared = shared
        self._arm_ids = arm_ids
        self._local_cash = initial_cash

    @property
    def cash(self) -> int:
        return self._local_cash if self._local_cash > 0 else 0

    @property
    def positions(self) -> list[Position]:
        return [p for p in self._shared.positions if p.ea_id in self._arm_ids]

    @property
    def trades(self):
        return self._shared.trades

    @property
    def balance_history(self):
        return self._shared.balance_history

    def total_value(self, current_prices):
        return self._shared.total_value(current_prices)

    def holdings(self, ea_id: int) -> int:
        return self._shared.holdings(ea_id)

    def buy(self, ea_id: int, quantity: int, price: int, timestamp: datetime):
        cost = price * quantity
        if cost > self._local_cash:
            return
        self._arm_ids.add(ea_id)
        self._local_cash -= cost
        return self._shared.buy(ea_id, quantity, price, timestamp)

    def sell(self, ea_id: int, quantity: int, price: int, timestamp: datetime):
        revenue = (price * quantity * 95) // 100
        self._local_cash += revenue
        return self._shared.sell(ea_id, quantity, price, timestamp)


class ComboV7Strategy(Strategy):
    name = "combo_v7"

    def __init__(self, params: dict):
        self.params = params
        self.budget: int = params.get("budget", 1_000_000)

        # Use each arm's default params UNCHANGED.
        pd_params = dict(PostDumpV23Strategy({}).param_grid_hourly()[0])
        fb_params = dict(FloorBuyV19Strategy({}).param_grid_hourly()[0])

        self.post_dump = PostDumpV23Strategy(pd_params)
        self.floor_buy = FloorBuyV19Strategy(fb_params)

        # Per-arm ea_id sets (populated as each arm buys into each card).
        self._pd_ids: set[int] = set()
        self._fb_ids: set[int] = set()

        # Per-arm dedicated cash pools — v23 primary ($750k), v19 secondary ($250k).
        self._pd_arm = _DedicatedCashArm(None, self._pd_ids, V23_BUDGET)
        self._fb_arm = _DedicatedCashArm(None, self._fb_ids, V19_BUDGET)

    def set_existing_ids(self, existing_ids: set[int]):
        self.post_dump.set_existing_ids(existing_ids)
        self.floor_buy.set_existing_ids(existing_ids)

    def set_created_at_map(self, created_at_map: dict):
        self.post_dump.set_created_at_map(created_at_map)
        self.floor_buy.set_created_at_map(created_at_map)

    def set_listing_counts(self, listing_counts: dict):
        self.post_dump.set_listing_counts(listing_counts)
        self.floor_buy.set_listing_counts(listing_counts)

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        price_by_id = {eid: p for eid, p in ticks}

        # Bind the shared portfolio each tick.
        self._pd_arm._shared = portfolio
        self._fb_arm._shared = portfolio

        # ── v23 arm (post_dump_v23) — PRIMARY, gated by its $750k local cash ──
        pd_signals_raw = self.post_dump.on_tick_batch(
            ticks, timestamp, self._pd_arm,
        )

        # ── v19 arm (floor_buy_v19) — SECONDARY, gated by its $250k local cash ──
        fb_signals_raw = self.floor_buy.on_tick_batch(
            ticks, timestamp, self._fb_arm,
        )

        # Mirror cash changes from signals (engine calls portfolio, not arms).
        for s in pd_signals_raw:
            p = price_by_id.get(s.ea_id, 0)
            if s.action == "BUY" and p > 0:
                self._pd_arm._arm_ids.add(s.ea_id)
                self._pd_arm._local_cash -= p * s.quantity
            elif s.action == "SELL" and p > 0:
                self._pd_arm._local_cash += (p * s.quantity * 95) // 100

        for s in fb_signals_raw:
            p = price_by_id.get(s.ea_id, 0)
            if s.action == "BUY" and p > 0:
                self._fb_arm._arm_ids.add(s.ea_id)
                self._fb_arm._local_cash -= p * s.quantity
            elif s.action == "SELL" and p > 0:
                self._fb_arm._local_cash += (p * s.quantity * 95) // 100

        # ── Merge: v23 first (priority, primary arm), v19 after; dedupe same-tick BUYs ──
        out: list[Signal] = []
        buy_ids: set[int] = set()
        for s in pd_signals_raw + fb_signals_raw:
            if s.action == "BUY":
                if s.ea_id in buy_ids:
                    # v19 bid same ea_id v23 already bid this tick — drop v19's.
                    # Refund v19's speculative debit.
                    p = price_by_id.get(s.ea_id, 0)
                    if p > 0:
                        self._fb_arm._local_cash += p * s.quantity
                    continue
                buy_ids.add(s.ea_id)
            out.append(s)
        return out

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        return [{"budget": 1_000_000}]
