from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from pydantic import ValidationError

from mtf.cli import main
from mtf.core.redaction import Redactor
from mtf.core.types import Platform, RunStatus, StepStatus
from mtf.reporting.models import EnvironmentInfo, PlanInfo, RunnerInfo, RunResult, StepResult
from mtf.reporting.writer import render_html, rerender, write_reports


def sample_result() -> RunResult:
    return RunResult(
        run_id="r1",
        status=RunStatus.FAILED,
        plan=PlanInfo(name="p", dsl_version=1, sha256="a" * 64, origin="ai_generated"),
        platform=Platform.API,
        environment=EnvironmentInfo(
            name="local", kind="local", backend_base_url="http://localhost:8080", test_support=True
        ),
        runner=RunnerInfo(framework_version="0", runner="x"),
        started_at="now",
        steps=[
            StepResult(
                index=0,
                section="steps",
                position=0,
                kind="api",
                name="GET /health",
                platform=Platform.API,
                status=StepStatus.FAILED,
                message="token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1In0.signature1 <script>",
            )
        ],
    )


def test_report_serialisation_is_redacted_and_escaped(tmp_path: Path) -> None:
    json_path, html_path = write_reports(sample_result(), tmp_path, Redactor())
    document = json.loads(json_path.read_text())
    assert RunResult.model_validate(document).status == RunStatus.FAILED
    assert "eyJhbGci" not in json_path.read_text()
    assert "eyJhbGci" not in html_path.read_text()
    html = html_path.read_text()
    assert "&lt;script&gt;" in html
    assert "<script>" not in html
    assert "AI-generated" in html


def test_rerender_refuses_a_tampered_result(tmp_path: Path) -> None:
    write_reports(sample_result(), tmp_path, Redactor())
    assert rerender(tmp_path).is_file()
    document = json.loads((tmp_path / "result.json").read_text())
    document["status"] = "great"
    (tmp_path / "result.json").write_text(json.dumps(document))
    with pytest.raises(ValidationError):
        rerender(tmp_path)


def test_html_renders_without_optional_sections() -> None:
    assert "FAILED" in render_html(sample_result().model_dump(mode="json"))


def test_cli_validate_and_catalog(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(root)
    runner = CliRunner()
    result = runner.invoke(main, ["validate", "plans", "--env", "local"])
    assert result.exit_code == 0, result.output
    assert "✗" not in result.output
    catalog = runner.invoke(main, ["catalog", "--json"])
    assert catalog.exit_code == 0
    assert "add_product_to_cart" in catalog.output
    schema = runner.invoke(main, ["contracts", "schema", "--check"])
    assert schema.exit_code == 0, schema.output


def test_cli_validate_reports_errors(root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(root)
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\nname: bad_plan\nplatforms: [api]\nsteps:\n  - task: nope_task\n")
    result = CliRunner().invoke(main, ["validate", str(bad), "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output)[0]["ok"] is False


def test_cli_refuses_production(root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(root)
    env = tmp_path / "prod.yaml"
    env.write_text("name: shop\nkind: production\nbackend: {base_url: 'https://api.example.com'}\n")
    result = CliRunner().invoke(main, ["run", "plans/api/health.yaml", "--platform", "api", "--env", str(env)])
    assert result.exit_code == 2
    assert "never tests production" in result.output


def test_cli_requires_prebuilt_artifacts(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(root)
    result = CliRunner().invoke(main, ["run", "plans/shared/cart_add_product.yaml", "--platform", "android"])
    assert result.exit_code == 2
    assert "prebuilt APK" in result.output
