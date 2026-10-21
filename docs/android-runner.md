# Android runner

```text
host (mtf)                                   emulator / device
──────────                                   ─────────────────
AndroidSession ── adb install -r -t app.apk ─► com.mobiletestplayground.app   (unchanged APK)
               ── adb install runner APK ────► dev.mtf.androidrunner
               ── am instrument -w -e port -e token ─► RunnerInstrumentation
               ── adb forward tcp:<free> tcp:7912                │ UIAutomator (accessibility layer)
AndroidUiDriver ── JSON lines over the forwarded port ─► CommandServer ─► UiCommands ─► app UI
               ── adb: pm clear / am start / am force-stop / screencap / logcat
```

## How it works

1. **Inspect** the APK with `aapt2 dump badging` (package, version, launchable activity) and hash it.
   A package that differs from the configured `app_id` is an `artifact_mismatch`.
2. **Select** the device (`--device`, or the only connected one) and wait for `sys.boot_completed`.
3. **Install** the APK unchanged (`adb install -r -t`) and **verify** with `dumpsys package` that the
   installed version equals the artifact's.
4. **Start** the framework's instrumentation with a per-run token and forward a free host port.
5. **Drive**: lifecycle through adb (clean launch = `pm clear` + `am start -n`, deep links =
   `am start -a VIEW -d`, background = HOME + relaunch); everything inside the UI through the runner.

The runner (`framework/android-runner`, Kotlin, UIAutomator 2.3) looks elements up only with
`By.res("<test id>")`. With `testTagsAsResourceId = true` the Compose tag *is* the resource ID.

| Command | Implementation notes |
|---|---|
| `tap`, `long_press`, `double_tap` | waits for the element, waits for UI idle, re-finds it (the app team's lesson: a node can appear before its screen accepts input), retries on `StaleObjectException` |
| `type_text`, `clear_text` | `UiObject2.setText` (accessibility `ACTION_SET_TEXT`), never echoes the value |
| `scroll_to` | accessibility `ACTION_SCROLL_FORWARD/BACKWARD` on the container (or the first scrollable), then **waits until the content stops moving** before looking (Compose animates these scrolls and reports their result inconsistently). If nothing moved, a swipe gesture inside the container confirms it (an action can be dropped right after a screen opens); only then is it the end of the content. Both behaviours were found on the emulator during verification |
| `swipe`, `drag_to` | `UiObject2.swipe` / `drag` |
| `back`, `dismiss_keyboard` | `pressBack` (keyboard only if an input-method window is shown) |
| `dismiss_system_dialogs` | taps `android:id/aerr_wait` on "isn't responding" dialogs (system resource ID) |
| `wait_for`, `read`, `first_visible` | condition polling, no fixed sleeps; `read` returns text, value, enabled, selected, bounds |
| `hierarchy` | `UiDevice.dumpWindowHierarchy` |

Evidence on failure: screenshot (`adb exec-out screencap`), UI hierarchy XML, logcat since the run
started, the crash buffer, and the instrumentation log. A crash of the app process (`logcat -b crash`)
turns the failure category into `app_crash`.

## Compose UI Test vs. a prebuilt APK — limitations

* **Compose UI Test (`createComposeRule`, `onNodeWithTag`) is not offered as an integration mode.**
  It reads the semantics tree *inside the app process*, which requires an instrumentation APK whose
  `targetPackage` is the app, built against the app's code and signed with the app's key. That
  means rebuilding or re-signing for each supplied APK, which this framework must not do. It is
  therefore not implemented, and no claim is made that it can inspect an arbitrary APK.
* **UIAutomator sees only what the accessibility layer exposes.** In the supplied APK,
  `testTagsAsResourceId` is set on the root composable only. Compose **dialogs, modal bottom sheets
  and dropdown menus** render in separate windows that do not inherit it, so their content has no
  resource IDs (verified on the emulator with `uiautomator dump`). These IDs are marked unavailable
  on Android in the registry, plans needing them are rejected for Android, and
  `plans/android/dialogs_dismiss_with_back.yaml` covers what is observable. The fix belongs in the
  app: apply `Modifier.semantics { testTagsAsResourceId = true }` inside each dialog/sheet/menu.
* Lazy lists compose only visible rows: always `scroll_to` an element before using it.
* Badges and other merged semantics may not carry their own ID (see the app's own automation docs).

## Configuration

| Setting | Where |
|---|---|
| APK path | `--apk`, `android.apk` in the run config |
| Application ID | `--app-id`, `android.app_id`, environment `android.app_id` |
| Launch activity | environment `android.launch_activity` (default: the APK's launchable activity) |
| Device | `--device`, `android.device_serial` (default: the only connected device) |
| Runner APK | `--runner-apk`, `android.runner_apk` |
| Reinstall | `android.reinstall` (default `true`: always install the supplied APK) |
| Element wait | plan `defaults.timeout_ms`, environment `timeouts.default_step_ms` |
| Step watchdog | `--max-step-seconds` |
| Clean state | `launch_app` with `clean_state: true` → `pm clear` (only when the plan asks for it) |

## Requirements

* Android SDK platform-tools (`adb`) and build-tools (`aapt2`, optional but recommended).
* An emulator or device, API 26+. Verified locally on an API 34 arm64 emulator (AVD `shoptest`).
* JDK 17+ to build the runner (`make android-runner`).
* Emulators do **not** run in this repository's Docker image.

## Run

```bash
make android-runner                                   # once (framework runner only)
uv run mtf run plans/shared plans/android --platform android \
  --apk artifacts/android/app-debug.apk --device emulator-5554
```
