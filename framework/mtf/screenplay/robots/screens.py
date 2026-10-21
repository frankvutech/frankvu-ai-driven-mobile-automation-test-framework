"""Screen Robots whose behaviour is identical on Android and iOS (shared test IDs)."""

from __future__ import annotations

from mtf.core.errors import AssertionFailed
from mtf.screenplay.robots.base import Robot, parse_int

TABS = ("home", "catalog", "cart", "profile")


class NavigationRobot(Robot):
    """App chrome: bottom navigation and back."""

    def open_tab(self, tab: str) -> None:
        self.tap(f"bottom_nav_{tab}")

    def back(self) -> None:
        self.driver.back()


class LoginScreenRobot(Robot):
    SCREEN = "login_screen"

    def wait_until_shown(self) -> None:
        self.wait_visible(self.SCREEN)

    def enter_email(self, email: str) -> None:
        self.type("login_email", email)

    def enter_password(self, password: str) -> None:
        self.type("login_password", password, sensitive=True)

    def tap_login(self) -> None:
        self.tap("login_button")

    def submit(self, email: str, password: str) -> None:
        self.wait_until_shown()
        self.enter_email(email)
        self.enter_password(password)
        self.tap_login()

    def after_successful_login(self) -> None:
        """Hook for platform prompts that follow a successful sign-in."""

    def read_validation_message(self) -> str:
        return self.text_of("login_error")

    def redirect_message_visible(self) -> bool:
        return self.is_visible("login_redirect_message")


class HomeScreenRobot(Robot):
    SCREEN = "home_screen"

    def wait_until_shown(self, timeout_ms: int | None = None) -> None:
        self.wait_visible(self.SCREEN, timeout_ms)

    def search(self, query: str) -> None:
        self.type("home_search", query)

    def scenario_visible(self, scenario: str) -> bool:
        return self.is_visible(f"scenario_{scenario}")

    def empty_search_visible(self) -> bool:
        return self.is_visible("home_empty_search")

    def open_scenario(self, scenario: str) -> None:
        target = f"scenario_{scenario}"
        self.scroll_into_view(target, container="home_scenario_list", max_swipes=20)
        self.tap(target)


class CatalogScreenRobot(Robot):
    SCREEN = "catalog_screen"
    # The scrollable list's own ID, where the platform exposes one (see the Android robot).
    LIST_CONTAINER: str | None = None

    def wait_until_loaded(self) -> None:
        self.wait_visible(self.SCREEN)
        self.wait_gone("catalog_loading")
        if self.is_visible("catalog_error"):
            raise AssertionFailed(
                "the catalog failed to load", expected="products", actual=self.text_of("catalog_error")
            )

    def add_to_cart(self, product_id: str) -> None:
        target = f"{product_id}_add_to_cart"
        self.scroll_into_view(target, container=self.LIST_CONTAINER, max_swipes=25)
        self.tap(target)

    def open_product(self, product_id: str) -> None:
        self.scroll_into_view(product_id, container=self.LIST_CONTAINER, max_swipes=25)
        self.tap(product_id)

    def action_error(self) -> str | None:
        info = self.driver.read("catalog_action_error")
        return (info.text or info.value) if info.visible else None


class ProductDetailRobot(Robot):
    SCREEN = "product_detail_screen"

    def wait_until_loaded(self) -> None:
        self.wait_visible(self.SCREEN)
        self.wait_for_any(["product_name", "product_not_found", "product_error"])

    def name(self) -> str:
        return self.text_of("product_name")

    def price(self) -> str:
        return self.text_of("product_price")

    def category(self) -> str:
        return self.text_of("product_category")

    def add_to_cart(self) -> None:
        self.tap("product_add_to_cart")

    def not_found_visible(self) -> bool:
        return self.is_visible("product_not_found")


class CartScreenRobot(Robot):
    SCREEN = "cart_screen"

    def wait_until_loaded(self) -> str:
        """Waits until the cart shows either items (total) or the empty state; returns which."""
        self.wait_visible(self.SCREEN)
        return self.wait_for_any(["cart_total", "cart_empty", "order_confirmation"])

    def has_item(self, product_id: str) -> bool:
        return self.is_visible(f"cart_item_{product_id}")

    def quantity(self, product_id: str) -> int:
        return parse_int(self.text_of(f"cart_item_quantity_{product_id}"))

    def total(self) -> str:
        return self.text_of("cart_total")

    def is_empty(self) -> bool:
        return self.is_visible("cart_empty")

    def remove_item(self, product_id: str) -> None:
        self.tap(f"cart_remove_{product_id}")
        self.wait_gone(f"cart_item_{product_id}")

    def checkout(self) -> None:
        self.scroll_into_view("cart_checkout_button", max_swipes=10)
        self.tap("cart_checkout_button")
        self.wait_for_any(["order_confirmation", "cart_error"])

    def order_number(self) -> str:
        return self.text_of("order_number")

    def error(self) -> str | None:
        info = self.driver.read("cart_error")
        return (info.text or info.value) if info.visible else None


class ProfileScreenRobot(Robot):
    SCREEN = "profile_screen"

    def wait_until_shown(self) -> None:
        self.wait_visible(self.SCREEN)

    def email(self) -> str:
        return self.text_of("profile_email")

    def role(self) -> str:
        return self.text_of("profile_role")

    def display_name(self) -> str:
        return self.text_of("profile_name")

    def admin_entry_visible(self) -> bool:
        return self.is_visible("profile_admin")

    def logout(self) -> None:
        self.scroll_into_view("profile_logout", max_swipes=5)
        self.tap("profile_logout")


class DialogsScreenRobot(Robot):
    SCREEN = "dialogs_screen"
    BUTTONS = {"confirm": "dialog_confirm", "cancel": "dialog_cancel", "ok": "dialog_ok"}

    def open(self, kind: str) -> None:
        self.wait_visible(self.SCREEN)
        self.tap(f"dialogs_open_{kind}")

    def wait_for_dialog(self, kind: str) -> None:
        """Waits until the dialog's buttons are on screen."""
        self.wait_visible("dialog_ok" if kind in ("information", "warning", "error") else "dialog_cancel")

    def respond(self, choice: str) -> None:
        self.tap(self.BUTTONS[choice])

    def choose_action(self, option: str) -> None:
        self.tap(f"action_sheet_option_{option}")

    def result(self) -> str:
        return self.text_of("dialogs_result")


class AccessRobot(Robot):
    """Authorization outcomes: the access-denied screen and the admin dashboard."""

    def denied(self) -> bool:
        return self.is_visible("access_denied_screen")

    def admin_dashboard_visible(self) -> bool:
        return self.is_visible("admin_dashboard")


class GesturesScreenRobot(Robot):
    SCREEN = "gestures_screen"

    def result(self) -> str:
        return self.text_of("gesture_result")
