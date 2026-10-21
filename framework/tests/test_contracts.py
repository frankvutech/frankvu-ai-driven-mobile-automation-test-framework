from __future__ import annotations

from pathlib import Path

import pytest

from mtf.contracts.bundle import Contracts
from mtf.contracts.importer import import_test_ids, shadowed_ios_ids
from mtf.contracts.test_data import SecretProvider, TestDataRegistry
from mtf.contracts.test_ids import TestIdEntry, TestIdRegistry
from mtf.core.errors import ApiContractViolation, ConfigurationError, SelectorContractViolation
from mtf.core.types import Platform


def test_registry_resolves_exact_ids_and_patterns(contracts: Contracts) -> None:
    ids = contracts.test_ids
    assert ids.resolve("login_button") is not None
    resolution = ids.resolve("cart_item_product_001")
    assert resolution is not None
    assert resolution.params == {"product_id": "product_001"}
    assert ids.resolve("product_007_add_to_cart") is not None
    assert ids.resolve("list_item_030") is not None
    assert ids.resolve("scenario_dialogs") is not None


@pytest.mark.parametrize(
    "candidate",
    ["cart_item_product_999", "scenario_unknown", "list_item_30", "Login_Button", "login button", "random_id"],
)
def test_registry_rejects_unknown_ids(contracts: Contracts, candidate: str) -> None:
    assert contracts.test_ids.resolve(candidate) is None
    with pytest.raises(SelectorContractViolation):
        contracts.test_ids.require(candidate)


def test_platform_availability_is_enforced(contracts: Contracts) -> None:
    ids = contracts.test_ids
    ids.require("top_bar_back", Platform.ANDROID)
    with pytest.raises(SelectorContractViolation, match="not available on ios"):
        ids.require("top_bar_back", Platform.IOS)
    ids.require("controls_stepper", Platform.IOS)
    with pytest.raises(SelectorContractViolation):
        ids.require("controls_stepper", Platform.ANDROID)
    # Shadowed at run time by the screen root on iOS (found during the iOS verification run).
    with pytest.raises(SelectorContractViolation, match="overridden by the outer"):
        ids.require("catalog_list", Platform.IOS)
    # Compose dialog content is in a separate window without testTagsAsResourceId (verified on the emulator).
    ids.require("dialog_confirm", Platform.IOS)
    with pytest.raises(SelectorContractViolation, match="separate AlertDialog window"):
        ids.require("dialog_confirm", Platform.ANDROID)
    with pytest.raises(SelectorContractViolation, match="ModalBottomSheet"):
        ids.require("action_sheet_option_share", Platform.ANDROID)


def test_sensitive_and_secret_revealing_ids(contracts: Contracts) -> None:
    password = contracts.test_ids.resolve("login_password")
    toggle = contracts.test_ids.resolve("login_password_toggle")
    assert password is not None
    assert password.sensitive
    assert toggle is not None
    assert toggle.reveals_secret


def test_registry_rejects_bad_entries() -> None:
    entry = TestIdEntry(id="Bad-Id", screen="s", element="e", platforms=frozenset({Platform.IOS}))
    with pytest.raises(ConfigurationError):
        TestIdRegistry([entry])
    ok = TestIdEntry(id="ok_id", screen="s", element="e", platforms=frozenset({Platform.IOS}))
    with pytest.raises(ConfigurationError, match="duplicate"):
        TestIdRegistry([ok, ok])


def test_shadowed_identifier_detection() -> None:
    source = """
        List { }
        .accessibilityIdentifier(TestIDs.catalogList)
        .screenRoot(TestIDs.catalogScreen)
        Text("x").accessibilityIdentifier(TestIDs.cartTotal)
        .padding()
    """
    assert shadowed_ios_ids(source) == {"catalogList": "catalogScreen"}


def test_importer_reads_a_catalogue(tmp_path: Path) -> None:
    catalogue = tmp_path / "android/core/ui/src/main/kotlin/com/mobiletestplayground/core/ui/TestTags.kt"
    catalogue.parent.mkdir(parents=True)
    catalogue.write_text(
        '// --- Login ---\nconst val LOGIN_BUTTON = "login_button"\nconst val LOGIN_PASSWORD = "login_password"\n'
        'fun cartItem(productId: String) = "cart_item_$productId"\n',
        encoding="utf-8",
    )
    (tmp_path / "android/app").mkdir(parents=True)
    (tmp_path / "android/app/Screen.kt").write_text(
        "TestTags.LOGIN_BUTTON TestTags.LOGIN_PASSWORD TestTags.cartItem(", encoding="utf-8"
    )
    (tmp_path / "ios/MobileTestPlayground").mkdir(parents=True)
    (tmp_path / "ios/MobileTestPlayground/View.swift").write_text(
        "TestIDs.loginButton TestIDs.cartItem(", encoding="utf-8"
    )
    data = import_test_ids(tmp_path)
    assert data["ids"]["login_button"]["platforms"] == ["android", "ios"]
    assert data["ids"]["login_password"]["platforms"] == ["android"]
    assert data["ids"]["login_password"]["sensitive"] is True
    assert data["patterns"]["cart_item"]["template"] == "cart_item_{product_id}"


def test_api_contract_operations(contracts: Contracts) -> None:
    api = contracts.api
    op = api.operation("GET", "/api/v1/products/{id}")
    assert op.path_params == ("id",)
    assert op.render_path({"id": "product_001"}) == "/api/v1/products/product_001"
    assert api.operation("POST", "/api/v1/test/reset").is_test_support
    assert not op.is_test_support
    assert api.operation("GET", "/api/v1/cart").requires_auth
    with pytest.raises(ApiContractViolation):
        api.operation("GET", "/api/v1/does-not-exist")
    with pytest.raises(ApiContractViolation):
        op.render_path({})


def test_api_contract_response_validation(contracts: Contracts) -> None:
    api = contracts.api
    op = api.operation("GET", "/api/v1/products/{id}")
    good = {"id": "p", "name": "n", "description": "d", "category": "c", "priceCents": 1, "stock": 1}
    assert api.validate_response(op, 200, good) == []
    assert api.validate_response(op, 200, {"id": "p"})  # missing required fields
    assert "not declared" in api.validate_response(op, 418, {})[0]


def test_test_data_never_stores_passwords() -> None:
    with pytest.raises(ConfigurationError, match="password_env"):
        TestDataRegistry({"version": 1, "accounts": {"a": {"email": "a@b.c", "password": "x"}}})


def test_secret_provider(contracts: Contracts) -> None:
    secrets = SecretProvider(contracts.test_data, {"MTF_PASSWORD_CUSTOMER": "s3cret-value"})
    assert secrets.resolve("accounts.customer.password") == "s3cret-value"
    with pytest.raises(ConfigurationError, match="MTF_PASSWORD_ADMIN"):
        secrets.resolve("accounts.admin.password")
    assert secrets.missing(["accounts.customer.password", "accounts.admin.password"]) == ["MTF_PASSWORD_ADMIN"]
    assert contracts.test_data.lookup("products.product_001.price_display") == "7.49"
