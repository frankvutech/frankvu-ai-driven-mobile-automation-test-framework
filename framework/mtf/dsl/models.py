"""Test Plan DSL, version 1.

A plan is data, never code. Every step is one of a closed set of kinds:

* ``task``        – a registered Screenplay task (business intent, e.g. ``login``)
* ``interaction`` – a registered low-level UI interaction on a registered test ID
* ``wait``        – an explicit, condition-based wait with a timeout
* ``assert``      – a question (or element property) compared with a matcher
* ``api``         – a request to an operation declared in the OpenAPI contract
* ``fixture``     – a registered setup/cleanup action (reset data, launch the app, seed a cart)
* ``capture``     – extra evidence (screenshot, UI hierarchy)

Unknown fields are rejected everywhere, so a typo can never be silently ignored. String values may
contain references (``${products.product_001.name}``, ``${vars.order_number}``,
``${secret:accounts.customer.password}``) but never expressions or code.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, field_validator, model_validator

from mtf.core.types import FailureCategory, PlanOrigin, Platform

CURRENT_VERSION = 1
SUPPORTED_VERSIONS = (1,)

# Bounds enforced by the schema itself; the safety policy may narrow them further.
MAX_STEP_TIMEOUT_MS = 120_000
MIN_STEP_TIMEOUT_MS = 100
MAX_RETRY_ATTEMPTS = 3
MAX_STEPS = 200

NAME_RE = r"^[a-z][a-z0-9_]{1,79}$"
REF_RE = re.compile(r"\$\{([^}]*)\}")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, use_enum_values=False)


# --- shared building blocks ---------------------------------------------------------------------
class Matcher(_Model):
    """Exactly one comparison. `equals` may compare with null; the others need a value."""

    equals: Any = None
    not_equals: Any = None
    contains: str | None = None
    not_contains: str | None = None
    matches: str | None = Field(default=None, description="A regular expression (full match).")
    one_of: list[Any] | None = None
    greater_than: float | None = None
    less_than: float | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> Matcher:
        chosen = [name for name in self.model_fields_set if name in type(self).model_fields]
        if len(chosen) != 1:
            raise ValueError(
                f"a matcher needs exactly one of {', '.join(type(self).model_fields)}; got {chosen or 'none'}"
            )
        if self.matches is not None:
            try:
                re.compile(self.matches)
            except re.error as exc:
                raise ValueError(f"invalid regular expression {self.matches!r}: {exc}") from exc
        return self

    @property
    def operator(self) -> str:
        return next(name for name in self.model_fields_set if name in type(self).model_fields)

    @property
    def operand(self) -> Any:
        return getattr(self, self.operator)


class RetryPolicy(_Model):
    """Retries are opt-in, bounded, and every attempt is recorded in the report."""

    max_attempts: int = Field(ge=1, le=MAX_RETRY_ATTEMPTS)
    delay_ms: int = Field(default=500, ge=0, le=10_000)
    on_failure: list[FailureCategory] = Field(
        default_factory=lambda: [FailureCategory.ELEMENT_NOT_FOUND, FailureCategory.TIMEOUT],
        description="Failure categories that may be retried. (`on` is avoided: YAML 1.1 reads it as true.)",
    )


class _StepBase(_Model):
    id: str | None = Field(default=None, pattern=NAME_RE, description="Optional stable step ID for reports.")
    description: str | None = Field(default=None, max_length=500)
    timeout_ms: int | None = Field(default=None, ge=MIN_STEP_TIMEOUT_MS, le=MAX_STEP_TIMEOUT_MS)
    retry: RetryPolicy | None = None
    platforms: list[Platform] | None = Field(
        default=None, description="Run this step only on these platforms (default: every plan platform)."
    )

    @property
    def kind(self) -> str:
        raise NotImplementedError


# --- step kinds ---------------------------------------------------------------------------------
class TaskStep(_StepBase):
    task: str = Field(pattern=NAME_RE)
    actor: str | None = Field(default=None, pattern=NAME_RE)
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")

    @property
    def kind(self) -> str:
        return "task"


class InteractionName(StrEnum):
    TAP = "tap"
    DOUBLE_TAP = "double_tap"
    LONG_PRESS = "long_press"
    TYPE_TEXT = "type_text"
    CLEAR_TEXT = "clear_text"
    SCROLL_TO = "scroll_to"
    SWIPE = "swipe"
    DRAG_TO = "drag_to"
    BACK = "back"
    DISMISS_KEYBOARD = "dismiss_keyboard"
    LAUNCH_APP = "launch_app"
    RELAUNCH_APP = "relaunch_app"
    TERMINATE_APP = "terminate_app"
    BACKGROUND_APP = "background_app"
    OPEN_DEEP_LINK = "open_deep_link"


class InteractionStep(_StepBase):
    interaction: InteractionName
    target: str | None = None
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")

    @property
    def kind(self) -> str:
        return "interaction"


class ElementState(StrEnum):
    VISIBLE = "visible"
    GONE = "gone"
    ENABLED = "enabled"
    DISABLED = "disabled"
    SELECTED = "selected"
    NOT_SELECTED = "not_selected"


class WaitSpec(_Model):
    target: str
    state: ElementState = ElementState.VISIBLE
    text: Matcher | None = Field(default=None, description="Additionally wait until the element text matches.")
    timeout_ms: int = Field(ge=MIN_STEP_TIMEOUT_MS, le=MAX_STEP_TIMEOUT_MS)


class WaitStep(_StepBase):
    wait: WaitSpec

    @property
    def kind(self) -> str:
        return "wait"


class ElementProperty(StrEnum):
    VISIBLE = "visible"
    EXISTS = "exists"
    TEXT = "text"
    ENABLED = "enabled"
    SELECTED = "selected"
    VALUE = "value"


class AssertSpec(_Model):
    question: str | None = Field(default=None, pattern=NAME_RE)
    target: str | None = None
    property: ElementProperty | None = None
    actor: str | None = Field(default=None, pattern=NAME_RE)
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")
    expect: Matcher
    timeout_ms: int | None = Field(
        default=None,
        ge=0,
        le=MAX_STEP_TIMEOUT_MS,
        description="Keep re-asking until the matcher holds or the timeout expires (UI questions only).",
    )
    save: str | None = Field(
        default=None,
        pattern=NAME_RE,
        description="Store the answer as ${vars.<name>} for later steps (UI/API correlation).",
    )

    @model_validator(mode="after")
    def _question_or_target(self) -> AssertSpec:
        if (self.question is None) == (self.target is None):
            raise ValueError("an assertion needs exactly one of `question` or `target`")
        if self.target is not None and self.property is None:
            raise ValueError("an element assertion needs `property`")
        if self.question is not None and self.property is not None:
            raise ValueError("`property` applies to element assertions only")
        return self


class AssertStep(_StepBase):
    assert_: AssertSpec = Field(alias="assert")

    @property
    def kind(self) -> str:
        return "assert"


class HttpMethod(StrEnum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    PATCH = "PATCH"
    DELETE = "DELETE"


class JsonAssertion(Matcher):
    path: str = Field(description="Dotted JSON path, e.g. `items[0].product.id`, `error.code`, `$` for the body.")

    @model_validator(mode="after")
    def _exactly_one(self) -> JsonAssertion:
        chosen = [n for n in self.model_fields_set if n in Matcher.model_fields]
        if len(chosen) != 1:
            raise ValueError(f"json assertion on {self.path!r} needs exactly one matcher; got {chosen or 'none'}")
        return self

    @property
    def operator(self) -> str:
        return next(name for name in self.model_fields_set if name in Matcher.model_fields)


class ApiExpect(_Model):
    status: int | list[int]
    headers: dict[str, Matcher] = Field(default_factory=dict)
    schema_: bool = Field(default=True, alias="schema", description="Validate the body against the contract.")
    json_: list[JsonAssertion] = Field(default_factory=list, alias="json")

    @field_validator("status")
    @classmethod
    def _status_range(cls, value: int | list[int]) -> int | list[int]:
        for status in value if isinstance(value, list) else [value]:
            if not 100 <= status <= 599:
                raise ValueError(f"invalid HTTP status {status}")
        return value


class ApiSpec(_Model):
    method: HttpMethod
    path: str = Field(
        pattern=r"^/[A-Za-z0-9_\-/{}.]*$", description="Path template exactly as in the OpenAPI contract."
    )
    path_params: dict[str, Any] = Field(default_factory=dict)
    query: dict[str, Any] = Field(default_factory=dict)
    body: Any = None
    raw_body: str | None = Field(default=None, max_length=10_000, description="A literal (possibly malformed) body.")
    as_: str | None = Field(default=None, alias="as", pattern=NAME_RE, description="Actor whose token is sent.")
    token: Literal["valid", "none", "invalid"] = Field(
        default="valid", description="`invalid` sends a deliberately malformed token (negative tests)."
    )
    expect: ApiExpect
    save: dict[str, str] = Field(default_factory=dict, description="Run variables taken from the response body.")

    @model_validator(mode="after")
    def _one_body(self) -> ApiSpec:
        if self.body is not None and self.raw_body is not None:
            raise ValueError("use either `body` or `raw_body`, not both")
        for var in self.save:
            if not re.match(NAME_RE, var):
                raise ValueError(f"invalid variable name {var!r}")
        return self


class ApiStep(_StepBase):
    api: ApiSpec

    @property
    def kind(self) -> str:
        return "api"


class FixtureStep(_StepBase):
    fixture: str = Field(pattern=NAME_RE)
    actor: str | None = Field(default=None, pattern=NAME_RE)
    with_: dict[str, Any] = Field(default_factory=dict, alias="with")

    @property
    def kind(self) -> str:
        return "fixture"


class CaptureSpec(_Model):
    screenshot: str | None = Field(default=None, pattern=NAME_RE)
    hierarchy: bool = False


class CaptureStep(_StepBase):
    capture: CaptureSpec

    @property
    def kind(self) -> str:
        return "capture"


STEP_KINDS = ("task", "interaction", "wait", "assert", "api", "fixture", "capture")


def _step_kind(value: Any) -> str | None:
    if isinstance(value, dict):
        found = [k for k in STEP_KINDS if k in value]
        return found[0] if len(found) == 1 else None
    if isinstance(value, _StepBase):
        return value.kind
    return None


Step = Annotated[
    Annotated[TaskStep, Tag("task")]
    | Annotated[InteractionStep, Tag("interaction")]
    | Annotated[WaitStep, Tag("wait")]
    | Annotated[AssertStep, Tag("assert")]
    | Annotated[ApiStep, Tag("api")]
    | Annotated[FixtureStep, Tag("fixture")]
    | Annotated[CaptureStep, Tag("capture")],
    Discriminator(
        _step_kind,
        custom_error_type="invalid_step",
        custom_error_message="a step needs exactly one of: " + ", ".join(STEP_KINDS),
    ),
]


# --- plan-level sections ------------------------------------------------------------------------
class ActorSpec(_Model):
    account: str | None = Field(default=None, description="Account reference from contracts/test-data.yaml.")
    description: str | None = None


class ScreenshotPolicy(StrEnum):
    NEVER = "never"
    ON_FAILURE = "on_failure"
    EVERY_STEP = "every_step"


class EvidencePolicy(_Model):
    screenshots: ScreenshotPolicy = ScreenshotPolicy.ON_FAILURE
    hierarchy: Literal["never", "on_failure"] = "on_failure"
    device_logs: Literal["never", "on_failure", "always"] = "on_failure"
    api_exchanges: Literal["never", "always"] = "always"


class Defaults(_Model):
    timeout_ms: int = Field(default=10_000, ge=MIN_STEP_TIMEOUT_MS, le=MAX_STEP_TIMEOUT_MS)
    actor: str | None = Field(default=None, pattern=NAME_RE)


class AiProvenance(_Model):
    provider: str
    model: str | None = None
    request: str = Field(description="The natural-language request (secrets are never included).")
    request_sha256: str
    generated_at: str
    explanation: str = ""
    assumptions: list[str] = Field(default_factory=list)


class Approval(_Model):
    approved_by: str = Field(min_length=1)
    approved_at: str
    plan_sha256: str = Field(description="Canonical hash of the plan content at approval time.")
    note: str | None = None


class PlanMetadata(_Model):
    origin: PlanOrigin = PlanOrigin.MANUAL
    author: str | None = None
    created_at: str | None = None
    ai: AiProvenance | None = None
    approval: Approval | None = None

    @model_validator(mode="after")
    def _ai_needs_provenance(self) -> PlanMetadata:
        if self.origin == PlanOrigin.AI_GENERATED and self.ai is None:
            raise ValueError("an AI-generated plan must record its `metadata.ai` provenance")
        return self


class TestPlan(_Model):
    __test__ = False

    version: Literal[1] = Field(description="DSL major version. See docs/test-plan-dsl.md for compatibility rules.")
    name: str = Field(pattern=NAME_RE)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    tags: list[str] = Field(default_factory=list)
    platforms: list[Platform] = Field(min_length=1)
    metadata: PlanMetadata = Field(default_factory=PlanMetadata)
    actors: dict[str, ActorSpec] = Field(default_factory=dict)
    defaults: Defaults = Field(default_factory=Defaults)
    evidence: EvidencePolicy = Field(default_factory=EvidencePolicy)
    preconditions: list[Step] = Field(default_factory=list, max_length=MAX_STEPS)
    steps: list[Step] = Field(min_length=1, max_length=MAX_STEPS)
    cleanup: list[Step] = Field(default_factory=list, max_length=MAX_STEPS)

    @field_validator("platforms")
    @classmethod
    def _unique_platforms(cls, value: list[Platform]) -> list[Platform]:
        if len(set(value)) != len(value):
            raise ValueError("platforms must not repeat")
        if Platform.API in value and len(value) > 1:
            raise ValueError("`api` plans run without a device; list mobile platforms in a separate plan")
        return value

    @field_validator("actors")
    @classmethod
    def _actor_names(cls, value: dict[str, ActorSpec]) -> dict[str, ActorSpec]:
        for name in value:
            if not re.match(NAME_RE, name):
                raise ValueError(f"invalid actor name {name!r}")
        return value

    def all_steps(self) -> list[tuple[str, int, Any]]:
        """(section, index, step) for preconditions, steps and cleanup, in execution order."""
        out: list[tuple[str, int, Any]] = []
        for section in ("preconditions", "steps", "cleanup"):
            out.extend((section, i, s) for i, s in enumerate(getattr(self, section)))
        return out


def references_in(value: Any) -> list[str]:
    """Every ``${...}`` reference inside a (nested) value."""
    found: list[str] = []
    if isinstance(value, str):
        found.extend(m.group(1) for m in REF_RE.finditer(value))
    elif isinstance(value, dict):
        for item in value.values():
            found.extend(references_in(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(references_in(item))
    return found
