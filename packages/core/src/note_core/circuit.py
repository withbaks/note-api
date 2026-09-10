"""Shared circuit breaker for OpenAI calls."""

from __future__ import annotations

import time


class CircuitBreaker:
    def __init__(self, *, failure_threshold: int = 5, open_seconds: float = 30.0) -> None:
        self.failure_threshold = failure_threshold
        self.open_seconds = open_seconds
        self.failures = 0
        self.opened_at: float | None = None

    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if time.monotonic() - self.opened_at >= self.open_seconds:
            self.opened_at = None
            self.failures = 0
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.monotonic()


_circuit = CircuitBreaker()


def circuit_is_open() -> bool:
    return _circuit.is_open()


def record_llm_success() -> None:
    _circuit.record_success()


def record_llm_failure() -> None:
    _circuit.record_failure()
