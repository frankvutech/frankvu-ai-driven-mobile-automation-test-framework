from __future__ import annotations

from pathlib import Path

import pytest

from mtf.contracts.bundle import Contracts
from mtf.core.catalog import Catalog
from mtf.core.types import Platform
from mtf.dsl import load_plan, load_plan_text
from mtf.dsl.loader import plan_hash
from mtf.validator import PlanValidator, SafetyPolicy

UI_HEADER = """
version: 1
name: ui_case
platforms: [android, ios]
actors: {customer: {account: customer}}
defaults: {actor: customer}
preconditions:
  - fixture: launch_app
"""
API_HEADER = """
version: 1
name: api_case
platforms: [api]
actors: {customer: {account: customer}, admin: {account: admin}}
"""


def codes(
    contracts: Contracts, catalog: Catalog, text: str, policy: SafetyPolicy | None = None, platforms=None
) -> set[str]:
    report = PlanValidator(contracts, catalog, policy).validate(load_plan_text(text), platforms)
    return {i.code for i in report.errors}


@pytest.mark.parametrize(
    "path", sorted(Path(__file__).resolve().parents[2].joinpath("plans").rglob("*.yaml")), ids=lambda p: p.name
)
def test_repository_plans_are_valid(contracts: Contracts, catalog: Catalog, path: Path) -> None:
    plan = load_plan(path)
    report = PlanValidator(contracts, catalog).validate(plan)
    assert report.ok, [str(i) for i in report.errors]


def test_valid_ui_plan(contracts: Contracts, catalog: Catalog) -> None:
    text = UI_HEADER + "steps:\n  - task: login\n  - assert: {question: cart_total, expect: {equals: '7.49'}}\n"
    assert codes(contracts, catalog, text) == set()


@pytest.mark.parametrize(
    ("steps", "code"),
    [
        ("  - task: fly_to_moon\n", "unknown_task"),
        ("  - assert: {question: is_happy, expect: {equals: true}}\n", "unknown_question"),
        ("  - interaction: tap\n    target: invented_button\n", "selector_contract_violation"),
        ("  - interaction: tap\n    target: cart_item_product_999\n", "selector_contract_violation"),
        ("  - wait: {target: does_not_exist, timeout_ms: 1000}\n", "selector_contract_violation"),
        ("  - interaction: tap\n", "missing_target"),
        ("  - interaction: back\n    target: login_button\n", "unexpected_target"),
        ("  - task: add_product_to_cart\n    with: {product: product_999}\n", "bad_param"),
        ("  - task: add_product_to_cart\n    with: {product: product_001, quantity: 9}\n", "bad_param"),
        ("  - task: add_product_to_cart\n    with: {product: product_001, colour: red}\n", "unknown_param"),
        ("  - task: add_product_to_cart\n", "missing_param"),
        ("  - task: submit_login\n    with: {email: a@b.c, password: hunter2hunter2}\n", "bad_param"),
        (
            "  - interaction: type_text\n    target: login_password\n    with: {text: literal-password}\n",
            "secret_literal",
        ),
        ("  - task: open_scenario\n    with: {scenario: teleport}\n", "bad_param"),
        ("  - task: open_deep_link\n    with: {link: https_evil}\n", "bad_param"),
        ("  - task: login\n    actor: stranger\n", "unknown_actor"),
        ("  - assert: {question: cart_total, expect: {equals: '${vars.never_saved}'}}\n", "undefined_variable"),
        ("  - assert: {question: cart_total, expect: {equals: '${products.product_999.name}'}}\n", "unknown_reference"),
        ("  - assert: {question: cart_total, expect: {equals: '${accounts.customer.password}'}}\n", "secret_literal"),
        ("  - assert: {question: cart_is_empty, expect: {contains: x}}\n", "bad_matcher"),
        ("  - api: {method: GET, path: /api/v1/nope, expect: {status: 200}}\n", "unknown_operation"),
    ],
)
def test_ui_plan_errors(contracts: Contracts, catalog: Catalog, steps: str, code: str) -> None:
    assert code in codes(contracts, catalog, UI_HEADER + "steps:\n" + steps)


def test_platform_specific_ids_are_checked_per_platform(contracts: Contracts, catalog: Catalog) -> None:
    text = UI_HEADER + "steps:\n  - interaction: tap\n    target: top_bar_back\n"
    assert "selector_contract_violation" in codes(contracts, catalog, text)
    android_only = text + "    platforms: [android]\n"
    assert codes(contracts, catalog, android_only) == set()


def test_ui_steps_before_launch_are_rejected(contracts: Contracts, catalog: Catalog) -> None:
    text = "version: 1\nname: nolaunch\nplatforms: [android]\nsteps:\n  - interaction: tap\n    target: login_button\n"
    assert "ui_before_launch" in codes(contracts, catalog, text)


def test_cleanup_cannot_assert(contracts: Contracts, catalog: Catalog) -> None:
    text = UI_HEADER + "steps:\n  - task: login\ncleanup:\n  - assert: {question: cart_total, expect: {equals: '1'}}\n"
    assert "assert_in_cleanup" in codes(contracts, catalog, text)


