"""Reads and patches the runner's `.xctestrun` (the build products of `build-for-testing`).

The patched copy injects the per-run configuration (port, token, target bundle ID) as environment
variables and resolves `__TESTROOT__` to absolute paths, so it can live in the run directory while
the build products stay untouched.
"""

from __future__ import annotations

import plistlib
from pathlib import Path
from typing import Any

from mtf.core.errors import ConfigurationError

RUNNER_TARGET = "MTFRunnerUITests"
SERVE_TEST = "MTFRunnerUITests/RunnerTests/testServe"


def _targets(document: dict[str, Any]) -> list[dict[str, Any]]:
    meta = document.get("__xctestrun_metadata__", {})
    if meta.get("FormatVersion") == 2:
        return [t for config in document.get("TestConfigurations", []) for t in config.get("TestTargets", [])]
    return [v for k, v in document.items() if k != "__xctestrun_metadata__" and isinstance(v, dict)]


def _resolve(value: Any, testroot: str) -> Any:
    if isinstance(value, str):
        return value.replace("__TESTROOT__", testroot)
    if isinstance(value, list):
        return [_resolve(v, testroot) for v in value]
    if isinstance(value, dict):
        return {k: _resolve(v, testroot) for k, v in value.items()}
    return value


def patch_xctestrun(source: Path, destination: Path, environment: dict[str, str], *, app_process: str | None) -> Path:
    if not source.is_file():
        raise ConfigurationError(
            f"runner .xctestrun not found at {source}; build it with `make ios-runner` "
            "(build-for-testing of the framework runner only — never the app)"
        )
    with source.open("rb") as handle:
        document: dict[str, Any] = plistlib.load(handle)
    document = _resolve(document, str(source.parent.resolve()))
    targets = _targets(document)
    if not targets:
        raise ConfigurationError(f"{source} contains no test targets")
    for target in targets:
        for key in ("EnvironmentVariables", "TestingEnvironmentVariables"):
            target.setdefault(key, {}).update(environment)
        if app_process:
            names = target.setdefault("ProcessNamesForCrashReportCollection", [])
            if app_process not in names:
                names.append(app_process)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("wb") as handle:
        plistlib.dump(document, handle)
    return destination


def find_xctestrun(products_dir: Path) -> Path:
    candidates = sorted(products_dir.glob("*.xctestrun"))
    if not candidates:
        raise ConfigurationError(f"no .xctestrun in {products_dir}; run `make ios-runner`")
    return candidates[-1]
