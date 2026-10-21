"""The machine-readable run result (`result.json`). The HTML report is rendered from it."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from mtf.core.types import FailureCategory, Platform, RunStatus, StepStatus

RESULT_SCHEMA_VERSION = 1


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceRef(_Model):
    kind: str  # screenshot | hierarchy | device_log | crash_log | xctest_log | runner_log | api_exchanges
    path: str  # relative to the run directory
    description: str = ""
    step_index: int | None = None


class AttemptRecord(_Model):
    attempt: int
    status: StepStatus
    duration_ms: float
    failure_category: FailureCategory | None = None
    message: str | None = None


class StepResult(_Model):
    index: int  # position in execution order across all sections
    section: str  # preconditions | steps | cleanup
    position: int  # index within the section
    step_id: str | None = None
    kind: str
    name: str  # task/question/fixture/interaction name, or "wait"/"api"/"capture"
    target: str | None = None
    description: str | None = None
    platform: Platform
    params: dict[str, Any] = Field(default_factory=dict)  # as written in the plan (references unresolved)
    status: StepStatus = StepStatus.PENDING
    failure_category: FailureCategory | None = None
    message: str | None = None
    expected: Any = None
    actual: Any = None
    checks: list[dict[str, Any]] = Field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: float = 0.0
    attempts: list[AttemptRecord] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    api_exchange_ids: list[int] = Field(default_factory=list)


class ArtifactInfo(_Model):
    platform: Platform
    path: str
    kind: str  # apk | app
    sha256: str | None = None
    identifier: str | None = None  # package name / bundle ID
    version_name: str | None = None
    version_code: str | None = None
    size_bytes: int | None = None
    extra: dict[str, Any] = Field(default_factory=dict)
    installed_verified: bool = False


class PlanInfo(_Model):
    name: str
    title: str | None = None
    dsl_version: int
    sha256: str
    path: str | None = None
    origin: str
    ai: dict[str, Any] | None = None
    approval: dict[str, Any] | None = None


class EnvironmentInfo(_Model):
    name: str
    kind: str
    backend_base_url: str
    test_support: bool


class RunnerInfo(_Model):
    framework_version: str
    runner: str
    runner_version: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class Summary(_Model):
    total: int = 0
    passed: int = 0
    failed: int = 0
    error: int = 0
    skipped: int = 0
    cancelled: int = 0
    retried: int = 0


class StateTransition(_Model):
    state: str
    at: str
    note: str | None = None


class Advisory(_Model):
    """AI analysis of a finished run. Informational only: it never changes a status."""

    label: str = "ADVISORY — AI-generated analysis; does not affect the recorded result"
    provider: str
    model: str | None = None
    generated_at: str
    summary: str
    suspected_causes: list[str] = Field(default_factory=list)
    suggested_plan_changes: list[str] = Field(default_factory=list)


class RunResult(_Model):
    schema_version: int = RESULT_SCHEMA_VERSION
    run_id: str
    status: RunStatus
    failure_category: FailureCategory | None = None
    message: str | None = None
    plan: PlanInfo
    platform: Platform
    environment: EnvironmentInfo
    artifact: ArtifactInfo | None = None
    device: dict[str, Any] = Field(default_factory=dict)
    runner: RunnerInfo
    configuration: dict[str, Any] = Field(default_factory=dict)
    started_at: str
    finished_at: str | None = None
    duration_ms: float = 0.0
    summary: Summary = Field(default_factory=Summary)
    steps: list[StepResult] = Field(default_factory=list)
    api_exchanges: list[dict[str, Any]] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    validation_issues: list[dict[str, Any]] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    lifecycle: list[StateTransition] = Field(default_factory=list)
    advisory: Advisory | None = None
