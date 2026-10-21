"""The shared test-ID registry (`contracts/test-ids.yaml`).

Every UI selector the framework sends to a device is a *logical ID* from this registry. IDs are
independent of visible text and locale and identical on Android (Compose test tags exposed as
resource IDs) and iOS (accessibility identifiers) wherever the element exists on both.

An ID that is not registered, or not available on the target platform, is a
*selector-contract violation*. The framework never falls back to coordinates or visible text.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from mtf.core.errors import ConfigurationError, SelectorContractViolation
from mtf.core.types import Platform

_ID_RE = re.compile(r"^[a-z0-9]+(_[a-z0-9]+)*$")
_PLACEHOLDER_RE = re.compile(r"\{([a-z_]+)\}")


@dataclass(frozen=True)
class TestIdEntry:
    __test__ = False  # not a pytest class

    id: str
    screen: str
    element: str
    platforms: frozenset[Platform]
    sensitive: bool = False  # text typed into it is secret (password fields)
    reveals_secret: bool = False  # interacting with it can make a secret visible (password toggle)
    screen_root: bool = False
    unavailable: tuple[tuple[str, str], ...] = ()  # (platform, reason) where the runners cannot address it


@dataclass(frozen=True)
class PatternParam:
    name: str
    regex: str | None = None
    values: tuple[str, ...] = ()
    ref: str | None = None  # "products", "accounts.user_id" or "test_id"

    def describe(self) -> str:
        if self.values:
            return "one of " + ", ".join(self.values)
        if self.ref:
            return f"a registered {self.ref} reference"
        return f"matching /{self.regex}/"


@dataclass(frozen=True)
class TestIdPattern:
    name: str
    template: str
    screen: str
    element: str
    platforms: frozenset[Platform]
    params: tuple[PatternParam, ...] = ()
    unavailable: tuple[tuple[str, str], ...] = ()
    _regex: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        body = _PLACEHOLDER_RE.sub(lambda m: f"(?P<{m.group(1)}>[a-z0-9_]+?)", _escape_template(self.template))
        object.__setattr__(self, "_regex", re.compile("^" + body + "$"))

    def match(self, candidate: str) -> dict[str, str] | None:
        found = self._regex.match(candidate)
        return found.groupdict() if found else None


def _escape_template(template: str) -> str:
    """Escapes regex metacharacters in a template while keeping `{param}` placeholders."""
    parts = _PLACEHOLDER_RE.split(template)
    # split() alternates literal, name, literal, name, ...
    out = []
    for index, part in enumerate(parts):
        out.append(re.escape(part) if index % 2 == 0 else "{" + part + "}")
    return "".join(out)


@dataclass
class IdResolution:
    id: str
    platforms: frozenset[Platform]
    entry: TestIdEntry | None = None
    pattern: TestIdPattern | None = None
    params: dict[str, str] = field(default_factory=dict)

    @property
    def sensitive(self) -> bool:
        return bool(self.entry and self.entry.sensitive)

    @property
    def reveals_secret(self) -> bool:
        return bool(self.entry and self.entry.reveals_secret)


class TestIdRegistry:
    __test__ = False  # not a pytest class

    def __init__(
        self,
        entries: Iterable[TestIdEntry],
        patterns: Iterable[TestIdPattern] = (),
        *,
        reference_values: Mapping[str, Iterable[str]] | None = None,
        source: Mapping[str, Any] | None = None,
    ) -> None:
        self.entries: dict[str, TestIdEntry] = {}
        for entry in entries:
            if not _ID_RE.match(entry.id):
                raise ConfigurationError(f"test ID {entry.id!r} is not lowercase snake_case")
            if entry.id in self.entries:
                raise ConfigurationError(f"duplicate test ID {entry.id!r}")
            self.entries[entry.id] = entry
        self.patterns = list(patterns)
        self.reference_values = {k: frozenset(v) for k, v in (reference_values or {}).items()}
        self.source = dict(source or {})

    # --- loading -------------------------------------------------------------------------------
    @classmethod
    def load(cls, path: Path, *, reference_values: Mapping[str, Iterable[str]] | None = None) -> TestIdRegistry:
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ConfigurationError(f"cannot read test-ID registry {path}: {exc}") from exc
        return cls.from_dict(data, reference_values=reference_values)

    @classmethod
    def from_dict(
        cls, data: Mapping[str, Any], *, reference_values: Mapping[str, Iterable[str]] | None = None
    ) -> TestIdRegistry:
        if data.get("version") != 1:
            raise ConfigurationError(f"unsupported test-ID registry version {data.get('version')!r}")
        entries = [
            TestIdEntry(
                id=key,
                screen=str(value.get("screen", "")),
                element=str(value.get("element", "")),
                platforms=frozenset(Platform(p) for p in value["platforms"]),
                sensitive=bool(value.get("sensitive", False)),
                reveals_secret=bool(value.get("reveals_secret", False)),
                screen_root=bool(value.get("screen_root", False)),
                unavailable=tuple(sorted((value.get("unavailable") or {}).items())),
            )
            for key, value in (data.get("ids") or {}).items()
        ]
        patterns = [
            TestIdPattern(
                name=name,
                template=str(value["template"]),
                screen=str(value.get("screen", "")),
                element=str(value.get("element", "")),
                platforms=frozenset(Platform(p) for p in value["platforms"]),
                params=tuple(
                    PatternParam(
                        name=pname,
                        regex=pspec.get("regex"),
                        values=tuple(pspec.get("values", ())),
                        ref=pspec.get("ref"),
                    )
                    for pname, pspec in (value.get("params") or {}).items()
                ),
                unavailable=tuple(sorted((value.get("unavailable") or {}).items())),
            )
            for name, value in (data.get("patterns") or {}).items()
        ]
        return cls(entries, patterns, reference_values=reference_values, source=data.get("source"))

    # --- queries -------------------------------------------------------------------------------
    def resolve(self, candidate: str) -> IdResolution | None:
        """Returns how `candidate` is registered, or None when it is unknown."""
        entry = self.entries.get(candidate)
        if entry is not None:
            return IdResolution(candidate, entry.platforms, entry=entry)
        for pattern in self.patterns:
            params = pattern.match(candidate)
            if params is not None and all(self._param_ok(p, params.get(p.name, "")) for p in pattern.params):
                return IdResolution(candidate, pattern.platforms, pattern=pattern, params=params)
        return None

    def _param_ok(self, param: PatternParam, value: str) -> bool:
        if param.values:
            return value in param.values
        if param.ref == "test_id":
            return value in self.entries
        if param.ref:
            allowed = self.reference_values.get(param.ref)
            return allowed is not None and value in allowed
        if param.regex:
            return re.fullmatch(param.regex, value) is not None
        return False

    def require(self, candidate: str, platform: Platform | None = None) -> IdResolution:
        """Resolves an ID or raises a selector-contract violation."""
        resolution = self.resolve(candidate)
        if resolution is None:
            raise SelectorContractViolation(
                f"unknown test ID {candidate!r}: not in contracts/test-ids.yaml",
                details={"id": candidate},
            )
        if platform is not None and platform.is_mobile and platform not in resolution.platforms:
            available = ", ".join(sorted(p.value for p in resolution.platforms)) or "none"
            reasons = dict(
                resolution.entry.unavailable
                if resolution.entry
                else resolution.pattern.unavailable
                if resolution.pattern
                else ()
            )
            why = f": {reasons[platform.value]}" if platform.value in reasons else ""
            raise SelectorContractViolation(
                f"test ID {candidate!r} is not available on {platform.value}{why} (available on: {available})",
                details={"id": candidate, "platform": platform.value},
            )
        return resolution

    def is_known(self, candidate: str, platform: Platform | None = None) -> bool:
        try:
            self.require(candidate, platform)
        except SelectorContractViolation:
            return False
        return True

    def ids_for(self, platform: Platform) -> list[str]:
        return sorted(i for i, e in self.entries.items() if platform in e.platforms)

    def screen_roots(self, platform: Platform | None = None) -> list[str]:
        return sorted(
            i for i, e in self.entries.items() if e.screen_root and (platform is None or platform in e.platforms)
        )

    def describe_for_planner(self) -> dict[str, Any]:
        """A compact view for the AI planner: IDs grouped by screen, patterns with constraints."""
        screens: dict[str, list[dict[str, Any]]] = {}
        for entry in self.entries.values():
            item: dict[str, Any] = {"id": entry.id, "element": entry.element}
            if entry.platforms != frozenset({Platform.ANDROID, Platform.IOS}):
                item["only_on"] = sorted(p.value for p in entry.platforms)
            if entry.unavailable:
                item["not_addressable_on"] = dict(entry.unavailable)
            if entry.sensitive:
                item["sensitive"] = True
            screens.setdefault(entry.screen, []).append(item)
        patterns = [
            {
                "template": p.template,
                "element": f"{p.screen}: {p.element}",
                "params": {param.name: param.describe() for param in p.params},
            }
            for p in self.patterns
        ]
        return {"screens": screens, "patterns": patterns}
