"""Independent arithmetic, applicability and lineage tests for P2; all data synthetic."""
from copy import deepcopy

import pytest

from src.services.business_valuation_service import compile_business_valuation
from src.services.fundamental_quality_service import compile_fundamental_quality
from tests.test_business_valuation_service import _base, _admission, _industry_route_plan
from tests.test_fundamental_quality_service import _payload


def bank_payload():
    payload = _base("BANK", inputs={"book_value_per_share": 10.0}, assumptions={"pb_low": 0.6, "pb_high": 1.0})
    payload["primary_method"] = "BANK_JUSTIFIED_PB"
    payload["assumptions"] = {
        "sustainable_roe_low": 0.12, "sustainable_roe_high": 0.16,
        "cost_of_equity_low": 0.10, "cost_of_equity_high": 0.12,
        "growth_low": 0.02, "growth_high": 0.04,
    }
    payload["assumption_sources"] = {
        key: "M_ERP_VAL" if key.startswith("cost_of_equity") else "P_EARNINGS_GROWTH"
        for key in payload["assumptions"]
    }
    payload["data_admissions"].extend([_admission("P_EARNINGS_GROWTH"), _admission("M_ERP_VAL")])
    return payload


def test_bank_public_consumer_derives_justified_pb_from_roe_ke_and_growth():
    result = compile_business_valuation(bank_payload())
    assert result["status"] == "READY", result["reason_codes"]
    # Independently enumerated extremes of BVPS*(ROE-g)/(ke-g).
    assert result["fair_value"]["low"] == pytest.approx(10.0)
    assert result["fair_value"]["high"] == pytest.approx(20.0)
    assert result["methods"] == ("BANK_JUSTIFIED_PB",)
    assert result["sensitivity"]["scenario_count"] == 8
    assert result["strategy_upgrade_authority"] is False
    assert result["independent_action_authority"] is False


def test_bank_model_roe_change_reaches_real_valuation_consumer():
    original = compile_business_valuation(bank_payload())
    changed = bank_payload()
    changed["assumptions"].update(sustainable_roe_low=0.14, sustainable_roe_high=0.18)
    higher = compile_business_valuation(changed)
    assert original["status"] == higher["status"] == "READY"
    assert higher["fair_value"]["low"] > original["fair_value"]["low"]
    assert higher["fair_value"]["high"] > original["fair_value"]["high"]
    assert higher["receipt_hash"] != original["receipt_hash"]


@pytest.mark.parametrize("fault", ["missing_ke", "g_at_ke", "g_above_roe", "percent_not_fraction", "wrong_source", "missing_rights", "reversed_range"])
def test_bank_model_refuses_unsupported_assumptions(fault):
    payload = bank_payload()
    if fault == "missing_ke":
        del payload["assumptions"]["cost_of_equity_high"]
        del payload["assumption_sources"]["cost_of_equity_high"]
    elif fault == "g_at_ke": payload["assumptions"]["growth_high"] = 0.10
    elif fault == "g_above_roe": payload["assumptions"]["sustainable_roe_low"] = 0.03
    elif fault == "percent_not_fraction": payload["assumptions"]["sustainable_roe_high"] = 16
    elif fault == "wrong_source": payload["assumption_sources"]["cost_of_equity_low"] = "P_BALANCE_SHEET"
    elif fault == "missing_rights": payload["data_admissions"] = [x for x in payload["data_admissions"] if x["dataset_id"] != "M_ERP_VAL"]
    else: payload["assumptions"]["sustainable_roe_low"] = 0.2
    result = compile_business_valuation(payload)
    assert result["status"] != "READY"
    assert result["fair_value"] is None


