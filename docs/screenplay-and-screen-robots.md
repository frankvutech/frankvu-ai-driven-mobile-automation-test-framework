# Screenplay and Screen Robots

Plans describe **what a user does and observes**. Screenplay maps that to code; Screen Robots hide
**how** each screen is driven on each platform.

```text
plan step ──► Task / Question (Screenplay, shared) ──► Screen Robot (per screen, per platform)
                                                        ──► UiDriver port ──► UIAutomator | XCUITest
```

## Screenplay

| Concept | Code | Example |
|---|---|---|
| Actor | `screenplay/actor.py` `Actor` | `customer` with account `customer` |
| Ability | `abilities.py` | `OperateMobileApp` (driver + robots), `CallBackendApi` (API client) |
| Task | `tasks.py` | `Login`, `AddProductToCart`, `Checkout`, `OpenDialog` |
| Interaction | `interactions.py` | `tap`, `type_text`, `scroll_to`, `open_deep_link` |
| Question | `questions.py` | `CartTotal`, `ProductIsInCart`, `ApiCartQuantity`, `CurrentScreen` |
| Assertion | DSL `assert` step | question + matcher, optional `timeout_ms` (eventual consistency) |
| Fixture | `fixtures.py` | `ResetBackendData`, `LaunchApp`, `SeedCart` |

Tasks compose other tasks:

```python
@register
class AddProductToCartFromDetails(Task):
    spec = _task("add_product_to_cart_from_details", "...", product=ParamSpec(ParamKind.PRODUCT))

    def perform_as(self, actor: Actor) -> None:
        actor.attempts_to(OpenProduct(product=self.param("product")))
        robots_of(actor).product.add_to_cart()
```

Each class declares an `ActionSpec` (name, description, typed parameters, platforms, the test IDs its
robots use). The specs form the catalog that the validator and the AI planner see
(`uv run mtf catalog`); the registry maps the same names to the classes. A unit test checks that the
two never diverge.

Actors hold identity, so one actor can act in the UI and through the API in the same plan:

```yaml
- task: checkout                                    # UI, as customer
- assert: {question: order_number, expect: {matches: 'ORDER-\d{4}'}, save: ui_order}
- assert: {question: api_latest_order_number, expect: {equals: "${vars.ui_order}"}}   # API, same customer
```

## Screen Robots

`screenplay/robots/screens.py` holds behaviour that is identical on both platforms (shared IDs):
`LoginScreenRobot.submit()`, `CatalogScreenRobot.add_to_cart()`, `CartScreenRobot.quantity()`,
`CartScreenRobot.checkout()` and so on.

Platform subclasses exist only for real differences:

| Robot | Android (`android.py`) | iOS (`ios.py`) | Why |
|---|---|---|---|
| Navigation `back()` | `top_bar_back`, else system Back | system navigation-bar back button | `top_bar_back` exists only in the Compose app |
| Login `after_successful_login()` | nothing | declines the AutoFill "Save Password?" sheet | iOS shows a system sheet that blocks taps |
| Login `tap_login()` | tap | dismiss keyboard, then tap | the keyboard can cover the button |
| Catalog `LIST_CONTAINER` | `catalog_list` | none (swipe the screen) | the list's ID is shadowed on iOS |
| Dialogs `wait_for_dialog()` | — | waits for the button identifiers | Compose dialog content is not addressable on Android (see `test-contract.md`) |

`robots_for(driver, timeout)` (Strategy) returns the right set for the driver's platform.

Robots never use visible text or coordinates. The only exceptions are two pieces of **system** UI
that have no app identifier: the iOS navigation-bar back button and the iOS AutoFill sheet's
"Not Now" button. Both are handled inside the iOS runner and documented.

## Adding a scenario

1. Prefer existing tasks and questions (`uv run mtf catalog`).
2. If a screen interaction is missing, add a method to the screen's robot (shared first; a platform
   subclass only for a documented difference).
3. Add a task or question class with an `ActionSpec`; register it with `@register`; list the IDs its
   robots need in `test_ids` when they are platform-sensitive.
4. Write the plan in `plans/shared/` (or `plans/<platform>/`), run `mtf validate`, then run it.
5. Add a unit test with the fakes in `framework/tests/fakes.py` if the task has logic.
