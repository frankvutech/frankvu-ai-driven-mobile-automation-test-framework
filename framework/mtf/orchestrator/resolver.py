"""Resolves ``${...}`` references in step parameters at execution time.

Only plain lookups are supported: test data (``${products.product_001.name}``), run variables
(``${vars.order_number}``) and secrets (``${secret:accounts.customer.password}``). Secrets are
registered with the redactor the moment they are resolved.
"""

from __future__ import annotations

from typing import Any

from mtf.contracts.test_data import SecretProvider, TestDataRegistry
from mtf.core.errors import ConfigurationError
from mtf.core.redaction import Redactor
from mtf.dsl.models import REF_RE


class ReferenceResolver:
    def __init__(
        self,
        test_data: TestDataRegistry,
        secrets: SecretProvider,
        redactor: Redactor,
        variables: dict[str, Any],
    ) -> None:
        self.test_data = test_data
        self.secrets = secrets
        self.redactor = redactor
        self.variables = variables

    def _lookup(self, ref: str) -> Any:
        if ref.startswith("secret:"):
            value = self.secrets.resolve(ref.removeprefix("secret:"))
            self.redactor.add_secret(value)
            return value
        if ref.startswith("vars."):
            name = ref.removeprefix("vars.")
            if name not in self.variables:
                raise ConfigurationError(f"run variable {name!r} has not been set")
            return self.variables[name]
        try:
            return self.test_data.lookup(ref)
        except KeyError as exc:
            raise ConfigurationError(f"unknown reference ${{{ref}}}") from exc

    def resolve(self, value: Any) -> Any:
        if isinstance(value, str):
            whole = REF_RE.fullmatch(value)
            if whole:  # a single reference keeps its type (numbers stay numbers)
                return self._lookup(whole.group(1))
            return REF_RE.sub(lambda m: str(self._lookup(m.group(1))), value)
        if isinstance(value, dict):
            return {k: self.resolve(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve(v) for v in value]
        return value
