"""Assertions on API responses: status, headers, contract schema and JSON values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from mtf.api_runner import jsonpath
from mtf.contracts.openapi import ApiContract, ApiOperation
from mtf.core.matching import evaluate
from mtf.core.ports import ApiResponse


@dataclass(frozen=True)
class Check:
    kind: str  # status | header | schema | json
    description: str
    passed: bool
    expected: Any = None
    actual: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "description": self.description,
            "passed": self.passed,
            "expected": self.expected,
            "actual": self.actual,
        }


def check_status(response: ApiResponse, expected: int | list[int]) -> Check:
    allowed = expected if isinstance(expected, list) else [expected]
    return Check(
        "status",
        f"status in {allowed}" if len(allowed) > 1 else f"status == {allowed[0]}",
        response.status in allowed,
        expected,
        response.status,
    )


def check_header(response: ApiResponse, name: str, operator: str, expected: Any) -> Check:
    actual = response.headers.get(name.lower())
    result = evaluate(operator, expected, actual) if actual is not None else None
    return Check(
        "header",
        f"header {name} {operator} {expected!r}",
        bool(result and result.passed),
        expected,
        actual,
    )


def check_schema(contract: ApiContract, operation: ApiOperation, response: ApiResponse) -> Check:
    errors = contract.validate_response(operation, response.status, response.body)
    return Check(
        "schema",
        f"response matches the contract for {operation.key} {response.status}",
        not errors,
        "conforms to contracts/openapi/backend.yaml",
        errors or "conforms",
    )


def check_json(response: ApiResponse, path: str, operator: str, expected: Any) -> Check:
    actual = jsonpath.get(response.body, path)
    if actual is jsonpath.MISSING:
        return Check("json", f"{path} {operator} {expected!r}", False, expected, "<missing>")
    result = evaluate(operator, expected, actual)
    return Check("json", f"{path} {operator} {expected!r}", result.passed, expected, actual)
