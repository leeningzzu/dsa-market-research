# -*- coding: utf-8 -*-
"""Admission receipts for the existing 76-object research graph.

This module is a thin validator/compiler used by the existing Registry. It is
not a provider, database, cache, rights authority, PIT store, strategy, report
or model feature factory. A receipt can only record explicit upstream facts.
Unknown rights, units, basis or clocks never admit Product; an explicitly bound
CURRENT_OBSERVATION may use unknown publisher revision/vintage for that exact
NO_SEND run only and can never become historical PIT, learning or promotion.
"""
from __future__ import annotations

from collections.abc import Mapping
import hashlib
import json
from pathlib import Path
from datetime import date, datetime, time, timezone
import math
from typing import Any
from urllib.parse import urlsplit

from src.services.pit_identity import sha256_payload


SCHEMA_VERSION = "data-object-admission-receipt-v1"
CURRENT_OBSERVATION_SCHEMA_VERSION = "data-object-admission-receipt-v2"
TEMPORAL_SCOPE_CURRENT_OBSERVATION = "CURRENT_OBSERVATION"
RIGHTS_EVIDENCE_SCHEMA_VERSION = "source-rights-evidence-v2"
RIGHTS_EVIDENCE_BUNDLE_SCHEMA_VERSION = "source-rights-evidence-bundle-v1"
B1_RIGHTS_CONTEXT_SCHEMA_VERSION = "b1-operation-rights-context-v1"
RIGHTS_EVIDENCE_PURPOSE_NO_SEND = "NO_SEND_RESEARCH"
RIGHTS_EVIDENCE_PURPOSE_LEARNING = "LEARNING_RESEARCH"
RIGHTS_EVIDENCE_PURPOSES = frozenset({
    RIGHTS_EVIDENCE_PURPOSE_NO_SEND,
    RIGHTS_EVIDENCE_PURPOSE_LEARNING,
})
RIGHTS_EVIDENCE_STATES = frozenset({"VERIFIED", "UNKNOWN"})
RIGHTS_TERMS_SCOPES = frozenset({
    "OFFICIAL_PROVIDER_DATA_USE_STATEMENT",
    "UPSTREAM_DATASET_TERMS",
    "SIGNED_PROVIDER_CONTRACT",
    "SYNTHETIC_TEST_CONTRACT",
    "COMPOSITE_OPERATION_MATCHED_UPSTREAM_TERMS",
})
_SOFTWARE_PACKAGE_METADATA_HOSTS = frozenset({
    "pypi.org",
    "files.pythonhosted.org",
    "pypi.python.org",
    "registry.npmjs.org",
    "npmjs.com",
    "rubygems.org",
    "crates.io",
    "repo.maven.apache.org",
    "central.sonatype.com",
    "nuget.org",
    "api.nuget.org",
})
# These are provider technical/API documentation routes, not data-use terms.
# Keep this narrow and explicit: a technical example that demonstrates local
# calculation/storage must not be promoted into a general rights grant.
_NON_TERMS_TECHNICAL_PATH_PREFIXES = (
    "/helpdocs/api/markdown/",
)
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
_CURRENT_RECEIPT_FIELDS = frozenset({
    "temporal_scope", "research_as_of", "observation_operation_id",
})
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
    "terms_scope",
    "product_use_clause",
    "product_use_clause_sha256",
    "rights",
    "evidence_hash",
})
_REQUIRED_RIGHTS_EVIDENCE_BUNDLE_FIELDS = frozenset({
    "schema_version",
    "operation_id",
    "intended_use",
    "provider_identity",
    "upstream_source_owner",
    "upstream_rights_state",
    "dataset_id",
    "consumer_scope",
    "terms_uris",
    "terms_sha256s",
    "terms_observed_at",
    "terms_scope",
    "product_use_clause",
    "product_use_clause_sha256",
    "components",
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
    temporal_scope = _text(result.get("temporal_scope")).upper()
    if temporal_scope and temporal_scope != TEMPORAL_SCOPE_CURRENT_OBSERVATION:
        raise DataObjectAdmissionError("DATA_OBJECT_COMPILED_ADMISSION_TEMPORAL_SCOPE_INVALID")
    if temporal_scope == TEMPORAL_SCOPE_CURRENT_OBSERVATION:
        if (
            result.get("historical_pit_eligible") is not False
            or result.get("learning_admitted") is not False
            or result.get("strategy_upgrade_authority") is not False
            or result.get("independent_action_authority") is not False
        ):
            raise DataObjectAdmissionError("CURRENT_OBSERVATION_AUTHORITY_FORBIDDEN")
        if result.get("intended_use") != RIGHTS_EVIDENCE_PURPOSE_NO_SEND:
            raise DataObjectAdmissionError("CURRENT_OBSERVATION_NO_SEND_REQUIRED")
        observation_operation_id = _text(result.get("observation_operation_id"))
        if (
            not observation_operation_id
            or observation_operation_id != _text(result.get("operation_id"))
        ):
            raise DataObjectAdmissionError("CURRENT_OBSERVATION_OPERATION_MISMATCH")
        receipt_asof = _parse_aware_datetime(result.get("research_as_of"))
        if as_of is not None:
            ceiling = _as_of_ceiling(as_of)
            if ceiling is None or receipt_asof != ceiling:
                raise DataObjectAdmissionError("CURRENT_OBSERVATION_CONTEXT_MISMATCH")
        for clock_field in ("available_at", "observed_at"):
            if _parse_aware_datetime(result.get(clock_field)) > receipt_asof:
                raise DataObjectAdmissionError(
                    "CURRENT_OBSERVATION_OUTSIDE_RESEARCH_ASOF:" + clock_field
                )
        rights_clock = _text(result.get("rights_terms_observed_at"))
        if rights_clock and _parse_aware_datetime(rights_clock) > receipt_asof:
            raise DataObjectAdmissionError(
                "CURRENT_OBSERVATION_OUTSIDE_RESEARCH_ASOF:rights_terms_observed_at"
            )
        vintage = _parse_optional_vintage_timestamp(result.get("vintage"))
        if vintage is not None and vintage > receipt_asof:
            raise DataObjectAdmissionError(
                "CURRENT_OBSERVATION_OUTSIDE_RESEARCH_ASOF:vintage"
            )
        if _completed_through_after_asof(
            result.get("completed_through"), receipt_asof
        ):
            raise DataObjectAdmissionError(
                "CURRENT_OBSERVATION_OUTSIDE_RESEARCH_ASOF:completed_through"
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
        try:
            runtime_clocks_match = (
                _parse_aware_datetime(result.get("runtime_source_observed_at"))
                == _parse_aware_datetime(result.get("observed_at"))
                and _parse_aware_datetime(result.get("runtime_source_available_at"))
                == _parse_aware_datetime(result.get("available_at"))
            )
        except DataObjectAdmissionError:
            runtime_clocks_match = False
        if (
            result.get("runtime_provider_identity") != result.get("provider_identity")
            or result.get("runtime_provider_route") != result.get("provider_route")
            or result.get("runtime_completed_through") != result.get("completed_through")
            or _normalized_units(result.get("runtime_units"))
            != _normalized_units(result.get("units"))
            or _text(result.get("runtime_currency")).upper()
            != _text(result.get("currency")).upper()
            or _text(result.get("runtime_adjustment_basis")).lower()
            != _text(result.get("adjustment_basis")).lower()
            or result.get("runtime_revision") != result.get("revision")
            or result.get("runtime_vintage") != result.get("vintage")
            or not runtime_clocks_match
        ):
            raise DataObjectAdmissionError(
                "DATA_OBJECT_COMPILED_ADMISSION_RUNTIME_SEMANTICS_MISMATCH"
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
    if body["schema_version"] == CURRENT_OBSERVATION_SCHEMA_VERSION:
        if _text(body.get("temporal_scope")).upper() != TEMPORAL_SCOPE_CURRENT_OBSERVATION:
            raise DataObjectAdmissionError("CURRENT_OBSERVATION_SCOPE_INVALID")
        if body.get("historical_pit_eligible") is not False:
            raise DataObjectAdmissionError("CURRENT_OBSERVATION_PIT_FORBIDDEN")
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


def _normalized_rights_claim(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != _REQUIRED_RIGHTS_FIELDS:
        raise DataObjectAdmissionError("SOURCE_RIGHTS_EVIDENCE_RIGHTS_SCHEMA_MISMATCH")
    normalized = {
        key: _text(value.get(key)).upper()
        for key in _REQUIRED_RIGHTS_FIELDS
    }
    if any(item not in RIGHTS_VALUES for item in normalized.values()):
        raise DataObjectAdmissionError("SOURCE_RIGHTS_EVIDENCE_RIGHTS_VALUE_INVALID")
    return normalized


def _validate_product_use_clause(result: dict[str, Any]) -> None:
    product_use_clause = _text(result.get("product_use_clause"))
    if not product_use_clause or len(product_use_clause) > 1000:
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_PRODUCT_USE_CLAUSE_INVALID"
        )
    expected_clause_hash = hashlib.sha256(
        product_use_clause.encode("utf-8")
    ).hexdigest()
    if result.get("product_use_clause_sha256") != expected_clause_hash:
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_PRODUCT_USE_CLAUSE_HASH_MISMATCH"
        )
    result["product_use_clause"] = product_use_clause


def _validate_single_source_rights_evidence(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if set(value) != _REQUIRED_RIGHTS_EVIDENCE_FIELDS:
        raise DataObjectAdmissionError("SOURCE_RIGHTS_EVIDENCE_SCHEMA_MISMATCH")
    result = dict(value)
    if result.get("schema_version") != RIGHTS_EVIDENCE_SCHEMA_VERSION:
        raise DataObjectAdmissionError("SOURCE_RIGHTS_EVIDENCE_VERSION_MISMATCH")
    for key in (
        "operation_id", "intended_use", "provider_identity",
        "upstream_source_owner", "upstream_rights_state", "dataset_id",
        "consumer_scope", "terms_uri", "terms_observed_at",
    ):
        if not _text(result.get(key)):
            raise DataObjectAdmissionError(
                "SOURCE_RIGHTS_EVIDENCE_FIELD_MISSING:" + key
            )
    try:
        terms_parts = urlsplit(_text(result.get("terms_uri")))
        terms_host = (terms_parts.hostname or "").lower().rstrip(".")
        terms_path = (terms_parts.path or "").lower()
    except ValueError as exc:
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_TERMS_URI_INVALID"
        ) from exc
    if terms_parts.scheme.lower() != "https" or not terms_host:
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_TERMS_URI_INVALID"
        )
    if any(
        terms_host == host or terms_host.endswith("." + host)
        for host in _SOFTWARE_PACKAGE_METADATA_HOSTS
    ):
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_SOFTWARE_PACKAGE_METADATA_IS_NOT_DATA_TERMS"
        )
    claimed_terms_scope = _text(result.get("terms_scope")).upper()
    provider_technical_doc = any(
        terms_path.startswith(prefix)
        for prefix in _NON_TERMS_TECHNICAL_PATH_PREFIXES
    )
    legacy_markdown_doc = (
        terms_path.rstrip("/") == "/maincontent"
        and "file=" in terms_parts.query.lower()
        and ".md" in terms_parts.query.lower()
    )
    if (
        claimed_terms_scope == "OFFICIAL_PROVIDER_DATA_USE_STATEMENT"
        and (provider_technical_doc or legacy_markdown_doc)
    ):
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_TECHNICAL_DOCUMENTATION_IS_NOT_DATA_TERMS"
        )
    for key in (
        "terms_sha256", "product_use_clause_sha256", "evidence_hash",
    ):
        if not _is_sha256(result.get(key)):
            raise DataObjectAdmissionError(
                "SOURCE_RIGHTS_EVIDENCE_HASH_INVALID:" + key
            )
    terms_scope = claimed_terms_scope
    if terms_scope not in RIGHTS_TERMS_SCOPES - {
        "COMPOSITE_OPERATION_MATCHED_UPSTREAM_TERMS"
    }:
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_TERMS_SCOPE_INVALID"
        )
    _validate_product_use_clause(result)
    result["terms_scope"] = terms_scope
    state = _text(result.get("upstream_rights_state")).upper()
    if state not in RIGHTS_EVIDENCE_STATES:
        raise DataObjectAdmissionError("SOURCE_RIGHTS_EVIDENCE_STATE_INVALID")
    result["upstream_rights_state"] = state
    intended_use = _text(result.get("intended_use"))
    if intended_use not in RIGHTS_EVIDENCE_PURPOSES:
        raise DataObjectAdmissionError("SOURCE_RIGHTS_EVIDENCE_PURPOSE_INVALID")
    result["intended_use"] = intended_use
    normalized_rights = _normalized_rights_claim(result.get("rights"))
    result["rights"] = normalized_rights
    if terms_scope == "OFFICIAL_PROVIDER_DATA_USE_STATEMENT" and any(
        normalized_rights[key] != "FORBIDDEN"
        for key in ("storage", "training", "redistribution")
    ):
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_STATEMENT_CANNOT_GRANT_SECONDARY_RIGHTS"
        )
    _parse_aware_datetime(result["terms_observed_at"])
    expected_hash = sha256_payload({
        key: result[key] for key in result if key != "evidence_hash"
    })
    if result["evidence_hash"] != expected_hash:
        raise DataObjectAdmissionError("SOURCE_RIGHTS_EVIDENCE_HASH_MISMATCH")
    return result


