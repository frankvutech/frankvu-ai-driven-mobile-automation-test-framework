# Architecture

MTF tests **prebuilt** application artifacts — an Android APK, an iOS simulator `.app` and the
backend — without building or modifying them. AI may *propose* test plans; only validated, approved
data ever runs.

```text
 mtf CLI ─────────────────────────────────────────────────────────────────────────────┐
   │ plan generate                     validate / run                                 │
   ▼                                        │                                         │
 AI Planner ──(candidate plan)──► Plan Validator + Safety Policy ◄── contracts/       │
 (provider port,                         │ (rejects before launch)   test-ids.yaml    │
  never executes)                        ▼                           test-data.yaml   │
                                 Execution Orchestrator             openapi/backend   │
                                 (lifecycle state machine,                            │
                                  interpreter = step commands,                        │
                                  watchdog, cancellation, evidence)                   │
                    ┌───────────────────┼────────────────────────┐                    │
                    ▼                   ▼                        ▼                    │
             Screenplay actors   Screenplay actors          API runner                │
             + Screen Robots     + Screen Robots            (httpx, OpenAPI checks)   │
                    │ UiDriver port      │ UiDriver port            │ HttpTransport port│
                    ▼                   ▼                        ▼                    │
          Android session        iOS session               Backend REST API           │
          adb + UIAutomator      simctl + xcodebuild                                  │
          runner APK (on device) test-without-building                                │
                    │            XCUITest runner (in sim)                              │
                    ▼                   ▼                                             │
          supplied APK (unchanged)  supplied .app (unchanged)                         │
                                                                                      │
 events (Observer) ──► console · events.jsonl · result.json · report.html ◄──────────┘
```

## Components

| Package | Responsibility |
|---|---|
| `framework/mtf/contracts` | Loads and imports the contract: test-ID registry, deterministic test data, OpenAPI. |
| `framework/mtf/dsl` | Versioned Test Plan DSL (pydantic models), safe YAML/JSON loading, migrations, canonical hash, JSON Schema export. |
| `framework/mtf/validator` | Semantic validation and safety policy (IDs, platforms, tasks, API routes, references, secrets, bounds, approval). |
| `framework/mtf/screenplay` | Actors, abilities, tasks, interactions, questions, fixtures and Screen Robots. |
| `framework/mtf/orchestrator` | Run lifecycle, interpreter (step commands), contract guard, evidence, reports. |
| `framework/mtf/api_runner` | HTTP client with token handling, JSON path, assertions, redacted capture. |
| `framework/mtf/android_runner` | Host side of the Android runner: adb, APK install/verification, runner lifecycle. |
| `framework/mtf/ios_runner` | Host side of the iOS runner: simctl, `.xctestrun` patching, `test-without-building`. |
| `framework/mtf/planner` | AI provider port, Anthropic and recorded providers, planner, approval, advisory analysis. |
| `framework/mtf/reporting` | Result model, JSON and HTML reports. |
| `framework/android-runner` | Kotlin, on device: self-instrumenting UIAutomator command server (never instruments the app). |
| `framework/ios-runner` | Swift, in the simulator: XCUITest bundle without a host app, serving the same commands. |

The host orchestrator is Python (fast iteration, pydantic/jsonschema validation, httpx, Jinja2
reports). The device-side runners are the smallest native pieces that can drive a black-box app on
each platform. Both speak the same newline-delimited JSON protocol (`framework/mtf/core/rpc.py`),
so one host adapter base (`core/remote_driver.py`) serves both.

## Patterns, and why each one is here

