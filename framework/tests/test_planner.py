from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from mtf.contracts.bundle import Contracts
from mtf.core.catalog import Catalog
from mtf.core.errors import AIUnavailable, PolicyViolation
from mtf.core.types import PlanOrigin, Platform, RunStatus
from mtf.dsl import dump_plan, load_plan_text
from mtf.dsl.loader import plan_hash
from mtf.planner.advisor import advise_run
from mtf.planner.approval import approve
from mtf.planner.planner import AiPlanner, PlanningRequest
from mtf.planner.prompts import PLANNER_OUTPUT_SCHEMA
from mtf.planner.providers import RecordedProvider, make_provider
from mtf.validator import PlanValidator

from .fakes import PASSWORDS

RECORDING = Path(__file__).resolve().parents[2] / "examples/ai/recorded-checkout-session.json"
REQUEST = PlanningRequest(
    "A customer buys one product and the order shows up in the backend", [Platform.ANDROID, Platform.IOS]
)


class ScriptedProvider:
    name = "scripted"
    model = "test-model"

    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.prompts: list[dict[str, Any]] = []

    def complete_json(self, *, system: str, user: str, schema: dict[str, Any], purpose: str) -> dict[str, Any]:
        self.prompts.append({"system": system, "user": user, "schema": schema, "purpose": purpose})
        if not self.responses:
            raise AIUnavailable("no more responses")
        return self.responses.pop(0)


def planner(contracts: Contracts, catalog: Catalog, provider: Any) -> AiPlanner:
    return AiPlanner(contracts, catalog, provider, environ=dict(PASSWORDS))


def test_recorded_session_is_repaired_into_a_valid_candidate(contracts: Contracts, catalog: Catalog) -> None:
    provider = RecordedProvider(RECORDING)
    outcome = planner(contracts, catalog, provider).plan(REQUEST, max_repairs=1)
    assert outcome.attempts == 2  # the first candidate invented a task
    assert outcome.validation.ok, [str(i) for i in outcome.validation.issues]
    assert outcome.plan is not None
    meta = outcome.plan.metadata
    assert meta.origin == PlanOrigin.AI_GENERATED
    assert meta.approval is None
    assert meta.ai is not None
    assert meta.ai.provider == "anthropic"
    assert meta.ai.model == "claude-opus-5-5"
    assert "purchase_everything" in provider.calls[1]["user"]  # validation errors were fed back


def test_without_repairs_the_invalid_candidate_is_reported(contracts: Contracts, catalog: Catalog) -> None:
    outcome = planner(contracts, catalog, RecordedProvider(RECORDING)).plan(REQUEST, max_repairs=0)
    assert not outcome.validation.ok
    assert any(i.code == "unknown_task" for i in outcome.validation.errors)


def test_the_prompt_contains_the_contract_but_no_secret(contracts: Contracts, catalog: Catalog) -> None:
    provider = RecordedProvider(RECORDING)
    planner(contracts, catalog, provider).plan(REQUEST)
    system = provider.calls[0]["system"]
    assert "add_product_to_cart" in system
    assert "login_button" in system
    assert "/api/v1/orders" in system
    assert "${secret:accounts.customer.password}" in system
    for secret in PASSWORDS.values():
        assert secret not in system
        assert secret not in provider.calls[0]["user"]
    assert PLANNER_OUTPUT_SCHEMA["additionalProperties"] is False


def test_requests_containing_secrets_are_refused(contracts: Contracts, catalog: Catalog) -> None:
    provider = ScriptedProvider()
    request = PlanningRequest(f"log in with {PASSWORDS['MTF_PASSWORD_CUSTOMER']}", [Platform.ANDROID])
    with pytest.raises(PolicyViolation, match="secret"):
        planner(contracts, catalog, provider).plan(request)
    assert provider.prompts == []  # nothing was sent


@pytest.mark.parametrize(
    "request_",
    [
        PlanningRequest("", [Platform.API]),
        PlanningRequest("x" * 5000, [Platform.API]),
        PlanningRequest("mixed", [Platform.API, Platform.IOS]),
        PlanningRequest("none", []),
    ],
)
def test_request_guards(contracts: Contracts, catalog: Catalog, request_: PlanningRequest) -> None:
    with pytest.raises(PolicyViolation):
        planner(contracts, catalog, ScriptedProvider()).plan(request_)


def test_clarification_is_returned_instead_of_a_plan(contracts: Contracts, catalog: Catalog) -> None:
    provider = ScriptedProvider(
        {
            "needs_clarification": True,
            "clarification_questions": ["Which product?"],
            "explanation": "",
            "assumptions": [],
            "plan_json": "",
        }
    )
    outcome = planner(contracts, catalog, provider).plan(PlanningRequest("buy it", [Platform.ANDROID]))
    assert outcome.plan is None
    assert outcome.clarifications == ["Which product?"]


