"""Market regime-aware crash recovery — don't buy during cascading crashes.

Data shows after >5% market crash days, the next day is STILL negative 10/14 times.
Consecutive down days do NOT predict bounces — they predict more down days.
The crash_recovery strategy buys too early during market panics, eating its
edge with drawdown from cascade buying.

The edge: track broad market state across ALL cards seen this tick. Count
what fraction went up vs down. When >60% of cards are falling (crash regime),
refuse to buy even if individual cards look crashed. Wait for regime to flip
to "recovery" (>50% of cards rising) before entering positions.

Tax survival: same 12-18% recovery targets as crash_recovery, but with better
entry timing. Should have higher win rate and lower max drawdown.
"""
from datetime import datetime
from collections import defaultdict
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class RegimeCrashStrategy(Strategy):
    """Crash recovery that waits for market regime to turn positive."""

    name = "regime_crash"

    def __init__(self, params: dict):
        self.params = params
        self.lookback: int = params.get("lookback", 14)
        self.crash_pct: float = params.get("crash_pct", 0.15)
        self.profit_target: float = params.get("profit_target", 0.12)
        self.stop_loss: float = params.get("stop_loss", 0.15)
        self.max_hold_days: int = params.get("max_hold_days", 21)
        self.position_pct: float = params.get("position_pct", 0.02)
        self.regime_threshold: float = params.get("regime_threshold", 0.50)
        self.regime_window: int = params.get("regime_window", 3)
        self._history: dict[int, list[int]] = defaultdict(list)
        self._buy_prices: dict[int, int] = {}
        self._buy_days: dict[int, int] = defaultdict(int)
        # Market regime tracking
        self._current_ts: datetime | None = None
        self._ts_up_count: int = 0
        self._ts_total_count: int = 0
        self._regime_history: list[float] = []  # pct_up per timestamp

    def _update_regime(self, ea_id: int, price: int, timestamp: datetime):
        """Track market-wide direction for regime detection."""
        history = self._history[ea_id]

        # New timestamp = finalize previous regime reading
        if self._current_ts is not None and timestamp != self._current_ts:
            if self._ts_total_count > 0:
                pct_up = self._ts_up_count / self._ts_total_count
                self._regime_history.append(pct_up)
            self._ts_up_count = 0
            self._ts_total_count = 0

        self._current_ts = timestamp

        # Count this card's direction
        if len(history) >= 2:
            self._ts_total_count += 1
            if price > history[-2]:
                self._ts_up_count += 1

    def _is_recovery_regime(self) -> bool:
        """Check if market has shifted from crash to recovery."""
        if len(self._regime_history) < self.regime_window:
            return True  # not enough data, allow trading

        # Check recent N days: average pct_up must be above threshold
        recent = self._regime_history[-self.regime_window:]
        avg_up = sum(recent) / len(recent)
        return avg_up >= self.regime_threshold

    def on_tick(
        self, ea_id: int, price: int, timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        history = self._history[ea_id]
        history.append(price)
        self._update_regime(ea_id, price, timestamp)

        holding = portfolio.holdings(ea_id)
        signals = []

        if holding > 0:
            self._buy_days[ea_id] += 1
            buy_price = self._buy_prices.get(ea_id, price)
            pct_change = (price - buy_price) / buy_price if buy_price > 0 else 0

            if (pct_change >= self.profit_target
                    or pct_change <= -self.stop_loss
                    or self._buy_days[ea_id] >= self.max_hold_days):
                signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                self._buy_prices.pop(ea_id, None)
                self._buy_days.pop(ea_id, None)
        else:
            if len(history) >= self.lookback:
                recent_high = max(history[-self.lookback:])
                if recent_high > 0 and price > 0:
                    drop_pct = (recent_high - price) / recent_high

                    if drop_pct >= self.crash_pct:
                        # REGIME GATE: only buy if market is in recovery mode
                        if not self._is_recovery_regime():
                            return signals

                        buy_budget = int(portfolio.cash * self.position_pct)
                        quantity = buy_budget // price
                        if quantity > 0:
                            signals.append(Signal(
                                action="BUY", ea_id=ea_id, quantity=quantity,
                            ))
                            self._buy_prices[ea_id] = price
                            self._buy_days[ea_id] = 0

        return signals

    def param_grid(self) -> list[dict]:
        combos = []
        for lookback in [7, 14, 21]:
            for crash_pct in [0.10, 0.15, 0.20]:
                for regime_threshold in [0.40, 0.50, 0.55]:
                    for profit_target in [0.08, 0.12, 0.18]:
                        combos.append({
                            "lookback": lookback,
                            "crash_pct": crash_pct,
                            "regime_threshold": regime_threshold,
                            "regime_window": 3,
                            "profit_target": profit_target,
                            "stop_loss": 0.15,
                            "max_hold_days": 21,
                            "position_pct": 0.02,
                        })
        return combos
