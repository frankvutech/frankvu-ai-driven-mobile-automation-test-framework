from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from click.testing import CliRunner

from mtf.cli import main
from mtf.core.types import Platform, RunStatus
from mtf.reporting.allure import CATEGORIES_FILE, ENVIRONMENT_FILE, export_run, find_run_dirs, write_executor

from .fakes import PASSWORDS, World
from .test_orchestrator import API_PLAN, CART_PLAN, run


def _results(allure_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in allure_dir.glob("*-result.json")]


def _containers(allure_dir: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text()) for p in allure_dir.glob("*-container.json")]


def _failed_cart_run(contracts, catalog, environment, tmp_path, platform=Platform.ANDROID):
    text = CART_PLAN.read_text().replace("expect: {equals: 1}", "expect: {equals: 3}", 1)
    result, _, backend = run(contracts, catalog, environment, tmp_path, text, platform)
    assert result.status == RunStatus.FAILED
    return result, tmp_path / "runs" / result.run_id, backend


def test_a_failed_run_becomes_one_allure_result_with_fixtures_and_evidence(
    contracts, catalog, environment, tmp_path
) -> None:
    result, run_dir, _ = _failed_cart_run(contracts, catalog, environment, tmp_path)
    allure_dir = tmp_path / "allure-results"
    export_run(run_dir, allure_dir)

    [test] = _results(allure_dir)
    assert test["status"] == "failed"
    assert test["name"] == result.plan.title
    assert test["fullName"] == "android.cart_add_product"
    assert test["statusDetails"]["trace"].startswith("failure_category: assertion_failed")
    labels = {(label["name"], label["value"]) for label in test["labels"]}
    assert {("parentSuite", "Android"), ("suite", "cart"), ("tag", "smoke"), ("framework", "mtf")} <= labels
    assert {p["name"] for p in test["parameters"]} >= {"platform", "environment", "app sha256"}

    [container] = _containers(allure_dir)
    assert container["children"] == [test["uuid"]]
    assert [f["name"] for f in container["befores"]] == ["fixture reset_backend_data", "fixture launch_app"]
    assert [f["name"] for f in container["afters"]] == ["fixture clear_cart"]
    assert container["afters"][0]["status"] == "passed"  # cleanup still ran

    failed = next(s for s in test["steps"] if s["status"] == "failed")
    assert {"name": "expected", "value": "3"} in failed["parameters"]
    assert {"name": "actual", "value": "1"} in failed["parameters"]
    assert test["steps"][-1]["status"] == "skipped"
    attachments = failed["attachments"] + test["attachments"]
    assert {"screenshot", "hierarchy", "device_log", "mtf result.json", "mtf report.html"} <= {
        a["name"].split(":")[0] for a in attachments
    }
    for attachment in attachments:
        assert (allure_dir / attachment["source"]).is_file()
    assert any(a["type"] == "image/png" for a in attachments)

    assert json.loads((allure_dir / CATEGORIES_FILE).read_text())
    properties = (allure_dir / ENVIRONMENT_FILE).read_text()
    assert "Android.App.sha256=" in properties
    assert "Backend=http\\://localhost\\:8080" in properties


def test_exported_results_never_contain_secrets(contracts, catalog, environment, tmp_path) -> None:
    _, run_dir, backend = _failed_cart_run(contracts, catalog, environment, tmp_path, Platform.IOS)
    allure_dir = tmp_path / "allure-results"
    export_run(run_dir, allure_dir)
    secrets = [*PASSWORDS.values(), *backend.world.tokens]
    assert secrets
    texts = [p for p in allure_dir.iterdir() if p.suffix in (".json", ".txt", ".log", ".html", ".properties")]
    assert any(p.name.endswith("-attachment.log") for p in texts)  # the runner log was attached
    for path in texts:
        content = path.read_text(encoding="utf-8")
        assert not any(s in content for s in secrets), path


