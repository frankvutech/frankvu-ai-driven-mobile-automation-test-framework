from __future__ import annotations

from pathlib import Path

import pytest

from mtf.contracts.bundle import Contracts
from mtf.core.catalog import Catalog
from mtf.core.config import Environment
from mtf.screenplay.registry import build_catalog

from .fakes import PASSWORDS, FakeBackend, World

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="session")
def root() -> Path:
    return ROOT


@pytest.fixture(scope="session")
def contracts() -> Contracts:
    return Contracts.load(ROOT / "contracts")


@pytest.fixture(scope="session")
def catalog() -> Catalog:
    return build_catalog()


@pytest.fixture
def environ() -> dict[str, str]:
    return dict(PASSWORDS)


@pytest.fixture
def world() -> World:
    return World()


@pytest.fixture
def backend(world: World) -> FakeBackend:
    return FakeBackend(world)


def make_environment(**overrides: object) -> Environment:
    data: dict[str, object] = {
        "name": "unit",
        "kind": "local",
        "backend": {"base_url": "http://localhost:8080", "test_support": True},
        "timeouts": {"default_step_ms": 2000, "run_s": 60, "runner_start_s": 10},
    }
    data.update(overrides)
    return Environment.model_validate(data)


@pytest.fixture
def environment() -> Environment:
    return make_environment()
