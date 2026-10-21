"""Inspection of prebuilt application artifacts (read-only): checksums and version metadata."""

from __future__ import annotations

import hashlib
import os
import plistlib
import re
import shutil
import subprocess
from pathlib import Path

from mtf.core.errors import ArtifactMismatch, ConfigurationError
from mtf.core.types import Platform
from mtf.reporting.models import ArtifactInfo


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def sha256_tree(root: Path) -> str:
    """Deterministic digest of a directory bundle (.app): relative paths and file contents."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file() and not p.is_symlink()):
        digest.update(str(path.relative_to(root)).encode("utf-8") + b"\0")
        digest.update(sha256_file(path).encode("ascii"))
    return digest.hexdigest()


def find_aapt2(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    found = shutil.which("aapt2")
    if found:
        return found
    for sdk in _sdk_roots():
        tools = sorted((sdk / "build-tools").glob("*/aapt2"), reverse=True)
        if tools:
            return str(tools[0])
    return None


def _sdk_roots() -> list[Path]:
    roots = [
        os.environ.get("ANDROID_HOME"),
        os.environ.get("ANDROID_SDK_ROOT"),
        str(Path.home() / "Library/Android/sdk"),
    ]
    return [Path(r) for r in roots if r and Path(r).is_dir()]


_BADGING_PACKAGE = re.compile(r"package: name='([^']+)' versionCode='([^']*)' versionName='([^']*)'")
_BADGING_SDK = re.compile(r"(sdkVersion|targetSdkVersion):'(\d+)'")
_BADGING_ACTIVITY = re.compile(r"launchable-activity: name='([^']+)'")


def inspect_apk(apk: Path, *, aapt2: str | None = None) -> ArtifactInfo:
    if not apk.is_file():
        raise ConfigurationError(f"APK not found: {apk}")
    info = ArtifactInfo(
        platform=Platform.ANDROID,
        path=str(apk.resolve()),
        kind="apk",
        sha256=sha256_file(apk),
        size_bytes=apk.stat().st_size,
    )
    tool = find_aapt2(aapt2)
    if tool is None:
        info.extra["metadata"] = "aapt2 not found: package and version read from the device after install"
        return info
    result = subprocess.run(
        [tool, "dump", "badging", str(apk)], capture_output=True, text=True, timeout=60, check=False
    )
    if result.returncode != 0:
        raise ArtifactMismatch(f"aapt2 cannot read {apk}: {result.stderr.strip()[:300]}")
    package = _BADGING_PACKAGE.search(result.stdout)
    if package:
        info.identifier, info.version_code, info.version_name = package.groups()
    info.extra.update(dict(_BADGING_SDK.findall(result.stdout)))
    activity = _BADGING_ACTIVITY.search(result.stdout)
    if activity:
        info.extra["launchable_activity"] = activity.group(1)
    info.extra["debuggable"] = "application-debuggable" in result.stdout
    return info


def inspect_ios_app(app: Path) -> ArtifactInfo:
    plist_path = app / "Info.plist"
    if not app.is_dir() or not plist_path.is_file():
        raise ConfigurationError(f"not an .app bundle (no Info.plist): {app}")
    with plist_path.open("rb") as handle:
        plist = plistlib.load(handle)
    platforms = plist.get("CFBundleSupportedPlatforms", [])
    return ArtifactInfo(
        platform=Platform.IOS,
        path=str(app.resolve()),
        kind="app",
        sha256=sha256_tree(app),
        identifier=plist.get("CFBundleIdentifier"),
        version_name=plist.get("CFBundleShortVersionString"),
        version_code=plist.get("CFBundleVersion"),
        size_bytes=sum(p.stat().st_size for p in app.rglob("*") if p.is_file()),
        extra={
            "supported_platforms": platforms,
            "minimum_os": plist.get("MinimumOSVersion"),
            "executable": plist.get("CFBundleExecutable"),
            "dt_sdk": plist.get("DTSDKName"),
            "simulator_build": any("Simulator" in p for p in platforms),
        },
    )
