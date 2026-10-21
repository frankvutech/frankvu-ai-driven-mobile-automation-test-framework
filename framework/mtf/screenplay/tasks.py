"""Business-level tasks shared by Android and iOS. Screen details live in the robots."""

from __future__ import annotations

from mtf.contracts.importer import SCENARIOS
from mtf.core.catalog import ActionKind, ActionSpec, ParamKind, ParamSpec
from mtf.screenplay.abilities import driver_of, robots_of
from mtf.screenplay.actor import Actor
from mtf.screenplay.registry import Task, register
from mtf.screenplay.robots.screens import TABS

LOGIN_TIMEOUT_MS = 15_000  # the app shows a fixed loading state, then calls the backend


def _task(
    name: str, description: str, *, needs_actor: bool = False, test_ids: tuple[str, ...] = (), **params: ParamSpec
) -> ActionSpec:
    return ActionSpec(
        name=name,
        kind=ActionKind.TASK,
        description=description,
        params=params,
        needs_actor=needs_actor,
        test_ids=test_ids,
    )


@register
class Login(Task):
    spec = _task("login", "Sign in with the actor's account and wait for the dashboard.", needs_actor=True)

    def perform_as(self, actor: Actor) -> None:
        robots = robots_of(actor)
        robots.login.submit(actor.email, actor.password())
        robots.home.wait_until_shown(LOGIN_TIMEOUT_MS)
        robots.login.after_successful_login()


@register
class SubmitLogin(Task):
    spec = _task(
        "submit_login",
        "Fill in the login form with the given values and submit it, without expecting success "
        "(for validation and error scenarios).",
        email=ParamSpec(ParamKind.TEXT, "Email text (may be empty or malformed)."),
        password=ParamSpec(ParamKind.PASSWORD, "A ${secret:...} or ${inputs....} reference."),
    )

    def perform_as(self, actor: Actor) -> None:
        robots_of(actor).login.submit(str(self.param("email")), str(self.param("password")))


@register
class Logout(Task):
    spec = _task("logout", "Sign out from the Profile tab and wait for the login screen.")

    def perform_as(self, actor: Actor) -> None:
        robots = robots_of(actor)
        robots.navigation.open_tab("profile")
        robots.profile.wait_until_shown()
        robots.profile.logout()
        robots.login.wait_until_shown()


@register
class OpenTab(Task):
    spec = _task("open_tab", "Open a bottom-navigation tab.", tab=ParamSpec(ParamKind.ENUM, values=TABS))

    def perform_as(self, actor: Actor) -> None:
        robots_of(actor).navigation.open_tab(str(self.param("tab")))


@register
class GoBack(Task):
    spec = _task("go_back", "Navigate back one screen.")

    def perform_as(self, actor: Actor) -> None:
        robots_of(actor).navigation.back()


@register
class OpenScenario(Task):
    spec = _task(
        "open_scenario",
        "Open one of the dashboard's scenario screens (from the Home tab).",
        scenario=ParamSpec(ParamKind.ENUM, values=SCENARIOS),
    )

    def perform_as(self, actor: Actor) -> None:
        robots = robots_of(actor)
        robots.navigation.open_tab("home")
        robots.home.wait_until_shown()
        robots.home.open_scenario(str(self.param("scenario")))


@register
class SearchScenarios(Task):
    spec = _task(
        "search_scenarios",
        "Type into the dashboard search field (filters the scenario list; the UI has no product search).",
        query=ParamSpec(ParamKind.TEXT),
    )

    def perform_as(self, actor: Actor) -> None:
        robots = robots_of(actor)
        robots.home.wait_until_shown()
        robots.home.search(str(self.param("query")))


@register
class OpenProduct(Task):
    spec = _task(
        "open_product",
        "Open a product's detail screen from the Catalog tab.",
        product=ParamSpec(ParamKind.PRODUCT),
    )

    def perform_as(self, actor: Actor) -> None:
        robots = robots_of(actor)
        robots.navigation.open_tab("catalog")
        robots.catalog.wait_until_loaded()
        robots.catalog.open_product(str(self.param("product")))
        robots.product.wait_until_loaded()


