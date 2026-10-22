from __future__ import annotations

from copy import deepcopy

import pytest

from src.services.provider_operation_rights import (
    AKSHARE_STATEMENT_SHA256,
    NO_SEND_SCOPE,
    PERSONAL_RESEARCH_PURPOSE,
    ProviderOperationRightsError,
    compile_provider_operation_rights_evidence,
    validate_provider_operation_rights_evidence,
)


_ACTIONS = {
    "product_use": True,
    "storage": False,
    "training": False,
    "redistribution": False,
}
_OBSERVED = "2026-10-22T11:06:28.499244+00:00"


@pytest.mark.parametrize(
    "route",
    (
        "akshare.stock_zh_a_hist",
        "akshare.fund_etf_hist_em",
        "akshare.stock_zh_a_hist_min_em",
        "akshare.fund_etf_hist_min_em",
    ),
)
def test_pinned_akshare_academic_no_send_operation_is_product_only(route):
    evidence = compile_provider_operation_rights_evidence(
        provider_identity="AkshareFetcher",
        provider_route=route,
        operation_purpose=PERSONAL_RESEARCH_PURPOSE,
        delivery_scope=NO_SEND_SCOPE,
        requested_actions=_ACTIONS,
        commercial_use=False,
        observed_at=_OBSERVED,
    )
    assert evidence["status"] == "PRODUCT_USE_ALLOWED_BOUNDED"
    assert evidence["rights"] == {
        "product_use": "ALLOWED",
        "storage": "FORBIDDEN",
        "training": "FORBIDDEN",
        "redistribution": "FORBIDDEN",
    }
    assert evidence["upstream_statement_sha256"] == AKSHARE_STATEMENT_SHA256
    assert evidence["underlying_source_terms_state"] == "NOT_INDEPENDENTLY_VERIFIED"
    assert evidence["strategy_upgrade_authority"] is False
    assert evidence["independent_action_authority"] is False
    assert validate_provider_operation_rights_evidence(evidence) == evidence


def test_pinned_baostock_private_research_no_send_is_product_only():
    evidence = compile_provider_operation_rights_evidence(
        provider_identity="BaostockFetcher",
        provider_route="baostock.query_history_k_data_plus",
        operation_purpose=PERSONAL_RESEARCH_PURPOSE,
        delivery_scope=NO_SEND_SCOPE,
        requested_actions=_ACTIONS,
        commercial_use=False,
        observed_at=_OBSERVED,
    )
    assert evidence["status"] == "PRODUCT_USE_ALLOWED_BOUNDED"
    assert evidence["rights"] == {
        "product_use": "ALLOWED",
        "storage": "FORBIDDEN",
        "training": "FORBIDDEN",
        "redistribution": "FORBIDDEN",
    }
    assert evidence["upstream_commit"] == "PYPI_RELEASE_0.9.4"
    assert evidence["underlying_source_terms_state"].startswith("PROVIDER_OWN_DATA_SERVER")
    assert validate_provider_operation_rights_evidence(evidence) == evidence


@pytest.mark.parametrize(
    "actions,commercial,delivery",
    (
        ({**_ACTIONS, "storage": True}, False, NO_SEND_SCOPE),
        ({**_ACTIONS, "training": True}, False, NO_SEND_SCOPE),
        ({**_ACTIONS, "redistribution": True}, False, NO_SEND_SCOPE),
        (_ACTIONS, True, NO_SEND_SCOPE),
        (_ACTIONS, False, "EMAIL_SEND"),
    ),
)
def test_scope_expansion_is_rejected(actions, commercial, delivery):
    evidence = compile_provider_operation_rights_evidence(
        provider_identity="AkshareFetcher",
        provider_route="akshare.stock_zh_a_hist",
        operation_purpose=PERSONAL_RESEARCH_PURPOSE,
        delivery_scope=delivery,
        requested_actions=actions,
        commercial_use=commercial,
        observed_at=_OBSERVED,
    )
    assert evidence["status"].startswith("NOT_ADMITTED")
    assert set(evidence["rights"].values()) == {"FORBIDDEN"}


def test_unpinned_provider_or_route_remains_unknown():
    for provider, route in (
        ("BaostockFetcher", "baostock.unpinned_route"),
        ("AkshareFetcher", "akshare.unpinned_route"),
    ):
        evidence = compile_provider_operation_rights_evidence(
            provider_identity=provider,
            provider_route=route,
            operation_purpose=PERSONAL_RESEARCH_PURPOSE,
            delivery_scope=NO_SEND_SCOPE,
            requested_actions=_ACTIONS,
            commercial_use=False,
            observed_at=_OBSERVED,
        )
        assert evidence["status"] == "UNKNOWN_NO_PINNED_OPERATION_POLICY"
        assert set(evidence["rights"].values()) == {"UNKNOWN"}


def test_policy_or_evidence_tamper_is_detected():
    evidence = compile_provider_operation_rights_evidence(
        provider_identity="AkshareFetcher",
        provider_route="akshare.fund_etf_hist_em",
        operation_purpose=PERSONAL_RESEARCH_PURPOSE,
        delivery_scope=NO_SEND_SCOPE,
        requested_actions=_ACTIONS,
        commercial_use=False,
        observed_at=_OBSERVED,
    )
    for key, value in (
        ("upstream_statement", "FORGED"),
        ("upstream_document_sha256", "0" * 64),
        ("provider_route", "akshare.unpinned_route"),
        ("evidence_hash", "0" * 64),
    ):
        forged = deepcopy(evidence)
        forged[key] = value
        with pytest.raises(ProviderOperationRightsError):
            validate_provider_operation_rights_evidence(forged)
