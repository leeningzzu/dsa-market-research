from __future__ import annotations

from copy import deepcopy

import pytest

from src.services.business_valuation_service import (
    BusinessValuationError,
    VALUATION_ROUTE_DATA_REQUIREMENTS,
    VALUATION_ROUTE_FIELD_SOURCES,
    VALUATION_ROUTE_IDS,
    compile_business_valuation,
    validate_business_valuation,
)
from src.services.data_object_admission import (
    compile_data_object_admission,
    seal_data_object_admission_receipt,
)
from src.services import evidence_traceability_registry as registry
from src.services.provider_operation_rights import (
    TEST_PURPOSE,
    TEST_SCOPE,
    compile_provider_operation_rights_evidence,
)


_DATASET_META = {
    "P_FIN_VINTAGE": ("quarter+event", "ASSET_RESEARCH_BRIEF"),
    "P_EARNINGS_GROWTH": ("quarter+event", "ASSET_RESEARCH_BRIEF"),
    "P_CASH_FLOW": ("quarter+event", "ASSET_RESEARCH_BRIEF"),
    "P_BALANCE_SHEET": ("quarter+event", "ASSET_RESEARCH_BRIEF"),
    "P_SHARES_EVENTS": ("event/known_at", "ASSET_RESEARCH_BRIEF"),
    "P_DIVIDEND": ("event/known_at", "ASSET_RESEARCH_BRIEF"),
    "P_VAL_RATIO": ("daily+quarter", "ASSET_RESEARCH_BRIEF"),
    "P_DAILY": ("daily/completed", "ASSET_RESEARCH_BRIEF"),
    "P_QUOTE_LIQUID": ("intraday+daily", "ASSET_RESEARCH_BRIEF"),
    "M_CN_BOND": ("daily/released", "MARKET_REGIME_BRIEF"),
    "M_ERP_VAL": ("daily/period", "MARKET_REGIME_BRIEF"),
    "E_NAV_IOPV": ("daily+intraday_published", "ETF_ASSET_BRIEF"),
    "E_PREMIUM": ("intraday+daily", "ETF_ASSET_BRIEF"),
    "E_UNDERLYING_VAL": ("quarter+daily", "ETF_ASSET_BRIEF"),
    "E_PRICE_1D": ("daily/close", "ETF_ASSET_BRIEF"),
}


def test_all_nine_routes_reference_only_the_accepted_76_object_catalog():
    """No valuation route may invent a tenth data plane or orphan field source."""
    universe = registry.compile_complete_research_universe_view(
        {"asset_type": "stock"}
    )
    catalog_ids = {
        row["dataset_id"] for row in universe["data_objects"]
    }
    assert len(catalog_ids) == 76
    assert len(VALUATION_ROUTE_IDS) == 9
    for route_id in VALUATION_ROUTE_IDS:
        requirement = VALUATION_ROUTE_DATA_REQUIREMENTS[route_id]
        route_ids = {
            *requirement["required_all"],
            *(dataset_id for group in requirement["required_any"] for dataset_id in group),
        }
        lineage = VALUATION_ROUTE_FIELD_SOURCES[route_id]
        lineage_ids = {
            *lineage["inputs"].values(),
            *lineage["assumptions"].values(),
        }
        assert route_ids <= catalog_ids
        assert lineage_ids <= route_ids


def _admission(dataset_id="P_FIN_VINTAGE", **receipt_overrides):
    native_frequency, consumer = _DATASET_META[dataset_id]
    obligation = {
        "dataset_id": dataset_id,
        "native_frequency": native_frequency,
        "product_consumer": consumer,
        "state": "UNKNOWN",
    }
    completed_through = (
        "2026-09-30"
        if dataset_id in {"P_FIN_VINTAGE", "E_NAV_IOPV"}
        else "2026-10-20"
    )
    payload = {
        "schema_version": "data-object-admission-receipt-v1",
        "dataset_id": dataset_id,
        "provider_identity": "synthetic-lawful-provider",
        "provider_route": "synthetic." + dataset_id.lower(),
        "native_frequency": native_frequency,
        "source_identity_hash": "a" * 64,
        "content_sha256": "b" * 64,
        "observed_at": "2026-10-21T10:00:00+00:00",
        "available_at": "2026-10-20T08:00:00+00:00",
        "completed_through": completed_through,
        "units": {"value": "CNY", "share_count": "SHARE"},
        "currency": "CNY",
        "adjustment_basis": "NOT_APPLICABLE",
        "revision": "ORIGINAL_DISCLOSURE",
        "vintage": "2026-10-20T08:00:00+00:00",
        "rights": {
            "product_use": "ALLOWED",
            "storage": "FORBIDDEN",
            "training": "FORBIDDEN",
            "redistribution": "FORBIDDEN",
        },
        "historical_pit_eligible": False,
        "consumer_scope": consumer,
        **receipt_overrides,
    }
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
    receipt = seal_data_object_admission_receipt(payload)
    return compile_data_object_admission(obligation, receipt)


