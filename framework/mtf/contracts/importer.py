"""Imports the app's published automation-ID catalogue into `contracts/test-ids.yaml`.

This is a *contract update* step, run by a person when the app team publishes new IDs. It reads
(never writes) the app repository:

* the catalogue `TestTags.kt` (the app's single source of IDs; iOS `TestIDs.swift` is generated
  from it), and
* the Android and iOS UI sources, only to record on which platform each ID is actually used.

Test runs never read the app repository: they use the committed YAML.
"""

from __future__ import annotations

import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from mtf.core.errors import ConfigurationError

CATALOGUE = Path("android/core/ui/src/main/kotlin/com/mobiletestplayground/core/ui/TestTags.kt")
ANDROID_SOURCES = Path("android")
IOS_SOURCES = Path("ios/MobileTestPlayground")
IOS_CATALOGUE_NAME = "TestIDs.swift"

# Text typed into these fields is secret: it is never logged and the plan must use a secret reference.
SENSITIVE_IDS = frozenset(
    {
        "login_password",
        "register_password",
        "register_confirm_password",
        "reset_password",
        "reset_confirm_password",
        "form_password",
    }
)
# Tapping these can make a secret visible on screen; screenshots are suppressed afterwards.
REVEALS_SECRET_IDS = frozenset({"login_password_toggle"})
EXTRA_SCREEN_ROOTS = frozenset({"admin_dashboard", "admin_products", "admin_users"})

SCENARIOS = (
    "auth", "forms", "controls", "lists", "scrolling", "gestures", "navigation", "dialogs",
    "permissions", "webview", "network", "accessibility", "errors", "dynamic_content", "test_data",
    "deep_links", "date_time", "file_picker", "notifications", "test_mode",
)  # fmt: skip
NETWORK_SCENARIOS = (
    "success", "slow", "timeout", "http_400", "http_401", "http_403", "http_404", "http_500",
    "empty", "malformed",
)  # fmt: skip

# Constraints for the parameter of every pattern builder in the catalogue. Enumerations come from the
# app's screens; where the app builds the value dynamically the constraint is a regex, and the
# element's existence is checked on the device at run time.
PARAMS: dict[str, dict[str, Any]] = {
    "scenario": {"values": list(SCENARIOS)},
    "product": {"ref": "products"},
    "productAddToCart": {"ref": "products"},
    "cartItem": {"ref": "products"},
    "cartItemQuantity": {"ref": "products"},
    "cartRemove": {"ref": "products"},
    "adminProduct": {"ref": "products"},
    "testDataProduct": {"ref": "products"},
    "networkResultItem": {"ref": "products"},
    "adminUser": {"ref": "users"},
    "testDataUser": {"ref": "users"},
    "settingsTheme": {"values": ["system", "light", "dark"]},
    "formCountryOption": {"values": ["de", "fr", "gb", "us"]},
    "formPlan": {"values": ["basic", "pro", "enterprise"]},
    "formError": {"ref": "test_id"},
    "controlsRadio": {"values": ["small", "medium", "large"]},
    "listItem": {"regex": r"\d{3}"},
    "dynamicListSort": {"regex": r"[a-z_]+"},
    "dynamicListItem": {"regex": r"\d+"},
    "dynamicListEdit": {"regex": r"\d+"},
    "dynamicListDelete": {"regex": r"\d+"},
    "infiniteItem": {"regex": r"\d{3}"},
    "scrollSection": {"regex": r"\d{2}"},
    "scrollCard": {"regex": r"\d{2}"},
    "navNestedTab": {"values": ["a", "b", "c"]},
    "actionSheetOption": {"values": ["share", "duplicate", "archive"]},
    "permissionStatus": {"regex": r"[a-z_]+"},
    "permissionRequest": {"regex": r"[a-z_]+"},
    "networkScenario": {"values": list(NETWORK_SCENARIOS)},
    "deepLinkExample": {"values": ["home", "forms", "product", "cart", "admin", "detail"]},
}

