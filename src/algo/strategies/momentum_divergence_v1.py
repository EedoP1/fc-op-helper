"""Momentum divergence v1 — cross-card spillover (pairs-trading simplification).

Hypothesis (iter 55): when one card in a price cohort spikes +5% within 24h,
correlated/neighbor cards follow within 24-72h. Buy the laggards.

This is genuinely orthogonal to single-card signals — it uses information from
OTHER cards in the same cohort to predict a specific card's move, so it should
naturally pass bar 4 (|corr| vs promo_dip_buy).

Per-tick workflow:
  1. Update per-card price history (24h rolling window).
  2. Manage open positions (profit target / stop / max_hold / cooldown).
  3. Bucket cards into price cohorts using *current* price.
  4. For each cohort, find "leaders" — cards with price/median_24h_ago >= +5%.
  5. If >=1 leader in cohort, find "laggards" — cards with same metric < +1%.
  6. Buy top 2 laggards per cohort per tick (fixed dollar sizing ~$60k).
  7. Respect cooldown (48h after prior exit) and max_positions.

Exits: +6% profit target, -4% stop, 48h max hold.
"""
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta

from src.algo.models import Portfolio, Signal
from src.algo.strategies.base import Strategy

logger = logging.getLogger(__name__)


# Price cohorts (min_price_inclusive, max_price_inclusive)
COHORTS: list[tuple[int, int]] = [
    (10_000, 15_000),
    (15_000, 20_000),
    (20_000, 30_000),
    (30_000, 50_000),
    (50_000, 100_000),
    (100_000, 10_000_000),
]


