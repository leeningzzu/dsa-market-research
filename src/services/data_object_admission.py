# -*- coding: utf-8 -*-
"""Fail-closed admission receipts for the existing 76-object research graph.

This module is a thin validator/compiler used by the existing Registry.  It is
not a provider, database, cache, rights authority, PIT store, strategy, report
or model feature factory.  A receipt can only record explicit upstream facts;
UNKNOWN rights, units, vintages or clocks remain not admitted.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timezone
import math
from typing import Any

from src.services.pit_identity import sha256_payload


SCHEMA_VERSION = "data-object-admission-receipt-v1"
RIGHTS_EVIDENCE_SCHEMA_VERSION = "source-rights-evidence-v1"
RIGHTS_EVIDENCE_PURPOSE_NO_SEND = "NO_SEND_RESEARCH"
RIGHTS_EVIDENCE_PURPOSE_LEARNING = "LEARNING_RESEARCH"
RIGHTS_EVIDENCE_PURPOSES = frozenset({
    RIGHTS_EVIDENCE_PURPOSE_NO_SEND,
    RIGHTS_EVIDENCE_PURPOSE_LEARNING,
})
RIGHTS_EVIDENCE_STATES = frozenset({"VERIFIED", "UNKNOWN"})
RIGHTS_VALUES = frozenset({"ALLOWED", "FORBIDDEN", "UNKNOWN", "NOT_APPLICABLE"})
ADMISSION_STATES = frozenset({
    "NOT_APPLICABLE",
    "NOT_OBSERVED",
    "SOURCE_IDENTITY_ONLY",
    "PRODUCT_ADMITTED",
    "LEARNING_ADMITTED",
})
_REQUIRED_RECEIPT_FIELDS = frozenset({
    "schema_version",
    "dataset_id",
    "provider_identity",
    "provider_route",
    "native_frequency",
    "source_identity_hash",
    "content_sha256",
    "observed_at",
    "available_at",
    "completed_through",
    "units",
    "currency",
    "adjustment_basis",
    "revision",
    "vintage",
    "rights",
    "historical_pit_eligible",
    "consumer_scope",
    "receipt_hash",
})
_REQUIRED_RIGHTS_FIELDS = frozenset({
    "product_use",
    "storage",
    "training",
    "redistribution",
})
_OPTIONAL_RECEIPT_FIELDS = frozenset({"rights_evidence"})
_REQUIRED_RIGHTS_EVIDENCE_FIELDS = frozenset({
    "schema_version",
    "operation_id",
    "intended_use",
    "provider_identity",
    "upstream_source_owner",
    "upstream_rights_state",
    "dataset_id",
    "consumer_scope",
    "terms_uri",
    "terms_sha256",
    "terms_observed_at",
    "rights",
    "evidence_hash",
})


class DataObjectAdmissionError(ValueError):
    """Raised when a claimed admission receipt is malformed or self-forged."""


def _seal_compiled_admission(value: Mapping[str, Any]) -> dict[str, Any]:
    body = dict(value)
    body.pop("admission_hash", None)
    body["admission_hash"] = sha256_payload(body)
    return body


def validate_compiled_data_object_admission(
    value: Mapping[str, Any],
    *,
    as_of: date | datetime | str | None = None,
) -> dict[str, Any]:
    """Verify the exact Product/Learning decision, not only its source receipt."""
    if not isinstance(value, Mapping):
        raise DataObjectAdmissionError("DATA_OBJECT_COMPILED_ADMISSION_INVALID")
    result = dict(value)
    admission_hash = result.pop("admission_hash", None)
    if not _is_sha256(admission_hash) or sha256_payload(result) != admission_hash:
        raise DataObjectAdmissionError("DATA_OBJECT_COMPILED_ADMISSION_HASH_MISMATCH")
    if result.get("schema_version") != SCHEMA_VERSION:
        raise DataObjectAdmissionError("DATA_OBJECT_COMPILED_ADMISSION_VERSION_MISMATCH")
    if result.get("admission_state") not in ADMISSION_STATES:
        raise DataObjectAdmissionError("DATA_OBJECT_COMPILED_ADMISSION_STATE_INVALID")
    for key in (
        "applicable", "source_identity_verified", "semantic_identity_complete",
        "product_admitted", "storage_admitted", "learning_admitted",
        "redistribution_admitted", "rights_evidence_verified", "runtime_source_bound",
        "historical_pit_eligible", "strategy_upgrade_authority", "independent_action_authority",
    ):
        if type(result.get(key)) is not bool:
            raise DataObjectAdmissionError(
                "DATA_OBJECT_COMPILED_ADMISSION_FLAG_INVALID:" + key
            )
    if result.get("product_admitted") is True and result.get("rights_evidence_verified") is not True:
        raise DataObjectAdmissionError(
            "DATA_OBJECT_COMPILED_ADMISSION_PRODUCT_WITHOUT_VERIFIED_RIGHTS"
        )
    if result.get("product_admitted") is True:
        if result.get("runtime_source_bound") is not True:
            raise DataObjectAdmissionError(
                "DATA_OBJECT_COMPILED_ADMISSION_PRODUCT_WITHOUT_RUNTIME_SOURCE"
            )
        if (
            result.get("runtime_source_identity_hash") != result.get("source_identity_hash")
            or result.get("runtime_source_content_sha256") != result.get("content_sha256")
        ):
            raise DataObjectAdmissionError(
                "DATA_OBJECT_COMPILED_ADMISSION_RUNTIME_SOURCE_MISMATCH"
            )
    if result.get("learning_admitted") is True:
        rights = result.get("rights")
        if (
            result.get("intended_use") != RIGHTS_EVIDENCE_PURPOSE_LEARNING
            or result.get("product_admitted") is not True
            or result.get("storage_admitted") is not True
            or result.get("historical_pit_eligible") is not True
            or not isinstance(rights, Mapping)
            or rights.get("training") != "ALLOWED"
        ):
            raise DataObjectAdmissionError(
                "DATA_OBJECT_COMPILED_ADMISSION_LEARNING_PURPOSE_INVALID"
            )
    if (
        result.get("intended_use") == RIGHTS_EVIDENCE_PURPOSE_NO_SEND
        and result.get("redistribution_admitted") is True
    ):
        raise DataObjectAdmissionError(
            "DATA_OBJECT_COMPILED_ADMISSION_NO_SEND_REDISTRIBUTION_INVALID"
        )
    # A receipt originally admitted for a *later* run must not become
    # known-at evidence in an earlier frozen run. Its hash can be intact while
    # the observed/available clocks are outside this consumer's cutoff.
    if as_of is not None and result.get("product_admitted") is True:
        ceiling = _as_of_ceiling(as_of)
        if ceiling is None:
            raise DataObjectAdmissionError(
                "DATA_OBJECT_COMPILED_ADMISSION_RESEARCH_ASOF_INVALID"
            )
        for clock_field in ("available_at", "observed_at", "rights_terms_observed_at"):
            timestamp = _parse_aware_datetime(result.get(clock_field))
            if timestamp > ceiling:
                raise DataObjectAdmissionError(
                    "DATA_OBJECT_COMPILED_ADMISSION_OUTSIDE_RESEARCH_ASOF:"
                    + clock_field
                )
        vintage = _parse_optional_vintage_timestamp(result.get("vintage"))
        if vintage is not None and vintage > ceiling:
            raise DataObjectAdmissionError(
                "DATA_OBJECT_COMPILED_ADMISSION_OUTSIDE_RESEARCH_ASOF:vintage"
            )
        if _completed_through_after_asof(
            result.get("completed_through"), ceiling
        ):
            raise DataObjectAdmissionError(
                "DATA_OBJECT_COMPILED_ADMISSION_OUTSIDE_RESEARCH_ASOF:completed_through"
            )
    result["admission_hash"] = admission_hash
    return result


def seal_data_object_admission_receipt(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Return one immutable-hash receipt from an explicit upstream payload.

    This helper does not decide whether the supplied rights assertion is true;
    it only makes later drift detectable.  The operation-specific rights owner
    and exact provider terms remain external evidence.
    """
    body = dict(payload)
    body.pop("receipt_hash", None)
    body.setdefault("schema_version", SCHEMA_VERSION)
    body["receipt_hash"] = sha256_payload(body)
    return body