def _route_dataset_ids(route_id):
    requirement = VALUATION_ROUTE_DATA_REQUIREMENTS[route_id]
    return tuple(dict.fromkeys((
        *requirement["required_all"],
        *(group[0] for group in requirement["required_any"]),
    )))

def _base(route_id, *, asset_type="stock", inputs=None, assumptions=None):
    inputs = dict(inputs or {})
    assumptions = dict(assumptions or {})
    expected_lineage = VALUATION_ROUTE_FIELD_SOURCES[route_id]
    return {
        "route_id": route_id,
        "asset_id": "600519" if asset_type == "stock" else "510300",
        "asset_type": asset_type,
        "currency": "CNY",
        "current_price": 20.0,
        "as_of": "2026-10-21T10:00:00+00:00",
        "financial_period_end": "2026-09-30",
        "published_at": "2026-10-20T08:00:00+00:00",
        "financial_version": "2026Q3_ORIGINAL",
        "data_admissions": [
            _admission(dataset_id) for dataset_id in _route_dataset_ids(route_id)
        ],
        "inputs": inputs,
        "assumptions": assumptions,
        "input_sources": {
            key: expected_lineage["inputs"][key] for key in inputs
        },
        "assumption_sources": {
            key: expected_lineage["assumptions"][key] for key in assumptions
        },
    }

@pytest.mark.parametrize(
    "route_id,asset_type,inputs,assumptions,method",
    [
        ("MATURE_INDUSTRIAL_CONSUMER", "stock", {"normalized_eps": 2.0}, {"pe_low": 8.0, "pe_high": 12.0}, "NORMALIZED_EARNINGS_PE"),
        ("HIGH_GROWTH_TECH", "stock", {"revenue": 1000.0, "net_debt": 100.0, "diluted_shares": 100.0}, {"ev_sales_low": 2.0, "ev_sales_high": 4.0}, "FORWARD_EV_SALES"),
        ("BANK", "stock", {"book_value_per_share": 12.0}, {"pb_low": 0.7, "pb_high": 1.2}, "PB_SUSTAINABLE_ROE_ROUTE"),
        ("INSURANCE", "stock", {"embedded_value_per_share": 18.0}, {"pev_low": 0.6, "pev_high": 1.0}, "PRICE_TO_EMBEDDED_VALUE"),
        ("CYCLICAL_RESOURCE", "stock", {"normalized_ebitda": 500.0, "net_debt": 100.0, "diluted_shares": 100.0}, {"ev_ebitda_low": 3.0, "ev_ebitda_high": 6.0}, "MID_CYCLE_EV_EBITDA"),
        ("UTILITY_INFRASTRUCTURE", "stock", {"next_dividend_per_share": 1.0}, {"cost_of_equity_low": 0.08, "cost_of_equity_high": 0.10, "growth_low": 0.01, "growth_high": 0.03}, "GORDON_DDM"),
        ("REIT", "stock", {"affo_per_share": 1.5}, {"paffo_low": 10.0, "paffo_high": 15.0}, "PRICE_TO_AFFO"),
        ("LOSS_MAKING_EARLY_GROWTH", "stock", {"revenue": 500.0, "net_debt": 50.0, "diluted_shares": 100.0}, {"ev_sales_low": 1.0, "ev_sales_high": 3.0}, "REVENUE_UNIT_ECONOMICS_EV_SALES"),
        ("ETF", "etf", {"nav_per_share": 4.0}, {"price_to_nav_low": 0.99, "price_to_nav_high": 1.01}, "NAV_PREMIUM_DISCOUNT_BAND"),
    ],
)
def test_all_nine_routes_are_deterministic_product_only(
    route_id, asset_type, inputs, assumptions, method,
):
    result = compile_business_valuation(
        _base(route_id, asset_type=asset_type, inputs=inputs, assumptions=assumptions)
    )
    assert result["status"] == "READY"
    assert result["route_id"] == route_id
    assert result["methods"] == (method,)
    assert result["fair_value"]["low"] > 0
    assert result["fair_value"]["high"] >= result["fair_value"]["low"]
    assert result["product_admitted"] is True
    assert result["learning_admitted"] is False
    assert result["strategy_upgrade_authority"] is False
    assert result["veto_override_authority"] is False
    assert result["independent_action_authority"] is False
    assert len(result["receipt_hash"]) == 64
    assert validate_business_valuation(result) == result
    assert "不覆盖数据、风险、流动性或执行否决" in result["summary_zh"]
    assert "显式上下界假设" in result["uncertainty_zh"]
    assert "不覆盖数据、风险、流动性或执行否决" in result["uncertainty_zh"]
    assert result["route_data_requirements"] == VALUATION_ROUTE_DATA_REQUIREMENTS[route_id]
    assert result["field_lineage"]["inputs"] == {
        key: VALUATION_ROUTE_FIELD_SOURCES[route_id]["inputs"][key]
        for key in inputs
    }
    assert result["field_lineage"]["assumptions"] == {
        key: VALUATION_ROUTE_FIELD_SOURCES[route_id]["assumptions"][key]
        for key in assumptions
    }


