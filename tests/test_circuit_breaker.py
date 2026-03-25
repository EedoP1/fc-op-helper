"""Tests for the circuit breaker state machine: CLOSED/OPEN/HALF_OPEN transitions."""
import time
import pytest
from unittest.mock import patch
from src.server.circuit_breaker import CircuitBreaker, CBState


def make_cb(**kwargs) -> CircuitBreaker:
    """Create a CircuitBreaker with test-friendly defaults."""
    defaults = {
        "failure_threshold": 5,
        "success_threshold": 2,
        "recovery_timeout": 60.0,
    }
    defaults.update(kwargs)
    return CircuitBreaker(**defaults)


def test_new_circuit_breaker_starts_closed():
    """Test 1: New CircuitBreaker starts in CLOSED state."""
    cb = make_cb()
    assert cb.state == CBState.CLOSED


def test_failure_threshold_transitions_to_open():
    """Test 2: Recording CB_FAILURE_THRESHOLD consecutive failures transitions to OPEN."""
    cb = make_cb(failure_threshold=5)
    for _ in range(4):
        cb.record_failure()
        assert cb.state == CBState.CLOSED
    cb.record_failure()
    assert cb.state == CBState.OPEN


def test_is_open_returns_true_when_open_and_timeout_not_elapsed():
    """Test 3: is_open returns True when state is OPEN and recovery_timeout has not elapsed."""
    cb = make_cb(failure_threshold=1, recovery_timeout=9999.0)
    cb.record_failure()
    assert cb.state == CBState.OPEN
    assert cb.is_open is True


def test_is_open_transitions_to_half_open_after_timeout():
    """Test 4: is_open returns False (transitions to HALF_OPEN) when recovery_timeout has elapsed."""
    cb = make_cb(failure_threshold=1, recovery_timeout=0.01)
    cb.record_failure()
    assert cb.state == CBState.OPEN
    time.sleep(0.05)
    # is_open should transition to HALF_OPEN and return False
    assert cb.is_open is False
    assert cb.state == CBState.HALF_OPEN


def test_half_open_success_threshold_transitions_to_closed():
    """Test 5: In HALF_OPEN, recording CB_SUCCESS_THRESHOLD successes transitions to CLOSED."""
    cb = make_cb(failure_threshold=1, success_threshold=2, recovery_timeout=0.01)
    cb.record_failure()
    time.sleep(0.05)
    # Trigger HALF_OPEN transition
    _ = cb.is_open
    assert cb.state == CBState.HALF_OPEN
    cb.record_success()
    assert cb.state == CBState.HALF_OPEN
    cb.record_success()
    assert cb.state == CBState.CLOSED


def test_half_open_failure_transitions_back_to_open():
    """Test 6: In HALF_OPEN, recording a single failure transitions back to OPEN."""
    cb = make_cb(failure_threshold=1, recovery_timeout=0.01)
    cb.record_failure()
    time.sleep(0.05)
    # Trigger HALF_OPEN
    _ = cb.is_open
    assert cb.state == CBState.HALF_OPEN
    cb.record_failure()
    assert cb.state == CBState.OPEN


def test_closed_success_resets_failure_counter():
    """Test 7: In CLOSED, recording a success resets the failure counter to 0."""
    cb = make_cb(failure_threshold=5)
    for _ in range(3):
        cb.record_failure()
    assert cb._failures == 3
    cb.record_success()
    assert cb._failures == 0
    assert cb.state == CBState.CLOSED


def test_record_success_and_failure_return_none():
    """Test 8: record_success and record_failure have no return value (void)."""
    cb = make_cb()
    result_s = cb.record_success()
    result_f = cb.record_failure()
    assert result_s is None
    assert result_f is None
