from __future__ import annotations

from copy import deepcopy

import pytest

from src.services.data_object_admission import (
    DataObjectAdmissionError,
    build_operation_specific_data_admission_receipt,
    compile_data_object_admission,
    compile_data_object_admission_view,
    seal_data_object_admission_receipt,
    validate_compiled_data_object_admission,
)
from src.services.provider_operation_rights import (
    TEST_PURPOSE,
    TEST_SCOPE,
    compile_provider_operation_rights_evidence,
)


OBLIGATION = {
    "dataset_id": "P_DAILY",
    "domain": "STOCK",
    "fact_or_fields": "股票日线完成bar",
    "native_frequency": "daily/close",
    "product_consumer": "STOCK_ASSET_RESEARCH",
    "state": "UNKNOWN",
}


def _receipt(**overrides):
    payload = {
        "schema_version": "data-object-admission-receipt-v1",
        "dataset_id": "P_DAILY",
        "provider_identity": "synthetic-lawful-provider",
        "provider_route": "synthetic.completed_daily",
        "native_frequency": "daily/close",
        "source_identity_hash": "a" * 64,
        "content_sha256": "b" * 64,
        "observed_at": "2026-10-21T10:00:00+00:00",
        "available_at": "2026-10-21T09:00:00+00:00",
        "completed_through": "2026-10-20",
        "units": {
            "price": "CNY_PER_SHARE",
            "volume": "SHARE",
            "amount": "CNY",
        },
        "currency": "CNY",
        "adjustment_basis": "QFQ",
        "revision": "ORIGINAL_OBSERVATION",
        "vintage": "2026-10-21T09:00:00+00:00",
        "rights": {
            "product_use": "ALLOWED",
            "storage": "FORBIDDEN",
            "training": "FORBIDDEN",
            "redistribution": "FORBIDDEN",
        },
        "historical_pit_eligible": False,
        "consumer_scope": "STOCK_ASSET_RESEARCH",
    }
    payload.update(overrides)
    payload.setdefault(
        "rights_evidence",
        compile_provider_operation_rights_evidence(
            provider_identity=payload["provider_identity"],
            provider_route=payload["provider_route"],
            operation_purpose=TEST_PURPOSE,
            delivery_scope=TEST_SCOPE,
            requested_actions={
                key: payload["rights"][key] == "ALLOWED"
                for key in ("product_use", "storage", "training", "redistribution")
            },
            commercial_use=False,
            observed_at=payload["observed_at"],
        ),
    )
    return seal_data_object_admission_receipt(payload)


def test_product_admission_requires_explicit_identity_semantics_and_rights():
    row = compile_data_object_admission(
        OBLIGATION,
        _receipt(),
        as_of="2026-10-21T10:30:00+00:00",
    )
    assert row["admission_state"] == "PRODUCT_ADMITTED"
    assert row["product_admitted"] is True
    assert row["storage_admitted"] is False
    assert row["learning_admitted"] is False
    assert row["strategy_upgrade_authority"] is False
    assert row["independent_action_authority"] is False
    assert len(row["admission_hash"]) == 64
    assert validate_compiled_data_object_admission(row) == row
    assert len(row["rights_evidence_hash"]) == 64
    assert row["operation_purpose"] == TEST_PURPOSE
    assert row["delivery_scope"] == TEST_SCOPE


def test_allowed_rights_without_operation_evidence_are_rejected():
    receipt = _receipt()
    receipt.pop("receipt_hash")
    receipt.pop("rights_evidence")
    receipt = seal_data_object_admission_receipt(receipt)
    with pytest.raises(DataObjectAdmissionError, match="RIGHTS_EVIDENCE_MISSING"):
        compile_data_object_admission(OBLIGATION, receipt)


