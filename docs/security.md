# Security and safety

## No arbitrary code

* Plans are YAML/JSON parsed with `yaml.safe_load` (no object construction) into pydantic models with
  `extra = forbid`. Step kinds, interactions, tasks, questions and fixtures form a closed set mapped
  to code at import time; there is no `eval`, plugin loading or shell step.
* References are plain dotted lookups (`${products.product_001.name}`); no expressions.
* Device runners accept a closed command set; element arguments must match the test-ID pattern.
  Host-side adb/simctl/xcodebuild calls use argument lists (never a shell string built from plan
  text); package and bundle IDs are validated; adb shell arguments are quoted.
* The AI planner returns data that goes through the same validation as hand-written plans plus a
  mandatory human approval bound to the content hash.

## Secrets

* Passwords are never stored in the repository: `contracts/test-data.yaml` names an environment
  variable per account (`MTF_PASSWORD_CUSTOMER`, …). Locally they come from a git-ignored `.env`
  (template: `config/.env.example`); in CI from repository secrets.
* Plans may only *reference* secrets (`${secret:accounts.customer.password}`). The validator rejects
  literal passwords in password parameters and sensitive fields, JWT/bearer/API-key shaped strings,
  and literals equal to known secret values. A run whose secrets are not configured is rejected
  before anything starts.
* The **redactor** (`core/redaction.py`) knows every resolved secret and token of the run and masks
  JWTs, bearer tokens, provider keys and sensitive JSON keys/headers. Everything written — JSON and
  HTML reports, the event log, API exchanges, UI hierarchies, device logs, error messages, AI prompts —
  passes through it. Unit tests assert that no secret value or token appears in any run file.
* Runners never echo typed text (`type_text` returns only the length). Text typed into a registered
  sensitive field is registered with the redactor.
* Raw runner output can still contain secrets — XCTest logs every `typeText` in clear. Session-level
  raw files (xcodebuild/instrumentation output, the patched `.xctestrun` with the runner token, the
  `.xcresult`, the app log stream) live in a private temporary directory removed when the session
  closes. Evidence copied from them is redacted, and at the end of every run every text file in the
  run directory is passed through the redactor again.
* **Screenshots**: password fields are masked by both platforms. After a tap on an element marked
  `reveals_secret` (`login_password_toggle`), screenshots are suppressed until the next app launch,
  and the report says so.
* The AI planner refuses requests containing a secret value; the advisor receives only a redacted
  summary.

## Environments

* `kind: production` is always refused; environment names or hosts with `prod`, `prd`, `production`
  or `live` tokens are refused.
* `local` and `ci` environments must use loopback hosts (or declared `private_hosts` such as a Compose
  service). Anything else must be `staging` and needs `--allow-remote-env` or `MTF_ALLOW_REMOTE_ENV=1`.
* `backend.allowed_hosts` pins the backend host; redirects are not followed.
* Reset/test-support endpoints require `backend.test_support: true` (off in the staging template);
  enabling them on a non-local environment produces a warning in the report.

## Device runner channel

* The Android runner listens on the device's loopback and is reached through `adb forward`; the iOS
  runner listens on the Mac's loopback (shared by the simulator). Neither is exposed to the network.
* Each run generates a 128-bit token; every request must carry it (constant-time comparison).
* Runners stop when the run ends (`shutdown`), or when the instrumentation/xcodebuild process ends.

## Reports

Reports can contain screenshots of the app and backend responses. They are written under `reports/`
(git-ignored); CI uploads them as workflow artifacts with the repository's retention policy.
