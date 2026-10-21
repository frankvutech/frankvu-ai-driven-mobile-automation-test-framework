"""Abilities: what an actor uses to act on the system under test."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mtf.screenplay.actor import Ability, Actor

if TYPE_CHECKING:
    from mtf.api_runner.client import ApiClient
    from mtf.core.ports import UiDriver
    from mtf.screenplay.robots.factory import Robots


class OperateMobileApp(Ability):
    """Drive the installed app through the platform runner, via Screen Robots."""

    def __init__(self, driver: UiDriver, robots: Robots) -> None:
        self.driver = driver
        self.robots = robots


class CallBackendApi(Ability):
    """Call the backend REST API (setup, verification and API-only scenarios)."""

    def __init__(self, client: ApiClient) -> None:
        self.client = client


def robots_of(actor: Actor) -> Robots:
    return actor.ability(OperateMobileApp).robots


def driver_of(actor: Actor) -> UiDriver:
    return actor.ability(OperateMobileApp).driver


def api_of(actor: Actor) -> ApiClient:
    return actor.ability(CallBackendApi).client
