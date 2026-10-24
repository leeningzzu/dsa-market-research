from __future__ import annotations

from copy import deepcopy

import pytest

from src.services.data_object_admission import (
    compile_data_object_admission,
    seal_data_object_admission_receipt,
    seal_source_rights_evidence,
)
from src.services.fundamental_quality_service import (
    FIELD_SOURCES,
    REQUIRED_DATASET_IDS,
    FundamentalQualityError,
    compile_fundamental_quality,
    validate_fundamental_quality,
)


_META = {
    "P_FIN_VINTAGE": "quarter+event",
    "P_EARNINGS_GROWTH": "quarter+event",
    "P_CASH_FLOW": "quarter+event",
    "P_BALANCE_SHEET": "quarter+event",
}


def _admission(dataset_id: str, **receipt_overrides):
    frequency = _META[dataset_id]
    rights = {
        "product_use": "ALLOWED",
        "storage": "FORBIDDEN",
        "training": "FORBIDDEN",
        "redistribution": "FORBIDDEN",
    }
    rights_evidence = seal_source_rights_evidence({
        "schema_version": "source-rights-evidence-v1",
        "operation_id": "op-quality-nosend-20261016-r001",
        "intended_use": "NO_SEND_RESEARCH",
        "provider_identity": "synthetic-lawful-provider",
        "upstream_source_owner": "synthetic-licensed-upstream",
        "upstream_rights_state": "VERIFIED",
        "dataset_id": dataset_id,
        "consumer_scope": "ASSET_RESEARCH_BRIEF",
        "terms_uri": "https://example.invalid/terms/synthetic",
        "terms_sha256": "c" * 64,
        "terms_observed_at": "2026-10-15T07:00:00+00:00",
        "rights": rights,
    })
    receipt = seal_data_object_admission_receipt({
        "schema_version": "data-object-admission-receipt-v1",
        "dataset_id": dataset_id,
        "provider_identity": "synthetic-lawful-provider",
        "provider_route": "synthetic." + dataset_id.lower(),
        "native_frequency": frequency,
        "source_identity_hash": "a" * 64,
        "content_sha256": "b" * 64,
        "observed_at": "2026-10-16T10:00:00+00:00",
        "available_at": "2026-10-15T08:00:00+00:00",
        "completed_through": "2026-09-30",
        "units": {"value": "CNY", "ratio": "PERCENT_OR_RATIO"},
        "currency": "CNY",
        "adjustment_basis": "NOT_APPLICABLE",
        "revision": "ORIGINAL_DISCLOSURE",
        "vintage": "2026-10-15T08:00:00+00:00",
        "rights": rights,
        "rights_evidence": rights_evidence,
        "historical_pit_eligible": False,
        "consumer_scope": "ASSET_RESEARCH_BRIEF",
        **receipt_overrides,
    })
    obligation = {
        "dataset_id": dataset_id,
        "native_frequency": frequency,
        "product_consumer": "ASSET_RESEARCH_BRIEF",
        "state": "UNKNOWN",
        "provider_identity": receipt["provider_identity"],
        "provider_route": receipt["provider_route"],
        "source_identity_hash": receipt["source_identity_hash"],
        "source_content_sha256": receipt["content_sha256"],
        "source_observed_at": receipt["observed_at"],
        "source_available_at": receipt["available_at"],
        "completed_through": receipt["completed_through"],
        "units": dict(receipt["units"]),
        "currency": receipt["currency"],
        "adjustment_basis": receipt["adjustment_basis"],
        "revision": receipt["revision"],
        "vintage": receipt["vintage"],
    }
    return compile_data_object_admission(
        obligation,
        receipt,
        as_of="2026-10-16T23:59:59+00:00",
        operation_id="op-quality-nosend-20261016-r001",
        intended_use="NO_SEND_RESEARCH",
    )


def _payload(**overrides):
    metrics = {
        "revenue_growth_yoy_pct": 12.5,
        "net_profit_growth_yoy_pct": 9.2,
        "gross_margin_pct": 42.1,
        "operating_cash_flow": 120.0,
        "net_income": 100.0,
        "total_debt": 300.0,
        "total_assets": 1000.0,
    }
    value = {
        "asset_id": "600519",
        "asset_type": "stock",
        "currency": "CNY",
        "as_of": "2026-10-16T23:59:59+00:00",
        "financial_period_end": "2026-09-30",
        "published_at": "2026-10-15T08:00:00+00:00",
        "financial_version": "2026Q3_ORIGINAL",
        "data_admissions": [_admission(dataset_id) for dataset_id in REQUIRED_DATASET_IDS],
        "metrics": metrics,
        "metric_sources": {key: FIELD_SOURCES[key] for key in metrics},
    }
    value.update(overrides)
    return value


