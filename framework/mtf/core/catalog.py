"""Specifications of every action a plan may use (tasks, questions, fixtures, interactions).

The specs are declared next to their implementations (Screenplay tasks and questions, fixtures)
and collected into a `Catalog`. The validator and the AI planner only ever see the specs; the
interpreter maps the same names to implementations. A test asserts the two never diverge.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from mtf.core.types import Platform


class ParamKind(StrEnum):
    PRODUCT = "product"  # a product ID from contracts/test-data.yaml
    ANY_PRODUCT_ID = "any_product_id"  # a registered or a deliberately missing product ID
    ACCOUNT = "account"  # an account reference
    TEXT = "text"  # free text (may contain ${...} references)
    PASSWORD = "password"  # noqa: S105 - a parameter kind, not a credential; must be a ${secret:...} or ${inputs....} reference, never a literal
    INT = "int"
    BOOL = "bool"
    ENUM = "enum"
    TEST_ID = "test_id"  # a registered test ID
    DEEP_LINK = "deep_link"  # a deep-link name from test data


@dataclass(frozen=True)
class ParamSpec:
    kind: ParamKind
    description: str = ""
    required: bool = True
    default: Any = None
    values: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.kind.value, "required": self.required}
        if self.description:
            out["description"] = self.description
        if self.values:
            out["values"] = list(self.values)
        if self.minimum is not None:
            out["min"] = self.minimum
        if self.maximum is not None:
            out["max"] = self.maximum
        if not self.required and self.default is not None:
            out["default"] = self.default
        return out


class ActionKind(StrEnum):
    TASK = "task"
    QUESTION = "question"
    FIXTURE = "fixture"
    INTERACTION = "interaction"


UI_PLATFORMS = frozenset({Platform.ANDROID, Platform.IOS})
ALL_PLATFORMS = frozenset({Platform.ANDROID, Platform.IOS, Platform.API})


@dataclass(frozen=True)
class ActionSpec:
    name: str
    kind: ActionKind
    description: str
    params: dict[str, ParamSpec] = field(default_factory=dict)
    platforms: frozenset[Platform] = UI_PLATFORMS
    needs_actor: bool = False
    needs_target: bool = False  # interactions only
    optional_target: bool = False  # interactions only (for example a swipe on the whole screen)
    returns: str | None = None  # questions only: bool | text | int
    uses_test_support: bool = False  # calls reset/seed endpoints (needs an environment that allows them)
    # Registered test IDs the implementation's Screen Robots touch on every platform. The validator
    # checks them per platform, so a contract gap is reported before anything is launched.
    test_ids: tuple[str, ...] = ()

    def describe(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "platforms": sorted(p.value for p in self.platforms),
            "params": {k: v.describe() for k, v in self.params.items()},
        }
        if self.needs_actor:
            out["needs_actor"] = True
        if self.needs_target:
            out["needs_target"] = True
        if self.returns:
            out["returns"] = self.returns
        if self.test_ids:
            out["uses_test_ids"] = list(self.test_ids)
        return out


class Catalog:
    def __init__(self, specs: Iterable[ActionSpec]) -> None:
        self._specs: dict[tuple[ActionKind, str], ActionSpec] = {}
        for spec in specs:
            key = (spec.kind, spec.name)
            if key in self._specs:
                raise ValueError(f"duplicate {spec.kind.value} {spec.name!r}")
            self._specs[key] = spec

    def get(self, kind: ActionKind, name: str) -> ActionSpec | None:
        return self._specs.get((kind, name))

    def of_kind(self, kind: ActionKind) -> list[ActionSpec]:
        return sorted((s for (k, _), s in self._specs.items() if k == kind), key=lambda s: s.name)

    def names(self, kind: ActionKind) -> list[str]:
        return [s.name for s in self.of_kind(kind)]

    def describe(self) -> dict[str, Any]:
        return {kind.value + "s": [s.describe() for s in self.of_kind(kind)] for kind in ActionKind}
