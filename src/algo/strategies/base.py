"""Abstract base class for trading strategies."""
from abc import ABC, abstractmethod
from datetime import datetime
from src.algo.models import Signal, Portfolio


class Strategy(ABC):
    """All strategies must implement this interface."""

    name: str

    @abstractmethod
    def __init__(self, params: dict):
        ...

    def set_existing_ids(self, existing_ids: set[int]):
        """Called by the engine with IDs that exist at the start of the data window.

        Strategies can use this to distinguish new cards from pre-existing ones.
        Default implementation does nothing.
        """
        pass

    def on_tick(
        self, ea_id: int, price: int, timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        """Process a single price tick. Return BUY/SELL signals or empty list.

        Called once per player per tick. Default implementation is called by
        on_tick_batch for backwards compatibility.
        """
        return []

    def on_tick_batch(
        self, ticks: list[tuple[int, int]], timestamp: datetime, portfolio: Portfolio,
    ) -> list[Signal]:
        """Process all price ticks for a single timestamp at once.

        Args:
            ticks: [(ea_id, price), ...] all cards for this timestamp.
            timestamp: The current timestamp.
            portfolio: Current portfolio state.

        Default implementation calls on_tick per card for backwards compatibility.
        Override this for strategies that need to see all cards before deciding.
        """
        signals = []
        for ea_id, price in ticks:
            signals.extend(self.on_tick(ea_id, price, timestamp, portfolio))
        return signals

    @abstractmethod
    def param_grid(self) -> list[dict]:
        """Return all parameter combinations to sweep."""
        ...
