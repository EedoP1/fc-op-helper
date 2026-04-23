"""Relative-strength continuation v1 — invert iter 55 momentum_divergence.

Hypothesis (iter 57): iter 55 bought laggards in rising cohorts on the premise
that they would catch up (they didn't — -$982k, 23.2% win). The symmetric claim
is that LEADERS in SINKING cohorts should continue: cards going up while their
cohort drags down carry *unique* demand unhooked from the cohort drag and so
the relative strength signal is a continuation tell.

Cohort buckets by current price (same as momentum_divergence_v1):
  $10-15k, $15-20k, $20-30k, $30-50k, $50-100k, $100k+

Per tick:
  1. Update per-card 24h rolling price history.
  2. Handle exits (+6% target, -5% stop, 72h max hold).
  3. Classify each cohort: compute the MEDIAN per-card 24h change across cards
     in that cohort. If median <= -3%, cohort is "sinking".
  4. Only act on sinking cohorts. Within them, find "leaders" where:
     - card 24h change >= +3% (stronger than +0% — actual strength)
     - current price <= 1.10 * 7-day median (don't chase blow-off tops)
     - age_days >= 7, not in cooldown, not already held
  5. BUY top 2 leaders per sinking cohort per tick (sorted by change descending).

Sizing: $60k per slot, max 15 positions. Cooldown 48h after exit.
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


class RelativeStrengthV1Strategy(Strategy):
    name = "relative_strength_v1"

    def __init__(self, params: dict):
        self.params = params
        # Window parameters
        self.history_window_h: int = params.get("history_window_h", 24)
        self.long_history_h: int = params.get("long_history_h", 24 * 7)  # 7d for top-chase guard
        self.burn_in_h: int = params.get("burn_in_h", 24)
        self.min_age_days: int = params.get("min_age_days", 7)

        # Signal thresholds
        self.leader_threshold: float = params.get("leader_threshold", 0.03)
        self.cohort_sink_threshold: float = params.get("cohort_sink_threshold", -0.03)
        self.top_chase_mult: float = params.get("top_chase_mult", 1.10)

        # Exit
        self.profit_target: float = params.get("profit_target", 0.06)
        self.stop_loss: float = params.get("stop_loss", 0.05)
        self.max_hold_h: int = params.get("max_hold_h", 72)
        self.cooldown_h: int = params.get("cooldown_h", 48)

        # Sizing
        self.max_positions: int = params.get("max_positions", 15)
        self.notional_per_slot: int = params.get("notional_per_slot", 60_000)
        self.leaders_per_cohort: int = params.get("leaders_per_cohort", 2)
        self.min_price: int = params.get("min_price", 10_000)

        # Per-card rolling histories
        # Short: 24h for change-vs-24h detection
        hist_len = self.history_window_h + 2
        self._history: dict[int, deque] = defaultdict(lambda: deque(maxlen=hist_len))
        # Long: 7d for top-chase guard (median)
        long_len = self.long_history_h + 2
        self._long_history: dict[int, deque] = defaultdict(lambda: deque(maxlen=long_len))

        # Holdings bookkeeping
        self._buy_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}
        self._cooldown_until: dict[int, datetime] = {}

        self._created_at: dict[int, datetime] = {}
        self._first_ts: datetime | None = None

    def set_created_at_map(self, created_at_map: dict):
        self._created_at = created_at_map

    @staticmethod
    def _median(values: list[float]) -> float:
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
        """Return (current - baseline) / baseline using oldest-3 median as baseline."""
        hist = self._history[ea_id]
        if len(hist) < self.history_window_h:
            return None
        lst = list(hist)
        current = lst[-1]
        baseline_samples = lst[:3]
        baseline = self._median([float(x) for x in baseline_samples])
        if baseline <= 0:
            return None
        return (current - baseline) / baseline

    def _median_7d(self, ea_id: int) -> float | None:
        hist = self._long_history[ea_id]
        if len(hist) < self.history_window_h:
            return None
        return self._median([float(x) for x in hist])

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals: list[Signal] = []
        ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp

        if self._first_ts is None:
            self._first_ts = ts_clean

        # --- 1. Update histories + handle exits ---
        for ea_id, price in ticks:
            self._history[ea_id].append(price)
            self._long_history[ea_id].append(price)

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

        # --- 2. Compute per-card 24h change + group by cohort ---
        # Also collect all deltas per cohort to compute cohort median.
        cohort_deltas: dict[int, list[float]] = defaultdict(list)
        # Candidate leaders: (cohort, ea_id, price, change)
        cand: list[tuple[int, int, int, float]] = []

        for ea_id, price in ticks:
            if price < self.min_price:
                continue
            cohort = self._cohort_for(price)
            if cohort is None:
                continue
            change = self._change_vs_24h(ea_id)
            if change is None:
                continue
            cohort_deltas[cohort].append(change)
            if change >= self.leader_threshold:
                cand.append((cohort, ea_id, price, change))

        # --- 3. Identify sinking cohorts ---
        sinking_cohorts: set[int] = set()
        for cohort, deltas in cohort_deltas.items():
            if len(deltas) < 3:  # need minimum sample for a meaningful median
                continue
            med = self._median(deltas)
            if med <= self.cohort_sink_threshold:
                sinking_cohorts.add(cohort)

        if not sinking_cohorts:
            return signals

        # --- 4. Filter candidate leaders to sinking cohorts + buy-eligibility ---
        eligible: dict[int, list[tuple[int, int, float]]] = defaultdict(list)
        # cohort -> [(ea_id, price, change), ...]

        for cohort, ea_id, price, change in cand:
            if cohort not in sinking_cohorts:
                continue
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
            # Top-chase guard: don't buy if price > 1.10 * 7d median
            m7 = self._median_7d(ea_id)
            if m7 is not None and m7 > 0:
                if price > self.top_chase_mult * m7:
                    continue
            eligible[cohort].append((ea_id, price, change))

        # --- 5. Emit BUY signals for strongest leaders per sinking cohort ---
        if len(portfolio.positions) >= self.max_positions:
            return signals

        # Pending sell revenue (pessimistic at tick price)
        sell_rev = 0
        for s in signals:
            if s.action == "SELL":
                p = next((p for eid, p in ticks if eid == s.ea_id), 0)
                sell_rev += (p * s.quantity * 95) // 100
        available = portfolio.cash + sell_rev

        open_slots = self.max_positions - len(portfolio.positions)
        picks: list[tuple[int, int]] = []  # (ea_id, price)

        for cohort_idx in sorted(eligible.keys()):
            candidates = eligible[cohort_idx]
            if not candidates:
                continue
            # Strongest leaders first
            candidates.sort(key=lambda x: x[2], reverse=True)
            for ea_id, price, _chg in candidates[: self.leaders_per_cohort]:
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
            "long_history_h": 24 * 7,
            "burn_in_h": 24,
            "min_age_days": 7,
            "leader_threshold": 0.03,
            "cohort_sink_threshold": -0.03,
            "top_chase_mult": 1.10,
            "profit_target": 0.06,
            "stop_loss": 0.05,
            "max_hold_h": 72,
            "cooldown_h": 48,
            "max_positions": 15,
            "notional_per_slot": 60_000,
            "leaders_per_cohort": 2,
            "min_price": 10_000,
        }]
