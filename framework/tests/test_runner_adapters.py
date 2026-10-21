from __future__ import annotations

import json
import plistlib
import socket
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from mtf.android_runner.adb import validate_package
from mtf.core.artifacts import inspect_ios_app, sha256_tree
from mtf.core.errors import (
    ArtifactMismatch,
    ConfigurationError,
    ElementNotFound,
    InfrastructureError,
    RunnerError,
    StepTimeout,
)
from mtf.core.ports import Direction, WaitState
from mtf.core.remote_driver import RemoteUiCommands
from mtf.core.rpc import RunnerClient
from mtf.ios_runner.simctl import validate_bundle_id
from mtf.ios_runner.xctestrun import SERVE_TEST, patch_xctestrun

TOKEN = "t" * 32


class FakeRunner:
    """A device runner stand-in speaking the newline-JSON protocol on localhost."""

    def __init__(self, handler: Callable[[str, dict[str, Any]], dict[str, Any]]) -> None:
        self.handler = handler
        self.requests: list[dict[str, Any]] = []
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(4)
        self.port = self.server.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.server.accept()
            except OSError:
                return
            with conn:
                data = b""
                while not data.endswith(b"\n"):
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                request = json.loads(data)
                self.requests.append(request)
                if request["token"] != TOKEN:
                    reply = {
                        "id": request["id"],
                        "ok": False,
                        "error": {"category": "unauthorized", "message": "bad token"},
                    }
                else:
                    reply = {"id": request["id"], **self.handler(request["command"], request["args"])}
                conn.sendall((json.dumps(reply) + "\n").encode())

    def close(self) -> None:
        self.server.close()


@pytest.fixture
def runner() -> Iterator[FakeRunner]:
    def handler(command: str, args: dict[str, Any]) -> dict[str, Any]:
        if command == "read":
            if args["id"] == "login_button":
                return {
                    "ok": True,
                    "result": {
                        "id": "login_button",
                        "exists": True,
                        "visible": True,
                        "text": "Sign in",
                        "enabled": True,
                        "bounds": [0, 0, 10, 10],
                        "class_name": "View",
                    },
                }
            return {"ok": True, "result": {"id": args["id"], "exists": False, "visible": False}}
        if command == "tap" and args["id"] == "missing_button":
            return {"ok": False, "error": {"category": "element_not_found", "message": "missing"}}
        if command == "wait_for":
            return {"ok": False, "error": {"category": "timeout", "message": "still visible"}}
        if command == "first_visible":
            return {"ok": True, "result": {"id": "login_screen"}}
        if command == "explode":
            return {"ok": False, "error": {"category": "runner_error", "message": "boom"}}
        return {"ok": True, "result": {}}

    fake = FakeRunner(handler)
    yield fake
    fake.close()


class Commands(RemoteUiCommands):
    pass


def test_remote_commands_map_to_protocol_and_errors(runner: FakeRunner) -> None:
    commands = Commands(RunnerClient("127.0.0.1", runner.port, TOKEN))
    info = commands.read("login_button")
    assert info.visible
    assert info.text == "Sign in"
    assert info.bounds == (0, 0, 10, 10)
    assert info.extra == {"class_name": "View"}
    assert not commands.read("nothing_here").exists
    commands.scroll_to("product_001", direction=Direction.DOWN, max_swipes=3, container="catalog_list", timeout_ms=100)
    assert runner.requests[-1]["args"] == {
        "id": "product_001",
        "direction": "down",
        "max_swipes": 3,
        "container": "catalog_list",
        "timeout_ms": 100,
    }
    commands.type_text("login_password", "s3cret", clear_first=True, sensitive=True, timeout_ms=100)
    assert runner.requests[-1]["args"]["sensitive"] is True
    assert commands.first_visible(["home_screen", "login_screen"]) == "login_screen"
    with pytest.raises(ElementNotFound):
        commands.tap("missing_button", 100)
    with pytest.raises(StepTimeout):
        commands.wait_for("login_button", WaitState.GONE, 100)
    with pytest.raises(RunnerError, match="boom"):
        commands.client.call("explode")


def test_runner_rejects_a_wrong_token(runner: FakeRunner) -> None:
    with pytest.raises(RunnerError, match="bad token"):
        RunnerClient("127.0.0.1", runner.port, "x" * 32).call("hello")


