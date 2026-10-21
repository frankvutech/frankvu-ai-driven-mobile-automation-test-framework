"""Step commands (Command pattern) and the plan interpreter's per-step logic.

Each validated DSL step becomes one command. A command resolves its references, performs its work
through actors (Screenplay), the guarded UI driver or the API client, and reports an outcome. Only
registered implementations can run: there is no path from plan text to code.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from mtf.api_runner import jsonpath
from mtf.api_runner.assertions import Check, check_header, check_json, check_schema, check_status
from mtf.api_runner.client import ApiClient
from mtf.contracts.bundle import Contracts
from mtf.core.catalog import ActionKind
from mtf.core.errors import (
    ApiAssertionFailed,
    ApiContractViolation,
    AssertionFailed,
    ConfigurationError,
    MtfError,
    StepTimeout,
)
from mtf.core.matching import evaluate
from mtf.core.ports import ElementInfo, UiDriver, WaitState
from mtf.dsl.models import (
    ApiStep,
    AssertStep,
    CaptureStep,
    ElementProperty,
    FixtureStep,
    InteractionStep,
    TaskStep,
    WaitStep,
)
from mtf.orchestrator.resolver import ReferenceResolver
from mtf.reporting.models import EvidenceRef
from mtf.screenplay.actor import Actor
from mtf.screenplay.interactions import perform_interaction
from mtf.screenplay.registry import REGISTRY

POLL_INTERVAL_S = 0.3


@dataclass
class ExecutionContext:
    contracts: Contracts
    resolver: ReferenceResolver
    actors: dict[str, Actor]
    system_actor: Actor
    default_actor: str | None
    default_timeout_ms: int
    driver: UiDriver | None
    api: ApiClient | None
    variables: dict[str, Any]
    evidence_dir: Path

    def actor(self, name: str | None) -> Actor:
        chosen = name or self.default_actor
        if chosen is None:
            return self.system_actor
        if chosen not in self.actors:
            raise ConfigurationError(f"unknown actor {chosen!r}")
        return self.actors[chosen]

    def require_driver(self) -> UiDriver:
        if self.driver is None:
            raise ConfigurationError("this step needs a device, but the run has no UI driver")
        return self.driver

    def require_api(self) -> ApiClient:
        if self.api is None:
            raise ConfigurationError("this step needs the backend API, but no backend is configured")
        return self.api


@dataclass
class Outcome:
    expected: Any = None
    actual: Any = None
    checks: list[Check] = field(default_factory=list)
    evidence: list[EvidenceRef] = field(default_factory=list)


class StepCommand(ABC):
    kind: str

    def __init__(self, step: Any, location: str) -> None:
        self.step = step
        self.location = location

    @property
    def name(self) -> str:
        return self.kind

    @property
    def target(self) -> str | None:
        return None

    def timeout_ms(self, ctx: ExecutionContext) -> int:
        return int(self.step.timeout_ms or ctx.default_timeout_ms)

    @abstractmethod
    def execute(self, ctx: ExecutionContext) -> Outcome: ...


class TaskCommand(StepCommand):
    kind = "task"
    step: TaskStep

    @property
    def name(self) -> str:
        return self.step.task

    def execute(self, ctx: ExecutionContext) -> Outcome:
        task = REGISTRY.create(ActionKind.TASK, self.step.task, ctx.resolver.resolve(self.step.with_))
        ctx.actor(self.step.actor).attempts_to(task)
        return Outcome()


class FixtureCommand(StepCommand):
    kind = "fixture"
    step: FixtureStep

    @property
    def name(self) -> str:
        return self.step.fixture

    def execute(self, ctx: ExecutionContext) -> Outcome:
        fixture = REGISTRY.create(ActionKind.FIXTURE, self.step.fixture, ctx.resolver.resolve(self.step.with_))
        needs_identity = self.step.actor is not None or fixture.spec.needs_actor
        actor = ctx.actor(self.step.actor) if needs_identity else ctx.system_actor
        actor.attempts_to(fixture)
        return Outcome()


class InteractionCommand(StepCommand):
    kind = "interaction"
    step: InteractionStep

    @property
    def name(self) -> str:
        return self.step.interaction.value

    @property
    def target(self) -> str | None:
        return self.step.target

    def execute(self, ctx: ExecutionContext) -> Outcome:
        ctx.require_driver()
        perform_interaction(
            ctx.actor(None),
            self.step.interaction,
            self.step.target,
            ctx.resolver.resolve(self.step.with_),
            self.timeout_ms(ctx),
        )
        return Outcome()


class WaitCommand(StepCommand):
    kind = "wait"
    step: WaitStep

    @property
    def target(self) -> str | None:
        return self.step.wait.target

    def execute(self, ctx: ExecutionContext) -> Outcome:
        driver = ctx.require_driver()
        spec = self.step.wait
        started = time.monotonic()
        info = driver.wait_for(spec.target, WaitState(spec.state.value), spec.timeout_ms)
        if spec.text is None:
            return Outcome(expected=spec.state.value, actual=spec.state.value)
        expected = ctx.resolver.resolve(spec.text.operand)
        deadline = started + spec.timeout_ms / 1000
        while True:
            actual = info.text or info.value
            result = evaluate(spec.text.operator, expected, actual)
            if result.passed:
                return Outcome(expected=expected, actual=actual)
            if time.monotonic() >= deadline:
                raise StepTimeout(
                    f"{spec.target} text did not satisfy {spec.text.operator} {expected!r} within {spec.timeout_ms} ms "
                    f"(last value {actual!r})",
                    details={"expected": expected, "actual": actual},
                )
            time.sleep(POLL_INTERVAL_S)
            info = driver.read(spec.target)


def _element_property(info: ElementInfo, prop: ElementProperty) -> Any:
    match prop:
        case ElementProperty.VISIBLE:
            return info.visible
        case ElementProperty.EXISTS:
            return info.exists
        case ElementProperty.TEXT:
            return (info.text or info.value) if info.exists else None
        case ElementProperty.VALUE:
            return info.value if info.exists else None
        case ElementProperty.ENABLED:
            return info.enabled
        case ElementProperty.SELECTED:
            return info.selected


class AssertCommand(StepCommand):
    kind = "assert"
    step: AssertStep

    @property
    def name(self) -> str:
        return self.step.assert_.question or f"element.{self.step.assert_.property}"

    @property
    def target(self) -> str | None:
        return self.step.assert_.target

    def execute(self, ctx: ExecutionContext) -> Outcome:
        spec = self.step.assert_
        expected = ctx.resolver.resolve(spec.expect.operand)
        if spec.question is not None:
            question = REGISTRY.create(ActionKind.QUESTION, spec.question, ctx.resolver.resolve(spec.with_))
            actor = ctx.actor(spec.actor)

            def ask() -> Any:
                return actor.asks(question)
        else:
            driver = ctx.require_driver()
            target, prop = spec.target or "", spec.property or ElementProperty.EXISTS

            def ask() -> Any:
                return _element_property(driver.read(target), prop)

        deadline = time.monotonic() + (spec.timeout_ms or 0) / 1000
        last_error: MtfError | None = None
        actual: Any = None
        while True:
            try:
                actual = ask()
                last_error = None
                result = evaluate(spec.expect.operator, expected, actual)
                if result.passed:
                    if spec.save:
                        ctx.variables[spec.save] = actual
                    return Outcome(expected=expected, actual=actual)
            except MtfError as exc:
                if not spec.timeout_ms:
                    raise
                last_error = exc
            if time.monotonic() >= deadline:
                break
            time.sleep(POLL_INTERVAL_S)
        if last_error is not None:
            raise last_error
        raise AssertionFailed(
            f"expected {self.name} {spec.expect.operator} {expected!r}, but it was {actual!r}",
            expected=expected,
            actual=actual,
        )


class ApiCommand(StepCommand):
    kind = "api"
    step: ApiStep

    @property
    def name(self) -> str:
        return f"{self.step.api.method.value} {self.step.api.path}"

    def execute(self, ctx: ExecutionContext) -> Outcome:
        client = ctx.require_api()
        spec = self.step.api
        operation = ctx.contracts.api.operation(spec.method.value, spec.path)
        account_ref = None
        if spec.as_ is not None:
            account_ref = ctx.actor(spec.as_).account_ref
        client.current_step = self.location
        try:
            response = client.send(
                operation,
                path_params=ctx.resolver.resolve(spec.path_params),
                query=ctx.resolver.resolve(spec.query),
                body=ctx.resolver.resolve(spec.body),
                raw_body=ctx.resolver.resolve(spec.raw_body),
                account_ref=account_ref,
                auth_mode=spec.token,
            )
        finally:
            client.current_step = None
        expect = spec.expect
        checks = [check_status(response, expect.status)]
        for name, matcher in expect.headers.items():
            checks.append(check_header(response, name, matcher.operator, ctx.resolver.resolve(matcher.operand)))
        if expect.schema_ and str(response.status) in operation.statuses:
            checks.append(check_schema(ctx.contracts.api, operation, response))
        elif expect.schema_:
            checks.append(
                Check(
                    "schema",
                    f"status {response.status} declared for {operation.key}",
                    False,
                    list(operation.statuses),
                    response.status,
                )
            )
        for assertion in expect.json_:
            checks.append(
                check_json(response, assertion.path, assertion.operator, ctx.resolver.resolve(assertion.operand))
            )
        for var, path in spec.save.items():
            value = jsonpath.get(response.body, path)
            if value is not jsonpath.MISSING:
                ctx.variables[var] = value
        failed = [c for c in checks if not c.passed]
        outcome = Outcome(expected=expect.status, actual=response.status, checks=checks)
        if failed:
            message = "; ".join(f"{c.description} (actual: {c.actual!r})" for c in failed)
            details = {"checks": [c.to_dict() for c in checks]}
            if all(c.kind == "schema" for c in failed):
                raise ApiContractViolation(message, details=details)
            error = ApiAssertionFailed(message, expected=expect.status, actual=response.status)
            error.details.update(details)
            raise error
        missing_vars = [v for v, p in spec.save.items() if jsonpath.get(response.body, p) is jsonpath.MISSING]
        if missing_vars:
            raise ApiAssertionFailed(
                f"cannot save {', '.join(missing_vars)}: path not in response",
                expected=spec.save,
                actual=None,
            )
        return outcome


class CaptureCommand(StepCommand):
    kind = "capture"
    step: CaptureStep

    def execute(self, ctx: ExecutionContext) -> Outcome:
        driver = ctx.require_driver()
        evidence: list[EvidenceRef] = []
        prefix = self.location.replace("[", "-").replace("]", "")
        if self.step.capture.screenshot:
            path = ctx.evidence_dir / f"{prefix}-{self.step.capture.screenshot}.png"
            if driver.screenshot(path):
                evidence.append(
                    EvidenceRef(kind="screenshot", path=str(path), description=self.step.capture.screenshot)
                )
            else:
                evidence.append(
                    EvidenceRef(kind="screenshot_suppressed", path="", description="a secret may be visible")
                )
        if self.step.capture.hierarchy:
            path = ctx.evidence_dir / f"{prefix}-hierarchy.txt"
            path.write_text(driver.hierarchy(), encoding="utf-8")
            evidence.append(EvidenceRef(kind="hierarchy", path=str(path)))
        return Outcome(evidence=evidence)


def command_for(step: Any, location: str) -> StepCommand:
    commands: dict[type, type[StepCommand]] = {
        TaskStep: TaskCommand,
        FixtureStep: FixtureCommand,
        InteractionStep: InteractionCommand,
        WaitStep: WaitCommand,
        AssertStep: AssertCommand,
        ApiStep: ApiCommand,
        CaptureStep: CaptureCommand,
    }
    return commands[type(step)](step, location)
