from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from mtf.contracts.bundle import Contracts
from mtf.core.cancellation import CancellationToken
from mtf.core.catalog import Catalog
from mtf.core.config import Environment
from mtf.core.errors import ArtifactMismatch
from mtf.core.events import STEP_FINISHED, Event, EventBus
from mtf.core.types import FailureCategory, Platform, RunStatus, StepStatus
from mtf.dsl import load_plan, load_plan_text
from mtf.orchestrator.lifecycle import IllegalTransition, RunLifecycle, RunState
from mtf.orchestrator.orchestrator import Orchestrator, RunRequest
from mtf.reporting.models import RunResult

from .fakes import PASSWORDS, FakeBackend, FakeSession, World

CART_PLAN = Path(__file__).resolve().parents[2] / "plans/shared/cart_add_product.yaml"


def run(
    contracts: Contracts,
    catalog: Catalog,
    environment: Environment,
    tmp_path: Path,
    plan_text_or_path: str | Path,
    platform: Platform,
    *,
    world: World | None = None,
    session: FakeSession | None = None,
    backend: FakeBackend | None = None,
    environ: dict[str, str] | None = None,
    **request_args: object,
) -> tuple[RunResult, FakeSession, FakeBackend]:
    world = world or World()
    plan = load_plan(plan_text_or_path) if isinstance(plan_text_or_path, Path) else load_plan_text(plan_text_or_path)
    session = session or FakeSession(platform, world)
    backend = backend or FakeBackend(world)
    result = Orchestrator(contracts, catalog).run(
        RunRequest(
            plan=plan,
            platform=platform,
            environment=environment,
            session=session,
            reports_dir=tmp_path,
            environ=dict(PASSWORDS) if environ is None else environ,
            transport=backend,
            **request_args,  # type: ignore[arg-type]
        )
    )
    return result, session, backend


@pytest.mark.parametrize("platform", [Platform.ANDROID, Platform.IOS])
def test_shared_plan_passes_on_both_platforms(contracts, catalog, environment, tmp_path, platform) -> None:
    result, session, backend = run(contracts, catalog, environment, tmp_path, CART_PLAN, platform)
    assert result.status == RunStatus.PASSED, result.message
    assert result.summary.passed == result.summary.total == 10
    assert session.prepared
    assert session.closed
    assert backend.world.resets == 1
    assert [t.state for t in result.lifecycle] == [
        "created",
        "validating",
        "preparing",
        "setting_up",
        "executing",
        "cleaning_up",
        "reporting",
        "finished",
    ]
    run_dir = tmp_path / "runs" / result.run_id
    assert (run_dir / "result.json").is_file()
    assert (run_dir / "report.html").is_file()
    assert (run_dir / "events.jsonl").is_file()
    assert result.artifact is not None
    assert result.artifact.installed_verified


def test_failed_assertion_skips_the_rest_and_still_cleans_up(contracts, catalog, environment, tmp_path) -> None:
    text = CART_PLAN.read_text().replace(
        "expect: {equals: 1}\n  - assert:\n      question: cart_total",
        "expect: {equals: 3}\n  - assert:\n      question: cart_total",
        1,
    )
    result, _, _backend = run(contracts, catalog, environment, tmp_path, text, Platform.ANDROID)
    assert result.status == RunStatus.FAILED
    assert result.failure_category == FailureCategory.ASSERTION_FAILED
    statuses = [s.status for s in result.steps]
    failed_at = statuses.index(StepStatus.FAILED)
    assert result.steps[failed_at].expected == 3
    assert result.steps[failed_at].actual == 1
    assert all(s == StepStatus.SKIPPED for s in statuses[failed_at + 1 : -1])
    assert statuses[-1] == StepStatus.PASSED  # cleanup ran
    kinds = {e.kind for e in result.steps[failed_at].evidence}
    assert {"screenshot", "hierarchy", "device_log"} <= kinds


def test_invalid_plan_is_rejected_before_anything_is_launched(contracts, catalog, environment, tmp_path) -> None:
    text = CART_PLAN.read_text().replace("task: open_cart", "task: open_cart_now")
    result, session, backend = run(contracts, catalog, environment, tmp_path, text, Platform.ANDROID)
    assert result.status == RunStatus.REJECTED
    assert not session.prepared
    assert backend.requests == []
    assert any(i["code"] == "unknown_task" for i in result.validation_issues)
    assert all(s.status == StepStatus.SKIPPED for s in result.steps) or result.steps == []


