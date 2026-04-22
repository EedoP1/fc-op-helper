"""Combo v3 — post_dump_v15 + floor_buy_v19 with SHARED capital, FULL universe.

Fixes two prior combo failures:
  - combo_v1: split $500k/$500k capital halved each arm's compounding.
  - combo_v2: disjoint price bands ($<=16k for fb, $>=18k for pd) gutted
    the post_dump arm because its organic baskets live below $18k.

combo_v3 approach:
  - SHARED $1M capital pool (via the real Portfolio).
  - BOTH arms see the FULL universe (no price-band filter).
  - Per-arm `_ArmPortfolio` proxy so each arm's own `.positions` /
    `max_positions` logic stays per-arm.
  - Cross-arm no-double-buy: `portfolio.holdings(ea_id)` delegates to the
    shared Portfolio, so if pd holds card X, fb's per-card check
    `portfolio.holdings(ea_id) > 0` will skip — and vice versa. No
    price-band gate needed.
  - End-of-tick BUY dedupe: if both arms race to the same card on the
    same tick (neither has recorded the holding yet), only the first
    wins.

Standalone baselines:
  - post_dump_v15:   +$144.6k, 83% win, all 6 bars PASS, W14/W15 coverage
  - floor_buy_v19:   +$345.8k, 80% win, W16 +35% (smashes 25%/wk in W16)

Expected combined PnL: ~$400k+ with pd contributing W14/W15 coverage and
fb contributing the W16 surge. Capital competition is resolved by the
engine's Portfolio.buy() rejecting insufficient-cash BUYs.
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

    - `.cash` → live shared cash (so both arms see capital competition).
    - `.positions` → only the positions whose ea_id is in this arm's set.
    - `.holdings(ea_id)` → delegated to the shared portfolio (so the
      other arm's holdings also block this arm's BUY on that card).
    - `.buy()` / `.sell()` → mutate the shared portfolio AND record the
      ea_id in the arm's own-id set so future `.positions` filters work.
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


class ComboV3Strategy(Strategy):
    name = "combo_v3"

    def __init__(self, params: dict):
        self.params = params
        self.budget: int = params.get("budget", 1_000_000)

        # Use each arm's default params UNCHANGED — do NOT override
        # max_positions. We want each arm's default behavior.
        pd_params = dict(PostDumpV15Strategy({}).param_grid_hourly()[0])
        fb_params = dict(FloorBuyV19Strategy({}).param_grid_hourly()[0])

        self.post_dump = PostDumpV15Strategy(pd_params)
        self.floor_buy = FloorBuyV19Strategy(fb_params)

        # Per-arm ea_id sets (populated as each arm buys into each card).
        self._pd_ids: set[int] = set()
        self._fb_ids: set[int] = set()

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
        # Per-arm proxy views onto the shared portfolio.
        pd_port = _ArmPortfolio(portfolio, self._pd_ids)
        fb_port = _ArmPortfolio(portfolio, self._fb_ids)

        # Each arm emits against its own proxy. Both arms see the FULL
        # universe — no price-band filter. Cross-arm no-double-buy is
        # enforced by the shared portfolio's holdings() check inside
        # each arm's candidate loop.
        pd_signals_raw = self.post_dump.on_tick_batch(ticks, timestamp, pd_port)
        fb_signals_raw = self.floor_buy.on_tick_batch(ticks, timestamp, fb_port)

        # Record ea_ids for arm-scoped position filtering.
        pd_signals: list[Signal] = []
        for s in pd_signals_raw:
            if s.action == "BUY":
                self._pd_ids.add(s.ea_id)
            pd_signals.append(s)

        fb_signals: list[Signal] = []
        for s in fb_signals_raw:
            if s.action == "BUY":
                self._fb_ids.add(s.ea_id)
            fb_signals.append(s)

        # Union + dedupe-by-ea_id within same tick (safety: if both arms
        # both race to BUY the same card on the same tick, neither has
        # yet recorded the holding so the shared-portfolio check won't
        # catch it. Keep first, drop duplicate).
        out: list[Signal] = []
        buy_ids: set[int] = set()
        for s in pd_signals + fb_signals:
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
