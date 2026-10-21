"""Safety policy: bounds and rules that apply to every plan, AI-generated or not."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyPolicy:
    max_step_timeout_ms: int = 60_000
    max_wait_timeout_ms: int = 60_000
    max_retry_attempts: int = 3
    # Retrying an assertion can hide a real defect; it must be explicitly allowed by the policy.
    allow_assertion_retries: bool = False
    # Reset/seed/deterministic-error endpoints. Taken from the selected environment.
    allow_test_support: bool = True
    # AI-generated plans need a recorded human approval whose hash matches the plan.
    require_approval_for_ai_plans: bool = True
    # Maximum steps in one plan section beyond the schema limit.
    max_steps: int = 150
