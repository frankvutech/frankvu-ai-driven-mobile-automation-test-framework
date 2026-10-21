"""Exception hierarchy. Every error carries the failure category the report will show."""

from __future__ import annotations

from typing import Any

from mtf.core.types import FailureCategory


class MtfError(Exception):
    category: FailureCategory = FailureCategory.RUNNER_ERROR

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ConfigurationError(MtfError):
    category = FailureCategory.CONFIGURATION_ERROR


class PolicyViolation(MtfError):
    category = FailureCategory.POLICY_VIOLATION


class PlanLoadError(MtfError):
    category = FailureCategory.PLAN_VALIDATION_ERROR


class SelectorContractViolation(MtfError):
    """A logical ID that is not in the shared registry (or not available on this platform)."""

    category = FailureCategory.SELECTOR_CONTRACT_VIOLATION


class ElementNotFound(MtfError):
    """A registered ID that did not appear on screen within the timeout."""

    category = FailureCategory.ELEMENT_NOT_FOUND


class StepTimeout(MtfError):
    category = FailureCategory.TIMEOUT


class AssertionFailed(MtfError):
    category = FailureCategory.ASSERTION_FAILED

    def __init__(self, message: str, *, expected: Any = None, actual: Any = None) -> None:
        super().__init__(message, details={"expected": expected, "actual": actual})
        self.expected = expected
        self.actual = actual


class ApiAssertionFailed(AssertionFailed):
    category = FailureCategory.API_ASSERTION_FAILED


class ApiContractViolation(MtfError):
    category = FailureCategory.API_CONTRACT_VIOLATION


class AppCrashed(MtfError):
    category = FailureCategory.APP_CRASH


class ArtifactMismatch(MtfError):
    category = FailureCategory.ARTIFACT_MISMATCH


class RunnerError(MtfError):
    category = FailureCategory.RUNNER_ERROR


class InfrastructureError(MtfError):
    category = FailureCategory.INFRASTRUCTURE_ERROR


class RunCancelled(MtfError):
    category = FailureCategory.CANCELLED


class AIUnavailable(MtfError):
    """No AI provider is configured or reachable. Saved plans remain runnable."""

    category = FailureCategory.CONFIGURATION_ERROR
