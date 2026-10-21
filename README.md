# MTF — AI-assisted test automation for prebuilt mobile apps

MTF tests the **Mobile Test Playground** Android and iOS apps and their REST backend. It is a separate
project from the application: it takes **prebuilt artifacts** (an APK, a simulator `.app`, a backend),
installs them unchanged, drives them through its own native runners, and reports what happened. It
never builds or modifies the application under test.

* **One scenario, both platforms** — Screenplay tasks and Screen Robots, Android through a UIAutomator
  runner (Compose test tags as resource IDs), iOS through XCUITest (accessibility identifiers) with
  `xcodebuild test-without-building`.
* **Plans are data** — a versioned YAML DSL, validated against a shared test-ID registry, the backend's
  OpenAPI contract, deterministic test data and a safety policy *before* anything launches.
* **AI proposes, people approve** — natural language → candidate plan (structured output) →
  validation → human approval bound to the plan's hash → deterministic execution without AI.
* **API automation** with contract checks, token handling and redaction, also inside UI plans for
  UI↔backend correlation.
* **Evidence** — JSON + HTML reports with screenshots, UI hierarchies, device logs, crash logs,
  redacted API exchanges, artifact checksums and versions, device details.

```text
CLI ─► AI Planner ─► Test Plan DSL ─► Validator + policy ─► Orchestrator ─┬─► Android runner (UIAutomator, on device)
                                                                         ├─► iOS runner (XCUITest, in simulator)
                                                                         └─► API runner (httpx + OpenAPI)
                                                         events ─► result.json · report.html · events.jsonl
```

Details: [architecture](docs/architecture.md) · [DSL](docs/test-plan-dsl.md) ·
[contract](docs/test-contract.md) · [Screenplay & robots](docs/screenplay-and-screen-robots.md) ·
[artifacts](docs/prebuilt-app-artifacts.md) · [Android runner](docs/android-runner.md) ·
[iOS runner](docs/ios-runner.md) · [API testing](docs/api-testing.md) · [AI planner](docs/ai-planner.md) ·
[security](docs/security.md) · [CI](docs/ci.md) · [troubleshooting](docs/troubleshooting.md)

## Repository layout

```text
contracts/            test-ids.yaml · test-data.yaml · openapi/backend.yaml · test-plan.schema.json
framework/mtf/        Python framework: core, contracts, dsl, validator, screenplay, orchestrator,
                      api_runner, android_runner, ios_runner, planner, reporting, cli
framework/android-runner/   Kotlin: the framework's self-instrumenting UIAutomator runner APK
framework/ios-runner/       Swift: the framework's XCUITest runner (no host app)
framework/tests/      unit tests with in-memory fakes (no device, no network)
plans/shared|android|ios|api/   test plans
config/environments/  local · ci · docker · staging.example
tools/upstream/       OPTIONAL artifact producers (call the app's own build; never used by test runs)
examples/ai/          recorded planner session (offline, deterministic demo)
.github/workflows/    framework CI, API/Android/iOS test jobs (consumers), optional upstream producer
```

## What you need

