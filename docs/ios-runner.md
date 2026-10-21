# iOS runner

```text
host (mtf)                                               simulator
──────────                                               ─────────
IosSession ── simctl boot / install MobileTestPlayground.app ─► com.mobiletestplayground.app (unchanged)
           ── patch .xctestrun (port, token, bundle ID)
           ── xcodebuild test-without-building -xctestrun … -only-testing MTFRunnerUITests/RunnerTests/testServe
                                                           ─► MTFRunnerUITests-Runner (XCTest)
IosUiDriver ── JSON lines to 127.0.0.1:<port> ─────────────► LineServer ─► Commands ─► XCUIApplication(bundleIdentifier:)
           ── simctl io screenshot / log show
```

## How it works

1. **Inspect** the `.app` (`Info.plist`: bundle ID, versions, supported platforms, executable) and hash
   the bundle. A device build is refused (`artifact_mismatch`).
2. **Select and boot** the simulator (`--simulator` name or UDID, optional `--ios-version`).
3. **Install** the `.app` with `simctl install` and **verify** the installed `Info.plist` versions.
4. **Patch** a copy of the runner's `.xctestrun` in the run directory: resolve `__TESTROOT__` and add
   `MTF_RUNNER_PORT`, `MTF_RUNNER_TOKEN`, `MTF_TARGET_BUNDLE_ID` to the test environment. The build
   products are not modified.
5. **Start** `xcodebuild test-without-building`. The single test `testServe` opens a loopback socket
   (the simulator shares the Mac's network stack) and serves commands until `shutdown`.
6. **Drive**: `launch` passes the environment's launch arguments (`-AppleLanguages (en)`, the clean
   state argument `-uiTestReset` when requested, `-backendURL <ios.backend_url>`); deep links use
   `XCUIApplication.open(url)`; background uses the Home button and `activate()`.

The runner's test bundle has no host or target app (`project.yml`), so `build-for-testing` builds only
the runner. **Verified**: `xcodebuild build-for-testing` of the runner and `test-without-building`
against a supplied `.app` on an iPhone 17 simulator, iOS 26.5, Xcode 27.1.

Elements are looked up with `app.descendants(matching: .any).matching(identifier:)` (taps prefer a
button with the identifier, as SwiftUI can put one identifier on a row and its button). XCUITest
"issues" (for example tapping an element that vanished) are captured by overriding `record(_:)` and
returned as command errors instead of failing the serve loop.

| Command | Notes |
|---|---|
| `tap`, `double_tap`, `long_press` | waits for existence and hittability (`element_not_found` otherwise) |
| `type_text`, `clear_text` | taps the field, deletes the current value (placeholder-aware), `typeText`; never echoes |
| `scroll_to` | slow, short swipes on the container (or the app) until the target is hittable, then the other direction |
| `back` | navigation-bar back button (system chrome without an app identifier — documented exception) |
| `dismiss_keyboard` | taps the navigation bar, as the app team's tests do |
| `dismiss_system_dialogs` | declines the AutoFill "Save Password?" sheet with its "Not Now" button (system UI, English locale) |
| `first_visible` | one `XCUIElement.snapshot()` of the app, then the first requested identifier that is on screen |
| `hierarchy` | `XCUIApplication.debugDescription` |

Evidence on failure: `simctl io screenshot`, the element tree, the app's unified log (streamed with
`simctl spawn … log stream` from the start of the session — `log show` afterwards took minutes), new
crash reports (`~/Library/Logs/DiagnosticReports/<app>*.ips`) and the tail of the xcodebuild/XCTest
log. If the app process is no longer running after a failure, the category becomes `app_crash`.

**XCTest records typed text in clear** in its activity log ("Type '…' into …"), so the raw xcodebuild
output and the `.xcresult` bundle contain passwords. They are written to a private temporary
directory that is deleted when the session closes; only redacted copies reach the run directory, and
the `.xcresult` bundle is not kept. (Found during verification: an earlier version copied the raw log
into the report directory.)

## Session reuse

One `mtf run` invocation prepares the simulator, the app and the runner once and reuses them for
every plan (each plan still resets the backend and launches the app clean in its preconditions).
`--fresh-session-per-plan` restores full isolation at the cost of ~40 s per plan. A session whose
runner was abandoned by the watchdog is always discarded.

## Requirements and constraints

* macOS with Xcode and an iOS simulator runtime. Linux (and therefore Docker) cannot run XCUITest.
* The `.app` must be a **simulator** build. No signing is needed for simulators. Running on a physical
  device would need a signed `.ipa`, a development team for the runner and device provisioning — not
  implemented.
* The runner products must be built by an Xcode that can target the installed simulator runtime
  (`make ios-runner`). The runner's deployment target is iOS 17.
* Debug builds of this app honour `-uiTestReset`, `-offlineMode` and `-backendURL`. Without a
  clean-state argument (`ios.clean_state_argument: null`), a clean launch reinstalls the same `.app`.

## Run

```bash
make ios-runner                         # once: build-for-testing of the framework runner
uv run mtf run plans/shared plans/ios --platform ios \
  --app artifacts/ios/MobileTestPlayground.app --simulator "iPhone 17" --ios-version 26.5
```