def test_api_checks(contracts: Contracts, catalog: Catalog) -> None:
    def api(spec: str) -> set[str]:
        return codes(contracts, catalog, API_HEADER + "steps:\n  - api: " + spec + "\n")

    assert api("{method: GET, path: '/api/v1/products/{id}', expect: {status: 200}}") == {"missing_param"}
    assert api(
        "{method: GET, path: '/api/v1/products/{id}', path_params: {id: x, extra: y}, expect: {status: 200}}"
    ) == {"unknown_param"}
    assert api("{method: GET, path: /api/v1/products, query: {colour: red}, expect: {status: 200}}") == {
        "unknown_param"
    }
    assert api("{method: GET, path: /api/v1/products, expect: {status: 418}}") == {"undeclared_status"}
    assert api("{method: GET, path: /health, body: {a: 1}, expect: {status: 200}}") == {"unexpected_body"}
    assert api("{method: GET, path: /api/v1/cart, expect: {status: 200}}") == {"auth_expectation"}
    assert api("{method: GET, path: /api/v1/cart, token: none, expect: {status: 401}}") == set()
    assert api("{method: GET, path: /api/v1/admin/orders, as: customer, expect: {status: 200}}") == {"auth_expectation"}
    assert api("{method: GET, path: /api/v1/admin/orders, as: admin, expect: {status: 200}}") == set()
    assert api("{method: GET, path: /api/v1/cart, as: nobody, expect: {status: 200}}") >= {"unknown_actor"}


def test_ui_ids_are_not_allowed_in_api_plans(contracts: Contracts, catalog: Catalog) -> None:
    text = API_HEADER + "steps:\n  - assert: {question: cart_total, expect: {equals: '1'}}\n"
    assert "unsupported_platform" in codes(contracts, catalog, text)


def test_literal_secrets_are_rejected(contracts: Contracts, catalog: Catalog) -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJlLXZhbHVl"
    text = (
        API_HEADER
        + f"steps:\n  - api: {{method: POST, path: /api/v1/auth/login, body: {{email: a, password: '{jwt}'}}, expect: {{status: 401}}}}\n"
    )
    assert "secret_literal" in codes(contracts, catalog, text)
    known = (
        API_HEADER
        + "steps:\n  - api: {method: POST, path: /api/v1/auth/login, body: {email: a, password: Known-Secret-1}, expect: {status: 401}}\n"
    )
    report = PlanValidator(contracts, catalog).validate(load_plan_text(known), known_secret_values=["Known-Secret-1"])
    assert "secret_literal" in {i.code for i in report.errors}
    described = (
        API_HEADER
        + "steps:\n  - api: {method: GET, path: /health, expect: {status: 200}}\n    description: no bearer token needed\n"
    )
    assert codes(contracts, catalog, described) == set()


def test_policy_bounds(contracts: Contracts, catalog: Catalog) -> None:
    strict = SafetyPolicy(max_step_timeout_ms=1000, max_wait_timeout_ms=1000, max_retry_attempts=1)
    text = UI_HEADER + (
        "steps:\n  - task: login\n    timeout_ms: 5000\n"
        "  - wait: {target: home_screen, timeout_ms: 5000}\n"
        "  - task: open_cart\n    retry: {max_attempts: 2}\n"
    )
    assert {"timeout_bounds", "retry_bounds"} <= codes(contracts, catalog, text, strict)
    assertion_retry = (
        UI_HEADER + "steps:\n  - task: open_cart\n    retry: {max_attempts: 2, on_failure: [assertion_failed]}\n"
    )
    assert "retry_policy" in codes(contracts, catalog, assertion_retry)
    assert "retry_policy" not in codes(contracts, catalog, assertion_retry, SafetyPolicy(allow_assertion_retries=True))


def test_test_support_follows_the_environment(contracts: Contracts, catalog: Catalog) -> None:
    text = (
        API_HEADER
        + "preconditions:\n  - fixture: reset_backend_data\nsteps:\n  - api: {method: GET, path: /api/v1/test/server-error, expect: {status: 500}}\n"
    )
    assert codes(contracts, catalog, text) == set()
    assert {"test_support_disabled"} == codes(contracts, catalog, text, SafetyPolicy(allow_test_support=False))


def test_platform_selection(contracts: Contracts, catalog: Catalog) -> None:
    text = UI_HEADER + "steps:\n  - task: login\n"
    assert "unsupported_platform" in codes(contracts, catalog, text, platforms=[Platform.API])


AI_PLAN = (
    API_HEADER
    + """
metadata:
  origin: ai_generated
  ai: {provider: recorded, request: check health, request_sha256: x, generated_at: now}
steps:
  - api: {method: GET, path: /health, expect: {status: 200}}
"""
)


def test_ai_plans_need_a_current_approval(contracts: Contracts, catalog: Catalog) -> None:
    assert "approval_required" in codes(contracts, catalog, AI_PLAN)
    plan = load_plan_text(AI_PLAN)
    approved = AI_PLAN.replace(
        "metadata:\n",
        f"metadata:\n  approval: {{approved_by: Reviewer, approved_at: now, plan_sha256: {plan_hash(plan)}}}\n",
    )
    assert codes(contracts, catalog, approved) == set()
    edited = approved.replace("status: 200", "status: 503")
    assert "approval_stale" in codes(contracts, catalog, edited)
