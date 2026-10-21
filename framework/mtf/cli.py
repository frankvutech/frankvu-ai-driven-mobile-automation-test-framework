"""`mtf` command line: validate, run, plan (AI), contracts, reports, artifacts."""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

import click

from mtf import __version__
from mtf.contracts.bundle import PLAN_SCHEMA_FILE, Contracts
from mtf.core.cancellation import CancellationToken
from mtf.core.config import (
    AndroidRunConfig,
    Environment,
    IosRunConfig,
    RunConfig,
    check_environment,
    load_environment,
    load_run_config,
)
from mtf.core.errors import MtfError
from mtf.core.events import RUN_FINISHED, STEP_FINISHED, Event, EventBus
from mtf.core.types import Platform, RunStatus
from mtf.dsl import load_plan
from mtf.dsl.loader import dump_plan, plan_hash
from mtf.dsl.schema import render_schema
from mtf.screenplay.registry import build_catalog
from mtf.validator import PlanValidator, SafetyPolicy

EXIT_OK, EXIT_FAILED, EXIT_USAGE = 0, 1, 2


def find_root(start: Path | None = None) -> Path:
    env = os.environ.get("MTF_HOME")
    if env:
        return Path(env).resolve()
    here = (start or Path.cwd()).resolve()
    for candidate in [here, *here.parents]:
        if (candidate / "contracts" / "test-ids.yaml").is_file():
            return candidate
    raise click.ClickException("cannot find the framework root (contracts/test-ids.yaml); set MTF_HOME")


