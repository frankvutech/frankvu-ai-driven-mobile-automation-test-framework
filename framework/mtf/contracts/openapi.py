"""The backend API contract (`contracts/openapi/backend.yaml`).

API steps may only call operations declared here. Responses can be validated against the schema
the contract declares for their status code.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft4Validator

from mtf.core.errors import ApiContractViolation, ConfigurationError

HTTP_METHODS = ("get", "post", "put", "patch", "delete")
TEST_SUPPORT_TAG = "Test support"
_PARAM_RE = re.compile(r"\{([A-Za-z0-9_]+)\}")


@dataclass(frozen=True)
class ApiOperation:
    method: str
    path: str
    summary: str
    tags: tuple[str, ...]
    requires_auth: bool
    statuses: tuple[str, ...]
    path_params: tuple[str, ...]
    query_params: tuple[str, ...]
    has_body: bool

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"

    @property
    def is_test_support(self) -> bool:
        """Setup/teardown endpoints (reset, deterministic errors) as opposed to product APIs."""
        return TEST_SUPPORT_TAG in self.tags

    def render_path(self, params: Mapping[str, Any]) -> str:
        missing = [p for p in self.path_params if p not in params]
        if missing:
            raise ApiContractViolation(f"{self.key}: missing path parameter(s) {', '.join(missing)}")
        return _PARAM_RE.sub(lambda m: str(params[m.group(1)]), self.path)


class ApiContract:
    def __init__(self, spec: Mapping[str, Any]) -> None:
        if not str(spec.get("openapi", "")).startswith("3."):
            raise ConfigurationError("the API contract must be an OpenAPI 3 document")
        self.spec = spec
        self.title = str(spec.get("info", {}).get("title", ""))
        self.version = str(spec.get("info", {}).get("version", ""))
        self.operations: dict[str, ApiOperation] = {}
        for path, item in (spec.get("paths") or {}).items():
            shared_params = item.get("parameters", [])
            for method in HTTP_METHODS:
                op = item.get(method)
                if op is None:
                    continue
                params = [self._deref(p) for p in [*shared_params, *op.get("parameters", [])]]
                operation = ApiOperation(
                    method=method.upper(),
                    path=path,
                    summary=str(op.get("summary", "")),
                    tags=tuple(op.get("tags", ())),
                    requires_auth=bool(op.get("security")),
                    statuses=tuple(str(s) for s in op.get("responses", {})),
                    path_params=tuple(p["name"] for p in params if p.get("in") == "path"),
                    query_params=tuple(p["name"] for p in params if p.get("in") == "query"),
                    has_body="requestBody" in op,
                )
                self.operations[operation.key] = operation

    @classmethod
    def load(cls, path: Path) -> ApiContract:
        try:
            return cls(yaml.safe_load(path.read_text(encoding="utf-8")))
        except OSError as exc:
            raise ConfigurationError(f"cannot read API contract {path}: {exc}") from exc

    # --- lookup --------------------------------------------------------------------------------
    def operation(self, method: str, path: str) -> ApiOperation:
        key = f"{method.upper()} {path}"
        op = self.operations.get(key)
        if op is None:
            raise ApiContractViolation(
                f"{key} is not an operation in the API contract (contracts/openapi/backend.yaml)",
                details={"operation": key},
            )
        return op

    def find(self, method: str, path: str) -> ApiOperation | None:
        return self.operations.get(f"{method.upper()} {path}")

    # --- schemas -------------------------------------------------------------------------------
    def _deref(self, node: Any) -> Any:
        if isinstance(node, Mapping) and "$ref" in node:
            ref = str(node["$ref"])
            if not ref.startswith("#/"):
                raise ConfigurationError(f"external $ref not supported: {ref}")
            target: Any = self.spec
            for part in ref[2:].split("/"):
                target = target[part]
            return self._deref(target)
        return node

    def _inline(self, node: Any) -> Any:
        """Returns a copy of a schema with every local $ref inlined (the contract has no cycles)."""
        node = self._deref(node)
        if isinstance(node, Mapping):
            return {k: self._inline(v) for k, v in node.items() if k not in ("example", "examples")}
        if isinstance(node, list):
            return [self._inline(v) for v in node]
        return copy.deepcopy(node)

    def response_schema(self, op: ApiOperation, status: int) -> dict[str, Any] | None:
        raw = self.spec["paths"][op.path][op.method.lower()]["responses"]
        response = raw.get(str(status)) or raw.get(f"{str(status)[0]}XX") or raw.get("default")
        if response is None:
            return None
        response = self._deref(response)
        schema = response.get("content", {}).get("application/json", {}).get("schema")
        return self._inline(schema) if schema is not None else None

    def validate_response(self, op: ApiOperation, status: int, body: Any) -> list[str]:
        """Contract errors for a response: undeclared status or a body that violates its schema."""
        if str(status) not in op.statuses:
            return [f"status {status} is not declared for {op.key} (declared: {', '.join(op.statuses)})"]
        schema = self.response_schema(op, status)
        if schema is None:
            return []
        validator = Draft4Validator(schema)
        return [
            f"{'/'.join(str(p) for p in error.absolute_path) or '<body>'}: {error.message}"
            for error in sorted(validator.iter_errors(body), key=lambda e: list(e.absolute_path))
        ]

    def describe_for_planner(self) -> list[dict[str, Any]]:
        return [
            {
                "operation": op.key,
                "summary": op.summary,
                "auth": "bearer" if op.requires_auth else "none",
                "statuses": list(op.statuses),
                "path_params": list(op.path_params),
                "query_params": list(op.query_params),
                "test_support": op.is_test_support,
            }
            for op in self.operations.values()
        ]