def test_retries_become_nested_attempts(contracts, catalog, environment, tmp_path) -> None:
    text = """
version: 1
name: retry_case
platforms: [android]
preconditions:
  - fixture: launch_app
steps:
  - wait: {target: home_screen, timeout_ms: 200}
    retry: {max_attempts: 3, delay_ms: 0, on_failure: [element_not_found]}
"""
    result, _, _ = run(contracts, catalog, environment, tmp_path, text, Platform.ANDROID)
    export_run(tmp_path / "runs" / result.run_id, tmp_path / "allure")
    [test] = _results(tmp_path / "allure")
    assert [s["name"] for s in test["steps"][0]["steps"]] == ["attempt 1", "attempt 2", "attempt 3"]
    assert ("suite", "retry_case") in {(label["name"], label["value"]) for label in test["labels"]}


def test_reexport_overwrites_and_environment_merges_across_platforms(contracts, catalog, environment, tmp_path) -> None:
    android, _, _ = run(contracts, catalog, environment, tmp_path, CART_PLAN, Platform.ANDROID)
    api, _, _ = run(contracts, catalog, environment, tmp_path, API_PLAN, Platform.API, world=World())
    allure_dir = tmp_path / "allure"
    for result in (android, api, android):
        export_run(tmp_path / "runs" / result.run_id, allure_dir)
    assert len(_results(allure_dir)) == 2
    assert len(_containers(allure_dir)) == 2
    properties = (allure_dir / ENVIRONMENT_FILE).read_text()
    assert "Android.App=" in properties
    assert "API.Runner=" in properties
    history_ids = {t["historyId"] for t in _results(allure_dir)}
    assert len(history_ids) == 2


def test_evidence_paths_outside_the_run_directory_are_not_copied(contracts, catalog, environment, tmp_path) -> None:
    _, run_dir, _ = _failed_cart_run(contracts, catalog, environment, tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_text("not evidence")
    document = json.loads((run_dir / "result.json").read_text())
    document["evidence"] = [{"kind": "device_log", "path": "../../outside.txt"}]
    document["steps"][0]["evidence"] = [{"kind": "device_log", "path": str(outside)}]
    (run_dir / "result.json").write_text(json.dumps(document))
    allure_dir = tmp_path / "allure"
    export_run(run_dir, allure_dir)
    for path in allure_dir.iterdir():
        assert path.read_bytes() != b"not evidence", path


def test_cli_exports_run_directories(contracts, catalog, environment, tmp_path, root, monkeypatch) -> None:
    monkeypatch.chdir(root)
    run(contracts, catalog, environment, tmp_path, API_PLAN, Platform.API)
    run(contracts, catalog, environment, tmp_path, API_PLAN, Platform.API)
    assert len(find_run_dirs([tmp_path])) == 2
    allure_dir = tmp_path / "allure"
    (allure_dir).mkdir()
    (allure_dir / "stale-result.json").write_text("{}")
    result = CliRunner().invoke(
        main, ["report", "allure", str(tmp_path / "runs"), "--alluredir", str(allure_dir), "--clean-alluredir"]
    )
    assert result.exit_code == 0, result.output
    assert "exported 2 run(s)" in result.output
    assert not (allure_dir / "stale-result.json").exists()
    assert len(_results(allure_dir)) == 2

    empty = CliRunner().invoke(main, ["report", "allure", str(allure_dir), "--alluredir", str(tmp_path / "x")])
    assert empty.exit_code == 2


def test_executor_is_written_only_on_github_actions(tmp_path) -> None:
    assert write_executor(tmp_path, {}) is None
    path = write_executor(
        tmp_path,
        {
            "GITHUB_ACTIONS": "true",
            "GITHUB_REPOSITORY": "o/r",
            "GITHUB_RUN_ID": "42",
            "GITHUB_RUN_NUMBER": "7",
            "GITHUB_WORKFLOW": "API tests",
        },
    )
    assert path is not None
    executor = json.loads(path.read_text())
    assert executor["buildUrl"] == "https://github.com/o/r/actions/runs/42"
    assert executor["buildOrder"] == 7
