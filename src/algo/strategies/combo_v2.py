"""Combo v2 — floor_buy_v22 ($<=16k band) + post_dump_v15 ($>=18k band).

Iter 5 lesson: splitting CAPITAL halved each arm's compounding (combo_v1
organic +$73k, far below either arm standalone). Splitting the UNIVERSE
instead keeps a shared $1M pool and compounds fully per arm, while the
disjoint price bands prevent:
  - Double-buying the same card
  - One arm exiting into the other's entry
  - Max-positions capping by aggregate rather than per-arm

Arms:
  - floor_buy_v22: only emits BUYs for cards whose tick price <= $16,000.
    v22's own floor_ceiling=13000 already gates this — the $16k guard is
    a belt-and-braces check in case smoothing pushes near-floor cards
    through.
  - post_dump_v15: only emits BUYs for cards whose tick price >= $18,000.
    v15's own min_price default is 11k; we raise the effective floor to
    18k so the two arms' universes are disjoint.

Shared Portfolio with a per-arm ProxyPortfolio that:
  - Reports ONLY that arm's positions for `.positions` (so each arm's
    own `max_positions` cap is per-arm, not aggregate).
  - Reads the live shared cash for `.cash`.
  - BUYs and SELLs on the real shared Portfolio, so capital competition
    is resolved at the engine level via Portfolio.buy() rejecting
    insufficient funds (when cash runs low the later arm's BUY is
    silently dropped).

SELL tracking: each arm only tracks/sells positions it itself bought
(separate `_buy_prices` / `_buy_ts` inside each sub-strategy). Disjoint
universes guarantee no overlap, but the arm-internal state ensures
correctness even without that.
"""
import logging
from datetime import datetime

from src.algo.models import Portfolio, Position, Signal
from src.algo.strategies.base import Strategy
from src.algo.strategies.floor_buy_v22 import FloorBuyV22Strategy
from src.algo.strategies.post_dump_v15 import PostDumpV15Strategy

logger = logging.getLogger(__name__)


# Price-band split — $16k (fb top) and $18k (pd bottom), 2k gap so a card
# drifting between bands isn't grabbed by both arms on back-to-back ticks.
FB_MAX_PRICE = 16_000
PD_MIN_PRICE = 18_000


class _ArmPortfolio:
    """Per-arm proxy view over a shared Portfolio.

    - `.cash` → live shared cash (so both arms see capital competition).
    - `.positions` → only the positions whose ea_id is in this arm's set.
    - `.holdings(ea_id)` → delegated to the shared portfolio (correct by
      disjoint-universe design — arm's ea_ids never overlap the other arm).
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


class ComboV2Strategy(Strategy):
    name = "combo_v2"

    def __init__(self, params: dict):
        self.params = params
        self.budget: int = params.get("budget", 1_000_000)

        pd_params = dict(PostDumpV15Strategy({}).param_grid_hourly()[0])
        pd_params["max_positions"] = 8  # per brief: pd arm cap = 8

        fb_params = dict(FloorBuyV22Strategy({}).param_grid_hourly()[0])
        fb_params["max_positions"] = 8  # per brief: fb arm cap = 8

        self.post_dump = PostDumpV15Strategy(pd_params)
        self.floor_buy = FloorBuyV22Strategy(fb_params)

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
        # Price index for band filtering on BUY signals.
        price_by_id = {eid: p for eid, p in ticks}

        # Per-arm proxy views onto the shared portfolio.
        pd_port = _ArmPortfolio(portfolio, self._pd_ids)
        fb_port = _ArmPortfolio(portfolio, self._fb_ids)

        # Each arm emits against its own proxy. SELLs target only the arm's
        # own tracked positions (arm-internal _buy_prices state).
        pd_signals_raw = self.post_dump.on_tick_batch(ticks, timestamp, pd_port)
        fb_signals_raw = self.floor_buy.on_tick_batch(ticks, timestamp, fb_port)

        # Price-band filter on BUYs only (SELLs flow through unchanged).
        pd_signals: list[Signal] = []
        for s in pd_signals_raw:
            if s.action == "BUY":
                price = price_by_id.get(s.ea_id, 0)
                if price < PD_MIN_PRICE:
                    continue
                self._pd_ids.add(s.ea_id)
            pd_signals.append(s)

        fb_signals: list[Signal] = []
        for s in fb_signals_raw:
            if s.action == "BUY":
                price = price_by_id.get(s.ea_id, 0)
                if price <= 0 or price > FB_MAX_PRICE:
                    continue
                self._fb_ids.add(s.ea_id)
            fb_signals.append(s)

        # Union + dedupe-by-ea_id within same tick (safety: disjoint bands
        # mean this shouldn't fire, but guard in case both arms both pass
        # their local filters in an edge case).
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
