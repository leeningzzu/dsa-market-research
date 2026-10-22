# -*- coding: utf-8 -*-
"""Operation-specific provider rights evidence for the sole DSA Product path.

This module records a narrow, pinned upstream policy observation.  It is not a
legal opinion, provider, database, report, strategy or model authority.  Only
an exact private academic-research NO_SEND operation may receive bounded
Product-use admission; storage, training and redistribution remain forbidden.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any

from src.services.pit_identity import sha256_payload


SCHEMA_VERSION = "provider-operation-rights-evidence-v1"
AKSHARE_UPSTREAM_COMMIT = "fe10d1decb705e5f43cc8ca3339ca39e580fb8f4"
AKSHARE_README_URL = (
    "https://raw.githubusercontent.com/akfamily/akshare/"
    + AKSHARE_UPSTREAM_COMMIT
    + "/README.md"
)
AKSHARE_README_SHA256 = "13b44c5e7b68e76071ebea0cb910587e3f8988c3b1ea06aa86590cbff081c9e2"
AKSHARE_ACADEMIC_STATEMENT = (
    "1. All data provided by [AKShare](https://github.com/akfamily/akshare) "
    "is just for academic research purpose;"
)
AKSHARE_STATEMENT_SHA256 = "25e70bb3e09d5985177cce4a20acf9ea7f24fd54ec752aea8deb8d8182c31a6b"
BAOSTOCK_RELEASE = "0.9.4"
BAOSTOCK_PYPI_URL = "https://pypi.org/pypi/baostock/0.9.4/json"
BAOSTOCK_DESCRIPTION_SHA256 = "b9e1750f27b6348925ba7b29c062a14afa7e618ea1f7300567d36a7a2e048693"
BAOSTOCK_RESEARCH_STATEMENT = (
    "We have our own data server, efficient and stable operation | "
    "Free china stock market data | Target Users: China Financial Market Analyst; "
    "Financial data analysis enthusiasts; Quanters who are interested in china stock market"
)
BAOSTOCK_STATEMENT_SHA256 = "e9e4de318c5b0cc26252e215b981b5ae79ce5b3ee6ad3f682ef55e89c342e9d7"
PERSONAL_RESEARCH_PURPOSE = "PERSONAL_ACADEMIC_RESEARCH_NO_SEND"
NO_SEND_SCOPE = "NO_SEND_LOCAL_RENDER_ONLY"
TEST_PURPOSE = "DETERMINISTIC_TEST_FIXTURE_NO_SEND"
TEST_SCOPE = "NO_SEND_TEST_ONLY"
_ALLOWED_AKSHARE_ROUTES = frozenset(
    {
        "akshare.stock_zh_a_hist",
        "akshare.fund_etf_hist_em",
        "akshare.stock_zh_a_hist_min_em",
        "akshare.fund_etf_hist_min_em",
    }
)
_ALLOWED_BAOSTOCK_ROUTES = frozenset({"baostock.query_history_k_data_plus"})
_RIGHTS_KEYS = ("product_use", "storage", "training", "redistribution")
_EVIDENCE_KEYS = frozenset(
    {
        "schema_version",
        "status",
        "provider_identity",
        "provider_route",
        "operation_purpose",
        "delivery_scope",
        "requested_actions",
        "commercial_use",
        "rights",
        "upstream_commit",
        "upstream_document_url",
        "upstream_document_sha256",
        "upstream_statement",
        "upstream_statement_sha256",
        "observed_at",
        "policy_interpretation",
        "underlying_source_terms_state",
        "strategy_upgrade_authority",
        "independent_action_authority",
        "evidence_hash",
    }
)


class ProviderOperationRightsError(ValueError):
    """Raised when provider-rights evidence is malformed or altered."""


def compile_provider_operation_rights_evidence(
    *,
    provider_identity: str,
    provider_route: str,
    operation_purpose: str,
    delivery_scope: str,
    requested_actions: Mapping[str, Any],
    commercial_use: bool,
    observed_at: str | datetime,
) -> dict[str, Any]:
    """Compile one bounded operation decision from pinned policy evidence."""
    provider = str(provider_identity or "").strip()
    route = str(provider_route or "").strip()
    purpose = str(operation_purpose or "").strip().upper()
    delivery = str(delivery_scope or "").strip().upper()
    actions = _normalize_actions(requested_actions)
    observed = _aware_utc_text(observed_at)

    is_test = provider.startswith("synthetic-lawful-provider")
    exact_akshare = provider == "AkshareFetcher" and route in _ALLOWED_AKSHARE_ROUTES
    exact_baostock = provider == "BaostockFetcher" and route in _ALLOWED_BAOSTOCK_ROUTES
    bounded_request = bool(
        actions["product_use"]
        and not actions["storage"]
        and not actions["training"]
        and not actions["redistribution"]
        and not commercial_use
    )

    test_learning_request = bool(
        actions == {
            "product_use": True,
            "storage": True,
            "training": True,
            "redistribution": False,
        }
        and not commercial_use
    )
    if is_test and purpose == TEST_PURPOSE and delivery == TEST_SCOPE and (
        bounded_request or test_learning_request
    ):
        status = (
            "LEARNING_USE_ALLOWED_TEST_ONLY"
            if test_learning_request
            else "PRODUCT_USE_ALLOWED_TEST_ONLY"
        )
        rights = (
            {
                "product_use": "ALLOWED",
                "storage": "ALLOWED",
                "training": "ALLOWED",
                "redistribution": "FORBIDDEN",
            }
            if test_learning_request
            else _product_only_rights()
        )
        upstream = {
            "upstream_commit": "TEST_FIXTURE_ONLY",
            "upstream_document_url": "test://synthetic-lawful-provider",
            "upstream_document_sha256": "0" * 64,
            "upstream_statement": "DETERMINISTIC TEST FIXTURE; NO EXTERNAL RIGHTS CLAIM",
            "upstream_statement_sha256": sha256_payload(
                "DETERMINISTIC TEST FIXTURE; NO EXTERNAL RIGHTS CLAIM"
            ),
            "policy_interpretation": "TEST_FIXTURE_ONLY_NO_EXTERNAL_RIGHTS_CLAIM",
            "underlying_source_terms_state": "NOT_APPLICABLE_TEST_FIXTURE",
        }
    elif exact_akshare or exact_baostock:
        upstream = _akshare_upstream() if exact_akshare else _baostock_upstream()
        if purpose == PERSONAL_RESEARCH_PURPOSE and delivery == NO_SEND_SCOPE and bounded_request:
            status = "PRODUCT_USE_ALLOWED_BOUNDED"
            rights = _product_only_rights()
        else:
            status = "NOT_ADMITTED_OPERATION_OUTSIDE_RESEARCH_NO_SEND_SCOPE"
            rights = {key: "FORBIDDEN" for key in _RIGHTS_KEYS}
    else:
        status = "UNKNOWN_NO_PINNED_OPERATION_POLICY"
        rights = {key: "UNKNOWN" for key in _RIGHTS_KEYS}
        upstream = {
            "upstream_commit": None,
            "upstream_document_url": None,
            "upstream_document_sha256": None,
            "upstream_statement": None,
            "upstream_statement_sha256": None,
            "policy_interpretation": "NO_OPERATION_SPECIFIC_POLICY_EVIDENCE",
            "underlying_source_terms_state": "UNKNOWN",
        }

    body = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "provider_identity": provider,
        "provider_route": route,
        "operation_purpose": purpose,
        "delivery_scope": delivery,
        "requested_actions": actions,
        "commercial_use": bool(commercial_use),
        "rights": rights,
        **upstream,
        "observed_at": observed,
        "strategy_upgrade_authority": False,
        "independent_action_authority": False,
    }
    body["evidence_hash"] = sha256_payload(body)
    return validate_provider_operation_rights_evidence(body)


def validate_provider_operation_rights_evidence(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _EVIDENCE_KEYS:
        raise ProviderOperationRightsError("PROVIDER_RIGHTS_EVIDENCE_SCHEMA_MISMATCH")
    result = dict(value)
    evidence_hash = str(result.pop("evidence_hash", "")).lower()
    if len(evidence_hash) != 64 or sha256_payload(result) != evidence_hash:
        raise ProviderOperationRightsError("PROVIDER_RIGHTS_EVIDENCE_HASH_MISMATCH")
    if result.get("schema_version") != SCHEMA_VERSION:
        raise ProviderOperationRightsError("PROVIDER_RIGHTS_EVIDENCE_VERSION_MISMATCH")
    actions = _normalize_actions(result.get("requested_actions"))
    rights = result.get("rights")
    if not isinstance(rights, Mapping) or set(rights) != set(_RIGHTS_KEYS):
        raise ProviderOperationRightsError("PROVIDER_RIGHTS_VALUES_SCHEMA_MISMATCH")
    normalized_rights = {key: str(rights.get(key) or "").strip().upper() for key in _RIGHTS_KEYS}
    if any(item not in {"ALLOWED", "FORBIDDEN", "UNKNOWN"} for item in normalized_rights.values()):
        raise ProviderOperationRightsError("PROVIDER_RIGHTS_VALUE_INVALID")
    _aware_utc_text(result.get("observed_at"))
    if type(result.get("commercial_use")) is not bool:
        raise ProviderOperationRightsError("PROVIDER_RIGHTS_COMMERCIAL_FLAG_INVALID")
    for key in ("strategy_upgrade_authority", "independent_action_authority"):
        if result.get(key) is not False:
            raise ProviderOperationRightsError("PROVIDER_RIGHTS_AUTHORITY_ESCALATION:" + key)

    status = str(result.get("status") or "")
    if status == "PRODUCT_USE_ALLOWED_BOUNDED":
        provider = result.get("provider_identity")
        route = result.get("provider_route")
        if provider == "AkshareFetcher" and route in _ALLOWED_AKSHARE_ROUTES:
            expected_upstream = _akshare_upstream()
        elif provider == "BaostockFetcher" and route in _ALLOWED_BAOSTOCK_ROUTES:
            expected_upstream = _baostock_upstream()
        else:
            raise ProviderOperationRightsError("PROVIDER_RIGHTS_ROUTE_NOT_PINNED")
        if result.get("operation_purpose") != PERSONAL_RESEARCH_PURPOSE or result.get("delivery_scope") != NO_SEND_SCOPE:
            raise ProviderOperationRightsError("PROVIDER_RIGHTS_OPERATION_SCOPE_MISMATCH")
        for key, expected_value in expected_upstream.items():
            if result.get(key) != expected_value:
                raise ProviderOperationRightsError("PROVIDER_RIGHTS_UPSTREAM_EVIDENCE_MISMATCH:" + key)
        if actions != {"product_use": True, "storage": False, "training": False, "redistribution": False}:
            raise ProviderOperationRightsError("PROVIDER_RIGHTS_ACTION_SCOPE_MISMATCH")
        if result.get("commercial_use") is not False or normalized_rights != _product_only_rights():
            raise ProviderOperationRightsError("PROVIDER_RIGHTS_BOUNDED_RESULT_MISMATCH")
    elif status in {
        "PRODUCT_USE_ALLOWED_TEST_ONLY",
        "LEARNING_USE_ALLOWED_TEST_ONLY",
    }:
        if not str(result.get("provider_identity") or "").startswith("synthetic-lawful-provider"):
            raise ProviderOperationRightsError("PROVIDER_RIGHTS_TEST_PROVIDER_INVALID")
        if result.get("operation_purpose") != TEST_PURPOSE or result.get("delivery_scope") != TEST_SCOPE:
            raise ProviderOperationRightsError("PROVIDER_RIGHTS_TEST_SCOPE_INVALID")
        expected = (
            {
                "product_use": "ALLOWED",
                "storage": "ALLOWED",
                "training": "ALLOWED",
                "redistribution": "FORBIDDEN",
            }
            if status == "LEARNING_USE_ALLOWED_TEST_ONLY"
            else _product_only_rights()
        )
        if normalized_rights != expected:
            raise ProviderOperationRightsError("PROVIDER_RIGHTS_TEST_RESULT_INVALID")
    elif status.startswith("NOT_ADMITTED"):
        if any(value != "FORBIDDEN" for value in normalized_rights.values()):
            raise ProviderOperationRightsError("PROVIDER_RIGHTS_REJECT_RESULT_INVALID")
    elif status == "UNKNOWN_NO_PINNED_OPERATION_POLICY":
        if any(value != "UNKNOWN" for value in normalized_rights.values()):
            raise ProviderOperationRightsError("PROVIDER_RIGHTS_UNKNOWN_RESULT_INVALID")
    else:
        raise ProviderOperationRightsError("PROVIDER_RIGHTS_STATUS_INVALID")

    result["requested_actions"] = actions
    result["rights"] = normalized_rights
    result["evidence_hash"] = evidence_hash
    return result


def _akshare_upstream() -> dict[str, Any]:
    return {
        "upstream_commit": AKSHARE_UPSTREAM_COMMIT,
        "upstream_document_url": AKSHARE_README_URL,
        "upstream_document_sha256": AKSHARE_README_SHA256,
        "upstream_statement": AKSHARE_ACADEMIC_STATEMENT,
        "upstream_statement_sha256": AKSHARE_STATEMENT_SHA256,
        "policy_interpretation": (
            "CONSERVATIVE_OPERATIONAL_CLASSIFICATION_NOT_LEGAL_OPINION; "
            "ACADEMIC_RESEARCH_NO_SEND_ONLY"
        ),
        "underlying_source_terms_state": "NOT_INDEPENDENTLY_VERIFIED",
    }


def _baostock_upstream() -> dict[str, Any]:
    return {
        "upstream_commit": "PYPI_RELEASE_" + BAOSTOCK_RELEASE,
        "upstream_document_url": BAOSTOCK_PYPI_URL,
        "upstream_document_sha256": BAOSTOCK_DESCRIPTION_SHA256,
        "upstream_statement": BAOSTOCK_RESEARCH_STATEMENT,
        "upstream_statement_sha256": BAOSTOCK_STATEMENT_SHA256,
        "policy_interpretation": (
            "CONSERVATIVE_OPERATIONAL_CLASSIFICATION_NOT_LEGAL_OPINION; "
            "PROVIDER_OWN_SERVER_FREE_MARKET_DATA_PERSONAL_RESEARCH_NO_SEND_ONLY"
        ),
        "underlying_source_terms_state": "PROVIDER_OWN_DATA_SERVER_CLAIM_NO_REDISTRIBUTION_GRANT",
    }


def _product_only_rights() -> dict[str, str]:
    return {
        "product_use": "ALLOWED",
        "storage": "FORBIDDEN",
        "training": "FORBIDDEN",
        "redistribution": "FORBIDDEN",
    }


def _normalize_actions(value: Any) -> dict[str, bool]:
    if not isinstance(value, Mapping) or set(value) != set(_RIGHTS_KEYS):
        raise ProviderOperationRightsError("PROVIDER_RIGHTS_ACTIONS_SCHEMA_MISMATCH")
    if any(type(value.get(key)) is not bool for key in _RIGHTS_KEYS):
        raise ProviderOperationRightsError("PROVIDER_RIGHTS_ACTION_FLAG_INVALID")
    return {key: bool(value[key]) for key in _RIGHTS_KEYS}


def _aware_utc_text(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError as exc:
            raise ProviderOperationRightsError("PROVIDER_RIGHTS_OBSERVED_AT_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProviderOperationRightsError("PROVIDER_RIGHTS_OBSERVED_AT_NAIVE")
    return parsed.astimezone(timezone.utc).isoformat()
