"""Shared part of the Android and iOS `UiDriver` adapters: UI commands over the runner protocol.

Platform subclasses add app lifecycle (adb / simctl), screenshots and logs.
"""

from __future__ import annotations

from typing import Any

from mtf.core.ports import Direction, ElementInfo, WaitState
from mtf.core.rpc import RunnerClient
from mtf.core.types import Platform

# Extra seconds the host waits beyond a command's own timeout (UI settling, transport).
TRANSPORT_MARGIN_S = 20.0


def _info(test_id: str, data: dict[str, Any]) -> ElementInfo:
    bounds = data.get("bounds")
    return ElementInfo(
        id=test_id,
        exists=bool(data.get("exists")),
        visible=bool(data.get("visible")),
        text=str(data.get("text") or ""),
        value=str(data.get("value") or ""),
        enabled=bool(data.get("enabled")),
        selected=bool(data.get("selected")),
        bounds=tuple(bounds) if isinstance(bounds, list) and len(bounds) == 4 else None,
        extra={
            k: v
            for k, v in data.items()
            if k not in {"id", "exists", "visible", "text", "value", "enabled", "selected", "bounds"}
        },
    )


class RemoteUiCommands:
    platform: Platform

    def __init__(self, client: RunnerClient) -> None:
        self.client = client

    def _call(self, command: str, args: dict[str, Any], timeout_ms: int = 10_000) -> dict[str, Any]:
        return self.client.call(command, args, timeout_s=timeout_ms / 1000 + TRANSPORT_MARGIN_S)

    def tap(self, test_id: str, timeout_ms: int) -> None:
        self._call("tap", {"id": test_id, "timeout_ms": timeout_ms}, timeout_ms)

    def double_tap(self, test_id: str, timeout_ms: int) -> None:
        self._call("double_tap", {"id": test_id, "timeout_ms": timeout_ms}, timeout_ms)

    def long_press(self, test_id: str, duration_ms: int, timeout_ms: int) -> None:
        self._call(
            "long_press",
            {"id": test_id, "duration_ms": duration_ms, "timeout_ms": timeout_ms},
            timeout_ms + duration_ms,
        )

    def type_text(self, test_id: str, text: str, *, clear_first: bool, sensitive: bool, timeout_ms: int) -> None:
        self._call(
            "type_text",
            {
                "id": test_id,
                "text": text,
                "clear_first": clear_first,
                "sensitive": sensitive,
                "timeout_ms": timeout_ms,
            },
            timeout_ms + 10_000,
        )

    def clear_text(self, test_id: str, timeout_ms: int) -> None:
        self._call("clear_text", {"id": test_id, "timeout_ms": timeout_ms}, timeout_ms)

    def scroll_to(
        self, test_id: str, *, direction: Direction, max_swipes: int, container: str | None, timeout_ms: int
    ) -> None:
        self._call(
            "scroll_to",
            {
                "id": test_id,
                "direction": direction.value,
                "max_swipes": max_swipes,
                "container": container,
                "timeout_ms": timeout_ms,
            },
            timeout_ms + max_swipes * 3000,
        )

    def swipe(self, test_id: str | None, direction: Direction, timeout_ms: int) -> None:
        self._call("swipe", {"id": test_id, "direction": direction.value, "timeout_ms": timeout_ms}, timeout_ms)

    def drag_to(self, test_id: str, target_id: str, timeout_ms: int) -> None:
        self._call("drag_to", {"id": test_id, "to": target_id, "timeout_ms": timeout_ms}, timeout_ms * 2)

    def back(self) -> None:
        self._call("back", {})

    def dismiss_keyboard(self) -> None:
        self._call("dismiss_keyboard", {})

    def dismiss_system_dialogs(self) -> list[str]:
        return list(self._call("dismiss_system_dialogs", {}).get("dismissed", []))

    def wait_for(self, test_id: str, state: WaitState, timeout_ms: int) -> ElementInfo:
        return _info(
            test_id,
            self._call("wait_for", {"id": test_id, "state": state.value, "timeout_ms": timeout_ms}, timeout_ms),
        )

    def read(self, test_id: str) -> ElementInfo:
        return _info(test_id, self._call("read", {"id": test_id}))

    def first_visible(self, test_ids: list[str]) -> str | None:
        found = self._call("first_visible", {"ids": test_ids}, 30_000).get("id")
        return str(found) if found else None

    def hierarchy(self) -> str:
        data = self._call("hierarchy", {}, 30_000)
        return str(data.get("xml") or data.get("text") or "")
