"""Loading, version handling, serialisation and canonical hashing of plans.

Compatibility rules (see docs/test-plan-dsl.md):

* ``version`` is the DSL *major* version. Additive, optional fields keep the version.
* A plan with a newer version than the framework supports is rejected, never half-understood.
* Older versions are upgraded by a registered migration before validation (none exist yet: 1 is
  the first version).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from mtf.core.errors import PlanLoadError
from mtf.dsl.models import CURRENT_VERSION, STEP_KINDS, SUPPORTED_VERSIONS, TestPlan

Migration = Callable[[dict[str, Any]], dict[str, Any]]
# from_version -> function returning a document of from_version + 1
MIGRATIONS: dict[int, Migration] = {}

MAX_PLAN_BYTES = 512 * 1024


def _format_errors(error: ValidationError) -> list[str]:
    messages = []
    for item in error.errors():
        parts: list[str] = []
        for index, part in enumerate(item["loc"]):
            # After a list index pydantic inserts the union tag (the step kind); it repeats the next key.
            if index > 0 and isinstance(item["loc"][index - 1], int) and part in STEP_KINDS:
                continue
            parts.append(str(part))
        messages.append(f"{'.'.join(parts) or '<plan>'}: {item['msg']}")
    return messages


def migrate(document: dict[str, Any]) -> dict[str, Any]:
    version = document.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        raise PlanLoadError("the plan needs an integer `version` (the DSL major version)")
    if version > CURRENT_VERSION:
        raise PlanLoadError(
            f"plan version {version} is newer than this framework supports "
            f"(supported: {', '.join(map(str, SUPPORTED_VERSIONS))}); upgrade the framework"
        )
    while version < CURRENT_VERSION:
        step = MIGRATIONS.get(version)
        if step is None:
            raise PlanLoadError(f"plan version {version} is no longer supported and has no migration")
        document = step(document)
        version = int(document["version"])
    return document


def parse_plan(document: Any, *, source: str = "<plan>") -> TestPlan:
    if not isinstance(document, dict):
        raise PlanLoadError(f"{source}: a plan must be a mapping at the top level")
    document = migrate(dict(document))
    try:
        return TestPlan.model_validate(document)
    except ValidationError as exc:
        errors = _format_errors(exc)
        raise PlanLoadError(
            f"{source}: invalid plan\n  - " + "\n  - ".join(errors), details={"errors": errors}
        ) from exc


def load_plan_text(text: str, *, source: str = "<plan>") -> TestPlan:
    if len(text.encode("utf-8")) > MAX_PLAN_BYTES:
        raise PlanLoadError(f"{source}: plan is larger than {MAX_PLAN_BYTES // 1024} KiB")
    try:
        document = yaml.safe_load(text)  # JSON is valid YAML; safe_load never constructs objects
    except yaml.YAMLError as exc:
        raise PlanLoadError(f"{source}: not valid YAML/JSON: {exc}") from exc
    return parse_plan(document, source=source)


def load_plan(path: Path) -> TestPlan:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise PlanLoadError(f"cannot read plan {path}: {exc}") from exc
    return load_plan_text(text, source=str(path))


def plan_to_document(plan: TestPlan) -> dict[str, Any]:
    # exclude_unset keeps exactly what the author wrote (including an explicit `equals: null`).
    return plan.model_dump(mode="json", by_alias=True, exclude_unset=True) | {"version": plan.version}


def dump_plan(plan: TestPlan) -> str:
    document = plan_to_document(plan)
    ordered = {k: document[k] for k in _KEY_ORDER if k in document}
    return yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, width=110)


_KEY_ORDER = (
    "version",
    "name",
    "title",
    "description",
    "tags",
    "platforms",
    "metadata",
    "actors",
    "defaults",
    "evidence",
    "preconditions",
    "steps",
    "cleanup",
)


def plan_hash(plan: TestPlan) -> str:
    """SHA-256 of the plan content excluding `metadata.approval`, so
    approving a plan does not change its hash, while editing any step does."""
    document = plan_to_document(plan)
    document["metadata"] = {k: v for k, v in document.get("metadata", {}).items() if k not in ("approval",)}
    canonical = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
