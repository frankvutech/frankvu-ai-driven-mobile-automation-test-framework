"""Secret redaction for logs, API captures, hierarchy dumps, reports and AI prompts.

Redaction works on two levels:

* **Known values**: every secret the run resolves (passwords, tokens) is registered with the
  redactor and replaced wherever it appears, in any text.
* **Shapes**: bearer tokens, JWTs, provider API keys and JSON fields with sensitive names are
  masked even when their value was never registered.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Iterable, Mapping
from typing import Any

REDACTED = "«redacted»"

SENSITIVE_KEYS = frozenset(
    {
        "password",
        "newpassword",
        "currentpassword",
        "confirmpassword",
        "token",
        "accesstoken",
        "refreshtoken",
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "secret",
        "apikey",
        "api_key",
        "x-api-key",
        "clientsecret",
    }
)
_NORMALISED_SENSITIVE_KEYS = frozenset(k.replace("_", "").replace("-", "") for k in SENSITIVE_KEYS)

_PATTERNS = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*"),
    re.compile(r"\beyJ[A-Za-z0-9_-]{5,}\.eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\b"),  # JWT
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{10,}\b"),  # Anthropic API keys
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),  # GitHub tokens
)

# Short values (for example a 4-digit PIN) would redact unrelated text; they are still masked by key.
_MIN_KNOWN_SECRET_LENGTH = 6


def is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return lowered in SENSITIVE_KEYS or lowered.replace("_", "").replace("-", "") in _NORMALISED_SENSITIVE_KEYS


class Redactor:
    """Thread-safe; one instance per run, shared by every component that writes output."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        self._lock = threading.Lock()
        self._secrets: set[str] = set()
        for value in secrets:
            self.add_secret(value)

    def add_secret(self, value: str | None) -> None:
        if value and len(value) >= _MIN_KNOWN_SECRET_LENGTH:
            with self._lock:
                self._secrets.add(value)

    @property
    def known_secret_count(self) -> int:
        return len(self._secrets)

    def redact_text(self, text: str) -> str:
        if not text:
            return text
        with self._lock:
            secrets = sorted(self._secrets, key=len, reverse=True)
        for secret in secrets:
            text = text.replace(secret, REDACTED)
        for pattern in _PATTERNS:
            text = pattern.sub(REDACTED, text)
        return text

    def redact(self, value: Any) -> Any:
        """Recursively redacts strings, dict values under sensitive keys, lists and tuples."""
        if isinstance(value, str):
            return self.redact_text(value)
        if isinstance(value, Mapping):
            return {
                key: (
                    REDACTED
                    if isinstance(key, str) and is_sensitive_key(key) and v not in (None, "")
                    else self.redact(v)
                )
                for key, v in value.items()
            }
        if isinstance(value, list | tuple):
            return [self.redact(item) for item in value]
        return value

    def redact_headers(self, headers: Mapping[str, str]) -> dict[str, str]:
        return {k: (REDACTED if is_sensitive_key(k) else self.redact_text(v)) for k, v in headers.items()}

    def contains_secret(self, text: str) -> bool:
        with self._lock:
            return any(secret in text for secret in self._secrets)
