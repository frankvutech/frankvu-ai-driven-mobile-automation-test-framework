"""Prompt construction for the planner and the advisor.

The system prompt holds only stable, non-secret material: the action catalog, the test-ID registry,
the API operations, the referenceable test data (passwords appear only as `${secret:...}` placeholders)
and the DSL rules. It is deterministic (sorted JSON), so it caches well.
"""

from __future__ import annotations

import json
from typing import Any

from mtf.contracts.bundle import Contracts
from mtf.core.catalog import Catalog
from mtf.core.types import Platform

# The structured-output envelope. The plan travels as a JSON string and is validated by the
# framework's own DSL loader and validator, which are stricter than any output schema.
PLANNER_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "needs_clarification": {"type": "boolean"},
        "clarification_questions": {"type": "array", "items": {"type": "string"}},
        "explanation": {"type": "string"},
        "assumptions": {"type": "array", "items": {"type": "string"}},
        "plan_json": {"type": "string"},
    },
    "required": ["needs_clarification", "clarification_questions", "explanation", "assumptions", "plan_json"],
    "additionalProperties": False,
}

ADVISOR_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "suspected_causes": {"type": "array", "items": {"type": "string"}},
        "suggested_plan_changes": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "suspected_causes", "suggested_plan_changes"],
    "additionalProperties": False,
}

PLANNER_RULES = """\
You write test plans for the Mobile Test Playground app (Android, iOS) and its REST backend, in the
MTF Test Plan DSL version 1. A human reviews and approves every plan before it runs; you never run anything.

Output: fill the response object.
- plan_json: one JSON document that is a complete DSL v1 plan (see "dsl_schema"), or "" when you need clarification.
- explanation: what the plan checks and why each step is there, in a few sentences.
- assumptions: every conservative assumption you made (accounts, products, platform behaviour).
- needs_clarification / clarification_questions: only when information is genuinely missing and no
  conservative assumption is reasonable. Otherwise plan, and list the assumption.

Rules:
1. Use only names that exist in the catalog: tasks, questions, fixtures, interactions. Never invent one.
2. UI targets must be registered test IDs (exact IDs, or a pattern filled with an allowed value) that exist on
   every platform the step runs on (respect "only_on" and "not_addressable_on"). Never use visible text,
   coordinates or XPath.
3. API steps may only call operations listed in "api_operations", with their exact path template; put
   path parameters in path_params and only declared query parameters in query.
4. Test data only by reference: accounts, products and inputs from "test_data", e.g.
   ${accounts.customer.email}, ${products.product_001.price_display}. Passwords only as
   ${secret:accounts.<ref>.password} or ${inputs.wrong_password}.
   Never write a literal password, token or key.
5. Every plan has explicit assertions (assert steps with a matcher) and explicit timeouts where waiting is needed.
   Quote expected UI text as strings.
6. Determinism: preconditions start with the reset_backend_data fixture (when the backend is involved) and,
   for UI plans, launch_app with clean_state true. Clean up what the plan creates (e.g. clear_cart).
7. platforms: exactly the requested platforms. An api plan targets only [api]; UI plans list android and/or ios.
8. Prefer business tasks (login, add_product_to_cart, checkout) over low-level interactions.
9. Declare actors with an account for anything that signs in or calls authenticated endpoints; set defaults.actor.
10. Do not set metadata; the framework records provenance and approval itself.
"""


def _dump(value: Any) -> str:
    return json.dumps(value, indent=1, sort_keys=True, ensure_ascii=False)


def planner_system_prompt(contracts: Contracts, catalog: Catalog, dsl_schema: dict[str, Any]) -> str:
    context = {
        "catalog": catalog.describe(),
        "test_ids": contracts.test_ids.describe_for_planner(),
        "api_operations": contracts.api.describe_for_planner(),
        "test_data": contracts.test_data.describe_for_planner(),
        "dsl_schema": dsl_schema,
    }
    return PLANNER_RULES + "\nReference material (JSON):\n" + _dump(context)


def planner_user_prompt(request: str, platforms: list[Platform]) -> str:
    return (
        f"Requested platforms: {', '.join(p.value for p in platforms)}\n"
        f"Test request:\n<request>\n{request}\n</request>\n"
        "Write the plan."
    )


def repair_prompt(request: str, platforms: list[Platform], previous_plan: str, errors: list[str]) -> str:
    listed = "\n".join(f"- {e}" for e in errors)
    return (
        planner_user_prompt(request, platforms)
        + "\n\nYour previous plan was rejected by the validator. Fix every error and return the full plan again.\n"
        + f"<previous_plan>\n{previous_plan}\n</previous_plan>\n<validation_errors>\n{listed}\n</validation_errors>"
    )


ADVISOR_SYSTEM = """\
You analyse a finished, already-decided test run of the Mobile Test Playground app. Your analysis is
ADVISORY: it is shown next to the recorded result and can never change it. Explain the most likely cause of
each failure from the evidence given (step messages, failure categories, expected/actual values, API
exchanges, validation issues), distinguish product defects from test or environment problems, and suggest
concrete plan changes only if the plan itself looks wrong. Never suggest weakening an assertion just to
make a run pass. Be concise and specific.
"""
