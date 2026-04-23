"""Combo v8 — DEDICATED capital pools (v19: $750k, v23: $250k).

Replicates combo_v5's dedicated-capital architecture but swaps the
post-dump arm from v15 → v23. Hypothesis: v23's improved corr profile
(DoW-gated trig_a) blended into combo_v5's dominant v19 harvest brings
merged |corr| under 0.30 while preserving v19's $377k PnL contribution.

Prior combo reference:
  - combo_v5 (v19 $750k + v15 $250k): +$289k, 78% win, 4/6 bars, high corr.
  - combo_v7 (v23 $750k + v19 $250k): +$69.5k, 60% win, 3/6 bars, W15 drag.

combo_v8 approach — DEDICATED arm-local cash counters:
  - v19 (floor_buy_v19 default params): budget $750k.
  - v23 (post_dump_v23 default params, DoW-gated trig_a): budget $250k.

Each arm has its OWN cash counter (`_local_cash`) that tracks what
the arm has spent and received. The arm-local cash is the gate: an
arm only emits BUY signals if its local pool can afford them.

SELL tracking: each arm tracks/sells only its own positions via its
own `_buy_prices` / `_buy_ts` maps (inherited from the two sub-classes).
"""
import logging
from datetime import datetime

from src.algo.models import Portfolio, Position, Signal
from src.algo.strategies.base import Strategy
from src.algo.strategies.floor_buy_v19 import FloorBuyV19Strategy
from src.algo.strategies.post_dump_v23 import PostDumpV23Strategy

logger = logging.getLogger(__name__)


V19_BUDGET = 750_000
V23_BUDGET = 250_000


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
            return
        self._arm_ids.add(ea_id)
        self._local_cash -= cost
        return self._shared.buy(ea_id, quantity, price, timestamp)

    def sell(self, ea_id: int, quantity: int, price: int, timestamp: datetime):
        revenue = (price * quantity * 95) // 100
        self._local_cash += revenue
        return self._shared.sell(ea_id, quantity, price, timestamp)


class ComboV8Strategy(Strategy):
    name = "combo_v8"

    def __init__(self, params: dict):
        self.params = params
        self.budget: int = params.get("budget", 1_000_000)

        # Use each arm's default params UNCHANGED.
        fb_params = dict(FloorBuyV19Strategy({}).param_grid_hourly()[0])
        pd_params = dict(PostDumpV23Strategy({}).param_grid_hourly()[0])

        self.floor_buy = FloorBuyV19Strategy(fb_params)
        self.post_dump = PostDumpV23Strategy(pd_params)

        # Per-arm ea_id sets (populated as each arm buys into each card).
        self._fb_ids: set[int] = set()
        self._pd_ids: set[int] = set()

        # Per-arm dedicated cash pools — persistent across ticks so each
        # arm's sell-revenue recycles into its own pool only.
        self._fb_arm = _DedicatedCashArm(None, self._fb_ids, V19_BUDGET)
        self._pd_arm = _DedicatedCashArm(None, self._pd_ids, V23_BUDGET)

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

        # ── v23 arm (post_dump_v23) — gated by its $250k local cash ──
        pd_signals_raw = self.post_dump.on_tick_batch(
            ticks, timestamp, self._pd_arm,
        )

        # Update arm-local cash ledger from emitted signals. Sub-strategies
        # emit Signals; the engine calls arm.buy/sell — we mirror the
        # cash changes here based on signals they issued.
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

        # ── Merge: v19 first (priority), v23 after; dedupe same-tick BUYs ──
        out: list[Signal] = []
        buy_ids: set[int] = set()
        for s in fb_signals_raw + pd_signals_raw:
            if s.action == "BUY":
                if s.ea_id in buy_ids:
                    # v23 bid same ea_id v19 already bid this tick — drop v23's.
                    # Refund v23's speculative debit.
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
