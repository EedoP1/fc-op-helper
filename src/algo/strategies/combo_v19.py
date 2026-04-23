"""Combo v19 — v18 with middle arm swapped to v38 (W13/W14/W15 BUYs).

combo_v18 restricted the v37 middle arm to W13/W14 only, which preserves v19's
W16 harvest but surrenders v36's natural W15 contribution (~$14k standalone).
v38 = v36 with BUYs allowed in W13/W14/W15 but blocked in W16, letting the
middle arm reclaim W15 coverage while still keeping v19 exclusive on W16.

Budgets: v19 $650k (full W16 harvest) + v38 $200k (W13/W14/W15 buys) + v23
$150k (corr support).
"""
import logging
from datetime import datetime

from src.algo.models import Portfolio, Position, Signal
from src.algo.strategies.base import Strategy
from src.algo.strategies.floor_buy_v19 import FloorBuyV19Strategy
from src.algo.strategies.floor_buy_v38 import FloorBuyV38Strategy
from src.algo.strategies.post_dump_v23 import PostDumpV23Strategy

logger = logging.getLogger(__name__)


V19_BUDGET = 650_000
V38_BUDGET = 200_000
V23_BUDGET = 150_000


class _DedicatedCashArm:
    """Per-arm proxy with its OWN cash counter (independent of shared)."""

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


class ComboV19Strategy(Strategy):
    name = "combo_v19"

    def __init__(self, params: dict):
        self.params = params
        self.budget: int = params.get("budget", 1_000_000)

        # Use each arm's default params UNCHANGED.
        fb19_params = dict(FloorBuyV19Strategy({}).param_grid_hourly()[0])
        fb38_params = dict(FloorBuyV38Strategy({}).param_grid_hourly()[0])
        pd23_params = dict(PostDumpV23Strategy({}).param_grid_hourly()[0])

        self.floor_buy_v19 = FloorBuyV19Strategy(fb19_params)
        self.floor_buy_v38 = FloorBuyV38Strategy(fb38_params)
        self.post_dump_v23 = PostDumpV23Strategy(pd23_params)

        # Per-arm ea_id sets (populated as each arm buys into each card).
        self._fb19_ids: set[int] = set()
        self._fb38_ids: set[int] = set()
        self._pd23_ids: set[int] = set()

        # Per-arm dedicated cash pools — persistent across ticks.
        self._fb19_arm = _DedicatedCashArm(None, self._fb19_ids, V19_BUDGET)
        self._fb38_arm = _DedicatedCashArm(None, self._fb38_ids, V38_BUDGET)
        self._pd23_arm = _DedicatedCashArm(None, self._pd23_ids, V23_BUDGET)

    def set_existing_ids(self, existing_ids: set[int]):
        self.floor_buy_v19.set_existing_ids(existing_ids)
        self.floor_buy_v38.set_existing_ids(existing_ids)
        self.post_dump_v23.set_existing_ids(existing_ids)

    def set_created_at_map(self, created_at_map: dict):
        self.floor_buy_v19.set_created_at_map(created_at_map)
        self.floor_buy_v38.set_created_at_map(created_at_map)
        self.post_dump_v23.set_created_at_map(created_at_map)

    def set_listing_counts(self, listing_counts: dict):
        self.floor_buy_v19.set_listing_counts(listing_counts)
        self.floor_buy_v38.set_listing_counts(listing_counts)
        self.post_dump_v23.set_listing_counts(listing_counts)

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        price_by_id = {eid: p for eid, p in ticks}

        # Bind the shared portfolio each tick.
        self._fb19_arm._shared = portfolio
        self._fb38_arm._shared = portfolio
        self._pd23_arm._shared = portfolio

        # ── v19 arm — gated by its $650k local cash ──
        fb19_signals_raw = self.floor_buy_v19.on_tick_batch(
            ticks, timestamp, self._fb19_arm,
        )

        # ── v38 arm — gated by its $200k local cash ──
        fb38_signals_raw = self.floor_buy_v38.on_tick_batch(
            ticks, timestamp, self._fb38_arm,
        )

        # ── v23 arm — gated by its $150k local cash ──
        pd23_signals_raw = self.post_dump_v23.on_tick_batch(
            ticks, timestamp, self._pd23_arm,
        )

        # Update arm-local cash ledgers from emitted signals.
        for s in fb19_signals_raw:
            p = price_by_id.get(s.ea_id, 0)
            if s.action == "BUY" and p > 0:
                self._fb19_arm._arm_ids.add(s.ea_id)
                self._fb19_arm._local_cash -= p * s.quantity
            elif s.action == "SELL" and p > 0:
                self._fb19_arm._local_cash += (p * s.quantity * 95) // 100

        for s in fb38_signals_raw:
            p = price_by_id.get(s.ea_id, 0)
            if s.action == "BUY" and p > 0:
                self._fb38_arm._arm_ids.add(s.ea_id)
                self._fb38_arm._local_cash -= p * s.quantity
            elif s.action == "SELL" and p > 0:
                self._fb38_arm._local_cash += (p * s.quantity * 95) // 100

        for s in pd23_signals_raw:
            p = price_by_id.get(s.ea_id, 0)
            if s.action == "BUY" and p > 0:
                self._pd23_arm._arm_ids.add(s.ea_id)
                self._pd23_arm._local_cash -= p * s.quantity
            elif s.action == "SELL" and p > 0:
                self._pd23_arm._local_cash += (p * s.quantity * 95) // 100

        # ── Merge: v19 → v38 → v23 priority; dedupe same-tick BUYs ──
        out: list[Signal] = []
        buy_ids: set[int] = set()

        for s in fb19_signals_raw:
            if s.action == "BUY":
                if s.ea_id in buy_ids:
                    continue
                buy_ids.add(s.ea_id)
            out.append(s)

        for s in fb38_signals_raw:
            if s.action == "BUY":
                if s.ea_id in buy_ids:
                    # Duplicate with v19's BUY — drop and refund v38.
                    p = price_by_id.get(s.ea_id, 0)
                    if p > 0:
                        self._fb38_arm._local_cash += p * s.quantity
                    continue
                buy_ids.add(s.ea_id)
            out.append(s)

        for s in pd23_signals_raw:
            if s.action == "BUY":
                if s.ea_id in buy_ids:
                    # Duplicate with v19/v38's BUY — drop and refund v23.
                    p = price_by_id.get(s.ea_id, 0)
                    if p > 0:
                        self._pd23_arm._local_cash += p * s.quantity
                    continue
                buy_ids.add(s.ea_id)
            out.append(s)

        return out

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        return [{"budget": 1_000_000}]