_CONST_RE = re.compile(r'const val ([A-Z0-9_]+) = "([a-z0-9_]+)"')
_FUNC_RE = re.compile(r"fun ([a-zA-Z0-9]+)\(([a-zA-Z]+): (String|Int)\) = (.+)")
_SECTION_RE = re.compile(r"//\s*---\s*(.+?)\s*---")


def _camel(name: str) -> str:
    parts = name.lower().split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)([A-Z])", r"_\1", name).lower()


def _humanize(const: str, section: str) -> str:
    words = const.lower().split("_")
    section_words = {w.lower() for w in re.findall(r"[A-Za-z0-9]+", section)}
    while len(words) > 1 and words[0] in section_words:
        words = words[1:]
    return " ".join(words).capitalize()


def _template(arg: str, body: str) -> str:
    body = body.strip()
    placeholder = "{" + _snake(arg) + "}"
    if body == arg:
        return placeholder
    literal = re.fullmatch(r'"(.*)"', body)
    if literal:
        text = literal.group(1).replace("${" + arg + "}", placeholder)
        return text.replace("$" + arg, placeholder)
    prefix = re.match(r'"([a-z_]+)"\s*\+', body)
    if prefix and "padStart" in body:
        return prefix.group(1) + placeholder
    raise ConfigurationError(f"cannot derive an ID template from {body!r}")


def _read_sources(root: Path, suffix: str, *, exclude_name: str | None = None) -> str:
    parts = []
    for path in sorted(root.rglob(f"*{suffix}")):
        text = str(path)
        if "/build/" in text or "/test/" in text or "/androidTest/" in text or path.name == exclude_name:
            continue
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def shadowed_ios_ids(ios_source: str) -> dict[str, str]:
    """IDs whose `.accessibilityIdentifier` is immediately overridden by an outer identifier modifier.

    SwiftUI keeps only the outermost identifier of one element, so in
    ``List { ... }.accessibilityIdentifier(TestIDs.catalogList).screenRoot(TestIDs.catalogScreen)``
    the list is exposed as `catalog_screen` and `catalog_list` never exists at run time. Returns
    {shadowed camelCase name: winning camelCase name}.
    """
    shadowed: dict[str, str] = {}
    lines = ios_source.splitlines()
    for index, line in enumerate(lines):
        inner = re.search(r"\.accessibilityIdentifier\(TestIDs\.(\w+)\)\s*$", line)
        if not inner:
            continue
        for following in lines[index + 1 : index + 4]:
            text = following.strip()
            if not text.startswith("."):
                break
            outer = re.search(r"\.(?:screenRoot|accessibilityIdentifier)\(TestIDs\.(\w+)\)", text)
            if outer:
                shadowed[inner.group(1)] = outer.group(1)
                break
    return shadowed


WINDOW_COMPOSABLES = (
    "AlertDialog", "BasicAlertDialog", "DatePickerDialog", "TimePickerDialog", "Dialog",
    "ModalBottomSheet", "DropdownMenu", "ExposedDropdownMenu", "Popup",
)  # fmt: skip
_WINDOW_CALL_RE = re.compile(r"\b(" + "|".join(WINDOW_COMPOSABLES) + r")\s*\(")


def _block_end(text: str, start: int, opening: str, closing: str) -> int:
    depth = 0
    for index in range(start, len(text)):
        if text[index] == opening:
            depth += 1
        elif text[index] == closing:
            depth -= 1
            if depth == 0:
                return index
    return len(text) - 1


