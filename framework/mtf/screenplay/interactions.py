"""Low-level interactions: one driver call on one registered test ID.

Plans should prefer tasks; interactions exist for scenario screens that have no dedicated task
(gestures, scrolling) and for precise negative tests.
"""

from __future__ import annotations

from typing import Any

from mtf.core.catalog import ActionKind, ActionSpec, ParamKind, ParamSpec
from mtf.core.errors import ConfigurationError
from mtf.core.ports import AppLaunchOptions, Direction
from mtf.dsl.models import InteractionName
from mtf.screenplay.abilities import driver_of
from mtf.screenplay.actor import Actor

_DIRECTIONS = tuple(d.value for d in Direction)


def _spec(name: InteractionName, description: str, *, target: str = "required", **params: ParamSpec) -> ActionSpec:
    return ActionSpec(
        name=name.value,
        kind=ActionKind.INTERACTION,
        description=description,
        params=params,
        needs_target=target == "required",
        optional_target=target == "optional",
    )


INTERACTION_SPECS = [
    _spec(InteractionName.TAP, "Tap the element."),
    _spec(InteractionName.DOUBLE_TAP, "Double-tap the element."),
    _spec(
        InteractionName.LONG_PRESS,
        "Press and hold the element.",
        duration_ms=ParamSpec(ParamKind.INT, required=False, default=1000, minimum=200, maximum=10_000),
    ),
    _spec(
        InteractionName.TYPE_TEXT,
        "Type text into a field (secret fields need a ${secret:...} reference).",
        text=ParamSpec(ParamKind.TEXT, "The text, or a reference."),
        clear_first=ParamSpec(ParamKind.BOOL, required=False, default=True),
    ),
    _spec(InteractionName.CLEAR_TEXT, "Clear a text field."),
    _spec(
        InteractionName.SCROLL_TO,
        "Scroll until the element is on screen (explicit, bounded).",
        direction=ParamSpec(ParamKind.ENUM, required=False, default="down", values=_DIRECTIONS),
        max_swipes=ParamSpec(ParamKind.INT, required=False, default=10, minimum=1, maximum=30),
        container=ParamSpec(ParamKind.TEST_ID, "The scrollable container.", required=False),
    ),
    _spec(
        InteractionName.SWIPE,
        "Swipe on the element (or the screen when no target is given).",
        target="optional",
        direction=ParamSpec(ParamKind.ENUM, values=_DIRECTIONS),
    ),
    _spec(InteractionName.DRAG_TO, "Drag the element onto another element.", to=ParamSpec(ParamKind.TEST_ID)),
    _spec(
        InteractionName.BACK,
        "Navigate back (system Back on Android, navigation bar back on iOS).",
        target="none",
    ),
    _spec(InteractionName.DISMISS_KEYBOARD, "Hide the software keyboard if shown.", target="none"),
    _spec(
        InteractionName.LAUNCH_APP,
        "Launch the app under test.",
        target="none",
        clean_state=ParamSpec(ParamKind.BOOL, "Start from cleared app data.", required=False, default=False),
    ),
    _spec(
        InteractionName.RELAUNCH_APP,
        "Terminate and launch the app again.",
        target="none",
        clean_state=ParamSpec(ParamKind.BOOL, required=False, default=False),
    ),
    _spec(InteractionName.TERMINATE_APP, "Terminate the app process.", target="none"),
    _spec(
        InteractionName.BACKGROUND_APP,
        "Send the app to the background and bring it back.",
        target="none",
        duration_ms=ParamSpec(ParamKind.INT, required=False, default=2000, minimum=500, maximum=30_000),
    ),
    _spec(
        InteractionName.OPEN_DEEP_LINK,
        "Open a registered deep link in the app.",
        target="none",
        link=ParamSpec(ParamKind.DEEP_LINK, "A deep-link name from contracts/test-data.yaml."),
    ),
]

_DEFAULTS = {spec.name: {k: p.default for k, p in spec.params.items() if not p.required} for spec in INTERACTION_SPECS}


def perform_interaction(
    actor: Actor, name: InteractionName, target: str | None, params: dict[str, Any], timeout_ms: int
) -> None:
    driver = driver_of(actor)
    p = _DEFAULTS.get(name.value, {}) | params

    def need_target() -> str:
        if target is None:
            raise ConfigurationError(f"{name.value} needs a target")
        return target

    match name:
        case InteractionName.TAP:
            driver.tap(need_target(), timeout_ms)
        case InteractionName.DOUBLE_TAP:
            driver.double_tap(need_target(), timeout_ms)
        case InteractionName.LONG_PRESS:
            driver.long_press(need_target(), int(p["duration_ms"]), timeout_ms)
        case InteractionName.TYPE_TEXT:
            # Sensitivity is decided by the contract-checking driver from the registry, not by the plan.
            driver.type_text(
                need_target(),
                str(p["text"]),
                clear_first=bool(p["clear_first"]),
                sensitive=False,
                timeout_ms=timeout_ms,
            )
        case InteractionName.CLEAR_TEXT:
            driver.clear_text(need_target(), timeout_ms)
        case InteractionName.SCROLL_TO:
            driver.scroll_to(
                need_target(),
                direction=Direction(p["direction"]),
                max_swipes=int(p["max_swipes"]),
                container=p.get("container"),
                timeout_ms=timeout_ms,
            )
        case InteractionName.SWIPE:
            driver.swipe(target, Direction(p["direction"]), timeout_ms)
        case InteractionName.DRAG_TO:
            driver.drag_to(need_target(), str(p["to"]), timeout_ms)
        case InteractionName.BACK:
            driver.back()
        case InteractionName.DISMISS_KEYBOARD:
            driver.dismiss_keyboard()
        case InteractionName.LAUNCH_APP:
            driver.launch_app(AppLaunchOptions(clean_state=bool(p["clean_state"])))
        case InteractionName.RELAUNCH_APP:
            driver.terminate_app()
            driver.launch_app(AppLaunchOptions(clean_state=bool(p["clean_state"])))
        case InteractionName.TERMINATE_APP:
            driver.terminate_app()
        case InteractionName.BACKGROUND_APP:
            driver.background_app(int(p["duration_ms"]))
        case InteractionName.OPEN_DEEP_LINK:
            url = actor.context.test_data.data["deep_links"][p["link"]]
            driver.open_deep_link(str(url))
