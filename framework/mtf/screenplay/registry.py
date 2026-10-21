"""Registration of Screenplay tasks, questions and fixtures together with their specs.

Each implementation is a class with a `spec` (`ActionSpec`). The registry instantiates them from
validated plan parameters; it is the only place that maps DSL names to code, and that mapping is
fixed at import time (plans cannot add or load code).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar, TypeVar

from mtf.core.catalog import ActionKind, ActionSpec, Catalog
from mtf.core.errors import ConfigurationError
from mtf.screenplay.actor import Actor


class Task:
    """A business-level activity performed by an actor."""

    spec: ClassVar[ActionSpec]

    def __init__(self, **params: Any) -> None:
        self.params = params

    def perform_as(self, actor: Actor) -> None:
        raise NotImplementedError

    def param(self, name: str) -> Any:
        if name in self.params:
            return self.params[name]
        return self.spec.params[name].default

    def __repr__(self) -> str:
        return f"{self.spec.name}({self.params})"


class Question:
    """Something an actor can find out about the system: a UI state or an API state."""

    spec: ClassVar[ActionSpec]

    def __init__(self, **params: Any) -> None:
        self.params = params

    def answered_by(self, actor: Actor) -> Any:
        raise NotImplementedError

    def param(self, name: str) -> Any:
        if name in self.params:
            return self.params[name]
        return self.spec.params[name].default


class Fixture(Task):
    """Setup or cleanup work (reset data, launch the app, seed a cart)."""


T = TypeVar("T", bound=type[Task] | type[Question])


class ScreenplayRegistry:
    def __init__(self) -> None:
        self._classes: dict[tuple[ActionKind, str], type[Task] | type[Question]] = {}

    def register(self, cls: T) -> T:
        spec = cls.spec
        key = (spec.kind, spec.name)
        if key in self._classes:
            raise ConfigurationError(f"duplicate {spec.kind.value} {spec.name!r}")
        expected = {ActionKind.TASK: Task, ActionKind.FIXTURE: Fixture, ActionKind.QUESTION: Question}[spec.kind]
        if not issubclass(cls, expected):
            raise ConfigurationError(f"{cls.__name__} must subclass {expected.__name__}")
        self._classes[key] = cls
        return cls

    def create(self, kind: ActionKind, name: str, params: dict[str, Any]) -> Any:
        cls = self._classes.get((kind, name))
        if cls is None:
            raise ConfigurationError(f"unknown {kind.value} {name!r}")
        return cls(**params)

    def specs(self) -> list[ActionSpec]:
        return [cls.spec for cls in self._classes.values()]


REGISTRY = ScreenplayRegistry()
register: Callable[[T], T] = REGISTRY.register


def build_catalog() -> Catalog:
    """The full catalog: registered tasks, questions and fixtures plus the built-in interactions."""
    # Importing the modules registers their classes; done here because they import this module.
    from mtf.screenplay import fixtures, questions, tasks  # noqa: F401, PLC0415
    from mtf.screenplay.interactions import INTERACTION_SPECS  # noqa: PLC0415

    return Catalog([*REGISTRY.specs(), *INTERACTION_SPECS])
