# The app contract

Three committed files define everything a plan may reference. Test runs read only these files —
never the application repository.

| File | What | Source of truth |
|---|---|---|
| `contracts/test-ids.yaml` | Logical test IDs, ID patterns for repeated items, platform availability, sensitivity | The app's ID catalogue (`TestTags.kt`; iOS `TestIDs.swift` is generated from it), imported with `mtf contracts import-ids` |
| `contracts/openapi/backend.yaml` | Backend operations, statuses and schemas | The backend's published spec (served at `/openapi/openapi.yaml`) |
| `contracts/test-data.yaml` | Seeded accounts (without passwords), products, inputs, deep links, result codes | The backend seed (`SeedData.kt`), identical in the apps' offline seed |
| `contracts/test-plan.schema.json` | JSON Schema of the DSL | Generated from the framework's DSL models |

## Test IDs

IDs are lowercase `snake_case`, independent of visible text and locale, and identical on Android
(Compose `testTag`, exposed as resource ID through `testTagsAsResourceId`) and iOS
(`accessibilityIdentifier`) wherever the element exists on both.

Repeated items use stable domain IDs through **patterns** with constrained parameters:

```yaml
patterns:
  cart_item:
    template: cart_item_{product_id}
    params: {product_id: {ref: products}}       # must be a product in test-data.yaml
  list_item:
    template: list_item_{number}
    params: {number: {regex: '\d{3}'}}
  scenario:
    template: scenario_{id}
    params: {id: {values: [auth, forms, ...]}}
```

`cart_item_product_001` is valid; `cart_item_product_999` and `list_item_30` are not.

Per-ID annotations:

* `platforms` — where the black-box runners can address the element.
* `unavailable` — why an element that exists in the app cannot be addressed on a platform.
* `sensitive` — text typed into it is secret (password fields).
* `reveals_secret` — interacting with it can show a secret (password toggle); screenshots are
  suppressed afterwards until the app is relaunched.
* `screen_root` — the element identifies a screen (used by the `current_screen` question).

### Contract gaps found and recorded

The importer scans each platform's UI code for where IDs are used and records two classes of gaps
that make an ID unaddressable at run time even though it is declared:

| Gap | Platform | IDs | How it was found |
|---|---|---|---|
| An inner `.accessibilityIdentifier` overridden by an outer identifier on the same SwiftUI element | iOS | `catalog_list` (overridden by `.screenRoot(catalog_screen)`) | iOS run of `cart_add_product` failed waiting for `catalog_list`; the XCUITest hierarchy showed the list as `catalog_screen` |
| Content of Compose dialogs, bottom sheets and dropdown menus, which render in separate windows that do not inherit `testTagsAsResourceId` (set only on the root composable) | Android | `dialog_*`, `action_sheet*`, `bottom_sheet*`, `date_picker_*`, `time_picker_*`, `dynamic_list_edit_*`, `form_country_option_*` | Android run of the dialogs scenario; `adb shell uiautomator dump` with the dialog open shows every node with `resource-id=""` |

Both are application issues (fix: apply `testTagsAsResourceId` inside each dialog/sheet, and give
the catalog list its own element). The framework does **not** work around them with visible text or
coordinates: the registry marks the IDs unavailable on that platform, the validator rejects plans
that need them before launch, and `plans/ios/dialogs_and_alerts.yaml` covers dialogs on iOS while
`plans/android/dialogs_dismiss_with_back.yaml` covers what Android exposes.

## Validation

* **Before a run**, the validator resolves every `target`, `wait.target`, `assert.target`, test-ID
  parameter and every ID a task's robots declare (`ActionSpec.test_ids`) for each target platform.
  An unknown ID, or one unavailable on the platform, is a `selector_contract_violation` and the run
  is `rejected`.
* **During a run**, the contract guard (`orchestrator/contract_guard.py`) checks every ID again
  before it reaches a device, including IDs that robots build dynamically. There is no fallback to
  text, accessibility labels, coordinates or XPath anywhere in the framework.
* API steps must name an operation (method + path template) in `backend.yaml`; expected statuses
  must be declared for it; path and query parameters must match; success expectations on
  authenticated or admin operations need a suitable actor.

## Updating the contract

```bash
# When the app team publishes new IDs (reads the app repo, never writes it):
uv run mtf contracts import-ids --app-repo ../frankvu-mobile-test-lab

# When the backend API changes: compare the committed copy with a running backend.
uv run mtf contracts check-openapi --base-url http://localhost:8080

# After changing the DSL models:
uv run mtf contracts schema
```

Then run `make check`: plans that reference removed or renamed IDs fail validation immediately.