def android_window_ids(android_source: str) -> dict[str, str]:
    """TestTags constants used inside composables that open a separate window (dialogs, sheets, menus).

    `testTagsAsResourceId` set on the root composable does not reach other windows, so unless the
    window re-applies it, UIAutomator (the black-box runner) sees no resource IDs there. Returns
    {CONSTANT_NAME: window composable}. A heuristic over the source; confirmed at run time for the
    Dialogs scenario (docs/test-contract.md).
    """
    found: dict[str, str] = {}
    blocks: list[tuple[str, str]] = []
    for match in _WINDOW_CALL_RE.finditer(android_source):
        open_paren = match.end() - 1
        end = _block_end(android_source, open_paren, "(", ")")
        rest = android_source[end + 1 : end + 3].lstrip()
        if rest.startswith("{"):
            end = _block_end(android_source, android_source.index("{", end + 1), "{", "}")
        block = android_source[open_paren : end + 1]
        if "testTagsAsResourceId" not in block:
            blocks.append((match.group(1), block))
    # Helpers that receive the tag as a parameter and put it on a window, e.g.
    # `fun SingleActionDialog(..., tag: String) { AlertDialog(modifier = Modifier.testTag(tag)) }`.
    for helper in re.finditer(r"fun\s+([A-Z]\w*)\s*\(([^)]*)\)", android_source):
        params = re.findall(r"(\w+)\s*:\s*String", helper.group(2))
        body_start = android_source.find("{", helper.end())
        if body_start < 0 or not params:
            continue
        body = android_source[body_start : _block_end(android_source, body_start, "{", "}") + 1]
        window = next((w for w, b in blocks if b in body), None)
        if window is None or not any(f"testTag({p})" in body for p in params):
            continue
        for call in re.finditer(rf"\b{helper.group(1)}\s*\(", android_source):
            if call.start() == helper.start() + helper.group(0).index(helper.group(1)):
                continue
            args = android_source[call.end() - 1 : _block_end(android_source, call.end() - 1, "(", ")") + 1]
            blocks.append((window, args))
    for window, block in blocks:
        for constant in re.findall(r"TestTags\.([A-Z][A-Z0-9_]+)\b", block):
            found.setdefault(constant, window)
        for builder in re.findall(r"TestTags\.([a-z][A-Za-z0-9]+)\(", block):
            found.setdefault(builder, window)
    return found


