from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator

from mtf.core.errors import PlanLoadError
from mtf.dsl import dump_plan, load_plan, load_plan_text
from mtf.dsl.loader import MIGRATIONS, plan_hash
from mtf.dsl.models import ApiStep, AssertStep, FixtureStep, InteractionName, InteractionStep, TaskStep, WaitStep
from mtf.dsl.schema import render_schema

MINIMAL = """
version: 1
name: minimal_plan
platforms: [api]
steps:
  - api: {method: GET, path: /health, expect: {status: 200}}
"""


def test_parses_every_step_kind() -> None:
    plan = load_plan_text(
        """
version: 1
name: all_kinds
platforms: [android, ios]
actors: {customer: {account: customer}}
preconditions:
  - fixture: launch_app
steps:
  - task: login
    actor: customer
  - interaction: tap
    target: login_button
  - wait: {target: home_screen, timeout_ms: 2000}
  - assert: {question: cart_total, expect: {equals: "7.49"}}
  - api: {method: GET, path: /health, expect: {status: 200}}
  - capture: {screenshot: after_login}
"""
    )
    kinds = [type(s) for s in plan.preconditions + plan.steps]
    assert kinds[:6] == [FixtureStep, TaskStep, InteractionStep, WaitStep, AssertStep, ApiStep]
    assert plan.steps[1].interaction == InteractionName.TAP


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("version: 2\nname: future\nplatforms: [api]\nsteps: [{capture: {}}]", "newer than this framework supports"),
        ("name: nover\nplatforms: [api]\nsteps: [{capture: {}}]", "integer `version`"),
        ("version: '1'\nname: strver\nplatforms: [api]\nsteps: [{capture: {}}]", "integer `version`"),
        ("version: 1\nname: twokinds\nplatforms: [api]\nsteps: [{task: login, interaction: tap}]", "exactly one of"),
        ("version: 1\nname: typo\nplatforms: [api]\nsteps: [{tsk: login}]", "exactly one of"),
        (
            "version: 1\nname: extra\nplatforms: [api]\nbogus: 1\nsteps: [{capture: {}}]",
            "Extra inputs are not permitted",
        ),
        ("version: 1\nname: Bad-Name\nplatforms: [api]\nsteps: [{capture: {}}]", "should match pattern"),
        ("version: 1\nname: mixed\nplatforms: [api, ios]\nsteps: [{capture: {}}]", "separate plan"),
        ("version: 1\nname: nosteps\nplatforms: [api]\nsteps: []", "at least 1"),
        (
            "version: 1\nname: matcher\nplatforms: [api]\nsteps: [{assert: {question: cart_total, expect: {}}}]",
            "exactly one",
        ),
        (
            "version: 1\nname: matcher2\nplatforms: [api]\nsteps: [{assert: {question: cart_total, expect: {equals: 1, contains: x}}}]",
            "exactly one",
        ),
        (
            "version: 1\nname: badregex\nplatforms: [api]\nsteps: [{assert: {question: cart_total, expect: {matches: '('}}}]",
            "regular expression",
        ),
        (
            "version: 1\nname: timeout\nplatforms: [android]\nsteps: [{wait: {target: x, timeout_ms: 999999}}]",
            "less than or equal",
        ),
        (
            "version: 1\nname: retry\nplatforms: [api]\nsteps: [{capture: {}, retry: {max_attempts: 9}}]",
            "less than or equal",
        ),
        ("[1, 2]", "mapping"),
        ("version: 1\nname: x: y: z", "not valid YAML"),
    ],
)
def test_rejects_invalid_plans(text: str, message: str) -> None:
    with pytest.raises(PlanLoadError) as error:
        load_plan_text(text)
    assert message in error.value.message


def test_ai_plan_requires_provenance() -> None:
    with pytest.raises(PlanLoadError, match="provenance"):
        load_plan_text(MINIMAL + "metadata: {origin: ai_generated}\n")


def test_yaml_is_parsed_safely() -> None:
    with pytest.raises(PlanLoadError):
        load_plan_text("!!python/object/apply:os.system ['echo hacked']")


def test_round_trip_preserves_content_and_hash() -> None:
    plan = load_plan_text(
        MINIMAL.replace("expect: {status: 200}", "expect: {status: 200, json: [{path: checks.db, equals: null}]}")
    )
    dumped = dump_plan(plan)
    again = load_plan_text(dumped)
    assert plan_hash(again) == plan_hash(plan)
    assert again.steps[0].api.expect.json_[0].operator == "equals"  # explicit null survived


def test_hash_changes_when_a_step_changes() -> None:
    first = load_plan_text(MINIMAL)
    second = load_plan_text(MINIMAL.replace("status: 200", "status: 204"))
    assert plan_hash(first) != plan_hash(second)


def test_hash_ignores_approval() -> None:
    plan = load_plan_text(MINIMAL)
    approved = load_plan_text(MINIMAL + "metadata: {approval: {approved_by: me, approved_at: now, plan_sha256: abc}}\n")
    assert plan_hash(plan) == plan_hash(approved)


def test_migrations_hook_upgrades_older_versions(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtf.dsl import loader

    monkeypatch.setattr(loader, "CURRENT_VERSION", 2)
    monkeypatch.setitem(MIGRATIONS, 1, lambda doc: {**doc, "version": 2, "migrated": True})
    assert loader.migrate({"version": 1})["migrated"] is True


def test_committed_schema_is_current(root: Path) -> None:
    assert (root / "contracts/test-plan.schema.json").read_text(encoding="utf-8") == render_schema()


def test_every_repository_plan_conforms_to_the_json_schema(root: Path) -> None:
    schema = json.loads((root / "contracts/test-plan.schema.json").read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)
    plans = sorted((root / "plans").rglob("*.yaml"))
    assert plans
    for path in plans:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        errors = [e.message for e in validator.iter_errors(document)]
        assert not errors, f"{path}: {errors[:3]}"
        load_plan(path)  # and the models accept it
