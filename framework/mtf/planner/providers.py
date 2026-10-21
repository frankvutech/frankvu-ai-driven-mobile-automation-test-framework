"""AI provider port and adapters (Strategy).

A provider turns (system prompt, user prompt, JSON Schema) into one JSON object. Providers:

* `AnthropicProvider` – Claude via the official `anthropic` SDK with structured outputs.
* `RecordedProvider` – replays recorded responses from a file: deterministic, offline, for tests,
  demos and reproducing a planning session without an AI service.

No provider secret is ever hard-coded: the Anthropic SDK resolves credentials from the environment.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol

from mtf.core.errors import AIUnavailable, ConfigurationError

DEFAULT_MODEL = "claude-opus-5-5"
MODEL_ENV_VAR = "MTF_AI_MODEL"


class PlannerProvider(Protocol):
    name: str
    model: str | None

    def complete_json(self, *, system: str, user: str, schema: dict[str, Any], purpose: str) -> dict[str, Any]:
        """Returns one JSON object conforming to `schema`, or raises AIUnavailable."""


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, model: str | None = None, *, effort: str = "high", max_tokens: int = 32_000) -> None:
        try:
            import anthropic  # noqa: PLC0415 - optional dependency
        except ImportError as exc:
            raise AIUnavailable("the Anthropic provider needs `pip install mtf[anthropic]`") from exc
        self._anthropic = anthropic
        self._model: str = model or os.environ.get(MODEL_ENV_VAR) or DEFAULT_MODEL
        self.model: str | None = self._model
        self.effort = effort
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic()

    def complete_json(self, *, system: str, user: str, schema: dict[str, Any], purpose: str) -> dict[str, Any]:
        anthropic = self._anthropic
        output_config: Any = {"effort": self.effort, "format": {"type": "json_schema", "schema": schema}}
        try:
            # Streaming avoids HTTP timeouts on long structured outputs; the final message is all we need.
            with self._client.beta.messages.stream(
                model=self._model,
                max_tokens=self.max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config=output_config,
                cache_control={"type": "ephemeral"},  # the catalog/contract system prompt is stable
                # If a safety classifier declines, the API re-runs the request on a fallback model.
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            ) as stream:
                message = stream.get_final_message()
        except anthropic.AuthenticationError as exc:
            raise AIUnavailable(
                "Anthropic credentials are missing or invalid (ANTHROPIC_API_KEY / ant auth login)"
            ) from exc
        except anthropic.RateLimitError as exc:
            raise AIUnavailable(f"Anthropic rate limit reached for {purpose}; retry later") from exc
        except anthropic.APIStatusError as exc:
            raise AIUnavailable(f"Anthropic API error {exc.status_code} during {purpose}: {exc.message}") from exc
        except anthropic.APIConnectionError as exc:
            raise AIUnavailable(f"cannot reach the Anthropic API during {purpose}") from exc
        if message.stop_reason == "refusal":
            raise AIUnavailable(f"the model declined the {purpose} request")
        if message.stop_reason == "max_tokens":
            raise AIUnavailable(f"the {purpose} response was cut off at max_tokens={self.max_tokens}")
        text = next((block.text for block in message.content if block.type == "text"), None)
        if text is None:
            raise AIUnavailable(f"no text output for {purpose}")
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise AIUnavailable(f"the {purpose} output is not JSON") from exc
        if not isinstance(data, dict):
            raise AIUnavailable(f"the {purpose} output is not a JSON object")
        return data


class RecordedProvider:
    """Replays responses recorded as {"provider": ..., "model": ..., "responses": {purpose: [obj, ...]}}.

    Each call for a purpose consumes the next recorded object, so a planning session with repair
    rounds replays exactly. Running out of recordings is an error, never an invented answer.
    """

    name = "recorded"

    def __init__(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ConfigurationError(f"cannot read recorded responses {path}: {exc}") from exc
        self.model = data.get("model")
        self.recorded_from = data.get("provider")
        self._responses: dict[str, list[dict[str, Any]]] = {k: list(v) for k, v in data.get("responses", {}).items()}
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, *, system: str, user: str, schema: dict[str, Any], purpose: str) -> dict[str, Any]:
        self.calls.append({"purpose": purpose, "system": system, "user": user})
        queue = self._responses.get(purpose) or []
        if not queue:
            raise AIUnavailable(f"no recorded response left for {purpose!r}")
        return queue.pop(0)


def make_provider(kind: str, *, model: str | None = None, recording: Path | None = None) -> PlannerProvider:
    if kind == "anthropic":
        return AnthropicProvider(model)
    if kind == "recorded":
        if recording is None:
            raise ConfigurationError("the recorded provider needs --recording FILE")
        return RecordedProvider(recording)
    raise ConfigurationError(f"unknown AI provider {kind!r}")
