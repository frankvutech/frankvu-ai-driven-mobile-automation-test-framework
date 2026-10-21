"""Android Screen Robots: only where the Compose app differs from iOS.

All selectors are Compose test tags exposed as resource IDs (`testTagsAsResourceId`), resolved by
the UIAutomator runner. Compose dialogs, bottom sheets and menus of the supplied APK render in
separate windows without `testTagsAsResourceId`, so their content is not addressable here (the
registry marks those IDs as unavailable on Android and the validator rejects plans that need them).
"""

from __future__ import annotations

from mtf.screenplay.robots.screens import CatalogScreenRobot, NavigationRobot


class AndroidNavigationRobot(NavigationRobot):
    def back(self) -> None:
        # Screens with a top app bar expose `top_bar_back` (Android only); otherwise use system Back.
        if self.is_visible("top_bar_back"):
            self.tap("top_bar_back")
        else:
            self.driver.back()


class AndroidCatalogScreenRobot(CatalogScreenRobot):
    # On iOS the list's identifier is shadowed by the screen root (see contracts/test-ids.yaml),
    # so only Android scrolls the catalog by its list ID.
    LIST_CONTAINER = "catalog_list"
