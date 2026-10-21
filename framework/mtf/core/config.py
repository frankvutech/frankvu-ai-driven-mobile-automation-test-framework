"""Environment and run configuration, and the environment safety rules.

* An **environment** (``config/environments/<name>.yaml``) describes a backend and how the app on a
  device reaches it. Production is never a valid target; non-local environments need an explicit
  opt-in.
* A **run configuration** (CLI flags or ``config/run*.yaml``) names the prebuilt artifacts, the
  device or simulator and the framework's own runner build products.
"""

from __future__ import annotations

import os
import re
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from mtf.core.errors import ConfigurationError, PolicyViolation

LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "10.0.2.2"})
PRODUCTION_MARKERS = frozenset({"prod", "prd", "production", "live"})
ALLOW_REMOTE_ENV_VAR = "MTF_ALLOW_REMOTE_ENV"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EnvironmentKind(StrEnum):
    LOCAL = "local"
    CI = "ci"
    STAGING = "staging"
    PRODUCTION = "production"


class BackendConfig(_Model):
    base_url: str
    allowed_hosts: list[str] = Field(default_factory=list)
    test_support: bool = Field(default=False, description="Reset/seed endpoints may be called.")
    verify_tls: bool = True
    timeout_s: float = Field(default=15.0, gt=0, le=120)


class AndroidAppConfig(_Model):
    app_id: str = "com.mobiletestplayground.app"
    launch_activity: str | None = Field(default=None, description="e.g. `.MainActivity`; default: launcher intent.")
    backend_url: str | None = Field(default=None, description="The backend URL as the app on the device sees it.")


class IosAppConfig(_Model):
    bundle_id: str = "com.mobiletestplayground.app"
    launch_arguments: list[str] = Field(default_factory=list)
    clean_state_argument: str | None = Field(
        default="-uiTestReset", description="Launch argument the app honours to start from clean storage."
    )
    backend_url_argument: str | None = Field(default="-backendURL")
    backend_url: str | None = None


class Timeouts(_Model):
    default_step_ms: int = Field(default=10_000, ge=100, le=120_000)
    run_s: int = Field(default=1800, ge=10, le=4 * 3600)
    runner_start_s: int = Field(default=180, ge=5, le=1800)


class Environment(_Model):
    name: str
    kind: EnvironmentKind
    description: str | None = None
    backend: BackendConfig
    private_hosts: list[str] = Field(
        default_factory=list,
        description="Non-loopback hosts that are still local, e.g. a Compose service name.",
    )
    android: AndroidAppConfig = Field(default_factory=AndroidAppConfig)
    ios: IosAppConfig = Field(default_factory=IosAppConfig)
    timeouts: Timeouts = Field(default_factory=Timeouts)

    @property
    def backend_host(self) -> str:
        return urlparse(self.backend.base_url).hostname or ""


def load_environment(name_or_path: str, config_dir: Path) -> Environment:
    path = Path(name_or_path)
    if not path.suffix:
        path = config_dir / "environments" / f"{name_or_path}.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"cannot read environment {path}: {exc}") from exc
    try:
        return Environment.model_validate(data)
    except ValidationError as exc:
        raise ConfigurationError(f"invalid environment {path}:\n{exc}") from exc


def check_environment(
    env: Environment, *, allow_remote: bool = False, environ: dict[str, str] | None = None
) -> list[str]:
    """Enforces the environment safety rules. Returns warnings; raises PolicyViolation on refusal."""
    environ = dict(os.environ) if environ is None else environ
    warnings: list[str] = []
    host = env.backend_host
    url = env.backend.base_url
    if env.kind == EnvironmentKind.PRODUCTION:
        raise PolicyViolation(f"environment {env.name!r} is production; the framework never tests production")
    tokens = set(re.split(r"[^a-z0-9]+", f"{env.name} {host}".lower()))
    if tokens & PRODUCTION_MARKERS:
        raise PolicyViolation(f"environment {env.name!r} ({host}) looks like production; refusing to run")
    if not url.startswith(("http://", "https://")) or not host:
        raise PolicyViolation(f"backend base_url {url!r} is not an http(s) URL")
    if env.backend.allowed_hosts and host not in env.backend.allowed_hosts:
        raise PolicyViolation(f"backend host {host!r} is not in the environment's allowed_hosts")
    is_local = host in LOOPBACK_HOSTS or host in env.private_hosts
    if env.kind in (EnvironmentKind.LOCAL, EnvironmentKind.CI) and not is_local:
        raise PolicyViolation(
            f"environment {env.name!r} is {env.kind.value} but its backend host {host!r} is not local; "
            "declare it as staging (with explicit opt-in) or add it to private_hosts if it is a local service"
        )
    if not is_local:
        opted_in = allow_remote or environ.get(ALLOW_REMOTE_ENV_VAR) == "1"
        if not opted_in:
            raise PolicyViolation(
                f"environment {env.name!r} targets a non-local backend ({host}); pass --allow-remote-env or set "
                f"{ALLOW_REMOTE_ENV_VAR}=1 to confirm it is a non-production test environment"
            )
        if env.backend.test_support:
            warnings.append(
                f"test-support endpoints are enabled for non-local environment {env.name!r}: reset wipes its data"
            )
        if url.startswith("http://"):
            warnings.append(f"environment {env.name!r} uses plain HTTP to a non-local host")
    return warnings


class AndroidRunConfig(_Model):
    apk: Path
    app_id: str | None = None
    device_serial: str | None = None
    runner_apk: Path = Path("framework/android-runner/runner/build/outputs/apk/debug/runner-debug.apk")
    runner_port: int = Field(default=0, ge=0, le=65535, description="0 picks a free port.")
    reinstall: bool = Field(default=True, description="Install the supplied APK even if the same version is present.")
    adb: str = "adb"
    aapt2: str | None = None


class IosRunConfig(_Model):
    app: Path
    bundle_id: str | None = None
    xctestrun: Path | None = Field(default=None, description="The framework runner's .xctestrun (build products).")
    simulator: str = Field(default="iPhone 17", description="Simulator name or UDID.")
    os_version: str | None = Field(default=None, description="e.g. 26.5; default: the newest installed runtime.")
    runner_port: int = Field(default=0, ge=0, le=65535)
    reinstall: bool = True


class RunConfig(_Model):
    environment: str = "local"
    reports_dir: Path = Path("reports")
    android: AndroidRunConfig | None = None
    ios: IosRunConfig | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


def load_run_config(path: Path) -> RunConfig:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except OSError as exc:
        raise ConfigurationError(f"cannot read run configuration {path}: {exc}") from exc
    try:
        return RunConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigurationError(f"invalid run configuration {path}:\n{exc}") from exc