| Platform | Artifacts (inputs) | Tools |
|---|---|---|
| API | a running backend, or the prebuilt `backend.jar` | Python 3.12+ with [uv](https://docs.astral.sh/uv/) (or Docker) |
| Android | the app APK (debug build) | Android SDK platform-tools (+ build-tools for `aapt2`), an emulator/device API 26+, JDK 17+ to build the framework runner once |
| iOS | the app as a **simulator** `.app` | macOS, Xcode with an iOS simulator runtime, XcodeGen to build the framework runner once |

Where the artifacts come from and what is recorded about them: [docs/prebuilt-app-artifacts.md](docs/prebuilt-app-artifacts.md).

## Quick start

```bash
make setup                                  # uv sync (framework, dev tools, optional Anthropic provider)
cp config/.env.example .env                 # then fill in the seeded accounts' passwords (local test credentials)
make check                                  # lint, types, unit tests, schema, validate all plans

# Backend: the app's prebuilt JAR on localhost:8080 (in-memory, seeded, resettable)
DATABASE_URL='jdbc:h2:mem:mtf;DB_CLOSE_DELAY=-1' TEST_SUPPORT_ENABLED=true java -jar artifacts/backend/backend.jar &

make test-api                               # API plans, no simulator

make android-runner                         # builds the FRAMEWORK runner APK only
make test-android APK=artifacts/android/app-debug.apk DEVICE=emulator-5554

make ios-runner                             # build-for-testing of the FRAMEWORK runner only
make test-ios IOS_APP=artifacts/ios/MobileTestPlayground.app SIMULATOR="iPhone 17"
```

Each run writes `reports/runs/<run-id>/report.html` (and `result.json`, `events.jsonl`, `evidence/`);
`mtf run` exits non-zero if any plan did not pass.

## Authoring and validating a plan

```yaml
version: 1
name: cart_add_product
platforms: [android, ios]
actors:
  customer: {account: customer}
defaults: {actor: customer}
preconditions:
  - fixture: reset_backend_data
  - fixture: launch_app
    with: {clean_state: true}
steps:
  - task: login
  - task: add_product_to_cart
    with: {product: product_001}
  - task: open_cart
  - assert: {question: product_is_in_cart, with: {product: product_001}, expect: {equals: true}, timeout_ms: 5000}
  - assert: {question: cart_total, expect: {equals: "${products.product_001.price_display}"}}
  - assert: {question: api_cart_quantity, with: {product: product_001}, expect: {equals: 1}}
cleanup:
  - fixture: clear_cart
```

```bash
uv run mtf catalog                                   # tasks, questions, fixtures, interactions
uv run mtf validate plans/shared/cart_add_product.yaml --env local
```

## AI-generated plans

```bash
uv run mtf plan generate "A customer buys one product and the order shows up in the backend" \
  --platform android --platform ios --out plans/generated/checkout.yaml     # Anthropic provider
uv run mtf plan approve plans/generated/checkout.yaml --by "Your Name"      # after review
uv run mtf run plans/generated/checkout.yaml --platform ios --app artifacts/ios/MobileTestPlayground.app
```

Unapproved or edited-after-approval AI plans are rejected. Offline/deterministic: add
`--provider recorded --recording examples/ai/recorded-checkout-session.json`. See
[docs/ai-planner.md](docs/ai-planner.md).

## Backend and credentials

* `config/environments/local.yaml` — backend `http://localhost:8080`, test support (reset) enabled;
  the Android APK reaches it as `http://10.0.2.2:8080/`, the iOS app gets `-backendURL http://localhost:8080`.
* `ci.yaml` (CI-local backend), `docker.yaml` (Compose service), `staging.example.yaml` (needs
  `--allow-remote-env`, reset disabled). Production is always refused.
* Passwords come only from `MTF_PASSWORD_CUSTOMER|QA|ADMIN|LOCKED` (`.env` locally, secrets in CI) and
  are referenced in plans as `${secret:accounts.<ref>.password}`; they are redacted everywhere.

## Verification status

Everything below was **actually run** on the development Mac (macOS, Apple Silicon) on 2026-10-21.
"Not run" means not verified, not "failed".

| Check | Result |
|---|---|
| `make check`: ruff, ruff format, mypy `--strict`, 190 unit tests, schema up to date, 26 plans valid | ✅ all passed |
| Android runner: ktlint, Android Lint (warnings as errors), `assembleDebug` of the **runner only** | ✅ clean, builds |
| iOS runner: SwiftLint `--strict`, XcodeGen + `build-for-testing` of the **runner only** | ✅ clean, builds |
| Android, prebuilt `app-debug.apk` (sha256 `b0e8b31a…`, 1.0.0 (1)) installed unchanged and version-verified, API 34 arm64 emulator | ✅ **17/17 plans passed** (`plans/shared` + `plans/android`); scroll-heavy plans re-run 6× green |
| iOS, prebuilt simulator `.app` (1.0 (1)), `xcodebuild test-without-building` of the framework runner, iPhone 17 / iOS 26.5 / Xcode 27.1 | ✅ **16/16 plans passed** (`plans/shared` + `plans/ios`) |
| API, prebuilt `backend.jar` on localhost (in-memory H2, seeded) | ✅ **8/8 plans passed**, also in reverse order |
| Docker: MTF image + the prebuilt backend JAR in Compose (`make docker-test-api`) | ✅ images built, **8/8 API plans passed** in containers |
| Negative checks: wrong expectations fail as `assertion_failed` with screenshot, hierarchy, device and runner logs (Android and iOS); remaining steps skipped, cleanup still runs | ✅ |
| AI flow with the recorded provider: generate → validator rejects an invented task → repaired candidate → rejected while unapproved → approved → executed on Android | ✅ passed |
| Secrets: scan of every run directory for passwords and tokens | ✅ none found (after fixing the XCTest log leak described in `docs/security.md`) |
| Application repository modified | ✅ no changes (`git status` clean) |
| Anthropic provider against the live API | ⚪ not run (no API key configured) |
| GitHub Actions workflows | ⚪ not run (YAML/structure checked locally only) |
| x86_64 emulator (CI image), physical Android/iOS devices, staging environment | ⚪ not run |

Artifact provenance for these runs: the APK and `backend.jar` were the prebuilt outputs already in the
app checkout (built 2026-10-18); no prebuilt iOS `.app` existed, so one was produced explicitly with
`tools/upstream/build-ios-app-artifact.sh` from app commit `5f42818` (the app's own Xcode project,
unmodified) before testing. The framework itself never built the app.

Findings in the application, recorded in the contract rather than worked around: Compose dialog
content is not addressable by UIAutomator in this APK, and `catalog_list` is shadowed on iOS
([docs/test-contract.md](docs/test-contract.md#contract-gaps-found-and-recorded)).

## Known limitations

* **Compose dialogs on Android.** The supplied APK sets `testTagsAsResourceId` only on its root
  composable; dialogs, bottom sheets and dropdown menus are separate windows without resource IDs, so
  UIAutomator cannot address their content. Recorded as `unavailable` in the registry; such plans are
  rejected for Android (dialogs are covered on iOS). Needs an app fix.
* **Compose UI Test is not an integration mode**: it runs in the app's process and would need a test
  APK built and signed against the app — impossible without rebuilding. The black-box UIAutomator
  runner is the Android integration.
* **`catalog_list` on iOS** is shadowed by the screen root's identifier (app issue); the iOS robot
  scrolls the screen instead.
* **iOS: simulator builds only** (no device signing/provisioning). XCUITest needs macOS + Xcode; it
  cannot run in Linux Docker. The Docker image is for the CLI, validation, API tests and reports only.
* **Product search and wishlist have no UI** in the app: product search, filters and wishlist are
  covered by API plans; the UI search plan covers the dashboard's scenario search.
* **Serial execution.** One device, one backend per invocation; parallel runs would need isolated
  devices and data sets. One `mtf run` reuses the prepared session across plans (isolation comes from
  each plan's reset + clean launch); `--fresh-session-per-plan` restores per-plan setup.
* **System UI on iOS** (navigation-bar back button, AutoFill "Save Password?" sheet) has no app
  identifiers; the iOS runner handles exactly those two by system position/label, in English.
* The Anthropic provider was not called live here (no API key); it is covered through the provider
  port and the recorded provider.
* The GitHub workflows were not executed from this environment.
