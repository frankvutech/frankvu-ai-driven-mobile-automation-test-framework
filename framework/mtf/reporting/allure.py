"""Exports finished runs as Allure results (`allure-results/`), viewable with the Allure 2 or 3 CLI.

The source is the run directory as written by the orchestrator: the redacted `result.json` and the
evidence files that already went through the final redaction scrub. Nothing is read from a device,
the backend or the environment, so an export can never add a secret that the run record does not
already hide. Evidence paths are confined to the run directory: a tampered `result.json` cannot
make the exporter copy files from elsewhere.

One plan run becomes one Allure test result. Preconditions and cleanup become set-up and tear-down
fixtures of a container, plan steps become steps, retries become nested attempts, and evidence and
API exchanges become attachments of the step that produced them.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from collections.abc import Iterable, Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from mtf.core.errors import ConfigurationError
from mtf.reporting.models import RunResult
from mtf.reporting.writer import HTML_FILE, RESULT_FILE

ENVIRONMENT_FILE = "environment.properties"
CATEGORIES_FILE = "categories.json"
EXECUTOR_FILE = "executor.json"

_UUID_NAMESPACE = uuid.UUID("6f3c7f43-3c7e-4d43-9f0e-2b8f2f4a8d11")
_MAX_PARAMETER_CHARS = 300

_RUN_STATUS = {"passed": "passed", "failed": "failed", "error": "broken", "cancelled": "skipped", "rejected": "broken"}
_STEP_STATUS = {
    "passed": "passed",
    "failed": "failed",
    "error": "broken",
    "skipped": "skipped",
    "cancelled": "skipped",
    "pending": "unknown",
}
_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".json": "application/json",
    ".jsonl": "text/plain",
    ".xml": "application/xml",
    ".html": "text/html",
    ".txt": "text/plain",
    ".log": "text/plain",
    ".ips": "text/plain",
}
_PLATFORM_LABELS = {"android": "Android", "ios": "iOS", "api": "API"}

# Allure categories match the trace with a full-match regular expression; the trace of every
# non-passing result starts with `failure_category: <category>`.
CATEGORIES: list[dict[str, Any]] = [
    {
        "name": "Product defects (assertion failed)",
        "matchedStatuses": ["failed"],
        "traceRegex": r"(?s)failure_category: (assertion_failed|api_assertion_failed)\b.*",
    },
    {
        "name": "Contract violations (test IDs or OpenAPI)",
        "matchedStatuses": ["failed", "broken"],
        "traceRegex": r"(?s)failure_category: (selector_contract_violation|api_contract_violation)\b.*",
    },
    {
        "name": "Element not found or timeout",
        "matchedStatuses": ["failed", "broken"],
        "traceRegex": r"(?s)failure_category: (element_not_found|timeout)\b.*",
    },
    {
        "name": "App crash",
        "matchedStatuses": ["failed", "broken"],
        "traceRegex": r"(?s)failure_category: app_crash\b.*",
    },
    {
        "name": "Infrastructure, runner or artifact",
        "matchedStatuses": ["failed", "broken"],
        "traceRegex": r"(?s)failure_category: (runner_error|infrastructure_error|artifact_mismatch)\b.*",
    },
    {
        "name": "Plan, configuration or policy rejected",
        "matchedStatuses": ["failed", "broken"],
        "traceRegex": r"(?s)failure_category: (configuration_error|plan_validation_error|policy_violation)\b.*",
    },
    {"name": "Cancelled", "matchedStatuses": ["skipped"], "traceRegex": r"(?s)failure_category: cancelled\b.*"},
]


def _digest(*parts: str) -> str:
    return hashlib.md5("\x1f".join(parts).encode("utf-8"), usedforsecurity=False).hexdigest()


def _epoch_ms(timestamp: str | None) -> int | None:
    if not timestamp:
        return None
    return int(datetime.fromisoformat(timestamp).timestamp() * 1000)


def _text(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text if len(text) <= _MAX_PARAMETER_CHARS else text[: _MAX_PARAMETER_CHARS - 1] + "…"


def _timing(start: str | None, stop: str | None) -> dict[str, int]:
    times = {"start": _epoch_ms(start), "stop": _epoch_ms(stop)}
    return {key: value for key, value in times.items() if value is not None}


def _trace(category: str | None, lines: Iterable[str]) -> str | None:
    body = [f"failure_category: {category}"] if category else []
    body.extend(line for line in lines if line)
    return "\n".join(body) or None


def _status_details(message: str | None, trace: str | None, *, flaky: bool = False) -> dict[str, Any]:
    details: dict[str, Any] = {}
    if message:
        details["message"] = message
    if trace:
        details["trace"] = trace
    if flaky:
        details["flaky"] = True
    return details


def _join(*values: Any) -> str:
    return " ".join(str(v) for v in values if v)


def _device_name(device: Mapping[str, Any]) -> str | None:
    name = device.get("name") or device.get("model") or device.get("serial")
    return str(name) if name else None


class _Exporter:
    def __init__(self, run_dir: Path, results_dir: Path, document: dict[str, Any]) -> None:
        self.run_dir = run_dir.resolve()
        self.results_dir = results_dir
        self.r = document
        self.test_uuid = str(uuid.uuid5(_UUID_NAMESPACE, f"test:{document['run_id']}"))
        self.exchanges = {e.get("id"): e for e in document.get("api_exchanges", [])}

    # --- attachments ---------------------------------------------------------------------------
    def _attachment_file(self, key: str, suffix: str) -> tuple[str, Path]:
        source = f"{uuid.uuid5(_UUID_NAMESPACE, f'{self.r["run_id"]}:{key}')}-attachment{suffix}"
        return source, self.results_dir / source

    def _attach_file(self, relative: str, name: str, key: str) -> dict[str, str] | None:
        path = (self.run_dir / relative).resolve()
        if not path.is_relative_to(self.run_dir) or not path.is_file():
            return None
        source, target = self._attachment_file(key, path.suffix)
        shutil.copyfile(path, target)
        return {"name": name, "source": source, "type": _MIME_TYPES.get(path.suffix, "application/octet-stream")}

    def _attach_json(self, value: Any, name: str, key: str) -> dict[str, str]:
        source, target = self._attachment_file(key, ".json")
        target.write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return {"name": name, "source": source, "type": "application/json"}

    def _attach_text(self, text: str, name: str, key: str) -> dict[str, str]:
        source, target = self._attachment_file(key, ".txt")
        target.write_text(text, encoding="utf-8")
        return {"name": name, "source": source, "type": "text/plain"}

    def _evidence(self, refs: Iterable[Mapping[str, Any]], key: str) -> list[dict[str, str]]:
        attachments = []
        for n, ref in enumerate(refs):
            name = str(ref.get("kind", "evidence"))
            if ref.get("description"):
                name = f"{name}: {ref['description']}"
            if not ref.get("path"):  # suppressed or failed capture: the reason is still worth showing
                attachments.append(self._attach_text(name + "\n", name, f"{key}:evidence:{n}"))
                continue
            attachment = self._attach_file(str(ref["path"]), name, f"{key}:evidence:{n}")
            if attachment:
                attachments.append(attachment)
        return attachments

    # --- steps ---------------------------------------------------------------------------------
    def _step(self, step: Mapping[str, Any]) -> dict[str, Any]:
        key = f"step:{step['index']}"
        name = step["kind"] if step["kind"] == step["name"] else f"{step['kind']} {step['name']}"
        if step.get("target"):
            name += f" → {step['target']}"
        if step.get("description"):
            name += f" — {step['description']}"
        parameters = [{"name": k, "value": _text(v)} for k, v in (step.get("params") or {}).items()]
        if step.get("status") in ("failed", "error"):
            parameters += [
                {"name": label, "value": _text(step.get(field))}
                for label, field in (("expected", "expected"), ("actual", "actual"))
                if step.get(field) is not None
            ]
        attempts = step.get("attempts") or []
        sub_steps = [
            {
                "name": f"attempt {a['attempt']}",
                "status": _STEP_STATUS.get(a["status"], "unknown"),
                "stage": "finished",
                "statusDetails": _status_details(a.get("message"), _trace(a.get("failure_category"), [])),
                "parameters": [{"name": "duration_ms", "value": str(a.get("duration_ms"))}],
            }
            for a in attempts
            if len(attempts) > 1
        ]
        sub_steps += [
            {
                "name": str(check.get("description") or check.get("kind") or "check"),
                "status": "passed" if check.get("passed") else "failed",
                "stage": "finished",
                "parameters": [
                    {"name": label, "value": _text(check.get(label))}
                    for label in ("expected", "actual")
                    if check.get(label) is not None
                ],
            }
            for check in step.get("checks") or []
        ]
        attachments = self._evidence(step.get("evidence") or [], key)
        exchanges = [self.exchanges[i] for i in step.get("api_exchange_ids") or [] if i in self.exchanges]
        if exchanges:
            attachments.append(self._attach_json(exchanges, "API exchanges", f"{key}:exchanges"))
        return {
            "name": name,
            "status": _STEP_STATUS.get(step.get("status", "pending"), "unknown"),
            "stage": "finished",
            "statusDetails": _status_details(step.get("message"), _trace(step.get("failure_category"), [])),
            "parameters": parameters,
            "steps": sub_steps,
            "attachments": attachments,
            **_timing(step.get("started_at"), step.get("finished_at")),
        }

    # --- result --------------------------------------------------------------------------------
    def _labels(self) -> list[dict[str, str]]:
        plan, platform = self.r["plan"], self.r["platform"]
        tags = list(plan.get("tags") or [])
        if plan.get("origin") == "ai_generated":
            tags.append("ai-generated")
        # The platform is already the parent suite: group by the first tag that names a feature area.
        area = next((tag for tag in tags if tag not in _PLATFORM_LABELS), plan["name"])
        labels = [
            ("framework", "mtf"),
            ("parentSuite", _PLATFORM_LABELS.get(platform, platform)),
            ("suite", area),
            ("epic", _PLATFORM_LABELS.get(platform, platform)),
            ("feature", area),
            ("story", plan["name"]),
        ]
        if plan.get("path"):
            labels.append(("package", Path(plan["path"]).parent.as_posix().replace("/", ".")))
        if host := _device_name(self.r.get("device") or {}):
            labels.append(("host", host))
        labels += [("tag", tag) for tag in tags]
        return [{"name": name, "value": value} for name, value in labels]

    def _parameters(self) -> list[dict[str, str]]:
        device = self.r.get("device") or {}
        artifact = self.r.get("artifact") or {}
        values = {
            "platform": self.r["platform"],
            "environment": self.r["environment"]["name"],
            "device": _device_name(device),
            "os": device.get("os"),
            "app version": _join(artifact.get("version_name"), artifact.get("version_code")),
            "app sha256": (artifact.get("sha256") or "")[:12],
        }
        return [{"name": k, "value": str(v)} for k, v in values.items() if v]

    def _run_attachments(self) -> list[dict[str, str]]:
        run_level = [e for e in self.r.get("evidence", []) if e.get("step_index") is None]
        attachments = self._evidence(run_level, "run")
        for name, file in (("mtf result.json", RESULT_FILE), ("mtf report.html", HTML_FILE)):
            if attachment := self._attach_file(file, name, f"run:{file}"):
                attachments.append(attachment)
        if advisory := self.r.get("advisory"):
            attachments.append(self._attach_json(advisory, advisory.get("label", "advisory"), "run:advisory"))
        return attachments

    def _trace_lines(self) -> list[str]:
        lines = [f"warning: {w}" for w in self.r.get("warnings", [])]
        lines += [
            f"validation {i.get('severity', '')}: {i.get('location', '')} {i.get('message', '')}".strip()
            for i in self.r.get("validation_issues", [])
        ]
        return lines

    def export(self) -> list[Path]:
        status = _RUN_STATUS.get(self.r["status"], "unknown")
        steps = self.r.get("steps", [])
        plan = self.r["plan"]
        flaky = status == "passed" and (self.r.get("summary") or {}).get("retried", 0) > 0
        trace = _trace(self.r.get("failure_category"), self._trace_lines()) if status != "passed" else None
        test = {
            "uuid": self.test_uuid,
            # Same plan, platform and environment: Allure shows reruns as retries and builds its history.
            "historyId": _digest(plan["name"], self.r["platform"], self.r["environment"]["name"]),
            "testCaseId": _digest(plan["name"]),
            "fullName": f"{self.r['platform']}.{plan['name']}",
            "name": plan.get("title") or plan["name"],
            "description": plan.get("description") or "",
            "status": status,
            "stage": "finished",
            "statusDetails": _status_details(self.r.get("message"), trace, flaky=flaky),
            "labels": self._labels(),
            "parameters": self._parameters(),
            "links": [],
            "steps": [self._step(s) for s in steps if s.get("section") == "steps"],
            "attachments": self._run_attachments(),
            **_timing(self.r.get("started_at"), self.r.get("finished_at")),
        }
        container = {
            "uuid": str(uuid.uuid5(_UUID_NAMESPACE, f"container:{self.r['run_id']}")),
            "name": plan["name"],
            "children": [self.test_uuid],
            "befores": [self._step(s) for s in steps if s.get("section") == "preconditions"],
            "afters": [self._step(s) for s in steps if s.get("section") == "cleanup"],
            **_timing(self.r.get("started_at"), self.r.get("finished_at")),
        }
        written = []
        for document, suffix in ((test, "result"), (container, "container")):
            path = self.results_dir / f"{document['uuid']}-{suffix}.json"
            path.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            written.append(path)
        return written


def find_run_dirs(paths: Iterable[Path]) -> list[Path]:
    """Accepts run directories or any directory above them (e.g. `reports/` or `reports/runs/`)."""
    found: list[Path] = []
    for path in paths:
        if (path / RESULT_FILE).is_file():
            found.append(path)
        else:
            found.extend(sorted(p.parent for p in path.rglob(RESULT_FILE)))
    return list(dict.fromkeys(found))


def prepare_results_dir(results_dir: Path, *, clean: bool = False) -> None:
    if clean and results_dir.exists():
        if not results_dir.is_dir():
            raise ConfigurationError(f"Allure results path {results_dir} is not a directory")
        shutil.rmtree(results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)


def export_run(run_dir: Path, results_dir: Path) -> list[Path]:
    """Writes one finished run into `results_dir`. Exporting the same run again overwrites it."""
    try:
        document: dict[str, Any] = json.loads((run_dir / RESULT_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigurationError(f"cannot read {run_dir / RESULT_FILE}: {exc}") from exc
    RunResult.model_validate(document)  # refuse a tampered or incompatible result
    results_dir.mkdir(parents=True, exist_ok=True)
    written = _Exporter(run_dir, results_dir, document).export()
    _merge_environment(results_dir, _environment_properties(document))
    (results_dir / CATEGORIES_FILE).write_text(json.dumps(CATEGORIES, indent=2) + "\n", encoding="utf-8")
    return written


def _environment_properties(document: Mapping[str, Any]) -> dict[str, str]:
    platform = _PLATFORM_LABELS.get(document["platform"], document["platform"])
    environment, runner = document["environment"], document["runner"]
    device, artifact = document.get("device") or {}, document.get("artifact") or {}
    values: dict[str, Any] = {
        "Environment": environment["name"],
        "Backend": environment["backend_base_url"],
        "MTF": runner["framework_version"],
        f"{platform}.Runner": _join(runner.get("runner"), runner.get("runner_version")),
        f"{platform}.Device": _device_name(device),
        f"{platform}.OS": device.get("os"),
        f"{platform}.App": _join(
            artifact.get("identifier"), artifact.get("version_name"), artifact.get("version_code")
        ),
        f"{platform}.App.sha256": artifact.get("sha256"),
    }
    return {key: str(value) for key, value in values.items() if value}


def _escape_property(text: str) -> str:
    return text.replace("\\", "\\\\").replace("\n", "\\n").replace("=", "\\=").replace(":", "\\:")


def _merge_environment(results_dir: Path, properties: Mapping[str, str]) -> None:
    path = results_dir / ENVIRONMENT_FILE
    merged: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and not line.startswith("#"):
                merged[key.replace("\\:", ":").replace("\\=", "=")] = value
    merged.update({key: _escape_property(value) for key, value in properties.items()})
    path.write_text(
        "".join(f"{_escape_property(key)}={value}\n" for key, value in sorted(merged.items())), encoding="utf-8"
    )


def write_executor(results_dir: Path, environ: Mapping[str, str]) -> Path | None:
    """Links the report to the CI build that produced it (GitHub Actions only)."""
    if environ.get("GITHUB_ACTIONS") != "true":
        return None
    server, repository, run_id = (
        environ.get("GITHUB_SERVER_URL", "https://github.com"),
        environ.get("GITHUB_REPOSITORY", ""),
        environ.get("GITHUB_RUN_ID", ""),
    )
    number = environ.get("GITHUB_RUN_NUMBER", "")
    executor = {
        "name": "GitHub Actions",
        "type": "github",
        "url": f"{server}/{repository}",
        "buildOrder": int(number) if number.isdigit() else None,
        "buildName": f"{environ.get('GITHUB_WORKFLOW', 'workflow')} #{number}",
        "buildUrl": f"{server}/{repository}/actions/runs/{run_id}",
    }
    path = results_dir / EXECUTOR_FILE
    path.write_text(json.dumps(executor, indent=2) + "\n", encoding="utf-8")
    return path