def test_route_set_is_exact_and_no_universal_default_route_exists():
    assert VALUATION_ROUTE_IDS == (
        "MATURE_INDUSTRIAL_CONSUMER", "HIGH_GROWTH_TECH", "BANK",
        "INSURANCE", "CYCLICAL_RESOURCE", "UTILITY_INFRASTRUCTURE",
        "REIT", "LOSS_MAKING_EARLY_GROWTH", "ETF",
    )
    invalid_route = _base(
        "MATURE_INDUSTRIAL_CONSUMER",
        inputs={"normalized_eps": 2.0},
        assumptions={"pe_low": 8.0, "pe_high": 12.0},
    )
    invalid_route["route_id"] = "UNIVERSAL_PE"
    result = compile_business_valuation(invalid_route)
    assert result["status"] == "MISSING"
    assert result["reason_codes"] == ("VALUATION_ROUTE_NOT_ADMITTED",)


def test_future_financials_and_missing_product_rights_fail_closed():
    future = _base(
        "MATURE_INDUSTRIAL_CONSUMER",
        inputs={"normalized_eps": 2.0},
        assumptions={"pe_low": 8.0, "pe_high": 12.0},
    )
    future["published_at"] = "2026-10-22T08:00:00+00:00"
    result = compile_business_valuation(future)
    assert result["status"] == "NOT_ADMITTED"
    assert "FINANCIAL_INFORMATION_NOT_KNOWN_AT_ASOF" in result["reason_codes"]

    no_rights = _base(
        "BANK",
        inputs={"book_value_per_share": 12.0},
        assumptions={"pb_low": 0.7, "pb_high": 1.2},
    )
    blocked_dataset = no_rights["data_admissions"][0]["dataset_id"]
    no_rights["data_admissions"][0] = _admission(
        blocked_dataset,
        rights={
            "product_use": "UNKNOWN",
            "storage": "UNKNOWN",
            "training": "UNKNOWN",
            "redistribution": "UNKNOWN",
        },
    )
    result = compile_business_valuation(no_rights)
    assert result["status"] == "NOT_ADMITTED"
    assert "VALUATION_DATA_NOT_PRODUCT_ADMITTED" in result["reason_codes"]


def test_missing_assumption_and_invalid_ddm_never_invent_defaults():
    missing = compile_business_valuation(
        _base(
            "MATURE_INDUSTRIAL_CONSUMER",
            inputs={"normalized_eps": 2.0},
            assumptions={"pe_low": 8.0},
        )
    )
    assert missing["status"] == "MISSING"
    assert missing["reason_codes"] == (
        "VALUATION_REQUIRED_POSITIVE_INPUT_MISSING:pe_high",
    )

    invalid = compile_business_valuation(
        _base(
            "UTILITY_INFRASTRUCTURE",
            inputs={"next_dividend_per_share": 1.0},
            assumptions={
                "cost_of_equity_low": 0.03,
                "cost_of_equity_high": 0.04,
                "growth_low": 0.02,
                "growth_high": 0.035,
            },
        )
    )
    assert invalid["status"] == "MISSING"
    assert invalid["reason_codes"] == (
        "DDM_COST_OF_EQUITY_MUST_EXCEED_GROWTH",
    )


