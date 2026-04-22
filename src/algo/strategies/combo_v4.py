"""Combo v4 — strict-priority stacking: floor_buy_v19 FIRST, post_dump_v15 SECOND.

Prior combo failures:
  - combo_v1: 50/50 capital split — both arms starved, -$X.
  - combo_v2: disjoint price bands — gutted post_dump arm (<$18k cards).
  - combo_v3: shared full-universe competitive — universe race
    cannibalized both arms.

combo_v4 approach — STRICT PRIORITY ORDERING on a shared $1M pool:
  - floor_buy_v19 (= the v21 baseline: same params, max_positions=8 default)
    fires FIRST every tick and sees the full shared-portfolio cash.
  - Its BUY signals are added directly to the output — those represent
    v19's cash commitment at this tick.
  - post_dump_v15 fires SECOND on a proxy whose `.cash` reports
    `shared.cash - pending_v19_cost` (so v15 sees residual cash AFTER
    v19's commitment). v15's BUYs only fire if residual is sufficient —
    if not, Portfolio.buy() will reject them at engine level as well.
  - Both arms see the FULL universe (no price-band filter).
  - Rationale: v21 uses only ~16 trades across 27 days → massive idle
    cash v15 can deploy without competing with v21 for entries.

SELL tracking: each arm tracks/sells only its own positions via its
own `_buy_prices` / `_buy_ts` maps.

No-double-buy (cross-arm same ea_id): the `.holdings()` check in each
arm's candidate loop delegates to the shared portfolio, so if v19 holds
card X, v15 skips it (and vice versa). If both arms race the same
ea_id on the same tick (neither has recorded yet), end-of-tick dedupe
keeps v19's BUY (priority).
"""
import logging
from datetime import datetime

from src.algo.models import Portfolio, Position, Signal
from src.algo.strategies.base import Strategy
from src.algo.strategies.floor_buy_v19 import FloorBuyV19Strategy
from src.algo.strategies.post_dump_v15 import PostDumpV15Strategy

logger = logging.getLogger(__name__)


class _ArmPortfolio:
    """Per-arm proxy view over a shared Portfolio.

    - `.cash` → live shared cash.
    - `.positions` → only positions whose ea_id is in this arm's set.
    - `.holdings(ea_id)` → delegated to shared portfolio (cross-arm
      double-buy guard).
    - `.buy()` / `.sell()` → mutate shared portfolio and record ea_id
      into this arm's id set.
    """

    def __init__(self, shared: Portfolio, arm_ids: set[int]):
        self._shared = shared
        self._arm_ids = arm_ids

    @property
    def cash(self) -> int:
        return self._shared.cash

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
        self._arm_ids.add(ea_id)
        return self._shared.buy(ea_id, quantity, price, timestamp)

    def sell(self, ea_id: int, quantity: int, price: int, timestamp: datetime):
        return self._shared.sell(ea_id, quantity, price, timestamp)


class _ResidualCashPortfolio(_ArmPortfolio):
    """Arm portfolio that reports cash net of a fixed pending reservation.

    Used for the SECOND-priority arm: its `.cash` reports
    `shared.cash - pending_reserved`, so its internal BUY sizing only
    allocates from the residual after the first-priority arm has
    committed. Other reads/writes pass through to shared.
    """

    def __init__(self, shared: Portfolio, arm_ids: set[int], pending_reserved: int):
        super().__init__(shared, arm_ids)
        self._pending_reserved = pending_reserved

    @property
    def cash(self) -> int:
        residual = self._shared.cash - self._pending_reserved
        return residual if residual > 0 else 0


class ComboV4Strategy(Strategy):
    name = "combo_v4"

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

        # ── FIRST-PRIORITY: floor_buy_v19 sees the full shared cash ──
        fb_port = _ArmPortfolio(portfolio, self._fb_ids)
        fb_signals_raw = self.floor_buy.on_tick_batch(ticks, timestamp, fb_port)

        # Record v19's BUY ea_ids and compute the cost it's committing
        # this tick. v19 subtracts expected sell-revenue from its BUY
        # sizing internally, so pending_fb_cost is the marginal NEW
        # cash outflow from its BUYs only (SELLs add cash; v15's sizing
        # naturally sees that via shared.cash after engine applies).
        # For residual calc we only need to reserve BUY outflow.
        pending_fb_cost = 0
        fb_buy_ids: set[int] = set()
        for s in fb_signals_raw:
            if s.action == "BUY":
                self._fb_ids.add(s.ea_id)
                fb_buy_ids.add(s.ea_id)
                p = price_by_id.get(s.ea_id, 0)
                pending_fb_cost += p * s.quantity

        # ── SECOND-PRIORITY: post_dump_v15 on residual cash ──
        pd_port = _ResidualCashPortfolio(portfolio, self._pd_ids, pending_fb_cost)
        pd_signals_raw = self.post_dump.on_tick_batch(ticks, timestamp, pd_port)

        pd_signals: list[Signal] = []
        for s in pd_signals_raw:
            if s.action == "BUY":
                self._pd_ids.add(s.ea_id)
            pd_signals.append(s)

        # ── Merge: v19 first, v15 after; dedupe same-tick BUYs (v19 wins) ──
        out: list[Signal] = []
        buy_ids: set[int] = set()
        for s in fb_signals_raw + pd_signals:
            if s.action == "BUY":
                if s.ea_id in buy_ids:
                    continue
                buy_ids.add(s.ea_id)
            out.append(s)
        return out

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        return [{"budget": 1_000_000}]
