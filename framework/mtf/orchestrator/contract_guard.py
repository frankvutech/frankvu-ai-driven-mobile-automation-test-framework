"""A `UiDriver` decorator that enforces the test-ID contract at run time.

Plans are validated before execution, but tasks and robots build some IDs dynamically
(``cart_item_<product>``). Every ID that reaches a device passes through this guard:

* unregistered IDs, or IDs not available on the platform, raise a selector-contract violation
  (no fallback to text or coordinates exists anywhere in the framework);
* text typed into a registered *sensitive* field is marked sensitive and registered for redaction;
* touching a *reveals_secret* element suppresses screenshots until the app is relaunched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mtf.contracts.test_ids import TestIdRegistry
from mtf.core.ports import AppLaunchOptions, Direction, ElementInfo, UiDriver, WaitState
from mtf.core.redaction import Redactor


class ContractCheckedDriver:
    def __init__(self, inner: UiDriver, registry: TestIdRegistry, redactor: Redactor) -> None:
        self.inner = inner
        self.registry = registry
        self.redactor = redactor
        self.platform = inner.platform
        self.secret_may_be_visible = False

    def _check(self, test_id: str) -> str:
        resolution = self.registry.require(test_id, self.platform)
        if resolution.reveals_secret:
            self.secret_may_be_visible = True
        return test_id

    # app lifecycle -------------------------------------------------------------------------------
    def launch_app(self, options: AppLaunchOptions) -> None:
        self.secret_may_be_visible = False
        self.inner.launch_app(options)

    def terminate_app(self) -> None:
        self.inner.terminate_app()

    def background_app(self, duration_ms: int) -> None:
        self.inner.background_app(duration_ms)

    def open_deep_link(self, url: str) -> None:
        self.inner.open_deep_link(url)

    # interactions -------------------------------------------------------------------------------
    def tap(self, test_id: str, timeout_ms: int) -> None:
        self.inner.tap(self._check(test_id), timeout_ms)

    def double_tap(self, test_id: str, timeout_ms: int) -> None:
        self.inner.double_tap(self._check(test_id), timeout_ms)

    def long_press(self, test_id: str, duration_ms: int, timeout_ms: int) -> None:
        self.inner.long_press(self._check(test_id), duration_ms, timeout_ms)

    def type_text(self, test_id: str, text: str, *, clear_first: bool, sensitive: bool, timeout_ms: int) -> None:
        resolution = self.registry.require(test_id, self.platform)
        sensitive = sensitive or resolution.sensitive
        if sensitive:
            self.redactor.add_secret(text)
        self.inner.type_text(test_id, text, clear_first=clear_first, sensitive=sensitive, timeout_ms=timeout_ms)

    def clear_text(self, test_id: str, timeout_ms: int) -> None:
        self.inner.clear_text(self._check(test_id), timeout_ms)

    def scroll_to(
        self, test_id: str, *, direction: Direction, max_swipes: int, container: str | None, timeout_ms: int
    ) -> None:
        if container is not None:
            self._check(container)
        self.inner.scroll_to(
            self._check(test_id),
            direction=direction,
            max_swipes=max_swipes,
            container=container,
            timeout_ms=timeout_ms,
        )

    def swipe(self, test_id: str | None, direction: Direction, timeout_ms: int) -> None:
        self.inner.swipe(self._check(test_id) if test_id else None, direction, timeout_ms)

    def drag_to(self, test_id: str, target_id: str, timeout_ms: int) -> None:
        self.inner.drag_to(self._check(test_id), self._check(target_id), timeout_ms)

    def back(self) -> None:
        self.inner.back()

    def dismiss_keyboard(self) -> None:
        self.inner.dismiss_keyboard()

    def dismiss_system_dialogs(self) -> list[str]:
        return self.inner.dismiss_system_dialogs()

    # queries ------------------------------------------------------------------------------------
    def wait_for(self, test_id: str, state: WaitState, timeout_ms: int) -> ElementInfo:
        return self.inner.wait_for(self._check(test_id), state, timeout_ms)

    def read(self, test_id: str) -> ElementInfo:
        return self.inner.read(self._check(test_id))

    def first_visible(self, test_ids: list[str]) -> str | None:
        return self.inner.first_visible([self._check(t) for t in test_ids])

    # evidence -----------------------------------------------------------------------------------
    def screenshot(self, path: Path) -> bool:
        if self.secret_may_be_visible:
            return False
        return self.inner.screenshot(path)

    def hierarchy(self) -> str:
        return self.redactor.redact_text(self.inner.hierarchy())

    def device_logs(self) -> str:
        return self.redactor.redact_text(self.inner.device_logs())

    def device_info(self) -> dict[str, Any]:
        return self.inner.device_info()

    def close(self) -> None:
        self.inner.close()
