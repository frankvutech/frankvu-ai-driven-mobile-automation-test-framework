"""iOS session: verify + install the supplied simulator .app unchanged, then start the framework's
prebuilt XCUITest runner with `xcodebuild test-without-building` and drive the app through it.

No build happens here: `test-without-building` only runs existing build products (the framework
runner's), and the application under test is installed from the supplied .app with simctl.
"""

from __future__ import annotations

import contextlib
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from mtf import __version__
from mtf.core.artifacts import inspect_ios_app
from mtf.core.config import Environment, IosRunConfig
from mtf.core.errors import ArtifactMismatch, ConfigurationError, InfrastructureError
from mtf.core.ports import AppLaunchOptions
from mtf.core.remote_driver import RemoteUiCommands
from mtf.core.rpc import RunnerClient, new_token
from mtf.core.types import Platform
from mtf.ios_runner import simctl
from mtf.ios_runner.xctestrun import SERVE_TEST, find_xctestrun, patch_xctestrun
from mtf.reporting.models import ArtifactInfo, EvidenceRef, RunnerInfo

DEFAULT_PRODUCTS = Path("framework/ios-runner/build/DerivedData/Build/Products")


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class IosUiDriver(RemoteUiCommands):
    platform = Platform.IOS

    def __init__(self, client: RunnerClient, session: IosSession) -> None:
        super().__init__(client)
        self.session = session

    def launch_app(self, options: AppLaunchOptions) -> None:
        self.session.launch(options)

    def terminate_app(self) -> None:
        self._call("terminate", {}, 30_000)

    def background_app(self, duration_ms: int) -> None:
        self._call("background", {"duration_ms": duration_ms}, duration_ms + 30_000)

    def open_deep_link(self, url: str) -> None:
        self._call("open_url", {"url": url}, 30_000)

    def screenshot(self, path: Path) -> bool:
        simctl.screenshot(self.session.udid, path)
        return path.stat().st_size > 0

    def device_logs(self) -> str:
        return self.session.app_log_tail()

    def device_info(self) -> dict[str, Any]:
        return self._call("hello", {})

    def close(self) -> None:
        return None


