"""Enumerations shared by the DSL, the orchestrator, the runners and the reports."""

from __future__ import annotations

from enum import StrEnum


class Platform(StrEnum):
    ANDROID = "android"
    IOS = "ios"
    API = "api"

    @property
    def is_mobile(self) -> bool:
        return self in (Platform.ANDROID, Platform.IOS)


MOBILE_PLATFORMS = (Platform.ANDROID, Platform.IOS)


class StepStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"  # an assertion about the app or API did not hold
    ERROR = "error"  # the step could not be executed (infrastructure, runner, configuration)
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


class RunStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    ERROR = "error"
    CANCELLED = "cancelled"
    REJECTED = "rejected"  # the plan or configuration failed validation; nothing was launched


class FailureCategory(StrEnum):
    """Why a step or run did not pass. Reports group failures by these values."""

    ASSERTION_FAILED = "assertion_failed"
    ELEMENT_NOT_FOUND = "element_not_found"
    SELECTOR_CONTRACT_VIOLATION = "selector_contract_violation"
    TIMEOUT = "timeout"
    API_ASSERTION_FAILED = "api_assertion_failed"
    API_CONTRACT_VIOLATION = "api_contract_violation"
    APP_CRASH = "app_crash"
    ARTIFACT_MISMATCH = "artifact_mismatch"
    RUNNER_ERROR = "runner_error"
    INFRASTRUCTURE_ERROR = "infrastructure_error"
    CONFIGURATION_ERROR = "configuration_error"
    PLAN_VALIDATION_ERROR = "plan_validation_error"
    POLICY_VIOLATION = "policy_violation"
    CANCELLED = "cancelled"

    @property
    def is_test_failure(self) -> bool:
        """True when the product (app or API) behaved wrongly, as opposed to the test setup."""
        return self in (
            FailureCategory.ASSERTION_FAILED,
            FailureCategory.ELEMENT_NOT_FOUND,
            FailureCategory.TIMEOUT,
            FailureCategory.API_ASSERTION_FAILED,
            FailureCategory.API_CONTRACT_VIOLATION,
            FailureCategory.APP_CRASH,
        )


class PlanOrigin(StrEnum):
    MANUAL = "manual"
    AI_GENERATED = "ai_generated"
