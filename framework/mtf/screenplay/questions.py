"""Questions: what an actor can observe, on screen or through the API."""

from __future__ import annotations

from typing import Any

from mtf.api_runner import jsonpath
from mtf.contracts.importer import SCENARIOS
from mtf.core.catalog import ALL_PLATFORMS, UI_PLATFORMS, ActionKind, ActionSpec, ParamKind, ParamSpec
from mtf.core.errors import InfrastructureError
from mtf.screenplay.abilities import api_of, driver_of, robots_of
from mtf.screenplay.actor import Actor
from mtf.screenplay.registry import Question, register


def _question(
    name: str,
    description: str,
    returns: str,
    *,
    api: bool = False,
    needs_actor: bool = False,
    **params: ParamSpec,
) -> ActionSpec:
    return ActionSpec(
        name=name,
        kind=ActionKind.QUESTION,
        description=description,
        params=params,
        returns=returns,
        needs_actor=needs_actor,
        platforms=ALL_PLATFORMS if api else UI_PLATFORMS,
    )


# --- generic UI questions -----------------------------------------------------------------------
@register
class CurrentScreen(Question):
    spec = _question("current_screen", "The ID of the screen root that is visible (e.g. `home_screen`).", "text")

    def answered_by(self, actor: Actor) -> Any:
        return driver_of(actor).first_visible(actor.context.screen_roots) or ""


@register
class IsVisible(Question):
    spec = _question("is_visible", "Whether the element is on screen.", "bool", target=ParamSpec(ParamKind.TEST_ID))

    def answered_by(self, actor: Actor) -> Any:
        return driver_of(actor).read(str(self.param("target"))).visible


@register
class TextOf(Question):
    spec = _question(
        "text_of",
        "The element's text (label, or value for fields).",
        "text",
        target=ParamSpec(ParamKind.TEST_ID),
    )

    def answered_by(self, actor: Actor) -> Any:
        info = driver_of(actor).read(str(self.param("target")))
        return (info.text or info.value) if info.exists else None


# --- domain UI questions ------------------------------------------------------------------------
@register
class LoginErrorMessage(Question):
    spec = _question("login_error_message", "The login screen's validation/error message.", "text")

    def answered_by(self, actor: Actor) -> Any:
        return robots_of(actor).login.read_validation_message()


@register
class ScenarioIsListed(Question):
    spec = _question(
        "scenario_is_listed",
        "Whether a scenario row is shown on the dashboard.",
        "bool",
        scenario=ParamSpec(ParamKind.ENUM, values=SCENARIOS),
    )

    def answered_by(self, actor: Actor) -> Any:
        return robots_of(actor).home.scenario_visible(str(self.param("scenario")))


@register
class EmptySearchResultShown(Question):
    spec = _question("empty_search_result_shown", "Whether the dashboard shows its no-results state.", "bool")

    def answered_by(self, actor: Actor) -> Any:
        return robots_of(actor).home.empty_search_visible()


@register
class ProductDetailName(Question):
    spec = _question("product_detail_name", "The name on the product detail screen.", "text")

    def answered_by(self, actor: Actor) -> Any:
        return robots_of(actor).product.name()


@register
class ProductDetailPrice(Question):
    spec = _question("product_detail_price", "The price on the product detail screen (e.g. `7.49`).", "text")

    def answered_by(self, actor: Actor) -> Any:
        return robots_of(actor).product.price()


@register
class ProductDetailCategory(Question):
    spec = _question("product_detail_category", "The category on the product detail screen.", "text")

    def answered_by(self, actor: Actor) -> Any:
        return robots_of(actor).product.category()


@register
class CatalogActionError(Question):
    spec = _question("catalog_action_error", "The catalog's add-to-cart error message, or empty text.", "text")

    def answered_by(self, actor: Actor) -> Any:
        return robots_of(actor).catalog.action_error() or ""


@register
class ProductIsInCart(Question):
    spec = _question(
        "product_is_in_cart",
        "Whether the cart screen lists the product.",
        "bool",
        product=ParamSpec(ParamKind.PRODUCT),
    )

    def answered_by(self, actor: Actor) -> Any:
        cart = robots_of(actor).cart
        cart.wait_until_loaded()
        return cart.has_item(str(self.param("product")))


