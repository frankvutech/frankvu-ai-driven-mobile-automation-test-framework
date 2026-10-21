#!/usr/bin/env bash
# UPSTREAM ARTIFACT PRODUCER — not part of the test framework.
#
# Produces the prebuilt APK the framework consumes, with the app's own Gradle build, unmodified.
# The framework never runs this during test execution; test jobs only download its output.
#
# Usage: tools/upstream/build-android-apk-artifact.sh <app-repo> <output-dir>
set -euo pipefail
APP_REPO=${1:?app repository path}
OUT=${2:?output directory}
mkdir -p "$OUT"
(cd "$APP_REPO/android" && ./gradlew --no-daemon :app:assembleDebug)
cp "$APP_REPO/android/app/build/outputs/apk/debug/app-debug.apk" "$OUT/app-debug.apk"
{
  echo "source_repo=$(git -C "$APP_REPO" remote get-url origin 2>/dev/null || echo unknown)"
  echo "source_commit=$(git -C "$APP_REPO" rev-parse HEAD)"
  echo "built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
} > "$OUT/app-debug.apk.provenance"
echo "Artifact: $OUT/app-debug.apk"