def seal_source_rights_evidence(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Seal operation-specific provider and upstream terms evidence.

    The seal only prevents drift. Admission still requires exact provider,
    dataset, consumer, purpose, clocks and rights agreement at point of use.
    """
    body = dict(payload)
    body.pop("evidence_hash", None)
    body.setdefault("schema_version", RIGHTS_EVIDENCE_SCHEMA_VERSION)
    body["evidence_hash"] = sha256_payload(body)
    return body


def validate_source_rights_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _REQUIRED_RIGHTS_EVIDENCE_FIELDS:
        raise DataObjectAdmissionError("SOURCE_RIGHTS_EVIDENCE_SCHEMA_MISMATCH")
    result = dict(value)
    if result.get("schema_version") != RIGHTS_EVIDENCE_SCHEMA_VERSION:
        raise DataObjectAdmissionError("SOURCE_RIGHTS_EVIDENCE_VERSION_MISMATCH")
    for key in (
        "operation_id", "intended_use", "provider_identity", "upstream_source_owner",
        "upstream_rights_state", "dataset_id", "consumer_scope", "terms_uri",
        "terms_observed_at",
    ):
        if not _text(result.get(key)):
            raise DataObjectAdmissionError("SOURCE_RIGHTS_EVIDENCE_FIELD_MISSING:" + key)
    for key in ("terms_sha256", "evidence_hash"):
        if not _is_sha256(result.get(key)):
            raise DataObjectAdmissionError("SOURCE_RIGHTS_EVIDENCE_HASH_INVALID:" + key)
    state = _text(result.get("upstream_rights_state")).upper()
    if state not in RIGHTS_EVIDENCE_STATES:
        raise DataObjectAdmissionError("SOURCE_RIGHTS_EVIDENCE_STATE_INVALID")
    result["upstream_rights_state"] = state
    intended_use = _text(result.get("intended_use"))
    if intended_use not in RIGHTS_EVIDENCE_PURPOSES:
        raise DataObjectAdmissionError("SOURCE_RIGHTS_EVIDENCE_PURPOSE_INVALID")
    result["intended_use"] = intended_use
    rights = result.get("rights")
    if not isinstance(rights, Mapping) or set(rights) != _REQUIRED_RIGHTS_FIELDS:
        raise DataObjectAdmissionError("SOURCE_RIGHTS_EVIDENCE_RIGHTS_SCHEMA_MISMATCH")
    normalized_rights = {key: _text(rights.get(key)).upper() for key in _REQUIRED_RIGHTS_FIELDS}
    if any(item not in RIGHTS_VALUES for item in normalized_rights.values()):
        raise DataObjectAdmissionError("SOURCE_RIGHTS_EVIDENCE_RIGHTS_VALUE_INVALID")
    result["rights"] = normalized_rights
    _parse_aware_datetime(result["terms_observed_at"])
    expected_hash = sha256_payload({key: result[key] for key in result if key != "evidence_hash"})
    if result["evidence_hash"] != expected_hash:
        raise DataObjectAdmissionError("SOURCE_RIGHTS_EVIDENCE_HASH_MISMATCH")
    return result


def compile_data_object_admission(
    obligation: Mapping[str, Any],
    receipt: Mapping[str, Any] | None,
    *,
    as_of: date | datetime | str | None = None,
    operation_id: str | None = None,
    intended_use: str | None = None,
) -> dict[str, Any]:
    """Compile one obligation into explicit Product/Learning admission states."""
    dataset_id = _text(obligation.get("dataset_id"))
    native_frequency = _text(obligation.get("native_frequency"))
    consumer_scope = _text(obligation.get("product_consumer"))
    applicable = obligation.get("state") != "NOT_APPLICABLE"
    base = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": dataset_id,
        "applicable": bool(applicable),
        "admission_state": "NOT_APPLICABLE" if not applicable else "NOT_OBSERVED",
        "reasons": [],
        "source_identity_verified": False,
        "semantic_identity_complete": False,
        "product_admitted": False,
        "storage_admitted": False,
        "learning_admitted": False,
        "redistribution_admitted": False,
        "rights_evidence_verified": False,
        "runtime_source_bound": False,
        "runtime_source_identity_hash": None,
        "runtime_source_content_sha256": None,
        "runtime_provider_identity": None,
        "runtime_completed_through": None,
        "rights_evidence_hash": None,
        "operation_id": None,
        "intended_use": None,
        "upstream_source_owner": None,
        "terms_uri": None,
        "rights_terms_observed_at": None,
        "historical_pit_eligible": False,
        "strategy_upgrade_authority": False,
        "independent_action_authority": False,
        "receipt_hash": None,
    }
    if not applicable:
        base["reasons"] = ["DATA_OBJECT_NOT_APPLICABLE_TO_ASSET_ROUTE"]
        return _seal_compiled_admission(base)
    if receipt is None:
        base["reasons"] = ["DATA_OBJECT_ADMISSION_RECEIPT_MISSING"]
        return _seal_compiled_admission(base)
    normalized = validate_data_object_admission_receipt(receipt)
    reasons: list[str] = []
    if normalized["dataset_id"] != dataset_id:
        reasons.append("DATASET_ID_MISMATCH")
    if normalized["native_frequency"] != native_frequency:
        reasons.append("NATIVE_FREQUENCY_MISMATCH")
    if normalized["consumer_scope"] != consumer_scope:
        reasons.append("PRODUCT_CONSUMER_MISMATCH")
    available_at = _parse_aware_datetime(normalized["available_at"])
    observed_at = _parse_aware_datetime(normalized["observed_at"])
    if available_at > observed_at:
        reasons.append("AVAILABLE_AT_AFTER_OBSERVED_AT")
    ceiling = _as_of_ceiling(as_of)
    if ceiling is not None:
        if available_at > ceiling:
            reasons.append("AVAILABLE_AT_AFTER_RESEARCH_ASOF")
        if observed_at > ceiling:
            reasons.append("OBSERVED_AT_AFTER_RESEARCH_ASOF")
        vintage = _parse_optional_vintage_timestamp(normalized["vintage"])
        if vintage is not None and vintage > ceiling:
            reasons.append("VINTAGE_AFTER_RESEARCH_ASOF")
        if _completed_through_after_asof(
            normalized["completed_through"], ceiling
        ):
            reasons.append("COMPLETED_THROUGH_AFTER_RESEARCH_ASOF")
    units = normalized["units"]
    unit_values = tuple(_text(value).upper() for value in units.values())
    semantic_complete = bool(
        units
        and all(value and value != "UNKNOWN" for value in unit_values)
        and _known_semantic(normalized["currency"])
        and _known_semantic(normalized["adjustment_basis"])
        and _known_semantic(normalized["revision"])
        and _known_semantic(normalized["vintage"])
        and _text(normalized["completed_through"])
    )
    if not semantic_complete:
        reasons.append("UNITS_BASIS_REVISION_OR_VINTAGE_UNKNOWN")
    rights = normalized["rights"]
    rights_evidence = normalized.get("rights_evidence")
    rights_evidence_verified = False
    runtime_source_identity_hash = _text(obligation.get("source_identity_hash")).lower()
    runtime_source_content_sha256 = _text(obligation.get("source_content_sha256")).lower()
    runtime_provider_identity = _text(obligation.get("provider_identity"))
    runtime_completed_through = _text(obligation.get("completed_through"))
    runtime_source_observed_at = _text(obligation.get("source_observed_at"))
    runtime_source_bound = bool(
        _is_sha256(runtime_source_identity_hash)
        and _is_sha256(runtime_source_content_sha256)
    )
    if not runtime_source_bound:
        reasons.append("RUNTIME_SOURCE_IDENTITY_OR_CONTENT_MISSING")
    else:
        if runtime_source_identity_hash != normalized["source_identity_hash"]:
            reasons.append("RUNTIME_SOURCE_IDENTITY_MISMATCH")
            runtime_source_bound = False
        if runtime_source_content_sha256 != normalized["content_sha256"]:
            reasons.append("RUNTIME_SOURCE_CONTENT_MISMATCH")
            runtime_source_bound = False
        if runtime_provider_identity and runtime_provider_identity != normalized["provider_identity"]:
            reasons.append("RUNTIME_SOURCE_PROVIDER_MISMATCH")
            runtime_source_bound = False
        if runtime_completed_through and runtime_completed_through != normalized["completed_through"]:
            reasons.append("RUNTIME_SOURCE_COMPLETED_THROUGH_MISMATCH")
            runtime_source_bound = False
        if runtime_source_observed_at:
            runtime_observed_at = _parse_aware_datetime(runtime_source_observed_at)
            if runtime_observed_at != observed_at:
                reasons.append("RUNTIME_SOURCE_OBSERVED_AT_MISMATCH")
                runtime_source_bound = False
    expected_operation_id = _text(operation_id)
    expected_intended_use = _text(intended_use)
    if rights["product_use"] == "ALLOWED":
        if (
            not expected_operation_id
            or expected_intended_use not in RIGHTS_EVIDENCE_PURPOSES
        ):
            reasons.append("RIGHTS_EVIDENCE_OPERATION_CONTEXT_MISSING_OR_INVALID")
        if not isinstance(rights_evidence, Mapping):
            reasons.append("PRODUCT_RIGHTS_EVIDENCE_MISSING")
        else:
            if rights_evidence["provider_identity"] != normalized["provider_identity"]:
                reasons.append("RIGHTS_EVIDENCE_PROVIDER_MISMATCH")
            if rights_evidence["dataset_id"] != normalized["dataset_id"]:
                reasons.append("RIGHTS_EVIDENCE_DATASET_MISMATCH")
            if rights_evidence["consumer_scope"] != normalized["consumer_scope"]:
                reasons.append("RIGHTS_EVIDENCE_CONSUMER_MISMATCH")
            if expected_operation_id and rights_evidence["operation_id"] != expected_operation_id:
                reasons.append("RIGHTS_EVIDENCE_OPERATION_MISMATCH")
            if expected_intended_use and rights_evidence["intended_use"] != expected_intended_use:
                reasons.append("RIGHTS_EVIDENCE_EXPECTED_USE_MISMATCH")
            if rights_evidence["rights"] != rights:
                reasons.append("RIGHTS_EVIDENCE_GRANT_MISMATCH")
            if rights_evidence["upstream_rights_state"] != "VERIFIED" or not _known_semantic(
                rights_evidence["upstream_source_owner"]
            ):
                reasons.append("UPSTREAM_RIGHTS_NOT_VERIFIED")
            if _parse_aware_datetime(rights_evidence["terms_observed_at"]) > observed_at:
                reasons.append("RIGHTS_EVIDENCE_AFTER_SOURCE_OBSERVATION")
            if ceiling is not None and _parse_aware_datetime(rights_evidence["terms_observed_at"]) > ceiling:
                reasons.append("RIGHTS_EVIDENCE_AFTER_RESEARCH_ASOF")
            if (
                rights_evidence["intended_use"] == RIGHTS_EVIDENCE_PURPOSE_NO_SEND
                and rights["redistribution"] != "FORBIDDEN"
            ):
                reasons.append("RIGHTS_EVIDENCE_NO_SEND_SCOPE_CONTRADICTION")
            rights_evidence_verified = not any(
                reason.startswith("RIGHTS_EVIDENCE_") or reason == "UPSTREAM_RIGHTS_NOT_VERIFIED"
                for reason in reasons
            )
    product_allowed = bool(
        rights["product_use"] == "ALLOWED"
        and rights_evidence_verified
        and runtime_source_bound
    )
    if rights["product_use"] != "ALLOWED":
        reasons.append("PRODUCT_USE_RIGHT_NOT_ALLOWED")
    source_verified = not any(
        reason in reasons
        for reason in (
            "DATASET_ID_MISMATCH",
            "NATIVE_FREQUENCY_MISMATCH",
            "PRODUCT_CONSUMER_MISMATCH",
            "AVAILABLE_AT_AFTER_OBSERVED_AT",
            "AVAILABLE_AT_AFTER_RESEARCH_ASOF",
            "OBSERVED_AT_AFTER_RESEARCH_ASOF",
            "VINTAGE_AFTER_RESEARCH_ASOF",
            "COMPLETED_THROUGH_AFTER_RESEARCH_ASOF",
        )
    )
    product_admitted = source_verified and semantic_complete and product_allowed
    storage_admitted = product_admitted and rights["storage"] == "ALLOWED"
    redistribution_admitted = product_admitted and rights["redistribution"] == "ALLOWED"
    learning_admitted = bool(
        product_admitted
        and storage_admitted
        and rights_evidence_verified
        and expected_intended_use == RIGHTS_EVIDENCE_PURPOSE_LEARNING
        and isinstance(rights_evidence, Mapping)
        and rights_evidence["intended_use"] == RIGHTS_EVIDENCE_PURPOSE_LEARNING
        and rights["training"] == "ALLOWED"
        and normalized["historical_pit_eligible"] is True
    )
    state = (
        "LEARNING_ADMITTED"
        if learning_admitted
        else "PRODUCT_ADMITTED"
        if product_admitted
        else "SOURCE_IDENTITY_ONLY"
        if source_verified
        else "NOT_OBSERVED"
    )
    return _seal_compiled_admission({
        **base,
        "admission_state": state,
        "reasons": tuple(dict.fromkeys(reasons)),
        "source_identity_verified": source_verified,
        "semantic_identity_complete": semantic_complete,
        "product_admitted": product_admitted,
        "storage_admitted": storage_admitted,
        "learning_admitted": learning_admitted,
        "redistribution_admitted": redistribution_admitted,
        "rights_evidence_verified": rights_evidence_verified,
        "runtime_source_bound": runtime_source_bound,
        "runtime_source_identity_hash": runtime_source_identity_hash or None,
        "runtime_source_content_sha256": runtime_source_content_sha256 or None,
        "runtime_provider_identity": runtime_provider_identity or None,
        "runtime_completed_through": runtime_completed_through or None,
        "rights_evidence_hash": rights_evidence.get("evidence_hash") if isinstance(rights_evidence, Mapping) else None,
        "operation_id": rights_evidence.get("operation_id") if isinstance(rights_evidence, Mapping) else None,
        "intended_use": rights_evidence.get("intended_use") if isinstance(rights_evidence, Mapping) else None,
        "upstream_source_owner": rights_evidence.get("upstream_source_owner") if isinstance(rights_evidence, Mapping) else None,
        "terms_uri": rights_evidence.get("terms_uri") if isinstance(rights_evidence, Mapping) else None,
        "rights_terms_observed_at": rights_evidence.get("terms_observed_at") if isinstance(rights_evidence, Mapping) else None,
        "historical_pit_eligible": normalized["historical_pit_eligible"],
        "provider_identity": normalized["provider_identity"],
        "provider_route": normalized["provider_route"],
        "source_identity_hash": normalized["source_identity_hash"],
        "content_sha256": normalized["content_sha256"],
        "observed_at": normalized["observed_at"],
        "available_at": normalized["available_at"],
        "completed_through": normalized["completed_through"],
        "units": dict(units),
        "currency": normalized["currency"],
        "adjustment_basis": normalized["adjustment_basis"],
        "revision": normalized["revision"],
        "vintage": normalized["vintage"],
        "rights": dict(rights),
        "receipt_hash": normalized["receipt_hash"],
    })


def validate_data_object_admission_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_RECEIPT_SCHEMA_MISMATCH")
    fields = set(value)
    if not _REQUIRED_RECEIPT_FIELDS <= fields or fields - _REQUIRED_RECEIPT_FIELDS - _OPTIONAL_RECEIPT_FIELDS:
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_RECEIPT_SCHEMA_MISMATCH")
    result = dict(value)
    if result.get("schema_version") != SCHEMA_VERSION:
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_RECEIPT_VERSION_MISMATCH")
    for key in (
        "dataset_id", "provider_identity", "provider_route", "native_frequency",
        "observed_at", "available_at", "completed_through", "currency",
        "adjustment_basis", "revision", "vintage", "consumer_scope",
    ):
        if not _text(result.get(key)):
            raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_FIELD_MISSING:" + key)
    for key in ("source_identity_hash", "content_sha256", "receipt_hash"):
        if not _is_sha256(result.get(key)):
            raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_HASH_INVALID:" + key)
    units = result.get("units")
    if not isinstance(units, Mapping) or not units:
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_UNITS_INVALID")
    rights = result.get("rights")
    if not isinstance(rights, Mapping) or set(rights) != _REQUIRED_RIGHTS_FIELDS:
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_RIGHTS_SCHEMA_MISMATCH")
    normalized_rights = {key: _text(rights.get(key)).upper() for key in _REQUIRED_RIGHTS_FIELDS}
    if any(value not in RIGHTS_VALUES for value in normalized_rights.values()):
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_RIGHTS_VALUE_INVALID")
    result["rights"] = normalized_rights
    rights_evidence = result.get("rights_evidence")
    if rights_evidence is not None:
        result["rights_evidence"] = validate_source_rights_evidence(rights_evidence)
    if type(result.get("historical_pit_eligible")) is not bool:
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_PIT_FLAG_INVALID")
    _parse_aware_datetime(result["observed_at"])
    _parse_aware_datetime(result["available_at"])
    expected_hash = sha256_payload({key: result[key] for key in result if key != "receipt_hash"})
    if result["receipt_hash"] != expected_hash:
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_RECEIPT_HASH_MISMATCH")
    return result


def compile_data_object_admission_view(
    obligations: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    receipts: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    as_of: date | datetime | str | None = None,
    operation_id: str | None = None,
    intended_use: str | None = None,
) -> dict[str, Any]:
    receipt_map = dict(receipts or {})
    known = {_text(item.get("dataset_id")) for item in obligations}
    unknown = sorted(set(receipt_map) - known)
    if unknown:
        raise DataObjectAdmissionError(
            "DATA_OBJECT_ADMISSION_ORPHAN_RECEIPT:" + ",".join(unknown)
        )
    rows = [
        compile_data_object_admission(
            obligation,
            receipt_map.get(_text(obligation.get("dataset_id"))),
            as_of=as_of,
            operation_id=operation_id,
            intended_use=intended_use,
        )
        for obligation in obligations
    ]
    counts = {state: sum(row["admission_state"] == state for row in rows) for state in sorted(ADMISSION_STATES)}
    document = {
        "schema_version": "data-object-admission-view-v1",
        "objects": rows,
        "counts": counts,
        "product_admitted_count": sum(row["product_admitted"] for row in rows),
        "learning_admitted_count": sum(row["learning_admitted"] for row in rows),
        "policy": {
            "unknown_rights_never_admit_product": True,
            "product_rights_do_not_imply_storage_or_training": True,
            "learning_requires_storage_training_and_historical_pit": True,
            "learning_requires_distinct_learning_operation_purpose": True,
            "product_admission_requires_current_operation_context": True,
            "product_admission_requires_current_runtime_source_identity": True,
            "no_strategy_or_action_authority": True,
        },
    }
    document["view_hash"] = sha256_payload(document)
    return document


def _known_semantic(value: Any) -> bool:
    text = _text(value).upper()
    return bool(text and text not in {"UNKNOWN", "UNSPECIFIED", "MISSING"})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _is_sha256(value: Any) -> bool:
    text = _text(value).lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def _parse_aware_datetime(value: Any) -> datetime:
    text = _text(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_TIMESTAMP_NAIVE")
    return parsed.astimezone(timezone.utc)


def _parse_optional_vintage_timestamp(value: Any) -> datetime | None:
    """Interpret explicit ISO vintages; leave opaque revision labels opaque.

    A date-only vintage is conservatively known no earlier than that day's
    UTC end. A datetime-looking vintage without a timezone is invalid rather
    than silently being treated as an opaque safe historical label.
    """
    text = _text(value)
    if len(text) == 10:
        try:
            return datetime.combine(
                date.fromisoformat(text), time.max, tzinfo=timezone.utc
            )
        except ValueError:
            return None
    if len(text) > 10 and text[10] in {"T", " "}:
        try:
            date.fromisoformat(text[:10])
        except ValueError:
            return None
        return _parse_aware_datetime(text)
    return None


def _completed_through_after_asof(value: Any, ceiling: datetime) -> bool:
    """Compare explicit ISO completion clocks without guessing opaque periods."""
    text = _text(value)
    if len(text) == 10:
        try:
            return date.fromisoformat(text) > ceiling.date()
        except ValueError:
            return False
    if len(text) > 10 and text[10] in {"T", " "}:
        try:
            date.fromisoformat(text[:10])
        except ValueError:
            return False
        return _parse_aware_datetime(text) > ceiling
    return False


def _as_of_ceiling(value: date | datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_ASOF_NAIVE")
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return datetime.combine(value, time.max, tzinfo=timezone.utc)
    text = _text(value)
    if not text:
        return None
    try:
        if "T" not in text and " " not in text:
            return datetime.combine(date.fromisoformat(text), time.max, tzinfo=timezone.utc)
        return _parse_aware_datetime(text)
    except ValueError as exc:
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_ASOF_INVALID") from exc
