"""Ports (in the Ports-and-Adapters sense) between orchestration and the outside world.

The orchestrator, Screenplay and Screen Robots depend only on these protocols. Adapters implement
them: the Android runner (UIAutomator instrumentation over adb), the iOS runner (XCUITest via
`xcodebuild test-without-building`), the HTTP API client, and in-memory fakes for unit tests.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from mtf.core.types import Platform


class Direction(StrEnum):
    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


class WaitState(StrEnum):
    VISIBLE = "visible"
    GONE = "gone"
    ENABLED = "enabled"
    DISABLED = "disabled"
    SELECTED = "selected"
    NOT_SELECTED = "not_selected"


@dataclass(frozen=True)
class ElementInfo:
    """A snapshot of one element, read by logical test ID."""

    id: str
    exists: bool
    visible: bool = False
    text: str = ""
    value: str = ""
    enabled: bool = False
    selected: bool = False
    bounds: tuple[int, int, int, int] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def missing(cls, test_id: str) -> ElementInfo:
        return cls(id=test_id, exists=False)


@dataclass(frozen=True)
class AppLaunchOptions:
    clean_state: bool = False
    deep_link: str | None = None


@runtime_checkable
class UiDriver(Protocol):
    """Black-box UI automation of the installed app, addressed only by registered test IDs."""

    platform: Platform

    # app lifecycle
    def launch_app(self, options: AppLaunchOptions) -> None: ...
    def terminate_app(self) -> None: ...
    def background_app(self, duration_ms: int) -> None: ...
    def open_deep_link(self, url: str) -> None: ...

    # interactions (each waits up to timeout_ms for the element first)
    def tap(self, test_id: str, timeout_ms: int) -> None: ...
    def double_tap(self, test_id: str, timeout_ms: int) -> None: ...
    def long_press(self, test_id: str, duration_ms: int, timeout_ms: int) -> None: ...
    def type_text(self, test_id: str, text: str, *, clear_first: bool, sensitive: bool, timeout_ms: int) -> None: ...
    def clear_text(self, test_id: str, timeout_ms: int) -> None: ...
    def scroll_to(
        self, test_id: str, *, direction: Direction, max_swipes: int, container: str | None, timeout_ms: int
    ) -> None: ...
    def swipe(self, test_id: str | None, direction: Direction, timeout_ms: int) -> None: ...
    def drag_to(self, test_id: str, target_id: str, timeout_ms: int) -> None: ...
    def back(self) -> None: ...
    def dismiss_keyboard(self) -> None: ...
    def dismiss_system_dialogs(self) -> list[str]: ...

    # queries
    def wait_for(self, test_id: str, state: WaitState, timeout_ms: int) -> ElementInfo: ...
    def read(self, test_id: str) -> ElementInfo: ...
    def first_visible(self, test_ids: list[str]) -> str | None: ...

    # evidence
    def screenshot(self, path: Path) -> bool: ...
    def hierarchy(self) -> str: ...
    def device_logs(self) -> str: ...
    def device_info(self) -> dict[str, Any]: ...

    def close(self) -> None: ...


@dataclass
class ApiResponse:
    status: int
    headers: dict[str, str]
    body: Any  # parsed JSON, or the raw text when the body is not JSON
    raw_text: str
    elapsed_ms: float
    request: dict[str, Any]  # method, url, headers, body — unredacted; redacted by the capturer


class HttpTransport(Protocol):
    """The API runner's network port (httpx in production, a fake in unit tests)."""

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None,
        json_body: Any,
        content: str | None,
        timeout_s: float,
    ) -> ApiResponse: ...

    def close(self) -> None: ...