@register
class AddProductToCart(Task):
    spec = _task(
        "add_product_to_cart",
        "Add a product to the cart from the Catalog tab (tapping Add `quantity` times).",
        product=ParamSpec(ParamKind.PRODUCT),
        quantity=ParamSpec(ParamKind.INT, required=False, default=1, minimum=1, maximum=5),
    )

    def perform_as(self, actor: Actor) -> None:
        robots = robots_of(actor)
        robots.navigation.open_tab("catalog")
        robots.catalog.wait_until_loaded()
        for _ in range(int(self.param("quantity"))):
            robots.catalog.add_to_cart(str(self.param("product")))


@register
class AddProductToCartFromDetails(Task):
    spec = _task(
        "add_product_to_cart_from_details",
        "Open the product's detail screen and add it to the cart there.",
        product=ParamSpec(ParamKind.PRODUCT),
    )

    def perform_as(self, actor: Actor) -> None:
        actor.attempts_to(OpenProduct(product=self.param("product")))
        robots_of(actor).product.add_to_cart()


@register
class OpenCart(Task):
    spec = _task("open_cart", "Open the Cart tab and wait until it has loaded (items or empty state).")

    def perform_as(self, actor: Actor) -> None:
        robots = robots_of(actor)
        robots.navigation.open_tab("cart")
        robots.cart.wait_until_loaded()


@register
class RemoveProductFromCart(Task):
    spec = _task(
        "remove_product_from_cart",
        "Remove a product's line from the cart.",
        product=ParamSpec(ParamKind.PRODUCT),
    )

    def perform_as(self, actor: Actor) -> None:
        actor.attempts_to(OpenCart())
        robots_of(actor).cart.remove_item(str(self.param("product")))


@register
class Checkout(Task):
    spec = _task("checkout", "Check out the cart and wait for the order confirmation (or an error).")

    def perform_as(self, actor: Actor) -> None:
        actor.attempts_to(OpenCart())
        robots_of(actor).cart.checkout()


@register
class OpenDeepLink(Task):
    spec = _task(
        "open_deep_link",
        "Open a registered deep link (mtp://playground/...) in the running app.",
        link=ParamSpec(ParamKind.DEEP_LINK),
    )

    def perform_as(self, actor: Actor) -> None:
        url = actor.context.test_data.data["deep_links"][self.param("link")]
        driver_of(actor).open_deep_link(str(url))


DIALOG_KINDS = ("confirmation", "information", "warning", "error", "action_sheet", "bottom_sheet")


@register
class OpenDialog(Task):
    spec = _task(
        "open_dialog",
        "On the Dialogs scenario screen, open a dialog and wait until it is shown.",
        test_ids=("dialogs_screen", "dialog_ok", "dialog_cancel"),
        kind=ParamSpec(ParamKind.ENUM, values=DIALOG_KINDS),
    )

    def perform_as(self, actor: Actor) -> None:
        dialogs = robots_of(actor).dialogs
        dialogs.open(str(self.param("kind")))
        dialogs.wait_for_dialog(str(self.param("kind")))


@register
class RespondToDialog(Task):
    spec = _task(
        "respond_to_dialog",
        "Press a button of the open dialog.",
        test_ids=("dialog_confirm", "dialog_cancel", "dialog_ok"),
        choice=ParamSpec(ParamKind.ENUM, values=("confirm", "cancel", "ok")),
    )

    def perform_as(self, actor: Actor) -> None:
        robots_of(actor).dialogs.respond(str(self.param("choice")))


@register
class ChooseActionSheetOption(Task):
    spec = _task(
        "choose_action_sheet_option",
        "Choose an option of the open action sheet.",
        test_ids=("action_sheet_option_share",),
        option=ParamSpec(ParamKind.ENUM, values=("share", "duplicate", "archive")),
    )

    def perform_as(self, actor: Actor) -> None:
        robots_of(actor).dialogs.choose_action(str(self.param("option")))
