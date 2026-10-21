"""Semantic plan validation against the contracts, the action catalog and the safety policy.

The DSL models already guarantee the *shape* of a plan. This validator checks *meaning*: every ID,
task, question, fixture, API operation, test-data reference and variable exists and is available on
the target platform; parameters have the right types; timeouts and retries are bounded; secrets are
only referenced, never written; and expectations are consistent with the API contract.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from mtf.contracts.bundle import Contracts
from mtf.core.catalog import ActionKind, ActionSpec, Catalog, ParamKind, ParamSpec
from mtf.core.errors import SelectorContractViolation
from mtf.core.redaction import Redactor
from mtf.core.types import FailureCategory, PlanOrigin, Platform
from mtf.dsl.loader import plan_hash
from mtf.dsl.models import (
    REF_RE,
    ApiStep,
    AssertStep,
    CaptureStep,
    FixtureStep,
    InteractionName,
    InteractionStep,
    TaskStep,
    TestPlan,
    WaitStep,
    references_in,
)
from mtf.validator.policy import SafetyPolicy

_REF_BODY_RE = re.compile(r"^(secret:)?[a-z_][a-z0-9_]*(\.[a-z0-9_]+)+$")
# Interactions that start or replace the app process: UI steps before one of these are invalid.
_LAUNCHERS = {InteractionName.LAUNCH_APP, InteractionName.RELAUNCH_APP, InteractionName.OPEN_DEEP_LINK}
_LAUNCH_FIXTURES = {"launch_app", "launch_app_clean"}


class Severity(StrEnum):
    ERROR = "error"
    WARNING = "warning"


@dataclass(frozen=True)
class Issue:
    severity: Severity
    code: str
    location: str
    message: str

    def __str__(self) -> str:
        return f"[{self.severity.value}] {self.location}: {self.message} ({self.code})"


@dataclass
class ValidationReport:
    plan_name: str
    platforms: list[Platform]
    issues: list[Issue] = field(default_factory=list)

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def selector_contract_violations(self) -> list[Issue]:
        return [i for i in self.errors if i.code == FailureCategory.SELECTOR_CONTRACT_VIOLATION.value]

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan": self.plan_name,
            "platforms": [p.value for p in self.platforms],
            "ok": self.ok,
            "issues": [
                {"severity": i.severity.value, "code": i.code, "location": i.location, "message": i.message}
                for i in self.issues
            ],
        }


class PlanValidator:
    def __init__(self, contracts: Contracts, catalog: Catalog, policy: SafetyPolicy | None = None) -> None:
        self.contracts = contracts
        self.catalog = catalog
        self.policy = policy or SafetyPolicy()
        self._redactor = Redactor()

    # --- entry point ---------------------------------------------------------------------------
    def validate(
        self,
        plan: TestPlan,
        platforms: Iterable[Platform] | None = None,
        *,
        known_secret_values: Iterable[str] = (),
    ) -> ValidationReport:
        targets = list(platforms) if platforms is not None else list(plan.platforms)
        report = _Collector(ValidationReport(plan.name, targets))
        for platform in targets:
            if platform not in plan.platforms:
                report.error(
                    "unsupported_platform",
                    "platforms",
                    f"the plan does not target {platform.value} (it targets "
                    f"{', '.join(p.value for p in plan.platforms)})",
                )
        targets = [p for p in targets if p in plan.platforms]
        self._check_metadata(plan, report)
        self._check_actors(plan, report)
        secrets = [s for s in known_secret_values if s]
        self._check_literals_for_secrets(plan, secrets, report)
        defined_vars: set[str] = set()
        launched = dict.fromkeys(targets, False)
        for section, index, step in plan.all_steps():
            location = f"{section}[{index}]"
            step_platforms = self._step_platforms(plan, step, targets, location, report)
            self._check_bounds(step, location, report)
            self._check_references(step, location, defined_vars, report)
            if section == "cleanup" and isinstance(step, AssertStep):
                report.error("assert_in_cleanup", location, "cleanup must not assert: it cannot change the verdict")
            for platform in step_platforms:
                self._check_step(plan, step, platform, location, report)
                if platform.is_mobile:
                    launched[platform] = self._track_launch(step, platform, launched[platform], location, report)
            if isinstance(step, ApiStep):
                defined_vars.update(step.api.save)
            elif isinstance(step, AssertStep) and step.assert_.save:
                defined_vars.add(step.assert_.save)
        return report.report

    # --- plan-level checks ---------------------------------------------------------------------
    def _check_metadata(self, plan: TestPlan, report: _Collector) -> None:
        meta = plan.metadata
        if meta.origin == PlanOrigin.AI_GENERATED and self.policy.require_approval_for_ai_plans:
            if meta.approval is None:
                report.error(
                    "approval_required",
                    "metadata.approval",
                    "AI-generated plans must be reviewed and approved before execution (mtf plan approve)",
                )
            elif meta.approval.plan_sha256 != plan_hash(plan):
                report.error(
                    "approval_stale",
                    "metadata.approval",
                    "the plan changed after it was approved; review it and approve it again",
                )

    def _check_actors(self, plan: TestPlan, report: _Collector) -> None:
        for name, actor in plan.actors.items():
            if actor.account is not None and actor.account not in self.contracts.test_data.accounts:
                report.error("unknown_account", f"actors.{name}", f"unknown account {actor.account!r}")
        default = plan.defaults.actor
        if default is not None and default not in plan.actors:
            report.error("unknown_actor", "defaults.actor", f"actor {default!r} is not declared in `actors`")

    def _check_literals_for_secrets(self, plan: TestPlan, secrets: list[str], report: _Collector) -> None:
        for section, index, step in plan.all_steps():
            for text in _strings(step.model_dump(mode="json", by_alias=True, exclude_unset=True)):
                literal = REF_RE.sub("", text)
                if self._redactor.redact_text(literal) != literal or any(s in literal for s in secrets):
                    report.error(
                        "secret_literal",
                        f"{section}[{index}]",
                        "the plan contains a literal secret (password, token or key); use a ${secret:...} reference",
                    )
                    break

    def _step_platforms(
        self, plan: TestPlan, step: Any, targets: list[Platform], location: str, report: _Collector
    ) -> list[Platform]:
        if step.platforms is None:
            return targets
        extra = [p for p in step.platforms if p not in plan.platforms]
        if extra:
            report.error(
                "unsupported_platform",
                location,
                f"step platforms {', '.join(p.value for p in extra)} are not plan platforms",
            )
        return [p for p in targets if p in step.platforms]

    def _check_bounds(self, step: Any, location: str, report: _Collector) -> None:
        if step.timeout_ms is not None and step.timeout_ms > self.policy.max_step_timeout_ms:
            report.error(
                "timeout_bounds",
                location,
                f"timeout {step.timeout_ms} ms exceeds the policy maximum {self.policy.max_step_timeout_ms} ms",
            )
        if isinstance(step, WaitStep) and step.wait.timeout_ms > self.policy.max_wait_timeout_ms:
            report.error(
                "timeout_bounds",
                location,
                f"wait {step.wait.timeout_ms} ms exceeds the policy maximum {self.policy.max_wait_timeout_ms} ms",
            )
        if step.retry is not None:
            if step.retry.max_attempts > self.policy.max_retry_attempts:
                report.error("retry_bounds", location, f"at most {self.policy.max_retry_attempts} attempts allowed")
            if FailureCategory.ASSERTION_FAILED in step.retry.on_failure and not self.policy.allow_assertion_retries:
                report.error(
                    "retry_policy",
                    location,
                    "retrying assertion failures is not allowed by the safety policy (it can hide defects)",
                )
            if isinstance(step, ApiStep) and step.api.method.value != "GET":
                report.warning("retry_policy", location, "retrying a non-GET request can repeat a side effect")

    def _check_references(self, step: Any, location: str, defined_vars: set[str], report: _Collector) -> None:
        data = self.contracts.test_data
        for ref in references_in(step.model_dump(mode="json", by_alias=True, exclude_unset=True)):
            if not _REF_BODY_RE.match(ref):
                report.error("invalid_reference", location, f"${{{ref}}} is not a plain dotted reference")
                continue
            if ref.startswith("secret:"):
                if not data.is_secret_ref(ref.removeprefix("secret:")):
                    report.error("unknown_reference", location, f"unknown secret reference ${{{ref}}}")
            elif ref.startswith("vars."):
                if ref.removeprefix("vars.") not in defined_vars:
                    report.error(
                        "undefined_variable",
                        location,
                        f"${{{ref}}} is used before an earlier api step saves it",
                    )
            elif ref.startswith("accounts.") and ref.endswith(".password"):
                report.error("secret_literal", location, f"use ${{secret:{ref}}} for passwords")
            elif not data.has(ref):
                report.error("unknown_reference", location, f"${{{ref}}} is not in contracts/test-data.yaml")

    def _track_launch(self, step: Any, platform: Platform, launched: bool, location: str, report: _Collector) -> bool:
        if isinstance(step, InteractionStep) and step.interaction in _LAUNCHERS:
            return True
        if isinstance(step, FixtureStep) and step.fixture in _LAUNCH_FIXTURES:
            return True
        if launched:
            return True
        needs_app = isinstance(step, InteractionStep | WaitStep | CaptureStep) or (
            isinstance(step, TaskStep | AssertStep) and self._is_ui_action(step)
        )
        if needs_app:
            report.error(
                "ui_before_launch",
                location,
                f"{platform.value}: UI step before the app is launched (add the `launch_app` fixture first)",
            )
            return True  # report once per platform
        return False

    def _is_ui_action(self, step: TaskStep | AssertStep) -> bool:
        if isinstance(step, TaskStep):
            spec = self.catalog.get(ActionKind.TASK, step.task)
        elif step.assert_.question is not None:
            spec = self.catalog.get(ActionKind.QUESTION, step.assert_.question)
        else:
            return True
        return spec is not None and Platform.API not in spec.platforms

    # --- per-step, per-platform checks ---------------------------------------------------------
    def _check_step(self, plan: TestPlan, step: Any, platform: Platform, location: str, report: _Collector) -> None:
        if isinstance(step, TaskStep):
            spec = self._spec(ActionKind.TASK, step.task, platform, location, report)
            if spec:
                self._check_actor_use(plan, spec, step.actor, location, report)
                self._check_params(spec, step.with_, platform, location, report)
                for test_id in spec.test_ids if platform.is_mobile else ():
                    if not self.contracts.test_ids.is_known(test_id, platform):
                        report.error(
                            FailureCategory.SELECTOR_CONTRACT_VIOLATION.value,
                            location,
                            f"task {spec.name!r} needs test ID {test_id!r}, which the {platform.value} app does not "
                            "expose to the black-box runner (see contracts/test-ids.yaml source notes)",
                        )
        elif isinstance(step, FixtureStep):
            spec = self._spec(ActionKind.FIXTURE, step.fixture, platform, location, report)
            if spec:
                self._check_actor_use(plan, spec, step.actor, location, report)
                self._check_params(spec, step.with_, platform, location, report)
                if spec.uses_test_support and not self.policy.allow_test_support:
                    report.error(
                        "test_support_disabled",
                        location,
                        f"fixture {spec.name!r} needs test-support endpoints, which this environment does not allow",
                    )
        elif isinstance(step, InteractionStep):
            self._check_interaction(step, platform, location, report)
        elif isinstance(step, WaitStep):
            self._require_mobile(platform, location, "wait", report)
            self._check_test_id(step.wait.target, platform, f"{location}.wait.target", report)
        elif isinstance(step, AssertStep):
            self._check_assert(plan, step, platform, location, report)
        elif isinstance(step, ApiStep):
            self._check_api(plan, step, location, report)
        elif isinstance(step, CaptureStep):
            self._require_mobile(platform, location, "capture", report)

    def _spec(
        self, kind: ActionKind, name: str, platform: Platform, location: str, report: _Collector
    ) -> ActionSpec | None:
        spec = self.catalog.get(kind, name)
        if spec is None:
            known = ", ".join(self.catalog.names(kind))
            report.error(f"unknown_{kind.value}", location, f"unknown {kind.value} {name!r} (registered: {known})")
            return None
        if platform not in spec.platforms:
            report.error(
                "unsupported_platform",
                location,
                f"{kind.value} {name!r} is not available on {platform.value} "
                f"(available on: {', '.join(sorted(p.value for p in spec.platforms))})",
            )
            return None
        return spec

    def _check_actor_use(
        self, plan: TestPlan, spec: ActionSpec, actor: str | None, location: str, report: _Collector
    ) -> None:
        effective = actor or plan.defaults.actor
        if actor is not None and actor not in plan.actors:
            report.error("unknown_actor", location, f"actor {actor!r} is not declared in `actors`")
        if spec.needs_actor and effective is None:
            report.error("missing_actor", location, f"{spec.kind.value} {spec.name!r} needs an actor")
        if spec.needs_actor and effective in plan.actors and plan.actors[effective].account is None:
            report.error("missing_actor", location, f"actor {effective!r} has no account")

    def _check_params(
        self, spec: ActionSpec, given: dict[str, Any], platform: Platform, location: str, report: _Collector
    ) -> None:
        for name in given:
            if name not in spec.params:
                allowed = ", ".join(spec.params) or "none"
                report.error("unknown_param", location, f"{spec.name}: unknown parameter {name!r} (allowed: {allowed})")
        for name, param in spec.params.items():
            if name not in given:
                if param.required:
                    report.error("missing_param", location, f"{spec.name}: missing required parameter {name!r}")
                continue
            problem = self._param_problem(param, given[name], platform)
            if problem:
                report.error("bad_param", location, f"{spec.name}.{name}: {problem}")

    def _param_problem(self, param: ParamSpec, value: Any, platform: Platform) -> str | None:
        data = self.contracts.test_data
        is_ref = isinstance(value, str) and REF_RE.fullmatch(value) is not None
        match param.kind:
            case ParamKind.PRODUCT:
                return None if value in data.products else f"{value!r} is not a product in the test data"
            case ParamKind.ANY_PRODUCT_ID:
                ok = value in data.products or value in data.missing_products
                return None if ok else f"{value!r} is neither a seeded nor a registered missing product"
            case ParamKind.ACCOUNT:
                return None if value in data.accounts else f"{value!r} is not an account in the test data"
            case ParamKind.TEXT:
                return None if isinstance(value, str) else "expected text"
            case ParamKind.PASSWORD:
                if not is_ref:
                    return "passwords must be a ${secret:accounts.<ref>.password} or ${inputs.<name>} reference"
                ref = REF_RE.fullmatch(value).group(1)  # type: ignore[union-attr]
                return (
                    None if ref.startswith(("secret:", "inputs.")) else "passwords must reference a secret or an input"
                )
            case ParamKind.INT:
                if not isinstance(value, int) or isinstance(value, bool):
                    return "expected an integer"
                if param.minimum is not None and value < param.minimum:
                    return f"must be >= {param.minimum}"
                if param.maximum is not None and value > param.maximum:
                    return f"must be <= {param.maximum}"
                return None
            case ParamKind.BOOL:
                return None if isinstance(value, bool) else "expected true or false"
            case ParamKind.ENUM:
                return None if value in param.values else f"expected one of {', '.join(param.values)}"
            case ParamKind.TEST_ID:
                if not isinstance(value, str):
                    return "expected a test ID"
                if not self.contracts.test_ids.is_known(value, platform if platform.is_mobile else None):
                    return f"{value!r} is not a registered test ID on {platform.value}"
                return None
            case ParamKind.DEEP_LINK:
                links = data.data.get("deep_links", {})
                return None if value in links else f"{value!r} is not a deep link in the test data"
        return None  # pragma: no cover

    def _check_interaction(self, step: InteractionStep, platform: Platform, location: str, report: _Collector) -> None:
        spec = self._spec(ActionKind.INTERACTION, step.interaction.value, platform, location, report)
        if spec is None:
            return
        if spec.needs_target and step.target is None:
            report.error("missing_target", location, f"{spec.name} needs a `target` test ID")
        if step.target is not None:
            if not (spec.needs_target or spec.optional_target):
                report.error("unexpected_target", location, f"{spec.name} does not take a target")
            else:
                resolution = self._check_test_id(step.target, platform, f"{location}.target", report)
                if (
                    resolution is not None
                    and resolution.sensitive
                    and step.interaction == InteractionName.TYPE_TEXT
                    and REF_RE.fullmatch(str(step.with_.get("text", ""))) is None
                ):
                    report.error(
                        "secret_literal",
                        location,
                        f"text typed into the sensitive field {step.target!r} must be a "
                        "${secret:...} or ${inputs...} reference",
                    )
        self._check_params(spec, step.with_, platform, location, report)

    def _check_test_id(self, test_id: str, platform: Platform, location: str, report: _Collector) -> Any:
        if not platform.is_mobile:
            report.error("unsupported_platform", location, "UI test IDs cannot be used in an api plan")
            return None
        resolution = self.contracts.test_ids.resolve(test_id)
        if resolution is None:
            report.error(
                FailureCategory.SELECTOR_CONTRACT_VIOLATION.value,
                location,
                f"unknown test ID {test_id!r}: it is not in contracts/test-ids.yaml",
            )
            return None
        if platform not in resolution.platforms:
            try:
                self.contracts.test_ids.require(test_id, platform)
            except SelectorContractViolation as exc:
                report.error(FailureCategory.SELECTOR_CONTRACT_VIOLATION.value, location, exc.message)
            return None
        return resolution

    def _check_assert(
        self, plan: TestPlan, step: AssertStep, platform: Platform, location: str, report: _Collector
    ) -> None:
        spec_ = step.assert_
        if spec_.question is not None:
            spec = self._spec(ActionKind.QUESTION, spec_.question, platform, location, report)
            if spec:
                self._check_actor_use(plan, spec, spec_.actor, location, report)
                self._check_params(spec, spec_.with_, platform, location, report)
                self._check_matcher_type(spec, spec_.expect.operator, location, report)
        else:
            self._require_mobile(platform, location, "element assertion", report)
            if spec_.target is not None and platform.is_mobile:
                self._check_test_id(spec_.target, platform, f"{location}.assert.target", report)

    def _check_matcher_type(self, spec: ActionSpec, operator: str, location: str, report: _Collector) -> None:
        if spec.returns == "bool" and operator not in ("equals", "not_equals"):
            report.error("bad_matcher", location, f"question {spec.name!r} returns true/false; use equals")
        if spec.returns != "int" and operator in ("greater_than", "less_than"):
            report.error("bad_matcher", location, f"question {spec.name!r} is not numeric")

    def _check_api(self, plan: TestPlan, step: ApiStep, location: str, report: _Collector) -> None:
        api = step.api
        op = self.contracts.api.find(api.method.value, api.path)
        if op is None:
            report.error(
                "unknown_operation",
                location,
                f"{api.method.value} {api.path} is not declared in contracts/openapi/backend.yaml",
            )
            return
        missing = [p for p in op.path_params if p not in api.path_params]
        extra = [p for p in api.path_params if p not in op.path_params]
        if missing:
            report.error("missing_param", location, f"{op.key}: missing path parameter(s) {', '.join(missing)}")
        if extra:
            report.error("unknown_param", location, f"{op.key}: unknown path parameter(s) {', '.join(extra)}")
        unknown_query = [q for q in api.query if q not in op.query_params]
        if unknown_query:
            report.error(
                "unknown_param",
                location,
                f"{op.key}: undeclared query parameter(s) {', '.join(unknown_query)}",
            )
        if (api.body is not None or api.raw_body is not None) and not op.has_body:
            report.error("unexpected_body", location, f"{op.key} takes no request body")
        statuses = api.expect.status if isinstance(api.expect.status, list) else [api.expect.status]
        for status in statuses:
            if str(status) not in op.statuses:
                report.error(
                    "undeclared_status",
                    location,
                    f"{op.key} does not declare status {status} (declared: {', '.join(op.statuses)})",
                )
        if op.is_test_support and not self.policy.allow_test_support:
            report.error("test_support_disabled", location, f"{op.key} is a test-support endpoint; not allowed here")
        if api.as_ is not None and api.as_ not in plan.actors:
            report.error("unknown_actor", location, f"actor {api.as_!r} is not declared in `actors`")
        success = all(200 <= s < 300 for s in statuses)
        if op.requires_auth and success and (api.as_ is None or api.token != "valid"):  # noqa: S105 - token mode
            report.error(
                "auth_expectation",
                location,
                f"{op.key} requires a bearer token: a success status needs `as: <actor>` with a valid token",
            )
        if success and op.path.startswith("/api/v1/admin") and api.as_ in plan.actors:
            account = plan.actors[api.as_].account
            role = self.contracts.test_data.accounts.get(account or "", {}).get("role")
            if role != "ADMIN":
                report.error(
                    "auth_expectation",
                    location,
                    f"{op.key} succeeds only for ADMIN; actor {api.as_!r} has role {role}",
                )

    @staticmethod
    def _require_mobile(platform: Platform, location: str, what: str, report: _Collector) -> None:
        if not platform.is_mobile:
            report.error("unsupported_platform", location, f"{what} needs a mobile platform")


class _Collector:
    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        self._seen: set[tuple[str, str, str]] = set()

    def _add(self, severity: Severity, code: str, location: str, message: str) -> None:
        key = (code, location, message)
        if key not in self._seen:  # the same issue on two platforms is reported once
            self._seen.add(key)
            self.report.issues.append(Issue(severity, code, location, message))

    def error(self, code: str, location: str, message: str) -> None:
        self._add(Severity.ERROR, code, location, message)

    def warning(self, code: str, location: str, message: str) -> None:
        self._add(Severity.WARNING, code, location, message)


def _strings(value: Any) -> Iterable[str]:
    """Every string value except free-text descriptions (which may legitimately say "bearer token")."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            if key != "description":
                yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)
