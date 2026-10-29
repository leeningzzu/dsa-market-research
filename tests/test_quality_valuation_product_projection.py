from __future__ import annotations

from copy import deepcopy

import pytest

from src.notification import (
    _append_investor_brief_block,
    _append_investor_notification_block,
)
from src.services.business_valuation_service import compile_business_valuation
from src.services.factor_decision_summary import (
    build_stock_factor_decision_summary,
    validate_canonical_factor_binding,
    validate_investor_brief_binding,
)
from src.services.fundamental_quality_service import compile_fundamental_quality
from tests.test_business_valuation_service import _base as valuation_payload
from tests.test_factor_decision_summary import _trend
from tests.test_fundamental_quality_service import _payload as quality_payload


def _ready_stock_factor():
    trend = _trend(code="600519")
    quality = compile_fundamental_quality(quality_payload())
    valuation = compile_business_valuation(
        valuation_payload(
            "MATURE_INDUSTRIAL_CONSUMER",
            inputs={"normalized_eps": 2.0},
            assumptions={"pe_low": 8.0, "pe_high": 12.0},
        )
    )
    baseline = build_stock_factor_decision_summary(
        trend,
        include_canonical=True,
    )
    factor = build_stock_factor_decision_summary(
        trend,
        fundamental_context={
            "market": "cn",
            "fundamental_quality": quality,
            "business_valuation": valuation,
        },
        include_canonical=True,
    )
    return factor, baseline, quality, valuation


def test_ready_stock_quality_and_business_valuation_reach_both_email_consumers():
    factor, baseline, quality, valuation = _ready_stock_factor()

    assert factor["canonical_decision"] == baseline["canonical_decision"]
    assert factor["fundamental_quality"] == quality
    assert factor["business_valuation"] == valuation
    assert factor["sections"]["quality_growth"].startswith("质量成长：收入同比")
    assert "NORMALIZED_EARNINGS_PE" in factor["sections"]["valuation"]

    brief = factor["investor_brief"]
    assert brief["quality"]["status"] == "READY"
    assert brief["quality"]["receipt_hash"] == quality["receipt_hash"]
    assert brief["valuation"]["status"] == "READY"
    assert brief["valuation"]["route_id"] == "MATURE_INDUSTRIAL_CONSUMER"
    assert brief["valuation"]["methods"] == ("NORMALIZED_EARNINGS_PE",)
    assert brief["valuation"]["fair_value"] == valuation["fair_value"]
    assert brief["valuation"]["receipt_hash"] == valuation["receipt_hash"]

    compact_lines: list[str] = []
    full_lines: list[str] = []
    assert _append_investor_brief_block(compact_lines, factor, "zh") is True
    assert _append_investor_notification_block(full_lines, factor, "zh") is True
    for rendered in ("\n".join(compact_lines), "\n".join(full_lines)):
        assert "**质量成长**: 收入同比 12.50%" in rendered
        assert "NORMALIZED_EARNINGS_PE" in rendered
        assert "16.00–24.00 CNY" in rendered
        assert "不覆盖数据、风险、流动性或执行否决" in rendered

    binding = validate_canonical_factor_binding(factor)
    assert binding["asset_material_binding_version"] == (
        "factor-brief-quality-valuation-v1"
    )
    assert len(binding["asset_material_sha256"]) == 64
    assert validate_investor_brief_binding(factor) == brief


