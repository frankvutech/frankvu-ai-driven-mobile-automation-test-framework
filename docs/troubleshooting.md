# Troubleshooting

| Symptom | Category | Cause and fix |
|---|---|---|
| `rejected` with `selector_contract_violation` | plan | The ID is not in `contracts/test-ids.yaml`, or not addressable on that platform (the message says why). Use a registered ID, restrict the step with `platforms:`, or re-import the contract after the app team fixes the gap. |
| `rejected` with `approval_required` / `approval_stale` | plan | AI-generated plan without approval, or edited after approval: review and `mtf plan approve`. |
| `rejected`: `missing secret environment variable(s)` | config | Create `.env` from `config/.env.example` or export `MTF_PASSWORD_*`. |
| `environment refused: … production` / `not local` | config | Production is never allowed. For a shared test backend use a `staging` environment and `--allow-remote-env`. |
| `artifact_mismatch` | artifact | Package/bundle ID differs from the configuration, the `.app` is not a simulator build, or the installed version differs from the supplied artifact (a stale install that could not be replaced). |
| `framework runner APK not found` | setup | `make android-runner` (builds only the framework runner). |
| `runner .xctestrun not found` | setup | `make ios-runner` (`build-for-testing` of the framework runner). |
| `runner not ready after …s` (iOS) | infra | Read `reports/runs/<id>/ios-runner.log`. Typical: the simulator runtime is not supported by the Xcode that built the runner, or a stale xcodebuild still holds the simulator (`killall xcodebuild`). |
| `N devices connected` | config | Pass `--device <serial>`. |
| App shows "Cannot reach the server" | env | Start the backend on the host's port 8080. The Android APK calls `http://10.0.2.2:8080/`; the iOS app uses `ios.backend_url` from the environment. |
| `element_not_found` right after navigation | app/test | Check the screenshot and hierarchy in the report. Lazy lists only contain visible rows — `scroll_to` first. On Android, content of dialogs/sheets/menus is not addressable (see `test-contract.md`). |
| Login takes ~13 s on iOS | expected | XCUITest types character by character and the app has a fixed loading state; the iOS robot also waits for and declines the AutoFill "Save Password?" sheet. |
| iOS steps fail behind a "Save Password?" sheet | system UI | The `login` task declines the sheet; `submit_login` does not (it is meant for logins expected to fail). Use `login` for successful sign-ins. |
| UIAutomator does not scroll on a slow emulator | infra | The runner waits for the scroll animation to settle and falls back to swipes; increase `max_swipes` or the element timeout, and disable emulator animations in CI. |
| "Pixel Launcher isn't responding" | infra | The runner taps "Wait" on ANR dialogs (system resource ID `android:id/aerr_wait`). |
| Step ends with `timeout` and later steps are skipped | infra | The step watchdog (`--max-step-seconds`) abandoned the runner. Look at the runner log in the run directory. |
| `api_contract_violation` | product/contract | The response does not match the OpenAPI schema for its status. Either the backend broke its contract or the contract is stale: `mtf contracts check-openapi --base-url …`. |
| `undeclared_status` during validation | plan | The plan expects a status the contract does not declare for that operation. |
| `docker compose` cannot reach Docker | env | Start Docker Desktop (`open -a Docker`). Docker is only used for the CLI/API image, never for emulators or XCUITest. |

Every run directory (`reports/runs/<run-id>/`) contains `report.html`, `result.json`,
`events.jsonl`, `evidence/` and the runner log; start there.
