from __future__ import annotations

from copy import deepcopy

import pytest

from data_provider.daily_data_identity import sha256_payload

from src.services.data_object_admission import (
    DataObjectAdmissionError,
    compile_data_object_admission,
    compile_data_object_admission_view,
    seal_data_object_admission_receipt,
    seal_source_rights_evidence,
    validate_compiled_data_object_admission,
)


OBLIGATION = {
    "dataset_id": "P_DAILY",
    "domain": "STOCK",
    "fact_or_fields": "股票日线完成bar",
    "native_frequency": "daily/close",
    "product_consumer": "STOCK_ASSET_RESEARCH",
    "state": "UNKNOWN",
    "provider_identity": "synthetic-lawful-provider",
    "provider_route": "synthetic.completed_daily",
    "source_identity_hash": "a" * 64,
    "source_content_sha256": "b" * 64,
    "source_observed_at": "2026-10-21T10:00:00+00:00",
    "source_available_at": "2026-10-21T09:00:00+00:00",
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
}

NO_SEND_OPERATION_ID = "op-stock-etf-nosend-20261021-r001"
LEARNING_OPERATION_ID = "op-stock-etf-learning-20261021-r001"
NO_SEND_CONTEXT = {
    "operation_id": NO_SEND_OPERATION_ID,
    "intended_use": "NO_SEND_RESEARCH",
}
LEARNING_CONTEXT = {
    "operation_id": LEARNING_OPERATION_ID,
    "intended_use": "LEARNING_RESEARCH",
}


def _rights_evidence(*, dataset_id="P_DAILY", provider_identity="synthetic-lawful-provider", consumer_scope="STOCK_ASSET_RESEARCH", rights=None, upstream_rights_state="VERIFIED", intended_use="NO_SEND_RESEARCH", operation_id=NO_SEND_OPERATION_ID, terms_observed_at="2026-10-21T08:00:00+00:00"):
    rights = rights or {
        "product_use": "ALLOWED",
        "storage": "FORBIDDEN",
        "training": "FORBIDDEN",
        "redistribution": "FORBIDDEN",
    }
    return seal_source_rights_evidence({
        "schema_version": "source-rights-evidence-v1",
        "operation_id": operation_id,
        "intended_use": intended_use,
        "provider_identity": provider_identity,
        "upstream_source_owner": "synthetic-licensed-upstream",
        "upstream_rights_state": upstream_rights_state,
        "dataset_id": dataset_id,
        "consumer_scope": consumer_scope,
        "terms_uri": "https://example.invalid/terms/synthetic",
        "terms_sha256": "c" * 64,
        "terms_observed_at": terms_observed_at,
        "rights": rights,
    })


def _receipt(*, include_rights_evidence=True, rights_operation_id=NO_SEND_OPERATION_ID, rights_intended_use="NO_SEND_RESEARCH", **overrides):
    rights = overrides.pop("rights", {
        "product_use": "ALLOWED",
        "storage": "FORBIDDEN",
        "training": "FORBIDDEN",
        "redistribution": "FORBIDDEN",
    })
    dataset_id = overrides.get("dataset_id", "P_DAILY")
    provider_identity = overrides.get("provider_identity", "synthetic-lawful-provider")
    consumer_scope = overrides.get("consumer_scope", "STOCK_ASSET_RESEARCH")
    payload = {
        "schema_version": "data-object-admission-receipt-v1",
        "dataset_id": dataset_id,
        "provider_identity": provider_identity,
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
        "rights": rights,
        "historical_pit_eligible": False,
        "consumer_scope": consumer_scope,
    }
    payload.update(overrides)
    if include_rights_evidence:
        payload["rights_evidence"] = _rights_evidence(
            dataset_id=payload["dataset_id"],
            provider_identity=payload["provider_identity"],
            consumer_scope=payload["consumer_scope"],
            rights=payload["rights"],
            operation_id=rights_operation_id,
            intended_use=rights_intended_use,
        )
    return seal_data_object_admission_receipt(payload)


