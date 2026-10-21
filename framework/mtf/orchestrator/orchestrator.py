"""The execution orchestrator.

    validate plan + configuration ──► prepare session (verify + install prebuilt app, start runner)
        ──► preconditions ──► steps ──► cleanup (always attempted) ──► reports

Serial by design: one plan, one device, one backend data set at a time. A failing step stops the
remaining steps (they are recorded as skipped); cleanup still runs. Nothing in this module can turn a
failure into a pass: statuses only ever move from pending to a final value once.
"""

from __future__ import annotations

import concurrent.futures
import contextlib
import json
import secrets as pysecrets
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mtf import __version__
from mtf.api_runner.client import ApiClient, ApiExchange, HttpxTransport
from mtf.contracts.bundle import Contracts
from mtf.contracts.test_data import SecretProvider
from mtf.core.cancellation import CancellationToken
from mtf.core.catalog import Catalog
from mtf.core.config import Environment
from mtf.core.errors import MtfError, RunCancelled, StepTimeout
from mtf.core.events import (
    API_EXCHANGE,
    EVIDENCE_CAPTURED,
    RUN_FINISHED,
    RUN_STATE_CHANGED,
    STEP_FINISHED,
    STEP_RETRY,
    STEP_STARTED,
    Event,
    EventBus,
    utc_now,
)
from mtf.core.ports import HttpTransport
from mtf.core.redaction import Redactor
from mtf.core.types import FailureCategory, Platform, RunStatus, StepStatus
from mtf.dsl.loader import plan_hash
from mtf.dsl.models import ScreenshotPolicy, TestPlan, references_in
from mtf.orchestrator.commands import ExecutionContext, StepCommand, command_for
from mtf.orchestrator.contract_guard import ContractCheckedDriver
from mtf.orchestrator.lifecycle import RunLifecycle, RunState
from mtf.orchestrator.resolver import ReferenceResolver
from mtf.orchestrator.sessions import PlatformSession
from mtf.reporting.models import (
    AttemptRecord,
    EnvironmentInfo,
    EvidenceRef,
    PlanInfo,
    RunnerInfo,
    RunResult,
    StateTransition,
    StepResult,
    Summary,
)
from mtf.reporting.writer import write_reports
from mtf.screenplay.abilities import CallBackendApi, OperateMobileApp
from mtf.screenplay.actor import Actor, ScreenplayContext
from mtf.screenplay.robots import robots_for
from mtf.validator.policy import SafetyPolicy
from mtf.validator.validator import PlanValidator


@dataclass
class RunRequest:
    plan: TestPlan
    platform: Platform
    environment: Environment
    session: PlatformSession
    reports_dir: Path
    plan_path: Path | None = None
    environ: dict[str, str] | None = None
    cancellation: CancellationToken = field(default_factory=CancellationToken)
    events: EventBus = field(default_factory=EventBus)
    transport: HttpTransport | None = None  # tests inject a fake; production uses httpx
    max_step_duration_s: float = 300.0  # watchdog for one step (tasks contain several waits)
    # False lets the caller reuse a prepared session (installed app, running runner) for the next plan.
    # A session whose runner was abandoned (watchdog/cancellation) is always closed.
    close_session: bool = True
    run_id: str | None = None
    environment_warnings: list[str] = field(default_factory=list)


class Orchestrator:
    def __init__(self, contracts: Contracts, catalog: Catalog, policy: SafetyPolicy | None = None) -> None:
        self.contracts = contracts
        self.catalog = catalog
        self.base_policy = policy or SafetyPolicy()

    def run(self, request: RunRequest) -> RunResult:
        return _Run(self, request).execute()


_TEXT_SUFFIXES = frozenset({".log", ".txt", ".json", ".jsonl", ".html", ".xml", ".ips", ".xctestrun"})


def _new_run_id(plan_name: str, platform: Platform) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{plan_name}-{platform.value}-{pysecrets.token_hex(3)}"


