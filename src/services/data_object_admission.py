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
from src.services.provider_operation_rights import (
    ProviderOperationRightsError,
    validate_provider_operation_rights_evidence,
)


SCHEMA_VERSION = "data-object-admission-receipt-v1"
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
        "redistribution_admitted", "historical_pit_eligible",
        "strategy_upgrade_authority", "independent_action_authority",
    ):
        if type(result.get(key)) is not bool:
            raise DataObjectAdmissionError(
                "DATA_OBJECT_COMPILED_ADMISSION_FLAG_INVALID:" + key
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
        for clock_field in ("available_at", "observed_at"):
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


def build_operation_specific_data_admission_receipt(
    obligation: Mapping[str, Any],
    source_identity: Mapping[str, Any],
    rights_evidence: Mapping[str, Any],
    *,
    units: Mapping[str, str],
    available_at: str | datetime,
    revision: str,
    vintage: str | datetime,
) -> dict[str, Any]:
    """Bind one observed source identity to one externally evidenced operation.

    The caller cannot supply a free-form rights map.  Product/storage/training/
    redistribution decisions come only from the validated rights-evidence
    owner and are sealed into the existing admission receipt.
    """
    if not isinstance(obligation, Mapping) or not isinstance(source_identity, Mapping):
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_BUILD_INPUT_INVALID")
    try:
        evidence = validate_provider_operation_rights_evidence(rights_evidence)
    except ProviderOperationRightsError as exc:
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_RIGHTS_EVIDENCE_INVALID") from exc
    provider = _text(source_identity.get("provider_identity"))
    route = _text(source_identity.get("provider_route"))
    if provider != evidence.get("provider_identity"):
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_RIGHTS_PROVIDER_MISMATCH")
    if route != evidence.get("provider_route"):
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_RIGHTS_ROUTE_MISMATCH")
    source_hash = _text(source_identity.get("identity_hash")).lower()
    content_hash = _text(source_identity.get("content_sha256")).lower()
    if not _is_sha256(source_hash) or not _is_sha256(content_hash):
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_SOURCE_IDENTITY_INVALID")
    observed_at = _text(source_identity.get("observed_at"))
    _parse_aware_datetime(observed_at)
    completed_through = _text(
        source_identity.get("returned_end")
        or source_identity.get("available_at_max")
    )
    if not completed_through:
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_COMPLETED_THROUGH_MISSING")
    adjustment_basis = _text(source_identity.get("observed_adjustment_basis")).upper()
    currency = _text(source_identity.get("currency")).upper()
    if not adjustment_basis or not currency:
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_SOURCE_SEMANTICS_MISSING")
    payload = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": _text(obligation.get("dataset_id")),
        "provider_identity": provider,
        "provider_route": route,
        "native_frequency": _text(obligation.get("native_frequency")),
        "source_identity_hash": source_hash,
        "content_sha256": content_hash,
        "observed_at": observed_at,
        "available_at": _aware_text(available_at),
        "completed_through": completed_through,
        "units": dict(units),
        "currency": currency,
        "adjustment_basis": adjustment_basis,
        "revision": _text(revision),
        "vintage": _aware_text(vintage),
        "rights": dict(evidence["rights"]),
        "rights_evidence": evidence,
        "historical_pit_eligible": False,
        "consumer_scope": _text(obligation.get("product_consumer")),
    }
    return seal_data_object_admission_receipt(payload)


def compile_data_object_admission(
    obligation: Mapping[str, Any],
    receipt: Mapping[str, Any] | None,
    *,
    as_of: date | datetime | str | None = None,
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
        "historical_pit_eligible": False,
        "strategy_upgrade_authority": False,
        "independent_action_authority": False,
        "receipt_hash": None,
        "rights_evidence_hash": None,
        "operation_purpose": None,
        "delivery_scope": None,
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
    product_allowed = rights["product_use"] == "ALLOWED"
    if not product_allowed:
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
        "rights_evidence_hash": (
            rights_evidence.get("evidence_hash")
            if isinstance(rights_evidence, Mapping)
            else None
        ),
        "operation_purpose": (
            rights_evidence.get("operation_purpose")
            if isinstance(rights_evidence, Mapping)
            else None
        ),
        "delivery_scope": (
            rights_evidence.get("delivery_scope")
            if isinstance(rights_evidence, Mapping)
            else None
        ),
        "receipt_hash": normalized["receipt_hash"],
    })


def validate_data_object_admission_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    keys = set(value) if isinstance(value, Mapping) else set()
    if (
        not isinstance(value, Mapping)
        or not _REQUIRED_RECEIPT_FIELDS <= keys
        or keys - _REQUIRED_RECEIPT_FIELDS - _OPTIONAL_RECEIPT_FIELDS
    ):
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
        try:
            rights_evidence = validate_provider_operation_rights_evidence(rights_evidence)
        except ProviderOperationRightsError as exc:
            raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_RIGHTS_EVIDENCE_INVALID") from exc
        if rights_evidence.get("provider_identity") != result.get("provider_identity"):
            raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_RIGHTS_PROVIDER_MISMATCH")
        if rights_evidence.get("provider_route") != result.get("provider_route"):
            raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_RIGHTS_ROUTE_MISMATCH")
        if rights_evidence.get("rights") != normalized_rights:
            raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_RIGHTS_DECISION_MISMATCH")
        result["rights_evidence"] = rights_evidence
    elif normalized_rights["product_use"] == "ALLOWED":
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_RIGHTS_EVIDENCE_MISSING")
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


def _aware_text(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = _text(value).replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError as exc:
            raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_TIMESTAMP_NAIVE")
    return parsed.astimezone(timezone.utc).isoformat()


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