def test_product_admission_requires_explicit_identity_semantics_and_rights():
    row = compile_data_object_admission(
        OBLIGATION,
        _receipt(),
        as_of="2026-10-21T10:30:00+00:00",
        **NO_SEND_CONTEXT,
    )
    assert row["admission_state"] == "PRODUCT_ADMITTED"
    assert row["product_admitted"] is True
    assert row["storage_admitted"] is False
    assert row["learning_admitted"] is False
    assert row["strategy_upgrade_authority"] is False
    assert row["independent_action_authority"] is False
    assert len(row["admission_hash"]) == 64
    assert validate_compiled_data_object_admission(row) == row
    assert row["rights_evidence_verified"] is True
    assert row["runtime_source_bound"] is True
    assert row["runtime_source_identity_hash"] == "a" * 64
    assert row["runtime_source_content_sha256"] == "b" * 64
    assert row["intended_use"] == "NO_SEND_RESEARCH"
    assert row["operation_id"] == "op-stock-etf-nosend-20261021-r001"


@pytest.mark.parametrize(
    ("field", "value", "reason"),
    (
        ("provider_route", "other.route", "RUNTIME_SOURCE_PROVIDER_ROUTE_MISMATCH"),
        ("source_available_at", "2026-10-21T08:59:00+00:00", "RUNTIME_SOURCE_AVAILABLE_AT_MISMATCH"),
        ("units", {"price": "CNY_PER_SHARE", "volume": "LOT", "amount": "CNY"}, "RUNTIME_SOURCE_UNITS_MISMATCH"),
        ("currency", "USD", "RUNTIME_SOURCE_CURRENCY_MISMATCH"),
        ("adjustment_basis", "HFQ", "RUNTIME_SOURCE_ADJUSTMENT_BASIS_MISMATCH"),
        ("revision", "OTHER_REVISION", "RUNTIME_SOURCE_REVISION_MISMATCH"),
        ("vintage", "2026Q4_OTHER_RELEASE", "RUNTIME_SOURCE_VINTAGE_MISMATCH"),
    ),
)
def test_product_admission_binds_complete_runtime_semantics(field, value, reason):
    obligation = deepcopy(OBLIGATION)
    obligation[field] = value
    row = compile_data_object_admission(
        obligation, _receipt(), **NO_SEND_CONTEXT,
    )
    assert row["product_admitted"] is False
    assert row["runtime_source_bound"] is False
    assert reason in row["reasons"]


def test_receipt_cannot_fill_missing_runtime_revision_or_vintage():
    for field in ("revision", "vintage"):
        obligation = deepcopy(OBLIGATION)
        obligation.pop(field)
        row = compile_data_object_admission(
            obligation, _receipt(), **NO_SEND_CONTEXT,
        )
        assert row["product_admitted"] is False
        assert "RUNTIME_SOURCE_SEMANTICS_MISSING" in row["reasons"]


def test_allowed_claim_without_operation_specific_rights_evidence_is_identity_only():
    row = compile_data_object_admission(
        OBLIGATION,
        _receipt(include_rights_evidence=False),
        **NO_SEND_CONTEXT,
    )
    assert row["admission_state"] == "SOURCE_IDENTITY_ONLY"
    assert row["product_admitted"] is False
    assert row["rights_evidence_verified"] is False
    assert "PRODUCT_RIGHTS_EVIDENCE_MISSING" in row["reasons"]


def test_operation_context_is_required_even_with_sealed_rights_evidence():
    row = compile_data_object_admission(OBLIGATION, _receipt())
    assert row["product_admitted"] is False
    assert "RIGHTS_EVIDENCE_OPERATION_CONTEXT_MISSING_OR_INVALID" in row["reasons"]


def test_receipt_must_match_the_current_runtime_source_bytes():
    wrong_identity = {
        **OBLIGATION,
        "source_identity_hash": "d" * 64,
    }
    result = compile_data_object_admission(
        wrong_identity, _receipt(), **NO_SEND_CONTEXT,
    )
    assert result["product_admitted"] is False
    assert result["runtime_source_bound"] is False
    assert "RUNTIME_SOURCE_IDENTITY_MISMATCH" in result["reasons"]

    wrong_content = {
        **OBLIGATION,
        "source_content_sha256": "e" * 64,
    }
    result = compile_data_object_admission(
        wrong_content, _receipt(), **NO_SEND_CONTEXT,
    )
    assert result["product_admitted"] is False
    assert "RUNTIME_SOURCE_CONTENT_MISMATCH" in result["reasons"]

    missing_content = dict(OBLIGATION)
    missing_content.pop("source_content_sha256")
    result = compile_data_object_admission(
        missing_content, _receipt(), **NO_SEND_CONTEXT,
    )
    assert result["product_admitted"] is False
    assert "RUNTIME_SOURCE_IDENTITY_OR_CONTENT_MISSING" in result["reasons"]


