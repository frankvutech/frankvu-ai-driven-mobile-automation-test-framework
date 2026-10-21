from __future__ import annotations

import pytest

from mtf.core.config import check_environment
from mtf.core.errors import PolicyViolation
from mtf.core.redaction import REDACTED, Redactor

from .conftest import make_environment


def test_known_secret_values_are_redacted_everywhere() -> None:
    redactor = Redactor(["Sup3r-Secret!"])
    assert redactor.redact_text("pw=Sup3r-Secret! end") == f"pw={REDACTED} end"
    assert redactor.redact({"a": ["x Sup3r-Secret! y"]}) == {"a": [f"x {REDACTED} y"]}
    assert redactor.contains_secret("Sup3r-Secret!")


def test_shapes_are_redacted_without_registration() -> None:
    redactor = Redactor()
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ1c2VyIn0.abcdefghijk"
    text = f"Authorization: Bearer {jwt} key sk-ant-api03-ABCDEFGHIJKLMNOP ghp_abcdefghijklmnopqrstuvwx"
    redacted = redactor.redact_text(text)
    assert jwt not in redacted
    assert "sk-ant-api03" not in redacted
    assert "ghp_" not in redacted


def test_sensitive_keys_are_masked_but_error_codes_are_kept() -> None:
    redactor = Redactor()
    body = {
        "email": "a@b.c",
        "password": "whatever",
        "newPassword": "x1",
        "token": "t",
        "tokenType": "Bearer",
        "error": {"code": "INVALID_PASSWORD"},
    }
    out = redactor.redact(body)
    assert out["password"] == out["newPassword"] == out["token"] == REDACTED
    assert out["tokenType"] == "Bearer"
    assert out["error"]["code"] == "INVALID_PASSWORD"
    assert out["email"] == "a@b.c"
    headers = redactor.redact_headers({"Authorization": "Bearer abc", "Accept": "application/json"})
    assert headers == {"Authorization": REDACTED, "Accept": "application/json"}


def test_short_values_are_not_registered() -> None:
    redactor = Redactor(["1234"])
    assert redactor.known_secret_count == 0


def test_local_environment_is_allowed() -> None:
    assert check_environment(make_environment(), environ={}) == []


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"kind": "production"}, "never tests production"),
        ({"name": "prod"}, "looks like production"),
        (
            {"name": "eu", "kind": "staging", "backend": {"base_url": "https://api.live.example.com"}},
            "looks like production",
        ),
        ({"backend": {"base_url": "https://staging.example.com"}}, "not local"),
        ({"kind": "staging", "backend": {"base_url": "https://staging.example.com"}}, "--allow-remote-env"),
        ({"backend": {"base_url": "ftp://localhost"}}, "not an http"),
        ({"backend": {"base_url": "http://localhost:8080", "allowed_hosts": ["127.0.0.1"]}}, "allowed_hosts"),
    ],
)
def test_environment_refusals(overrides: dict[str, object], message: str) -> None:
    with pytest.raises(PolicyViolation, match=message):
        check_environment(make_environment(**overrides), environ={})


def test_staging_needs_an_explicit_opt_in_and_warns_about_reset() -> None:
    env = make_environment(
        kind="staging", backend={"base_url": "http://products-staging.example.com", "test_support": True}
    )
    warnings = check_environment(env, allow_remote=True, environ={})
    assert any("reset" in w for w in warnings)
    assert any("plain HTTP" in w for w in warnings)
    assert check_environment(env, environ={"MTF_ALLOW_REMOTE_ENV": "1"})


def test_private_compose_hosts_count_as_local() -> None:
    env = make_environment(kind="ci", backend={"base_url": "http://backend:8080"}, private_hosts=["backend"])
    assert check_environment(env, environ={}) == []