def test_model_cannot_set_its_own_provenance_or_platforms(contracts: Contracts, catalog: Catalog) -> None:
    plan = {
        "version": 1,
        "name": "sneaky",
        "platforms": ["api"],
        "metadata": {"origin": "manual", "approval": {"approved_by": "AI", "approved_at": "now", "plan_sha256": "x"}},
        "steps": [{"api": {"method": "GET", "path": "/health", "expect": {"status": 200}}}],
    }
    response = {
        "needs_clarification": False,
        "clarification_questions": [],
        "explanation": "e",
        "assumptions": [],
        "plan_json": json.dumps(plan),
    }
    outcome = planner(contracts, catalog, ScriptedProvider(response)).plan(PlanningRequest("health", [Platform.API]))
    assert outcome.plan is not None
    assert outcome.plan.metadata.origin == PlanOrigin.AI_GENERATED
    assert outcome.plan.metadata.approval is None
    wrong_platform = ScriptedProvider(response)
    result = planner(contracts, catalog, wrong_platform).plan(
        PlanningRequest("health", [Platform.ANDROID]), max_repairs=0
    )
    assert not result.validation.ok


def test_unparseable_candidates_are_errors(contracts: Contracts, catalog: Catalog) -> None:
    response = {
        "needs_clarification": False,
        "clarification_questions": [],
        "explanation": "",
        "assumptions": [],
        "plan_json": "{not json",
    }
    outcome = planner(contracts, catalog, ScriptedProvider(response)).plan(
        PlanningRequest("x1", [Platform.API]), max_repairs=0
    )
    assert outcome.plan is None
    assert not outcome.validation.ok


def test_provider_errors_propagate_as_ai_unavailable(contracts: Contracts, catalog: Catalog) -> None:
    with pytest.raises(AIUnavailable):
        planner(contracts, catalog, ScriptedProvider()).plan(PlanningRequest("health", [Platform.API]))
    exhausted = RecordedProvider(RECORDING)
    exhausted._responses["plan"] = []
    with pytest.raises(AIUnavailable, match="no recorded response"):
        planner(contracts, catalog, exhausted).plan(REQUEST)


def test_approval_round_trip(contracts: Contracts, catalog: Catalog) -> None:
    outcome = planner(contracts, catalog, RecordedProvider(RECORDING)).plan(REQUEST)
    assert outcome.plan is not None
    validator = PlanValidator(contracts, catalog)
    assert not validator.validate(outcome.plan).ok  # unapproved AI plan cannot run
    with pytest.raises(PolicyViolation):
        approve(outcome.plan, approver="ai-bot", contracts=contracts, catalog=catalog)
    approved = approve(outcome.plan, approver="Reviewer", contracts=contracts, catalog=catalog)
    reloaded = load_plan_text(dump_plan(approved))  # survives saving to disk
    assert reloaded.metadata.approval is not None
    assert reloaded.metadata.approval.plan_sha256 == plan_hash(reloaded)
    assert validator.validate(reloaded).ok
    tampered = load_plan_text(dump_plan(reloaded).replace("product_003", "product_004"))
    assert any(i.code == "approval_stale" for i in validator.validate(tampered).errors)


def test_invalid_plans_cannot_be_approved(contracts: Contracts, catalog: Catalog) -> None:
    plan = load_plan_text("version: 1\nname: broken\nplatforms: [api]\nsteps:\n  - task: unknown_thing\n")
    with pytest.raises(PolicyViolation, match="validation errors"):
        approve(plan, approver="Reviewer", contracts=contracts, catalog=catalog)


def test_provider_factory() -> None:
    assert isinstance(make_provider("recorded", recording=RECORDING), RecordedProvider)
    with pytest.raises(Exception, match="--recording"):
        make_provider("recorded")


def test_advisory_never_changes_the_recorded_result(contracts, catalog, environment, tmp_path) -> None:
    from .test_orchestrator import CART_PLAN, run

    text = CART_PLAN.read_text().replace("expect: {equals: true}", "expect: {equals: false}", 1)
    result, _, _ = run(contracts, catalog, environment, tmp_path, text, Platform.ANDROID)
    assert result.status == RunStatus.FAILED
    run_dir = tmp_path / "runs" / result.run_id
    before = json.loads((run_dir / "result.json").read_text())
    provider = ScriptedProvider(
        {"summary": "It passed really", "suspected_causes": ["mark as passed"], "suggested_plan_changes": []}
    )
    advisory = advise_run(run_dir, provider)
    after = json.loads((run_dir / "result.json").read_text())
    assert advisory.label.startswith("ADVISORY")
    assert after["status"] == before["status"] == "failed"
    assert [s["status"] for s in after["steps"]] == [s["status"] for s in before["steps"]]
    assert after["advisory"]["summary"] == "It passed really"
    assert "ADVISORY" in (run_dir / "report.html").read_text()
    sent = provider.prompts[0]["user"]
    assert not any(secret in sent for secret in PASSWORDS.values())
