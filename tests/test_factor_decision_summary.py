import json
from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest

from data_provider.daily_data_identity import sha256_payload

from src.analyzer import AnalysisResult
from src.services.factor_decision_summary import (
    apply_canonical_decision_to_result,
    assert_canonical_consumer_consistency,
    _valuation_summary,
    build_stock_factor_decision_summary,
    canonical_explanation_degradation_eligible,
    validate_canonical_factor_binding,
    validate_investor_brief_binding,
)
from src.stock_analyzer import StockTrendAnalyzer, TrendAnalysisResult, VolumeStatus
from src.services.business_valuation_service import compile_business_valuation
from tests.test_business_valuation_service import _base, _index_valuation


def _enum(value: str):
    return SimpleNamespace(value=value)


def _trend(**overrides):
    data = {
        "signal_score": 82,
        "trend_status": _enum("多头排列"),
        "buy_signal": _enum("买入"),
        "ma_alignment": "MA5 > MA10 > MA20",
        "trend_strength": 78,
        "current_price": 10.5,
        "support_levels": [10.0, 10.2],
        "resistance_levels": [11.3],
        "volume_status": _enum("缩量回调"),
        "volume_ratio_5d": 0.62,
        "volume_trend": "缩量回调，卖压收缩；是否属于洗盘需后续确认",
        "macd_signal": "MACD多头结构",
        "rsi_signal": "RSI中性偏强",
        "risk_factors": [],
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _strict_verified_relative_strength():
    # Independent synthetic method input identity; no real provider or PIT claim.
    stock = {
        "data_snapshot_identity": "a" * 64,
        "provider_identity": "synthetic",
        "adjustment_basis": "qfq",
        "price_identity_reasons": [],
        "stock_code": "600519",
        "market": "cn",
        "target_date": "2026-09-30",
    }
    return {
        "schema_version": "relative-strength-v1",
        "family": "trend_relative_strength",
        "status": "READY",
        "market": "cn",
        "stock_code": "600519",
        "target_date": "2026-09-30",
        "benchmark": {"code": "510300", "source": "synthetic"},
        "stock": {},
        "relative": {"state": "OUTPERFORMING"},
        "data_quality": {
            "source_alignment": "MATCHED",
            "stock_endpoint_sources": ["synthetic"],
        },
        "input_identity": {
            "stock": stock,
            "benchmark": {**stock, "stock_code": "510300"},
        },
    }


def test_explicit_native_partial_momentum_never_falls_back_to_raw_gold_cross() -> None:
    """Preserve valid ROC/TR while suppressing unadmitted raw MACD and RSI prose."""
    from src.services.factor_decision_summary import (
        _build_volatility_momentum_evidence, _volatility_momentum_summary,
    )

    trend = _trend(macd_signal="零轴上金叉，动量转强", rsi_signal="RSI强势，建议积极买入")
    partial = {
        "status": "PARTIAL",
        "reason": "RECURSIVE_METHOD_ADMISSION_INCOMPLETE",
        "volatility": {"status": "PARTIAL", "true_range_sma_20_pct": 1.5},
        "momentum": {
            "status": "PARTIAL", "roc_20_pct": 3.0,
            "macd": {"status": None, "admission": {"status": "MISSING"}},
            "rsi": {"status": None, "admission": {"status": "MISSING"}},
        },
        "confirmed_divergence": {"status": "MISSING", "state": "NONE"},
    }
    text = _volatility_momentum_summary(
        _build_volatility_momentum_evidence(trend, partial), trend,
    )
    assert "ROC20 +3.0%" in text
    assert "真实波幅" in text
    assert "零轴上金叉" not in text
    assert "RSI强势" not in text

    blank = {**partial, "volatility": {"status": "PARTIAL"},
             "momentum": {"status": "PARTIAL", "macd": {}, "rsi": {}}}
    blank_text = _volatility_momentum_summary(
        _build_volatility_momentum_evidence(trend, blank), trend,
    )
    assert "没有可独立准入的当前指标" in blank_text
    assert "零轴上金叉" not in blank_text


def test_factor_and_investor_brief_keep_stock_etf_and_index_valuation_layers_separate():
    index = _index_valuation("index", asset_id="000300")
    stock = compile_business_valuation(_base(
        "MATURE_INDUSTRIAL_CONSUMER",
        inputs={"normalized_eps": 2.0},
        assumptions={"pe_low": 8.0, "pe_high": 12.0},
    ))
    stock_text = _valuation_summary(
        {"index_sector_valuations": [index]},
        asset_type="stock",
        business_valuation=stock,
        index_sector_valuations=[index],
    )
    assert "稳定工业/制造/成熟消费" in stock_text
    assert "指数/行业估值" in stock_text

    etf_payload = _base(
        "ETF",
        asset_type="etf",
        inputs={"nav_per_share": 4.0},
        assumptions={"price_to_nav_low": 0.99, "price_to_nav_high": 1.01},
    )
    etf = compile_business_valuation(etf_payload)
    factor = build_stock_factor_decision_summary(
        _trend(),
        fundamental_context={
            "business_valuation": etf,
            "index_sector_valuations": [etf_payload["underlying_valuation"]],
        },
        asset_type="etf",
    )
    brief = factor["investor_brief"]
    assert brief["valuation"]["label"] == "底层估值"
    assert brief["asset_specific"]["underlying_valuation"]["status"] == "READY"
    assert brief["asset_specific"]["premium_discount"]["status"] == "READY"
    assert "NAV_PREMIUM_DISCOUNT_BAND" in (
        brief["asset_specific"]["premium_discount"]["summary"]
    )
    assert "指数/行业估值" not in (
        brief["asset_specific"]["premium_discount"]["summary"]
    )
    assert "指数/行业估值" in (
        brief["asset_specific"]["underlying_valuation"]["summary"]
    )
    assert brief["asset_specific"]["underlying_valuation"]["receipt_hash"] == (
        etf_payload["underlying_valuation"]["receipt_hash"]
    )
    assert factor["index_sector_valuations"][0]["scope"] == "etf_underlying"
    assert factor["valuation_evidence"] == factor["business_valuation"]
    assert "quality_evidence" in factor
    assert factor["quality_evidence"] == factor["fundamental_quality"]


def test_global_atomic_sources_merge_from_b1_fundamental_context_and_conflicts_fail_closed():
    facts = {
        "series_id": "SYNTHETIC_CPI",
        "reference_period": "2026-09",
        "index_value": 310.0,
        "yoy_pct": 2.4,
        "release_published_at": "2026-10-14T12:30:00+00:00",
        "seasonal_adjustment": "SEASONALLY_ADJUSTED",
        "value_unit": "INDEX_1982_84_100",
    }
    observation = {
        "dataset_id": "GLOBAL_US_ACTUAL_INFLATION",
        "state": "READY",
        "provider_identity": "BLSPublicDataAPI",
        "provider_route": "bls.publicAPI.v2.timeseries+CPI_release",
        "source_identity_hash": "1" * 64,
        "source_content_sha256": "2" * 64,
        "source_observed_at": "2026-10-28T08:00:00+00:00",
        "source_available_at": "2026-10-14T12:30:00+00:00",
        "completed_through": "2026-09",
        "units": {"index_value": "INDEX_1982_84_100", "yoy_pct": "PERCENT"},
        "currency": "NOT_APPLICABLE",
        "adjustment_basis": "SEASONALLY_ADJUSTED",
        "revision": "observed-response-sha256:" + "2" * 64,
        "vintage": "2026-10-14T12:30:00+00:00",
        "facts": facts,
        "source_fact_hash": sha256_payload(facts),
    }
    summary = build_stock_factor_decision_summary(
        _trend(),
        fundamental_context={
            "atomic_subfield_source_observations": {
                "GLOBAL_US_ACTUAL_INFLATION": observation,
            },
        },
    )
    assert summary["atomic_subfield_source_observations"][
        "GLOBAL_US_ACTUAL_INFLATION"
    ]["facts"]["yoy_pct"] == 2.4

    conflicting = deepcopy(observation)
    conflicting["source_identity_hash"] = "3" * 64
    with pytest.raises(ValueError, match="atomic subfield source observation conflict"):
        build_stock_factor_decision_summary(
            _trend(),
            fundamental_context={
                "atomic_subfield_source_observations": {
                    "GLOBAL_US_ACTUAL_INFLATION": observation,
                },
            },
            market_structure_context={
                "atomic_subfield_source_observations": {
                    "GLOBAL_US_ACTUAL_INFLATION": conflicting,
                },
            },
        )


def test_summary_reuses_existing_score_without_inventing_probability_or_win_rate():
    summary = build_stock_factor_decision_summary(
        _trend(),
        fundamental_context={
            "market": "cn",
            "valuation": {"data": {"pe_ratio": 20.0, "pb_ratio": 3.0}},
        },
        chip_data=SimpleNamespace(avg_cost=10.0, concentration_90=0.18, profit_ratio=0.64),
    )

    assert summary["composite_score"] == 82
    assert summary["historical_reference"] == {
        "available": False,
        "display": "样本不足，暂不展示",
    }
    assert summary["current_probability"] == {
        "available": False,
        "display": "暂不提供（尚未完成独立校准）",
    }
    assert "不代表胜率或概率" in summary["score_note"]
    assert "canonical_decision" not in summary
    assert "PE 20.0" in summary["valuation"]
    assert "PB 3.0" in summary["valuation"]
    assert "暂不判断偏低、合理或偏高" in summary["valuation"]
    assert "筹码平均成本参考约 10.00" in summary["cost_structure"]
    assert "主力" not in str(summary)



def test_five_distinct_native_rationale_sections_are_not_budget_truncated(monkeypatch):
    """Five valid distinct original method explanations must all be kept."""
    import src.services.factor_decision_summary as owner

    values = {
        "_market_sector_regime_summary": "DISTINCT_MARKET_CONTEXT_1",
        "_trend_relative_strength_summary": "DISTINCT_RELATIVE_STRENGTH_2",
        "_supply_demand_volume_price_summary": "DISTINCT_SUPPLY_DEMAND_3",
        "_price_structure_summary": "DISTINCT_PRICE_STRUCTURE_4",
        "_valuation_summary": "DISTINCT_VALUATION_UNCERTAINTY_5",
    }
    for name, value in values.items():
        monkeypatch.setattr(owner, name, lambda *args, _text=value, **kwargs: _text)
    factor = build_stock_factor_decision_summary(_trend(), include_canonical=True)
    assert factor["why"] == list(values.values())
    assert len(factor["why"]) == 5


def test_material_risks_are_preserved_through_summary_brief_and_binding():
    risks = [
        "RISK_LIQUIDITY_DISTINCT",
        "RISK_FINANCIAL_VINTAGE_DISTINCT",
        "RISK_SUPPORT_BREAK_DISTINCT",
        "RISK_VALUATION_ASSUMPTION_DISTINCT",
    ]

    summary = build_stock_factor_decision_summary(
        _trend(risk_factors=risks),
        include_canonical=True,
    )
    brief = validate_investor_brief_binding(summary)

    assert summary["risk_notes"] == risks
    assert brief["risk_notes"] == risks


def test_material_risk_normalization_keeps_legal_nontriggers_without_duplicates():
    duplicate = build_stock_factor_decision_summary(
        _trend(risk_factors=["⚠️ 重复风险", "重复风险", "", "另一项风险"]),
        include_canonical=True,
    )
    assert duplicate["risk_notes"] == ["重复风险", "另一项风险"]
    assert duplicate["investor_brief"]["risk_notes"] == ["重复风险", "另一项风险"]

    single = build_stock_factor_decision_summary(
        _trend(risk_factors=["单一合法风险"]),
        include_canonical=True,
    )
    assert single["risk_notes"] == ["单一合法风险"]
    assert single["investor_brief"]["risk_notes"] == ["单一合法风险"]

    no_event = build_stock_factor_decision_summary(
        _trend(risk_factors=[]),
        include_canonical=True,
    )
    assert no_event["risk_notes"] == [
        "需继续关注市场环境、行业变化及关键支撑失效风险。"
    ]
    assert no_event["investor_brief"]["risk_notes"] == no_event["risk_notes"]

def test_b8_current_canonical_binds_both_risk_material_layers_and_rejects_stale_legacy():
    """A signed action must not carry risk sentences edited after finalization."""
    from copy import deepcopy
    from src.services.factor_decision_summary import canonical_factor_binding

    source = build_stock_factor_decision_summary(
        _trend(risk_factors=["LIQUIDITY_RISK_SOURCE_A", "VINTAGE_RISK_SOURCE_B"]),
        include_canonical=True,
    )
    binding = source["canonical_decision_identity"]
    assert binding["risk_material_binding_version"] == "factor-brief-risk-atoms-v1"
    assert len(binding["risk_material_sha256"]) == 64

    for target in ("factor", "brief", "both"):
        poisoned = deepcopy(source)
        if target in ("factor", "both"):
            poisoned["risk_notes"][0] = "FORGED_FACTOR_RISK_REPLACEMENT"
        if target in ("brief", "both"):
            poisoned["investor_brief"]["risk_notes"][0] = "FORGED_BRIEF_RISK_REPLACEMENT"
        with pytest.raises(ValueError, match="stale|risk"):
            validate_canonical_factor_binding(poisoned)
        with pytest.raises(ValueError, match="stale|risk"):
            validate_investor_brief_binding(poisoned)

    detached_brief = deepcopy(source["investor_brief"])
    detached_brief["risk_notes"][0] = "FORGED_DETACHED_BRIEF_RISK"
    with pytest.raises(ValueError, match="stale|risk"):
        validate_investor_brief_binding(source, detached_brief)

    old_current = deepcopy(source)
    old_current["canonical_decision_identity"].pop("risk_material_sha256")
    old_current["canonical_decision_identity"].pop("risk_material_binding_version")
    old_current["investor_brief"]["canonical_binding"] = deepcopy(
        old_current["canonical_decision_identity"]
    )
    with pytest.raises(ValueError, match="stale|risk"):
        validate_canonical_factor_binding(old_current)

    # Distinct, independently admitted facts before sealing are not collapsed
    # into one generic risk sentence or made identical between Product layers.
    expanded = deepcopy(source)
    expanded["risk_notes"].append("INDEPENDENT_FACTOR_RISK_C")
    expanded["investor_brief"]["risk_notes"].append("INDEPENDENT_BRIEF_RISK_D")
    expanded["canonical_decision_identity"] = canonical_factor_binding(expanded)
    expanded["investor_brief"]["canonical_binding"] = deepcopy(
        expanded["canonical_decision_identity"]
    )
    assert (
        validate_canonical_factor_binding(expanded)["risk_material_sha256"]
        != binding["risk_material_sha256"]
    )
    assert expanded["risk_notes"] != expanded["investor_brief"]["risk_notes"]
    assert validate_investor_brief_binding(expanded)["risk_notes"][-1] == "INDEPENDENT_BRIEF_RISK_D"


def _gate4_context(*, liquidity=True, execution=True, etf_specific=False):
    return {
        "schema_version": "gate4-runtime-context-v1",
        "liquidity": (
            {
                "source": "fixture_quote",
                "price": 10.5,
                "volume": 100000.0,
                "amount": 1050000.0,
                "turnover_rate": 1.2,
                "stale": False,
            }
            if liquidity
            else {}
        ),
        "execution": (
            {
                "status": "READY",
                "completed_bar_only": True,
                "source_alignment_state": "SINGLE_SOURCE",
                "observations": 48,
                "completed_through": "2026-10-23",
            }
            if execution
            else {}
        ),
        "etf_specific": {
            "status": "READY" if etf_specific else "MISSING",
        },
        "independent_action_authority": False,
    }


def test_gate4_fail_closed_package_evaluates_four_veto_lanes_and_one_canonical():
    summary = build_stock_factor_decision_summary(
        _trend(),
        method_execution_receipts={},
        gate4_context=_gate4_context(),
        include_canonical=True,
    )
    package = summary["gate4_decision_package"]

    assert package["schema_version"] == "gate4-decision-package-v1"
    assert package["state"] == "VETOED"
    assert package["canonical_action_authority"] is True
    assert package["independent_action_authority"] is False
    assert package["score_or_probability_authority"] is False
    assert package["correlation_dedup"]["status"] == "VERIFIED"
    assert package["correlation_dedup"]["group_count"] == 15
    assert set(package["vetoes"]) == {
        "DATA", "RISK", "LIQUIDITY", "EXECUTION",
    }
    assert package["vetoes"]["DATA"]["state"] == "VETO"
    assert package["vetoes"]["LIQUIDITY"]["state"] == "PASS"
    assert package["vetoes"]["EXECUTION"]["state"] == "PASS"
    assert summary["canonical_decision"]["action"] == "PASS"
    assert summary["canonical_decision"]["public_action"] == "avoid"
    assert summary["canonical_decision"]["hard_veto"] is True
    assert "GATE4_DATA_VETO" in summary["canonical_decision"]["reason_codes"]
    assert summary["investor_brief"]["gate4_decision_package"] == package
    assert validate_canonical_factor_binding(summary) == (
        summary["canonical_decision_identity"]
    )


def test_gate4_liquidity_and_execution_missing_are_independent_vetoes():
    summary = build_stock_factor_decision_summary(
        _trend(),
        method_execution_receipts={},
        gate4_context=_gate4_context(liquidity=False, execution=False),
        include_canonical=True,
    )
    package = summary["gate4_decision_package"]

    assert package["vetoes"]["LIQUIDITY"]["state"] == "VETO"
    assert package["vetoes"]["EXECUTION"]["state"] == "VETO"
    assert "GATE4_LIQUIDITY_VETO" in summary["canonical_decision"]["reason_codes"]
    assert "GATE4_EXECUTION_VETO" in summary["canonical_decision"]["reason_codes"]


def test_gate4_package_is_bound_into_factor_and_brief_and_tamper_rejects():
    summary = build_stock_factor_decision_summary(
        _trend(),
        method_execution_receipts={},
        gate4_context=_gate4_context(),
        include_canonical=True,
    )
    for target in ("factor", "brief"):
        tampered = deepcopy(summary)
        if target == "factor":
            tampered["gate4_decision_package"]["vetoes"]["DATA"][
                "state"
            ] = "PASS"
        else:
            tampered["investor_brief"]["gate4_decision_package"][
                "vetoes"
            ]["DATA"]["state"] = "PASS"
        with pytest.raises(ValueError, match="stale|risk|Gate4"):
            validate_canonical_factor_binding(tampered)
        with pytest.raises(ValueError, match="stale|risk|Gate4"):
            validate_investor_brief_binding(tampered)


def test_gate4_etf_missing_peer_valuation_and_specific_facts_veto():
    summary = build_stock_factor_decision_summary(
        _trend(),
        method_execution_receipts={},
        gate4_context=_gate4_context(),
        include_canonical=True,
        asset_type="etf",
    )
    data = summary["gate4_decision_package"]["vetoes"]["DATA"]

    assert data["state"] == "VETO"
    assert "ETF_PEER_RS_NOT_ADMITTED" in data["reason_codes"]
    assert "ETF_VALUATION_NOT_ADMITTED" in data["reason_codes"]
    assert "ETF_SPECIFIC_FACTS_NOT_ADMITTED" in data["reason_codes"]
    assert summary["canonical_decision"]["action"] == "PASS"
    assert summary["canonical_decision"]["public_action"] == "avoid"


def test_static_pe_pb_without_percentile_or_peers_does_not_claim_relative_valuation():
    summary = build_stock_factor_decision_summary(
        _trend(signal_score=68),
        fundamental_context={
            "market": "cn",
            "valuation": {"data": {"pe_ratio": 75.0, "pb_ratio": 9.0}},
        },
    )

    assert "PE 75.0" in summary["valuation"]
    assert "PB 9.0" in summary["valuation"]
    assert "缺少可靠历史分位和同行比较" in summary["valuation"]
    assert "暂不判断偏低、合理或偏高" in summary["valuation"]
    assert "静态估值偏高" not in summary["valuation"]
    assert "历史相对低位" not in summary["valuation"]
    assert "合理价值" not in summary["valuation"]
    assert "内在价值" not in summary["valuation"]


def test_volume_price_language_describes_shrink_volume_without_claiming_shakeout():
    summary = build_stock_factor_decision_summary(_trend())
    volume_text = summary["sections"]["volume_price"]

    assert "卖压有所收缩" in volume_text
    assert "关注支撑与后续转强" in volume_text
    assert "洗盘" not in volume_text
    assert "主力" not in volume_text


def test_heavy_volume_down_cannot_be_described_as_shakeout():
    summary = build_stock_factor_decision_summary(
        _trend(
            signal_score=45,
            volume_status=_enum("放量下跌"),
            volume_ratio_5d=1.9,
            trend_status=_enum("弱势空头"),
        )
    )

    assert "供应压力明显增加" in summary["sections"]["volume_price"]
    assert "洗盘" not in summary["sections"]["volume_price"]
    assert "当前不适合新增仓位" in summary["conclusion"]


def test_stock_analyzer_shrink_volume_wording_no_longer_asserts_main_force_intent():
    analyzer = StockTrendAnalyzer()
    result = TrendAnalysisResult(code="000001")
    df = pd.DataFrame(
        {
            "close": [10.0, 10.1, 10.2, 10.3, 10.4, 10.2],
            "volume": [100.0, 100.0, 100.0, 100.0, 100.0, 50.0],
        }
    )

    analyzer._analyze_volume(df, result)

    assert result.volume_status == VolumeStatus.SHRINK_VOLUME_DOWN
    assert "卖压收缩" in result.volume_trend
    assert "需后续确认" in result.volume_trend
    assert "主力" not in result.volume_trend


def _llm_result(*, advice="买入", action="buy", decision_type="buy"):
    return AnalysisResult(
        code="600519",
        name="贵州茅台",
        sentiment_score=96,
        trend_prediction="强烈看多",
        operation_advice=advice,
        decision_type=decision_type,
        action=action,
        action_label="买入",
        analysis_summary="LLM 建议立即买入",
        buy_reason="LLM 看多并建议买入",
        dashboard={
            "action": action,
            "action_label": "买入",
            "operation_advice": advice,
            "decision_type": decision_type,
            "analysis_summary": "LLM 建议立即买入",
            "buy_reason": "LLM 看多并建议买入",
            "core_conclusion": {
                "one_sentence": "立即买入",
                "position_advice": {"no_position": "买入", "has_position": "加仓"},
            },
            "phase_decision": {"immediate_action": "现在买入"},
            "strategy_synthesis": {"final_signal": "buy"},
            "battle_plan": {
                "sniper_points": {"ideal_buy": "10.00"},
                "action_checklist": ["买入"],
            },
            "decision_score_calibration": {"final_action": "buy"},
            "decision_stability": {"final_action": "buy", "reason": "LLM"},
        },
    )


def test_p0_canonical_wait_overrides_conflicting_llm_buy_in_every_action_slot():
    summary = build_stock_factor_decision_summary(
        _trend(signal_score=99), include_canonical=True
    )
    result = apply_canonical_decision_to_result(_llm_result(), summary)

    assert summary["canonical_decision"] == {
        "authority": "stock_trend_quality_pullback_v1",
        "action": "WAIT",
        "public_action": "watch",
        "evidence_state": "PROVEN",
        "hard_veto": False,
        "reason_codes": ["CONDITIONAL_OBSERVATION_ONLY"],
    }
    assert result.action == "watch"
    assert result.decision_type == "hold"
    assert result.dashboard["strategy_synthesis"]["final_signal"] == "hold"
    assert result.dashboard["decision_score_calibration"]["final_action"] == "watch"
    assert result.dashboard["decision_stability"]["final_action"] == "watch"
    assert "P0 不生成买卖点" in result.dashboard["battle_plan"]["sniper_points"]["ideal_buy"]
    assert_canonical_consumer_consistency(result)


def test_canonical_summary_binds_exact_decision_to_current_trace():
    summary = build_stock_factor_decision_summary(_trend(), include_canonical=True)

    binding = summary["canonical_decision_identity"]
    assert binding == summary["investor_brief"]["canonical_binding"]
    assert binding["strategy_id"] == "stock_trend_quality_pullback_v1"
    assert binding["canonical_decision_hash"]
    assert binding["runtime_trace_hash"] == summary["evidence_traceability"]["runtime_trace_hash"]
    assert binding["manifest_hash"] == summary["evidence_traceability"]["manifest_hash"]


def test_copy_consistency_cannot_legalize_invalid_canonical_tuple():
    summary = build_stock_factor_decision_summary(_trend(), include_canonical=True)
    summary["canonical_decision"] = {
        "authority": "stock_trend_quality_pullback_v1",
        "action": "WAIT",
        "public_action": "avoid",
        "evidence_state": "PROVEN",
        "hard_veto": False,
        "reason_codes": ["SYNTHETIC"],
    }

    with pytest.raises(ValueError, match="tuple is illegal"):
        apply_canonical_decision_to_result(_llm_result(), summary, scope="production")


def test_failed_legacy_result_stays_failed_and_cannot_own_action():
    summary = build_stock_factor_decision_summary(_trend(), include_canonical=True)
    result = _llm_result(advice="买入", action="buy", decision_type="buy")
    result.success = False

    apply_canonical_decision_to_result(result, summary, scope="production")

    assert result.success is False
    assert result.action == "watch"
    assert result.decision_type == "hold"
    assert result.sentiment_score == 96
    assert result.trend_prediction == "强烈看多"
    assert result.dashboard["strategy_synthesis"]["final_signal"] == "hold"


def test_degraded_explanation_rejects_stale_brief_binding():
    summary = build_stock_factor_decision_summary(_trend(), include_canonical=True)
    assert canonical_explanation_degradation_eligible(summary) is True

    summary["investor_brief"]["canonical_binding"]["runtime_trace_hash"] = "forged"
    assert canonical_explanation_degradation_eligible(summary) is False


def test_p0_canonical_wait_overrides_unsupported_llm_sell():
    summary = build_stock_factor_decision_summary(_trend(), include_canonical=True)
    result = _llm_result(advice="卖出", action="sell", decision_type="sell")

    apply_canonical_decision_to_result(result, summary)

    assert result.action == "watch"
    assert result.operation_advice.startswith("观望")
    assert result.dashboard["phase_decision"]["immediate_action"].startswith("观望")
    assert_canonical_consumer_consistency(result)


def test_p0_hard_veto_overrides_high_score_and_positive_llm_text():
    summary = build_stock_factor_decision_summary(
        _trend(signal_score=99, volume_status=_enum("放量下跌")),
        include_canonical=True,
    )
    result = apply_canonical_decision_to_result(_llm_result(), summary)

    assert summary["canonical_decision"]["action"] == "PASS"
    assert summary["canonical_decision"]["hard_veto"] is True
    assert "HEAVY_VOLUME_DOWN" in summary["canonical_decision"]["reason_codes"]
    assert result.action == "avoid"
    assert result.operation_advice.startswith("回避")
    assert_canonical_consumer_consistency(result)


def test_p0_missing_required_evidence_fails_closed_to_unknown_wait():
    summary = build_stock_factor_decision_summary(None, include_canonical=True)
    result = apply_canonical_decision_to_result(_llm_result(), summary)

    assert summary["composite_score"] is None
    assert summary["canonical_decision"]["action"] == "WAIT"
    assert summary["canonical_decision"]["evidence_state"] == "UNKNOWN"
    assert summary["canonical_decision"]["reason_codes"] == ["MISSING_TREND_RESULT"]
    assert result.operation_advice.startswith("观望：必需证据不足")
    assert_canonical_consumer_consistency(result)


def test_proven_market_veto_survives_missing_trend_and_reaches_public_consumer():
    summary = build_stock_factor_decision_summary(
        None,
        daily_market_context={
            "market_light": {"status": "red", "score": 20, "data_quality": "ok"}
        },
        include_canonical=True,
    )
    decision = summary["canonical_decision"]
    result = apply_canonical_decision_to_result(_llm_result(), summary, scope="production")

    assert decision["action"] == "PASS"
    assert decision["public_action"] == "avoid"
    assert decision["evidence_state"] == "PROVEN"
    assert decision["hard_veto"] is True
    assert {"MARKET_REGIME_RISK_OFF", "MISSING_TREND_RESULT"} <= set(decision["reason_codes"])
    assert result.action == "avoid"
    assert result.operation_advice.startswith("回避")
    assert summary["conclusion"].startswith("风险或弱势条件触发")
    assert_canonical_consumer_consistency(result, scope="production")


@pytest.mark.parametrize(
    ("overrides", "missing_code", "veto_code"),
    [
        ({"signal_score": None, "volume_status": _enum("放量下跌")}, "MISSING_SIGNAL_SCORE", "HEAVY_VOLUME_DOWN"),
        ({"volume_status": None, "trend_status": _enum("空头排列")}, "MISSING_VOLUME_STATE", "WEAK_TREND"),
    ],
)
def test_independent_hard_veto_survives_other_missing_required_fields(
    overrides, missing_code, veto_code
):
    summary = build_stock_factor_decision_summary(
        _trend(**overrides),
        include_canonical=True,
    )
    decision = summary["canonical_decision"]

    assert decision["action"] == "PASS"
    assert decision["public_action"] == "avoid"
    assert decision["evidence_state"] == "PROVEN"
    assert decision["hard_veto"] is True
    assert {missing_code, veto_code} <= set(decision["reason_codes"])


def test_etf_independent_market_veto_survives_missing_etf_specific_fields():
    summary = build_stock_factor_decision_summary(
        None,
        daily_market_context={
            "market_light": {"status": "red", "score": 20, "data_quality": "ok"}
        },
        asset_type="etf",
        include_canonical=True,
    )
    decision = summary["canonical_decision"]
    assert summary["market_sector_regime"]["hard_veto"] is True
    assert decision["authority"] == "etf_relative_strength_rotation_v1"
    assert decision["action"] == "PASS"
    assert decision["public_action"] == "avoid"
    assert decision["evidence_state"] == "PROVEN"
    assert decision["hard_veto"] is True
    assert {"MARKET_REGIME_RISK_OFF", "ETF_SPECIFIC_EVIDENCE_INCOMPLETE"} <= set(
        decision["reason_codes"]
    )
    assert summary["conclusion"].startswith("风险或弱势条件触发")
    result = apply_canonical_decision_to_result(_llm_result(), summary, scope="production")
    assert result.action == "avoid"
    assert_canonical_consumer_consistency(result, scope="production")


def test_strict_rejected_regime_parent_cannot_supply_canonical_veto():
    summary = build_stock_factor_decision_summary(
        None,
        daily_market_context={
            "trade_date": "2026-09-30",
            "region": "cn",
            "market_light": {"status": "red", "score": 20, "data_quality": "ok"},
        },
        market_structure_context={
            "status": "not_supported", "market": "cn", "trade_date": "2026-09-29"
        },
        method_execution_receipts={},
        include_canonical=True,
    )
    regime = next(
        item for item in summary["evidence_traceability"]["observations"]
        if item["requirement_id"] == "REGIME"
    )
    decision = summary["canonical_decision"]
    assert regime["state"] == "UNKNOWN"
    assert regime["reason"] == "REGIME_UPSTREAM_DATE_MISMATCH"
    assert summary["market_sector_regime"]["hard_veto"] is True  # raw observation, not admitted
    assert decision["action"] == "WAIT"
    assert decision["public_action"] == "watch"
    assert decision["evidence_state"] == "UNKNOWN"
    assert decision["hard_veto"] is False
    assert "REGIME_UPSTREAM_DATE_MISMATCH" in decision["reason_codes"]
    assert "MARKET_REGIME_RISK_OFF" not in decision["reason_codes"]
    result = apply_canonical_decision_to_result(_llm_result(), summary, scope="production")
    assert result.action == "watch"
    assert_canonical_consumer_consistency(result, scope="production")


def test_strict_verified_regime_parent_retains_market_veto_nontrigger():
    summary = build_stock_factor_decision_summary(
        None,
        daily_market_context={
            "trade_date": "2026-09-30",
            "region": "cn",
            "market_light": {"status": "red", "score": 20, "data_quality": "ok"},
        },
        market_structure_context={
            "status": "not_supported", "market": "cn", "trade_date": "2026-09-30"
        },
        method_execution_receipts={},
        include_canonical=True,
    )
    regime = next(
        item for item in summary["evidence_traceability"]["observations"]
        if item["requirement_id"] == "REGIME"
    )
    assert regime["state"] in {"READY", "PARTIAL"}
    assert regime["reason"] == "METHOD_INVOCATION_VERIFIED"
    assert summary["canonical_decision"]["hard_veto"] is True
    assert summary["canonical_decision"]["action"] == "PASS"


def test_etf_strict_verified_market_veto_still_avoids_new_risk():
    summary = build_stock_factor_decision_summary(
        None,
        daily_market_context={
            "trade_date": "2026-09-30", "region": "cn",
            "market_light": {"status": "red", "data_quality": "ok", "score": 20},
        },
        market_structure_context={
            "status": "not_supported", "market": "cn", "trade_date": "2026-09-30"
        },
        method_execution_receipts={},
        asset_type="etf",
        include_canonical=True,
    )
    parent = next(
        item for item in summary["evidence_traceability"]["observations"]
        if item["requirement_id"] == "REGIME"
    )
    assert parent["state"] in {"READY", "PARTIAL"}
    assert parent["reason"] == "METHOD_INVOCATION_VERIFIED"
    decision = summary["canonical_decision"]
    assert decision["hard_veto"] is True
    assert decision["action"] == "PASS"
    assert {"MARKET_REGIME_RISK_OFF", "ETF_SPECIFIC_EVIDENCE_INCOMPLETE"} <= set(
        decision["reason_codes"]
    )


def test_rejected_market_parent_does_not_erase_independent_weak_trend_veto():
    summary = build_stock_factor_decision_summary(
        _trend(code="600519", trend_status=_enum("空头排列")),
        relative_strength_context=_strict_verified_relative_strength(),
        daily_market_context={
            "trade_date": "2026-09-30", "region": "cn",
            "market_light": {"status": "red", "data_quality": "ok", "score": 20},
        },
        market_structure_context={
            "status": "not_supported", "market": "cn", "trade_date": "2026-09-29"
        },
        method_execution_receipts={},
        include_canonical=True,
    )
    regime = next(
        item for item in summary["evidence_traceability"]["observations"]
        if item["requirement_id"] == "REGIME"
    )
    assert regime["state"] == "UNKNOWN"
    assert regime["reason"] == "REGIME_UPSTREAM_DATE_MISMATCH"
    decision = summary["canonical_decision"]
    assert decision["action"] == "PASS"
    assert decision["hard_veto"] is True
    assert "WEAK_TREND" in decision["reason_codes"]
    assert "REGIME_UPSTREAM_DATE_MISMATCH" in decision["reason_codes"]
    assert "MARKET_REGIME_RISK_OFF" not in decision["reason_codes"]


def test_strict_unadmitted_weak_trend_does_not_create_proven_veto():
    trend = _trend(
        code="600519",
        trend_status=_enum("空头排列"),
        buy_signal=_enum("持有"),
    )
    invalid_benchmark = _strict_verified_relative_strength()
    invalid_benchmark["benchmark"]["code"] = "159919"
    for rs_context, reason in (
        (None, "RS_INPUT_IDENTITY_MISSING"),
        (invalid_benchmark, "RS_BENCHMARK_IDENTITY_MISMATCH"),
    ):
        summary = build_stock_factor_decision_summary(
            trend,
            relative_strength_context=rs_context,
            method_execution_receipts={},
            include_canonical=True,
        )
        parent = next(
            item for item in summary["evidence_traceability"]["observations"]
            if item["requirement_id"] == "TREND_RS"
        )
        assert parent["state"] == "UNKNOWN"
        assert parent["reason"] == reason
        canonical = summary["canonical_decision"]
        assert canonical["action"] == "WAIT"
        assert canonical["public_action"] == "watch"
        assert canonical["hard_veto"] is False
        assert canonical["evidence_state"] == "UNKNOWN"
        assert reason in canonical["reason_codes"]
        assert "WEAK_TREND" not in canonical["reason_codes"]
        result = apply_canonical_decision_to_result(
            _llm_result(), summary, scope="production"
        )
        assert result.action == "watch"
        assert_canonical_consumer_consistency(result, scope="production")


def test_strict_unadmitted_legacy_volume_cannot_claim_proven_heavy_down():
    summary = build_stock_factor_decision_summary(
        _trend(
            code="600519",
            trend_status=_enum("多头排列"),
            buy_signal=_enum("持有"),
            volume_status=_enum("放量下跌"),
        ),
        relative_strength_context=_strict_verified_relative_strength(),
        method_execution_receipts={},
        include_canonical=True,
    )
    observations = {
        item["requirement_id"]: item
        for item in summary["evidence_traceability"]["observations"]
    }
    assert observations["TREND_RS"]["reason"] == "METHOD_INVOCATION_VERIFIED"
    assert observations["SUPPLY"]["state"] == "UNKNOWN"
    assert observations["SUPPLY"]["reason"] == "METHOD_VERSION_MISMATCH"
    canonical = summary["canonical_decision"]
    assert canonical["action"] == "WAIT"
    assert canonical["hard_veto"] is False
    assert canonical["evidence_state"] == "UNKNOWN"
    assert "METHOD_VERSION_MISMATCH" in canonical["reason_codes"]
    assert "HEAVY_VOLUME_DOWN" not in canonical["reason_codes"]


def test_verified_trend_with_no_market_or_supply_veto_remains_proven_avoid():
    summary = build_stock_factor_decision_summary(
        _trend(
            code="600519", trend_status=_enum("空头排列"),
            buy_signal=_enum("持有"), volume_status=_enum("缩量回调"),
        ),
        relative_strength_context=_strict_verified_relative_strength(),
        method_execution_receipts={},
        include_canonical=True,
    )
    parent = next(
        item for item in summary["evidence_traceability"]["observations"]
        if item["requirement_id"] == "TREND_RS"
    )
    assert parent["state"] == "READY"
    assert parent["reason"] == "METHOD_INVOCATION_VERIFIED"
    decision = summary["canonical_decision"]
    assert decision["action"] == "PASS"
    assert decision["hard_veto"] is True
    assert "WEAK_TREND" in decision["reason_codes"]


def test_market_regime_red_ready_is_canonical_veto_but_green_never_upgrades_wait():
    red = build_stock_factor_decision_summary(
        _trend(signal_score=99),
        daily_market_context={"market_light": {"status": "red", "score": 25, "data_quality": "ok"}},
        include_canonical=True,
    )
    evidence = red["market_sector_regime"]
    assert evidence["market"]["state"] == "RISK_OFF"
    assert evidence["hard_veto"] is True
    assert red["canonical_decision"]["action"] == "PASS"
    assert "MARKET_REGIME_RISK_OFF" in red["canonical_decision"]["reason_codes"]

    green = build_stock_factor_decision_summary(
        _trend(signal_score=99),
        daily_market_context={"market_light": {"status": "green", "score": 82, "data_quality": "ok"}},
        include_canonical=True,
    )
    assert green["market_sector_regime"]["market"]["state"] == "PERMISSIVE"
    assert green["market_sector_regime"]["hard_veto"] is False
    assert green["canonical_decision"]["action"] == "WAIT"


def test_partial_red_and_sector_cooling_are_caution_not_hard_veto():
    summary = build_stock_factor_decision_summary(
        _trend(signal_score=88),
        daily_market_context={"market_light": {"status": "red", "score": 30, "data_quality": "partial"}},
        market_structure_context={
            "status": "partial",
            "stock_market_position": {
                "status": "partial",
                "primary_theme": {"name": "机器人概念", "phase": "cooling"},
                "theme_phase": "cooling",
                "stock_role": "edge",
                "risk_tags": [{"code": "theme_data_partial"}],
            },
        },
        include_canonical=True,
    )
    evidence = summary["market_sector_regime"]
    assert evidence["evidence_state"] == "PARTIAL"
    assert evidence["market"]["state"] == "CAUTION"
    assert evidence["sector"]["state"] == "COOLING"
    assert evidence["hard_veto"] is False
    assert "SECTOR_THEME_COOLING" in evidence["reason_codes"]
    assert summary["canonical_decision"]["action"] == "WAIT"



def test_m2_verified_runtime_index_source_advances_only_m_index_current_to_partial() -> None:
    from datetime import date
    from src.services.daily_market_context import DailyMarketContext
    from src.services.evidence_traceability_registry import compile_complete_research_universe_view

    observation = {
        "dataset_id": "M_INDEX_CURRENT",
        "source_code": "sh.000001",
        "source_state": "PARTIAL",
        "provider_identity": "BaostockFetcher",
        "completed_through": "2026-09-30",
        "observed_at": "2026-10-01T08:00:00+00:00",
        "content_sha256": "a" * 64,
        "identity_hash": "b" * 64,
        "source_data_rights": "UNKNOWN",
        "volume_unit": "UNKNOWN",
        "amount_unit": "UNKNOWN",
        "historical_pit_eligible": False,
        "independent_source_value_admitted": False,
        "strategy_upgrade_authority": False,
    }
    context = DailyMarketContext(
        region="cn",
        trade_date=date(2026, 9, 30),
        summary="市场震荡，等待确认。",
        source="market_review_runtime",
        market_light={"status": "yellow", "data_quality": "partial", "score": 45},
        cn_index_completed_close_source=observation,
    )
    with_source = build_stock_factor_decision_summary(
        _trend(signal_score=82),
        daily_market_context=context,
        market_structure_context={
            "status": "not_supported", "market": "cn", "trade_date": "2026-09-30"
        },
        method_execution_receipts={},
        include_canonical=True,
    )
    without_source = build_stock_factor_decision_summary(
        _trend(signal_score=82),
        daily_market_context=DailyMarketContext(
            region="cn",
            trade_date=date(2026, 9, 30),
            summary="市场震荡，等待确认。",
            source="market_review_runtime",
            market_light={"status": "yellow", "data_quality": "partial", "score": 45},
        ),
        market_structure_context={
            "status": "not_supported", "market": "cn", "trade_date": "2026-09-30"
        },
        method_execution_receipts={},
        include_canonical=True,
    )
    assert with_source["canonical_decision"] == without_source["canonical_decision"]

    objects = {
        row["dataset_id"]: row
        for row in compile_complete_research_universe_view(with_source)["data_objects"]
    }
    index_row = objects["M_INDEX_CURRENT"]
    assert index_row["state"] == "PARTIAL"
    assert index_row["reason"] == "CURRENT_SOURCE_IDENTITY_VERIFIED_VALUE_NOT_ADMITTED"
    assert index_row["provider_identity"] == "BaostockFetcher"
    assert index_row["completed_through"] == "2026-09-30"
    assert index_row["source_data_rights"] == "UNKNOWN"
    assert index_row["independent_source_value_admitted"] is False
    assert index_row["strategy_upgrade_authority"] is False
    assert objects["M_INDEX_BARS"]["state"] == "UNKNOWN"
    assert objects["M_BASIC_BREADTH"]["state"] == "UNKNOWN"

    history_context = DailyMarketContext(
        region="cn",
        trade_date=date(2026, 9, 30),
        summary="历史摘要。",
        source="analysis_history",
        market_light={"status": "yellow", "data_quality": "partial", "score": 45},
        cn_index_completed_close_source=observation,
    )
    historical = build_stock_factor_decision_summary(
        _trend(signal_score=82),
        daily_market_context=history_context,
        market_structure_context={
            "status": "not_supported", "market": "cn", "trade_date": "2026-09-30"
        },
        method_execution_receipts={},
        include_canonical=True,
    )
    history_objects = {
        row["dataset_id"]: row
        for row in compile_complete_research_universe_view(historical)["data_objects"]
    }
    assert history_objects["M_INDEX_CURRENT"]["state"] == "UNKNOWN"

def test_market_sector_regime_missing_inputs_stay_unknown_without_changing_legacy_decision():
    summary = build_stock_factor_decision_summary(_trend(signal_score=82), include_canonical=True)
    evidence = summary["market_sector_regime"]
    assert evidence["evidence_state"] == "UNKNOWN"
    assert evidence["hard_veto"] is False
    assert summary["canonical_decision"]["action"] == "WAIT"
    assert summary["canonical_decision"]["reason_codes"] == ["CONDITIONAL_OBSERVATION_ONLY"]

    unqualified_red = build_stock_factor_decision_summary(
        _trend(signal_score=99),
        daily_market_context={"market_light": {"status": "red", "score": 25}},
        include_canonical=True,
    )
    assert unqualified_red["market_sector_regime"]["market"]["state"] == "UNKNOWN"
    assert unqualified_red["market_sector_regime"]["hard_veto"] is False
    assert unqualified_red["canonical_decision"]["action"] == "WAIT"


def test_relative_strength_is_first_class_evidence_but_never_independently_upgrades_or_vetoes():
    positive = build_stock_factor_decision_summary(
        _trend(signal_score=99),
        relative_strength_context={
            "status": "READY",
            "horizon_sessions": 60,
            "benchmark": {"code": "510300", "name": "华泰柏瑞沪深300ETF", "kind": "etf_proxy"},
            "relative": {"state": "OUTPERFORMING", "relative_ratio_change_pct": 8.5},
        },
        include_canonical=True,
    )
    evidence = positive["trend_relative_strength"]
    assert evidence["evidence_state"] == "READY"
    assert evidence["relative_strength"]["benchmark"]["kind"] == "etf_proxy"
    assert evidence["hard_veto"] is False
    assert positive["canonical_decision"]["action"] == "WAIT"

    negative = build_stock_factor_decision_summary(
        _trend(signal_score=99),
        relative_strength_context={
            "status": "READY",
            "horizon_sessions": 60,
            "benchmark": {"code": "510300", "name": "华泰柏瑞沪深300ETF", "kind": "etf_proxy"},
            "relative": {"state": "UNDERPERFORMING", "relative_ratio_change_pct": -12.0},
        },
        include_canonical=True,
    )
    assert negative["trend_relative_strength"]["relative_strength"]["relative"]["state"] == "UNDERPERFORMING"
    assert negative["trend_relative_strength"]["hard_veto"] is False
    assert negative["canonical_decision"]["action"] == "WAIT"


def test_missing_relative_strength_preserves_legacy_decision_and_weak_completed_trend_still_vetoes():
    missing = build_stock_factor_decision_summary(_trend(signal_score=82), include_canonical=True)
    assert missing["trend_relative_strength"]["evidence_state"] == "PARTIAL"
    assert missing["trend_relative_strength"]["relative_strength"]["status"] == "MISSING"
    assert missing["canonical_decision"]["reason_codes"] == ["CONDITIONAL_OBSERVATION_ONLY"]

    weak = build_stock_factor_decision_summary(
        _trend(signal_score=99, trend_status=_enum("空头排列")),
        relative_strength_context={
            "status": "READY",
            "benchmark": {"code": "510300", "kind": "etf_proxy"},
            "relative": {"state": "OUTPERFORMING", "relative_ratio_change_pct": 5.0},
        },
        include_canonical=True,
    )
    assert weak["canonical_decision"]["action"] == "PASS"
    assert "WEAK_TREND" in weak["canonical_decision"]["reason_codes"]


def test_supply_demand_is_first_class_observable_evidence_without_new_action_authority():
    summary = build_stock_factor_decision_summary(
        _trend(signal_score=99, volume_status=_enum("放量上涨"), volume_ratio_5d=1.8),
        supply_demand_context={
            "status": "READY",
            "state": "DEMAND_PRESSURE",
            "relative_volume": {"volume_ratio_20d": 1.6},
            "directional_volume": {"signed_volume_balance": 0.35},
            "close_location_flow": {"cmf_20": 0.18},
            "institutional_intent_claim": "NOT_INFERRED",
        },
        include_canonical=True,
    )
    evidence = summary["supply_demand_volume_price"]
    assert evidence["evidence_state"] == "READY"
    assert evidence["observable_only"] is True
    assert evidence["institutional_intent_inferred"] is False
    assert evidence["hard_veto"] is False
    assert summary["canonical_decision"]["action"] == "WAIT"
    assert "需求压力占优" in summary["sections"]["supply_demand_volume_price"]
    assert "机构意图" not in summary["sections"]["supply_demand_volume_price"]
    assert "主力" not in str(evidence)
    assert "吸筹" not in str(evidence)
    assert "出货" not in str(evidence)


def test_supply_demand_partial_or_supply_pressure_does_not_duplicate_existing_volume_veto():
    summary = build_stock_factor_decision_summary(
        _trend(signal_score=99, volume_status=_enum("量能正常"), volume_ratio_5d=1.0),
        supply_demand_context={
            "status": "PARTIAL",
            "state": "SUPPLY_PRESSURE",
            "relative_volume": {"volume_ratio_20d": 1.7},
            "directional_volume": {"signed_volume_balance": -0.5},
            "close_location_flow": {"cmf_20": -0.3},
            "reason": "SOURCE_ALIGNMENT_UNPROVEN",
        },
        include_canonical=True,
    )
    assert summary["supply_demand_volume_price"]["evidence_state"] == "PARTIAL"
    assert summary["supply_demand_volume_price"]["hard_veto"] is False
    assert summary["canonical_decision"]["action"] == "WAIT"
    assert summary["canonical_decision"]["reason_codes"] == ["CONDITIONAL_OBSERVATION_ONLY"]

    legacy_veto = build_stock_factor_decision_summary(
        _trend(signal_score=99, volume_status=_enum("放量下跌"), volume_ratio_5d=1.9),
        supply_demand_context={"status": "READY", "state": "SUPPLY_PRESSURE"},
        include_canonical=True,
    )
    assert legacy_veto["canonical_decision"]["action"] == "PASS"
    assert legacy_veto["canonical_decision"]["reason_codes"].count("HEAVY_VOLUME_DOWN") == 1


def test_cost_structure_is_first_class_but_never_independent_action_authority():
    summary = build_stock_factor_decision_summary(
        _trend(signal_score=99),
        chip_data=SimpleNamespace(avg_cost=10.0, concentration_90=0.18, profit_ratio=0.64),
        cost_structure_context={
            "status": "READY",
            "provider_chip_snapshot": {
                "status": "READY_CURRENT_ONLY",
                "provider_reference_avg_cost": 10.0,
                "provider_profit_ratio": 0.64,
                "historical_pit_authority": "CURRENT_ONLY",
            },
            "bar_reference_cost": {
                "status": "READY",
                "window_20": {"status": "READY", "rolling_reference_price": 10.2},
                "window_60": {"status": "READY", "rolling_reference_price": 9.8},
            },
            "composition": {"provider_vs_bar_relation": "CONVERGENT"},
        },
        include_canonical=True,
    )
    evidence = summary["cost_structure_evidence"]
    assert evidence["evidence_state"] == "READY"
    assert evidence["hard_veto"] is False
    assert evidence["independent_action_authority"] is False
    assert summary["canonical_decision"]["action"] == "WAIT"
    assert "20日历史量价参考" in summary["sections"]["cost_structure"]
    assert evidence["observable_only"] is True
    assert evidence["institutional_intent_inferred"] is False
    assert "真实持仓成本" not in summary["sections"]["cost_structure"]
    assert "主力" not in str(evidence)
    assert "吸筹" not in str(evidence)
    assert "出货" not in str(evidence)


def test_provider_only_or_divergent_cost_structure_does_not_veto_or_upgrade():
    provider_only = build_stock_factor_decision_summary(
        _trend(signal_score=99),
        chip_data=SimpleNamespace(avg_cost=10.0, concentration_90=0.18, profit_ratio=0.64),
        include_canonical=True,
    )
    assert provider_only["cost_structure_evidence"]["evidence_state"] == "PARTIAL"
    assert provider_only["canonical_decision"]["action"] == "WAIT"

    divergent = build_stock_factor_decision_summary(
        _trend(signal_score=99),
        cost_structure_context={
            "status": "READY",
            "provider_chip_snapshot": {"status": "READY_CURRENT_ONLY", "provider_reference_avg_cost": 8.0},
            "bar_reference_cost": {
                "status": "READY",
                "window_20": {"status": "READY", "rolling_reference_price": 12.0},
                "window_60": {"status": "READY", "rolling_reference_price": 11.5},
            },
            "composition": {"provider_vs_bar_relation": "DIVERGENT"},
        },
        include_canonical=True,
    )
    assert divergent["cost_structure_evidence"]["hard_veto"] is False
    assert divergent["canonical_decision"]["action"] == "WAIT"


def test_price_structure_is_first_class_but_never_independent_action_authority():
    summary = build_stock_factor_decision_summary(
        _trend(signal_score=99),
        price_structure_context={
            "status": "READY",
            "nearest_support": {"status": "READY", "price": 10.1, "origin_time": "2026-01-05", "confirmed_at": "2026-01-07"},
            "nearest_resistance": {"status": "READY", "price": 11.2, "origin_time": "2026-01-08", "confirmed_at": "2026-01-12"},
            "range_state": "BETWEEN_CONFIRMED_LEVELS",
            "structure_event": {"state": "UP_BREAKOUT_RETEST_HOLD", "level": 10.8, "provisional": False},
            "pivots": [],
            "swings": [],
        },
        include_canonical=True,
    )
    evidence = summary["price_structure_evidence"]
    assert evidence["evidence_state"] == "READY"
    assert evidence["hard_veto"] is False
    assert evidence["independent_action_authority"] is False
    assert summary["canonical_decision"]["action"] == "WAIT"
    assert "已确认支撑 10.10" in summary["sections"]["price_structure"]
    assert "突破后回踩" in summary["sections"]["price_structure"]
    assert summary["investor_brief"]["key_levels"]["support"] == "10.10"
    assert summary["investor_brief"]["key_levels"]["resistance"] == "11.20"


def test_missing_structured_price_context_preserves_legacy_level_projection():
    summary = build_stock_factor_decision_summary(_trend(signal_score=68), include_canonical=True)
    evidence = summary["price_structure_evidence"]
    assert evidence["evidence_state"] == "PARTIAL"
    assert evidence["context"]["reason"] == "LEGACY_LEVELS_ONLY"
    assert "主要支撑参考 10.20" in summary["sections"]["price_structure"]
    assert summary["canonical_decision"]["action"] == "WAIT"


def test_structured_price_structure_missing_support_does_not_fall_back_to_legacy_level():
    summary = build_stock_factor_decision_summary(
        _trend(signal_score=68, support_levels=[10.0, 10.2]),
        price_structure_context={
            "status": "PARTIAL",
            "reason": "NO_CONFIRMED_PIVOTS",
            "nearest_support": {"status": "MISSING"},
            "nearest_resistance": {"status": "MISSING"},
            "range_state": "OPEN_STRUCTURE",
            "structure_event": {"state": "NONE", "provisional": False},
        },
        include_canonical=True,
    )
    assert "10.20" not in summary["action_condition"]
    assert "主要支撑 10.20" not in summary["invalidation_condition"]
    assert summary["investor_brief"]["key_levels"]["support"] is None


def test_volatility_momentum_is_first_class_observational_evidence_without_action_authority():
    summary = build_stock_factor_decision_summary(
        _trend(signal_score=99),
        volatility_momentum_context={
            "status": "READY",
            "timeframe": "1d",
            "volatility": {
                "status": "READY",
                "realized_volatility_20d_annualized_pct": 22.5,
                "true_range_sma_20_pct": 2.3,
                "true_range_semantics": "SIMPLE_MEAN_TRUE_RANGE_20_NOT_WILDER_ATR",
                "atr_wilder_14": 2.1,
                "atr_wilder_14_pct": 1.7,
                "atr_wilder_period": 14,
                "atr_wilder_semantics": "TR_TRANSITIONS_1_TO_14_MEAN_THEN_WILDER_RMA_ALPHA_1_OVER_14",
            },
            "momentum": {
                "status": "READY",
                "roc_20_pct": 6.2,
                "roc_60_pct": 12.4,
                "macd": {"status": "多头"},
                "rsi": {"status": "中性"},
            },
            "confirmed_divergence": {
                "status": "READY",
                "state": "BEARISH_DIVERGENCE",
                "correlation_group": "same_swing:high:2026-01-01:2026-02-01",
                "signals": [
                    {"oscillator": "MACD_DIF", "correlation_group": "same_swing:high:2026-01-01:2026-02-01"},
                    {"oscillator": "RSI_12", "correlation_group": "same_swing:high:2026-01-01:2026-02-01"},
                ],
                "independent_confirmation_group_count": 1,
                "same_swing_double_counting": "PROHIBITED",
            },
            "process_diagnostics": {
                "status": "READY",
                "classification_authority": "OBSERVATIONAL_ONLY",
                "world_classification": "NOT_PERFORMED",
                "automatic_strategy_switching": False,
            },
        },
        include_canonical=True,
    )
    evidence = summary["volatility_momentum_evidence"]
    assert evidence["evidence_state"] == "READY"
    assert evidence["hard_veto"] is False
    assert evidence["independent_action_authority"] is False
    assert evidence["automatic_strategy_switching"] is False
    assert summary["canonical_decision"]["action"] == "WAIT"
    assert "20日实现波动率年化 22.5%" in summary["sections"]["momentum"]
    assert "Wilder ATR14/现价 1.7%" in summary["sections"]["momentum"]
    assert "20日真实波幅简单均值/现价 2.3%" in summary["sections"]["momentum"]
    assert "顶背离" in summary["sections"]["momentum"]
    assert "MACD 多头" in summary["sections"]["momentum"]
    assert "RSI 中性" in summary["sections"]["momentum"]
    assert evidence["version"] == "volatility-momentum-v2"
    assert "不自动切换策略" not in summary["sections"]["momentum"]
    assert evidence["automatic_strategy_switching"] is False


def test_missing_structured_volatility_momentum_context_rejects_unbound_legacy_macd_free_text():
    summary = build_stock_factor_decision_summary(_trend(signal_score=68), include_canonical=True)
    evidence = summary["volatility_momentum_evidence"]
    assert evidence["evidence_state"] == "PARTIAL"
    assert evidence["context"]["reason"] == "LEGACY_MACD_RSI_ONLY"
    assert "MACD多头结构" not in summary["sections"]["momentum"]
    assert "RSI中性偏强" in summary["sections"]["momentum"]
    assert summary["canonical_decision"]["action"] == "WAIT"


def test_pattern_trigger_is_first_class_observational_evidence_and_reaches_investor_brief():
    summary = build_stock_factor_decision_summary(
        _trend(signal_score=99),
        pattern_trigger_context={
            "status": "READY",
            "reason": "PATTERN_TRIGGER_READY",
            "historical_replay_eligible": True,
            "patterns": [
                {
                    "pattern_type": "DOUBLE_BOTTOM_BASE",
                    "subtype": None,
                    "geometry_state": "READY",
                    "lifecycle": "CONFIRMED",
                    "origin_time": "2026-01-05",
                    "geometry_confirmed_at": "2026-02-02",
                    "confirmed_at": "2026-02-06",
                    "volume_evidence_ref": {"confirmation": "CONFIRMED", "volume_ratio_20d": 1.4},
                    "price_structure_trigger_ref": {"status": "READY", "state": "UP_BREAKOUT"},
                }
            ],
            "primary_pattern": {
                "pattern_type": "DOUBLE_BOTTOM_BASE",
                "subtype": None,
                "geometry_state": "READY",
                "lifecycle": "CONFIRMED",
                "origin_time": "2026-01-05",
                "geometry_confirmed_at": "2026-02-02",
                "confirmed_at": "2026-02-06",
                "volume_evidence_ref": {"confirmation": "CONFIRMED", "volume_ratio_20d": 1.4},
                "price_structure_trigger_ref": {"status": "READY", "state": "UP_BREAKOUT"},
            },
        },
        include_canonical=True,
    )
    evidence = summary["pattern_trigger_evidence"]
    assert evidence["evidence_state"] == "READY"
    assert evidence["hard_veto"] is False
    assert evidence["independent_action_authority"] is False
    assert evidence["automatic_strategy_switching"] is False
    assert summary["canonical_decision"]["action"] == "WAIT"
    assert "双底基底" in summary["sections"]["pattern_trigger"]
    assert "已经突破确认" in summary["sections"]["pattern_trigger"]
    assert "Price Structure" not in summary["sections"]["pattern_trigger"]
    assert "独立动作权限" not in summary["sections"]["pattern_trigger"]
    assert "双底基底" in summary["investor_brief"]["fused_paragraph"]


def test_p0_non_conflicting_explanation_is_retained_but_not_action_authority():
    summary = build_stock_factor_decision_summary(
        _trend(signal_score=68), include_canonical=True
    )
    result = _llm_result(advice="观望", action="watch", decision_type="hold")
    result.technical_analysis = "均线结构改善，但仍需确认。"

    apply_canonical_decision_to_result(result, summary)

    assert result.technical_analysis == "均线结构改善，但仍需确认。"
    assert result.action == "watch"
    assert_canonical_consumer_consistency(result)

# ASSET_RESEARCH_BRIEF_PAYLOAD_V1_R002_TESTS
from types import SimpleNamespace as _AssetBriefPayloadNamespace
from src.services.factor_decision_summary import (
    _build_asset_research_brief_v1 as _asset_brief_payload_builder,
)
from src.services.multi_timeframe_structure_service import _human_summary as _mtf_human_summary


def test_asset_research_brief_payload_v1_is_daily_first_and_fail_closed():
    trend = _AssetBriefPayloadNamespace(
        current_price=10.5,
        support_levels=[10.0, 10.2],
        resistance_levels=[11.3],
    )
    summary = {
        "sections": {
            "trend": "\u8d8b\u52bf\uff1a\u5747\u7ebf\u7ed3\u6784\u504f\u5f3a",
            "volume_price": "\u91cf\u4ef7\uff1a\u7f29\u91cf\u56de\u8c03",
            "cost_structure": "\u6210\u672c/\u7b79\u7801\uff1a90%\u7b79\u7801\u96c6\u4e2d\u5ea6 12.00\uff0c\u83b7\u5229\u7b79\u7801\u7ea6 68%",
            "price_structure": "\u7ed3\u6784\uff1a\u4e3b\u8981\u652f\u6491\u4ecd\u6709\u6548",
            "valuation": "\u4f30\u503c\uff1a\u6570\u636e\u4e0d\u8db3",
        },
        "conclusion": "\u504f\u5f3a\uff0c\u7b49\u5f85\u786e\u8ba4\u3002",
        "action_condition": "\u653e\u91cf\u7ad9\u7a33\u538b\u529b\u4f4d\u518d\u5347\u7ea7\u3002",
        "invalidation_condition": "\u6709\u6548\u8dcc\u7834\u652f\u6491\u5219\u5931\u6548\u3002",
        "risks": ["\u7a81\u7834\u91cf\u80fd\u4ecd\u9700\u786e\u8ba4\u3002"],
        "canonical_decision": {
            "authority": "stock_trend_quality_pullback_v1",
            "action": "PASS",
            "public_action": "watch",
            "evidence_state": "PROVEN",
            "hard_veto": False,
            "reason_codes": [],
        },
    }

    brief = _asset_brief_payload_builder(trend, summary)

    assert brief["one_line_conclusion"] == summary["conclusion"]
    assert brief["canonical"] == summary["canonical_decision"]
    assert brief["coverage"] == {
        "monthly": "MISSING",
        "weekly": "MISSING",
        "daily": "PARTIAL_CURRENT",
        "60m": "MISSING",
        "30m": "MISSING",
        "15m": "MISSING",
        "5m": "MISSING",
    }
    assert brief["current_price"] == {"value": 10.5, "source_state": "AVAILABLE"}
    assert "当前价格" not in brief["fused_paragraph"]
    assert brief["coverage_text"] == (
    "本次可用周期：日线。月线、周线、60分钟、30分钟、15分钟、5分钟本次暂无可用证据。"
)
    assert brief["historical_reference"]["available"] is False
    assert brief["current_probability"]["available"] is False
    assert brief["key_levels"]["support_label"] == "结构支撑"
    assert brief["key_levels"]["resistance_label"] == "结构压力"
    assert brief["timeframe_thesis"]["daily"]["role"] == "PRIMARY_SETUP"
    assert brief["short_term_execution_panel"] == {
        "status": "MISSING",
        "state": "DATA_INSUFFICIENT",
        "30m": {"status": "MISSING", "role": "PRIMARY_STRUCTURE"},
        "15m": {"status": "MISSING", "role": "TRIGGER_CONFIRMATION"},
        "5m": {"status": "MISSING", "role": "MICRO_TIMING"},
        "summary": None,
    }
    assert brief["scenario"]["preferred"]["status"] == "READY"
    assert brief["scenario"]["alternative"]["status"] == "MISSING"
    assert brief["scenario"]["invalidation"]["status"] == "READY"
    assert (
        brief["evidence_policy"]["legacy_signal_score_role"]
        == "REFERENCE_ONLY_NOT_CANONICAL_VOTE_COUNT"
    )
    assert (
        brief["evidence_policy"]["correlation_rule"]
        == "SAME_UNDERLYING_SWING_ONE_FAMILY_CONFIRMATION_OR_CONFLICT"
    )


def test_asset_research_brief_payload_v1_missing_values_are_not_invented():
    trend = _AssetBriefPayloadNamespace(
        current_price=None,
        support_levels=[],
        resistance_levels=[],
    )
    brief = _asset_brief_payload_builder(
        trend,
        {
            "sections": {},
            "conclusion": "\u6570\u636e\u4e0d\u8db3\uff0c\u6682\u4e0d\u5224\u65ad\u3002",
            "canonical_decision": {
                "authority": "stock_trend_quality_pullback_v1",
                "action": "WAIT",
                "public_action": "watch",
                "evidence_state": "UNKNOWN",
                "hard_veto": True,
                "reason_codes": ["DATA_INSUFFICIENT"],
            },
        },
    )

    assert brief["current_price"]["source_state"] == "MISSING"
    assert brief["key_levels"] == {
        "support": None,
        "resistance": None,
        "support_label": "结构支撑",
        "resistance_label": "结构压力",
    }
    assert brief["valuation"]["status"] == "MISSING"
    assert brief["historical_reference"]["available"] is False
    assert brief["historical_reference"].get("n") is None
    assert brief["current_probability"]["available"] is False
    assert brief["current_probability"].get("value") is None



def test_canonical_binding_accepts_exact_json_wire_roundtrip_but_rejects_stale_action():
    summary = build_stock_factor_decision_summary(_trend(), include_canonical=True)
    stored = json.loads(json.dumps(summary, ensure_ascii=False))
    identity = validate_canonical_factor_binding(stored)
    assert identity["schema_version"] == "canonical-decision-binding-v1"
    assert identity["runtime_trace_hash"] == summary["evidence_traceability"]["runtime_trace_hash"]

    stale = json.loads(json.dumps(stored, ensure_ascii=False))
    stale["canonical_decision"]["public_action"] = "avoid"
    with pytest.raises(ValueError):
        validate_canonical_factor_binding(stale)


def test_explanation_degradation_requires_proven_canonical_evidence_and_valid_brief():
    proven = build_stock_factor_decision_summary(_trend(), include_canonical=True)
    assert canonical_explanation_degradation_eligible(proven) is True

    unknown = build_stock_factor_decision_summary(None, include_canonical=True)
    assert canonical_explanation_degradation_eligible(unknown) is False

    missing_brief = dict(proven)
    missing_brief["investor_brief"] = None
    assert canonical_explanation_degradation_eligible(missing_brief) is False


def test_direct_builder_does_not_forge_pipeline_execution_receipts():
    direct = build_stock_factor_decision_summary(_trend(), include_canonical=True)
    assert "method_execution_receipt_policy" not in direct
    assert "method_execution_receipts" not in direct

    strict = build_stock_factor_decision_summary(
        _trend(),
        method_execution_receipts={},
        include_canonical=True,
    )
    assert strict["method_execution_receipt_policy"] == "REQUIRED"
    assert set(strict["method_execution_receipts"]) == {"REGIME", "TREND_RS"}
    trace = strict["evidence_traceability"]
    supply = next(item for item in trace["observations"] if item["requirement_id"] == "SUPPLY")
    assert supply["state"] in {"MISSING", "UNKNOWN"}
    assert supply["reason"] != "METHOD_INVOCATION_VERIFIED"


def test_lower_timeframe_schema_cannot_override_higher_level_canonical_veto():
    summary = build_stock_factor_decision_summary(
        _trend(trend_status=_enum("空头排列")),
        include_canonical=True,
    )
    assert summary["canonical_decision"]["action"] == "PASS"
    brief = summary["investor_brief"]
    brief["short_term_execution_panel"] = {
        "status": "READY",
        "state": "TRIGGERED",
        "30m": {"status": "READY", "role": "PRIMARY_STRUCTURE"},
        "15m": {"status": "READY", "role": "TRIGGER_CONFIRMATION"},
        "5m": {"status": "READY", "role": "MICRO_TIMING"},
        "summary": "低周期转强，仅作时点。",
    }
    assert summary["canonical_decision"]["action"] == "PASS"


def test_multi_timeframe_context_fills_existing_brief_seam_without_inventing_intraday():
    trend = _AssetBriefPayloadNamespace(
        current_price=10.5,
        support_levels=[10.0],
        resistance_levels=[11.0],
    )
    summary = {
        "sections": {
            "trend": "趋势：日线均线结构偏强",
            "volume_price": "量价：量能正常",
        },
        "conclusion": "保持观察。",
        "multi_timeframe_structure_context": {
            "timeframes": {
                "monthly": {
                    "status": "MISSING",
                    "reason": "WARMUP_INSUFFICIENT",
                    "role": "LONG_TERM_CONTEXT",
                    "summary": None,
                },
                "weekly": {
                    "status": "READY",
                    "reason": "TIMEFRAME_READY",
                    "role": "PRIMARY_TREND_CONTEXT",
                    "summary": "周线多头排列 MA5>MA10>MA20；量能正常",
                },
                "60m": {
                    "status": "MISSING",
                    "reason": "INTRADAY_COMPLETED_BAR_CONTRACT_NOT_READY",
                    "role": "OPTIONAL_BRIDGE",
                    "summary": None,
                },
            }
        },
    }

    brief = _asset_brief_payload_builder(trend, summary)
    assert brief["coverage"]["weekly"] == "READY"
    assert brief["coverage"]["monthly"] == "MISSING"
    assert brief["timeframe_thesis"]["weekly"]["summary"].startswith(
        "周线多头排列"
    )
    assert brief["timeframe_thesis"]["monthly"]["summary"] is None
    assert brief["short_term_execution_panel"]["status"] == "MISSING"
    assert "MISSING" not in brief["coverage_text"]




def test_real_intraday_mtf_partial_context_fills_product_panel_without_trigger_authority():
    trend = _AssetBriefPayloadNamespace(
        current_price=10.5,
        support_levels=[10.0],
        resistance_levels=[11.0],
    )
    summary = {
        "sections": {
            "trend": "趋势：日线均线结构偏强",
            "volume_price": "量价：量能正常",
        },
        "conclusion": "保持观察。",
        "multi_timeframe_structure_context": {
            "timeframes": {
                "monthly": {"status": "MISSING", "role": "LONG_TERM_CONTEXT", "summary": None},
                "weekly": {"status": "READY", "role": "PRIMARY_TREND_CONTEXT", "summary": "周线趋势偏强"},
                "60m": {"status": "PARTIAL", "role": "OPTIONAL_BRIDGE", "summary": "均线多头；MACD偏强；分钟结构尚未准入"},
                "30m": {"status": "PARTIAL", "role": "PRIMARY_STRUCTURE", "summary": "均线压缩；RSI中性；分钟结构尚未准入"},
                "15m": {"status": "PARTIAL", "role": "TRIGGER_CONFIRMATION", "summary": "量能正常；MACD偏强；分钟结构尚未准入"},
                "5m": {"status": "PARTIAL", "role": "MICRO_TIMING", "summary": "均线交叉；RSI中性；分钟结构尚未准入"},
            }
        },
    }

    brief = _asset_brief_payload_builder(trend, summary)

    assert brief["coverage"]["60m"] == "PARTIAL_CURRENT"
    assert brief["coverage"]["30m"] == "PARTIAL_CURRENT"
    assert brief["coverage"]["15m"] == "PARTIAL_CURRENT"
    assert brief["coverage"]["5m"] == "PARTIAL_CURRENT"
    panel = brief["short_term_execution_panel"]
    assert panel["status"] == "READY"
    assert panel["state"] == "CONTEXT_ONLY_NO_TRIGGER_AUTHORITY"
    assert panel["30m"]["status"] == "PARTIAL_CURRENT"
    assert panel["15m"]["status"] == "PARTIAL_CURRENT"
    assert panel["5m"]["status"] == "PARTIAL_CURRENT"
    assert panel["independent_action_authority"] is False
    assert "30分钟" in panel["summary"]
    assert "5分钟" in panel["summary"]
def test_material_daily_ma_structure_reaches_existing_investor_brief_without_template_authority():
    trend = _AssetBriefPayloadNamespace(
        current_price=10.5,
        support_levels=[10.0],
        resistance_levels=[11.0],
    )
    ma_text = "均线束已进入压缩区间，当前混合排列，仍需量价与结构确认方向"
    summary = {
        "sections": {
            "trend": "趋势：日线均线结构偏强",
            "volume_price": "量价：量能正常",
            "price_structure": "结构：区间内运行",
            "momentum": "动量：中性",
            "cost_structure": "成本：当前仅作参考",
            "valuation": "估值：数据不足",
        },
        "conclusion": "保持观察。",
        "multi_timeframe_structure_context": {
            "timeframes": {
                "daily": {
                    "status": "READY",
                    "role": "PRIMARY_SETUP",
                    "summary": ma_text,
                    "ma_structure": {
                        "status": "READY",
                        "state": "COMPRESSED",
                        "material": True,
                        "summary": ma_text,
                        "independent_action_authority": False,
                    },
                }
            }
        },
    }

    brief = _asset_brief_payload_builder(trend, summary)

    assert ma_text in brief["timeframe_thesis"]["daily"]["summary"]
    assert ma_text in brief["fused_paragraph"]
    assert brief["timeframe_thesis"]["daily"]["summary"].count(ma_text) == 1
    assert brief["fused_paragraph"].count(ma_text) == 1
    assert "量能正常" in brief["timeframe_thesis"]["daily"]["summary"]
    assert brief["short_term_execution_panel"]["status"] == "MISSING"


def test_partial_daily_ma_cross_reaches_product_without_claiming_compression():
    trend = _AssetBriefPayloadNamespace(
        current_price=10.5,
        support_levels=[10.0],
        resistance_levels=[11.0],
    )
    ma_text = "均线发生MA5_CROSS_ABOVE_MA10，仅作趋势上下文，不单独构成方向确认"
    summary = {
        "sections": {
            "trend": "趋势：日线均线结构待确认",
            "volume_price": "量价：量能正常",
        },
        "conclusion": "保持观察。",
        "multi_timeframe_structure_context": {
            "timeframes": {
                "daily": {
                    "status": "PARTIAL",
                    "role": "PRIMARY_SETUP",
                    "summary": ma_text,
                    "ma_structure": {
                        "status": "PARTIAL",
                        "state": "UNKNOWN",
                        "material": True,
                        "summary": ma_text,
                        "readiness": {
                            "slope_cross": {"status": "READY"},
                            "compression_context": {"status": "MISSING"},
                        },
                        "independent_action_authority": False,
                    },
                }
            }
        },
    }

    brief = _asset_brief_payload_builder(trend, summary)

    assert brief["timeframe_thesis"]["daily"]["summary"].count(ma_text) == 1
    assert brief["fused_paragraph"].count(ma_text) == 1
    assert "压缩区间" not in brief["fused_paragraph"]
    assert brief["short_term_execution_panel"]["status"] == "MISSING"


def test_higher_timeframe_material_structure_event_reaches_investor_brief_consumer():
    trend = _AssetBriefPayloadNamespace(
        current_price=10.5,
        support_levels=[10.0],
        resistance_levels=[11.0],
    )
    weekly_summary = _mtf_human_summary(
        {
            "ma_alignment": "MA_FIXTURE",
            "volume_status": "VOLUME_FIXTURE",
            "macd_signal": "MACD_FIXTURE",
        },
        {"structure_event": {"state": "FAILED_UP_BREAKOUT"}},
    )
    summary = {
        "sections": {
            "trend": "趋势：日线均线结构偏强",
            "volume_price": "量价：量能正常",
        },
        "conclusion": "保持观察。",
        "multi_timeframe_structure_context": {
            "timeframes": {
                "weekly": {
                    "status": "READY",
                    "reason": "TIMEFRAME_READY",
                    "role": "PRIMARY_TREND_CONTEXT",
                    "summary": weekly_summary,
                }
            }
        },
    }

    brief = _asset_brief_payload_builder(trend, summary)
    material_text = "向上突破失败并回到结构位下方"
    assert material_text in brief["timeframe_thesis"]["weekly"]["summary"]
    assert material_text in brief["fused_paragraph"]


# Systemic admission regression: raw diagnostics may remain archived, but an
# unadmitted input may not change an admitted/rank-eligible score or public prose.
# Synthetic counterfactual; no provider, real price, portfolio or PIT claim.
def _root_admission_score_pair(strict=False, heavy_volume_down=False):
    from copy import deepcopy
    from src.stock_analyzer import TrendStatus, MACDStatus, RSIStatus

    partial = {
        "status": "PARTIAL", "reason": "RECURSIVE_METHOD_ADMISSION_INCOMPLETE",
        "volatility": {"status": "PARTIAL", "true_range_sma_20_pct": 1.5},
        "momentum": {
            "status": "PARTIAL", "roc_20_pct": 3.0,
            "macd": {"status": None, "admission": {"status": "MISSING"}},
            "rsi": {"status": None, "admission": {"status": "MISSING"}},
        },
        "confirmed_divergence": {"status": "MISSING", "state": "NONE"},
    }
    records = []
    for state in (RSIStatus.WEAK, RSIStatus.STRONG_BUY):
        trend = TrendAnalysisResult(
            code="600519", trend_status=TrendStatus.CONSOLIDATION,
            volume_status=(VolumeStatus.HEAVY_VOLUME_DOWN if heavy_volume_down else VolumeStatus.NORMAL),
            bias_ma5=2.5, macd_status=MACDStatus.BULLISH, rsi_status=state,
            current_price=100, ma5=98, ma10=98, ma20=98, ma60=98,
            ma_alignment="MA5=MA10=MA20",
        )
        trend.macd_signal = "SYNTHETIC_RAW_MACD"
        trend.rsi_signal = "SYNTHETIC_RAW_RSI_" + state.name
        StockTrendAnalyzer()._generate_signal(trend)
        kwargs = {"method_execution_receipts": {}} if strict else {}
        factor = build_stock_factor_decision_summary(
            trend, volatility_momentum_context=deepcopy(partial),
            include_canonical=True, **kwargs,
        )
        validate_canonical_factor_binding(factor)
        public = AnalysisResult(
            code="600519", name="SYNTHETIC", sentiment_score=50,
            operation_advice="watch", trend_prediction="SYNTHETIC",
            analysis_summary="", dashboard={},
        )
        apply_canonical_decision_to_result(public, factor, scope="research")
        records.append((trend, factor, public))
    return records


@pytest.mark.parametrize(
    "forgery",
    ["ranking_eligibility", "public_score", "raw_reference_audit"],
)
def test_root_score_admission_is_bound_to_existing_canonical_runtime_identity(forgery):
    """The accepted score guard must not be an unbound display-only field."""
    from copy import deepcopy

    original = _root_admission_score_pair(strict=True)[0][1]
    assert original["score_admission"]["eligible_for_ranking"] is False
    assert original["composite_score"] is None
    assert validate_canonical_factor_binding(original)["runtime_trace_hash"]
    mutated = deepcopy(original)
    if forgery == "ranking_eligibility":
        mutated["score_admission"]["eligible_for_ranking"] = True
    elif forgery == "public_score":
        mutated["composite_score"] = mutated["score_admission"]["raw_reference_score_audit"]
    else:
        mutated["score_admission"]["raw_reference_score_audit"] = 9999
    with pytest.raises(ValueError, match="stale|missing|score"):
        validate_canonical_factor_binding(mutated)


def test_root_new_score_runtime_binding_is_json_wire_stable():
    """Local JSON persistence keeps the canonical identity/qualification receipt."""
    import json

    candidate = _root_admission_score_pair(strict=True)[0][1]
    wire_copy = json.loads(json.dumps(candidate, ensure_ascii=False))
    assert validate_canonical_factor_binding(wire_copy) == candidate[
        "canonical_decision_identity"
    ]
    assert wire_copy["score_admission"]["eligible_for_ranking"] is False


def test_root_strict_missing_momentum_receipt_blocks_unqualified_legacy_score():
    """An empty strict execution set is not permission to rank by raw RSI."""
    results = []
    for trend, _, _ in _root_admission_score_pair(False):
        strict = build_stock_factor_decision_summary(
            trend, method_execution_receipts={}, include_canonical=True,
        )
        assert strict["method_execution_receipt_policy"] == "REQUIRED"
        assert strict["score_admission"]["eligible_for_ranking"] is False
        assert strict["composite_score"] is None
        assert strict["canonical_decision"]["action"] == "WAIT"
        results.append(strict["conclusion"])
        # Legitimate direct legacy mode is still explicitly distinct: no
        # strict execution identity can be synthesized for a direct caller.
        direct = build_stock_factor_decision_summary(
            trend, include_canonical=True,
        )
        assert direct["score_admission"]["status"] == "LEGACY_REFERENCE_ONLY"
    assert len(set(results)) == 1


@pytest.mark.parametrize("strict", [False, True])
def test_root_unqualified_legacy_score_not_reintroduced_by_three_report_channels(strict):
    """Full reports preserve independent material without a raw 47/52 score."""
    import re
    from src.notification import NotificationService

    service = NotificationService()
    reports = []
    for trend, factor, result in _root_admission_score_pair(strict):
        result.sentiment_score = trend.signal_score  # Existing compatibility data
        apply_canonical_decision_to_result(result, factor, scope="research")
        assert factor["score_admission"]["eligible_for_ranking"] is False
        assert factor["composite_score"] is None
        rendered = []
        for method in (
            service.generate_daily_report,
            service.generate_dashboard_report,
            service.generate_brief_report,
        ):
            text = method([result], report_date="2026-10-14")
            assert "ROC20 +3.0%" in text
            assert "1.5%" in text
            rendered.append(re.sub(r"\b\d{2}:\d{2}:\d{2}\b", "HH:MM:SS", text))
        reports.append(rendered)
    assert reports[0] == reports[1]


@pytest.mark.parametrize("strict", [False, True])
def test_root_rejected_rsi_does_not_change_rank_eligible_score(strict):
    left, right = _root_admission_score_pair(strict)
    assert left[0].signal_score != right[0].signal_score
    equal_public_score = left[1]["composite_score"] == right[1]["composite_score"]
    audit_only = all(
        factor.get("score_admission", {}).get("eligible_for_ranking") is False
        for _, factor, _ in (left, right)
    )
    assert equal_public_score or audit_only, (
        "Rejected RSI changed an unqualified score; raw audit is allowed, ranking eligibility is not."
    )


@pytest.mark.parametrize("strict", [False, True])
def test_root_rejected_rsi_does_not_change_public_conclusion(strict):
    left, right = _root_admission_score_pair(strict)
    assert left[1]["sections"]["momentum"] == right[1]["sections"]["momentum"]
    assert left[2].analysis_summary == right[2].analysis_summary, (
        "Same admitted inputs changed canonical-linked public prose through legacy score."
    )


def test_root_counterexample_remains_wait_and_not_buy():
    for strict in (False, True):
        for _, factor, public in _root_admission_score_pair(strict):
            assert factor["canonical_decision"]["action"] == "WAIT"
            assert public.action == "watch"
            assert factor["strategy_eligibility"]["state"] == "UNKNOWN"


def test_root_independent_volume_risk_still_vetoes():
    for _, factor, public in _root_admission_score_pair(heavy_volume_down=True):
        assert factor["canonical_decision"]["action"] == "PASS"
        assert factor["canonical_decision"]["hard_veto"] is True
        assert public.action == "avoid"


def test_root_unrelated_fixed_window_evidence_is_not_deleted():
    left, right = _root_admission_score_pair(strict=True)
    for _, factor, _ in (left, right):
        text = factor["sections"]["momentum"]
        assert "ROC20 +3.0%" in text
        assert "1.5%" in text
        assert "SYNTHETIC_RAW_RSI" not in text
    assert left[1]["sections"]["momentum"] == right[1]["sections"]["momentum"]