| Pattern | Where | Benefit in this framework |
|---|---|---|
| **Screenplay** | `screenplay/actor.py`, `tasks.py`, `questions.py` | Plans speak business intent (`login`, `checkout`); one scenario serves Android and iOS. Actors carry identity (account) and abilities (UI, API), so UI and API steps share one actor. |
| **Screen Robot** | `screenplay/robots/` | Each screen's IDs and quirks live in one class. Platform subclasses exist only where the apps really differ (iOS "Save Password?" sheet, Android `top_bar_back`, catalog list ID shadowed on iOS). |
| **Adapter** | `core/remote_driver.py`, `android_runner/session.py`, `ios_runner/session.py`, `api_runner/client.py` | One `UiDriver` port for UIAutomator and XCUITest; one `HttpTransport` port for httpx and the unit-test fake. |
| **Ports and Adapters** | `core/ports.py`, `orchestrator/sessions.py` | The orchestrator never imports adb, simctl or httpx. Unit tests run whole plans against in-memory fakes. |
| **Interpreter** | `orchestrator/commands.py` + `orchestrator.py` | Validated DSL steps are interpreted; there is no code generation and no path from plan text to code. |
| **Command** | `orchestrator/commands.py` (`TaskCommand`, `ApiCommand`, …) | Each step is an object with uniform retry, timeout, evidence and reporting handling. |
| **Strategy** | platform sessions, robot factory, AI providers | Choose the runner per platform, robots per platform, provider per configuration (Anthropic / recorded). |
| **Observer** | `core/events.py` | Console output, the JSONL event log and future integrations subscribe to run events. A failing subscriber cannot change a result. |
| **State** | `orchestrator/lifecycle.py` | Explicit run states and legal transitions (`validating → rejected` never reaches `preparing`; cleanup always follows preparation). |
| **Decorator** | `orchestrator/contract_guard.py` | Every ID that reaches a device is checked against the registry at run time; sensitive input and secret-revealing elements are handled in one place. |

Deliberately **not** abstracted: there is one DSL interpreter (no plugin system for step kinds), one
result model, and no generic "device farm" layer. Parallel execution is not implemented; see
[Known limitations](../README.md#known-limitations).

## Execution flow

1. **Validate** (`RunState.VALIDATING`): DSL schema, then semantic validation for the target
   platform, safety policy, environment rules and the presence of every needed secret variable.
   Any error → `REJECTED`; nothing is installed or launched.
2. **Prepare**: inspect the artifact (checksum, package/bundle ID, version), install it unchanged,
   verify the installed version, start the framework runner. Failures are `error`, not `failed`.
3. **Preconditions → steps**: each step becomes a command, runs under a watchdog thread with
   cooperative cancellation, may retry only if the plan allows the failure category, and records
   evidence on failure. The first failing step skips the rest.
4. **Cleanup** always runs once preparation started. A cleanup failure turns a pass into `error`.
5. **Report**: everything goes through the redactor into `result.json`, `report.html` and
   `events.jsonl`. Statuses are set once; no component can turn a failure into a pass.

## Device runner protocol

Request and response are one JSON line each, one request per TCP connection:

```json
{"id": "7", "token": "<per-run 128-bit token>", "command": "tap", "args": {"id": "login_button", "timeout_ms": 10000}}
{"id": "7", "ok": false, "error": {"category": "element_not_found", "message": "'login_button' did not appear within 10000 ms"}}
```

The command set is closed (`tap`, `type_text`, `scroll_to`, `wait_for`, `read`, `first_visible`,
`hierarchy`, …). Element arguments must match the test-ID pattern `^[a-z0-9]+(_[a-z0-9]+)*$`;
there is no command that accepts text, XPath, coordinates, shell commands or code. Servers bind to
loopback only and compare the token in constant time.

## Key decisions

* **Self-instrumenting Android runner.** Instrumenting the app's process would need the app's
  signing key and a rebuilt test APK; a black-box UIAutomator runner works with any prebuilt APK.
* **iOS UI-test bundle without a host app.** `XCUIApplication(bundleIdentifier:)` drives the
  installed app, so `build-for-testing` builds only the runner and `test-without-building` runs it
  against whatever `.app` was supplied.
* **Step-level remote control** rather than shipping whole plans to the device: API setup and
  checks can sit between UI steps, the host enforces timeouts and cancellation, and evidence is
  captured per step.
* **Contracts are committed data.** Test runs never read the app repository; the importer runs
  only when the app team publishes new IDs.