def test_missing_secrets_reject_the_run(contracts, catalog, environment, tmp_path) -> None:
    result, session, _ = run(contracts, catalog, environment, tmp_path, CART_PLAN, Platform.ANDROID, environ={})
    assert result.status == RunStatus.REJECTED
    assert "MTF_PASSWORD_CUSTOMER" in (result.message or "")
    assert not session.prepared


def test_preparation_failure_is_an_error_not_a_failure(contracts, catalog, environment, tmp_path) -> None:
    world = World()
    session = FakeSession(Platform.ANDROID, world, fail_prepare=ArtifactMismatch("installed version differs"))
    result, _, backend = run(
        contracts, catalog, environment, tmp_path, CART_PLAN, Platform.ANDROID, world=world, session=session
    )
    assert result.status == RunStatus.ERROR
    assert result.failure_category == FailureCategory.ARTIFACT_MISMATCH
    assert backend.requests == []
    assert all(s.status == StepStatus.SKIPPED for s in result.steps)


def test_precondition_failure_skips_steps(contracts, catalog, environment, tmp_path) -> None:
    world = World()
    backend = FakeBackend(world)
    backend.fail_reset = True
    result, _, _ = run(
        contracts, catalog, environment, tmp_path, CART_PLAN, Platform.ANDROID, world=world, backend=backend
    )
    assert result.status == RunStatus.ERROR
    assert result.message
    assert result.message.startswith("precondition")
    assert all(s.status == StepStatus.SKIPPED for s in result.steps if s.section == "steps")


API_PLAN = """
version: 1
name: api_unit
platforms: [api]
actors: {customer: {account: customer}}
steps:
  - api: {method: GET, path: /health, expect: {status: 200, json: [{path: status, equals: ok}]}}
  - api:
      method: GET
      path: /api/v1/cart
      as: customer
      expect: {status: 200, json: [{path: totalCents, equals: 0}]}
      save: {total: totalCents}
  - api: {method: GET, path: /api/v1/test/server-error, expect: {status: 500, json: [{path: error.code, equals: INTERNAL_ERROR}]}}
"""


def test_api_plan_runs_without_a_device(contracts, catalog, environment, tmp_path) -> None:
    result, session, _ = run(contracts, catalog, environment, tmp_path, API_PLAN, Platform.API)
    assert result.status == RunStatus.PASSED, result.message
    assert session.driver is None
    assert result.artifact is None
    assert [e["operation"] for e in result.api_exchanges][:2] == ["GET /health", "POST /api/v1/auth/login"]
    assert result.steps[1].api_exchange_ids  # correlated with the step
    assert {c["kind"] for c in result.steps[1].checks} == {"status", "schema", "json"}


def test_wrong_status_is_an_api_assertion_failure(contracts, catalog, environment, tmp_path) -> None:
    result, _, _ = run(
        contracts,
        catalog,
        environment,
        tmp_path,
        API_PLAN.replace("equals: INTERNAL_ERROR", "equals: OTHER_CODE"),
        Platform.API,
    )
    assert result.status == RunStatus.FAILED
    assert result.failure_category == FailureCategory.API_ASSERTION_FAILED
    assert any(not c["passed"] for c in result.steps[2].checks)


def test_cancellation_stops_the_run(contracts, catalog, environment, tmp_path) -> None:
    token = CancellationToken()
    bus = EventBus()

    def cancel_after_first_step(event: Event) -> None:
        if event.type == STEP_FINISHED and event.payload.get("index") == 0:
            token.cancel("test")

    bus.subscribe(cancel_after_first_step)
    result, _, _ = run(
        contracts, catalog, environment, tmp_path, CART_PLAN, Platform.ANDROID, cancellation=token, events=bus
    )
    assert result.status == RunStatus.CANCELLED
    assert StepStatus.CANCELLED in {s.status for s in result.steps}


def test_step_watchdog_times_out_a_hung_step(contracts, catalog, environment, tmp_path) -> None:
    world = World()
    session = FakeSession(Platform.ANDROID, world)
    assert session.driver is not None
    session.driver.hang_on = "login_button"
    result, _, _ = run(
        contracts,
        catalog,
        environment,
        tmp_path,
        CART_PLAN,
        Platform.ANDROID,
        world=world,
        session=session,
        max_step_duration_s=0.5,
    )
    assert result.status == RunStatus.FAILED
    assert result.failure_category == FailureCategory.TIMEOUT
    assert session.driver.closed  # the runner is abandoned, later UI steps are skipped


