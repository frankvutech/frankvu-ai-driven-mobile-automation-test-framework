# Prebuilt application artifacts

The framework consumes artifacts that were built elsewhere. It never runs the application's Gradle
or Xcode build during a test run and never edits the application's source.

## What each platform needs

| Platform | Input | Format | Produced by |
|---|---|---|---|
| Android | Application APK | Installable `.apk` (debug build for this app: Test Mode, test tags exposed as resource IDs, backend `http://10.0.2.2:8080/`) | The app's pipeline: `android-apks` artifact of the app repo's `android.yml`, or `tools/upstream/build-android-apk-artifact.sh` |
| Android | Framework runner APK | `runner-debug.apk` | `make android-runner` (builds only `framework/android-runner`) or the `mtf-android-runner` artifact of `framework-ci.yml` |
| iOS | Application | **Simulator** `.app` bundle (`CFBundleSupportedPlatforms = iPhoneSimulator`), Debug configuration | The app's pipeline: `.app` inside the `ios-test-results` artifact of the app repo's `ios.yml`, or `tools/upstream/build-ios-app-artifact.sh` |
| iOS | Framework runner build products | `MTFRunner_*.xctestrun` + `Debug-iphonesimulator/MTFRunnerUITests-Runner.app` | `make ios-runner` (`build-for-testing` of `framework/ios-runner` only) or the `mtf-ios-runner` artifact of `framework-ci.yml` |
| API / UI | Backend | `backend.jar` (fat JAR) or any running instance of the backend | The app's backend build (`buildFatJar`); `upstream-artifacts.yml` publishes it as `mtp-backend-jar` |

The runner artifacts are the framework's own test code. They are built separately from the
application and do not contain, link or depend on it:

* the Android runner is self-instrumenting (`android:targetPackage` is its own package) and drives the
  app as a black box through UIAutomator, so the app can be signed with any key;
* the iOS runner is a UI-test bundle without a host or target application; it attaches to the
  installed app with `XCUIApplication(bundleIdentifier:)`.

## What is recorded for every run

`result.json` → `artifact` and `device`:

* absolute artifact path, SHA-256 (APK file; for `.app` bundles a deterministic digest over relative
  paths and file contents), size;
* package / bundle ID, version name and code (from `aapt2 dump badging` or `Info.plist`), min/target
  SDK or minimum OS, debuggable flag / supported platforms;
* `installed_verified: true` only when the version installed on the device equals the artifact's;
  otherwise the run stops with `artifact_mismatch` before any step;
* device serial/UDID, model, OS version, API level, ABI, locale; runner versions;
* environment, backend URL, the app-side backend URL and launch arguments.

## Supplying artifacts locally

```bash
mkdir -p artifacts/android artifacts/ios artifacts/backend      # git-ignored
cp <somewhere>/app-debug.apk artifacts/android/
cp -R <somewhere>/MobileTestPlayground.app artifacts/ios/
cp <somewhere>/backend.jar artifacts/backend/
uv run mtf inspect-artifact artifacts/android/app-debug.apk       # read-only metadata + checksum
```

Or pass any path: `--apk`, `--app`, or a run-configuration file:

```yaml
# config/run.local.yaml (git-ignored)
environment: local
android:
  apk: /path/to/app-debug.apk
  app_id: com.mobiletestplayground.app
  device_serial: emulator-5554
ios:
  app: /path/to/MobileTestPlayground.app
  simulator: iPhone 17
  os_version: "26.5"
```

`uv run mtf run plans/shared --platform android --run-config config/run.local.yaml`

## Producing artifacts (outside the framework)

`tools/upstream/` holds two **upstream producer** scripts. They call the app's own build on an app
checkout and copy the output, with a provenance file (source commit, configuration, toolchain). They
are not called by `mtf`, the Makefile's test targets or the test workflows.

```bash
tools/upstream/build-android-apk-artifact.sh ../frankvu-mobile-test-lab artifacts/android
tools/upstream/build-ios-app-artifact.sh     ../frankvu-mobile-test-lab artifacts/ios
```

For the verification recorded in the README, the APK was the one already present in the app checkout
(built 2026-10-18), the iOS `.app` was produced with `build-ios-app-artifact.sh` from commit
`5f42818` because no prebuilt `.app` existed on the machine, and the backend was the app checkout's
existing `backend.jar`.

## Constraints

* **iOS simulator only.** A device `.app`/`.ipa` needs signing, provisioning and a physical device;
  the session refuses non-simulator builds with `artifact_mismatch`. The XCUITest runner itself
  could run on a device if signed with a team; this is not implemented or verified.
* **Debug builds.** This app enables Test Mode, `-uiTestReset` and test tags as resource IDs in Debug
  builds. A release build may hide them; the contract would then fail at run time, visibly.
* **Backend URL baked into the APK.** The Android APK calls `http://10.0.2.2:8080/` (the host's
  port 8080 from an emulator). Run the backend there, or change the URL in the app's Test Mode UI.
  The iOS app accepts `-backendURL <url>`, which the environment's `ios.backend_url` supplies.