def test_unreachable_runner_is_an_infrastructure_error() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    with pytest.raises(InfrastructureError):
        RunnerClient("127.0.0.1", port, TOKEN, connect_timeout_s=0.5).call("hello")
    with pytest.raises(InfrastructureError, match="exited"):
        RunnerClient("127.0.0.1", port, TOKEN).wait_until_ready(5, is_alive=lambda: False)


def test_xctestrun_patching(tmp_path: Path) -> None:
    source = tmp_path / "products" / "MTFRunner.xctestrun"
    source.parent.mkdir()
    document = {
        "__xctestrun_metadata__": {"FormatVersion": 1},
        "MTFRunnerUITests": {
            "TestHostPath": "__TESTROOT__/Debug-iphonesimulator/MTFRunnerUITests-Runner.app",
            "EnvironmentVariables": {"TERM": "dumb"},
        },
    }
    with source.open("wb") as handle:
        plistlib.dump(document, handle)
    out = patch_xctestrun(
        source, tmp_path / "run" / "patched.xctestrun", {"MTF_RUNNER_PORT": "1234"}, app_process="MobileTestPlayground"
    )
    with out.open("rb") as handle:
        patched = plistlib.load(handle)["MTFRunnerUITests"]
    assert patched["TestHostPath"].startswith(str(source.parent.resolve()))
    assert patched["EnvironmentVariables"] == {"TERM": "dumb", "MTF_RUNNER_PORT": "1234"}
    assert patched["TestingEnvironmentVariables"]["MTF_RUNNER_PORT"] == "1234"
    assert "MobileTestPlayground" in patched["ProcessNamesForCrashReportCollection"]
    with source.open("rb") as handle:  # the build products are not modified
        assert "MTF_RUNNER_PORT" not in plistlib.load(handle)["MTFRunnerUITests"]["EnvironmentVariables"]
    assert SERVE_TEST == "MTFRunnerUITests/RunnerTests/testServe"
    with pytest.raises(ConfigurationError, match="make ios-runner"):
        patch_xctestrun(tmp_path / "missing.xctestrun", tmp_path / "x", {}, app_process=None)


def make_app(tmp_path: Path, platforms: list[str]) -> Path:
    app = tmp_path / "Sample.app"
    app.mkdir()
    with (app / "Info.plist").open("wb") as handle:
        plistlib.dump(
            {
                "CFBundleIdentifier": "com.example.app",
                "CFBundleShortVersionString": "1.2",
                "CFBundleVersion": "7",
                "CFBundleSupportedPlatforms": platforms,
                "CFBundleExecutable": "Sample",
            },
            handle,
        )
    (app / "Sample").write_bytes(b"binary")
    return app


def test_ios_app_inspection_and_tree_checksum(tmp_path: Path) -> None:
    app = make_app(tmp_path, ["iPhoneSimulator"])
    info = inspect_ios_app(app)
    assert (info.identifier, info.version_name, info.version_code) == ("com.example.app", "1.2", "7")
    assert info.extra["simulator_build"] is True
    first = sha256_tree(app)
    (app / "Sample").write_bytes(b"changed")
    assert sha256_tree(app) != first
    with pytest.raises(ConfigurationError):
        inspect_ios_app(tmp_path / "nothing.app")


def test_ios_session_refuses_device_builds(tmp_path: Path) -> None:
    from mtf.core.config import Environment, IosRunConfig
    from mtf.ios_runner.session import IosSession

    app = make_app(tmp_path, ["iPhoneOS"])
    env = Environment.model_validate(
        {
            "name": "u",
            "kind": "local",
            "backend": {"base_url": "http://localhost:1"},
            "ios": {"bundle_id": "com.example.app"},
        }
    )
    with pytest.raises(ArtifactMismatch, match="not a simulator build"):
        IosSession(IosRunConfig(app=app), env).prepare(tmp_path)


def test_identifier_validation() -> None:
    assert validate_package("com.mobiletestplayground.app")
    assert validate_bundle_id("com.mobiletestplayground.app")
    for bad in ("com.app; rm -rf /", "$(id)", "a b"):
        with pytest.raises(ConfigurationError):
            validate_package(bad)
        with pytest.raises(ConfigurationError):
            validate_bundle_id(bad)