RATIO_FIELDS = {
    "earnings_common": (100.0, "P_EARNINGS_GROWTH", "CNY"),
    "diluted_earnings_common": (100.0, "P_EARNINGS_GROWTH", "CNY"),
    "weighted_basic_shares": (50.0, "P_SHARES_EVENTS", "SHARE"),
    "weighted_diluted_shares": (55.0, "P_SHARES_EVENTS", "SHARE"),
    "ending_shares": (50.0, "P_SHARES_EVENTS", "SHARE"),
    "equity_begin": (400.0, "P_BALANCE_SHEET", "CNY"),
    "equity_end": (600.0, "P_BALANCE_SHEET", "CNY"),
    "price": (20.0, "P_DAILY", "CNY_PER_SHARE"),
    "ebit": (150.0, "P_EARNINGS_GROWTH", "CNY"),
    "effective_tax_rate": (0.2, "P_EARNINGS_GROWTH", "FRACTION"),
    "invested_capital_begin": (800.0, "P_BALANCE_SHEET", "CNY"),
    "invested_capital_end": (1000.0, "P_BALANCE_SHEET", "CNY"),
}


def ratio_payload():
    p = _payload()
    p.update(as_of="2026-10-21T10:00:00+00:00", published_at="2026-10-20T08:00:00+00:00")
    ids = ["P_FIN_VINTAGE", "P_EARNINGS_GROWTH", "P_CASH_FLOW", "P_BALANCE_SHEET", "P_SHARES_EVENTS", "P_DAILY"]
    p["data_admissions"] = [
        _admission(i, completed_through="2026-10-20" if i == "P_DAILY" else "2026-09-30",
                   adjustment_basis="RAW" if i == "P_DAILY" else "NOT_APPLICABLE")
        for i in ids
    ]
    p["accounting_ratio_input"] = {
        "schema_version": "dated-accounting-ratio-input-v1",
        "asset_id": p["asset_id"], "financial_version": p["financial_version"],
        "financial_period_end": p["financial_period_end"],
        "earnings_basis": "TTM", "price_date": "2026-10-20",
        "industry_applicability": "NON_FINANCIAL",
        "industry_route_plan": _industry_route_plan("MATURE_INDUSTRIAL_CONSUMER"),

        "metrics": {k: v[0] for k, v in RATIO_FIELDS.items()},
        "field_sources": {k: v[1] for k, v in RATIO_FIELDS.items()},
        "field_units": {k: v[2] for k, v in RATIO_FIELDS.items()},
    }
    return p


def test_eps_roe_pe_pb_roic_are_recomputed_from_dated_inputs_not_supplied_ratios():
    result = compile_fundamental_quality(ratio_payload())
    ratios = result.get("accounting_ratios", {})
    assert ratios.get("status") == "READY", ratios
    values = ratios["values"]
    assert values["eps_basic"] == pytest.approx(2.0)
    assert values["eps_diluted"] == pytest.approx(100 / 55, abs=1e-6)
    assert values["roe_fraction"] == pytest.approx(0.20)
    assert values["book_value_per_share"] == pytest.approx(12.0)
    assert values["pe_basic_ttm"] == pytest.approx(10.0)
    assert values["pe_diluted_ttm"] == pytest.approx(11.0)
    assert values["pb"] == pytest.approx(20 / 12, abs=1e-6)
    assert values["roic_nopat_fraction"] == pytest.approx(120 / 900, abs=1e-6)
    assert "EPS" in result["summary_zh"] and "ROE" in result["summary_zh"]
    assert result["strategy_upgrade_authority"] is False


@pytest.mark.parametrize("fault", ["wrong_version", "future_price", "unknown_units", "missing_shares", "wrong_lineage"])
def test_ratios_fail_closed_without_same_period_currency_units_and_lineage(fault):
    p = ratio_payload(); raw = p["accounting_ratio_input"]
    if fault == "wrong_version": raw["financial_version"] = "other-version"
    elif fault == "future_price": raw["price_date"] = "2026-10-22"
    elif fault == "unknown_units": raw["field_units"]["weighted_basic_shares"] = "LOT"
    elif fault == "missing_shares": p["data_admissions"] = [x for x in p["data_admissions"] if x["dataset_id"] != "P_SHARES_EVENTS"]
    else: raw["field_sources"]["equity_end"] = "P_CASH_FLOW"
    r = compile_fundamental_quality(p)
    assert r.get("accounting_ratios", {}).get("status") in {"MISSING", "NOT_ADMITTED"}
    assert r["accounting_ratios"]["values"] == {}