def load_dotenv(path: Path) -> None:
    """Loads KEY=VALUE lines into the environment without overriding variables already set."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and value and key not in os.environ:
            os.environ[key] = value


class Ctx:
    def __init__(self) -> None:
        self.root = find_root()
        load_dotenv(self.root / ".env")
        self._contracts: Contracts | None = None

    @property
    def contracts(self) -> Contracts:
        if self._contracts is None:
            self._contracts = Contracts.load(self.root / "contracts")
        return self._contracts


pass_ctx = click.make_pass_decorator(Ctx, ensure=True)


def _fail(message: str, code: int = EXIT_FAILED) -> None:
    click.secho(message, fg="red", err=True)
    sys.exit(code)


@click.group()
@click.version_option(__version__, prog_name="mtf")
def main() -> None:
    """AI-assisted, deterministic test automation for prebuilt Android/iOS apps and their backend."""


# --- validate ---------------------------------------------------------------------------------------
@main.command()
@click.argument("plans", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--platform", "platforms", multiple=True, type=click.Choice([p.value for p in Platform]))
@click.option(
    "--env",
    "env_name",
    default=None,
    help="Apply this environment's policy (e.g. test-support availability).",
)
@click.option("--json", "as_json", is_flag=True)
@pass_ctx
def validate(
    ctx: Ctx, plans: tuple[Path, ...], platforms: tuple[str, ...], env_name: str | None, as_json: bool
) -> None:
    """Validate plans against the DSL schema, contracts, catalog and safety policy."""
    policy = SafetyPolicy()
    if env_name:
        env = load_environment(env_name, ctx.root / "config")
        policy = SafetyPolicy(allow_test_support=env.backend.test_support)
    validator = PlanValidator(ctx.contracts, build_catalog(), policy)
    failed = 0
    reports = []
    for path in _expand(plans):
        try:
            plan = load_plan(path)
        except MtfError as exc:
            failed += 1
            reports.append(
                {
                    "plan": str(path),
                    "ok": False,
                    "issues": [{"severity": "error", "code": "schema", "location": "<plan>", "message": exc.message}],
                }
            )
            if not as_json:
                click.secho(f"✗ {path}", fg="red")
                click.echo("  " + exc.message.replace("\n", "\n  "))
            continue
        report = validator.validate(plan, [Platform(p) for p in platforms] or None)
        reports.append({"path": str(path), **report.to_dict()})
        if not report.ok:
            failed += 1
        if not as_json:
            mark = click.style("✓", fg="green") if report.ok else click.style("✗", fg="red")
            click.echo(f"{mark} {path} ({', '.join(p.value for p in report.platforms)})")
            for issue in report.issues:
                click.echo(f"    {issue}")
    if as_json:
        click.echo(json.dumps(reports, indent=2))
    sys.exit(EXIT_FAILED if failed else EXIT_OK)


def _expand(paths: tuple[Path, ...]) -> list[Path]:
    out: list[Path] = []
    for path in paths:
        if path.is_dir():
            out.extend(sorted(p for p in path.rglob("*.y*ml")))
        else:
            out.append(path)
    return out


# --- run --------------------------------------------------------------------------------------------
@main.command()
@click.argument("plans", nargs=-1, required=True, type=click.Path(exists=True, path_type=Path))
@click.option("--platform", required=True, type=click.Choice([p.value for p in Platform]))
@click.option(
    "--env", "env_name", default=None, help="Environment name or file (default: from run config, else local)."
)
@click.option(
    "--run-config",
    type=click.Path(exists=True, path_type=Path),
    help="YAML with artifacts and device settings.",
)
@click.option(
    "--apk",
    type=click.Path(exists=True, path_type=Path),
    help="Prebuilt application APK (installed unchanged).",
)
@click.option("--app-id", help="Android application ID (default: from the environment).")
@click.option("--device", "device_serial", help="Android device serial.")
@click.option("--runner-apk", type=click.Path(path_type=Path), help="The framework's runner APK.")
@click.option(
    "--app",
    "ios_app",
    type=click.Path(exists=True, path_type=Path),
    help="Prebuilt iOS .app (simulator build).",
)
@click.option("--bundle-id", help="iOS bundle identifier (default: from the environment).")
@click.option(
    "--xctestrun",
    type=click.Path(path_type=Path),
    help="The framework iOS runner's .xctestrun (build products).",
)
@click.option("--simulator", help="iOS simulator name or UDID.")
@click.option("--ios-version", help="iOS runtime version, e.g. 26.5.")
@click.option("--reports", "reports_dir", type=click.Path(path_type=Path), default=None)
@click.option("--allow-remote-env", is_flag=True, help="Confirm a non-local (never production) environment.")
@click.option("--max-step-seconds", default=300.0, show_default=True, help="Watchdog for a single step.")
@click.option(
    "--fresh-session-per-plan",
    is_flag=True,
    help="Reinstall the app and restart the runner for every plan (default: reuse one session per invocation).",
)
@pass_ctx
def run(
    ctx: Ctx,
    plans: tuple[Path, ...],
    platform: str,
    env_name: str | None,
    run_config: Path | None,
    apk: Path | None,
    app_id: str | None,
    device_serial: str | None,
    runner_apk: Path | None,
    ios_app: Path | None,
    bundle_id: str | None,
    xctestrun: Path | None,
    simulator: str | None,
    ios_version: str | None,
    reports_dir: Path | None,
    allow_remote_env: bool,
    max_step_seconds: float,
    fresh_session_per_plan: bool,
) -> None:
    """Execute validated plans on one platform. Plans never trigger a build of the app under test."""
    from mtf.orchestrator.orchestrator import Orchestrator, RunRequest

    config = load_run_config(run_config) if run_config else RunConfig()
    env = load_environment(env_name or config.environment, ctx.root / "config")
    try:
        warnings = check_environment(env, allow_remote=allow_remote_env)
    except MtfError as exc:
        _fail(f"environment refused: {exc.message}", EXIT_USAGE)
    target = Platform(platform)
    config = _merge_cli(
        config,
        target,
        apk,
        app_id,
        device_serial,
        runner_apk,
        ios_app,
        bundle_id,
        xctestrun,
        simulator,
        ios_version,
        ctx.root,
    )
    reports = reports_dir or config.reports_dir
    if not reports.is_absolute():
        reports = ctx.root / reports
    catalog = build_catalog()
    orchestrator = Orchestrator(ctx.contracts, catalog)
    cancellation = CancellationToken()
    signal.signal(signal.SIGINT, lambda *_: cancellation.cancel("interrupted (Ctrl-C)"))
    events = EventBus()
    events.subscribe(_print_event)
    results = []
    session: Any = None
    for path in _expand(plans):
        try:
            plan = load_plan(path)
        except MtfError as exc:
            click.secho(f"✗ {path}: {exc.message}", fg="red")
            results.append({"plan": str(path), "status": RunStatus.REJECTED.value, "run_dir": None})
            continue
        if target not in plan.platforms:
            click.secho(f"- {path}: does not target {target.value}; skipped", fg="yellow")
            continue
        if session is None or fresh_session_per_plan or getattr(session, "closed", False):
            session = _session(target, config, env, ctx.root)
        click.secho(f"▶ {plan.name} on {target.value} ({env.name})", bold=True)
        result = orchestrator.run(
            RunRequest(
                plan=plan,
                platform=target,
                environment=env,
                session=session,
                reports_dir=reports,
                plan_path=path,
                cancellation=cancellation,
                events=events,
                max_step_duration_s=max_step_seconds,
                environment_warnings=warnings,
                close_session=fresh_session_per_plan,
            )
        )
        run_dir = reports / "runs" / result.run_id
        results.append(
            {
                "plan": plan.name,
                "status": result.status.value,
                "run_dir": str(run_dir),
                "message": result.message,
            }
        )
        if cancellation.cancelled:
            break
    if session is not None and not getattr(session, "closed", True):
        session.close()
    _write_summary(reports, target, results)
    bad = [r for r in results if r["status"] != RunStatus.PASSED.value]
    click.echo("")
    for r in results:
        colour = "green" if r["status"] == "passed" else "red"
        click.secho(f"{r['status']:>9}  {r['plan']}", fg=colour)
        if r.get("run_dir"):
            click.echo(f"           {r['run_dir']}/report.html")
    sys.exit(EXIT_FAILED if bad or not results else EXIT_OK)


def _merge_cli(
    config: RunConfig,
    target: Platform,
    apk: Path | None,
    app_id: str | None,
    device_serial: str | None,
    runner_apk: Path | None,
    ios_app: Path | None,
    bundle_id: str | None,
    xctestrun: Path | None,
    simulator: str | None,
    ios_version: str | None,
    root: Path,
) -> RunConfig:
    if target == Platform.ANDROID:
        base = config.android.model_dump() if config.android else {}
        updates = {
            k: v
            for k, v in {
                "apk": apk,
                "app_id": app_id,
                "device_serial": device_serial,
                "runner_apk": runner_apk,
            }.items()
            if v
        }
        merged = {**base, **updates}
        if "apk" not in merged:
            _fail("android runs need a prebuilt APK: --apk PATH (or android.apk in --run-config)", EXIT_USAGE)
        android = AndroidRunConfig.model_validate(merged)
        if not android.runner_apk.is_absolute():
            android = android.model_copy(update={"runner_apk": root / android.runner_apk})
        return config.model_copy(update={"android": android})
    if target == Platform.IOS:
        base = config.ios.model_dump() if config.ios else {}
        updates = {
            k: v
            for k, v in {
                "app": ios_app,
                "bundle_id": bundle_id,
                "xctestrun": xctestrun,
                "simulator": simulator,
                "os_version": ios_version,
            }.items()
            if v
        }
        merged = {**base, **updates}
        if "app" not in merged:
            _fail("ios runs need a prebuilt .app: --app PATH (or ios.app in --run-config)", EXIT_USAGE)
        return config.model_copy(update={"ios": IosRunConfig.model_validate(merged)})
    return config


def _session(target: Platform, config: RunConfig, env: Environment, root: Path) -> Any:
    if target == Platform.ANDROID:
        from mtf.android_runner.session import AndroidSession

        assert config.android is not None
        return AndroidSession(config.android, env)
    if target == Platform.IOS:
        from mtf.ios_runner.session import IosSession

        assert config.ios is not None
        return IosSession(config.ios, env, root=root)
    from mtf.orchestrator.sessions import ApiOnlySession

    return ApiOnlySession()


def _print_event(event: Event) -> None:
    p = event.payload
    if event.type == STEP_FINISHED and p.get("name"):
        status = str(p.get("status", ""))
        colour = {"passed": "green", "failed": "red", "error": "yellow"}.get(status, "white")
        target = f" → {p['target']}" if p.get("target") else ""
        badge = click.style(f"{status:>9}", fg=colour)
        line = f"  {badge} {p.get('section', ''):<13} {p.get('kind')} {p.get('name')}{target}"
        if p.get("duration_ms") is not None:
            line += click.style(f"  {p['duration_ms']:.0f} ms", dim=True)
        if status != "passed" and p.get("message"):
            line += f"\n{'':>12}{p['message']}"
        click.echo(line)
    elif event.type == RUN_FINISHED:
        click.echo(f"  ⇒ {p['status']}  {p['run_dir']}")


def _write_summary(reports: Path, platform: Platform, results: list[dict[str, Any]]) -> None:
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"summary-{platform.value}.json").write_text(
        json.dumps({"platform": platform.value, "results": results}, indent=2) + "\n"
    )


# --- plan (AI planner and approval) ----------------------------------------------------------------
@main.group()
def plan() -> None:
    """Generate (AI), approve and inspect plans."""


@plan.command("generate")
@click.argument("request")
@click.option("--platform", "platforms", multiple=True, required=True, type=click.Choice([p.value for p in Platform]))
@click.option("--out", "out_path", type=click.Path(path_type=Path), required=True)
@click.option("--provider", type=click.Choice(["anthropic", "recorded"]), default="anthropic", show_default=True)
@click.option(
    "--recording", type=click.Path(exists=True, path_type=Path), help="Recorded provider responses (offline)."
)
@click.option("--model", default=None, help="Model ID for the provider.")
@click.option("--max-repairs", default=1, show_default=True, help="Rounds in which validation errors are fed back.")
@pass_ctx
def plan_generate(
    ctx: Ctx,
    request: str,
    platforms: tuple[str, ...],
    out_path: Path,
    provider: str,
    recording: Path | None,
    model: str | None,
    max_repairs: int,
) -> None:
    """Turn a natural-language request into a candidate plan (never executes it)."""
    from mtf.planner.planner import AiPlanner, PlanningRequest
    from mtf.planner.providers import make_provider

    planner = AiPlanner(ctx.contracts, build_catalog(), make_provider(provider, model=model, recording=recording))
    try:
        outcome = planner.plan(
            PlanningRequest(request=request, platforms=[Platform(p) for p in platforms]),
            max_repairs=max_repairs,
        )
    except MtfError as exc:
        _fail(f"planning failed: {exc.message}")
    if outcome.clarifications:
        click.secho("The planner needs clarification before it can write a plan:", fg="yellow")
        for question in outcome.clarifications:
            click.echo(f"  ? {question}")
        sys.exit(EXIT_FAILED)
    assert outcome.plan is not None
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(dump_plan(outcome.plan), encoding="utf-8")
    click.secho(f"Candidate plan written to {out_path} (status: NOT APPROVED)", fg="green")
    click.echo(f"Explanation: {outcome.explanation}")
    for assumption in outcome.assumptions:
        click.echo(f"  assumption: {assumption}")
    for issue in outcome.validation.issues:
        click.echo(f"  {issue}")
    if not outcome.validation.ok:
        click.secho("The candidate still has validation errors; fix it before approval.", fg="red")
        sys.exit(EXIT_FAILED)
    click.echo(f"Review it, then: mtf plan approve {out_path} --by <your name>")


@plan.command("approve")
@click.argument("plan_path", type=click.Path(exists=True, path_type=Path))
@click.option("--by", "approver", required=True)
@click.option("--note", default=None)
@pass_ctx
def plan_approve(ctx: Ctx, plan_path: Path, approver: str, note: str | None) -> None:
    """Record a human approval (bound to the plan's content hash)."""
    from mtf.planner.approval import approve

    try:
        approved = approve(
            load_plan(plan_path),
            approver=approver,
            note=note,
            contracts=ctx.contracts,
            catalog=build_catalog(),
        )
    except MtfError as exc:
        _fail(exc.message)
    plan_path.write_text(dump_plan(approved), encoding="utf-8")
    click.secho(f"Approved {approved.name} ({plan_hash(approved)[:12]}) by {approver}", fg="green")


@plan.command("hash")
@click.argument("plan_path", type=click.Path(exists=True, path_type=Path))
def plan_hash_cmd(plan_path: Path) -> None:
    """Print the canonical content hash of a plan."""
    click.echo(plan_hash(load_plan(plan_path)))


# --- catalog and contracts --------------------------------------------------------------------------
@main.command()
@click.option("--json", "as_json", is_flag=True)
@pass_ctx
def catalog(ctx: Ctx, as_json: bool) -> None:
    """List registered tasks, questions, fixtures and interactions."""
    described = build_catalog().describe()
    if as_json:
        click.echo(json.dumps(described, indent=2))
        return
    for section, items in described.items():
        click.secho(section, bold=True)
        for item in items:
            params = ", ".join(f"{k}:{v['type']}{'' if v['required'] else '?'}" for k, v in item["params"].items())
            click.echo(f"  {item['name']}({params})  [{', '.join(item['platforms'])}]  {item['description']}")


@main.group()
def contracts() -> None:
    """Contract registry: test IDs, plan schema, OpenAPI."""


@contracts.command("import-ids")
@click.option("--app-repo", required=True, type=click.Path(exists=True, file_okay=False, path_type=Path))
@pass_ctx
def contracts_import_ids(ctx: Ctx, app_repo: Path) -> None:
    """Re-import contracts/test-ids.yaml from the app's published ID catalogue (read-only)."""
    from mtf.contracts.importer import import_test_ids, write_registry

    data = import_test_ids(app_repo)
    write_registry(data, ctx.root / "contracts" / "test-ids.yaml")
    click.echo(
        f"{len(data['ids'])} IDs and {len(data['patterns'])} patterns imported "
        f"(app commit {data['source']['app_commit']})"
    )


@contracts.command("schema")
@click.option("--check", is_flag=True, help="Fail if the committed schema is stale.")
@pass_ctx
def contracts_schema(ctx: Ctx, check: bool) -> None:
    """Write (or check) contracts/test-plan.schema.json from the DSL models."""
    path = ctx.root / "contracts" / PLAN_SCHEMA_FILE
    rendered = render_schema()
    if check:
        if not path.is_file() or path.read_text(encoding="utf-8") != rendered:
            _fail(f"{path} is stale; run `mtf contracts schema`")
        click.echo(f"{path.name} is up to date")
        return
    path.write_text(rendered, encoding="utf-8")
    click.echo(f"wrote {path}")


@contracts.command("check-openapi")
@click.option("--base-url", required=True, help="A running backend (local or CI).")
@pass_ctx
def contracts_check_openapi(ctx: Ctx, base_url: str) -> None:
    """Compare contracts/openapi/backend.yaml with the spec a running backend serves."""
    import httpx
    import yaml

    response = httpx.get(base_url.rstrip("/") + "/openapi/openapi.yaml", timeout=15)
    response.raise_for_status()
    live = yaml.safe_load(response.text)
    if live == ctx.contracts.api.spec:
        click.secho("contracts/openapi/backend.yaml matches the backend's served spec", fg="green")
        return
    committed_ops = set(ctx.contracts.api.operations)
    from mtf.contracts.openapi import ApiContract

    live_ops = set(ApiContract(live).operations)
    click.secho("The committed contract differs from the served spec.", fg="red")
    for op in sorted(live_ops - committed_ops):
        click.echo(f"  + {op} (only in the backend)")
    for op in sorted(committed_ops - live_ops):
        click.echo(f"  - {op} (only in the committed contract)")
    sys.exit(EXIT_FAILED)


# --- reports ----------------------------------------------------------------------------------------
@main.group()
def report() -> None:
    """Re-render reports or add an advisory AI analysis."""


@report.command("render")
@click.argument("run_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
def report_render(run_dir: Path) -> None:
    from mtf.reporting.writer import rerender

    click.echo(rerender(run_dir))


@report.command("advise")
@click.argument("run_dir", type=click.Path(exists=True, file_okay=False, path_type=Path))
@click.option("--provider", type=click.Choice(["anthropic", "recorded"]), default="anthropic")
@click.option("--recording", type=click.Path(exists=True, path_type=Path))
@click.option("--model", default=None)
def report_advise(run_dir: Path, provider: str, recording: Path | None, model: str | None) -> None:
    """Attach an AI failure analysis to a finished run. It is advisory and never changes a status."""
    from mtf.planner.advisor import advise_run
    from mtf.planner.providers import make_provider

    try:
        advisory = advise_run(run_dir, make_provider(provider, model=model, recording=recording))
    except MtfError as exc:
        _fail(exc.message)
    click.echo(advisory.summary)


# --- artifacts --------------------------------------------------------------------------------------
@main.command("inspect-artifact")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
def inspect_artifact(path: Path) -> None:
    """Show checksum and version metadata of a prebuilt APK or .app (read-only)."""
    from mtf.core.artifacts import inspect_apk, inspect_ios_app

    info = inspect_ios_app(path) if path.suffix == ".app" else inspect_apk(path)
    click.echo(json.dumps(info.model_dump(mode="json"), indent=2))


if __name__ == "__main__":  # pragma: no cover
    main()
