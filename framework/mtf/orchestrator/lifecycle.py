"""Run lifecycle (State pattern): explicit states and the only transitions allowed between them."""

from __future__ import annotations

from collections.abc import Callable
from enum import StrEnum

from mtf.core.events import utc_now


class RunState(StrEnum):
    CREATED = "created"
    VALIDATING = "validating"
    REJECTED = "rejected"  # terminal: invalid plan or configuration, nothing was launched
    PREPARING = "preparing"  # artifacts verified and installed, runner started
    SETTING_UP = "setting_up"  # preconditions
    EXECUTING = "executing"  # steps
    CLEANING_UP = "cleaning_up"  # cleanup steps, always attempted once preparation started
    REPORTING = "reporting"
    FINISHED = "finished"  # terminal


TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset({RunState.VALIDATING}),
    RunState.VALIDATING: frozenset({RunState.REJECTED, RunState.PREPARING}),
    RunState.REJECTED: frozenset({RunState.REPORTING}),
    RunState.PREPARING: frozenset({RunState.SETTING_UP, RunState.CLEANING_UP}),
    RunState.SETTING_UP: frozenset({RunState.EXECUTING, RunState.CLEANING_UP}),
    RunState.EXECUTING: frozenset({RunState.CLEANING_UP}),
    RunState.CLEANING_UP: frozenset({RunState.REPORTING}),
    RunState.REPORTING: frozenset({RunState.FINISHED}),
    RunState.FINISHED: frozenset(),
}


class IllegalTransition(RuntimeError):
    pass


class RunLifecycle:
    def __init__(self, on_change: Callable[[RunState, RunState, str | None], None] | None = None) -> None:
        self.state = RunState.CREATED
        self.history: list[tuple[RunState, str, str | None]] = [(RunState.CREATED, utc_now().isoformat(), None)]
        self._on_change = on_change

    def advance(self, new_state: RunState, note: str | None = None) -> None:
        if new_state not in TRANSITIONS[self.state]:
            raise IllegalTransition(f"cannot go from {self.state.value} to {new_state.value}")
        previous, self.state = self.state, new_state
        self.history.append((new_state, utc_now().isoformat(), note))
        if self._on_change is not None:
            self._on_change(previous, new_state, note)

    @property
    def finished(self) -> bool:
        return self.state == RunState.FINISHED