def test_loss_making_pe_is_not_reported_as_cheap_negative_multiple():
    p = ratio_payload()
    p["accounting_ratio_input"]["metrics"].update(earnings_common=-100.0, diluted_earnings_common=-100.0)
    ratios = compile_fundamental_quality(p).get("accounting_ratios", {})
    assert ratios.get("status") == "PARTIAL"
    assert ratios["values"]["eps_basic"] == -2.0
    assert ratios["values"]["pe_basic_ttm"] is None
    assert ratios["value_states"]["pe_basic_ttm"] == "NOT_COMPARABLE_NON_POSITIVE_EARNINGS"


def test_financial_industry_does_not_receive_industrial_roic():
    p = ratio_payload()
    p["accounting_ratio_input"]["industry_applicability"] = "FINANCIAL"
    p["accounting_ratio_input"]["industry_route_plan"] = _industry_route_plan("BANK")
    ratios = compile_fundamental_quality(p).get("accounting_ratios", {})
    assert ratios.get("values", {}).get("roic_nopat_fraction") is None
    assert ratios.get("value_states", {}).get("roic_nopat_fraction") == "NOT_APPLICABLE_FINANCIAL_INDUSTRY"


def test_ratio_industry_label_cannot_override_verified_bank_classification():
    p = ratio_payload()
    p["accounting_ratio_input"]["industry_route_plan"] = _industry_route_plan("BANK")
    result = compile_fundamental_quality(p)["accounting_ratios"]
    assert result["status"] == "NOT_ADMITTED"
    assert result["values"] == {}


def test_ratio_prices_cannot_use_adjusted_bars_against_nominal_book_value():
    p = ratio_payload()
    p["data_admissions"][-1] = _admission("P_DAILY", adjustment_basis="QFQ")
    result = compile_fundamental_quality(p)["accounting_ratios"]
    assert result["status"] == "NOT_ADMITTED"
    assert "RATIO_PRICE_BASIS_NOT_RAW" in result["reason_codes"]


def test_ratio_financial_fields_cannot_mix_different_report_periods():
    p = ratio_payload()
    p["data_admissions"] = [
        _admission("P_BALANCE_SHEET", completed_through="2026-06-30")
        if row["dataset_id"] == "P_BALANCE_SHEET" else row
        for row in p["data_admissions"]
    ]
    result = compile_fundamental_quality(p)["accounting_ratios"]
    assert result["status"] == "NOT_ADMITTED"
    assert "RATIO_SOURCE_PERIOD_MISMATCH:P_BALANCE_SHEET" in result["reason_codes"]
    assert result["values"] == {}


def test_new_formulas_reach_both_existing_report_consumers_without_action_upgrade():
    from src.notification import _append_investor_brief_block, _append_investor_notification_block
    from src.services.factor_decision_summary import (
        build_stock_factor_decision_summary, validate_canonical_factor_binding,
    )
    from tests.test_factor_decision_summary import _trend
    quality = compile_fundamental_quality(ratio_payload())
    valuation = compile_business_valuation(bank_payload())
    baseline = build_stock_factor_decision_summary(_trend(code="600519"), include_canonical=True)
    factor = build_stock_factor_decision_summary(
        _trend(code="600519"), include_canonical=True,
        fundamental_context={"market": "cn", "fundamental_quality": quality, "business_valuation": valuation},
    )
    assert factor["canonical_decision"] == baseline["canonical_decision"]
    validate_canonical_factor_binding(factor)
    for render in (_append_investor_brief_block, _append_investor_notification_block):
        lines = []
        assert render(lines, factor, "zh") is True
        text = "\n".join(lines)
        assert "EPS" in text and "ROE" in text and "ROIC" in text
        assert "BANK_JUSTIFIED_PB" in text
        assert "10.00–20.00 CNY" in text
