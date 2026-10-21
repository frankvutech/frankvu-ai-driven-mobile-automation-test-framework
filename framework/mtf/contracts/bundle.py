"""Loads the three contract files together."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from mtf.contracts.openapi import ApiContract
from mtf.contracts.test_data import TestDataRegistry
from mtf.contracts.test_ids import TestIdRegistry

TEST_IDS_FILE = "test-ids.yaml"
TEST_DATA_FILE = "test-data.yaml"
OPENAPI_FILE = "openapi/backend.yaml"
PLAN_SCHEMA_FILE = "test-plan.schema.json"


@dataclass(frozen=True)
class Contracts:
    root: Path
    test_ids: TestIdRegistry
    test_data: TestDataRegistry
    api: ApiContract

    @classmethod
    def load(cls, root: Path) -> Contracts:
        test_data = TestDataRegistry.load(root / TEST_DATA_FILE)
        test_ids = TestIdRegistry.load(root / TEST_IDS_FILE, reference_values=test_data.reference_values())
        return cls(root=root, test_ids=test_ids, test_data=test_data, api=ApiContract.load(root / OPENAPI_FILE))
