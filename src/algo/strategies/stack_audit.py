"""Stack audit — runs all 8 claimed-stack strategies as ONE combined portfolio.

Iter94 verification: the claimed "$1.49M paper stack" was derived by summing
self-reported single-run/Δstack PnLs across iterations, never measured with
the strategies actually running together. This combo measures the TRUE
combined PnL accounting for:
  1. Trade-level overlap (two strategies firing on same ea_id × hour →
     only one can hold).
  2. Cash-flow / capital sharing (one strategy's exit frees capital for
     another's entry — but slot count and cash are FINITE).

Stack members (8 strategies):
  floor_buy_v19, floor_buy_v19_ext, floor_buy_v24,
  post_dump_v15,
  daily_trend_dip_v5,
  monday_rebound_v1,
  mid_dip_v2, low_dip_v3.

Two budget modes (param_grid):
  - mode="equal_split":  $125k per arm, total $1M shared portfolio.
                         Realistic single-account deployment.
  - mode="no_contention": $1M per arm (8x parallel sandboxes via
                         per-arm cash, single shared trade ledger).
                         Isolates pure trade-overlap penalty
                         (no capital constraint).

Implementation follows the combo_v22 _DedicatedCashArm pattern: each arm
has its own cash counter, the shared Portfolio holds positions+trades,
and same-tick BUY signals on the same ea_id are de-duplicated (priority
order: as listed above).
"""
import logging
from datetime import datetime

from src.algo.models import Portfolio, Position, Signal
from src.algo.strategies.base import Strategy
from src.algo.strategies.daily_trend_dip_v5 import DailyTrendDipV5Strategy
from src.algo.strategies.floor_buy_v19 import FloorBuyV19Strategy
from src.algo.strategies.floor_buy_v19_ext import FloorBuyV19ExtStrategy
from src.algo.strategies.floor_buy_v24 import FloorBuyV24Strategy
from src.algo.strategies.low_dip_v3 import LowDipV3Strategy
from src.algo.strategies.mid_dip_v2 import MidDipV2Strategy
from src.algo.strategies.monday_rebound_v1 import MondayReboundV1Strategy
from src.algo.strategies.post_dump_v15 import PostDumpV15Strategy

logger = logging.getLogger(__name__)


# Priority order: best-single-PnL first, so that on same-tick conflicts the
# higher-PnL strategy wins the trade. Matches the bias in our Δstack claims.
_STACK_MEMBERS = [
    ("floor_buy_v19", FloorBuyV19Strategy),
    ("floor_buy_v24", FloorBuyV24Strategy),
    ("floor_buy_v19_ext", FloorBuyV19ExtStrategy),
    ("low_dip_v3", LowDipV3Strategy),
    ("mid_dip_v2", MidDipV2Strategy),
    ("daily_trend_dip_v5", DailyTrendDipV5Strategy),
    ("monday_rebound_v1", MondayReboundV1Strategy),
    ("post_dump_v15", PostDumpV15Strategy),
]


class _DedicatedCashArm:
    """Per-arm proxy with own cash counter (lifted from combo_v22)."""

    def __init__(self, shared: Portfolio | None, arm_ids: set[int], initial_cash: int):
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


class StackAuditStrategy(Strategy):
    name = "stack_audit"

    def __init__(self, params: dict):
        self.params = params
        self.mode: str = params.get("mode", "equal_split")
        self.budget: int = params.get("budget", 1_000_000)

        # Per-arm budget allocation
        if self.mode == "equal_split":
            per_arm = self.budget // len(_STACK_MEMBERS)
            self._arm_budgets = {n: per_arm for n, _ in _STACK_MEMBERS}
        elif self.mode == "no_contention":
            # Each arm gets full budget (isolates trade-overlap penalty
            # from capital-contention penalty).
            self._arm_budgets = {n: self.budget for n, _ in _STACK_MEMBERS}
        else:
            raise ValueError(f"Unknown mode: {self.mode}")

        # Instantiate each arm with its default param grid's first combo.
        self._arms: list[tuple[str, Strategy, _DedicatedCashArm, set[int]]] = []
        for name, cls in _STACK_MEMBERS:
            sample = cls({})
            grid = (
                sample.param_grid_hourly()
                if hasattr(sample, "param_grid_hourly")
                else sample.param_grid()
            )
            instance = cls(dict(grid[0]))
            arm_ids: set[int] = set()
            arm = _DedicatedCashArm(None, arm_ids, self._arm_budgets[name])
            self._arms.append((name, instance, arm, arm_ids))

    def set_existing_ids(self, existing_ids: set[int]):
        for _, inst, _, _ in self._arms:
            inst.set_existing_ids(existing_ids)

    def set_created_at_map(self, created_at_map: dict):
        for _, inst, _, _ in self._arms:
            inst.set_created_at_map(created_at_map)

    def set_listing_counts(self, listing_counts: dict):
        for _, inst, _, _ in self._arms:
            inst.set_listing_counts(listing_counts)

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        price_by_id = {eid: p for eid, p in ticks}

        # Bind shared portfolio each tick.
        for _, _, arm, _ in self._arms:
            arm._shared = portfolio

        # Collect signals from each arm in priority order.
        per_arm_signals: list[tuple[str, _DedicatedCashArm, list[Signal]]] = []
        for name, inst, arm, _ in self._arms:
            sigs = inst.on_tick_batch(ticks, timestamp, arm)
            per_arm_signals.append((name, arm, sigs))

            # Update per-arm cash ledger for the signals THIS arm emitted.
            for s in sigs:
                p = price_by_id.get(s.ea_id, 0)
                if s.action == "BUY" and p > 0:
                    arm._arm_ids.add(s.ea_id)
                    arm._local_cash -= p * s.quantity
                elif s.action == "SELL" and p > 0:
                    arm._local_cash += (p * s.quantity * 95) // 100

        # Merge with priority dedup: first arm to fire BUY on an ea_id
        # this tick wins; later arms have their cash refunded.
        out: list[Signal] = []
        buy_ids_this_tick: set[int] = set()

        for name, arm, sigs in per_arm_signals:
            for s in sigs:
                if s.action == "BUY":
                    if s.ea_id in buy_ids_this_tick:
                        # Conflict — refund and drop.
                        p = price_by_id.get(s.ea_id, 0)
                        if p > 0:
                            arm._local_cash += p * s.quantity
                        continue
                    buy_ids_this_tick.add(s.ea_id)
                out.append(s)

        return out

    def param_grid(self) -> list[dict]:
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        return [
            {"mode": "equal_split", "budget": 1_000_000},
            {"mode": "no_contention", "budget": 1_000_000},
        ]
