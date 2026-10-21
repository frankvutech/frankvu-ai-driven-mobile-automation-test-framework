"""Platform sessions (Strategy): how a run gets a device-side runner for its platform.

A session verifies and installs the *prebuilt* application artifact, starts the framework's runner
and hands out a `UiDriver`. It never builds the application under test.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from mtf import __version__
from mtf.core.ports import UiDriver
from mtf.core.types import Platform
from mtf.reporting.models import ArtifactInfo, EvidenceRef, RunnerInfo


class PlatformSession(Protocol):
    platform: Platform

    def prepare(self, run_dir: Path) -> None:
        """Verify the artifact, install it unchanged, start the runner. Raises on failure."""

    @property
    def driver(self) -> UiDriver | None: ...

    def artifact_info(self) -> ArtifactInfo | None: ...

    def device_info(self) -> dict[str, Any]: ...

    def runner_info(self) -> RunnerInfo: ...

    def configuration(self) -> dict[str, Any]:
        """The effective, non-secret configuration recorded in the report."""

    def collect_failure_evidence(self, evidence_dir: Path, prefix: str) -> list[EvidenceRef]:
        """Platform-specific extras on failure (crash logs, runner logs)."""

    def app_crashed(self) -> str | None:
        """A description when the app process died unexpectedly, else None."""

    def close(self) -> None: ...


class ApiOnlySession:
    """API plans need no device: the session only records what was used."""

    platform = Platform.API

    def prepare(self, run_dir: Path) -> None:
        return None

    @property
    def driver(self) -> UiDriver | None:
        return None

    def artifact_info(self) -> ArtifactInfo | None:
        return None

    def device_info(self) -> dict[str, Any]:
        return {}

    def runner_info(self) -> RunnerInfo:
        return RunnerInfo(framework_version=__version__, runner="api-runner (httpx)")

    def configuration(self) -> dict[str, Any]:
        return {}

    def collect_failure_evidence(self, evidence_dir: Path, prefix: str) -> list[EvidenceRef]:
        return []

    def app_crashed(self) -> str | None:
        return None

    def close(self) -> None:
        return None
