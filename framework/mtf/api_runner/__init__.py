"""API automation against the backend REST API, validated by the OpenAPI contract.

Runs without a simulator or emulator. Test-support endpoints (reset, deterministic errors) are
separated from product endpoints by the contract's `Test support` tag.
"""
