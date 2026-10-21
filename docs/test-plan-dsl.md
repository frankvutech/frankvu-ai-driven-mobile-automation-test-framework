# Test Plan DSL (version 1)

A plan is YAML (or JSON) data. It is validated against the DSL models, the JSON Schema
(`contracts/test-plan.schema.json`, generated from the models) and the contracts before anything
runs. Plans cannot contain code, shell commands, selectors other than registered test IDs, or literal
secrets.

## Shape

```yaml
version: 1                       # DSL major version (required)
name: checkout_order_confirmation  # lowercase snake_case, unique per plan
title: Checkout shows an order confirmation that matches the backend order
description: optional free text
tags: [checkout, smoke]
platforms: [android, ios]        # or [api]; api plans never mix with mobile platforms
metadata:                        # set by the framework for AI plans; optional for manual plans
  origin: manual                 # manual | ai_generated
actors:
  customer: {account: customer}  # account references from contracts/test-data.yaml
defaults:
  actor: customer
  timeout_ms: 10000              # element waits inside UI steps
evidence:
  screenshots: on_failure        # never | on_failure | every_step
  hierarchy: on_failure          # never | on_failure
  device_logs: on_failure        # never | on_failure | always
  api_exchanges: always          # never | always
preconditions: [...]             # steps; a failure here is a setup error and skips the steps
steps: [...]
cleanup: [...]                   # always attempted once the app/runner was prepared; no asserts
```

## Step kinds

Every step has exactly one kind key, plus optional `id`, `description`, `timeout_ms`, `retry` and
`platforms` (restricts the step to some of the plan's platforms).

| Kind | Example | Notes |
|---|---|---|
| `task` | `task: add_product_to_cart`<br>`with: {product: product_001}` | Registered Screenplay task (`mtf catalog`). `actor:` optional. |
| `interaction` | `interaction: tap`<br>`target: login_button` | Low-level action on a registered test ID. See below. |
| `wait` | `wait: {target: home_screen, state: visible, timeout_ms: 5000}` | Explicit, condition-based. States: `visible`, `gone`, `enabled`, `disabled`, `selected`, `not_selected`; optional `text:` matcher. |
| `assert` | `assert: {question: cart_total, expect: {equals: "29.97"}, timeout_ms: 5000}` | A registered question, or `target:` + `property:` (`visible`, `exists`, `text`, `value`, `enabled`, `selected`). `timeout_ms` re-asks until the matcher holds. `save: var` stores the answer. |
| `api` | see below | An operation from the OpenAPI contract. |
| `fixture` | `fixture: reset_backend_data` | Registered setup/cleanup action (`launch_app`, `seed_cart`, `clear_cart`, `terminate_app`). |
| `capture` | `capture: {screenshot: after_login, hierarchy: true}` | Extra evidence. |

Interactions: `tap`, `double_tap`, `long_press` (`duration_ms`), `type_text` (`text`,
`clear_first`), `clear_text`, `scroll_to` (`direction`, `max_swipes`, `container`), `swipe`
(`direction`, optional target), `drag_to` (`to`), `back`, `dismiss_keyboard`, `launch_app`,
`relaunch_app` (`clean_state`), `terminate_app`, `background_app` (`duration_ms`),
`open_deep_link` (`link`: a name from `test-data.yaml`).

### Matchers

Exactly one of `equals`, `not_equals`, `contains`, `not_contains`, `matches` (full-match regular
expression), `one_of`, `greater_than`, `less_than`. Comparison is strict: UI text is text, so quote
expected values (`equals: "7.49"`, not `equals: 7.49`).

### API steps

```yaml
- id: checkout
  api:
    method: POST
    path: /api/v1/orders/{id}        # exactly the template in contracts/openapi/backend.yaml
    path_params: {id: "${vars.order_id}"}
    query: {size: 7}                 # only declared query parameters
    body: {productId: product_001}   # or raw_body: '{"broken": ' for malformed-request tests
    as: customer                     # actor whose token is sent (acquired once, redacted)
    token: valid                     # valid | none | invalid (negative tests)
    expect:
      status: 201                    # or a list; must be declared by the contract
      headers: {content-type: {contains: json}}
      schema: true                   # validate the body against the contract's response schema
      json:
        - {path: orderNumber, equals: ORDER-0001}
        - {path: items.length, equals: 2}
    save: {order_id: id}             # run variables for later steps
```

JSON paths are dotted keys with `[index]` and `length` (`items[0].product.id`, `error.details.length`,
`$` for the body). No filters or expressions.

## References

String values may contain plain references, resolved at execution time:

| Reference | Source |
|---|---|
| `${products.product_001.price_display}` | `contracts/test-data.yaml` (accounts, products, inputs, deep_links, …) |
| `${vars.order_number}` | a variable saved by an earlier `api` or `assert` step |
| `${secret:accounts.customer.password}` | the environment variable named by the account (`MTF_PASSWORD_CUSTOMER`) |

A string that is exactly one reference keeps the referenced type (numbers stay numbers). Secrets are
registered with the redactor when resolved and never appear in reports. Password parameters and text
typed into sensitive fields **must** be a `${secret:…}` or `${inputs.…}` reference.

## Retries and timeouts

* `timeout_ms` (step or `defaults`) bounds each element wait inside the step; `wait` and `assert`
  take their own timeouts. The policy caps them at 60 s.
* The orchestrator's watchdog (`--max-step-seconds`, default 300 s) aborts a hung step, marks it
  `timeout` and retires the device runner for the rest of the run.
* `retry: {max_attempts: 2, delay_ms: 500, on_failure: [element_not_found, timeout]}` is opt-in,
  bounded (≤ 3), and every attempt is recorded. Retrying `assertion_failed` is refused by the
  default safety policy. (The key is `on_failure`, not `on`: YAML 1.1 reads `on` as `true`.)

## Platform-specific steps

Shared plans list both platforms; behaviour that differs belongs in Screen Robots. When a step only
makes sense on one platform, use `platforms: [android]` on the step, or put the plan in
`plans/android/` / `plans/ios/`. The validator checks every test ID per platform, including the IDs
a task's robots use.

## Versioning and compatibility

* `version` is the DSL **major** version. Adding optional fields keeps the version.
* Removing or changing the meaning of a field bumps the major version and adds a migration
  (`MIGRATIONS[n]` in `framework/mtf/dsl/loader.py`) that upgrades old documents before validation.
* A plan newer than the installed framework is rejected with an explicit message — never
  partially interpreted. Unknown fields are always errors (`extra = forbid`), so typos cannot be
  ignored silently.
* `contracts/test-plan.schema.json` is generated from the models (`mtf contracts schema`); CI fails if
  it is stale (`mtf contracts schema --check`).

## Validating

```bash
uv run mtf validate plans --env local          # every plan, every platform it targets
uv run mtf validate plans/shared/checkout_order_confirmation.yaml --platform ios --json
```
