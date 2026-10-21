"""Run events and a synchronous event bus (Observer).

Runners and the orchestrator publish events; the console printer, the JSONL event log and the
report builder subscribe. A failing subscriber is isolated: it can never change a step's result.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

log = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class Event:
    type: str
    run_id: str
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "run_id": self.run_id,
            "timestamp": self.timestamp.isoformat(),
            **self.payload,
        }


# Event types. Strings rather than classes keep the JSONL log self-describing.
RUN_STATE_CHANGED = "run.state_changed"
RUN_FINISHED = "run.finished"
STEP_STARTED = "step.started"
STEP_FINISHED = "step.finished"
STEP_RETRY = "step.retry"
EVIDENCE_CAPTURED = "evidence.captured"
RUNNER_LOG = "runner.log"
API_EXCHANGE = "api.exchange"

Subscriber = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._subscribers: list[tuple[str | None, Subscriber]] = []
        self._lock = threading.Lock()

    def subscribe(self, subscriber: Subscriber, event_type: str | None = None) -> None:
        with self._lock:
            self._subscribers.append((event_type, subscriber))

    def publish(self, event: Event) -> None:
        with self._lock:
            subscribers = list(self._subscribers)
        for event_type, subscriber in subscribers:
            if event_type is not None and event_type != event.type:
                continue
            try:
                subscriber(event)
            except Exception:  # a broken observer must not affect the run
                log.exception("event subscriber failed for %s", event.type)
