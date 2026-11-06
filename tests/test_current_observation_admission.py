"""Independent P0 contract: current observations never manufacture historical PIT."""
from copy import deepcopy

import pytest

from src.services.data_object_admission import (
    DataObjectAdmissionError, compile_data_object_admission,
    compile_b1_runtime_admission_receipts, seal_data_object_admission_receipt,
    validate_compiled_data_object_admission,
)
from src.services.pit_identity import sha256_payload
from tests.test_data_object_admission import (
    OBLIGATION, NO_SEND_CONTEXT, _receipt, _rights_evidence,
)

ASOF = "2026-10-21T10:30:00+00:00"


def current_pair(**overrides):
    obligation = {**deepcopy(OBLIGATION), "revision": "UNKNOWN", "vintage": "UNKNOWN"}
    receipt = dict(_receipt(revision="UNKNOWN", vintage="UNKNOWN"))
    receipt.update({
        "schema_version": "data-object-admission-receipt-v2",
        "temporal_scope": "CURRENT_OBSERVATION",
        "research_as_of": ASOF,
        "observation_operation_id": NO_SEND_CONTEXT["operation_id"],
    })
    receipt.update(overrides)
    receipt = seal_data_object_admission_receipt(receipt)
    return obligation, receipt


def test_current_observation_with_unknown_upstream_vintage_is_not_forced_to_fake_pit():
    obligation, receipt = current_pair()
    row = compile_data_object_admission(obligation, receipt, as_of=ASOF, **NO_SEND_CONTEXT)
    assert row["product_admitted"] is True
    assert row["revision"] == row["vintage"] == "UNKNOWN"
    assert row["temporal_scope"] == "CURRENT_OBSERVATION"
    assert row["historical_pit_eligible"] is False
    assert row["learning_admitted"] is False
    assert row["storage_admitted"] is False
    assert row["strategy_upgrade_authority"] is False
    assert validate_compiled_data_object_admission(row, as_of=ASOF) == row


def test_legacy_unknown_vintage_remains_blocked_without_explicit_current_scope():
    obligation = {**deepcopy(OBLIGATION), "revision": "UNKNOWN", "vintage": "UNKNOWN"}
    row = compile_data_object_admission(
        obligation, _receipt(revision="UNKNOWN", vintage="UNKNOWN"),
        as_of=ASOF, **NO_SEND_CONTEXT,
    )
    assert not row["product_admitted"]
    assert "UNITS_BASIS_REVISION_OR_VINTAGE_UNKNOWN" in row["reasons"]


@pytest.mark.parametrize("field,value", [
    ("currency", "UNKNOWN"), ("adjustment_basis", "UNKNOWN"),
    ("units", {"price": "UNKNOWN"}),
    ("rights", {"product_use": "UNKNOWN", "storage": "FORBIDDEN",
                "training": "FORBIDDEN", "redistribution": "FORBIDDEN"}),
])
def test_current_scope_never_overrides_unknown_rights_units_or_basis(field, value):
    obligation, receipt = current_pair()
    receipt[field] = value
    if field == "rights":
        receipt["rights_evidence"] = _rights_evidence(rights=value)
    else:
        obligation[field] = value
    receipt = seal_data_object_admission_receipt(receipt)
    row = compile_data_object_admission(obligation, receipt, as_of=ASOF, **NO_SEND_CONTEXT)
    assert row["product_admitted"] is False


@pytest.mark.parametrize("asof", ["2026-10-21T09:30:00+00:00", "2026-10-22T10:30:00+00:00"])
def test_current_snapshot_cannot_be_replayed_under_a_different_research_clock(asof):
    obligation, receipt = current_pair()
    row = compile_data_object_admission(obligation, receipt, as_of=asof, **NO_SEND_CONTEXT)
    assert row["product_admitted"] is False
    assert "CURRENT_OBSERVATION_CONTEXT_MISMATCH" in row["reasons"]


def test_current_snapshot_rejects_historical_pit_or_learning_promotion():
    with pytest.raises(DataObjectAdmissionError, match="CURRENT_OBSERVATION_PIT_FORBIDDEN"):
        current_pair(historical_pit_eligible=True)
    obligation, receipt = current_pair()
    row = compile_data_object_admission(
        obligation, receipt, as_of=ASOF,
        operation_id=NO_SEND_CONTEXT["operation_id"], intended_use="LEARNING_RESEARCH",
    )
    assert not row["product_admitted"] and not row["learning_admitted"]


def test_resealed_current_snapshot_cannot_claim_historical_pit_at_consumer():
    obligation, receipt = current_pair()
    row = compile_data_object_admission(obligation, receipt, as_of=ASOF, **NO_SEND_CONTEXT)
    row["historical_pit_eligible"] = True
    row.pop("admission_hash")
    row["admission_hash"] = sha256_payload(row)
    with pytest.raises(DataObjectAdmissionError, match="CURRENT_OBSERVATION"):
        validate_compiled_data_object_admission(row, as_of=ASOF)


def test_resealed_current_snapshot_cannot_hide_source_after_its_own_asof():
    obligation, receipt = current_pair()
    row = compile_data_object_admission(
        obligation, receipt, as_of=ASOF, **NO_SEND_CONTEXT,
    )
    row["research_as_of"] = "2026-10-21T09:30:00+00:00"
    row.pop("admission_hash")
    row["admission_hash"] = sha256_payload(row)
    with pytest.raises(
        DataObjectAdmissionError,
        match="CURRENT_OBSERVATION_OUTSIDE_RESEARCH_ASOF:observed_at",
    ):
        validate_compiled_data_object_admission(row)


def test_current_mode_is_consumed_by_existing_b1_runtime_compiler():
    obligation = {**deepcopy(OBLIGATION), "revision": "UNKNOWN", "vintage": "UNKNOWN"}
    context = {
        **NO_SEND_CONTEXT, "research_as_of": ASOF,
        "temporal_scope": "CURRENT_OBSERVATION",
        "rights_by_dataset": {"P_DAILY": _rights_evidence()},
    }
    receipts = compile_b1_runtime_admission_receipts([obligation], b1_context=context)
    row = compile_data_object_admission(obligation, receipts["P_DAILY"], as_of=ASOF, **NO_SEND_CONTEXT)
    assert row["product_admitted"] is True
    assert not row["historical_pit_eligible"]