def test_operation_specific_builder_binds_source_and_rights_evidence():
    evidence = compile_provider_operation_rights_evidence(
        provider_identity="synthetic-lawful-provider",
        provider_route="synthetic.completed_daily",
        operation_purpose=TEST_PURPOSE,
        delivery_scope=TEST_SCOPE,
        requested_actions={
            "product_use": True,
            "storage": False,
            "training": False,
            "redistribution": False,
        },
        commercial_use=False,
        observed_at="2026-10-21T10:00:00+00:00",
    )
    receipt = build_operation_specific_data_admission_receipt(
        OBLIGATION,
        {
            "provider_identity": "synthetic-lawful-provider",
            "provider_route": "synthetic.completed_daily",
            "identity_hash": "a" * 64,
            "content_sha256": "b" * 64,
            "observed_at": "2026-10-21T10:00:00+00:00",
            "returned_end": "2026-10-20",
            "observed_adjustment_basis": "qfq",
            "currency": "CNY",
        },
        evidence,
        units={"price": "CNY_PER_SHARE", "volume": "SHARE", "amount": "CNY"},
        available_at="2026-10-21T09:00:00+00:00",
        revision="ORIGINAL_OBSERVATION",
        vintage="2026-10-21T09:00:00+00:00",
    )
    row = compile_data_object_admission(OBLIGATION, receipt)
    assert row["product_admitted"] is True
    assert row["storage_admitted"] is False
    assert row["operation_purpose"] == TEST_PURPOSE




def test_unknown_product_rights_and_unknown_units_remain_identity_only():
    rights = _receipt(
        rights={
            "product_use": "UNKNOWN",
            "storage": "UNKNOWN",
            "training": "UNKNOWN",
            "redistribution": "UNKNOWN",
        },
        units={"price": "UNKNOWN"},
    )
    row = compile_data_object_admission(OBLIGATION, rights)
    assert row["admission_state"] == "SOURCE_IDENTITY_ONLY"
    assert row["source_identity_verified"] is True
    assert row["semantic_identity_complete"] is False
    assert row["product_admitted"] is False
    assert "PRODUCT_USE_RIGHT_NOT_ALLOWED" in row["reasons"]
    assert "UNITS_BASIS_REVISION_OR_VINTAGE_UNKNOWN" in row["reasons"]


def test_learning_requires_storage_training_and_historical_pit_separately():
    payload = _receipt(
        rights={
            "product_use": "ALLOWED",
            "storage": "ALLOWED",
            "training": "ALLOWED",
            "redistribution": "FORBIDDEN",
        },
        historical_pit_eligible=True,
    )
    row = compile_data_object_admission(OBLIGATION, payload)
    assert row["admission_state"] == "LEARNING_ADMITTED"
    assert row["product_admitted"] is True
    assert row["storage_admitted"] is True
    assert row["learning_admitted"] is True
    assert row["redistribution_admitted"] is False


def test_future_available_at_and_mismatched_object_fail_closed():
    future = compile_data_object_admission(
        OBLIGATION,
        _receipt(
            observed_at="2026-10-21T12:00:00+00:00",
            available_at="2026-10-21T11:00:00+00:00",
        ),
        as_of="2026-10-21T10:00:00+00:00",
    )
    assert future["admission_state"] == "NOT_OBSERVED"
    assert "AVAILABLE_AT_AFTER_RESEARCH_ASOF" in future["reasons"]

    mismatch = compile_data_object_admission(
        OBLIGATION,
        _receipt(dataset_id="P_5M"),
    )
    assert mismatch["admission_state"] == "NOT_OBSERVED"
    assert "DATASET_ID_MISMATCH" in mismatch["reasons"]


def test_tamper_or_orphan_receipt_is_rejected():
    forged = _receipt()
    forged["units"]["price"] = "USD_PER_SHARE"
    with pytest.raises(DataObjectAdmissionError, match="HASH_MISMATCH"):
        compile_data_object_admission(OBLIGATION, forged)

    orphan = _receipt(dataset_id="ORPHAN")
    with pytest.raises(DataObjectAdmissionError, match="ORPHAN_RECEIPT"):
        compile_data_object_admission_view([OBLIGATION], {"ORPHAN": orphan})


def test_compiled_admission_tamper_is_detected():
    row = compile_data_object_admission(OBLIGATION, _receipt())
    forged = deepcopy(row)
    forged["product_admitted"] = False
    with pytest.raises(DataObjectAdmissionError, match="COMPILED_ADMISSION_HASH_MISMATCH"):
        validate_compiled_data_object_admission(forged)


