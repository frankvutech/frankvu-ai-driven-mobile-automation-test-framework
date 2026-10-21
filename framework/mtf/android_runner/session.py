"""Android session: verify + install the supplied APK unchanged, start the UIAutomator runner.

Lifecycle operations that need shell privileges (install, clear data, start activities, deep links)
use adb from the host. Everything inside the app's UI goes through the on-device runner.
"""

from __future__ import annotations

import contextlib
import re
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from mtf import __version__
from mtf.android_runner.adb import Adb, validate_package
from mtf.core.artifacts import inspect_apk
from mtf.core.config import AndroidRunConfig, Environment
from mtf.core.errors import ArtifactMismatch, ConfigurationError, InfrastructureError
from mtf.core.ports import AppLaunchOptions
from mtf.core.remote_driver import RemoteUiCommands
from mtf.core.rpc import RunnerClient, new_token
from mtf.core.types import Platform
from mtf.reporting.models import ArtifactInfo, EvidenceRef, RunnerInfo

RUNNER_PACKAGE = "dev.mtf.androidrunner"
RUNNER_INSTRUMENTATION = f"{RUNNER_PACKAGE}/.RunnerInstrumentation"
DEVICE_PORT = 7912
_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class AndroidUiDriver(RemoteUiCommands):
    platform = Platform.ANDROID

    def __init__(self, client: RunnerClient, adb: Adb, package: str, component: str, logcat_since: str) -> None:
        super().__init__(client)
        self.adb = adb
        self.package = validate_package(package)
        self.component = component
        self.logcat_since = logcat_since

    # app lifecycle (adb) --------------------------------------------------------------------------
    def launch_app(self, options: AppLaunchOptions) -> None:
        if options.clean_state:
            self.adb.shell("pm", "clear", self.package, timeout_s=60)
        self.adb.shell("am", "start", "-W", "-n", self.component, timeout_s=60)
        if options.deep_link:
            self.open_deep_link(options.deep_link)
        self._call("wait_for_idle", {"timeout_ms": 3000})

    def terminate_app(self) -> None:
        self.adb.shell("am", "force-stop", self.package, timeout_s=30)

    def background_app(self, duration_ms: int) -> None:
        self.adb.shell("input", "keyevent", "KEYCODE_HOME", timeout_s=20)
        time.sleep(duration_ms / 1000)
        self.adb.shell("am", "start", "-W", "-n", self.component, timeout_s=60)
        self._call("wait_for_idle", {"timeout_ms": 3000})

    def open_deep_link(self, url: str) -> None:
        if not _SCHEME_RE.match(url):
            raise ConfigurationError(f"not a deep link URL: {url!r}")
        self.adb.shell("am", "start", "-W", "-a", "android.intent.action.VIEW", "-d", url, self.package, timeout_s=60)
        self._call("wait_for_idle", {"timeout_ms": 3000})

    # evidence -------------------------------------------------------------------------------------
    def screenshot(self, path: Path) -> bool:
        self.adb.screencap(path)
        return path.stat().st_size > 0

    def device_logs(self) -> str:
        out = self.adb.run(
            "logcat", "-d", "-v", "threadtime", "-T", self.logcat_since, timeout_s=60, check=False
        ).stdout
        lines = out.splitlines()
        return "\n".join(lines[-3000:])

    def device_info(self) -> dict[str, Any]:
        return self._call("hello", {})

    def close(self) -> None:
        return None


