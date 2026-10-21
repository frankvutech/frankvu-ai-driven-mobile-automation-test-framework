# CI/CD

Two kinds of workflow are kept strictly apart:

* **Consumers** (this framework's test jobs) download prebuilt artifacts and test them. They never run
  the application's build; the only things they build are the framework's own runners.
* **Producers** build the application. That is the app repository's pipeline, or the optional
  `upstream-artifacts.yml` here. Producers are never part of a test job.

| Workflow | Trigger | Kind | What it does |
|---|---|---|---|
| `framework-ci.yml` | push, PR | framework | ruff, ruff format, mypy, unit tests, schema check, plan validation; builds + lints the Android runner APK (ktlint, Android Lint); SwiftLint (strict) and `build-for-testing` of the iOS runner on macOS. Uploads both runners. |
| `api-tests.yml` | dispatch, `workflow_call` | consumer | Downloads `backend.jar`, starts it on the runner with an in-memory seeded database, checks OpenAPI drift, runs `plans/api`. No simulator. |
| `android-tests.yml` | dispatch, `workflow_call` | consumer | Downloads the APK and backend, builds the framework runner APK, starts the backend, boots an API 34 x86_64 emulator (KVM), runs `plans/shared` + `plans/android`. |
| `ios-tests.yml` | dispatch, `workflow_call` | consumer | macOS 15: downloads the simulator `.app` and backend, `build-for-testing` of the framework runner, runs `plans/shared` + `plans/ios` with `test-without-building`. |
| `upstream-artifacts.yml` | dispatch | **producer (optional)** | Checks out the app at a ref, runs the app's own builds (APK, backend JAR, simulator `.app`), uploads them, then calls the three consumer workflows with this run's ID. |

## Inputs

Consumer workflows take `artifacts_repository` (default: the app repository), `artifacts_run_id`
(required), and artifact names that default to what the app's CI publishes (`android-apks`,
`ios-test-results`) plus `mtp-backend-jar` (published by `upstream-artifacts.yml`, because the app's
CI does not publish its backend JAR). Cross-repository downloads use `secrets.ARTIFACTS_READ_TOKEN`
(a fine-grained token with `actions:read` on the app repository) and fall back to `github.token`
within this repository.

## Secrets

| Secret | Use |
|---|---|
| `MTF_PASSWORD_CUSTOMER`, `MTF_PASSWORD_QA`, `MTF_PASSWORD_ADMIN`, `MTF_PASSWORD_LOCKED` | Passwords of the seeded test accounts of the CI-local backend |
| `ARTIFACTS_READ_TOKEN` | Read artifacts from the app repository (optional) |
| `ANTHROPIC_API_KEY` | Not used by CI: plans in the repository run without AI |

## Never production

All consumer jobs start their own backend on `localhost:8080` from the prebuilt JAR, with an in-memory
H2 database and test support enabled, and use the `ci` environment, which only allows loopback hosts.
Production environments are refused by the framework itself, not only by convention.

## Reports

Every consumer job uploads `reports/` (per-run `result.json`, `report.html`, `events.jsonl`,
screenshots, hierarchies, logs, the iOS `.xcresult`) and the backend log, also when tests fail.
`mtf run` exits non-zero when any plan is not `passed`, so the job fails visibly.

## Status

The workflows were written and checked locally for YAML syntax and structure (every job has
`runs-on`/`uses`, every step `run`/`uses`). They have **not been executed on GitHub** from this
environment (no push was made), so treat their first runs as unverified. `actionlint` was not
available locally.
