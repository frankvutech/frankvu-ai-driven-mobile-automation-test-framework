"""Fixtures: deterministic setup and cleanup (backend data, app state)."""

from __future__ import annotations

from mtf.core.catalog import ALL_PLATFORMS, UI_PLATFORMS, ActionKind, ActionSpec, ParamKind, ParamSpec
from mtf.core.errors import InfrastructureError
from mtf.core.ports import AppLaunchOptions
from mtf.screenplay.abilities import api_of, driver_of
from mtf.screenplay.actor import Actor
from mtf.screenplay.registry import Fixture, register

RESET_PATH = "/api/v1/test/reset"


def _fixture(
    name: str,
    description: str,
    *,
    api: bool,
    needs_actor: bool = False,
    test_support: bool = False,
    **params: ParamSpec,
) -> ActionSpec:
    return ActionSpec(
        name=name,
        kind=ActionKind.FIXTURE,
        description=description,
        params=params,
        platforms=ALL_PLATFORMS if api else UI_PLATFORMS,
        needs_actor=needs_actor,
        uses_test_support=test_support,
    )


@register
class ResetBackendData(Fixture):
    spec = _fixture(
        "reset_backend_data",
        "Restore the backend's seed data (POST /api/v1/test/reset). Needs an environment that allows test support.",
        api=True,
        test_support=True,
    )

    def perform_as(self, actor: Actor) -> None:
        client = api_of(actor)
        response = client.send(client.contract.operation("POST", RESET_PATH), purpose="setup")
        if response.status != 204:
            raise InfrastructureError(f"backend reset failed: HTTP {response.status}")
        client.forget_tokens()


@register
class LaunchApp(Fixture):
    spec = _fixture(
        "launch_app",
        "Launch the installed app (by default from cleared app data, i.e. a fresh-install state).",
        api=False,
        clean_state=ParamSpec(ParamKind.BOOL, required=False, default=True),
    )

    def perform_as(self, actor: Actor) -> None:
        driver_of(actor).launch_app(AppLaunchOptions(clean_state=bool(self.param("clean_state"))))


@register
class TerminateApp(Fixture):
    spec = _fixture("terminate_app", "Stop the app process.", api=False)

    def perform_as(self, actor: Actor) -> None:
        driver_of(actor).terminate_app()


@register
class SeedCart(Fixture):
    spec = _fixture(
        "seed_cart",
        "Put a product into the actor's cart through the API (product endpoint, not test support).",
        api=True,
        needs_actor=True,
        product=ParamSpec(ParamKind.PRODUCT),
        quantity=ParamSpec(ParamKind.INT, required=False, default=1, minimum=1, maximum=10),
    )

    def perform_as(self, actor: Actor) -> None:
        client = api_of(actor)
        response = client.send(
            client.contract.operation("POST", "/api/v1/cart/items"),
            body={"productId": self.param("product"), "quantity": int(self.param("quantity"))},
            account_ref=actor.account_ref,
            purpose="setup",
        )
        if response.status != 201:
            raise InfrastructureError(f"seeding the cart failed: HTTP {response.status}")


@register
class ClearCart(Fixture):
    spec = _fixture("clear_cart", "Empty the actor's cart through the API.", api=True, needs_actor=True)

    def perform_as(self, actor: Actor) -> None:
        client = api_of(actor)
        response = client.send(
            client.contract.operation("DELETE", "/api/v1/cart"),
            account_ref=actor.account_ref,
            purpose="setup",
        )
        if response.status != 200:
            raise InfrastructureError(f"clearing the cart failed: HTTP {response.status}")
