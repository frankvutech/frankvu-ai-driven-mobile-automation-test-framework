"""Actors, abilities and the Performable/Question protocols."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from mtf.core.errors import ConfigurationError

if TYPE_CHECKING:
    from mtf.contracts.test_data import SecretProvider, TestDataRegistry
    from mtf.core.redaction import Redactor
    from mtf.core.types import Platform


class Ability:
    """Something an actor can do: operate the mobile app, call the backend API, ..."""


A = TypeVar("A", bound=Ability)


class Performable(Protocol):
    """A task or interaction."""

    def perform_as(self, actor: Actor) -> None: ...


class Question(Protocol):
    def answered_by(self, actor: Actor) -> Any: ...


@dataclass
class ScreenplayContext:
    """What tasks and questions may read: test data, secrets, run variables, defaults."""

    platform: Platform
    test_data: TestDataRegistry
    secrets: SecretProvider
    redactor: Redactor
    default_timeout_ms: int = 10_000
    variables: dict[str, Any] = field(default_factory=dict)
    screen_roots: list[str] = field(default_factory=list)  # registered screen-root IDs on this platform

    def secret(self, ref: str) -> str:
        """Resolves a secret reference and registers the value for redaction before returning it."""
        value = self.secrets.resolve(ref)
        self.redactor.add_secret(value)
        return value


class Actor:
    def __init__(self, name: str, context: ScreenplayContext, account_ref: str | None = None) -> None:
        self.name = name
        self.context = context
        self.account_ref = account_ref
        self._abilities: dict[type[Ability], Ability] = {}

    def __repr__(self) -> str:
        return f"Actor({self.name!r}, account={self.account_ref!r})"

    # --- abilities -----------------------------------------------------------------------------
    def can(self, *abilities: Ability) -> Actor:
        for ability in abilities:
            self._abilities[type(ability)] = ability
        return self

    def ability(self, kind: type[A]) -> A:
        for registered, ability in self._abilities.items():
            if issubclass(registered, kind):
                return ability  # type: ignore[return-value]
        raise ConfigurationError(f"actor {self.name!r} cannot {kind.__name__} in this run")

    def has_ability(self, kind: type[Ability]) -> bool:
        return any(issubclass(registered, kind) for registered in self._abilities)

    # --- identity ------------------------------------------------------------------------------
    @property
    def account(self) -> dict[str, Any]:
        if self.account_ref is None:
            raise ConfigurationError(f"actor {self.name!r} has no account")
        return dict(self.context.test_data.accounts[self.account_ref])

    @property
    def email(self) -> str:
        return str(self.account["email"])

    def password(self) -> str:
        if self.account_ref is None:
            raise ConfigurationError(f"actor {self.name!r} has no account")
        return self.context.secret(f"accounts.{self.account_ref}.password")

    # --- screenplay verbs ----------------------------------------------------------------------
    def attempts_to(self, *performables: Performable) -> None:
        for performable in performables:
            performable.perform_as(self)

    def asks(self, question: Question) -> Any:
        return question.answered_by(self)
