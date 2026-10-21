from __future__ import annotations

import pytest

from mtf.contracts.bundle import Contracts
from mtf.contracts.test_data import SecretProvider
from mtf.core.catalog import ActionKind, Catalog
from mtf.core.errors import ConfigurationError, ElementNotFound, SelectorContractViolation
from mtf.core.ports import AppLaunchOptions, UiDriver
from mtf.core.redaction import Redactor
from mtf.core.types import Platform
from mtf.dsl.models import InteractionName
from mtf.orchestrator.contract_guard import ContractCheckedDriver
from mtf.screenplay.abilities import OperateMobileApp
from mtf.screenplay.actor import Actor, ScreenplayContext
from mtf.screenplay.registry import REGISTRY
from mtf.screenplay.robots import robots_for
from mtf.screenplay.tasks import AddProductToCart, Login, OpenCart, RemoveProductFromCart

from .fakes import PASSWORDS, FakeApp, FakeUiDriver, World


def actor_on(
    platform: Platform, contracts: Contracts, world: World | None = None
) -> tuple[Actor, FakeUiDriver, Redactor]:
    world = world or World()
    driver = FakeUiDriver(FakeApp(world, platform))
    redactor = Redactor()
    guarded = ContractCheckedDriver(driver, contracts.test_ids, redactor)
    context = ScreenplayContext(
        platform=platform,
        test_data=contracts.test_data,
        secrets=SecretProvider(contracts.test_data, dict(PASSWORDS)),
        redactor=redactor,
        default_timeout_ms=1000,
        screen_roots=contracts.test_ids.screen_roots(platform),
    )
    actor = Actor("customer", context, "customer").can(OperateMobileApp(guarded, robots_for(guarded, 1000)))
    driver.launch_app(AppLaunchOptions(clean_state=True))
    return actor, driver, redactor


@pytest.mark.parametrize("platform", [Platform.ANDROID, Platform.IOS])
def test_shared_tasks_compose_on_both_platforms(contracts: Contracts, platform: Platform) -> None:
    actor, driver, redactor = actor_on(platform, contracts)
    actor.attempts_to(Login(), AddProductToCart(product="product_002", quantity=2), OpenCart())
    cart = actor.ability(OperateMobileApp).robots.cart
    assert cart.has_item("product_002")
    assert cart.quantity("product_002") == 2
    assert cart.total() == "19.98"
    actor.attempts_to(RemoveProductFromCart(product="product_002"))
    assert cart.is_empty()
    # the password was typed as a sensitive value and registered for redaction
    assert ("type_text", "login_password", "<sensitive>") in driver.calls
    assert redactor.contains_secret(PASSWORDS["MTF_PASSWORD_CUSTOMER"])


def test_platform_robots_differ_only_where_the_app_differs(contracts: Contracts) -> None:
    android, a_driver, _ = actor_on(Platform.ANDROID, contracts)
    ios, i_driver, _ = actor_on(Platform.IOS, contracts)
    for actor in (android, ios):
        actor.attempts_to(Login(), AddProductToCart(product="product_001"))
    # iOS declines the AutoFill "Save Password?" prompt after login; Android has no such prompt
    assert ("dismiss_system_dialogs", None) in i_driver.calls
    assert ("dismiss_system_dialogs", None) not in a_driver.calls
    # only Android scrolls the catalog by its list ID (shadowed on iOS)
    assert ("scroll_to", "product_001_add_to_cart", "catalog_list") in a_driver.calls
    assert ("scroll_to", "product_001_add_to_cart", None) in i_driver.calls


def test_failed_login_surfaces_the_validation_message(contracts: Contracts) -> None:
    actor, _, _ = actor_on(Platform.ANDROID, contracts)
    login = actor.ability(OperateMobileApp).robots.login
    login.submit("test@example.com", "wrong-password")
    assert login.read_validation_message() == "Incorrect password."
    with pytest.raises(ElementNotFound):
        actor.ability(OperateMobileApp).robots.home.wait_until_shown(100)


def test_contract_guard_rejects_unregistered_and_unavailable_ids(contracts: Contracts) -> None:
    driver = FakeUiDriver(FakeApp(World(), Platform.IOS))
    guarded = ContractCheckedDriver(driver, contracts.test_ids, Redactor())
    with pytest.raises(SelectorContractViolation):
        guarded.tap("not_a_registered_id", 100)
    with pytest.raises(SelectorContractViolation):
        guarded.tap("top_bar_back", 100)  # Android-only
    assert driver.calls == []  # nothing reached the device


def test_contract_guard_suppresses_screenshots_after_revealing_a_secret(contracts: Contracts, tmp_path) -> None:
    world = World()
    driver = FakeUiDriver(FakeApp(world, Platform.ANDROID))
    driver.launch_app(AppLaunchOptions(clean_state=True))
    guarded = ContractCheckedDriver(driver, contracts.test_ids, Redactor())
    assert guarded.screenshot(tmp_path / "a.png")
    guarded.tap("login_password_toggle", 100)
    assert not guarded.screenshot(tmp_path / "b.png")
    guarded.launch_app(AppLaunchOptions(clean_state=True))
    assert guarded.screenshot(tmp_path / "c.png")


def test_actor_without_ability_or_account(contracts: Contracts) -> None:
    context = ScreenplayContext(Platform.API, contracts.test_data, SecretProvider(contracts.test_data, {}), Redactor())
    nobody = Actor("system", context)
    with pytest.raises(ConfigurationError):
        nobody.ability(OperateMobileApp)
    with pytest.raises(ConfigurationError):
        nobody.password()


def test_catalog_matches_implementations(catalog: Catalog) -> None:
    for kind in (ActionKind.TASK, ActionKind.QUESTION, ActionKind.FIXTURE):
        for spec in catalog.of_kind(kind):
            instance = REGISTRY.create(kind, spec.name, {})
            assert type(instance).spec is spec
    assert set(catalog.names(ActionKind.INTERACTION)) == {i.value for i in InteractionName}


def test_fake_driver_satisfies_the_port() -> None:
    assert isinstance(FakeUiDriver(FakeApp(World(), Platform.ANDROID)), UiDriver)
