"""A small, explicit adb wrapper. Every command is an argument list; nothing is built from plan text."""

from __future__ import annotations

import re
import shlex
import subprocess
from pathlib import Path

from mtf.core.errors import ConfigurationError, InfrastructureError

_PACKAGE_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z][A-Za-z0-9_]*)+$")


def validate_package(name: str) -> str:
    if not _PACKAGE_RE.match(name):
        raise ConfigurationError(f"invalid Android package name {name!r}")
    return name


class Adb:
    def __init__(self, adb: str = "adb", serial: str | None = None) -> None:
        self.adb = adb
        self.serial = serial

    def base_command(self) -> list[str]:
        return [self.adb, *(["-s", self.serial] if self.serial else [])]

    def run(self, *args: str, timeout_s: float = 60, check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                [*self.base_command(), *args], capture_output=True, text=True, timeout=timeout_s, check=False
            )
        except FileNotFoundError as exc:
            raise ConfigurationError(f"adb not found ({self.adb}); install Android platform-tools") from exc
        except subprocess.TimeoutExpired as exc:
            raise InfrastructureError(f"adb {' '.join(args[:3])} timed out after {timeout_s}s") from exc
        if check and result.returncode != 0:
            raise InfrastructureError(
                f"adb {' '.join(args[:3])} failed: {(result.stderr or result.stdout).strip()[:500]}"
            )
        return result

    def run_bytes(self, *args: str, timeout_s: float = 60) -> bytes:
        try:
            result = subprocess.run([*self.base_command(), *args], capture_output=True, timeout=timeout_s, check=False)
        except subprocess.TimeoutExpired as exc:
            raise InfrastructureError(f"adb {' '.join(args[:3])} timed out") from exc
        if result.returncode != 0:
            raise InfrastructureError(
                f"adb {' '.join(args[:3])} failed: {result.stderr.decode(errors='replace')[:300]}"
            )
        return result.stdout

    def shell(self, *args: str, timeout_s: float = 60, check: bool = True) -> str:
        # adb joins shell arguments with spaces and the device shell re-parses them: quote each one.
        return self.run("shell", " ".join(shlex.quote(a) for a in args), timeout_s=timeout_s, check=check).stdout

    def devices(self) -> list[str]:
        out = Adb(self.adb).run("devices", timeout_s=20).stdout
        return [line.split()[0] for line in out.splitlines()[1:] if line.strip().endswith("device")]

    def getprop(self, name: str) -> str:
        return self.shell("getprop", name, timeout_s=20).strip()

    def install(self, apk: Path, *, timeout_s: float = 300) -> None:
        # -r replace, -t allow test-only builds, -g grant runtime permissions declared by the manifest is
        # deliberately NOT used: permission behaviour is part of what the app's tests may check.
        out = self.run("install", "-r", "-t", str(apk), timeout_s=timeout_s, check=False)
        if out.returncode != 0 or "Success" not in out.stdout:
            raise InfrastructureError(f"installing {apk.name} failed: {(out.stdout + out.stderr).strip()[:500]}")

    def installed_version(self, package: str) -> tuple[str | None, str | None]:
        dump = self.shell("dumpsys", "package", validate_package(package), timeout_s=30, check=False)
        name = re.search(r"versionName=(\S+)", dump)
        code = re.search(r"versionCode=(\d+)", dump)
        return (name.group(1) if name else None, code.group(1) if code else None)

    def forward(self, local_port: int, device_port: int) -> None:
        self.run("forward", f"tcp:{local_port}", f"tcp:{device_port}", timeout_s=20)

    def remove_forward(self, local_port: int) -> None:
        self.run("forward", "--remove", f"tcp:{local_port}", timeout_s=20, check=False)

    def screencap(self, path: Path) -> None:
        path.write_bytes(self.run_bytes("exec-out", "screencap", "-p", timeout_s=30))

    def pidof(self, package: str) -> str | None:
        out = self.shell("pidof", validate_package(package), timeout_s=15, check=False).strip()
        return out or None
