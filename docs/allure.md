# Allure reports

Every run already writes its own `result.json` and `report.html`. Allure adds one browsable report
across many runs and platforms, with history and trends, retries, failure categories and the
environment the app was tested in.

MTF writes the standard Allure results format itself (no `allure-pytest`, no extra Python
dependency); any Allure 2 or Allure 3 CLI can turn it into HTML.

## Writing Allure results

```bash
# while running: one result per plan run, appended to the directory
uv run mtf run plans/api --platform api --alluredir reports/allure-results
uv run mtf run plans/shared plans/android --platform android --apk app-debug.apk \
    --alluredir reports/allure-results --clean-alluredir   # start from an empty directory

# afterwards: from finished runs (run directories, or any directory above them)
uv run mtf report allure reports/runs --alluredir reports/allure-results --clean-alluredir
```

`allure_dir` in a run configuration (`config/run.example.yaml`) sets the default for `--alluredir`.
The `make test-api` / `test-android` / `test-ios` targets always write to `reports/allure-results`
(`ALLURE_RESULTS=...` to change it), so running several platforms produces one combined report.

## Viewing

```bash
make allure-report    # reports/allure-report/index.html (Allure 2 CLI via npx; needs Node and Java 8+)
make allure-serve     # opens it in a browser
make allure-export    # rebuild reports/allure-results from every run in reports/runs
```

`ALLURE=...` selects another CLI, e.g. a local `allure` install or Allure 3:
`make allure-report ALLURE="npx --yes allure@3.21.0"` (Allure 3's `generate` writes to
`./allure-report` unless `-o` is given; it does not use Java). `allure-report` copies the previous
report's `history/` into the results first, so the trend charts grow from one generation to the next.

## How a run maps to Allure

| MTF | Allure |
|---|---|
| one plan run (`result.json`) | one test result; `fullName` = `<platform>.<plan name>` |
| plan title / description | name / description |
| `preconditions` / `cleanup` | *Set up* / *Tear down* fixtures (a container per run) |
| `steps` | steps; a step with more than one attempt has nested `attempt N` steps; API checks are nested steps |
| step evidence (screenshot, hierarchy, device/runner/crash logs) | attachments of that step |
| API exchanges of a step | one JSON attachment of that step |
| run-level evidence, `result.json`, `report.html`, advisory | attachments of the test |
| `passed` / `failed` / `error` / `rejected` / `cancelled` | `passed` / `failed` / `broken` / `broken` / `skipped` |
| failure category | first line of the trace (`failure_category: …`), matched by `categories.json` |
| platform; first feature tag; plan name | suites: parent suite / suite; behaviours: epic / feature / story |
| platform, environment, device, OS, app version and checksum | parameters |
| artifact, device and runner versions per platform; backend URL | `environment.properties` (merged across runs) |
| GitHub Actions run | `executor.json` (build link) |

`historyId` is derived from plan name, platform and environment, so re-running a plan shows up as
retries of one test (a run that passed after a step retry is marked flaky), while the same plan on
another platform or environment is a separate test. Exporting the same run twice overwrites its
files instead of duplicating it.

## Secrets

The exporter reads only the run directory: the redacted `result.json` and evidence that already
went through the final redaction pass of the run (see [security](security.md)). It never contacts
a device or the backend and never reads environment secrets, so an Allure report cannot contain a
value that the run record hides. Evidence paths are resolved inside the run directory; a tampered
`result.json` cannot make the exporter copy other files. Unit tests export failing iOS runs whose
raw runner log contained every configured password and assert that none reaches the Allure output.
