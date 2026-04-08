"""Promo dip buy — buy new promo cards after their initial crash.

Every Friday promo release crashes 50-75% over hours/days as supply floods in.
Then they rally as supply dries up and demand returns.

Strategy: when a new card appears, track its low. Once price bounces X% off
that tracked low, buy. Sell on trailing stop from peak.

Works on hourly market_snapshots data where we can see the intraday crash and bounce.
"""
from datetime import datetime, timedelta
from collections import defaultdict
from src.algo.models import Signal, Portfolio
from src.algo.strategies.base import Strategy


class PromoDipBuyStrategy(Strategy):
    """Buy new cards after initial crash, sell on trailing stop."""

    name = "promo_dip_buy"

    def __init__(self, params: dict):
        self.params = params
        # Entry: buy when price is trending up — current price > price N hours ago by this %
        self.trend_pct: float = params.get("trend_pct", 0.05)
        # How many hours back to compare for trend
        self.trend_lookback: int = params.get("trend_lookback", 24)
        # Card age window in days (only buy between min_day and max_day after release)
        self.min_day: int = params.get("min_day", 3)
        self.max_day: int = params.get("max_day", 10)
        # Sell when rolling average trend drops to this level
        self.sell_trend_pct: float = params.get("sell_trend_pct", 0.0)
        # Min drop from first-seen price before we consider buying
        self.min_crash: float = params.get("min_crash", 0.05)
        # Exit: trailing stop from peak since buying
        self.trailing_stop: float = params.get("trailing_stop", 0.10)
        # Don't apply trailing stop until this many hours after buy (let it breathe)
        self.stop_delay_hours: int = params.get("stop_delay_hours", 24)
        # Max hold time in hours before force-sell
        self.max_hold_hours: int = params.get("max_hold_hours", 336)  # 14 days
        # Price filters
        self.min_price: int = params.get("min_price", 11000)
        self.max_price: int = params.get("max_price", 200000)
        # Position sizing: max % of portfolio per card
        self.max_position_pct: float = params.get("max_position_pct", 0.10)
        # Max card age in days to consider (only buy genuinely new cards)
        self.max_card_age_days: int = params.get("max_card_age_days", 7)

        # Internal state
        self._old_cards: set[int] = set()
        self._created_at_map: dict[int, datetime] = {}
        self._promo_ids: set[int] = set()  # ea_ids that are part of a Friday promo batch
        self._first_seen_ts: dict[int, datetime] = {}
        self._first_seen_price: dict[int, int] = {}
        self._tracked_low: dict[int, int] = {}
        self._history: dict[int, list[tuple[datetime, int]]] = defaultdict(list)
        self._bought: set[int] = set()
        self._peak_prices: dict[int, int] = {}
        self._buy_ts: dict[int, datetime] = {}

    def set_existing_ids(self, existing_ids: set[int]):
        self._old_cards = existing_ids

    def set_created_at_map(self, created_at_map: dict):
        self._created_at_map = created_at_map
        # Identify promo batches: 10+ cards created in the same hour on a Friday
        hour_buckets: dict[tuple, list[int]] = defaultdict(list)
        for ea_id, created in created_at_map.items():
            cr = created.replace(tzinfo=None) if created.tzinfo else created
            if cr.weekday() == 4:  # Friday
                bucket = (cr.year, cr.month, cr.day, cr.hour)
                hour_buckets[bucket].append(ea_id)
        self._promo_ids = set()
        for bucket, ids in hour_buckets.items():
            if len(ids) >= 10:
                self._promo_ids.update(ids)

    def _hours_since_release(self, ea_id: int, timestamp: datetime) -> float:
        """Hours since card was first seen or created_at."""
        if ea_id in self._created_at_map:
            created = self._created_at_map[ea_id]
            ts = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp
            cr = created.replace(tzinfo=None) if created.tzinfo else created
            return (ts - cr).total_seconds() / 3600
        if ea_id in self._first_seen_ts:
            ts = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp
            fs = self._first_seen_ts[ea_id]
            fs = fs.replace(tzinfo=None) if fs.tzinfo else fs
            return (ts - fs).total_seconds() / 3600
        return 0

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        signals = []
        potential_buys = []

        for ea_id, price in ticks:
            # Only consider cards with created_at data
            if ea_id not in self._created_at_map:
                continue

            # Track first seen and history
            if ea_id not in self._first_seen_ts:
                self._first_seen_ts[ea_id] = timestamp
                self._first_seen_price[ea_id] = price
                self._tracked_low[ea_id] = price

            self._history[ea_id].append((timestamp, price))

            # Update tracked low
            if price < self._tracked_low[ea_id]:
                self._tracked_low[ea_id] = price

            holding = portfolio.holdings(ea_id)
            hours = self._hours_since_release(ea_id, timestamp)

            if holding > 0:
                # Update peak
                if price > self._peak_prices.get(ea_id, 0):
                    self._peak_prices[ea_id] = price

                peak = self._peak_prices[ea_id]
                drop_from_peak = (peak - price) / peak if peak > 0 else 0

                # How long have we held?
                buy_ts = self._buy_ts.get(ea_id)
                hold_hours = 0
                if buy_ts:
                    ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp
                    bt_clean = buy_ts.replace(tzinfo=None) if buy_ts.tzinfo else buy_ts
                    hold_hours = (ts_clean - bt_clean).total_seconds() / 3600

                sell = False
                # Sell when rolling average trend turns negative (rally is over)
                sell_history = self._history[ea_id]
                sell_lb = self.trend_lookback
                if hold_hours >= self.stop_delay_hours and len(sell_history) >= sell_lb * 2:
                    sell_recent = sorted([p for _, p in sell_history[-sell_lb:]])
                    sell_older = sorted([p for _, p in sell_history[-sell_lb*2:-sell_lb]])
                    sell_recent_med = sell_recent[len(sell_recent) // 2]
                    sell_older_med = sell_older[len(sell_older) // 2]
                    if sell_older_med > 0:
                        sell_trend = (sell_recent_med - sell_older_med) / sell_older_med
                        if sell_trend <= self.sell_trend_pct:
                            sell = True
                # Force sell after max hold
                if hold_hours >= self.max_hold_hours:
                    sell = True

                if sell:
                    signals.append(Signal(action="SELL", ea_id=ea_id, quantity=holding))
                    self._peak_prices.pop(ea_id, None)
                    self._buy_ts.pop(ea_id, None)

            elif ea_id not in self._bought:
                # Only buy cards from a Friday promo batch
                if ea_id not in self._promo_ids:
                    continue
                # Must be recent (within 13 days of release)
                created = self._created_at_map[ea_id]
                cr_clean = created.replace(tzinfo=None) if created.tzinfo else created
                ts_clean = timestamp.replace(tzinfo=None) if timestamp.tzinfo else timestamp
                days_since = (ts_clean - cr_clean).days
                if days_since < 0 or days_since > 13:
                    continue

                # Check entry: is price trending up?
                # Compare median of last N ticks vs median of N ticks before that
                # Median filters out brief spikes from thin supply / manipulation
                history = self._history[ea_id]
                lb = self.trend_lookback
                if len(history) < lb * 2:
                    continue

                recent_prices = sorted([p for _, p in history[-lb:]])
                older_prices = sorted([p for _, p in history[-lb*2:-lb]])
                recent_med = recent_prices[len(recent_prices) // 2]
                older_med = older_prices[len(older_prices) // 2]
                if older_med <= 0:
                    continue
                trend = (recent_med - older_med) / older_med
                if trend < self.trend_pct:
                    continue

                # Price filter
                if not (self.min_price <= price <= self.max_price):
                    continue

                potential_buys.append((ea_id, price))

        # Size buys: split available cash equally, capped by max_position_pct
        if potential_buys:
            sell_revenue = sum(
                next((p * sig.quantity * 95 // 100 for eid, p in ticks if eid == sig.ea_id), 0)
                for sig in signals if sig.action == "SELL"
            )
            available_cash = portfolio.cash + sell_revenue
            per_card = available_cash // len(potential_buys)

            if self.max_position_pct > 0:
                price_map = {eid: p for eid, p in ticks}
                portfolio_value = portfolio.cash + sell_revenue + sum(
                    price_map.get(pos.ea_id, pos.buy_price) * pos.quantity
                    for pos in portfolio.positions
                )
                max_spend = int(portfolio_value * self.max_position_pct)
                per_card = min(per_card, max_spend)

            for ea_id, price in potential_buys:
                quantity = per_card // price if price > 0 else 0
                if quantity > 0:
                    signals.append(Signal(action="BUY", ea_id=ea_id, quantity=quantity))
                    self._peak_prices[ea_id] = price
                    self._buy_ts[ea_id] = timestamp
                    self._bought.add(ea_id)

        return signals

    def param_grid(self) -> list[dict]:
        """Daily data grid (not tuned — use param_grid_hourly for market_snapshots)."""
        return self.param_grid_hourly()

    def param_grid_hourly(self) -> list[dict]:
        """Hourly market_snapshots grid."""
        combos = []
        combos.append({
            "trend_pct": 0.20,
            "trend_lookback": 12,
            "sell_trend_pct": 0.02,
            "min_day": 0,
            "max_day": 999,
            "min_crash": 0.05,
            "trailing_stop": 1.0,
            "stop_delay_hours": 96,
            "max_hold_hours": 336,
            "min_price": 12000,
            "max_price": 61000,
            "max_position_pct": 0.10,
        })
        return combos
