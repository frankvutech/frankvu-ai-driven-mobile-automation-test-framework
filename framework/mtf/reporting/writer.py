"""Writes `result.json` and `report.html` for a run. Everything passes through the redactor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, PackageLoader, select_autoescape

from mtf.core.redaction import Redactor
from mtf.reporting.models import RunResult

RESULT_FILE = "result.json"
HTML_FILE = "report.html"


def _jinja() -> Environment:
    env = Environment(
        loader=PackageLoader("mtf.reporting", "templates"),
        autoescape=select_autoescape(["html", "j2"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["pretty"] = lambda value: json.dumps(value, indent=2, ensure_ascii=False, default=str)
    return env


def result_document(result: RunResult, redactor: Redactor) -> dict[str, Any]:
    document: dict[str, Any] = redactor.redact(result.model_dump(mode="json"))
    return document


def render_html(document: dict[str, Any]) -> str:
    return _jinja().get_template("report.html.j2").render(r=document)


def write_reports(result: RunResult, run_dir: Path, redactor: Redactor) -> tuple[Path, Path]:
    run_dir.mkdir(parents=True, exist_ok=True)
    document = result_document(result, redactor)
    json_path = run_dir / RESULT_FILE
    json_path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    html_path = run_dir / HTML_FILE
    html_path.write_text(render_html(document), encoding="utf-8")
    return json_path, html_path


def rerender(run_dir: Path) -> Path:
    """Regenerates report.html from an existing result.json (e.g. after adding an advisory)."""
    document = json.loads((run_dir / RESULT_FILE).read_text(encoding="utf-8"))
    RunResult.model_validate(document)  # refuse to render a tampered or incompatible result
    html_path = run_dir / HTML_FILE
    html_path.write_text(render_html(document), encoding="utf-8")
    return html_path
