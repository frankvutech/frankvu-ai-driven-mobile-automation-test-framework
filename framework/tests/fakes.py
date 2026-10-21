"""In-memory fakes for unit tests: a tiny model of the Mobile Test Playground app and backend.

`FakeBackend` implements the `HttpTransport` port for a subset of the real API; `FakeApp` +
`FakeUiDriver` implement the `UiDriver` port. They share state, so a UI action (add to cart) is
visible through the API, as with the real app in backend mode. No device or network is used.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from mtf.core.errors import ElementNotFound, StepTimeout
from mtf.core.ports import ApiResponse, AppLaunchOptions, Direction, ElementInfo, WaitState
from mtf.core.types import Platform
from mtf.reporting.models import ArtifactInfo, EvidenceRef, RunnerInfo

PASSWORDS = {
    "MTF_PASSWORD_CUSTOMER": "Cust0mer-Secret!",
    "MTF_PASSWORD_QA": "Qa-Secret-123!",
    "MTF_PASSWORD_ADMIN": "Adm1n-Secret!",
    "MTF_PASSWORD_LOCKED": "L0cked-Secret!",
}
USERS = {
    "test@example.com": ("user_customer", "CUSTOMER", PASSWORDS["MTF_PASSWORD_CUSTOMER"]),
    "qa@example.com": ("user_qa", "CUSTOMER", PASSWORDS["MTF_PASSWORD_QA"]),
    "admin@example.com": ("user_admin", "ADMIN", PASSWORDS["MTF_PASSWORD_ADMIN"]),
}


def price(product_id: str) -> int:
    return 499 + 250 * int(product_id.split("_")[1])


@dataclass
class World:
    """State shared by the fake backend and the fake app."""

    carts: dict[str, dict[str, int]] = field(default_factory=dict)
    tokens: dict[str, str] = field(default_factory=dict)  # token -> user id
    orders: list[dict[str, Any]] = field(default_factory=list)
    resets: int = 0

    def reset(self) -> None:
        self.carts.clear()
        self.tokens.clear()
        self.orders.clear()
        self.resets += 1

    def cart_body(self, user: str) -> dict[str, Any]:
        cart = self.carts.get(user, {})
        items = [
            {"product": _product(pid), "quantity": qty, "lineTotalCents": qty * price(pid)}
            for pid, qty in sorted(cart.items())
        ]
        return {
            "items": items,
            "totalQuantity": sum(cart.values()),
            "totalCents": sum(i["lineTotalCents"] for i in items),
        }


def _product(pid: str) -> dict[str, Any]:
    number = pid.split("_")[1]
    categories = ["Books", "Electronics", "Garden", "Toys"]
    return {
        "id": pid,
        "name": f"Product {number}",
        "description": f"Deterministic sample product number {number}.",
        "category": categories[(int(number) - 1) % 4],
        "priceCents": price(pid),
        "stock": 10,
    }


class FakeBackend:
    """Implements a subset of the backend API for the HttpTransport port."""

    def __init__(self, world: World | None = None) -> None:
        self.world = world or World()
        self.requests: list[dict[str, Any]] = []
        self.fail_reset = False

    def _respond(self, status: int, body: Any, request: dict[str, Any]) -> ApiResponse:
        text = "" if body is None else json.dumps(body)
        return ApiResponse(status, {"content-type": "application/json"}, body, text, 1.0, request)

    def _error(self, status: int, code: str, request: dict[str, Any]) -> ApiResponse:
        return self._respond(status, {"error": {"code": code, "message": code.lower(), "details": []}}, request)

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, Any] | None,
        json_body: Any,
        content: str | None,
        timeout_s: float,
    ) -> ApiResponse:
        path = urlparse(url).path
        request = {
            "method": method,
            "url": url,
            "headers": dict(headers),
            "body": json_body if content is None else content,
        }
        self.requests.append(request)
        auth = headers.get("Authorization", "")
        user = self.world.tokens.get(auth.removeprefix("Bearer ")) if auth else None
        if method == "GET" and path == "/health":
            return self._respond(200, {"status": "ok", "checks": {}}, request)
        if method == "POST" and path == "/api/v1/test/reset":
            if self.fail_reset:
                return self._error(500, "INTERNAL_ERROR", request)
            self.world.reset()
            return self._respond(204, None, request)
        if method == "POST" and path == "/api/v1/auth/login":
            body = json_body or {}
            record = USERS.get(body.get("email", ""))
            if not body.get("email"):
                return self._error(400, "VALIDATION_ERROR", request)
            if record is None:
                return self._error(401, "UNKNOWN_USER", request)
            if body.get("password") != record[2]:
                return self._error(401, "INVALID_PASSWORD", request)
            token = f"eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ{record[0]}x.signature{len(self.world.tokens)}"
            self.world.tokens[token] = record[0]
            user_body = {"id": record[0], "email": body["email"], "displayName": "Test", "role": record[1]}
            return self._respond(
                200, {"token": token, "tokenType": "Bearer", "expiresAt": 1, "user": user_body}, request
            )
        if path.startswith("/api/v1/cart") or path.startswith("/api/v1/orders"):
            if user is None:
                return self._error(401, "UNAUTHORIZED" if not auth else "INVALID_TOKEN", request)
            if method == "GET" and path == "/api/v1/cart":
                return self._respond(200, self.world.cart_body(user), request)
            if method == "DELETE" and path == "/api/v1/cart":
                self.world.carts[user] = {}
                return self._respond(200, self.world.cart_body(user), request)
            if method == "POST" and path == "/api/v1/cart/items":
                pid = (json_body or {}).get("productId", "")
                cart = self.world.carts.setdefault(user, {})
                cart[pid] = cart.get(pid, 0) + int((json_body or {}).get("quantity", 1))
                return self._respond(201, self.world.cart_body(user), request)
            if method == "GET" and path == "/api/v1/orders":
                return self._respond(200, [o for o in self.world.orders if o["userId"] == user], request)
        if method == "GET" and path.startswith("/api/v1/products/"):
            pid = path.rsplit("/", 1)[1]
            if pid in {f"product_{i:03d}" for i in range(1, 21)}:
                return self._respond(200, _product(pid), request)
            return self._error(404, "PRODUCT_NOT_FOUND", request)
        if method == "GET" and path == "/api/v1/test/server-error":
            return self._error(500, "INTERNAL_ERROR", request)
        return self._error(404, "ROUTE_NOT_FOUND", request)

    def close(self) -> None:
        return None


SCREEN_ROOTS = {
    "login": "login_screen",
    "home": "home_screen",
    "catalog": "catalog_screen",
    "cart": "cart_screen",
    "profile": "profile_screen",
}


class FakeApp:
    """A minimal model of the app's login, home, catalog, cart and profile screens."""

    def __init__(self, world: World, platform: Platform) -> None:
        self.world = world
        self.platform = platform
        self.running = False
        self.screen = "login"
        self.user: str | None = None
        self.fields: dict[str, str] = {}
        self.login_error = ""
        self.order_number = ""
        self.calls: list[tuple[str, Any]] = []

    # visible elements: id -> (text, enabled)
    def elements(self) -> dict[str, str]:
        if not self.running:
            return {}
        out: dict[str, str] = {SCREEN_ROOTS[self.screen]: ""}
        if self.screen == "login":
            out.update(
                {
                    "login_email": self.fields.get("login_email", ""),
                    "login_password": "•" * len(self.fields.get("login_password", "")),
                    "login_button": "Sign in",
                    "login_password_toggle": "",
                }
            )
            if self.login_error:
                out["login_error"] = self.login_error
            return out
        out.update({f"bottom_nav_{t}": t for t in ("home", "catalog", "cart", "profile")})
        if self.platform == Platform.ANDROID:
            out["top_bar_title"] = self.screen
        if self.screen == "home":
            out["home_search"] = ""
        elif self.screen == "catalog":
            if self.platform == Platform.ANDROID:
                out["catalog_list"] = ""
            for i in range(1, 21):
                pid = f"product_{i:03d}"
                out[pid] = f"Product {i:03d}"
                out[f"{pid}_add_to_cart"] = "Add"
        elif self.screen == "cart":
            cart = self.world.carts.get(self.user or "", {})
            if self.order_number:
                out["order_confirmation"] = ""
                out["order_number"] = self.order_number
            if not cart:
                out["cart_empty"] = "Your cart is empty"
            else:
                for pid, qty in cart.items():
                    out[f"cart_item_{pid}"] = f"Product {pid[-3:]}"
                    out[f"cart_item_quantity_{pid}"] = f"× {qty}"
                    out[f"cart_remove_{pid}"] = ""
                total = sum(q * price(p) for p, q in cart.items())
                out["cart_total"] = f"{total // 100}.{total % 100:02d}"
                out["cart_checkout_button"] = "Check out"
        elif self.screen == "profile":
            out.update(
                {
                    "profile_email": next((e for e, r in USERS.items() if r[0] == self.user), ""),
                    "profile_role": "CUSTOMER",
                    "profile_logout": "Log out",
                }
            )
        return out

    def tap(self, test_id: str) -> None:
        self.calls.append(("tap", test_id))
        if test_id == "login_button":
            email = self.fields.get("login_email", "")
            password = self.fields.get("login_password", "")
            record = USERS.get(email)
            if not email:
                self.login_error = "Email is required."
            elif record is None:
                self.login_error = "No account exists for this email."
            elif password != record[2]:
                self.login_error = "Incorrect password."
            else:
                self.user, self.screen, self.login_error = record[0], "home", ""
        elif test_id.startswith("bottom_nav_"):
            self.screen = test_id.removeprefix("bottom_nav_")
        elif test_id.endswith("_add_to_cart") and self.user:
            pid = test_id.removesuffix("_add_to_cart")
            cart = self.world.carts.setdefault(self.user, {})
            cart[pid] = cart.get(pid, 0) + 1
        elif test_id.startswith("cart_remove_") and self.user:
            self.world.carts.get(self.user, {}).pop(test_id.removeprefix("cart_remove_"), None)
        elif test_id == "cart_checkout_button" and self.user:
            self.order_number = f"ORDER-{len(self.world.orders) + 1:04d}"
            self.world.orders.append({"userId": self.user, "orderNumber": self.order_number})
            self.world.carts[self.user] = {}
        elif test_id == "profile_logout":
            self.user, self.screen = None, "login"


