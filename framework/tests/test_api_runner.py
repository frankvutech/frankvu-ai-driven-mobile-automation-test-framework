from __future__ import annotations

from typing import Any

import pytest

from mtf.api_runner import jsonpath
from mtf.api_runner.assertions import check_json, check_schema, check_status
from mtf.api_runner.client import ApiClient, ApiExchange
from mtf.contracts.bundle import Contracts
from mtf.contracts.test_data import SecretProvider
from mtf.core.errors import ConfigurationError, InfrastructureError
from mtf.core.matching import evaluate
from mtf.core.ports import ApiResponse
from mtf.core.redaction import REDACTED, Redactor

from .fakes import PASSWORDS, FakeBackend


def make_client(
    contracts: Contracts, backend: FakeBackend, exchanges: list[ApiExchange], redactor: Redactor
) -> ApiClient:
    return ApiClient(
        "http://localhost:8080/",
        contract=contracts.api,
        transport=backend,
        redactor=redactor,
        test_data=contracts.test_data,
        secrets=SecretProvider(contracts.test_data, dict(PASSWORDS)),
        on_exchange=exchanges.append,
    )


def test_tokens_are_acquired_once_and_never_recorded(contracts: Contracts, backend: FakeBackend) -> None:
    exchanges: list[ApiExchange] = []
    redactor = Redactor()
    client = make_client(contracts, backend, exchanges, redactor)
    cart = contracts.api.operation("GET", "/api/v1/cart")
    assert client.send(cart, account_ref="customer").status == 200
    assert client.send(cart, account_ref="customer").status == 200
    logins = [r for r in backend.requests if r["url"].endswith("/auth/login")]
    assert len(logins) == 1
    recorded = repr([e.to_dict() for e in exchanges])
    assert PASSWORDS["MTF_PASSWORD_CUSTOMER"] not in recorded
    token = next(iter(backend.world.tokens))
    assert token not in recorded
    assert exchanges[0].purpose == "setup"
    assert exchanges[1].request["headers"]["Authorization"] == REDACTED


def test_token_modes(contracts: Contracts, backend: FakeBackend) -> None:
    client = make_client(contracts, backend, [], Redactor())
    cart = contracts.api.operation("GET", "/api/v1/cart")
    assert client.send(cart, auth_mode="none").body["error"]["code"] == "UNAUTHORIZED"
    assert client.send(cart, auth_mode="invalid").body["error"]["code"] == "INVALID_TOKEN"


def test_bad_credentials_fail_setup_with_redacted_details(contracts: Contracts, backend: FakeBackend) -> None:
    secrets = SecretProvider(contracts.test_data, {"MTF_PASSWORD_CUSTOMER": "wrong-password-value"})
    client = ApiClient(
        "http://localhost:8080",
        contract=contracts.api,
        transport=backend,
        redactor=Redactor(),
        test_data=contracts.test_data,
        secrets=secrets,
    )
    with pytest.raises(InfrastructureError, match="could not obtain a token"):
        client.token_for("customer")


def test_invalid_base_url(contracts: Contracts, backend: FakeBackend) -> None:
    with pytest.raises(ConfigurationError):
        ApiClient(
            "localhost:8080",
            contract=contracts.api,
            transport=backend,
            redactor=Redactor(),
            test_data=contracts.test_data,
            secrets=SecretProvider(contracts.test_data, {}),
        )


def response(status: int, body: Any) -> ApiResponse:
    return ApiResponse(status, {"content-type": "application/json"}, body, "", 1.0, {})


def test_assertions() -> None:
    ok = response(200, {"items": [{"id": "product_001"}], "total": 3})
    assert check_status(ok, 200).passed
    assert check_status(ok, [200, 201]).passed
    assert not check_status(ok, 404).passed
    assert check_json(ok, "items[0].id", "equals", "product_001").passed
    assert check_json(ok, "items.length", "equals", 1).passed
    assert check_json(ok, "total", "greater_than", 2).passed
    missing = check_json(ok, "items[5].id", "equals", "x")
    assert not missing.passed
    assert missing.actual == "<missing>"


def test_schema_check_catches_contract_violations(contracts: Contracts) -> None:
    op = contracts.api.operation("GET", "/api/v1/products/{id}")
    bad = check_schema(contracts.api, op, response(200, {"id": 7}))
    assert not bad.passed
    assert bad.actual


def test_jsonpath() -> None:
    doc = {"a": [{"b": 1}, {"b": 2}], "c": "text"}
    assert jsonpath.get(doc, "$") is doc
    assert jsonpath.get(doc, "a[1].b") == 2
    assert jsonpath.get(doc, "a.length") == 2
    assert jsonpath.get(doc, "c.length") == 4
    assert jsonpath.get(doc, "a[9]") is jsonpath.MISSING
    assert jsonpath.get([{"x": 1}], "[0].x") == 1
    with pytest.raises(jsonpath.JsonPathError):
        jsonpath.get(doc, "a[?(@.b>1)]")


@pytest.mark.parametrize(
    ("operator", "expected", "actual", "passed"),
    [
        ("equals", 1, 1.0, True),
        ("equals", "1", 1, False),  # no text/number coercion
        ("equals", True, 1, False),
        ("not_equals", "a", "b", True),
        ("contains", "err", "an error", True),
        ("not_contains", "Exception", "fine", True),
        ("matches", r"ORDER-\d{4}", "ORDER-0001", True),
        ("matches", r"ORDER-\d{4}", "ORDER-01", False),
        ("one_of", ["a", "b"], "b", True),
        ("greater_than", 2, "3", True),
        ("less_than", 2, None, False),
    ],
)
def test_matchers(operator: str, expected: Any, actual: Any, passed: bool) -> None:
    assert evaluate(operator, expected, actual).passed is passed
