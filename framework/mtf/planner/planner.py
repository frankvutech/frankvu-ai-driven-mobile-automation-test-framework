"""The AI Planner: request -> provider -> candidate plan -> schema + semantic validation (+ repair)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from mtf.contracts.bundle import Contracts
from mtf.contracts.test_data import SecretProvider
from mtf.core.catalog import Catalog
from mtf.core.errors import PlanLoadError, PolicyViolation
from mtf.core.redaction import Redactor
from mtf.core.types import PlanOrigin, Platform
from mtf.dsl.loader import parse_plan, plan_to_document
from mtf.dsl.models import AiProvenance, PlanMetadata, TestPlan
from mtf.dsl.schema import plan_json_schema
from mtf.planner.prompts import PLANNER_OUTPUT_SCHEMA, planner_system_prompt, planner_user_prompt, repair_prompt
from mtf.planner.providers import PlannerProvider
from mtf.validator.policy import SafetyPolicy
from mtf.validator.validator import Issue, PlanValidator, Severity, ValidationReport

MAX_REQUEST_CHARS = 4000


@dataclass(frozen=True)
class PlanningRequest:
    request: str
    platforms: list[Platform]


@dataclass
class PlanningOutcome:
    plan: TestPlan | None
    explanation: str
    assumptions: list[str]
    clarifications: list[str]
    validation: ValidationReport
    attempts: int
    raw_candidates: list[str] = field(default_factory=list)


class AiPlanner:
    def __init__(
        self,
        contracts: Contracts,
        catalog: Catalog,
        provider: PlannerProvider,
        *,
        environ: dict[str, str] | None = None,
    ) -> None:
        self.contracts = contracts
        self.catalog = catalog
        self.provider = provider
        # Candidates are validated like any plan, except that approval cannot exist yet.
        self.validator = PlanValidator(contracts, catalog, SafetyPolicy(require_approval_for_ai_plans=False))
        self.secrets = SecretProvider(contracts.test_data, environ)

    def _guard_request(self, request: PlanningRequest) -> str:
        text = request.request.strip()
        if not text:
            raise PolicyViolation("the planning request is empty")
        if len(text) > MAX_REQUEST_CHARS:
            raise PolicyViolation(f"the planning request is longer than {MAX_REQUEST_CHARS} characters")
        if not request.platforms:
            raise PolicyViolation("name at least one platform")
        if Platform.API in request.platforms and len(request.platforms) > 1:
            raise PolicyViolation("plan API scenarios separately from mobile scenarios")
        # Never send a secret to an AI service: refuse a request that contains a known secret value.
        redactor = Redactor()
        for account in self.contracts.test_data.accounts:
            try:
                redactor.add_secret(self.secrets.resolve(f"accounts.{account}.password"))
            except Exception:  # noqa: S112 - an unset secret cannot leak
                continue
        if redactor.redact_text(text) != text:
            raise PolicyViolation(
                "the request contains a secret (password, token or key); describe it by reference instead"
            )
        return text

    def plan(self, request: PlanningRequest, *, max_repairs: int = 1) -> PlanningOutcome:
        text = self._guard_request(request)
        system = planner_system_prompt(self.contracts, self.catalog, plan_json_schema())
        user = planner_user_prompt(text, request.platforms)
        candidates: list[str] = []
        for attempt in range(1, max_repairs + 2):
            response = self.provider.complete_json(
                system=system, user=user, schema=PLANNER_OUTPUT_SCHEMA, purpose="plan"
            )
            explanation = str(response.get("explanation", ""))
            assumptions = [str(a) for a in response.get("assumptions", [])]
            if response.get("needs_clarification"):
                questions = [str(q) for q in response.get("clarification_questions", [])] or ["(no question given)"]
                return PlanningOutcome(
                    None, explanation, assumptions, questions, ValidationReport("", []), attempt, candidates
                )
            raw = str(response.get("plan_json", ""))
            candidates.append(raw)
            plan, report = self._candidate(raw, text, request.platforms, explanation, assumptions)
            if report.ok or attempt > max_repairs:
                return PlanningOutcome(plan, explanation, assumptions, [], report, attempt, candidates)
            user = repair_prompt(text, request.platforms, raw, [str(i) for i in report.errors])
        raise AssertionError("unreachable")  # pragma: no cover

    def _candidate(
        self, raw: str, request: str, platforms: list[Platform], explanation: str, assumptions: list[str]
    ) -> tuple[TestPlan | None, ValidationReport]:
        try:
            document = json.loads(raw)
        except ValueError as exc:
            return None, _single_error(f"plan_json is not valid JSON: {exc}")
        if not isinstance(document, dict):
            return None, _single_error("plan_json must be a JSON object")
        # Provenance is recorded by the framework, never taken from the model.
        document.pop("metadata", None)
        try:
            plan = parse_plan(document, source="<ai candidate>")
        except PlanLoadError as exc:
            return None, _single_error(exc.message)
        if sorted(plan.platforms) != sorted(platforms):
            return None, _single_error(
                f"platforms must be exactly {[p.value for p in platforms]}, got {[p.value for p in plan.platforms]}"
            )
        provider_name = getattr(self.provider, "recorded_from", None) or self.provider.name
        plan = plan.model_copy(
            update={
                "metadata": PlanMetadata(
                    origin=PlanOrigin.AI_GENERATED,
                    author=f"ai:{provider_name}",
                    created_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
                    ai=AiProvenance(
                        provider=provider_name,
                        model=self.provider.model,
                        request=request,
                        request_sha256=hashlib.sha256(request.encode("utf-8")).hexdigest(),
                        generated_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
                        explanation=explanation,
                        assumptions=assumptions,
                    ),
                )
            }
        )
        # Re-parse so the copy is fully validated and serialises exactly like a loaded plan.
        plan = parse_plan(
            plan_to_document(plan) | {"metadata": plan.metadata.model_dump(mode="json", exclude_none=True)}
        )
        return plan, self.validator.validate(plan)


def _single_error(message: str) -> ValidationReport:
    return ValidationReport("<candidate>", [], [Issue(Severity.ERROR, "schema", "<plan>", message)])
