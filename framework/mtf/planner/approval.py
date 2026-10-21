"""Human approval of plans. An approval is bound to the plan's canonical content hash: editing any step
after approval invalidates it, and the validator refuses to run an AI-generated plan without a valid one."""

from __future__ import annotations

from datetime import UTC, datetime

from mtf.contracts.bundle import Contracts
from mtf.core.catalog import Catalog
from mtf.core.errors import PolicyViolation
from mtf.dsl.loader import plan_hash
from mtf.dsl.models import Approval, TestPlan
from mtf.validator.policy import SafetyPolicy
from mtf.validator.validator import PlanValidator


def approve(
    plan: TestPlan, *, approver: str, contracts: Contracts, catalog: Catalog, note: str | None = None
) -> TestPlan:
    approver = approver.strip()
    if not approver or approver.lower().startswith("ai"):
        raise PolicyViolation("an approval needs a human approver name")
    report = PlanValidator(contracts, catalog, SafetyPolicy(require_approval_for_ai_plans=False)).validate(plan)
    if not report.ok:
        details = "\n  ".join(str(i) for i in report.errors)
        raise PolicyViolation(f"a plan with validation errors cannot be approved:\n  {details}")
    unapproved = plan.model_copy(update={"metadata": plan.metadata.model_copy(update={"approval": None})})
    approval = Approval(
        approved_by=approver,
        approved_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        plan_sha256=plan_hash(unapproved),
        note=note,
    )
    metadata = plan.metadata.model_copy(update={"approval": approval})
    return plan.model_copy(update={"metadata": metadata})