@register
class CartItemQuantity(Question):
    spec = _question(
        "cart_item_quantity",
        "The quantity of a product's cart line.",
        "int",
        product=ParamSpec(ParamKind.PRODUCT),
    )

    def answered_by(self, actor: Actor) -> Any:
        return robots_of(actor).cart.quantity(str(self.param("product")))


@register
class CartTotal(Question):
    spec = _question("cart_total", "The cart total as displayed (e.g. `14.98`).", "text")

    def answered_by(self, actor: Actor) -> Any:
        return robots_of(actor).cart.total()


@register
class CartIsEmpty(Question):
    spec = _question("cart_is_empty", "Whether the cart shows its empty state.", "bool")

    def answered_by(self, actor: Actor) -> Any:
        cart = robots_of(actor).cart
        cart.wait_until_loaded()
        return cart.is_empty()


@register
class OrderNumber(Question):
    spec = _question("order_number", "The order number shown after checkout.", "text")

    def answered_by(self, actor: Actor) -> Any:
        return robots_of(actor).cart.order_number()


@register
class ProfileRole(Question):
    spec = _question("profile_role", "The role shown on the Profile tab (CUSTOMER or ADMIN).", "text")

    def answered_by(self, actor: Actor) -> Any:
        return robots_of(actor).profile.role()


@register
class ProfileEmail(Question):
    spec = _question("profile_email", "The email shown on the Profile tab.", "text")

    def answered_by(self, actor: Actor) -> Any:
        return robots_of(actor).profile.email()


@register
class AccessDeniedShown(Question):
    spec = _question("access_denied_shown", "Whether the access-denied screen is shown.", "bool")

    def answered_by(self, actor: Actor) -> Any:
        return robots_of(actor).access.denied()


@register
class DialogResult(Question):
    spec = _question("dialog_result", "The Dialogs scenario's result code (e.g. `confirmed`).", "text")

    def answered_by(self, actor: Actor) -> Any:
        return robots_of(actor).dialogs.result()


@register
class GestureResult(Question):
    spec = _question("gesture_result", "The Gestures scenario's result label.", "text")

    def answered_by(self, actor: Actor) -> Any:
        return robots_of(actor).gestures.result()


# --- API questions (work in UI and API plans) ---------------------------------------------------
def _api_get(actor: Actor, path: str) -> Any:
    client = api_of(actor)
    response = client.send(client.contract.operation("GET", path), account_ref=actor.account_ref, purpose="setup")
    if response.status != 200:
        raise InfrastructureError(f"GET {path} as {actor.name} returned HTTP {response.status}")
    return response.body


@register
class ApiCartTotalCents(Question):
    spec = _question(
        "api_cart_total_cents",
        "The actor's cart total in cents, read from the backend.",
        "int",
        api=True,
        needs_actor=True,
    )

    def answered_by(self, actor: Actor) -> Any:
        return jsonpath.get(_api_get(actor, "/api/v1/cart"), "totalCents")


@register
class ApiCartQuantity(Question):
    spec = _question(
        "api_cart_quantity",
        "The quantity of a product in the actor's backend cart (0 when absent).",
        "int",
        api=True,
        needs_actor=True,
        product=ParamSpec(ParamKind.PRODUCT),
    )

    def answered_by(self, actor: Actor) -> Any:
        cart = _api_get(actor, "/api/v1/cart")
        for item in cart.get("items", []):
            if item.get("product", {}).get("id") == self.param("product"):
                return int(item.get("quantity", 0))
        return 0


@register
class ApiOrderCount(Question):
    spec = _question(
        "api_order_count",
        "How many orders the actor has, read from the backend.",
        "int",
        api=True,
        needs_actor=True,
    )

    def answered_by(self, actor: Actor) -> Any:
        return len(_api_get(actor, "/api/v1/orders"))


@register
class ApiLatestOrderNumber(Question):
    spec = _question(
        "api_latest_order_number",
        "The actor's most recent order number, read from the backend.",
        "text",
        api=True,
        needs_actor=True,
    )

    def answered_by(self, actor: Actor) -> Any:
        orders = _api_get(actor, "/api/v1/orders")
        return orders[-1]["orderNumber"] if orders else None
