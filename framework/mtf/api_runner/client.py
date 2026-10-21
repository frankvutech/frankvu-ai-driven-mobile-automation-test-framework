"""HTTP client for the backend API: contract-checked requests, token handling, redacted capture."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from mtf.contracts.openapi import ApiContract, ApiOperation
from mtf.contracts.test_data import SecretProvider, TestDataRegistry
from mtf.core.errors import ApiContractViolation, ConfigurationError, InfrastructureError
from mtf.core.ports import ApiResponse, HttpTransport
from mtf.core.redaction import Redactor

LOGIN_PATH = "/api/v1/auth/login"
# A syntactically valid but unsigned token, for negative tests. Not a secret.
INVALID_TOKEN = "eyJhbGciOiJub25lIn0.eyJzdWIiOiJub2JvZHkifQ.invalid"  # noqa: S105


class HttpxTransport:
    """Production transport. Redirects are not followed: a redirect is reported, never hidden."""

    def __init__(self, *, verify_tls: bool = True) -> None:
        self._client = httpx.Client(follow_redirects=False, verify=verify_tls)

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
    ) -> ApiResponse:
        started = time.monotonic()
        try:
            response = self._client.request(
                method,
                url,
                headers=headers,
                params=params,
                json=json_body if content is None else None,
                content=content.encode("utf-8") if content is not None else None,
                timeout=timeout_s,
            )
        except httpx.TimeoutException as exc:
            raise InfrastructureError(f"{method} {url} timed out after {timeout_s:.1f}s") from exc
        except httpx.HTTPError as exc:
            raise InfrastructureError(f"{method} {url} failed: {exc.__class__.__name__}: {exc}") from exc
        elapsed = (time.monotonic() - started) * 1000
        text = response.text
        try:
            body: Any = response.json() if text else None
        except ValueError:
            body = text
        return ApiResponse(
            status=response.status_code,
            headers={k.lower(): v for k, v in response.headers.items()},
            body=body,
            raw_text=text,
            elapsed_ms=elapsed,
            request={
                "method": method,
                "url": str(response.request.url),
                "headers": dict(headers),
                "body": json_body if content is None else content,
            },
        )

    def close(self) -> None:
        self._client.close()


@dataclass
class ApiExchange:
    """One request/response pair, already redacted, as stored in reports."""

    step: str | None
    purpose: str  # "test" | "setup" (token acquisition, fixtures)
    operation: str
    request: dict[str, Any]
    status: int
    response_headers: dict[str, str]
    response_body: Any
    elapsed_ms: float
    test_support: bool = False
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "purpose": self.purpose,
            "operation": self.operation,
            "test_support": self.test_support,
            "request": self.request,
            "response": {"status": self.status, "headers": self.response_headers, "body": self.response_body},
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


class ApiClient:
    def __init__(
        self,
        base_url: str,
        *,
        contract: ApiContract,
        transport: HttpTransport,
        redactor: Redactor,
        test_data: TestDataRegistry,
        secrets: SecretProvider,
        timeout_s: float = 15.0,
        on_exchange: Callable[[ApiExchange], None] | None = None,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ConfigurationError(f"invalid backend base URL {base_url!r}")
        self.base_url = base_url.rstrip("/")
        self.contract = contract
        self.transport = transport
        self.redactor = redactor
        self.test_data = test_data
        self.secrets = secrets
        self.timeout_s = timeout_s
        self.on_exchange = on_exchange
        self.current_step: str | None = None
        self._tokens: dict[str, str] = {}

    # --- tokens --------------------------------------------------------------------------------
    def token_for(self, account_ref: str) -> str:
        cached = self._tokens.get(account_ref)
        if cached is not None:
            return cached
        account = self.test_data.accounts.get(account_ref)
        if account is None:
            raise ConfigurationError(f"unknown account {account_ref!r}")
        password = self.secrets.resolve(f"accounts.{account_ref}.password")
        self.redactor.add_secret(password)
        response = self.send(
            self.contract.operation("POST", LOGIN_PATH),
            body={"email": account["email"], "password": password},
            purpose="setup",
        )
        if response.status != 200 or not isinstance(response.body, dict) or "token" not in response.body:
            raise InfrastructureError(
                f"could not obtain a token for account {account_ref!r}: HTTP {response.status}",
                details={"body": self.redactor.redact(response.body)},
            )
        token = str(response.body["token"])
        self.redactor.add_secret(token)
        self._tokens[account_ref] = token
        return token

    def forget_tokens(self) -> None:
        """Called after a data reset: the seed is restored, so cached sessions are discarded."""
        self._tokens.clear()

    # --- requests ------------------------------------------------------------------------------
    def send(
        self,
        operation: ApiOperation,
        *,
        path_params: dict[str, Any] | None = None,
        query: dict[str, Any] | None = None,
        body: Any = None,
        raw_body: str | None = None,
        account_ref: str | None = None,
        auth_mode: str = "valid",
        purpose: str = "test",
    ) -> ApiResponse:
        url = self.base_url + operation.render_path(path_params or {})
        headers = {"Accept": "application/json"}
        if body is not None or raw_body is not None:
            headers["Content-Type"] = "application/json"
        if auth_mode == "invalid":
            headers["Authorization"] = f"Bearer {INVALID_TOKEN}"
        elif auth_mode == "valid" and account_ref is not None:
            headers["Authorization"] = f"Bearer {self.token_for(account_ref)}"
        elif auth_mode not in ("valid", "none", "invalid"):
            raise ApiContractViolation(f"unknown token mode {auth_mode!r}")
        response = self.transport.request(
            operation.method,
            url,
            headers=headers,
            params={k: v for k, v in (query or {}).items() if v is not None} or None,
            json_body=body,
            content=raw_body,
            timeout_s=self.timeout_s,
        )
        self._record(operation, response, purpose)
        return response

    def _record(self, operation: ApiOperation, response: ApiResponse, purpose: str) -> None:
        if self.on_exchange is None:
            return
        request = dict(response.request)
        request["headers"] = self.redactor.redact_headers(request.get("headers", {}))
        request["body"] = self.redactor.redact(_maybe_json(request.get("body")))
        self.on_exchange(
            ApiExchange(
                step=self.current_step,
                purpose=purpose,
                operation=operation.key,
                request=self.redactor.redact(request),
                status=response.status,
                response_headers=self.redactor.redact_headers(response.headers),
                response_body=self.redactor.redact(response.body),
                elapsed_ms=response.elapsed_ms,
                test_support=operation.is_test_support,
            )
        )

    def close(self) -> None:
        self.transport.close()


def _maybe_json(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value
