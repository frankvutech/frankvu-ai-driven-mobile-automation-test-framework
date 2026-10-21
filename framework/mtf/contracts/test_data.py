"""Deterministic test data (`contracts/test-data.yaml`) and secret resolution."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from mtf.core.errors import ConfigurationError


class TestDataRegistry:
    __test__ = False

    def __init__(self, data: Mapping[str, Any]) -> None:
        if data.get("version") != 1:
            raise ConfigurationError(f"unsupported test-data version {data.get('version')!r}")
        self.data = dict(data)
        self.accounts: dict[str, dict[str, Any]] = dict(data.get("accounts") or {})
        self.products: dict[str, dict[str, Any]] = dict(data.get("products") or {})
        self.missing_products: list[str] = list(data.get("missing_products") or [])
        for ref, account in self.accounts.items():
            if "password" in account:
                raise ConfigurationError(
                    f"accounts.{ref} stores a password value; use password_env with an environment variable"
                )

    @classmethod
    def load(cls, path: Path) -> TestDataRegistry:
        try:
            return cls(yaml.safe_load(path.read_text(encoding="utf-8")))
        except OSError as exc:
            raise ConfigurationError(f"cannot read test data {path}: {exc}") from exc

    # --- references ----------------------------------------------------------------------------
    def lookup(self, path: str) -> Any:
        """Resolves a dotted reference such as `products.product_001.price_display`."""
        node: Any = self.data
        for part in path.split("."):
            if isinstance(node, Mapping) and part in node:
                node = node[part]
            elif isinstance(node, list) and part.isdigit() and int(part) < len(node):
                node = node[int(part)]
            else:
                raise KeyError(path)
        return node

    def has(self, path: str) -> bool:
        try:
            self.lookup(path)
        except KeyError:
            return False
        return True

    def is_secret_ref(self, path: str) -> bool:
        parts = path.split(".")
        return len(parts) == 3 and parts[0] == "accounts" and parts[1] in self.accounts and parts[2] == "password"

    def secret_env_var(self, path: str) -> str:
        if not self.is_secret_ref(path):
            raise KeyError(path)
        return str(self.accounts[path.split(".")[1]]["password_env"])

    def reference_values(self) -> dict[str, list[str]]:
        """Values the test-ID registry accepts for `ref:` pattern parameters."""
        return {
            "products": sorted(self.products),
            "users": sorted(str(a["user_id"]) for a in self.accounts.values()),
        }

    def describe_for_planner(self) -> dict[str, Any]:
        """Everything a planner may reference, without any secret value."""
        return {
            "accounts": {
                ref: {k: v for k, v in account.items() if k != "password_env"}
                | {"password": f"${{secret:accounts.{ref}.password}}"}
                for ref, account in self.accounts.items()
            },
            "inputs": self.data.get("inputs", {}),
            "products": self.products,
            "missing_products": self.missing_products,
            "categories": self.data.get("categories", []),
            "deep_links": self.data.get("deep_links", {}),
            "result_codes": self.data.get("result_codes", {}),
        }


class SecretProvider:
    """Resolves secret references from the environment (or an injected mapping in tests)."""

    def __init__(self, registry: TestDataRegistry, environ: Mapping[str, str] | None = None) -> None:
        self.registry = registry
        self.environ = environ if environ is not None else os.environ

    def resolve(self, path: str) -> str:
        try:
            var = self.registry.secret_env_var(path)
        except KeyError as exc:
            raise ConfigurationError(f"unknown secret reference {path!r}") from exc
        value = self.environ.get(var)
        if not value:
            raise ConfigurationError(
                f"secret {path!r} needs the environment variable {var} (see config/.env.example)",
                details={"env_var": var},
            )
        return value

    def configured_values(self) -> list[str]:
        """Every account password that is set, whether or not the current plan uses it.

        A reused runner session (or its log) can still hold text typed by an earlier plan, so a run
        must be able to redact secrets that it never resolved itself.
        """
        variables = (str(account["password_env"]) for account in self.registry.accounts.values())
        return [value for var in variables if (value := self.environ.get(var))]

    def missing(self, paths: list[str]) -> list[str]:
        """Environment variables that are not set for the given secret references."""
        out = []
        for path in paths:
            try:
                var = self.registry.secret_env_var(path)
            except KeyError:
                continue
            if not self.environ.get(var):
                out.append(var)
        return sorted(set(out))