def test_quality_growth_is_transparent_product_fact_not_score_or_action():
    result = compile_fundamental_quality(_payload())
    assert result["status"] == "READY"
    assert len(result["config_hash"]) == 64
    assert result["effective_parameters"]["composite_score"] is False
    assert result["metrics"]["cash_conversion_ratio"] == 1.2
    assert result["metrics"]["debt_to_assets_ratio"] == 0.3
    assert result["metrics"]["growth_direction"] == "BOTH_POSITIVE"
    assert "收入同比 12.50%" in result["summary_zh"]
    assert "不构成综合质量分" in result["summary_zh"]
    assert result["product_admitted"] is True
    assert result["learning_admitted"] is False
    assert result["strategy_upgrade_authority"] is False
    assert result["veto_override_authority"] is False
    assert result["independent_action_authority"] is False
    assert "score" not in result
    assert len(result["receipt_hash"]) == 64
    assert validate_fundamental_quality(result) == result


def test_future_financials_and_unknown_product_rights_fail_closed():
    future = compile_fundamental_quality(
        _payload(published_at="2026-10-17T08:00:00+00:00"),
        as_of="2026-10-16T23:59:59+00:00",
    )
    assert future["status"] == "NOT_ADMITTED"
    assert "FINANCIAL_INFORMATION_NOT_KNOWN_AT_ASOF" in future["reason_codes"]

    admissions = list(_payload()["data_admissions"])
    unknown = _admission(
        "P_CASH_FLOW",
        rights={
            "product_use": "UNKNOWN", "storage": "UNKNOWN",
            "training": "UNKNOWN", "redistribution": "UNKNOWN",
        },
    )
    admissions[REQUIRED_DATASET_IDS.index("P_CASH_FLOW")] = unknown
    blocked = compile_fundamental_quality(_payload(data_admissions=admissions))
    assert blocked["status"] == "NOT_ADMITTED"
    assert "QUALITY_DATA_NOT_PRODUCT_ADMITTED" in blocked["reason_codes"]


def test_missing_or_wrong_lineage_never_becomes_ready():
    missing = _payload()
    missing["metrics"] = dict(missing["metrics"])
    missing["metric_sources"] = dict(missing["metric_sources"])
    missing["metrics"].pop("total_assets")
    missing["metric_sources"].pop("total_assets")
    result = compile_fundamental_quality(missing)
    assert result["status"] == "MISSING"
    assert result["reason_codes"] == ("QUALITY_REQUIRED_METRIC_MISSING:total_assets",)

    wrong = _payload()
    wrong["metric_sources"] = dict(wrong["metric_sources"])
    wrong["metric_sources"]["operating_cash_flow"] = "P_EARNINGS_GROWTH"
    result = compile_fundamental_quality(wrong)
    assert result["status"] == "NOT_ADMITTED"
    assert result["reason_codes"] == ("QUALITY_FIELD_LINEAGE_MISMATCH",)


def test_non_positive_net_income_is_reported_not_fabricated_as_good_or_bad_ratio():
    payload = _payload()
    payload["metrics"] = dict(payload["metrics"], net_income=-10.0)
    result = compile_fundamental_quality(payload)
    assert result["status"] == "READY"
    assert result["metrics"]["cash_conversion_ratio"] is None
    assert result["metrics"]["cash_conversion_state"] == (
        "NOT_COMPARABLE_NON_POSITIVE_NET_INCOME"
    )
    assert "不可比（净利润非正）" in result["summary_zh"]


def test_etf_or_tampered_ready_receipt_is_rejected():
    etf = compile_fundamental_quality(_payload(asset_type="etf"))
    assert etf["status"] == "NOT_ADMITTED"
    assert "QUALITY_ASSET_TYPE_NOT_STOCK" in etf["reason_codes"]

    ready = compile_fundamental_quality(_payload())
    forged = deepcopy(ready)
    forged["metrics"]["revenue_growth_yoy_pct"] = 999.0
    with pytest.raises(FundamentalQualityError, match="RECEIPT_STALE"):
        validate_fundamental_quality(forged)

def test_later_observed_quality_admission_cannot_be_reused_for_earlier_research():
    payload = _payload()
    assert compile_fundamental_quality(payload)["status"] == "READY"
    # Financial publication was October 15, but the exact input receipts
    # were observed October 16 at 10:00 UTC, later than this frozen as-of.
    result = compile_fundamental_quality(
        payload, as_of="2026-10-16T09:00:00+00:00",
    )
    assert result["status"] == "NOT_ADMITTED"
    assert "QUALITY_DATA_NOT_KNOWN_AT_ASOF" in result["reason_codes"]
    assert result["learning_admitted"] is False