def test_retries_are_recorded_not_hidden(contracts, catalog, environment, tmp_path) -> None:
    text = """
version: 1
name: retry_case
platforms: [android]
preconditions:
  - fixture: launch_app
steps:
  - wait: {target: home_screen, timeout_ms: 200}
    retry: {max_attempts: 3, delay_ms: 0, on_failure: [element_not_found]}
"""
    result, _, _ = run(contracts, catalog, environment, tmp_path, text, Platform.ANDROID)
    step = result.steps[1]
    assert step.status == StepStatus.FAILED
    assert len(step.attempts) == 3
    assert result.summary.retried == 1


def test_cleanup_failure_turns_a_pass_into_an_error(contracts, catalog, environment, tmp_path) -> None:
    text = (
        API_PLAN
        + "cleanup:\n  - api: {method: GET, path: /health, expect: {status: 200, json: [{path: status, equals: down}]}}\n"
    )
    result, _, _ = run(contracts, catalog, environment, tmp_path, text, Platform.API)
    assert result.status == RunStatus.ERROR
    assert result.warnings
    assert "cleanup failed" in result.warnings[0]


def test_reports_never_contain_secrets(contracts, catalog, environment, tmp_path) -> None:
    result, _, backend = run(contracts, catalog, environment, tmp_path, CART_PLAN, Platform.IOS)
    run_dir = tmp_path / "runs" / result.run_id
    secrets = [*PASSWORDS.values(), *backend.world.tokens]
    assert secrets
    for path in run_dir.rglob("*"):
        if path.is_file() and path.suffix in (".json", ".jsonl", ".html", ".txt", ".log"):
            content = path.read_text(encoding="utf-8")
            assert not any(s in content for s in secrets), path
    document = json.loads((run_dir / "result.json").read_text())
    assert RunResult.model_validate(document).status == RunStatus.PASSED


def test_lifecycle_rejects_illegal_transitions() -> None:
    lifecycle = RunLifecycle()
    with pytest.raises(IllegalTransition):
        lifecycle.advance(RunState.EXECUTING)
    lifecycle.advance(RunState.VALIDATING)
    lifecycle.advance(RunState.REJECTED)
    with pytest.raises(IllegalTransition):
        lifecycle.advance(RunState.EXECUTING)


def test_failing_event_subscribers_cannot_break_a_run(contracts, catalog, environment, tmp_path) -> None:
    bus = EventBus()

    def broken(event: Event) -> None:
        raise RuntimeError("observer bug")

    bus.subscribe(broken)
    result, _, _ = run(contracts, catalog, environment, tmp_path, API_PLAN, Platform.API, events=bus)
    assert result.status == RunStatus.PASSED


def test_runs_are_serial_and_independent(contracts, catalog, environment, tmp_path) -> None:
    world = World()
    results = [
        run(contracts, catalog, environment, tmp_path, CART_PLAN, Platform.ANDROID, world=world)[0] for _ in range(2)
    ]
    assert [r.status for r in results] == [RunStatus.PASSED, RunStatus.PASSED]
    assert len({r.run_id for r in results}) == 2
    assert threading.active_count() < 20


def test_runner_logs_with_typed_secrets_are_redacted(contracts, catalog, environment, tmp_path) -> None:
    text = CART_PLAN.read_text().replace("expect: {equals: 1}", "expect: {equals: 9}", 1)
    result, _, _ = run(contracts, catalog, environment, tmp_path, text, Platform.IOS)
    assert result.status == RunStatus.FAILED
    run_dir = tmp_path / "runs" / result.run_id
    logs = list(run_dir.rglob("*-runner.log"))
    assert logs
    content = logs[0].read_text()
    assert PASSWORDS["MTF_PASSWORD_CUSTOMER"] not in content
    assert "«redacted»" in content


def test_a_session_that_failed_to_prepare_is_closed_even_when_reuse_is_requested(
    contracts, catalog, environment, tmp_path
) -> None:
    world = World()
    session = FakeSession(Platform.ANDROID, world, fail_prepare=ArtifactMismatch("wrong version"))
    result, _, _ = run(
        contracts,
        catalog,
        environment,
        tmp_path,
        CART_PLAN,
        Platform.ANDROID,
        world=world,
        session=session,
        close_session=False,
    )
    assert result.status == RunStatus.ERROR
    assert session.closed


def test_a_healthy_session_is_kept_for_reuse(contracts, catalog, environment, tmp_path) -> None:
    result, session, _ = run(
        contracts, catalog, environment, tmp_path, CART_PLAN, Platform.ANDROID, close_session=False
    )
    assert result.status == RunStatus.PASSED
    assert not session.closed
