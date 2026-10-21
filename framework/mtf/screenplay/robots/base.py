"""Base class for Screen Robots."""

from __future__ import annotations

import re
import time

from mtf.core.errors import AssertionFailed, StepTimeout
from mtf.core.ports import Direction, ElementInfo, UiDriver, WaitState


class Robot:
    """Thin, intention-revealing wrapper over the driver for one screen."""

    def __init__(self, driver: UiDriver, timeout_ms: int) -> None:
        self.driver = driver
        self.timeout_ms = timeout_ms

    # primitives used by subclasses -------------------------------------------------------------
    def tap(self, test_id: str) -> None:
        self.driver.tap(test_id, self.timeout_ms)

    def type(self, test_id: str, text: str, *, sensitive: bool = False) -> None:
        self.driver.type_text(test_id, text, clear_first=True, sensitive=sensitive, timeout_ms=self.timeout_ms)

    def wait_visible(self, test_id: str, timeout_ms: int | None = None) -> ElementInfo:
        return self.driver.wait_for(test_id, WaitState.VISIBLE, timeout_ms or self.timeout_ms)

    def wait_gone(self, test_id: str, timeout_ms: int | None = None) -> ElementInfo:
        return self.driver.wait_for(test_id, WaitState.GONE, timeout_ms or self.timeout_ms)

    def text_of(self, test_id: str) -> str:
        info = self.wait_visible(test_id)
        return info.text or info.value

    def is_visible(self, test_id: str) -> bool:
        return self.driver.read(test_id).visible

    def scroll_into_view(self, test_id: str, container: str | None = None, max_swipes: int = 15) -> None:
        self.driver.scroll_to(
            test_id,
            direction=Direction.DOWN,
            max_swipes=max_swipes,
            container=container,
            timeout_ms=self.timeout_ms,
        )

    def wait_for_any(self, test_ids: list[str], timeout_ms: int | None = None) -> str:
        """Waits until one of several mutually exclusive states is visible and returns its ID."""
        deadline = time.monotonic() + (timeout_ms or self.timeout_ms) / 1000
        while True:
            found = self.driver.first_visible(test_ids)
            if found is not None:
                return found
            if time.monotonic() >= deadline:
                raise StepTimeout(
                    f"none of {', '.join(test_ids)} became visible within {timeout_ms or self.timeout_ms} ms",
                    details={"expected_any_of": test_ids},
                )
            time.sleep(0.25)


def parse_int(text: str) -> int:
    """Extracts the integer from labels such as "× 2" or "Count: 30 of 30" (first number)."""
    match = re.search(r"-?\d+", text)
    if match is None:
        raise AssertionFailed(f"no number in {text!r}", expected="a number", actual=text)
    return int(match.group(0))