def test_complete_view_keeps_missing_and_not_applicable_distinct():
    other = {
        **OBLIGATION,
        "dataset_id": "E_NAV_IOPV",
        "domain": "ETF",
        "product_consumer": "ETF_ASSET_RESEARCH",
        "state": "NOT_APPLICABLE",
    }
    view = compile_data_object_admission_view(
        [OBLIGATION, other],
        {"P_DAILY": _receipt()},
    )
    assert view["product_admitted_count"] == 1
    assert view["learning_admitted_count"] == 0
    assert view["counts"]["PRODUCT_ADMITTED"] == 1
    assert view["counts"]["NOT_APPLICABLE"] == 1
    assert view["policy"]["product_rights_do_not_imply_storage_or_training"] is True
    assert len(view["view_hash"]) == 64

def test_compiled_future_observation_cannot_be_reused_at_earlier_research_asof():
    """Admitted for a later run must not become earlier PIT/known-at evidence."""
    row = compile_data_object_admission(
        OBLIGATION, _receipt(),
        as_of="2026-10-21T10:30:00+00:00",
    )
    assert row["product_admitted"] is True
    # Publication at 09:00 precedes this research time, but this exact
    # observation was not acquired until 10:00.
    with pytest.raises(DataObjectAdmissionError, match="OUTSIDE_RESEARCH_ASOF"):
        validate_compiled_data_object_admission(
            row, as_of="2026-10-21T09:30:00+00:00",
        )

def test_source_compiler_rejects_not_yet_observed_even_if_published_earlier():
    receipt = _receipt(
        available_at="2026-10-20T08:00:00+00:00",
        observed_at="2026-10-21T10:00:00+00:00",
        vintage="2026-10-20T08:00:00+00:00",
    )
    row = compile_data_object_admission(
        OBLIGATION, receipt, as_of="2026-10-21T09:30:00+00:00",
    )
    assert row["product_admitted"] is False
    assert row["admission_state"] == "NOT_OBSERVED"
    assert "OBSERVED_AT_AFTER_RESEARCH_ASOF" in row["reasons"]


def test_source_compiler_rejects_later_vintage_despite_early_observation():
    receipt = _receipt(
        observed_at="2026-10-21T09:00:00+00:00",
        available_at="2026-10-21T08:00:00+00:00",
        vintage="2026-10-22T08:00:00+00:00",
    )
    row = compile_data_object_admission(
        OBLIGATION, receipt, as_of="2026-10-21T10:00:00+00:00",
    )
    assert row["product_admitted"] is False
    assert "VINTAGE_AFTER_RESEARCH_ASOF" in row["reasons"]

def test_source_compiler_date_only_vintage_after_cutoff_is_not_product_ready():
    receipt = _receipt(vintage="2026-10-22")
    row = compile_data_object_admission(
        OBLIGATION, receipt, as_of="2026-10-21T10:30:00+00:00",
    )
    assert row["product_admitted"] is False
    assert "VINTAGE_AFTER_RESEARCH_ASOF" in row["reasons"]


def test_source_compiler_naive_timestamp_vintage_cannot_bypass_cutoff():
    receipt = _receipt(vintage="2026-10-22T08:00:00")
    with pytest.raises(DataObjectAdmissionError, match="TIMESTAMP_NAIVE"):
        compile_data_object_admission(
            OBLIGATION, receipt, as_of="2026-10-21T10:30:00+00:00",
        )

def test_known_at_vintage_guard_preserves_opaque_version_label_nontrigger():
    """A source-backed opaque version label must not be guessed as a future clock."""
    receipt = _receipt(vintage="2026Q3_ORIGINAL_RELEASE")
    result = compile_data_object_admission(
        OBLIGATION, receipt, as_of="2026-10-21T10:30:00+00:00",
    )
    assert result["product_admitted"] is True
    assert result["admission_state"] == "PRODUCT_ADMITTED"
    assert result["learning_admitted"] is False


def test_future_completed_period_cannot_be_product_admitted():
    result = compile_data_object_admission(
        OBLIGATION,
        _receipt(completed_through="2026-10-22"),
        as_of="2026-10-21T10:30:00+00:00",
    )
    assert result["product_admitted"] is False
    assert "COMPLETED_THROUGH_AFTER_RESEARCH_ASOF" in result["reasons"]


def test_same_day_completed_period_remains_valid_nontrigger():
    result = compile_data_object_admission(
        OBLIGATION,
        _receipt(completed_through="2026-10-21"),
        as_of="2026-10-21T10:30:00+00:00",
    )
    assert result["product_admitted"] is True
    assert result["learning_admitted"] is False