class _Run:
    def __init__(self, orchestrator: Orchestrator, request: RunRequest) -> None:
        self.o = orchestrator
        self.r = request
        self.plan = request.plan
        self.platform = request.platform
        self.run_id = request.run_id or _new_run_id(self.plan.name, self.platform)
        self.run_dir = request.reports_dir / "runs" / self.run_id
        self.evidence_dir = self.run_dir / "evidence"
        self.secrets = SecretProvider(orchestrator.contracts.test_data, request.environ)
        self.redactor = Redactor(self.secrets.configured_values())
        self.events = request.events
        self.started = time.monotonic()
        self.run_deadline = self.started + request.environment.timeouts.run_s
        self.exchanges: list[dict[str, Any]] = []
        self.variables: dict[str, Any] = {}
        self.lifecycle = RunLifecycle(on_change=self._on_state_change)
        self.driver_usable = True
        self.api: ApiClient | None = None
        self.guarded: ContractCheckedDriver | None = None
        self.result = RunResult(
            run_id=self.run_id,
            status=RunStatus.ERROR,
            plan=PlanInfo(
                name=self.plan.name,
                title=self.plan.title,
                description=self.plan.description,
                tags=list(self.plan.tags),
                dsl_version=self.plan.version,
                sha256=plan_hash(self.plan),
                path=str(request.plan_path) if request.plan_path else None,
                origin=self.plan.metadata.origin.value,
                ai=self.plan.metadata.ai.model_dump(mode="json") if self.plan.metadata.ai else None,
                approval=self.plan.metadata.approval.model_dump(mode="json") if self.plan.metadata.approval else None,
            ),
            platform=self.platform,
            environment=EnvironmentInfo(
                name=request.environment.name,
                kind=request.environment.kind.value,
                backend_base_url=request.environment.backend.base_url,
                test_support=request.environment.backend.test_support,
            ),
            runner=RunnerInfo(framework_version=__version__, runner="unknown"),
            started_at=utc_now().isoformat(),
            warnings=list(request.environment_warnings),
        )

    # --- events --------------------------------------------------------------------------------
    def _publish(self, event_type: str, **payload: Any) -> None:
        self.events.publish(Event(event_type, self.run_id, self.redactor.redact(payload)))

    def _write_event(self, event: Event) -> None:
        if event.run_id != self.run_id:
            return
        with (self.run_dir / "events.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(self.redactor.redact(event.to_dict()), default=str) + "\n")

    def _on_state_change(self, previous: RunState, new: RunState, note: str | None) -> None:
        self.result.lifecycle.append(StateTransition(state=new.value, at=utc_now().isoformat(), note=note))
        self._publish(RUN_STATE_CHANGED, previous=previous.value, state=new.value, note=note)

    def _on_exchange(self, exchange: ApiExchange) -> None:
        if self.plan.evidence.api_exchanges == "never":
            return
        exchange_id = len(self.exchanges)
        self.exchanges.append({"id": exchange_id, **exchange.to_dict()})
        self._publish(
            API_EXCHANGE,
            id=exchange_id,
            operation=exchange.operation,
            status=exchange.status,
            step=exchange.step,
        )

    # --- main flow -----------------------------------------------------------------------------
    def execute(self) -> RunResult:
        self.result.lifecycle.append(StateTransition(state=RunState.CREATED.value, at=self.result.started_at))
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self.events.subscribe(self._write_event)
        session = self.r.session
        try:
            self.lifecycle.advance(RunState.VALIDATING)
            if not self._validate():
                self.lifecycle.advance(RunState.REJECTED)
                self.result.status = RunStatus.REJECTED
                self.result.failure_category = FailureCategory.PLAN_VALIDATION_ERROR
                return self._finish()
            self.lifecycle.advance(RunState.PREPARING)
            self._build_step_results()
            prepared = self._prepare_or_record(session)
            if prepared:
                context = self._context(session)
                self.lifecycle.advance(RunState.SETTING_UP)
                ok = self._run_section("preconditions", context)
                if ok:
                    self.lifecycle.advance(RunState.EXECUTING)
                    self._run_section("steps", context)
                self.lifecycle.advance(RunState.CLEANING_UP)
                self._run_section("cleanup", context, cleanup=True)
            else:
                self.lifecycle.advance(RunState.CLEANING_UP, note="preparation failed; nothing to clean up")
            self._decide_status()
            return self._finish()
        except Exception as exc:
            self.result.status = RunStatus.ERROR
            self.result.failure_category = FailureCategory.RUNNER_ERROR
            self.result.message = f"internal error: {exc.__class__.__name__}: {self.redactor.redact_text(str(exc))}"
            return self._finish(force=True)
        finally:
            self._close(session)

    def _close(self, session: PlatformSession) -> None:
        """Releases the API client and the session. A failure here is reported, never raised."""
        close_session = self.r.close_session or not self.driver_usable
        for name, closer in (
            ("api client", self.api.close if self.api else None),
            ("session", session.close if close_session else None),
        ):
            if closer is None:
                continue
            try:
                closer()
            except Exception as exc:
                note = f"closing the {name} failed: {exc.__class__.__name__}: {self.redactor.redact_text(str(exc))}"
                self.result.warnings.append(note)

    def _validate(self) -> bool:
        policy = SafetyPolicy(
            **{**self.o.base_policy.__dict__, "allow_test_support": self.r.environment.backend.test_support}
        )
        validator = PlanValidator(self.o.contracts, self.o.catalog, policy)
        report = validator.validate(self.plan, [self.platform])
        self.result.validation_issues = report.to_dict()["issues"]
        if not report.ok:
            self.result.message = f"plan rejected: {len(report.errors)} validation error(s); nothing was launched"
            return False
        missing = self.secrets.missing(self._secret_refs())
        if missing:
            self.result.message = "missing secret environment variable(s): " + ", ".join(missing)
            self.result.failure_category = FailureCategory.CONFIGURATION_ERROR
            self.result.validation_issues.append(
                {
                    "severity": "error",
                    "code": "missing_secret",
                    "location": "environment",
                    "message": self.result.message,
                }
            )
            return False
        return True

    def _secret_refs(self) -> list[str]:
        refs = set()
        for _, _, step in self.plan.all_steps():
            for ref in references_in(step.model_dump(mode="json", by_alias=True, exclude_unset=True)):
                if ref.startswith("secret:"):
                    refs.add(ref.removeprefix("secret:"))
        for actor in self.plan.actors.values():  # actors may log in (UI login task or API token)
            if actor.account is not None:
                refs.add(f"accounts.{actor.account}.password")
        return sorted(refs)

    def _prepare(self, session: PlatformSession) -> bool:
        try:
            session.prepare(self.run_dir)
        except Exception:
            self.driver_usable = False  # a half-prepared session is never reused: it is closed
            raise
        return True

    def _prepare_or_record(self, session: PlatformSession) -> bool:
        try:
            return self._prepare(session)
        except MtfError as exc:
            self.result.status = RunStatus.ERROR
            self.result.failure_category = exc.category
            self.result.message = f"preparation failed: {self.redactor.redact_text(exc.message)}"
            return False
        except Exception as exc:
            self.result.status = RunStatus.ERROR
            self.result.failure_category = FailureCategory.INFRASTRUCTURE_ERROR
            self.result.message = f"preparation failed: {exc.__class__.__name__}: {self.redactor.redact_text(str(exc))}"
            return False
        finally:
            self.result.artifact = session.artifact_info()
            self.result.device = session.device_info()
            self.result.runner = session.runner_info()
            self.result.configuration = session.configuration()

    def _context(self, session: PlatformSession) -> ExecutionContext:
        env = self.r.environment
        self.api = ApiClient(
            env.backend.base_url,
            contract=self.o.contracts.api,
            transport=self.r.transport or HttpxTransport(verify_tls=env.backend.verify_tls),
            redactor=self.redactor,
            test_data=self.o.contracts.test_data,
            secrets=self.secrets,
            timeout_s=env.backend.timeout_s,
            on_exchange=self._on_exchange,
        )
        driver = None
        if session.driver is not None:
            driver = ContractCheckedDriver(session.driver, self.o.contracts.test_ids, self.redactor)
            self.guarded = driver
        timeout = self.plan.defaults.timeout_ms or env.timeouts.default_step_ms
        sp_context = ScreenplayContext(
            platform=self.platform,
            test_data=self.o.contracts.test_data,
            secrets=self.secrets,
            redactor=self.redactor,
            default_timeout_ms=timeout,
            variables=self.variables,
            screen_roots=self.o.contracts.test_ids.screen_roots(self.platform) if self.platform.is_mobile else [],
        )

        def make_actor(name: str, account: str | None) -> Actor:
            actor = Actor(name, sp_context, account).can(CallBackendApi(self.api))  # type: ignore[arg-type]
            if driver is not None:
                actor.can(OperateMobileApp(driver, robots_for(driver, timeout)))
            return actor

        actors = {name: make_actor(name, spec.account) for name, spec in self.plan.actors.items()}
        resolver = ReferenceResolver(self.o.contracts.test_data, self.secrets, self.redactor, self.variables)
        return ExecutionContext(
            contracts=self.o.contracts,
            resolver=resolver,
            actors=actors,
            system_actor=make_actor("system", None),
            default_actor=self.plan.defaults.actor,
            default_timeout_ms=timeout,
            driver=driver,
            api=self.api,
            variables=self.variables,
            evidence_dir=self.evidence_dir,
        )

    # --- steps ---------------------------------------------------------------------------------
    def _build_step_results(self) -> None:
        for index, (section, position, step) in enumerate(self.plan.all_steps()):
            command = command_for(step, f"{section}[{position}]")
            raw = step.model_dump(mode="json", by_alias=True, exclude_unset=True)
            params = {
                k: v for k, v in raw.items() if k not in ("id", "description", "platforms", "timeout_ms", "retry")
            }
            self.result.steps.append(
                StepResult(
                    index=index,
                    section=section,
                    position=position,
                    step_id=step.id,
                    kind=command.kind,
                    name=command.name,
                    target=command.target,
                    description=step.description,
                    platform=self.platform,
                    params=self.redactor.redact(params),
                )
            )

    def _run_section(self, section: str, context: ExecutionContext, *, cleanup: bool = False) -> bool:
        """Runs one section. Returns False when a step did not pass (remaining steps are skipped)."""
        ok = True
        for (sec, position, step), record in zip(self.plan.all_steps(), self.result.steps, strict=True):
            if sec != section:
                continue
            if step.platforms is not None and self.platform not in step.platforms:
                self._skip(record, "step does not apply to this platform")
                continue
            if self.r.cancellation.cancelled:
                record.status = StepStatus.CANCELLED
                record.message = f"run cancelled: {self.r.cancellation.reason}"
                ok = False
                continue
            if not ok and not cleanup:
                self._skip(record, "skipped: an earlier step did not pass")
                continue
            if time.monotonic() > self.run_deadline:
                self.r.cancellation.cancel(f"run timeout of {self.r.environment.timeouts.run_s}s exceeded")
                record.status = StepStatus.CANCELLED
                record.message = self.r.cancellation.reason
                ok = False
                continue
            command = command_for(step, f"{section}[{position}]")
            if (
                not self.driver_usable
                and command.kind in ("interaction", "wait", "capture", "task", "assert")
                and self.platform.is_mobile
            ):
                self._skip(record, "skipped: the device runner is unavailable after an earlier timeout")
                ok = False
                continue
            self._execute(command, step, record, context)
            if record.status != StepStatus.PASSED:
                ok = False
        return ok

    def _skip(self, record: StepResult, message: str) -> None:
        record.status = StepStatus.SKIPPED
        record.message = message
        self._publish(
            STEP_FINISHED,
            index=record.index,
            section=record.section,
            kind=record.kind,
            name=record.name,
            target=record.target,
            status=record.status.value,
            message=message,
        )

    def _execute(self, command: StepCommand, step: Any, record: StepResult, context: ExecutionContext) -> None:
        record.started_at = utc_now().isoformat()
        self._publish(
            STEP_STARTED,
            index=record.index,
            section=record.section,
            kind=record.kind,
            name=record.name,
            target=record.target,
        )
        started = time.monotonic()
        max_attempts = step.retry.max_attempts if step.retry else 1
        retry_on = set(step.retry.on_failure) if step.retry else set()
        attempt = 0
        while True:
            attempt += 1
            attempt_started = time.monotonic()
            error = self._attempt(command, record, context)
            status, category = self._classify(error)
            record.attempts.append(
                AttemptRecord(
                    attempt=attempt,
                    status=status,
                    duration_ms=round((time.monotonic() - attempt_started) * 1000, 1),
                    failure_category=category,
                    message=self.redactor.redact_text(error.message) if error else None,
                )
            )
            if error is None or attempt >= max_attempts or category not in retry_on or self.r.cancellation.cancelled:
                break
            self._publish(
                STEP_RETRY, index=record.index, attempt=attempt, category=category.value if category else None
            )
            time.sleep(step.retry.delay_ms / 1000)
        record.status, record.failure_category = status, category
        if error is not None:
            record.message = self.redactor.redact_text(error.message)
            record.expected = self.redactor.redact(error.details.get("expected", record.expected))
            record.actual = self.redactor.redact(error.details.get("actual", record.actual))
            if "checks" in error.details:
                record.checks = self.redactor.redact(error.details["checks"])
            crash = self._crash_check()
            if crash:
                record.message = f"{record.message} — {crash}"
                record.failure_category = FailureCategory.APP_CRASH
                record.status = StepStatus.FAILED
        record.finished_at = utc_now().isoformat()
        record.duration_ms = round((time.monotonic() - started) * 1000, 1)
        record.api_exchange_ids = [
            e["id"] for e in self.exchanges if e.get("step") == f"{record.section}[{record.position}]"
        ]
        self._collect_evidence(record, failed=record.status in (StepStatus.FAILED, StepStatus.ERROR))
        self._publish(
            STEP_FINISHED,
            index=record.index,
            section=record.section,
            kind=record.kind,
            name=record.name,
            target=record.target,
            status=record.status.value,
            category=record.failure_category.value if record.failure_category else None,
            message=record.message,
            duration_ms=record.duration_ms,
        )

    def _attempt(self, command: StepCommand, record: StepResult, context: ExecutionContext) -> MtfError | None:
        """Runs one attempt under the step watchdog and cooperative cancellation."""
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"step-{record.index}")
        future = executor.submit(command.execute, context)
        deadline = time.monotonic() + self.r.max_step_duration_s
        try:
            while True:
                try:
                    outcome = future.result(timeout=0.2)
                    break
                except concurrent.futures.TimeoutError:
                    if self.r.cancellation.cancelled:
                        self._abandon_driver(context, "cancelled")
                        return RunCancelled(f"run cancelled during the step: {self.r.cancellation.reason}")
                    if time.monotonic() > deadline:
                        self._abandon_driver(context, "step watchdog")
                        return StepTimeout(f"step exceeded the {self.r.max_step_duration_s:.0f}s step watchdog")
        except MtfError as exc:
            return exc
        except Exception as exc:
            return MtfError(f"{exc.__class__.__name__}: {exc}")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        record.expected = self.redactor.redact(outcome.expected)
        record.actual = self.redactor.redact(outcome.actual)
        record.checks = self.redactor.redact([c.to_dict() for c in outcome.checks])
        record.evidence.extend(outcome.evidence)
        return None

    def _abandon_driver(self, context: ExecutionContext, reason: str) -> None:
        if context.driver is not None and self.driver_usable:
            self.driver_usable = False
            with contextlib.suppress(Exception):  # best effort while abandoning
                context.driver.close()

    @staticmethod
    def _classify(error: MtfError | None) -> tuple[StepStatus, FailureCategory | None]:
        if error is None:
            return StepStatus.PASSED, None
        category = error.category
        if category == FailureCategory.CANCELLED:
            return StepStatus.CANCELLED, category
        return (StepStatus.FAILED if category.is_test_failure else StepStatus.ERROR), category

    def _crash_check(self) -> str | None:
        try:
            return self.r.session.app_crashed()
        except Exception:
            return None

    # --- evidence ------------------------------------------------------------------------------
    def _collect_evidence(self, record: StepResult, *, failed: bool) -> None:
        guarded = self.guarded if self.driver_usable else None
        prefix = f"step-{record.index:03d}-{record.name.replace(' ', '_').replace('/', '_')}"[:80]
        policy = self.plan.evidence
        want_screenshot = guarded is not None and (
            (failed and policy.screenshots != ScreenshotPolicy.NEVER)
            or policy.screenshots == ScreenshotPolicy.EVERY_STEP
        )
        if want_screenshot and guarded is not None:
            self._add_evidence(record, self._screenshot(guarded, prefix))
        if failed and guarded is not None and policy.hierarchy == "on_failure":
            self._add_evidence(record, self._text_evidence("hierarchy", prefix + "-hierarchy.txt", guarded.hierarchy))
        if guarded is not None and (policy.device_logs == "always" or (failed and policy.device_logs == "on_failure")):
            self._add_evidence(record, self._text_evidence("device_log", prefix + "-device.log", guarded.device_logs))
        if failed:
            try:
                for ref in self.r.session.collect_failure_evidence(self.evidence_dir, prefix):
                    self._redact_file(Path(ref.path) if Path(ref.path).is_absolute() else self.run_dir / ref.path)
                    self._add_evidence(record, ref)
            except Exception as exc:
                record.message = f"{record.message} (evidence collection failed: {exc})"

    def _redact_file(self, path: Path) -> None:
        """Runner-produced logs can contain typed secrets (XCTest logs typed text): redact in place."""
        if path.is_file() and path.suffix in _TEXT_SUFFIXES:
            text = path.read_text(encoding="utf-8", errors="replace")
            redacted = self.redactor.redact_text(text)
            if redacted != text:
                path.write_text(redacted, encoding="utf-8")

    def _scrub_run_dir(self) -> None:
        """Last line of defence: every text file of the run passes through the redactor."""
        for path in self.run_dir.rglob("*"):
            self._redact_file(path)

    def _screenshot(self, driver: ContractCheckedDriver, prefix: str) -> EvidenceRef | None:
        path = self.evidence_dir / f"{prefix}.png"
        try:
            if driver.secret_may_be_visible or not driver.screenshot(path):
                return EvidenceRef(
                    kind="screenshot_suppressed",
                    path="",
                    description="not captured (a secret may be visible or capture failed)",
                )
        except Exception as exc:
            return EvidenceRef(kind="screenshot_failed", path="", description=str(exc))
        return EvidenceRef(kind="screenshot", path=str(path.relative_to(self.run_dir)))

    def _text_evidence(self, kind: str, name: str, producer: Callable[[], str]) -> EvidenceRef | None:
        try:
            text = self.redactor.redact_text(producer())
        except Exception as exc:
            return EvidenceRef(kind=f"{kind}_failed", path="", description=str(exc))
        path = self.evidence_dir / name
        path.write_text(text, encoding="utf-8")
        return EvidenceRef(kind=kind, path=str(path.relative_to(self.run_dir)))

    def _add_evidence(self, record: StepResult, ref: EvidenceRef | None) -> None:
        if ref is None:
            return
        if ref.path and Path(ref.path).is_absolute():
            with contextlib.suppress(ValueError):
                ref = ref.model_copy(update={"path": str(Path(ref.path).relative_to(self.run_dir))})
        ref = ref.model_copy(update={"step_index": record.index})
        record.evidence.append(ref)
        self.result.evidence.append(ref)
        self._publish(EVIDENCE_CAPTURED, index=record.index, kind=ref.kind, path=ref.path)

    # --- verdict -------------------------------------------------------------------------------
    def _decide_status(self) -> None:
        main = [s for s in self.result.steps if s.section != "cleanup"]
        cleanup = [s for s in self.result.steps if s.section == "cleanup"]
        if (
            self.result.status == RunStatus.ERROR
            and self.result.message
            and self.result.message.startswith("preparation failed")
        ):
            for record in self.result.steps:
                if record.status == StepStatus.PENDING:
                    record.status = StepStatus.SKIPPED
                    record.message = "skipped: preparation failed"
            return
        first_bad = next(
            (s for s in main if s.status in (StepStatus.FAILED, StepStatus.ERROR, StepStatus.CANCELLED)), None
        )
        if first_bad is None:
            status, category, message = RunStatus.PASSED, None, None
        else:
            status = {
                StepStatus.FAILED: RunStatus.FAILED,
                StepStatus.ERROR: RunStatus.ERROR,
                StepStatus.CANCELLED: RunStatus.CANCELLED,
            }[first_bad.status]
            where = "precondition" if first_bad.section == "preconditions" else "step"
            category = first_bad.failure_category
            message = f"{where} {first_bad.section}[{first_bad.position}] {first_bad.name}: {first_bad.message}"
        bad_cleanup = [s for s in cleanup if s.status in (StepStatus.FAILED, StepStatus.ERROR)]
        if bad_cleanup:
            note = f"cleanup failed at {bad_cleanup[0].section}[{bad_cleanup[0].position}]: {bad_cleanup[0].message}"
            self.result.warnings.append(note)
            if status == RunStatus.PASSED:  # a dirty environment is reported, never hidden
                status, category, message = RunStatus.ERROR, bad_cleanup[0].failure_category, note
        self.result.status, self.result.failure_category, self.result.message = status, category, message

    def _finish(self, *, force: bool = False) -> RunResult:
        if force:
            # Reached only after an internal error: record where the lifecycle stopped.
            self.result.lifecycle.append(
                StateTransition(
                    state=RunState.REPORTING.value,
                    at=utc_now().isoformat(),
                    note=f"forced from {self.lifecycle.state.value}",
                )
            )
        elif self.lifecycle.state != RunState.REPORTING:
            self.lifecycle.advance(RunState.REPORTING)
        for record in self.result.steps:
            if record.status == StepStatus.PENDING:
                record.status = StepStatus.SKIPPED
                record.message = record.message or "not executed"
        self.result.summary = Summary(
            total=len(self.result.steps),
            passed=sum(s.status == StepStatus.PASSED for s in self.result.steps),
            failed=sum(s.status == StepStatus.FAILED for s in self.result.steps),
            error=sum(s.status == StepStatus.ERROR for s in self.result.steps),
            skipped=sum(s.status == StepStatus.SKIPPED for s in self.result.steps),
            cancelled=sum(s.status == StepStatus.CANCELLED for s in self.result.steps),
            retried=sum(len(s.attempts) > 1 for s in self.result.steps),
        )
        self.result.api_exchanges = self.exchanges
        self.result.finished_at = utc_now().isoformat()
        self.result.duration_ms = round((time.monotonic() - self.started) * 1000, 1)
        if not force:
            self.lifecycle.advance(RunState.FINISHED)
        write_reports(self.result, self.run_dir, self.redactor)
        self._scrub_run_dir()
        self._publish(
            RUN_FINISHED,
            status=self.result.status.value,
            run_dir=str(self.run_dir),
            message=self.result.message,
        )
        return self.result