class MomentumDivergenceV1Strategy(Strategy):
    name = "momentum_divergence_v1"

    def __init__(self, params: dict):
        self.params = params
        # Window parameters
        self.history_window_h: int = params.get("history_window_h", 24)
        self.burn_in_h: int = params.get("burn_in_h", 24)
        self.min_age_days: int = params.get("min_age_days", 7)

        # Signal thresholds
        self.leader_threshold: float = params.get("leader_threshold", 0.05)
        self.laggard_threshold: float = params.get("laggard_threshold", 0.01)

        # Exit
        self.profit_target: float = params.get("profit_target", 0.06)
        self.stop_loss: float = params.get("stop_loss", 0.04)
        self.max_hold_h: int = params.get("max_hold_h", 48)
        self.cooldown_h: int = params.get("cooldown_h", 48)

        # Sizing
        self.max_positions: int = params.get("max_positions", 15)
        self.notional_per_slot: int = params.get("notional_per_slot", 60_000)
        self.laggards_per_cohort: int = params.get("laggards_per_cohort", 2)
        self.min_price: int = params.get("min_price", 10_000)

        # Per-card rolling history (timestamp, price) limited to history_window_h
        hist_len = self.history_window_h + 2
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))

        # Holdings bookkeeping
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._cooldown_until: dict[int, datetime] = {}

        self._created_at: dict[int, datetime] = {}
        self._first_ts: datetime | None = None

    def set_created_at_map(self, created_at_map: dict):
        self._created_at = created_at_map

    @staticmethod
    def _median(values: list[int]) -> float:
        if not values:
            return 0.0
        s = sorted(values)
        n = len(s)
        if n % 2 == 1:
            return float(s[n // 2])
        return (s[n // 2 - 1] + s[n // 2]) / 2.0

    def _cohort_for(self, price: int) -> int | None:
        for i, (lo, hi) in enumerate(COHORTS):
            if lo <= price <= hi:
                return i
        return None

    def _change_vs_24h(self, ea_id: int) -> float | None:
        """Return (current - median_24h_ago_sample) / median_24h_ago_sample.

        We use the *oldest* samples in the rolling deque as the "24h ago" baseline,
        taking the median of the earliest 3 samples for robustness. Returns None
        if insufficient history.
        """
        hist = self._history[ea_id]
        if len(hist) < self.history_window_h:
            return None
        lst = list(hist)
        current = lst[-1]
        # Use the oldest 3 samples as the "24h ago" baseline
        baseline_samples = lst[:3]
        baseline = self._median(baseline_samples)
        if baseline <= 0:
            return None
        return (current - baseline) / baseline

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

        if self._first_ts is None:
            self._first_ts = ts_clean

        # --- 1. Update histories and handle exits for all ticks ---
        for ea_id, price in ticks:
            self._history[ea_id].append(price)

            holding = portfolio.holdings(ea_id)
            if holding > 0:
                buy_price = self._buy_prices.get(ea_id, price)
                buy_ts = self._buy_ts.get(ea_id, ts_clean)
                bt_clean = buy_ts.replace(tzinfo=None) if buy_ts.tzinfo else buy_ts
                hold_h = (ts_clean - bt_clean).total_seconds() / 3600.0

                sell = False
                if buy_price > 0:
                    pct = (price - buy_price) / buy_price
                    if pct >= self.profit_target:
                        sell = True
                    elif pct <= -self.stop_loss:
                        sell = True
                if not sell and hold_h >= self.max_hold_h:
                    sell = True

                if sell:
                    signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                    self._buy_prices.pop(ea_id, None)
                    self._buy_ts.pop(ea_id, None)
                    self._cooldown_until[ea_id] = ts_clean + timedelta(hours=self.cooldown_h)

        # --- Burn-in ---
        elapsed_h = (ts_clean - self._first_ts).total_seconds() / 3600.0
        if elapsed_h < self.burn_in_h:
            return signals

        # --- 2. Classify leaders / laggards per cohort ---
        cohort_leaders: dict[int, int] = defaultdict(int)  # cohort -> leader count
        cohort_laggards: dict[int, list[tuple[int, int, float]]] = defaultdict(list)
        # cohort -> [(ea_id, price, change), ...]

        for ea_id, price in ticks:
            if price < self.min_price:
                continue
            cohort = self._cohort_for(price)
            if cohort is None:
                continue

            change = self._change_vs_24h(ea_id)
            if change is None:
                continue

            if change >= self.leader_threshold:
                cohort_leaders[cohort] += 1
            elif change < self.laggard_threshold:
                # laggard candidate — but enforce filters for buy eligibility below
                if portfolio.holdings(ea_id) > 0:
                    continue
                cd_end = self._cooldown_until.get(ea_id)
                if cd_end and ts_clean < cd_end:
                    continue
                created = self._created_at.get(ea_id)
                if created:
                    cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
                    age_days = (ts_clean - cr_clean).days
                    if age_days < self.min_age_days:
                        continue
                cohort_laggards[cohort].append((ea_id, price, change))

        # --- 3. Emit BUY signals for top laggards in cohorts that have >=1 leader ---
        if len(portfolio.positions) >= self.max_positions:
            return signals

        # Estimate available cash incl. pending sell revenue (pessimistic at tick price)
        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                p = next((p for eid, p in ticks if eid == s.ea_id), 0)
                sell_rev += (p * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

        open_slots = self.max_positions - len(portfolio.positions)
        picks: list[tuple[int, int]] = []  # (ea_id, price)

        # Build deterministic order: cohorts ascending; within cohort, laggards by change ascending
        # (most "behind" first — those have the biggest catch-up potential).
        for cohort_idx in sorted(cohort_leaders.keys()):
            candidates = cohort_laggards.get(cohort_idx, [])
            if not candidates:
                continue
            candidates.sort(key=lambda x: x[2])  # most negative change first
            for ea_id, price, _chg in candidates[: self.laggards_per_cohort]:
                picks.append((ea_id, price))

        already_buying: set[int] = set()
        buys_made = 0
        for ea_id, price in picks:
            if buys_made >= open_slots:
                break
            if available <= 0:
                break
            if ea_id in already_buying:
                continue
            if price <= 0:
                continue
            qty = self.notional_per_slot // price
            if qty <= 0:
                continue
            cost = qty * price
            if cost > available:
                qty = available // price
                if qty <= 0:
                    continue
                cost = qty * price
            signals.append(Signal(action="BUY", ea_id=ea_id, quantity=qty))
            self._buy_prices[ea_id] = price
            self._buy_ts[ea_id] = timestamp
            available -= cost
            buys_made += 1
            already_buying.add(ea_id)

        return signals

    def param_grid(self) -> list[dict]:
        return [{
            "history_window_h": 24,
            "burn_in_h": 24,
            "min_age_days": 7,
            "leader_threshold": 0.05,
            "laggard_threshold": 0.01,
            "profit_target": 0.06,
            "stop_loss": 0.04,
            "max_hold_h": 48,
            "cooldown_h": 48,
            "max_positions": 15,
            "notional_per_slot": 60_000,
            "laggards_per_cohort": 2,
            "min_price": 10_000,
        }]
