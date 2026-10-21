"""AI failure analysis attached to a finished run as an *advisory*.

The advisor reads a redacted summary of `result.json` (never screenshots or raw logs), asks the
provider for an explanation, and stores it in the `advisory` field. It cannot change any status: the
result is re-validated afterwards and every status is compared with the original.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from mtf.core.errors import ConfigurationError, PolicyViolation
from mtf.core.redaction import Redactor
from mtf.planner.prompts import ADVISOR_OUTPUT_SCHEMA, ADVISOR_SYSTEM
from mtf.planner.providers import PlannerProvider
from mtf.reporting.models import Advisory, RunResult
from mtf.reporting.writer import RESULT_FILE, rerender

MAX_EXCHANGES = 6


def failure_summary(result: RunResult) -> dict[str, Any]:
    """The only run data sent to the provider (already redacted when the run was written)."""
    bad = [s for s in result.steps if s.status.value in ("failed", "error", "cancelled")]
    exchange_ids = {i for s in bad for i in s.api_exchange_ids}
    return {
        "plan": result.plan.name,
        "platform": result.platform.value,
        "status": result.status.value,
        "failure_category": result.failure_category.value if result.failure_category else None,
        "message": result.message,
        "steps": [
            {
                "index": s.index,
                "section": s.section,
                "kind": s.kind,
                "name": s.name,
                "target": s.target,
                "params": s.params,
                "status": s.status.value,
                "failure_category": s.failure_category.value if s.failure_category else None,
                "message": s.message,
                "expected": s.expected,
                "actual": s.actual,
                "checks": [c for c in s.checks if not c.get("passed", True)],
            }
            for s in result.steps
        ],
        "failed_api_exchanges": [x for x in result.api_exchanges if x.get("id") in exchange_ids][:MAX_EXCHANGES],
        "validation_issues": result.validation_issues,
        "warnings": result.warnings,
    }


def advise_run(run_dir: Path, provider: PlannerProvider) -> Advisory:
    path = run_dir / RESULT_FILE
    if not path.is_file():
        raise ConfigurationError(f"no {RESULT_FILE} in {run_dir}")
    original = RunResult.model_validate_json(path.read_text(encoding="utf-8"))
    summary = Redactor().redact(failure_summary(original))
    response = provider.complete_json(
        system=ADVISOR_SYSTEM,
        user="Analyse this run:\n" + json.dumps(summary, indent=1, ensure_ascii=False, default=str),
        schema=ADVISOR_OUTPUT_SCHEMA,
        purpose="advise",
    )
    advisory = Advisory(
        provider=getattr(provider, "recorded_from", None) or provider.name,
        model=provider.model,
        generated_at=datetime.now(UTC).replace(microsecond=0).isoformat(),
        summary=str(response.get("summary", "")),
        suspected_causes=[str(c) for c in response.get("suspected_causes", [])],
        suggested_plan_changes=[str(c) for c in response.get("suggested_plan_changes", [])],
    )
    updated = original.model_copy(update={"advisory": advisory})
    _assert_verdict_unchanged(original, updated)
    path.write_text(json.dumps(updated.model_dump(mode="json"), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    rerender(run_dir)
    return advisory


def _assert_verdict_unchanged(before: RunResult, after: RunResult) -> None:
    same = (
        before.status == after.status
        and before.failure_category == after.failure_category
        and [(s.status, s.failure_category) for s in before.steps]
        == [(s.status, s.failure_category) for s in after.steps]
        and before.summary == after.summary
    )
    if not same:  # pragma: no cover - defensive; the advisory only adds a field
        raise PolicyViolation("an advisory must never change a recorded result")