def _rights_intersection(
    components: tuple[Mapping[str, Any], ...],
) -> dict[str, str]:
    result: dict[str, str] = {}
    for key in _REQUIRED_RIGHTS_FIELDS:
        values = tuple(component["rights"][key] for component in components)
        if len(set(values)) == 1:
            result[key] = values[0]
        elif "FORBIDDEN" in values:
            result[key] = "FORBIDDEN"
        elif all(value == "ALLOWED" for value in values):
            result[key] = "ALLOWED"
        elif all(value == "NOT_APPLICABLE" for value in values):
            result[key] = "NOT_APPLICABLE"
        else:
            result[key] = "UNKNOWN"
    return result


def _validate_source_rights_evidence_bundle(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if set(value) != _REQUIRED_RIGHTS_EVIDENCE_BUNDLE_FIELDS:
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_BUNDLE_SCHEMA_MISMATCH"
        )
    result = dict(value)
    if result.get("schema_version") != RIGHTS_EVIDENCE_BUNDLE_SCHEMA_VERSION:
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_BUNDLE_VERSION_MISMATCH"
        )
    for key in (
        "operation_id", "intended_use", "provider_identity",
        "upstream_source_owner", "upstream_rights_state", "dataset_id",
        "consumer_scope", "terms_observed_at",
    ):
        if not _text(result.get(key)):
            raise DataObjectAdmissionError(
                "SOURCE_RIGHTS_EVIDENCE_BUNDLE_FIELD_MISSING:" + key
            )
    if result.get("terms_scope") != (
        "COMPOSITE_OPERATION_MATCHED_UPSTREAM_TERMS"
    ):
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_BUNDLE_TERMS_SCOPE_INVALID"
        )
    _validate_product_use_clause(result)
    if not _is_sha256(result.get("product_use_clause_sha256")) or not _is_sha256(
        result.get("evidence_hash")
    ):
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_BUNDLE_HASH_INVALID"
        )
    raw_components = result.get("components")
    if (
        not isinstance(raw_components, (list, tuple))
        or not 2 <= len(raw_components) <= 8
    ):
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_BUNDLE_COMPONENTS_INVALID"
        )
    components: list[dict[str, Any]] = []
    for raw_component in raw_components:
        if (
            not isinstance(raw_component, Mapping)
            or raw_component.get("schema_version")
            != RIGHTS_EVIDENCE_SCHEMA_VERSION
        ):
            raise DataObjectAdmissionError(
                "SOURCE_RIGHTS_EVIDENCE_BUNDLE_COMPONENT_INVALID"
            )
        component = _validate_single_source_rights_evidence(raw_component)
        for key in (
            "operation_id", "intended_use", "dataset_id", "consumer_scope",
        ):
            if component[key] != result[key]:
                raise DataObjectAdmissionError(
                    "SOURCE_RIGHTS_EVIDENCE_BUNDLE_COMPONENT_SCOPE_MISMATCH"
                )
        if component["upstream_rights_state"] != "VERIFIED":
            raise DataObjectAdmissionError(
                "SOURCE_RIGHTS_EVIDENCE_BUNDLE_COMPONENT_UNVERIFIED"
            )
        components.append(component)
    component_tuple = tuple(components)
    providers = tuple(component["provider_identity"] for component in component_tuple)
    terms_uris = tuple(component["terms_uri"] for component in component_tuple)
    terms_sha256s = tuple(component["terms_sha256"] for component in component_tuple)
    if len(set(providers)) != len(providers) or len(set(terms_uris)) != len(terms_uris):
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_BUNDLE_COMPONENT_DUPLICATE"
        )
    if tuple(result.get("terms_uris") or ()) != terms_uris:
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_BUNDLE_TERMS_URI_MISMATCH"
        )
    if tuple(result.get("terms_sha256s") or ()) != terms_sha256s:
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_BUNDLE_TERMS_HASH_MISMATCH"
        )
    latest_terms = max(
        _parse_aware_datetime(component["terms_observed_at"])
        for component in component_tuple
    ).isoformat()
    if result["terms_observed_at"] != latest_terms:
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_BUNDLE_TERMS_CLOCK_MISMATCH"
        )
    state = _text(result.get("upstream_rights_state")).upper()
    if state != "VERIFIED":
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_BUNDLE_STATE_INVALID"
        )
    intended_use = _text(result.get("intended_use"))
    if intended_use not in RIGHTS_EVIDENCE_PURPOSES:
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_BUNDLE_PURPOSE_INVALID"
        )
    result["upstream_rights_state"] = state
    result["intended_use"] = intended_use
    expected_rights = _rights_intersection(component_tuple)
    normalized_rights = _normalized_rights_claim(result.get("rights"))
    if normalized_rights != expected_rights:
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_BUNDLE_RIGHTS_INTERSECTION_MISMATCH"
        )
    if (
        intended_use == RIGHTS_EVIDENCE_PURPOSE_NO_SEND
        and (
            normalized_rights["product_use"] != "ALLOWED"
            or normalized_rights["redistribution"] != "FORBIDDEN"
        )
    ):
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_BUNDLE_NO_SEND_RIGHTS_INVALID"
        )
    result["rights"] = normalized_rights
    result["components"] = component_tuple
    result["terms_uris"] = terms_uris
    result["terms_sha256s"] = terms_sha256s
    expected_hash = sha256_payload({
        key: result[key] for key in result if key != "evidence_hash"
    })
    if result["evidence_hash"] != expected_hash:
        raise DataObjectAdmissionError(
            "SOURCE_RIGHTS_EVIDENCE_BUNDLE_HASH_MISMATCH"
        )
    return result


