"""Combo v5 — DEDICATED capital pools with asymmetric split (v19: $750k, v15: $250k).

Prior combo failures:
  - combo_v1: 50/50 split — both arms starved.
  - combo_v2: disjoint price bands — gutted pd arm.
  - combo_v3: shared universe competitive — cross-arm cannibalization.
  - combo_v4: strict-priority shared cash — v15 barely fires, v19 holds most cash.

combo_v5 approach — DEDICATED arm-local cash counters:
  - v19 (floor_buy_v19 default params, max_positions=8): budget $750k.
    v19's typical 2-3 simultaneous positions @ qty 10/18/25 × ~$10-13k
    fit within $750k with plenty of headroom.
  - v15 (post_dump_v15 default params): budget $250k. v15's basket=6 ×
    qty=6 × ~$13k ≈ $468k max per trigger — with $250k pool v15 buys
    fewer qty-per-card but still fires regime triggers on every W14/W15
    recovery it detects.

Each arm has its OWN cash counter (`_local_cash`) that tracks what
the arm has spent and received. The arm-local cash is the gate: an
arm only emits BUY signals if its local pool can afford them.

The shared Portfolio ($1M) has plenty of headroom to accept both arms'
combined commitments simultaneously (750 + 250 = 1000 = budget), so
engine-level Portfolio.buy() rejections should be rare. If both arms
bid the same ea_id on the same tick, v19 wins (larger pool, higher
priority on the merge path).

SELL tracking: each arm tracks/sells only its own positions via its
own `_buy_prices` / `_buy_ts` maps (inherited from the two sub-classes).
"""
import logging
from datetime import datetime

from src.algo.models import Portfolio, Position, Signal
from src.algo.strategies.base import Strategy
from src.algo.strategies.floor_buy_v19 import FloorBuyV19Strategy
from src.algo.strategies.post_dump_v15 import PostDumpV15Strategy

logger = logging.getLogger(__name__)


V19_BUDGET = 750_000
V15_BUDGET = 250_000


class _DedicatedCashArm:
    """Per-arm proxy with its OWN cash counter (independent of shared).

    - `.cash` → this arm's local cash balance.
    - `.positions` → only positions whose ea_id is in this arm's set.
    - `.holdings(ea_id)` → delegated to shared portfolio (cross-arm guard).
    - `.buy(qty, price)` → deduct qty*price from local cash; also call
      shared.buy (shared has $1M, will accept).
    - `.sell(qty, price)` → credit qty*price*0.95 (post 5% tax) to local
      cash; also call shared.sell.
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
            # Arm-local affordability check (belt-and-braces; sub-strategy
            # already sizes by .cash).
            return
        self._arm_ids.add(ea_id)
        self._local_cash -= cost
        return self._shared.buy(ea_id, quantity, price, timestamp)

    def sell(self, ea_id: int, quantity: int, price: int, timestamp: datetime):
        # Credit post-tax proceeds (matches engine's 5% EA tax).
        revenue = (price * quantity * 95) // 100
        self._local_cash += revenue
        return self._shared.sell(ea_id, quantity, price, timestamp)


class ComboV5Strategy(Strategy):
    name = "combo_v5"

    def __init__(self, params: dict):
        self.params = params
        self.budget: int = params.get("budget", 1_000_000)

        # Use each arm's default params UNCHANGED.
        fb_params = dict(FloorBuyV19Strategy({}).param_grid_hourly()[0])
        pd_params = dict(PostDumpV15Strategy({}).param_grid_hourly()[0])

        self.floor_buy = FloorBuyV19Strategy(fb_params)
        self.post_dump = PostDumpV15Strategy(pd_params)

        # Per-arm ea_id sets (populated as each arm buys into each card).
        self._fb_ids: set[int] = set()
        self._pd_ids: set[int] = set()

        # Per-arm dedicated cash pools — persistent across ticks so each
        # arm's sell-revenue recycles into its own pool only.
        self._fb_arm = _DedicatedCashArm(None, self._fb_ids, V19_BUDGET)
        self._pd_arm = _DedicatedCashArm(None, self._pd_ids, V15_BUDGET)

    def set_existing_ids(self, existing_ids: set[int]):
        self.floor_buy.set_existing_ids(existing_ids)
        self.post_dump.set_existing_ids(existing_ids)

    def set_created_at_map(self, created_at_map: dict):
        self.floor_buy.set_created_at_map(created_at_map)
        self.post_dump.set_created_at_map(created_at_map)

    def set_listing_counts(self, listing_counts: dict):
        self.floor_buy.set_listing_counts(listing_counts)
        self.post_dump.set_listing_counts(listing_counts)

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        price_by_id = {eid: p for eid, p in ticks}

        # Bind the shared portfolio each tick (same _DedicatedCashArm
        # instance persists so _local_cash survives across ticks).
        self._fb_arm._shared = portfolio
        self._pd_arm._shared = portfolio

        # ── v19 arm (floor_buy_v19) — gated by its $750k local cash ──
        fb_signals_raw = self.floor_buy.on_tick_batch(
            ticks, timestamp, self._fb_arm,
        )

        # ── v15 arm (post_dump_v15) — gated by its $250k local cash ──
        # Order doesn't matter for cash (separate pools); order only
        # matters for same-ea_id same-tick dedupe (v19 wins).
        pd_signals_raw = self.post_dump.on_tick_batch(
            ticks, timestamp, self._pd_arm,
        )

        # Update the arm-local cash ledger from the raw signals emitted
        # by each sub-strategy. The sub-strategies emit Signals but do
        # NOT call arm.buy/sell directly — the engine does. So we must
        # mirror the cash changes here based on the signals they issued.
        for s in fb_signals_raw:
            p = price_by_id.get(s.ea_id, 0)
            if s.action == "BUY" and p > 0:
                self._fb_arm._arm_ids.add(s.ea_id)
                self._fb_arm._local_cash -= p * s.quantity
            elif s.action == "SELL" and p > 0:
                self._fb_arm._local_cash += (p * s.quantity * 95) // 100

        for s in pd_signals_raw:
            p = price_by_id.get(s.ea_id, 0)
            if s.action == "BUY" and p > 0:
                self._pd_arm._arm_ids.add(s.ea_id)
                self._pd_arm._local_cash -= p * s.quantity
            elif s.action == "SELL" and p > 0:
                self._pd_arm._local_cash += (p * s.quantity * 95) // 100

        # ── Merge: v19 first (priority), v15 after; dedupe same-tick BUYs ──
        out: list[Signal] = []
        buy_ids: set[int] = set()
        for s in fb_signals_raw + pd_signals_raw:
            if s.action == "BUY":
                if s.ea_id in buy_ids:
                    # v15 bid same ea_id v19 already bid this tick — drop v15's.
                    # Refund v15's speculative debit.
                    p = price_by_id.get(s.ea_id, 0)
                    if p > 0:
                        self._pd_arm._local_cash += p * s.quantity
                    continue
                buy_ids.add(s.ea_id)
            out.append(s)
        return out

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        return [{"budget": 1_000_000}]
