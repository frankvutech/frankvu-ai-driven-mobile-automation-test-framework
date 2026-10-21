"""Client for the device runners' newline-delimited JSON protocol (one request per connection).

Both device runners (Android instrumentation, iOS XCUITest bundle) speak the same protocol, so the
host adapters share this client. Errors are mapped onto the framework's failure taxonomy.
"""

from __future__ import annotations

import json
import secrets
import socket
import time
from typing import Any

from mtf.core.errors import ElementNotFound, InfrastructureError, MtfError, RunnerError, StepTimeout

_ERRORS: dict[str, type[MtfError]] = {
    "element_not_found": ElementNotFound,
    "timeout": StepTimeout,
}

MAX_RESPONSE_BYTES = 32 * 1024 * 1024


def new_token() -> str:
    return secrets.token_hex(16)


class RunnerClient:
    def __init__(self, host: str, port: int, token: str, *, connect_timeout_s: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.token = token
        self.connect_timeout_s = connect_timeout_s
        self._counter = 0

    def call(self, command: str, args: dict[str, Any] | None = None, *, timeout_s: float = 30.0) -> dict[str, Any]:
        self._counter += 1
        request = {"id": str(self._counter), "token": self.token, "command": command, "args": args or {}}
        payload = (json.dumps(request) + "\n").encode("utf-8")
        try:
            with socket.create_connection((self.host, self.port), timeout=self.connect_timeout_s) as conn:
                conn.settimeout(timeout_s)
                conn.sendall(payload)
                chunks: list[bytes] = []
                size = 0
                while True:
                    chunk = conn.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > MAX_RESPONSE_BYTES:
                        raise RunnerError(f"runner response for {command} exceeds {MAX_RESPONSE_BYTES} bytes")
                    if chunk.endswith(b"\n"):
                        break
        except TimeoutError as exc:
            raise StepTimeout(f"runner did not answer {command} within {timeout_s:.1f}s") from exc
        except OSError as exc:
            raise InfrastructureError(f"runner unreachable at {self.host}:{self.port} ({command}): {exc}") from exc
        raw = b"".join(chunks).decode("utf-8").strip()
        if not raw:
            raise RunnerError(f"runner closed the connection without answering {command}")
        try:
            response = json.loads(raw)
        except ValueError as exc:
            raise RunnerError(f"runner sent invalid JSON for {command}: {raw[:200]}") from exc
        if response.get("ok"):
            result = response.get("result") or {}
            return result if isinstance(result, dict) else {"value": result}
        error = response.get("error") or {}
        category = str(error.get("category", "runner_error"))
        message = str(error.get("message", "unknown runner error"))
        raise _ERRORS.get(category, RunnerError)(message, details={"runner_category": category, "command": command})

    def wait_until_ready(self, timeout_s: float, *, probe: str = "hello", is_alive: Any = None) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        last: Exception | None = None
        while time.monotonic() < deadline:
            if is_alive is not None and not is_alive():
                raise InfrastructureError(f"the runner process exited before it was ready ({last})")
            try:
                return self.call(probe, timeout_s=10)
            except (InfrastructureError, RunnerError, StepTimeout) as exc:
                last = exc
                time.sleep(0.5)
        raise InfrastructureError(f"runner not ready after {timeout_s:.0f}s: {last}")