def validate_source_rights_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DataObjectAdmissionError("SOURCE_RIGHTS_EVIDENCE_SCHEMA_MISMATCH")
    if value.get("schema_version") == RIGHTS_EVIDENCE_BUNDLE_SCHEMA_VERSION:
        return _validate_source_rights_evidence_bundle(value)
    return _validate_single_source_rights_evidence(value)


def validate_b1_rights_context(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one external operation-rights package without inventing a run clock."""
    required = {
        "schema_version", "operation_id", "intended_use",
        "allowed_instruments", "rights_by_dataset",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        raise DataObjectAdmissionError("B1_RIGHTS_CONTEXT_SCHEMA_MISMATCH")
    result = dict(value)
    if result.get("schema_version") != B1_RIGHTS_CONTEXT_SCHEMA_VERSION:
        raise DataObjectAdmissionError("B1_RIGHTS_CONTEXT_VERSION_MISMATCH")
    operation_id = _text(result.get("operation_id"))
    intended_use = _text(result.get("intended_use"))
    grants = result.get("rights_by_dataset")
    instruments = result.get("allowed_instruments")
    if not operation_id or intended_use != RIGHTS_EVIDENCE_PURPOSE_NO_SEND:
        raise DataObjectAdmissionError("B1_RIGHTS_CONTEXT_OPERATION_INVALID")
    if not isinstance(grants, Mapping) or not grants:
        raise DataObjectAdmissionError("B1_RIGHTS_CONTEXT_GRANTS_MISSING")
    if (
        not isinstance(instruments, (list, tuple))
        or not instruments
        or len(instruments) > 50
    ):
        raise DataObjectAdmissionError("B1_RIGHTS_CONTEXT_INSTRUMENTS_INVALID")
    normalized_instruments = tuple(
        dict.fromkeys(_text(item).upper() for item in instruments if _text(item))
    )
    if len(normalized_instruments) != len(instruments):
        raise DataObjectAdmissionError("B1_RIGHTS_CONTEXT_INSTRUMENTS_INVALID")
    normalized: dict[str, dict[str, Any]] = {}
    for dataset_id, raw_grant in grants.items():
        key = _text(dataset_id)
        if not key or not isinstance(raw_grant, Mapping):
            raise DataObjectAdmissionError("B1_RIGHTS_CONTEXT_GRANT_INVALID")
        grant = validate_source_rights_evidence(raw_grant)
        if (
            grant["dataset_id"] != key
            or grant["operation_id"] != operation_id
            or grant["intended_use"] != intended_use
            or grant["upstream_rights_state"] != "VERIFIED"
            or grant["rights"]["product_use"] != "ALLOWED"
            or grant["rights"]["redistribution"] != "FORBIDDEN"
        ):
            raise DataObjectAdmissionError("B1_RIGHTS_CONTEXT_GRANT_SCOPE_MISMATCH")
        normalized[key] = grant
    result["operation_id"] = operation_id
    result["intended_use"] = intended_use
    result["allowed_instruments"] = normalized_instruments
    result["rights_by_dataset"] = normalized
    result["rights_context_sha256"] = sha256_payload({
        key: result[key] for key in required
    })
    return result


def load_b1_rights_context(path: str | Path | None) -> dict[str, Any] | None:
    """Read one bounded UTF-8 JSON authorization package; no secret/token lookup."""
    if path is None:
        return None
    # Legacy tests and callers may expose an unspecified attribute through a
    # dynamic object (for example MagicMock). Only a real configured path turns
    # B1 on; never stringify arbitrary runtime objects into filesystem paths.
    if not isinstance(path, (str, Path)):
        return None
    raw_path = str(path).strip()
    if not raw_path:
        return None
    target = Path(raw_path).expanduser()
    try:
        payload = target.read_bytes()
    except OSError as exc:
        raise DataObjectAdmissionError("B1_RIGHTS_CONTEXT_FILE_UNREADABLE") from exc
    if not payload or len(payload) > 1024 * 1024:
        raise DataObjectAdmissionError("B1_RIGHTS_CONTEXT_FILE_SIZE_INVALID")
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DataObjectAdmissionError("B1_RIGHTS_CONTEXT_FILE_JSON_INVALID") from exc
    return validate_b1_rights_context(document)


def bind_b1_rights_context_as_of(
    context: Mapping[str, Any], research_as_of: datetime,
) -> dict[str, Any]:
    """Bind a validated external grant package to one exact aware run instant."""
    if not isinstance(research_as_of, datetime) or research_as_of.tzinfo is None or research_as_of.utcoffset() is None:
        raise DataObjectAdmissionError("B1_RESEARCH_ASOF_REQUIRES_AWARE_DATETIME")
    base = validate_b1_rights_context({
        key: context[key]
        for key in (
            "schema_version", "operation_id", "intended_use",
            "allowed_instruments", "rights_by_dataset",
        )
        if key in context
    })
    base["research_as_of"] = research_as_of.astimezone(timezone.utc).isoformat()
    return base


def validate_b1_provider_preflight(
    grant: Mapping[str, Any] | None,
    *,
    operation_id: str,
    intended_use: str,
    dataset_id: str,
    consumer_scope: str,
    research_as_of: str | datetime,
) -> dict[str, Any]:
    """Admit only a precisely scoped *supplied* upstream grant before fetching.

    The sealed claim is NOT an independent verification of its external terms.
    Its owner must supply independently checked upstream authorization; this
    code never invents terms bytes, vendor rights or a source observation.
    """
    if not isinstance(grant, Mapping):
        raise DataObjectAdmissionError("B1_UPSTREAM_RIGHTS_EVIDENCE_MISSING")
    try:
        rights = validate_source_rights_evidence(grant)
    except (DataObjectAdmissionError, ValueError) as exc:
        raise DataObjectAdmissionError("B1_UPSTREAM_RIGHTS_EVIDENCE_INVALID") from exc
    expected = {
        "operation_id": operation_id,
        "intended_use": intended_use,
        "dataset_id": dataset_id,
        "consumer_scope": consumer_scope,
    }
    if any(not _text(value) or rights.get(key) != value for key, value in expected.items()):
        raise DataObjectAdmissionError("B1_OPERATION_DATASET_CONSUMER_MISMATCH")
    if intended_use != RIGHTS_EVIDENCE_PURPOSE_NO_SEND:
        raise DataObjectAdmissionError("B1_ONLY_NO_SEND_RESEARCH_IS_ADMITTED")
    if rights["upstream_rights_state"] != "VERIFIED":
        raise DataObjectAdmissionError("B1_UPSTREAM_GRANT_UNVERIFIED")
    if (rights["rights"]["product_use"] != "ALLOWED"
        or rights["rights"]["redistribution"] != "FORBIDDEN"):
        raise DataObjectAdmissionError("B1_NO_SEND_PRODUCT_RIGHT_NOT_ADMITTED")
    terms_uris = (
        tuple(rights.get("terms_uris") or ())
        if rights.get("schema_version") == RIGHTS_EVIDENCE_BUNDLE_SCHEMA_VERSION
        else (rights.get("terms_uri"),)
    )
    if (
        not _text(rights["provider_identity"])
        or not terms_uris
        or any(not str(uri or "").startswith("https://") for uri in terms_uris)
    ):
        raise DataObjectAdmissionError("B1_PROVIDER_OR_TERMS_URI_INVALID")
    if isinstance(research_as_of, date) and not isinstance(research_as_of, datetime):
        raise DataObjectAdmissionError("B1_RESEARCH_ASOF_REQUIRES_INSTANT")
    if isinstance(research_as_of, str) and "T" not in research_as_of:
        raise DataObjectAdmissionError("B1_RESEARCH_ASOF_REQUIRES_INSTANT")
    try:
        ceiling = _as_of_ceiling(research_as_of)
        if ceiling is None:
            raise DataObjectAdmissionError("B1_RESEARCH_ASOF_MISSING")
        terms_after_as_of = _parse_aware_datetime(rights["terms_observed_at"]) > ceiling
    except (ValueError, TypeError) as exc:
        raise DataObjectAdmissionError("B1_RESEARCH_ASOF_INVALID") from exc
    # DataObjectAdmissionError subclasses ValueError; emitting this inside the
    # parsing try/except would mask a precise license timing veto as bad input.
    if terms_after_as_of:
        raise DataObjectAdmissionError("B1_TERMS_AFTER_RESEARCH_ASOF")
    return rights



def compile_b1_runtime_admission_receipts(
    obligations: list[Mapping[str, Any]] | tuple[Mapping[str, Any], ...],
    *,
    b1_context: Mapping[str, Any],
) -> dict[str, dict[str, Any]]:
    """Bind observed original-Registry rows to the external B1 grant package.

    Source metadata is produced by the existing MTF/Registry path.  A caller may
    supply an exact duplicate for independent comparison, but cannot supply a
    missing source, provider route, units, revision or vintage into existence.
    """
    if not isinstance(b1_context, Mapping):
        raise DataObjectAdmissionError("B1_OPERATION_CONTEXT_MISSING")
    operation_id = _text(b1_context.get("operation_id"))
    intended_use = _text(b1_context.get("intended_use"))
    as_of = b1_context.get("research_as_of")
    rights_by_dataset = b1_context.get("rights_by_dataset") or {}
    supplied_metadata = b1_context.get("source_metadata_by_dataset") or {}
    temporal_scope = _text(b1_context.get("temporal_scope")).upper()
    if temporal_scope not in {"", TEMPORAL_SCOPE_CURRENT_OBSERVATION}:
        raise DataObjectAdmissionError("B1_TEMPORAL_SCOPE_INVALID")
    current_research_asof = None
    if temporal_scope == TEMPORAL_SCOPE_CURRENT_OBSERVATION:
        if intended_use != RIGHTS_EVIDENCE_PURPOSE_NO_SEND:
            raise DataObjectAdmissionError("B1_CURRENT_OBSERVATION_NO_SEND_REQUIRED")
        try:
            current_research_asof = _parse_aware_datetime(as_of).isoformat()
        except DataObjectAdmissionError as exc:
            raise DataObjectAdmissionError(
                "B1_CURRENT_OBSERVATION_REQUIRES_EXACT_ASOF"
            ) from exc
    if not isinstance(rights_by_dataset, Mapping) or not isinstance(supplied_metadata, Mapping):
        raise DataObjectAdmissionError("B1_RECEIPT_PRODUCER_SCHEMA_INVALID")
    known = {
        _text(item.get("dataset_id")): dict(item)
        for item in obligations
        if item.get("state") != "NOT_APPLICABLE"
    }
    # The 76-object Registry is the closed world. Do not permanently privilege
    # four price objects: any applicable object may compile only after its
    # existing producer has supplied the same complete runtime identity and an
    # exact operation grant. Missing producers remain lawful nontriggers.
    if set(supplied_metadata) - set(known):
        raise DataObjectAdmissionError("B1_RECEIPT_PRODUCER_ORPHAN_SOURCE")

    receipts: dict[str, dict[str, Any]] = {}
    for dataset_id in sorted(set(rights_by_dataset) & set(known)):
        scope = known[dataset_id]
        required_source = (
            "provider_identity", "provider_route", "source_identity_hash",
            "source_content_sha256", "source_observed_at",
            "source_available_at", "completed_through",
        )
        # A lawful grant without an observed same-run source is a nontrigger.
        if any(not _text(scope.get(key)) for key in required_source):
            continue
        grant = validate_b1_provider_preflight(
            rights_by_dataset.get(dataset_id),
            operation_id=operation_id,
            intended_use=intended_use,
            dataset_id=dataset_id,
            consumer_scope=_text(scope.get("product_consumer")),
            research_as_of=as_of,
        )
        metadata = {
            "provider_identity": scope.get("provider_identity"),
            "provider_route": scope.get("provider_route"),
            "source_identity_hash": scope.get("source_identity_hash"),
            "source_content_sha256": scope.get("source_content_sha256"),
            "source_observed_at": scope.get("source_observed_at"),
            "source_available_at": scope.get("source_available_at"),
            "completed_through": scope.get("completed_through"),
            "units": scope.get("units"),
            "currency": scope.get("currency"),
            "adjustment_basis": scope.get("adjustment_basis"),
            "revision": scope.get("revision"),
            "vintage": scope.get("vintage"),
            "volume_unit": scope.get("volume_unit"),
            "amount_unit": scope.get("amount_unit"),
        }
        duplicate = supplied_metadata.get(dataset_id)
        if duplicate is not None:
            if not isinstance(duplicate, Mapping):
                raise DataObjectAdmissionError("B1_SOURCE_METADATA_INVALID")
            for key, source_value in metadata.items():
                if source_value not in (None, "", {}, []) and duplicate.get(key) != source_value:
                    raise DataObjectAdmissionError("B1_RUNTIME_METADATA_SOURCE_MISMATCH")
        if metadata["provider_identity"] != grant["provider_identity"]:
            raise DataObjectAdmissionError("B1_RUNTIME_PROVIDER_RIGHTS_MISMATCH")
        units = metadata.get("units")
        if not isinstance(units, Mapping) or not units:
            raise DataObjectAdmissionError("B1_RUNTIME_UNITS_MISSING")
        receipt_payload = {
            "schema_version": (
                CURRENT_OBSERVATION_SCHEMA_VERSION
                if temporal_scope == TEMPORAL_SCOPE_CURRENT_OBSERVATION
                else SCHEMA_VERSION
            ),
            "dataset_id": dataset_id,
            "provider_identity": grant["provider_identity"],
            "provider_route": metadata["provider_route"],
            "native_frequency": scope["native_frequency"],
            "source_identity_hash": metadata["source_identity_hash"],
            "content_sha256": metadata["source_content_sha256"],
            "observed_at": metadata["source_observed_at"],
            "available_at": metadata["source_available_at"],
            "completed_through": metadata["completed_through"],
            "units": dict(units),
            "currency": _text(metadata.get("currency")) or "UNKNOWN",
            "adjustment_basis": _text(metadata.get("adjustment_basis")) or "UNKNOWN",
            "revision": _text(metadata.get("revision")) or "UNKNOWN",
            "vintage": _text(metadata.get("vintage")) or "UNKNOWN",
            "rights": grant["rights"],
            "rights_evidence": grant,
            "historical_pit_eligible": False,
            "consumer_scope": scope["product_consumer"],
        }
        if temporal_scope == TEMPORAL_SCOPE_CURRENT_OBSERVATION:
            receipt_payload.update({
                "temporal_scope": TEMPORAL_SCOPE_CURRENT_OBSERVATION,
                "research_as_of": current_research_asof,
                "observation_operation_id": operation_id,
            })
        receipts[dataset_id] = seal_data_object_admission_receipt(receipt_payload)
    return receipts

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
        "runtime_provider_route": None,
        "runtime_source_observed_at": None,
        "runtime_source_available_at": None,
        "runtime_completed_through": None,
        "runtime_units": None,
        "runtime_currency": None,
        "runtime_adjustment_basis": None,
        "runtime_revision": None,
        "runtime_vintage": None,
        "rights_evidence_hash": None,
        "operation_id": None,
        "intended_use": None,
        "upstream_source_owner": None,
        "terms_uri": None,
        "terms_uris": None,
        "rights_terms_observed_at": None,
        "historical_pit_eligible": False,
        "strategy_upgrade_authority": False,
        "independent_action_authority": False,
        "receipt_hash": None,
        "temporal_scope": None,
        "research_as_of": None,
        "observation_operation_id": None,
    }
    if not applicable:
        base["reasons"] = ["DATA_OBJECT_NOT_APPLICABLE_TO_ASSET_ROUTE"]
        return _seal_compiled_admission(base)
    if receipt is None:
        base["reasons"] = ["DATA_OBJECT_ADMISSION_RECEIPT_MISSING"]
        return _seal_compiled_admission(base)
    normalized = validate_data_object_admission_receipt(receipt)
    temporal_scope = _text(normalized.get("temporal_scope")).upper() or None
    current_observation = temporal_scope == TEMPORAL_SCOPE_CURRENT_OBSERVATION
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
    version_semantics_complete = bool(
        current_observation
        or (
            _known_semantic(normalized["revision"])
            and _known_semantic(normalized["vintage"])
        )
    )
    semantic_complete = bool(
        units
        and all(value and value != "UNKNOWN" for value in unit_values)
        and _known_semantic(normalized["currency"])
        and _known_semantic(normalized["adjustment_basis"])
        and version_semantics_complete
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
    runtime_provider_route = _text(obligation.get("provider_route"))
    runtime_completed_through = _text(obligation.get("completed_through"))
    runtime_source_observed_at = _text(obligation.get("source_observed_at"))
    runtime_source_available_at = _text(obligation.get("source_available_at"))
    runtime_units = _normalized_units(obligation.get("units"))
    runtime_currency = _text(obligation.get("currency"))
    runtime_adjustment_basis = _text(obligation.get("adjustment_basis"))
    runtime_revision = _text(obligation.get("revision"))
    runtime_vintage = _text(obligation.get("vintage"))
    runtime_identity_complete = bool(
        _is_sha256(runtime_source_identity_hash)
        and _is_sha256(runtime_source_content_sha256)
    )
    runtime_semantics_complete = bool(
        runtime_provider_identity
        and runtime_provider_route
        and runtime_completed_through
        and runtime_source_observed_at
        and runtime_source_available_at
        and runtime_units
        and runtime_currency
        and runtime_adjustment_basis
        and runtime_revision
        and runtime_vintage
    )
    runtime_source_bound = runtime_identity_complete and runtime_semantics_complete
    if not runtime_identity_complete:
        reasons.append("RUNTIME_SOURCE_IDENTITY_OR_CONTENT_MISSING")
    if not runtime_semantics_complete:
        reasons.append("RUNTIME_SOURCE_SEMANTICS_MISSING")
    if runtime_identity_complete:
        if runtime_source_identity_hash != normalized["source_identity_hash"]:
            reasons.append("RUNTIME_SOURCE_IDENTITY_MISMATCH")
            runtime_source_bound = False
        if runtime_source_content_sha256 != normalized["content_sha256"]:
            reasons.append("RUNTIME_SOURCE_CONTENT_MISMATCH")
            runtime_source_bound = False
    if runtime_semantics_complete:
        if runtime_provider_identity != normalized["provider_identity"]:
            reasons.append("RUNTIME_SOURCE_PROVIDER_MISMATCH")
            runtime_source_bound = False
        if runtime_provider_route != normalized["provider_route"]:
            reasons.append("RUNTIME_SOURCE_PROVIDER_ROUTE_MISMATCH")
            runtime_source_bound = False
        if runtime_completed_through != normalized["completed_through"]:
            reasons.append("RUNTIME_SOURCE_COMPLETED_THROUGH_MISMATCH")
            runtime_source_bound = False
        if _parse_aware_datetime(runtime_source_observed_at) != observed_at:
            reasons.append("RUNTIME_SOURCE_OBSERVED_AT_MISMATCH")
            runtime_source_bound = False
        if _parse_aware_datetime(runtime_source_available_at) != available_at:
            reasons.append("RUNTIME_SOURCE_AVAILABLE_AT_MISMATCH")
            runtime_source_bound = False
        if runtime_units != _normalized_units(normalized["units"]):
            reasons.append("RUNTIME_SOURCE_UNITS_MISMATCH")
            runtime_source_bound = False
        if runtime_currency.upper() != _text(normalized["currency"]).upper():
            reasons.append("RUNTIME_SOURCE_CURRENCY_MISMATCH")
            runtime_source_bound = False
        if runtime_adjustment_basis.lower() != _text(normalized["adjustment_basis"]).lower():
            reasons.append("RUNTIME_SOURCE_ADJUSTMENT_BASIS_MISMATCH")
            runtime_source_bound = False
        if runtime_revision != _text(normalized["revision"]):
            reasons.append("RUNTIME_SOURCE_REVISION_MISMATCH")
            runtime_source_bound = False
        if runtime_vintage != _text(normalized["vintage"]):
            reasons.append("RUNTIME_SOURCE_VINTAGE_MISMATCH")
            runtime_source_bound = False
    expected_operation_id = _text(operation_id)
    expected_intended_use = _text(intended_use)
    current_context_valid = True
    if current_observation:
        receipt_asof = _parse_aware_datetime(normalized["research_as_of"])
        if (
            expected_intended_use != RIGHTS_EVIDENCE_PURPOSE_NO_SEND
            or normalized["observation_operation_id"] != expected_operation_id
            or ceiling is None
            or receipt_asof != ceiling
        ):
            reasons.append("CURRENT_OBSERVATION_CONTEXT_MISMATCH")
            current_context_valid = False
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
        and current_context_valid
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
        "runtime_provider_route": runtime_provider_route or None,
        "runtime_source_observed_at": runtime_source_observed_at or None,
        "runtime_source_available_at": runtime_source_available_at or None,
        "runtime_completed_through": runtime_completed_through or None,
        "runtime_units": dict(runtime_units) if runtime_units else None,
        "runtime_currency": runtime_currency or None,
        "runtime_adjustment_basis": runtime_adjustment_basis or None,
        "runtime_revision": runtime_revision or None,
        "runtime_vintage": runtime_vintage or None,
        "rights_evidence_hash": rights_evidence.get("evidence_hash") if isinstance(rights_evidence, Mapping) else None,
        "operation_id": rights_evidence.get("operation_id") if isinstance(rights_evidence, Mapping) else None,
        "intended_use": rights_evidence.get("intended_use") if isinstance(rights_evidence, Mapping) else None,
        "temporal_scope": temporal_scope,
        "research_as_of": normalized.get("research_as_of"),
        "observation_operation_id": normalized.get("observation_operation_id"),
        "upstream_source_owner": rights_evidence.get("upstream_source_owner") if isinstance(rights_evidence, Mapping) else None,
        "terms_uri": rights_evidence.get("terms_uri") if isinstance(rights_evidence, Mapping) else None,
        "terms_uris": (
            tuple(rights_evidence.get("terms_uris") or ())
            if isinstance(rights_evidence, Mapping)
            else None
        ),
        "terms_scope": rights_evidence.get("terms_scope") if isinstance(rights_evidence, Mapping) else None,
        "product_use_clause_sha256": rights_evidence.get("product_use_clause_sha256") if isinstance(rights_evidence, Mapping) else None,
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
    result = dict(value)
    schema_version = result.get("schema_version")
    if schema_version == SCHEMA_VERSION:
        allowed_fields = _REQUIRED_RECEIPT_FIELDS | _OPTIONAL_RECEIPT_FIELDS
        if not _REQUIRED_RECEIPT_FIELDS <= fields or fields - allowed_fields:
            raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_RECEIPT_SCHEMA_MISMATCH")
    elif schema_version == CURRENT_OBSERVATION_SCHEMA_VERSION:
        required_fields = _REQUIRED_RECEIPT_FIELDS | _CURRENT_RECEIPT_FIELDS
        allowed_fields = required_fields | _OPTIONAL_RECEIPT_FIELDS
        if not required_fields <= fields or fields - allowed_fields:
            raise DataObjectAdmissionError("DATA_OBJECT_ADMISSION_RECEIPT_SCHEMA_MISMATCH")
    else:
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
    if result["schema_version"] == CURRENT_OBSERVATION_SCHEMA_VERSION:
        if result.get("temporal_scope") != TEMPORAL_SCOPE_CURRENT_OBSERVATION:
            raise DataObjectAdmissionError("CURRENT_OBSERVATION_SCOPE_INVALID")
        if result.get("historical_pit_eligible") is not False:
            raise DataObjectAdmissionError("CURRENT_OBSERVATION_PIT_FORBIDDEN")
        if not _text(result.get("observation_operation_id")):
            raise DataObjectAdmissionError("CURRENT_OBSERVATION_OPERATION_MISSING")
        research_asof = _parse_aware_datetime(result.get("research_as_of"))
        if (
            _parse_aware_datetime(result["observed_at"]) > research_asof
            or _parse_aware_datetime(result["available_at"]) > research_asof
        ):
            raise DataObjectAdmissionError("CURRENT_OBSERVATION_AFTER_RESEARCH_ASOF")
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
            "current_observation_unknown_revision_never_grants_historical_pit": True,
            "no_strategy_or_action_authority": True,
        },
    }
    document["view_hash"] = sha256_payload(document)
    return document


def _normalized_units(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        _text(key): _text(item).upper()
        for key, item in value.items()
        if _text(key)
    }


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
