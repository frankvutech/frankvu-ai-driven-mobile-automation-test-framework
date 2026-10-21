#!/usr/bin/env bash
# UPSTREAM ARTIFACT PRODUCER — not part of the test framework.
#
# Produces the prebuilt iOS simulator .app that the framework consumes, from an app checkout, using the
# app's own Xcode project and scheme, unmodified. The framework's test runs never call this script;
# in CI the equivalent step lives in the app repository's pipeline (or the optional
# `upstream-artifacts.yml` workflow), and the test workflows only download its output.
#
# Usage: tools/upstream/build-ios-app-artifact.sh <app-repo> <output-dir> [configuration]
set -euo pipefail
APP_REPO=${1:?app repository path}
OUT=${2:?output directory}
CONFIGURATION=${3:-Debug}   # Debug enables the app's Test Mode and launch arguments used by tests
PROJECT="$APP_REPO/ios/MobileTestPlayground.xcodeproj"
SCHEME=MobileTestPlayground
DERIVED="$OUT/DerivedData"

mkdir -p "$OUT"
xcodebuild build \
  -project "$PROJECT" -scheme "$SCHEME" -configuration "$CONFIGURATION" \
  -destination 'generic/platform=iOS Simulator' \
  -derivedDataPath "$DERIVED" \
  CODE_SIGNING_ALLOWED=NO \
  | tail -n 5

APP="$DERIVED/Build/Products/${CONFIGURATION}-iphonesimulator/MobileTestPlayground.app"
rm -rf "$OUT/MobileTestPlayground.app"
cp -R "$APP" "$OUT/MobileTestPlayground.app"
{
  echo "source_repo=$(git -C "$APP_REPO" remote get-url origin 2>/dev/null || echo unknown)"
  echo "source_commit=$(git -C "$APP_REPO" rev-parse HEAD)"
  echo "configuration=$CONFIGURATION"
  echo "built_at=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "xcode=$(xcodebuild -version | tr '\n' ' ')"
} > "$OUT/MobileTestPlayground.app.provenance"
echo "Artifact: $OUT/MobileTestPlayground.app"
