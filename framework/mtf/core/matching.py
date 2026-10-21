"""Evaluates DSL matchers. Shared by UI questions, element assertions and API JSON assertions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol


class MatcherLike(Protocol):
    @property
    def operator(self) -> str: ...

    @property
    def operand(self) -> Any: ...


@dataclass(frozen=True)
class MatchResult:
    passed: bool
    operator: str
    expected: Any
    actual: Any

    def describe(self) -> str:
        verdict = "ok" if self.passed else "FAILED"
        return f"{self.operator} {self.expected!r}: actual {self.actual!r} ({verdict})"


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _equal(actual: Any, expected: Any) -> bool:
    """Strict equality: no coercion between text and numbers (quote expected UI text in plans)."""
    if isinstance(actual, bool) or isinstance(expected, bool):
        return actual is expected
    if isinstance(actual, int | float) and isinstance(expected, int | float):
        return float(actual) == float(expected)
    return bool(actual == expected)


def evaluate(operator: str, expected: Any, actual: Any) -> MatchResult:
    match operator:
        case "equals":
            passed = _equal(actual, expected)
        case "not_equals":
            passed = not _equal(actual, expected)
        case "contains":
            passed = isinstance(actual, str | list) and expected in actual
        case "not_contains":
            passed = isinstance(actual, str | list) and expected not in actual
        case "matches":
            passed = isinstance(actual, str) and re.fullmatch(str(expected), actual) is not None
        case "one_of":
            passed = any(_equal(actual, option) for option in expected)
        case "greater_than":
            number = _number(actual)
            passed = number is not None and number > float(expected)
        case "less_than":
            number = _number(actual)
            passed = number is not None and number < float(expected)
        case _:
            raise ValueError(f"unknown matcher operator {operator!r}")
    return MatchResult(passed=passed, operator=operator, expected=expected, actual=actual)


def evaluate_matcher(matcher: MatcherLike, actual: Any, expected: Any | None = None) -> MatchResult:
    """`expected` overrides the matcher operand (after ${...} references were resolved)."""
    return evaluate(matcher.operator, matcher.operand if expected is None else expected, actual)
