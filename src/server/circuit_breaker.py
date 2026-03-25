"""Circuit breaker state machine for fut.gg API resilience."""
import time
import logging
from enum import Enum
from src.config import CB_FAILURE_THRESHOLD, CB_RECOVERY_TIMEOUT, CB_SUCCESS_THRESHOLD

logger = logging.getLogger(__name__)


class CBState(Enum):
    """Enumeration of circuit breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Circuit breaker state machine with CLOSED -> OPEN -> HALF_OPEN -> CLOSED transitions.

    Args:
        failure_threshold: Number of consecutive failures before opening the circuit.
        success_threshold: Number of consecutive successes in HALF_OPEN before closing.
        recovery_timeout: Seconds to wait in OPEN state before transitioning to HALF_OPEN.
    """

    def __init__(
        self,
        failure_threshold: int = CB_FAILURE_THRESHOLD,
        success_threshold: int = CB_SUCCESS_THRESHOLD,
        recovery_timeout: float = CB_RECOVERY_TIMEOUT,
    ):
        self.failure_threshold = failure_threshold
        self.success_threshold = success_threshold
        self.recovery_timeout = recovery_timeout
        self.state = CBState.CLOSED
        self._failures = 0
        self._successes = 0
        self._opened_at: float | None = None

    def record_success(self) -> None:
        """Record a successful API call.

        In HALF_OPEN: increments success counter; closes circuit when threshold reached.
        In CLOSED: resets failure counter to 0.
        """
        if self.state == CBState.HALF_OPEN:
            self._successes += 1
            if self._successes >= self.success_threshold:
                logger.info("Circuit breaker CLOSED after successful probes")
                self.state = CBState.CLOSED
                self._failures = 0
                self._successes = 0
                self._opened_at = None
        elif self.state == CBState.CLOSED:
            self._failures = 0

    def record_failure(self) -> None:
        """Record a failed API call.

        In HALF_OPEN: immediately transitions back to OPEN.
        In CLOSED: increments failure counter; opens circuit when threshold reached.
        """
        if self.state == CBState.HALF_OPEN:
            logger.warning("Circuit breaker re-OPENED after probe failure")
            self.state = CBState.OPEN
            self._opened_at = time.monotonic()
            self._successes = 0
        elif self.state == CBState.CLOSED:
            self._failures += 1
            if self._failures >= self.failure_threshold:
                logger.warning(f"Circuit breaker OPEN after {self._failures} failures")
                self.state = CBState.OPEN
                self._opened_at = time.monotonic()

    @property
    def is_open(self) -> bool:
        """Check whether the circuit breaker is currently open (blocking calls).

        If OPEN and recovery_timeout has elapsed, transitions to HALF_OPEN and returns False.

        Returns:
            True if circuit is OPEN and blocking calls, False otherwise.
        """
        if self.state == CBState.OPEN:
            if self._opened_at is not None and time.monotonic() - self._opened_at >= self.recovery_timeout:
                logger.info("Circuit breaker transitioning to HALF_OPEN")
                self.state = CBState.HALF_OPEN
                self._successes = 0
                return False
            return True
        return False