def _git_commit(repo: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() or None


WINDOW_NOTE = "rendered in a separate {window} window without testTagsAsResourceId: no resource ID for UIAutomator"
SHADOW_NOTE = "its identifier is overridden by the outer {winner} identifier on the same SwiftUI element"


class _Availability:
    """Decides on which platform the black-box runners can address an ID, and why not elsewhere."""

    def __init__(self, android: str, ios: str) -> None:
        self.android = android
        self.ios = ios
        self.shadowed = shadowed_ios_ids(ios)
        self.windowed = android_window_ids(android)
        self.shadow_notes: dict[str, str] = {}
        self.window_notes: dict[str, str] = {}

    def of_constant(self, name: str, value: str) -> tuple[list[str], dict[str, str]]:
        platforms: list[str] = []
        unavailable: dict[str, str] = {}
        if re.search(rf"TestTags\.{name}\b", self.android):
            if name in self.windowed:
                unavailable["android"] = WINDOW_NOTE.format(window=self.windowed[name])
                self.window_notes[value] = unavailable["android"]
            else:
                platforms.append("android")
        if re.search(rf"TestIDs\.{_camel(name)}\b", self.ios):
            if _camel(name) in self.shadowed:
                unavailable["ios"] = SHADOW_NOTE.format(winner=self.shadowed[_camel(name)])
                self.shadow_notes[value] = unavailable["ios"]
            else:
                platforms.append("ios")
        return platforms, unavailable

    def of_builder(self, name: str, template: str) -> tuple[list[str], dict[str, str]]:
        platforms: list[str] = []
        unavailable: dict[str, str] = {}
        if f"TestTags.{name}(" in self.android:
            if name in self.windowed:
                unavailable["android"] = WINDOW_NOTE.format(window=self.windowed[name])
                self.window_notes[template] = unavailable["android"]
            else:
                platforms.append("android")
        if f"TestIDs.{name}(" in self.ios:
            platforms.append("ios")
        return platforms, unavailable


def _constant_entry(name: str, value: str, section: str, availability: _Availability) -> dict[str, Any] | None:
    platforms, unavailable = availability.of_constant(name, value)
    if not platforms and not unavailable:
        return None
    entry: dict[str, Any] = {"screen": section, "element": _humanize(name, section), "platforms": platforms}
    if unavailable:
        entry["unavailable"] = unavailable
    if value in SENSITIVE_IDS:
        entry["sensitive"] = True
    if value in REVEALS_SECRET_IDS:
        entry["reveals_secret"] = True
    if value.endswith("_screen") or value in EXTRA_SCREEN_ROOTS:
        entry["screen_root"] = True
    return entry


def import_test_ids(app_repo: Path) -> dict[str, Any]:
    catalogue_path = app_repo / CATALOGUE
    if not catalogue_path.is_file():
        raise ConfigurationError(f"ID catalogue not found: {catalogue_path}")
    catalogue = catalogue_path.read_text(encoding="utf-8")
    android = _read_sources(app_repo / ANDROID_SOURCES, ".kt", exclude_name=CATALOGUE.name)
    ios = _read_sources(app_repo / IOS_SOURCES, ".swift", exclude_name=IOS_CATALOGUE_NAME)

    ids: dict[str, Any] = {}
    patterns: dict[str, Any] = {}
    unused: list[str] = []
    availability = _Availability(android, ios)
    section = "General"
    for line in catalogue.splitlines():
        if heading := _SECTION_RE.search(line):
            section = heading.group(1)
        elif const := _CONST_RE.search(line):
            name, value = const.groups()
            entry = _constant_entry(name, value, section, availability)
            if entry is None:
                unused.append(value)
            else:
                ids[value] = entry
        elif func := _FUNC_RE.search(line):
            name, arg, _type, body = func.groups()
            if name not in PARAMS:
                raise ConfigurationError(f"no parameter constraint for catalogue builder {name}(); add it to PARAMS")
            template = _template(arg, body)
            platforms, unavailable = availability.of_builder(name, template)
            if platforms or unavailable:
                patterns[_snake(name)] = {
                    "template": template,
                    "screen": section,
                    "element": _humanize(_snake(name).upper(), section) + " (pattern)",
                    "platforms": platforms,
                    "params": {_snake(arg): PARAMS[name]},
                    **({"unavailable": unavailable} if unavailable else {}),
                }

    return {
        "version": 1,
        "source": {
            "app": "Mobile Test Playground",
            "catalogue": str(CATALOGUE),
            "app_commit": _git_commit(app_repo),
            "imported_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            "importer": "mtf contracts import-ids",
            "unused_ids_skipped": unused,
            "ios_shadowed_ids": availability.shadow_notes,
            "android_window_ids": availability.window_notes,
        },
        "ids": ids,
        "patterns": patterns,
    }


HEADER = """\
# Shared test-ID registry: the logical IDs the framework may send to a device.
#
# GENERATED by `mtf contracts import-ids --app-repo <path>` from the app's published catalogue
# (TestTags.kt; the iOS TestIDs.swift is generated from the same file). Do not edit by hand: change
# the annotations in framework/mtf/contracts/importer.py and re-import.
#
# - platforms: where the black-box runners can address the element (scanned from each platform's UI
#   code; IDs shadowed on iOS or rendered in Android windows without testTagsAsResourceId are excluded,
#   see source.ios_shadowed_ids and source.android_window_ids).
# - sensitive: text typed into it is secret; plans must use a ${secret:...} reference.
# - reveals_secret: interacting with it can show a secret; later screenshots are suppressed.
# - screen_root: the element identifies a screen (used by the current-screen question).
# - patterns: IDs for repeated items, keyed by stable domain IDs, with parameter constraints.
"""


def write_registry(data: dict[str, Any], path: Path) -> None:
    path.write_text(
        HEADER + yaml.safe_dump(data, sort_keys=False, width=110, default_flow_style=None), encoding="utf-8"
    )
