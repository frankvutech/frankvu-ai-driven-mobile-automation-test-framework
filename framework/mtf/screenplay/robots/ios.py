"""iOS Screen Robots: only where the SwiftUI app differs from Android.

All selectors are accessibility identifiers, resolved by the XCUITest runner.
"""

from __future__ import annotations

from mtf.screenplay.robots.screens import DialogsScreenRobot, LoginScreenRobot


class IosLoginScreenRobot(LoginScreenRobot):
    def tap_login(self) -> None:
        # The keyboard can cover the button on small devices; the button keeps its identifier.
        self.driver.dismiss_keyboard()
        super().tap_login()

    def after_successful_login(self) -> None:
        # iOS AutoFill offers "Save Password?" after a login in the simulator; the sheet blocks taps.
        self.driver.dismiss_system_dialogs()


class IosDialogsScreenRobot(DialogsScreenRobot):
    def wait_for_dialog(self, kind: str) -> None:
        # SwiftUI alerts and confirmation dialogs are system UI without a container identifier;
        # the app sets identifiers on their buttons, so wait for the relevant button instead.
        if kind == "action_sheet":
            self.wait_visible("action_sheet_option_share")
        elif kind == "bottom_sheet":
            self.wait_visible("bottom_sheet_close")
        else:
            super().wait_for_dialog(kind)