class FakeUiDriver:
    """`UiDriver` over `FakeApp`. Waits are evaluated immediately: the fake has no asynchrony."""

    def __init__(self, app: FakeApp) -> None:
        self.app = app
        self.platform = app.platform
        self.calls: list[tuple[str, Any]] = []
        self.closed = False
        self.hang_on: str | None = None  # a tap on this ID blocks (watchdog tests)

    def _require(self, test_id: str) -> None:
        if test_id not in self.app.elements():
            raise ElementNotFound(f"'{test_id}' not on screen ({self.app.screen})")

    def launch_app(self, options: AppLaunchOptions) -> None:
        self.calls.append(("launch_app", options.clean_state))
        if options.clean_state:
            self.app.fields.clear()
            self.app.user = None
            self.app.order_number = ""
        self.app.running, self.app.screen = True, "login" if self.app.user is None else "home"

    def terminate_app(self) -> None:
        self.app.running = False

    def background_app(self, duration_ms: int) -> None:
        self.calls.append(("background_app", duration_ms))

    def open_deep_link(self, url: str) -> None:
        self.calls.append(("open_deep_link", url))

    def tap(self, test_id: str, timeout_ms: int) -> None:
        if test_id == self.hang_on:
            import time

            time.sleep(5)
        self._require(test_id)
        self.calls.append(("tap", test_id))
        self.app.tap(test_id)

    def double_tap(self, test_id: str, timeout_ms: int) -> None:
        self.tap(test_id, timeout_ms)

    def long_press(self, test_id: str, duration_ms: int, timeout_ms: int) -> None:
        self.tap(test_id, timeout_ms)

    def type_text(self, test_id: str, text: str, *, clear_first: bool, sensitive: bool, timeout_ms: int) -> None:
        self._require(test_id)
        self.calls.append(("type_text", test_id, "<sensitive>" if sensitive else text))
        self.app.fields[test_id] = text if clear_first else self.app.fields.get(test_id, "") + text

    def clear_text(self, test_id: str, timeout_ms: int) -> None:
        self._require(test_id)
        self.app.fields[test_id] = ""

    def scroll_to(
        self, test_id: str, *, direction: Direction, max_swipes: int, container: str | None, timeout_ms: int
    ) -> None:
        self.calls.append(("scroll_to", test_id, container))
        if container is not None:
            self._require(container)
        self._require(test_id)

    def swipe(self, test_id: str | None, direction: Direction, timeout_ms: int) -> None:
        self.calls.append(("swipe", test_id, direction.value))

    def drag_to(self, test_id: str, target_id: str, timeout_ms: int) -> None:
        self._require(test_id)
        self._require(target_id)

    def back(self) -> None:
        self.calls.append(("back", None))

    def dismiss_keyboard(self) -> None:
        self.calls.append(("dismiss_keyboard", None))

    def dismiss_system_dialogs(self) -> list[str]:
        self.calls.append(("dismiss_system_dialogs", None))
        return []

    def wait_for(self, test_id: str, state: WaitState, timeout_ms: int) -> ElementInfo:
        info = self.read(test_id)
        ok = {
            WaitState.VISIBLE: info.visible,
            WaitState.GONE: not info.visible,
            WaitState.ENABLED: info.exists and info.enabled,
            WaitState.DISABLED: info.exists and not info.enabled,
            WaitState.SELECTED: info.exists and info.selected,
            WaitState.NOT_SELECTED: info.exists and not info.selected,
        }[state]
        if not ok:
            if state == WaitState.VISIBLE and not info.exists:
                raise ElementNotFound(f"'{test_id}' did not appear within {timeout_ms} ms")
            raise StepTimeout(f"'{test_id}' did not become {state.value}")
        return info

    def read(self, test_id: str) -> ElementInfo:
        elements = self.app.elements()
        if test_id not in elements:
            return ElementInfo.missing(test_id)
        return ElementInfo(id=test_id, exists=True, visible=True, text=elements[test_id], enabled=True)

    def first_visible(self, test_ids: list[str]) -> str | None:
        elements = self.app.elements()
        return next((t for t in test_ids if t in elements), None)

    def screenshot(self, path: Path) -> bool:
        path.write_bytes(b"\x89PNG fake")
        return True

    def hierarchy(self) -> str:
        return json.dumps(self.app.elements(), ensure_ascii=False)

    def device_logs(self) -> str:
        return "fake log line\n"

    def device_info(self) -> dict[str, Any]:
        return {"model": "fake"}

    def close(self) -> None:
        self.closed = True