def test_unknown_or_mismatched_upstream_rights_evidence_cannot_admit_product():
    unknown = _receipt()
    unknown["rights_evidence"] = _rights_evidence(upstream_rights_state="UNKNOWN")
    unknown = seal_data_object_admission_receipt(unknown)
    row = compile_data_object_admission(OBLIGATION, unknown)
    assert row["product_admitted"] is False
    assert "UPSTREAM_RIGHTS_NOT_VERIFIED" in row["reasons"]

    mismatch = _receipt()
    mismatch["rights_evidence"] = _rights_evidence(provider_identity="other-provider")
    mismatch = seal_data_object_admission_receipt(mismatch)
    row = compile_data_object_admission(OBLIGATION, mismatch)
    assert row["product_admitted"] is False
    assert "RIGHTS_EVIDENCE_PROVIDER_MISMATCH" in row["reasons"]


def test_no_send_scope_rejects_redistribution_and_late_terms_evidence():
    rights = {
        "product_use": "ALLOWED", "storage": "FORBIDDEN",
        "training": "FORBIDDEN", "redistribution": "ALLOWED",
    }
    contradiction = _receipt(rights=rights)
    row = compile_data_object_admission(OBLIGATION, contradiction)
    assert row["product_admitted"] is False
    assert "RIGHTS_EVIDENCE_NO_SEND_SCOPE_CONTRADICTION" in row["reasons"]

    late = _receipt()
    late["rights_evidence"] = _rights_evidence(
        terms_observed_at="2026-10-21T11:00:00+00:00"
    )
    late = seal_data_object_admission_receipt(late)
    row = compile_data_object_admission(OBLIGATION, late)
    assert row["product_admitted"] is False
    assert "RIGHTS_EVIDENCE_AFTER_SOURCE_OBSERVATION" in row["reasons"]


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


def test_learning_requires_distinct_operation_purpose_plus_storage_training_and_pit():
    rights = {
        "product_use": "ALLOWED",
        "storage": "ALLOWED",
        "training": "ALLOWED",
        "redistribution": "FORBIDDEN",
    }
    no_send_payload = _receipt(rights=rights, historical_pit_eligible=True)
    no_send = compile_data_object_admission(
        OBLIGATION, no_send_payload, **NO_SEND_CONTEXT,
    )
    assert no_send["admission_state"] == "PRODUCT_ADMITTED"
    assert no_send["product_admitted"] is True
    assert no_send["storage_admitted"] is True
    assert no_send["learning_admitted"] is False

    learning_payload = _receipt(
        rights=rights,
        historical_pit_eligible=True,
        rights_operation_id=LEARNING_OPERATION_ID,
        rights_intended_use="LEARNING_RESEARCH",
    )
    learning = compile_data_object_admission(
        OBLIGATION, learning_payload, **LEARNING_CONTEXT,
    )
    assert learning["admission_state"] == "LEARNING_ADMITTED"
    assert learning["product_admitted"] is True
    assert learning["storage_admitted"] is True
    assert learning["learning_admitted"] is True
    assert learning["redistribution_admitted"] is False


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
    row = compile_data_object_admission(
        OBLIGATION, _receipt(), **NO_SEND_CONTEXT,
    )
    forged = deepcopy(row)
    forged["product_admitted"] = False
    with pytest.raises(DataObjectAdmissionError, match="COMPILED_ADMISSION_HASH_MISMATCH"):
        validate_compiled_data_object_admission(forged)