def test_ready_etf_nav_route_is_visible_without_stock_quality_or_action_upgrade():
    trend = _trend(code="510300")
    valuation = compile_business_valuation(
        valuation_payload(
            "ETF",
            asset_type="etf",
            inputs={"nav_per_share": 4.0},
            assumptions={"price_to_nav_low": 0.99, "price_to_nav_high": 1.01},
        )
    )
    factor = build_stock_factor_decision_summary(
        trend,
        fundamental_context={"business_valuation": valuation},
        include_canonical=True,
        asset_type="etf",
    )

    assert factor["canonical_decision"]["action"] == "WAIT"
    assert factor["canonical_decision"]["evidence_state"] == "UNKNOWN"
    assert factor["investor_brief"]["quality"]["status"] == "NOT_APPLICABLE"
    assert factor["investor_brief"]["valuation"]["status"] == "READY"
    assert factor["investor_brief"]["valuation"]["route_id"] == "ETF"
    assert factor["investor_brief"]["valuation"]["methods"] == (
        "NAV_PREMIUM_DISCOUNT_BAND",
    )

    lines: list[str] = []
    assert _append_investor_notification_block(lines, factor, "zh") is True
    rendered = "\n".join(lines)
    assert "ETF不适用单公司财务质量口径" in rendered
    assert "NAV_PREMIUM_DISCOUNT_BAND" in rendered
    assert "3.96–4.04 CNY" in rendered


def test_stock_quality_cannot_be_injected_into_etf_projection():
    quality = compile_fundamental_quality(quality_payload())
    valuation = compile_business_valuation(
        valuation_payload(
            "ETF",
            asset_type="etf",
            inputs={"nav_per_share": 4.0},
            assumptions={"price_to_nav_low": 0.99, "price_to_nav_high": 1.01},
        )
    )
    with pytest.raises(ValueError, match="not applicable to ETF"):
        build_stock_factor_decision_summary(
            _trend(code="510300"),
            fundamental_context={
                "fundamental_quality": quality,
                "business_valuation": valuation,
            },
            include_canonical=True,
            asset_type="etf",
        )


def test_quality_valuation_material_is_bound_and_tampering_is_rejected():
    factor, _, _, _ = _ready_stock_factor()

    forged_factor = deepcopy(factor)
    forged_factor["business_valuation"]["fair_value"]["high"] = 999.0
    with pytest.raises(ValueError, match="stale|missing|identity"):
        validate_canonical_factor_binding(forged_factor)

    forged_brief = deepcopy(factor["investor_brief"])
    forged_brief["quality"]["summary"] = "FORGED_QUALITY_TEXT"
    with pytest.raises(ValueError, match="stale|quality|valuation"):
        validate_investor_brief_binding(factor, forged_brief)

    legacy_binding = deepcopy(factor)
    legacy_binding["canonical_decision_identity"].pop("asset_material_sha256")
    legacy_binding["canonical_decision_identity"].pop(
        "asset_material_binding_version"
    )
    legacy_binding["investor_brief"]["canonical_binding"] = deepcopy(
        legacy_binding["canonical_decision_identity"]
    )
    with pytest.raises(ValueError, match="stale|missing"):
        validate_canonical_factor_binding(legacy_binding)


def test_missing_structured_material_remains_honest_nontrigger():
    trend = _trend(code="600519")
    missing_valuation = compile_business_valuation({})
    baseline = build_stock_factor_decision_summary(
        trend,
        include_canonical=True,
    )
    factor = build_stock_factor_decision_summary(
        trend,
        fundamental_context={"business_valuation": missing_valuation},
        include_canonical=True,
    )

    assert factor["canonical_decision"] == baseline["canonical_decision"]
    assert factor["investor_brief"]["quality"]["status"] == "MISSING"
    assert factor["investor_brief"]["valuation"]["status"] == "MISSING"
    assert factor["investor_brief"]["valuation"]["fair_value"] is None
    assert factor["investor_brief"]["valuation"]["receipt_hash"] is None


def test_ready_material_for_another_asset_is_rejected_before_product_projection():
    quality = compile_fundamental_quality(quality_payload())
    valuation = compile_business_valuation(
        valuation_payload(
            "MATURE_INDUSTRIAL_CONSUMER",
            inputs={"normalized_eps": 2.0},
            assumptions={"pe_low": 8.0, "pe_high": 12.0},
        )
    )
    with pytest.raises(ValueError, match="asset identity"):
        build_stock_factor_decision_summary(
            _trend(code="000001"),
            fundamental_context={
                "fundamental_quality": quality,
                "business_valuation": valuation,
            },
            include_canonical=True,
        )
