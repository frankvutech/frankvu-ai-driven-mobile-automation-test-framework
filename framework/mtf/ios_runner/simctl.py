"""`xcrun simctl` wrapper: simulator selection, boot, install, app metadata, screenshots, logs."""

from __future__ import annotations

import json
import plistlib
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mtf.core.errors import ConfigurationError, InfrastructureError

_UDID_RE = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
_BUNDLE_RE = re.compile(r"^[A-Za-z0-9\-.]+$")


def validate_bundle_id(bundle_id: str) -> str:
    if not _BUNDLE_RE.match(bundle_id):
        raise ConfigurationError(f"invalid bundle identifier {bundle_id!r}")
    return bundle_id


@dataclass(frozen=True)
class Simulator:
    udid: str
    name: str
    runtime: str  # e.g. "iOS 26.5"
    state: str


def _run(args: list[str], timeout_s: float = 120, check: bool = True) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["xcrun", "simctl", *args], capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except FileNotFoundError as exc:
        raise ConfigurationError("xcrun not found: iOS runs need macOS with Xcode") from exc
    except subprocess.TimeoutExpired as exc:
        raise InfrastructureError(f"simctl {' '.join(args[:2])} timed out after {timeout_s}s") from exc
    if check and result.returncode != 0:
        raise InfrastructureError(
            f"simctl {' '.join(args[:2])} failed: {(result.stderr or result.stdout).strip()[:500]}"
        )
    return result


def list_simulators() -> list[Simulator]:
    data = json.loads(_run(["list", "devices", "available", "-j"], timeout_s=60).stdout)
    out = []
    for runtime_id, devices in data.get("devices", {}).items():
        match = re.search(r"SimRuntime\.(\w+)-(\d+)-(\d+)", runtime_id)
        runtime = f"{match.group(1)} {match.group(2)}.{match.group(3)}" if match else runtime_id
        for device in devices:
            out.append(Simulator(device["udid"], device["name"], runtime, device["state"]))
    return out


def select_simulator(name_or_udid: str, os_version: str | None) -> Simulator:
    candidates = [s for s in list_simulators() if s.runtime.startswith("iOS")]
    if _UDID_RE.match(name_or_udid):
        found = [s for s in candidates if s.udid == name_or_udid]
    else:
        found = [
            s for s in candidates if s.name == name_or_udid and (os_version is None or s.runtime == f"iOS {os_version}")
        ]
    if not found:
        available = sorted({f"{s.name} ({s.runtime})" for s in candidates})
        raise ConfigurationError(
            f"no available simulator {name_or_udid!r} {os_version or ''}; available: {', '.join(available[:12])}"
        )
    # Prefer a booted one, then the newest runtime.
    found.sort(key=lambda s: (s.state != "Booted", [-int(p) for p in re.findall(r"\d+", s.runtime)]))
    return found[0]


def boot(udid: str, timeout_s: float = 240) -> None:
    _run(["boot", udid], check=False)  # "already booted" is fine
    _run(["bootstatus", udid, "-b"], timeout_s=timeout_s)


def install(udid: str, app: Path) -> None:
    _run(["install", udid, str(app)], timeout_s=300)


def uninstall(udid: str, bundle_id: str) -> None:
    _run(["uninstall", udid, validate_bundle_id(bundle_id)], check=False)


def installed_info(udid: str, bundle_id: str) -> dict[str, Any] | None:
    result = _run(["get_app_container", udid, validate_bundle_id(bundle_id), "app"], check=False)
    if result.returncode != 0:
        return None
    plist = Path(result.stdout.strip()) / "Info.plist"
    with plist.open("rb") as handle:
        data: dict[str, Any] = plistlib.load(handle)
    return data


def screenshot(udid: str, path: Path) -> None:
    _run(["io", udid, "screenshot", "--type=png", str(path)], timeout_s=60)


def open_url(udid: str, url: str) -> None:
    _run(["openurl", udid, url], timeout_s=60)


def start_log_stream(udid: str, process: str, path: Path) -> subprocess.Popen[bytes]:
    """Streams the app process's unified log into `path` (much faster than `log show` afterwards)."""
    handle = path.open("wb")
    return subprocess.Popen(
        [
            "xcrun",
            "simctl",
            "spawn",
            udid,
            "log",
            "stream",
            "--style",
            "compact",
            "--level",
            "info",
            "--predicate",
            f'process == "{process}"',
        ],
        stdout=handle,
        stderr=subprocess.STDOUT,
    )


def device_details(udid: str) -> dict[str, Any]:
    for sim in list_simulators():
        if sim.udid == udid:
            return {
                "udid": sim.udid,
                "name": sim.name,
                "os": sim.runtime,
                "state": sim.state,
                "simulator": True,
            }
    return {"udid": udid}
