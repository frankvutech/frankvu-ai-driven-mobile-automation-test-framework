"""Exports the JSON Schema of the DSL to `contracts/test-plan.schema.json`.

The pydantic models are the single source of truth; the committed schema is generated from them and
`mtf contracts check` fails when it is stale. Editors, the AI planner and external tools use the
JSON Schema; the framework itself validates with the models (which add cross-field rules).
"""

from __future__ import annotations

import json
from typing import Any

from mtf.dsl.models import CURRENT_VERSION, TestPlan

SCHEMA_ID = "https://mtf.local/schemas/test-plan.v1.schema.json"


def plan_json_schema() -> dict[str, Any]:
    schema = TestPlan.model_json_schema(by_alias=True, mode="validation")
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": SCHEMA_ID,
        "title": f"MTF Test Plan (DSL v{CURRENT_VERSION})",
        **schema,
    }


def render_schema() -> str:
    return json.dumps(plan_json_schema(), indent=2, sort_keys=False) + "\n"