class AndroidSession:
    platform = Platform.ANDROID

    def __init__(
        self, config: AndroidRunConfig, environment: Environment, *, runner_start_s: float | None = None
    ) -> None:
        self.config = config
        self.environment = environment
        self.adb = Adb(config.adb, config.device_serial)
        self.runner_start_s = runner_start_s or environment.timeouts.runner_start_s
        self.package = validate_package(config.app_id or environment.android.app_id)
        self._artifact: ArtifactInfo | None = None
        self._device: dict[str, Any] = {}
        self._driver: AndroidUiDriver | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._closed = False
        self._host_port: int | None = None
        self._client: RunnerClient | None = None
        self._runner_log: Path | None = None
        self._work_dir = Path()  # created by prepare(), removed by close()
        self._logcat_since = ""

    # --- PlatformSession -----------------------------------------------------------------------
    def prepare(self, run_dir: Path) -> None:
        if self.prepared and self._process is not None and self._process.poll() is None:
            # Reused for another plan: the app was installed and verified, the runner is still up.
            assert self._artifact is not None
            self._artifact.extra["session_reused"] = True
            return
        self._artifact = inspect_apk(self.config.apk, aapt2=self.config.aapt2)
        if self._artifact.identifier and self._artifact.identifier != self.package:
            raise ArtifactMismatch(
                f"the APK's package is {self._artifact.identifier}, but the configuration expects {self.package}"
            )
        self._work_dir = Path(tempfile.mkdtemp(prefix="mtf-android-session-"))
        self._select_device()
        self._wait_for_boot()
        self._device = self._device_props()
        self._logcat_since = self.adb.shell("date", "+%m-%d %H:%M:%S.000", timeout_s=20).strip()
        self._install_app()
        self._start_runner(run_dir)
        assert self._client is not None
        component = self._launch_component()
        self._driver = AndroidUiDriver(self._client, self.adb, self.package, component, self._logcat_since)
        self._device.update(
            {f"runner_{k}": v for k, v in self._client.call("hello").items() if k in ("runner_version", "uiautomator")}
        )

    @property
    def prepared(self) -> bool:
        return self._driver is not None

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def driver(self) -> AndroidUiDriver | None:
        return self._driver

    def artifact_info(self) -> ArtifactInfo | None:
        return self._artifact

    def device_info(self) -> dict[str, Any]:
        return dict(self._device)

    def runner_info(self) -> RunnerInfo:
        return RunnerInfo(
            framework_version=__version__,
            runner="android-uiautomator-runner",
            runner_version=str(self._device.get("runner_runner_version", "")) or None,
            extra={"instrumentation": RUNNER_INSTRUMENTATION, "runner_apk": str(self.config.runner_apk)},
        )

    def configuration(self) -> dict[str, Any]:
        return {
            "app_id": self.package,
            "device_serial": self.adb.serial,
            "app_backend_url": self.environment.android.backend_url,
            "reinstall": self.config.reinstall,
        }

    def collect_failure_evidence(self, evidence_dir: Path, prefix: str) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = []
        crash = self.adb.run("logcat", "-d", "-b", "crash", "-T", self._logcat_since, timeout_s=30, check=False).stdout
        if crash.strip():
            path = evidence_dir / f"{prefix}-crash.log"
            path.write_text(crash, encoding="utf-8")
            refs.append(EvidenceRef(kind="crash_log", path=str(path)))
        if self._runner_log and self._runner_log.exists():
            path = evidence_dir / f"{prefix}-runner.log"
            path.write_text(
                "\n".join(self._runner_log.read_text(errors="replace").splitlines()[-200:]), encoding="utf-8"
            )
            refs.append(EvidenceRef(kind="runner_log", path=str(path)))
        return refs

    def app_crashed(self) -> str | None:
        crash = self.adb.run("logcat", "-d", "-b", "crash", "-T", self._logcat_since, timeout_s=30, check=False).stdout
        if f"Process: {self.package}" in crash:
            first = next((line for line in crash.splitlines() if "FATAL EXCEPTION" in line or "Exception" in line), "")
            return f"the app process crashed ({first.strip()[:200]})"
        return None

    def close(self) -> None:
        self._closed = True
        self._driver = None
        if self._client is not None:
            with contextlib.suppress(Exception):  # best effort
                self._client.call("shutdown", timeout_s=5)
        if self._process is not None:
            try:
                self._process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._process.terminate()
        if self._host_port is not None:
            self.adb.remove_forward(self._host_port)
        if self._work_dir != Path():
            shutil.rmtree(self._work_dir, ignore_errors=True)

    # --- steps -----------------------------------------------------------------------------------
    def _select_device(self) -> None:
        devices = self.adb.devices()
        if self.adb.serial:
            if self.adb.serial not in devices:
                raise InfrastructureError(f"device {self.adb.serial} is not connected (connected: {devices or 'none'})")
            return
        if len(devices) != 1:
            raise ConfigurationError(
                f"{len(devices)} devices connected ({', '.join(devices) or 'none'}); set --device to choose one"
            )
        self.adb.serial = devices[0]

    def _wait_for_boot(self) -> None:
        deadline = time.monotonic() + self.runner_start_s
        while self.adb.getprop("sys.boot_completed") != "1":
            if time.monotonic() > deadline:
                raise InfrastructureError(f"device {self.adb.serial} did not finish booting")
            time.sleep(2)

    def _device_props(self) -> dict[str, Any]:
        props = {
            "serial": self.adb.serial,
            "model": self.adb.getprop("ro.product.model"),
            "os": "Android " + self.adb.getprop("ro.build.version.release"),
            "api_level": self.adb.getprop("ro.build.version.sdk"),
            "abi": self.adb.getprop("ro.product.cpu.abi"),
            "emulator": self.adb.getprop("ro.kernel.qemu") == "1" or self.adb.getprop("ro.boot.qemu") == "1",
            "locale": self.adb.getprop("persist.sys.locale") or self.adb.getprop("ro.product.locale"),
        }
        return props

    def _install_app(self) -> None:
        assert self._artifact is not None
        installed_name, installed_code = self.adb.installed_version(self.package)
        same = (installed_name, installed_code) == (self._artifact.version_name, self._artifact.version_code)
        if self.config.reinstall or not same:
            self.adb.install(Path(self._artifact.path))
        installed_name, installed_code = self.adb.installed_version(self.package)
        if installed_name is None:
            raise ArtifactMismatch(f"{self.package} is not installed after installing {self._artifact.path}")
        if self._artifact.version_name and (installed_name, installed_code) != (
            self._artifact.version_name,
            self._artifact.version_code,
        ):
            raise ArtifactMismatch(
                f"installed {self.package} is {installed_name} ({installed_code}), "
                f"the supplied APK is {self._artifact.version_name} ({self._artifact.version_code})"
            )
        self._artifact.installed_verified = True
        self._artifact.extra["installed_version"] = f"{installed_name} ({installed_code})"
        self._artifact.extra["reinstalled"] = self.config.reinstall or not same

    def _start_runner(self, run_dir: Path) -> None:
        runner_apk = self.config.runner_apk
        if not runner_apk.is_file():
            raise ConfigurationError(
                f"framework runner APK not found at {runner_apk}; build it with `make android-runner` "
                "(this builds only the framework's runner, never the app)"
            )
        self.adb.install(runner_apk)
        self.adb.shell("am", "force-stop", RUNNER_PACKAGE, timeout_s=20, check=False)
        token = new_token()
        self._host_port = self.config.runner_port or _free_port()
        self.adb.forward(self._host_port, DEVICE_PORT)
        self._runner_log = self._work_dir / "android-runner.log"
        log = self._runner_log.open("wb")
        command = [
            *self.adb.base_command(),
            "shell",
            f"am instrument -w -e port {DEVICE_PORT} -e token {token} {RUNNER_INSTRUMENTATION}",
        ]
        self._process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        self._client = RunnerClient("127.0.0.1", self._host_port, token)
        self._client.wait_until_ready(
            self.runner_start_s, is_alive=lambda: self._process is not None and self._process.poll() is None
        )

    def _launch_component(self) -> str:
        assert self._artifact is not None
        activity = self.environment.android.launch_activity or self._artifact.extra.get("launchable_activity")
        if not activity:
            resolved = (
                self.adb.shell(
                    "cmd",
                    "package",
                    "resolve-activity",
                    "--brief",
                    "-c",
                    "android.intent.category.LAUNCHER",
                    self.package,
                    timeout_s=20,
                )
                .strip()
                .splitlines()
            )
            if not resolved or "/" not in resolved[-1]:
                raise ConfigurationError(f"cannot resolve the launcher activity of {self.package}")
            return resolved[-1].strip()
        if activity.startswith("."):
            activity = self.package + activity
        return f"{self.package}/{activity}"