def test_compiled_admission_resealed_runtime_semantic_tamper_is_rejected():
    row = compile_data_object_admission(
        OBLIGATION, _receipt(), **NO_SEND_CONTEXT,
    )
    forged = deepcopy(row)
    forged["runtime_revision"] = "RESEALED_OTHER_REVISION"
    forged.pop("admission_hash")
    forged["admission_hash"] = sha256_payload(forged)
    with pytest.raises(
        DataObjectAdmissionError,
        match="COMPILED_ADMISSION_RUNTIME_SEMANTICS_MISMATCH",
    ):
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
        **NO_SEND_CONTEXT,
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
        **NO_SEND_CONTEXT,
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
    runtime_obligation = {
        **OBLIGATION,
        "source_available_at": "2026-10-20T08:00:00+00:00",
        "source_observed_at": "2026-10-21T10:00:00+00:00",
        "vintage": "2026-10-20T08:00:00+00:00",
    }
    row = compile_data_object_admission(
        runtime_obligation, receipt, as_of="2026-10-21T09:30:00+00:00",
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
    runtime_obligation = {
        **OBLIGATION,
        "source_observed_at": "2026-10-21T09:00:00+00:00",
        "source_available_at": "2026-10-21T08:00:00+00:00",
        "vintage": "2026-10-22T08:00:00+00:00",
    }
    row = compile_data_object_admission(
        runtime_obligation, receipt, as_of="2026-10-21T10:00:00+00:00",
    )
    assert row["product_admitted"] is False
    assert "VINTAGE_AFTER_RESEARCH_ASOF" in row["reasons"]

def test_source_compiler_date_only_vintage_after_cutoff_is_not_product_ready():
    receipt = _receipt(vintage="2026-10-22")
    runtime_obligation = {**OBLIGATION, "vintage": "2026-10-22"}
    row = compile_data_object_admission(
        runtime_obligation, receipt, as_of="2026-10-21T10:30:00+00:00",
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
    runtime_obligation = {
        **OBLIGATION,
        "vintage": "2026Q3_ORIGINAL_RELEASE",
    }
    result = compile_data_object_admission(
        runtime_obligation, receipt, as_of="2026-10-21T10:30:00+00:00",
        **NO_SEND_CONTEXT,
    )
    assert result["product_admitted"] is True
    assert result["admission_state"] == "PRODUCT_ADMITTED"
    assert result["learning_admitted"] is False


def test_future_completed_period_cannot_be_product_admitted():
    runtime_obligation = {
        **OBLIGATION,
        "completed_through": "2026-10-22",
    }
    result = compile_data_object_admission(
        runtime_obligation,
        _receipt(completed_through="2026-10-22"),
        as_of="2026-10-21T10:30:00+00:00",
    )
    assert result["product_admitted"] is False
    assert "COMPLETED_THROUGH_AFTER_RESEARCH_ASOF" in result["reasons"]


def test_same_day_completed_period_remains_valid_nontrigger():
    runtime_obligation = {
        **OBLIGATION,
        "completed_through": "2026-10-21",
    }
    result = compile_data_object_admission(
        runtime_obligation,
        _receipt(completed_through="2026-10-21"),
        as_of="2026-10-21T10:30:00+00:00",
        **NO_SEND_CONTEXT,
    )
    assert result["product_admitted"] is True
    assert result["learning_admitted"] is False


def test_operation_specific_rights_are_bound_to_the_current_research_run():
    matching = compile_data_object_admission(
        OBLIGATION,
        _receipt(),
        as_of="2026-10-21T10:30:00+00:00",
        **NO_SEND_CONTEXT,
    )
    assert matching["product_admitted"] is True

    mismatched = compile_data_object_admission(
        OBLIGATION,
        _receipt(),
        as_of="2026-10-21T10:30:00+00:00",
        operation_id="other-research-run",
        intended_use="NO_SEND_RESEARCH",
    )
    assert mismatched["product_admitted"] is False
    assert "RIGHTS_EVIDENCE_OPERATION_MISMATCH" in mismatched["reasons"]

    view = compile_data_object_admission_view(
        [OBLIGATION],
        {"P_DAILY": _receipt()},
        as_of="2026-10-21T10:30:00+00:00",
        operation_id="other-research-run",
        intended_use="NO_SEND_RESEARCH",
    )
    assert view["product_admitted_count"] == 0
    assert "RIGHTS_EVIDENCE_OPERATION_MISMATCH" in view["objects"][0]["reasons"]


def test_rights_terms_observed_after_research_asof_are_not_product_ready():
    receipt = _receipt()
    receipt["rights_evidence"] = _rights_evidence(
        terms_observed_at="2026-10-21T10:15:00+00:00"
    )
    receipt = seal_data_object_admission_receipt(receipt)
    result = compile_data_object_admission(
        OBLIGATION,
        receipt,
        as_of="2026-10-21T10:00:00+00:00",
        **NO_SEND_CONTEXT,
    )
    assert result["product_admitted"] is False
    assert "RIGHTS_EVIDENCE_AFTER_RESEARCH_ASOF" in result["reasons"]