class IosSession:
    platform = Platform.IOS

    def __init__(self, config: IosRunConfig, environment: Environment, *, root: Path | None = None) -> None:
        self.config = config
        self.environment = environment
        self.root = root or Path.cwd()
        self.bundle_id = simctl.validate_bundle_id(config.bundle_id or environment.ios.bundle_id)
        self.udid = ""
        self.app_process = ""
        self._log_stream: subprocess.Popen[bytes] | None = None
        # Raw session files live in a private directory deleted on close: XCTest's activity log (in the
        # xcodebuild output and the .xcresult) records typed text, passwords included, in clear.
        self._work_dir = Path()  # created by prepare(), removed by close()
        self._artifact: ArtifactInfo | None = None
        self._device: dict[str, Any] = {}
        self._client: RunnerClient | None = None
        self._driver: IosUiDriver | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._closed = False
        self._runner_log: Path | None = None
        self._xctestrun: Path | None = None
        self._started = time.time()
        self._launched = False

    # --- PlatformSession -----------------------------------------------------------------------
    def prepare(self, run_dir: Path) -> None:
        if self.prepared and self._process is not None and self._process.poll() is None:
            # Reused for another plan: the app was installed and verified, the runner is still up.
            assert self._artifact is not None
            self._artifact.extra["session_reused"] = True
            return
        self._artifact = inspect_ios_app(self.config.app)
        if self._artifact.identifier != self.bundle_id:
            raise ArtifactMismatch(f"the .app's bundle ID is {self._artifact.identifier}, expected {self.bundle_id}")
        if not self._artifact.extra.get("simulator_build"):
            raise ArtifactMismatch(
                f"{self.config.app} is not a simulator build (CFBundleSupportedPlatforms="
                f"{self._artifact.extra.get('supported_platforms')}); physical devices need a signed build"
            )
        self.app_process = str(self._artifact.extra.get("executable") or "")
        self._work_dir = Path(tempfile.mkdtemp(prefix="mtf-ios-session-"))
        simulator = simctl.select_simulator(self.config.simulator, self.config.os_version)
        self.udid = simulator.udid
        simctl.boot(self.udid, timeout_s=self.environment.timeouts.runner_start_s)
        self._device = simctl.device_details(self.udid)
        if self.app_process:
            self._log_stream = simctl.start_log_stream(self.udid, self.app_process, self._work_dir / "app.log")
        self._install()
        self._start_runner(run_dir)
        assert self._client is not None
        self._driver = IosUiDriver(self._client, self)
        hello = self._client.call("hello")
        self._device.update(
            {"runner_version": hello.get("runner_version"), "system_version": hello.get("system_version")}
        )

    @property
    def prepared(self) -> bool:
        return self._driver is not None

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def driver(self) -> IosUiDriver | None:
        return self._driver

    def artifact_info(self) -> ArtifactInfo | None:
        return self._artifact

    def device_info(self) -> dict[str, Any]:
        return dict(self._device)

    def runner_info(self) -> RunnerInfo:
        return RunnerInfo(
            framework_version=__version__,
            runner="ios-xcuitest-runner (xcodebuild test-without-building)",
            runner_version=str(self._device.get("runner_version") or "") or None,
            extra={"xctestrun": str(self._xctestrun) if self._xctestrun else None},
        )

    def configuration(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "simulator": self.config.simulator,
            "os_version": self.config.os_version or self._device.get("os"),
            "app_backend_url": self.environment.ios.backend_url,
            "launch_arguments": " ".join(self._launch_arguments(clean_state=False)),
        }

    def collect_failure_evidence(self, evidence_dir: Path, prefix: str) -> list[EvidenceRef]:
        refs: list[EvidenceRef] = []
        reports = Path.home() / "Library/Logs/DiagnosticReports"
        if self.app_process and reports.is_dir():
            for crash in sorted(reports.glob(f"{self.app_process}*.ips")):
                if crash.stat().st_mtime >= self._started:
                    target = evidence_dir / f"{prefix}-{crash.name}"
                    target.write_bytes(crash.read_bytes())
                    refs.append(EvidenceRef(kind="crash_log", path=str(target)))
        if self._runner_log and self._runner_log.exists():
            path = evidence_dir / f"{prefix}-xctest.log"
            path.write_text(
                "\n".join(self._runner_log.read_text(errors="replace").splitlines()[-300:]), encoding="utf-8"
            )
            refs.append(EvidenceRef(kind="xctest_log", path=str(path)))
        return refs

    def app_crashed(self) -> str | None:
        if self._client is None or not self._launched:
            return None
        state = self._client.call("app_state", timeout_s=20).get("state")
        if state == "not_running":
            return "the app is no longer running (crashed or was killed)"
        return None

    def app_log_tail(self, lines: int = 3000) -> str:
        log_file = self._work_dir / "app.log"
        if self._work_dir == Path() or not log_file.exists():
            return ""
        return "\n".join(log_file.read_text(errors="replace").splitlines()[-lines:])

    def close(self) -> None:
        self._closed = True
        if self._log_stream is not None:
            self._log_stream.terminate()
        self._driver = None
        if self._client is not None:
            with contextlib.suppress(Exception):  # best effort
                self._client.call("shutdown", timeout_s=10)
        if self._process is not None:
            try:
                self._process.wait(timeout=60)
            except subprocess.TimeoutExpired:
                self._process.terminate()
        if self._work_dir != Path():
            shutil.rmtree(self._work_dir, ignore_errors=True)

    # --- app lifecycle -------------------------------------------------------------------------
    def _launch_arguments(self, *, clean_state: bool) -> list[str]:
        ios = self.environment.ios
        args = list(ios.launch_arguments)
        if clean_state and ios.clean_state_argument:
            args.append(ios.clean_state_argument)
        if ios.backend_url and ios.backend_url_argument:
            args += [ios.backend_url_argument, ios.backend_url]
        return args

    def launch(self, options: AppLaunchOptions) -> None:
        assert self._client is not None
        if options.clean_state and not self.environment.ios.clean_state_argument:
            # The app has no clean-launch argument: reinstall the same artifact for a fresh state.
            self._client.call("terminate", timeout_s=30)
            simctl.uninstall(self.udid, self.bundle_id)
            simctl.install(self.udid, self.config.app)
        self._client.call(
            "launch", {"arguments": self._launch_arguments(clean_state=options.clean_state)}, timeout_s=90
        )
        self._launched = True
        if options.deep_link:
            self._client.call("open_url", {"url": options.deep_link}, timeout_s=30)

    # --- steps -----------------------------------------------------------------------------------
    def _install(self) -> None:
        assert self._artifact is not None
        installed = simctl.installed_info(self.udid, self.bundle_id)
        same = installed is not None and (
            installed.get("CFBundleShortVersionString"),
            installed.get("CFBundleVersion"),
        ) == (self._artifact.version_name, self._artifact.version_code)
        if self.config.reinstall or not same:
            simctl.install(self.udid, self.config.app)
        installed = simctl.installed_info(self.udid, self.bundle_id)
        if installed is None:
            raise ArtifactMismatch(f"{self.bundle_id} is not installed after installing {self.config.app}")
        version = (installed.get("CFBundleShortVersionString"), installed.get("CFBundleVersion"))
        if version != (self._artifact.version_name, self._artifact.version_code):
            raise ArtifactMismatch(
                f"installed {self.bundle_id} is {version}, the supplied app is "
                f"{(self._artifact.version_name, self._artifact.version_code)}"
            )
        self._artifact.installed_verified = True
        self._artifact.extra["installed_version"] = f"{version[0]} ({version[1]})"
        self._artifact.extra["reinstalled"] = self.config.reinstall or not same

    def _start_runner(self, run_dir: Path) -> None:
        source = self.config.xctestrun or find_xctestrun(self.root / DEFAULT_PRODUCTS)
        if not source.is_absolute():
            source = self.root / source
        port = self.config.runner_port or _free_port()
        token = new_token()
        self._xctestrun = patch_xctestrun(
            source,
            self._work_dir / "ios-runner.xctestrun",
            {"MTF_RUNNER_PORT": str(port), "MTF_RUNNER_TOKEN": token, "MTF_TARGET_BUNDLE_ID": self.bundle_id},
            app_process=self.app_process or None,
        )
        self._runner_log = self._work_dir / "ios-runner.log"
        log = self._runner_log.open("wb")
        command = [
            "xcodebuild", "test-without-building",
            "-xctestrun", str(self._xctestrun),
            "-destination", f"id={self.udid}",
            "-only-testing", SERVE_TEST,
            "-resultBundlePath", str(self._work_dir / "ios-runner.xcresult"),
        ]  # fmt: skip
        try:
            self._process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT)
        except FileNotFoundError as exc:
            raise ConfigurationError("xcodebuild not found: iOS runs need macOS with Xcode") from exc
        self._client = RunnerClient("127.0.0.1", port, token)
        try:
            self._client.wait_until_ready(
                self.environment.timeouts.runner_start_s,
                is_alive=lambda: self._process is not None and self._process.poll() is None,
            )
        except InfrastructureError as exc:
            tail = "\n".join(self._runner_log.read_text(errors="replace").splitlines()[-15:])
            raise InfrastructureError(f"{exc.message}\nxcodebuild output (tail):\n{tail}") from exc
