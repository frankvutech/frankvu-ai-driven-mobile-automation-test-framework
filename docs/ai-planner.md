# AI planner

The planner turns a natural-language request into a **candidate** plan. It is a separate step in
front of validation and approval; it never runs a plan, operates a device, calls the backend, runs a
shell command or executes code.

```text
request ──► guard (length, platforms, no secrets) ──► provider (structured output)
        ──► parse + DSL schema ──► semantic validator ──(errors)──► repair round (bounded)
        ──► candidate saved with metadata.origin = ai_generated, NOT approved
human review ──► mtf plan approve --by <name>  (approval bound to the plan's content hash)
mtf run ──► validator requires a current approval for AI plans ──► execution (no AI involved)
```

## What the model sees

One stable, deterministic system prompt (cacheable): the DSL rules, the action catalog (tasks,
questions, fixtures, interactions with typed parameters and platforms), the test-ID registry grouped
by screen (with platform availability and the reason an ID is not addressable), the API operations,
the referenceable test data, and the DSL JSON Schema. Passwords appear only as
`${secret:accounts.<ref>.password}` placeholders. A request that contains a known secret value is
refused before anything is sent.

## Output contract

The provider must return one JSON object (enforced with structured outputs):

```json
{"needs_clarification": false, "clarification_questions": [], "explanation": "…",
 "assumptions": ["Uses the seeded customer account."], "plan_json": "{ …DSL v1 plan… }"}
```

The plan travels as a string and is checked by the framework's own loader and validator, which are
stricter than any output schema (unknown tasks, IDs not on a platform, undeclared statuses, literal
secrets, …). The planner overwrites `metadata` (origin, provider, model, request hash, explanation,
assumptions) — the model cannot mark its own plan as approved or manual. Platforms must be exactly
the requested ones.

* **Clarification** only when information is genuinely missing; otherwise the model must make a
  conservative assumption and list it. `mtf plan generate` prints the questions and exits non-zero.
* **Repair**: validation errors are fed back for `--max-repairs` rounds (default 1). A candidate that
  is still invalid is reported, written for inspection, and cannot be approved.

## Approval and execution

```bash
uv run mtf plan generate "A customer buys one product and the order shows up in the backend" \
  --platform android --platform ios --out plans/generated/checkout_ai.yaml
uv run mtf validate plans/generated/checkout_ai.yaml        # → approval_required
# review the YAML, edit if needed, then:
uv run mtf plan approve plans/generated/checkout_ai.yaml --by "Your Name"
uv run mtf run plans/generated/checkout_ai.yaml --platform android --apk artifacts/android/app-debug.apk
```

The approval stores the approver, time and the plan's canonical SHA-256 (approval excluded). Any
later edit makes the approval stale and the plan is rejected until it is approved again. Approvers
named like `ai…` are refused. Manually authored plans need no approval.

## Deterministic replay

A saved plan is plain data: running it never calls an AI service, so results do not depend on model
availability or non-determinism. Planning sessions can also be replayed offline with the
`recorded` provider, which returns stored responses in order (and fails when they run out instead of
inventing an answer):

```bash
uv run mtf plan generate "…" --platform android --platform ios \
  --provider recorded --recording examples/ai/recorded-checkout-session.json --out /tmp/plan.yaml
```

`examples/ai/recorded-checkout-session.json` demonstrates a repair: the first candidate invents a
task (`purchase_everything`), the validator rejects it, the second candidate is valid. That plan was
approved and executed on the Android emulator (see README → Verification).

## Providers

| Provider | Configuration |
|---|---|
| `anthropic` (default) | `pip install mtf[anthropic]` (`uv sync --extra anthropic`); credentials from `ANTHROPIC_API_KEY` or an `ant auth login` profile; model `claude-opus-5-5` unless `--model` / `MTF_AI_MODEL`. Uses structured outputs (`output_config.format`), adaptive thinking at `high` effort, streaming, prompt caching of the system prompt, and server-side refusal fallback. Refusals and truncated outputs are errors, never partial plans. |
| `recorded` | `--recording file.json` — offline, deterministic |

Add a provider by implementing `PlannerProvider.complete_json(system, user, schema, purpose)`.
If no provider is available, `plan generate` fails with a clear message; validation and execution
of existing plans are unaffected.

## Advisory failure analysis

```bash
uv run mtf report advise reports/runs/<run-id>
```

Sends a redacted summary of the finished run (statuses, failure categories, messages, expected/actual
values, failing checks, failed API exchanges — never screenshots or raw logs) and stores the answer in
`result.json` → `advisory`, shown in the HTML report under a label that says it is advisory. The
recorded status, every step status, the failure category and the summary are compared before and after;
the advisory can only add a field. It may suggest plan changes for human review; it never edits plans.

**Not verified here:** no live call to the Anthropic API was made in this environment (no API key
configured); the provider is covered by unit tests through the provider port and the recorded provider.
