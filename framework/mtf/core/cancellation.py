"""Cooperative cancellation and deadlines."""

from __future__ import annotations

import threading
import time

from mtf.core.errors import RunCancelled, StepTimeout


class CancellationToken:
    def __init__(self) -> None:
        self._event = threading.Event()
        self.reason = ""

    def cancel(self, reason: str = "cancelled") -> None:
        self.reason = reason
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise RunCancelled(f"run cancelled: {self.reason}")

    def wait(self, seconds: float) -> bool:
        """Sleeps up to `seconds`; returns True if cancelled meanwhile. Used only between polls."""
        return self._event.wait(seconds)


class Deadline:
    """A monotonic deadline. `remaining()` never returns a negative value."""

    def __init__(self, seconds: float, *, what: str = "operation") -> None:
        self.seconds = seconds
        self.what = what
        self._end = time.monotonic() + seconds

    def remaining(self) -> float:
        return max(0.0, self._end - time.monotonic())

    @property
    def expired(self) -> bool:
        return time.monotonic() >= self._end

    def check(self) -> None:
        if self.expired:
            raise StepTimeout(f"{self.what} exceeded its {self.seconds:.1f}s timeout")