def test_route_specific_dataset_and_field_lineage_fail_closed():
    payload = _base(
        "BANK",
        inputs={"book_value_per_share": 12.0},
        assumptions={"pb_low": 0.7, "pb_high": 1.2},
    )
    payload["data_admissions"] = [
        row for row in payload["data_admissions"]
        if row["dataset_id"] != "P_BALANCE_SHEET"
    ]
    result = compile_business_valuation(payload)
    assert result["status"] == "MISSING"
    assert any(
        reason.startswith("VALUATION_REQUIRED_DATASET_MISSING:P_BALANCE_SHEET")
        for reason in result["reason_codes"]
    )

    forged = _base(
        "BANK",
        inputs={"book_value_per_share": 12.0},
        assumptions={"pb_low": 0.7, "pb_high": 1.2},
    )
    forged["input_sources"]["book_value_per_share"] = "P_EARNINGS_GROWTH"
    result = compile_business_valuation(forged)
    assert result["status"] == "NOT_ADMITTED"
    assert "VALUATION_INPUT_LINEAGE_ROUTE_MISMATCH" in result["reason_codes"]


def test_primary_period_and_currency_mismatch_are_not_admitted():
    payload = _base(
        "MATURE_INDUSTRIAL_CONSUMER",
        inputs={"normalized_eps": 2.0},
        assumptions={"pe_low": 8.0, "pe_high": 12.0},
    )
    vintage = next(
        row for row in payload["data_admissions"]
        if row["dataset_id"] == "P_FIN_VINTAGE"
    )
    payload["data_admissions"][payload["data_admissions"].index(vintage)] = _admission(
        "P_FIN_VINTAGE", completed_through="2026-06-30"
    )
    result = compile_business_valuation(payload)
    assert result["status"] == "NOT_ADMITTED"
    assert "VALUATION_PRIMARY_PERIOD_MISMATCH" in result["reason_codes"]

    payload = _base(
        "MATURE_INDUSTRIAL_CONSUMER",
        inputs={"normalized_eps": 2.0},
        assumptions={"pe_low": 8.0, "pe_high": 12.0},
    )
    first_dataset = payload["data_admissions"][0]["dataset_id"]
    payload["data_admissions"][0] = _admission(first_dataset, currency="USD")
    result = compile_business_valuation(payload)
    assert result["status"] == "NOT_ADMITTED"
    assert "VALUATION_DATA_CURRENCY_MISMATCH" in result["reason_codes"]


def test_tampered_compiled_admission_cannot_grant_valuation_rights():
    payload = _base(
        "BANK",
        inputs={"book_value_per_share": 12.0},
        assumptions={"pb_low": 0.7, "pb_high": 1.2},
    )
    payload["data_admissions"][0]["product_admitted"] = False
    result = compile_business_valuation(payload)
    assert result["status"] == "NOT_ADMITTED"
    assert "VALUATION_DATA_ADMISSION_INVALID" in result["reason_codes"]


def test_ready_receipt_tampering_is_detected():
    result = compile_business_valuation(
        _base(
            "BANK",
            inputs={"book_value_per_share": 12.0},
            assumptions={"pb_low": 0.7, "pb_high": 1.2},
        )
    )
    forged = deepcopy(result)
    forged["fair_value"]["high"] = 999.0
    with pytest.raises(BusinessValuationError, match="STALE"):
        validate_business_valuation(forged)

def test_later_compiled_dataset_cannot_be_borrowed_for_earlier_valuation():
    payload = _base(
        "BANK",
        inputs={"book_value_per_share": 12.0},
        assumptions={"pb_low": 0.7, "pb_high": 1.2},
    )
    assert compile_business_valuation(payload)["status"] == "READY"
    # The financial publication (October 20) predates this research time.
    # The dataset itself was first observed October 21 at 10:00 UTC.
    result = compile_business_valuation(
        payload, as_of="2026-10-21T09:00:00+00:00",
    )
    assert result["status"] == "NOT_ADMITTED"
    assert "VALUATION_DATA_NOT_KNOWN_AT_ASOF" in result["reason_codes"]
    assert result["strategy_upgrade_authority"] is False