class FakeSession:
    """A PlatformSession over the fakes. Records whether preparation happened."""

    def __init__(self, platform: Platform, world: World, *, fail_prepare: Exception | None = None) -> None:
        self.platform = platform
        self.app = FakeApp(world, platform)
        self._driver = FakeUiDriver(self.app) if platform.is_mobile else None
        self.prepared = False
        self.closed = False
        self.fail_prepare = fail_prepare
        self.raw_typed: list[str] = list(PASSWORDS.values())  # what a raw runner log could contain

    def prepare(self, run_dir: Path) -> None:
        if self.fail_prepare is not None:
            raise self.fail_prepare
        self.prepared = True

    @property
    def driver(self) -> FakeUiDriver | None:
        return self._driver

    def artifact_info(self) -> ArtifactInfo | None:
        if not self.platform.is_mobile:
            return None
        return ArtifactInfo(
            platform=self.platform,
            path="/fake/app",
            kind="apk",
            sha256="0" * 64,
            identifier="com.mobiletestplayground.app",
            version_name="1.0.0",
            version_code="1",
            installed_verified=True,
        )

    def device_info(self) -> dict[str, Any]:
        return {"model": "fake"} if self.platform.is_mobile else {}

    def runner_info(self) -> RunnerInfo:
        return RunnerInfo(framework_version="test", runner="fake")

    def configuration(self) -> dict[str, Any]:
        return {"fake": True}

    def collect_failure_evidence(self, evidence_dir: Path, prefix: str) -> list[EvidenceRef]:
        # Like XCTest's activity log, the fake runner log records typed text in clear.
        typed = [c[2] for c in (self._driver.calls if self._driver else []) if c[0] == "type_text"]
        path = evidence_dir / f"{prefix}-runner.log"
        path.write_text("\n".join(f"Type '{t}' into field" for t in typed) + "\n" + "\n".join(self.raw_typed))
        return [EvidenceRef(kind="runner_log", path=str(path))]

    def app_crashed(self) -> str | None:
        return None

    def close(self) -> None:
        self.closed = True
