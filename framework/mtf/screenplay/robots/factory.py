"""Creates the right robot variants for a platform (Strategy)."""

from __future__ import annotations

from dataclasses import dataclass

from mtf.core.errors import ConfigurationError
from mtf.core.ports import UiDriver
from mtf.core.types import Platform
from mtf.screenplay.robots.android import (
    AndroidCatalogScreenRobot,
    AndroidNavigationRobot,
)
from mtf.screenplay.robots.ios import IosDialogsScreenRobot, IosLoginScreenRobot
from mtf.screenplay.robots.screens import (
    AccessRobot,
    CartScreenRobot,
    CatalogScreenRobot,
    DialogsScreenRobot,
    GesturesScreenRobot,
    HomeScreenRobot,
    LoginScreenRobot,
    NavigationRobot,
    ProductDetailRobot,
    ProfileScreenRobot,
)


@dataclass(frozen=True)
class Robots:
    navigation: NavigationRobot
    login: LoginScreenRobot
    home: HomeScreenRobot
    catalog: CatalogScreenRobot
    product: ProductDetailRobot
    cart: CartScreenRobot
    profile: ProfileScreenRobot
    dialogs: DialogsScreenRobot
    gestures: GesturesScreenRobot
    access: AccessRobot


def robots_for(driver: UiDriver, timeout_ms: int) -> Robots:
    platform = driver.platform
    if platform == Platform.ANDROID:
        navigation: NavigationRobot = AndroidNavigationRobot(driver, timeout_ms)
        login: LoginScreenRobot = LoginScreenRobot(driver, timeout_ms)
        dialogs: DialogsScreenRobot = DialogsScreenRobot(driver, timeout_ms)
        catalog: CatalogScreenRobot = AndroidCatalogScreenRobot(driver, timeout_ms)
    elif platform == Platform.IOS:
        navigation = NavigationRobot(driver, timeout_ms)
        login = IosLoginScreenRobot(driver, timeout_ms)
        dialogs = IosDialogsScreenRobot(driver, timeout_ms)
        catalog = CatalogScreenRobot(driver, timeout_ms)
    else:
        raise ConfigurationError(f"no Screen Robots for platform {platform.value}")
    return Robots(
        navigation=navigation,
        login=login,
        home=HomeScreenRobot(driver, timeout_ms),
        catalog=catalog,
        product=ProductDetailRobot(driver, timeout_ms),
        cart=CartScreenRobot(driver, timeout_ms),
        profile=ProfileScreenRobot(driver, timeout_ms),
        dialogs=dialogs,
        gestures=GesturesScreenRobot(driver, timeout_ms),
        access=AccessRobot(driver, timeout_ms),
    )
