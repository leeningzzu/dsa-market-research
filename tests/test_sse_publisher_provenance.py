"""SSE publisher-version boundary: source observation is not upstream vintage.

These fixtures are SYNTHETIC; they never establish real SSE terms or real Product.
The independent publisher-version requirement follows DSA's current P0/v1 contract.
"""
from __future__ import annotations

from src.services import evidence_traceability_registry as reg
from src.services.data_object_admission import (
    compile_b1_runtime_admission_receipts,
    compile_data_object_admission_view,
)
from tests.test_evidence_traceability_registry import (
    _build_sse_fixture_bundle,
    _build_sse_event_fixture_bundle,
    _sse_b1_context,
    native_summary,
)


def _admission(bundle: dict, kind: str):
    factor = native_summary()
    factor["asset_type"] = kind
    key = "stock_observations" if kind == "stock" else "etf_observations"
    factor["data_object_source_observations"] = bundle[key]
    obligations = reg._compile_accepted_data_object_scope(factor)
    b1 = _sse_b1_context()
    receipts = compile_b1_runtime_admission_receipts(obligations, b1_context=b1)
    return compile_data_object_admission_view(
        obligations, receipts, as_of=b1["research_as_of"],
        operation_id=b1["operation_id"], intended_use=b1["intended_use"],
    )


def test_sse_current_response_hash_is_not_a_publisher_revision_or_vintage():
    bundle = _build_sse_fixture_bundle()
    identity = bundle["stock_observations"]["P_CODE_IDENTITY"]
    assert identity["state"] == "READY"  # Source observation can be read.
    assert len(identity["source_content_sha256"]) == 64
    # A DSA response hash/observation clock cannot be silently reused as
    # official publisher revision, publication time, or historical vintage.
    assert identity["revision"] == "UNKNOWN"
    assert identity["vintage"] == "UNKNOWN"
    basket = bundle["etf_observations"]["E_BASKET"]
    assert basket["state"] == "READY"
    assert basket["revision"].startswith("SSE_PCF_FILE_ID:")
    assert basket["vintage"] == "UNKNOWN"


def test_current_sse_publisher_vintage_absent_never_admits_product():
    bundle = _build_sse_fixture_bundle()
    for kind, case in (("stock", "P_CODE_IDENTITY"), ("etf", "E_BASKET")):
        view = _admission(bundle, kind)
        row = next(r for r in view["objects"] if r["dataset_id"] == case)
        assert row["product_admitted"] is False
        assert row["historical_pit_eligible"] is False
        assert row["learning_admitted"] is False
        assert "UNITS_BASIS_REVISION_OR_VINTAGE_UNKNOWN" in row["reasons"]


def test_sse_dividend_and_share_event_dates_are_not_publisher_versions():
    bundle = _build_sse_event_fixture_bundle(reconciled=True)
    for case in ("P_SHARES_EVENTS", "P_DIVIDEND"):
        item = bundle["stock_observations"][case]
        assert item["state"] == "READY"  # Valid source facts stay inspectable.
        assert item["revision"] == "UNKNOWN"
        assert item["vintage"] == "UNKNOWN"
        assert len(item["source_content_sha256"]) == 64
    assert bundle["stock_observations"]["P_DIVIDEND"]["facts"]["record_count"] == 2
    view = _admission(bundle, "stock")
    for case in ("P_SHARES_EVENTS", "P_DIVIDEND"):
        row = next(r for r in view["objects"] if r["dataset_id"] == case)
        assert row["product_admitted"] is False
        assert row["historical_pit_eligible"] is False
        assert row["learning_admitted"] is False
        assert "UNITS_BASIS_REVISION_OR_VINTAGE_UNKNOWN" in row["reasons"]
