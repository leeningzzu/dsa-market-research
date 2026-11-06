"""Exact-reference and normalized-frame tests; no live provider or renderer."""
from copy import deepcopy
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.services import evidence_traceability_registry as reg
from src.services import v2_5_evidence_coverage as v25
from src.services.factor_decision_summary import (
    _build_asset_research_brief_v1,
    canonical_factor_binding,
)
from src.services.v2_5_evidence_coverage import (
    _v25_assign,
    compile_input_coverage, compile_product_coverage,
    compile_whole_product_scope_gate, render_v2_5_dynamic_no_send,
    verify_original_mapping,
)
from tests.test_evidence_traceability_registry import (  # noqa: F401
    _closed_typed_stock_source_factor,
    _closed_typed_etf_source_factor,
    _build_sse_fixture_bundle,
    _build_sse_event_fixture_bundle,
    _sse_b1_context,
    _factor_with_source_bound_30m_candlestick,
    _p_daily_operation_admission,
    explicit_native_config,
    native_summary,
    native_summary_with_mtf,
)
from tests.test_sse_index_current_source import (  # noqa: F401
    _factor_with_admitted_index,
)

ROOT = Path(__file__).resolve().parents[1]


def input_fixture():
    frame = pd.DataFrame({"date": pd.bdate_range("2025-06-02", periods=30).date,
                          "open": 10., "high": 11., "low": 9., "close": 10.5,
                          "volume": 100000., "data_source": "unit-test-provider"})
    target = frame.date.iloc[-1].isoformat()
    meta = {"provider": "unit-test-provider", "generation": "unit-test-generation",
            "units": dict.fromkeys(("open", "high", "low", "close"), "CNY") | {"volume": "share"},
            "target_date": target, "timeframe": "1d", "completed_bar_only": True,
            "adjustment_basis": "raw", "available_at": target + "T15:01:00+08:00",
            "decision_time": target + "T19:00:00+08:00", "synthetic": True}
    return frame, meta


def test_protected_original_is_read_from_independent_git_bytes_not_regenerated():
    raw = subprocess.check_output(["git", "show", "HEAD:templates/v2_5/STOCK_DSA_V2_5_PRODUCTION_MAIL_BASELINE_R001.html"], cwd=ROOT)
    receipt = verify_original_mapping(raw)
    assert receipt["bytes"] == 125919
    assert receipt["baseline_sha256"] == "039ca6394baf9cf39494cc29f512802b114c8227f8c965723197a8df4b9de823"
    assert receipt["rendered"] is False
    assert receipt["dynamic_fidelity_proven"] is False
    for changed in (raw[:-1], raw.replace(b"\n", b"\r\n"), raw[:4096] + raw[4196:]):
        with pytest.raises(reg.TraceabilityError, match="ORIGINAL_BYTES_MISMATCH"):
            verify_original_mapping(changed)


def test_v25_fact_summary_keeps_daily_premium_and_execution_partial_boundaries():
    premium = v25._v25_data_object_fact_summary(
        "E_PREMIUM",
        {
            "trading_day": "2026-09-28",
            "daily_close_cny_per_share": 4.04,
            "official_nav_cny_per_share": 4.00,
            "premium_discount_pct": 1.0,
        },
    )
    execution = v25._v25_data_object_fact_summary(
        "P_EXEC_RISK",
        {
            "trading_day": "2026-09-28",
            "observed_components": (
                "COMPLETED_XSHG_SESSION",
                "DAILY_CLOSE_VOLUME_AMOUNT_TURNOVER",
            ),
            "unobserved_components": (
                "SUSPENSION_STATUS",
                "DAILY_PRICE_LIMIT_STATUS",
                "BID_ASK_SPREAD",
                "FEES_AND_TAXES",
            ),
        },
    )
    assert "日终折溢价" in premium and "IOPV实时同刻未绑定" in premium
    assert "完整执行风险未就绪" in execution
    assert "BID_ASK_SPREAD" in execution and "FEES_AND_TAXES" in execution




@pytest.mark.parametrize(
    ("publish_flag", "expected_flag"),
    [(True, "是"), (False, "否"), (None, "未知")],
)
def test_etf_iopv_publication_flag_never_becomes_intraday_value_or_spread(
    publish_flag, expected_flag,
):
    # SSE's 2026 external IOPV channel is independent of the PCF flag.
    # The fixed V2.5 full source remains byte-for-byte unchanged; only
    # consumer-authored, source-bound dynamic fact text may clarify semantics.
    report = v25._v25_data_object_fact_summary(
        "E_NAV_IOPV",
        {
            "fund_code": "510300",
            "trading_day": "2026-11-05",
            "nav_cny_per_share": "4.1785",
            "publish_iopv": publish_flag,
        },
    )
    assert "NAV 4.1785元/份" in report
    assert f"IOPV发布标志 {expected_flag}" in report
    assert "未取得实际IOPV数值及发布时间" in report
    assert "同刻买卖盘和价差未准入" in report
    assert "实时IOPV已取得" not in report



@pytest.mark.parametrize("current_total,latest_total,expected", [
    (1250081600, 1256197800, "最新事件股本与当期总股本不一致，事件链未完成勾稽"),
    (1256197800, 1256197800, "最近事件股本与当期总股本数量一致"),
    (1250081600, None, "最近事件股本缺失，无法勾稽"),
])
def test_current_sse_share_events_v25_fact_never_silences_parent_reconciliation(
    current_total, latest_total, expected,
):
    # Independent SSE fact projections; do not assume a 2015 event determines
    # the current issuer share count. This is a forward-only dynamic consumer.
    fact = v25._v25_data_object_fact_summary(
        "P_SHARES_EVENTS",
        {
            "stock_code": "600519",
            "capital_trade_date": "2026-11-04",
            "current_total_domestic_shares": current_total,
            "event_count": 15,
            "latest_event_date": "2015-07-20",
            "latest_event_total_shares": latest_total,
        },
    )
    assert "股本事件：600519" in fact
    assert expected in fact
    assert "事件 15条" in fact
    if latest_total != current_total:
        assert "勾稽已完成" not in fact


def test_sse_index_current_product_reaches_original_v25_without_market_overclaim():
    factor, observation, _b1 = _factor_with_admitted_index()
    factor["evidence_traceability"] = reg.build_runtime_trace(factor)
    factor["investor_brief"] = _build_asset_research_brief_v1(
        SimpleNamespace(current_price=None), factor,
    )
    binding = canonical_factor_binding(factor)
    factor["canonical_decision_identity"] = binding
    factor["investor_brief"]["canonical_binding"] = dict(binding)

    summary = v25._v25_data_object_fact_summary(
        "M_INDEX_CURRENT", observation["facts"],
    )
    assert "上证指数(000001) 3689.91点" in summary
    assert "上证50(000016) 2811.16点" in summary
    assert "科创50(000688) 1286.68点" in summary
    assert "不代表深交所/北交所、全A宽度或全市场成交额" in summary
    assert "999999.99" not in summary

    projected = render_v2_5_dynamic_no_send(
        [_v25_result(factor=factor, code="600519", name="贵州茅台")],
        report_date="2026-11-06",
    )
    receipt = projected["receipt"]
    assert receipt["results"][0]["current_product_admitted_count"] == 1
    objects = {
        row["id"]: row
        for row in receipt["whole_scope_projection"]["data_object_projections"]
    }
    assert objects["M_INDEX_CURRENT"]["state"] == "READY"
    assert objects["M_TURNOVER"]["state"] != "READY"
    assert objects["M_BASIC_BREADTH"]["state"] != "READY"
    assert summary in projected["html"]
    assert "999999.99" not in projected["html"]
    assert receipt["all_input_facts_product_ready"] is False


def test_semantic_slots_preserve_the_independent_accepted_contract():
    expected = {
        "first_screen.conclusion", "first_screen.price_or_valuation", "first_screen.higher_timeframe_thesis",
        "first_screen.short_term_conflict_or_trigger", "first_screen.supply_distribution_risk",
        "first_screen.trigger", "first_screen.invalidation", "first_screen.material_risks",
        "detail.timeframe.price_position", "detail.timeframe.trend_ma", "detail.timeframe.volume_price",
        "detail.timeframe.cost_structure", "detail.timeframe.price_structure", "detail.timeframe.momentum_divergence",
        "detail.timeframe.pattern_trigger", "detail.timeframe.support_resistance", "detail.timeframe.confirmation_invalidation",
        "asset.stock.quality_growth", "asset.stock.valuation", "asset.etf.underlying_valuation",
        "asset.etf.premium_discount", "asset.etf.liquidity_spread", "asset.etf.tracking_concentration",
        "audit.coverage", "audit.data_time_quality", "audit.historical_reference", "audit.current_probability",
    }
    document = reg.load_slot_map()
    assert {s["id"] for s in document["slots"]} == expected
    changed = deepcopy(document)
    changed["slots"][0]["paths"] = ["investor_brief.fused_paragraph"]
    with pytest.raises(reg.TraceabilityError, match="ORPHAN_PRODUCT_PATH"):
        reg.load_slot_map(changed)
    changed = deepcopy(document)
    changed["rendering_authorized"] = True
    with pytest.raises(reg.TraceabilityError, match="SLOT_AUTHORITY"):
        reg.load_slot_map(changed)
    changed = deepcopy(document)
    changed["slots"] = changed["slots"][:-1]
    with pytest.raises(reg.TraceabilityError, match="PRODUCT_SLOT_SET_MISMATCH"):
        reg.load_slot_map(changed)
    changed = deepcopy(document)
    changed["slots"] = changed["slots"][:1]
    with pytest.raises(reg.TraceabilityError, match="PRODUCT_SLOT_SET_MISMATCH"):
        reg.load_slot_map(changed)
    changed = deepcopy(document)
    changed["slots"] = list(reversed(changed["slots"]))
    with pytest.raises(reg.TraceabilityError, match="PRODUCT_SLOT_MAP_IDENTITY_MISMATCH"):
        reg.load_slot_map(changed)
    changed = deepcopy(document)
    next(
        slot for slot in changed["slots"]
        if slot["id"] == "first_screen.supply_distribution_risk"
    )["requirements"] = ["SUPPLY"]
    with pytest.raises(reg.TraceabilityError, match="PRODUCT_SLOT_REQUIREMENT_SET_MISMATCH"):
        reg.load_slot_map(changed)


def test_whole_product_scope_gate_rejects_universe_or_slot_deletion():
    factor = native_summary()
    trace = deepcopy(factor["evidence_traceability"])
    trace["complete_research_universe"]["requirements"].pop()
    with pytest.raises(reg.TraceabilityError, match="WHOLE_PRODUCT_REQUIREMENT_SET_MISMATCH"):
        compile_whole_product_scope_gate(trace)
    factor = native_summary()
    trace = deepcopy(factor["evidence_traceability"])
    trace["complete_research_universe"]["requirements"][0][
        "contract_role"
    ] = "DEFERRED"
    with pytest.raises(
        reg.TraceabilityError,
        match="WHOLE_PRODUCT_REQUIREMENT_BINDING_MISMATCH",
    ):
        compile_whole_product_scope_gate(trace)

    factor = native_summary()
    trace = deepcopy(factor["evidence_traceability"])
    trace["complete_research_universe"]["planes"].pop()
    with pytest.raises(
        reg.TraceabilityError,
        match="WHOLE_PRODUCT_PLANE_BINDING_MISMATCH",
    ):
        compile_whole_product_scope_gate(trace)

    factor = native_summary()
    trace = deepcopy(factor["evidence_traceability"])
    trace["complete_research_universe"]["valuation_route_ids"] = ("ETF",)
    with pytest.raises(reg.TraceabilityError, match="VALUATION_ROUTE_SET_MISMATCH"):
        compile_whole_product_scope_gate(trace)

    factor = native_summary()
    trace = deepcopy(factor["evidence_traceability"])
    trace["complete_research_universe"]["known_atomic_subfield_obligations"] = ()
    with pytest.raises(reg.TraceabilityError, match="ATOMIC_SUBFIELD_SET_MISMATCH"):
        compile_whole_product_scope_gate(trace)
    factor = native_summary()
    trace = deepcopy(factor["evidence_traceability"])
    trace["complete_research_universe"]["full_domain_data_gate"][
        "full_domain_product_ready"
    ] = True
    with pytest.raises(
        reg.TraceabilityError,
        match="WHOLE_PRODUCT_FULL_DOMAIN_GATE_MISMATCH",
    ):
        compile_whole_product_scope_gate(trace)

    factor = native_summary()
    slot_map = deepcopy(reg.load_slot_map())
    slot_map["slots"].pop()
    with pytest.raises(reg.TraceabilityError, match="PRODUCT_SLOT_SET_MISMATCH"):
        compile_whole_product_scope_gate(
            factor["evidence_traceability"],
            slot_map=slot_map,
        )


def test_synthetic_trial_is_never_misreported_as_real_data():
    frame, meta = input_fixture()
    receipt = compile_input_coverage(frame, metadata=meta)
    assert receipt["manifest_hash"] == reg.MANIFEST_HASH
    assert receipt["row_count"] == len(frame)
    assert receipt["synthetic"] is True
    assert all(r["state"] == "RIGHTS_OR_PIT_BLOCKED" for r in receipt["coverage"])
    assert receipt["pit_admitted"] is False
    assert receipt["product_ready"] is False
    assert receipt["training_authorized"] is False


def test_attested_source_state_is_not_rights_or_algorithm_execution_proof():
    # Tests the metadata branch only: these bytes are still synthetic test inputs.
    frame, meta = input_fixture()
    meta["synthetic"] = False
    receipt = compile_input_coverage(frame, metadata=meta)
    rows = {r["requirement_id"]: r for r in receipt["coverage"]}
    assert rows["SUPPLY"]["state"] == "BASE_BYTES_AVAILABLE"
    assert rows["SUPPLY"]["method_executed"] is False
    assert receipt["rights_state"] == "NOT_INDEPENDENTLY_VERIFIED"
    assert receipt["pit_admitted"] is False


@pytest.mark.parametrize("mutation", ["units", "source", "nan", "order", "future", "basis", "intraday_date", "completed", "boolean"])
def test_invalid_observed_inputs_are_rejected(mutation):
    frame, meta = input_fixture()
    if mutation == "units":
        meta["units"]["volume"] = "lot"
    elif mutation == "source":
        frame.loc[0, "data_source"] = "second-provider"
    elif mutation == "nan":
        frame.loc[0, "close"] = np.nan
    elif mutation == "order":
        frame = frame.iloc[::-1].copy()
    elif mutation == "future":
        meta["available_at"] = "2099-01-01T12:00:00+08:00"
    elif mutation == "basis":
        meta["adjustment_basis"] = "unspecified"
    elif mutation == "intraday_date":
        frame["date"] = pd.to_datetime(frame.date) + pd.Timedelta(hours=1)
    elif mutation == "completed":
        meta["completed_bar_only"] = False
    else:
        frame["volume"] = True
    with pytest.raises(reg.TraceabilityError):
        compile_input_coverage(frame, metadata=meta)


def test_adjusted_prices_require_factor_asof_identity():
    frame, meta = input_fixture()
    meta.update(synthetic=False, adjustment_basis="qfq")
    receipt = compile_input_coverage(frame, metadata=meta)
    assert receipt["basis_identity_declared"] is False
    assert all(r["state"] == "RIGHTS_OR_PIT_BLOCKED" for r in receipt["coverage"])


def test_daily_cmf_does_not_fill_weekly_monthly_or_intraday_slots():
    factor = native_summary()
    rows = {r["slot_id"]: r for r in compile_product_coverage(factor)["slots"]}
    states = rows["detail.timeframe.volume_price"]["timeframe_states"]
    assert states["daily"] == "EVIDENCE_AVAILABLE"
    assert all(states[tf] == "DATA_INSUFFICIENT" for tf in ("weekly", "monthly", "60m", "30m", "15m", "5m"))
    assert all(row["projection_state"] == "NOT_RENDERED" for row in rows.values())


def test_product_coverage_consumes_the_trace_matrix_for_actual_weekly_structure_and_momentum():
    factor = native_summary_with_mtf()
    coverage = compile_product_coverage(factor)
    rows = {row["slot_id"]: row for row in coverage["slots"]}
    trace_matrix = factor["evidence_traceability"]["timeframe_family_matrix"]

    assert coverage["timeframe_family_matrix_hash"] == trace_matrix["matrix_hash"]
    assert coverage["method_window_policy_hash"] == factor["evidence_traceability"]["method_window_policy_hash"]
    assert len(trace_matrix["cells"]) == 56
    trace_universe = factor["evidence_traceability"]["complete_research_universe"]
    assert coverage["complete_research_universe_hash"] == trace_universe["universe_hash"]
    assert coverage["complete_research_universe_schema_version"] == "complete-research-universe-view-v1"
    assert coverage["complete_research_universe_counts"] == {
        "requirements": 26,
        "methods": 43,
        "strategy_bindings": 13,
        "technical_matrix_cells": 56,
        "atomic_subfields": 7,
        "atomic_subfields_product_admitted": 0,
    }
    scope_gate = coverage["whole_product_scope_gate"]
    assert scope_gate["full_scope_accounted_pass"] is True
    assert scope_gate["required_requirement_count"] == 26
    assert scope_gate["required_slot_count"] == 27
    assert scope_gate["required_data_domain_counts"] == {
        "GLOBAL": 18, "CN_MARKET": 18, "SECTOR": 7,
        "ETF": 12, "STOCK": 17, "RECORD": 4,
    }
    assert scope_gate["dynamic_product_admitted"] is False
    assert set(scope_gate["required_valuation_route_ids"]) == {
        "MATURE_INDUSTRIAL_CONSUMER", "HIGH_GROWTH_TECH", "BANK", "INSURANCE",
        "CYCLICAL_RESOURCE", "UTILITY_INFRASTRUCTURE", "REIT",
        "LOSS_MAKING_EARLY_GROWTH", "ETF",
    }
    assert set(scope_gate["unresolved_atomic_subfields"]) == {
        "GLOBAL_US_ACTUAL_INFLATION", "GLOBAL_US_CORE_INFLATION",
        "GLOBAL_US_SERVICE_INFLATION", "GLOBAL_US_5Y_INFLATION_EXPECTATION",
        "GLOBAL_SOX_INDEX", "GLOBAL_TECH_ETF_FLOW",
        "GLOBAL_SEMICONDUCTOR_SOFTWARE_RELATIVE",
    }
    assert scope_gate["atomic_subfield_binding_complete"] is False
    data_gate = coverage["full_domain_data_gate"]
    assert data_gate["full_domain_accounted_pass"] is True
    assert data_gate["full_domain_product_ready"] is False
    assert data_gate["accounted_object_count"] == 76
    assert rows["detail.timeframe.price_structure"]["timeframe_states"]["weekly"] == "EVIDENCE_AVAILABLE"
    # Complete weekly structures and volume remain material; unverified
    # recursive oscillator output must not create weekly MOMENTUM coverage.
    weekly = factor["multi_timeframe_structure_context"]["timeframes"]["weekly"]
    assert weekly["macd_startup_stability"]["status"] == "MISSING"
    assert weekly["rsi_startup_stability"]["status"] == "MISSING"
    assert rows["detail.timeframe.momentum_divergence"]["timeframe_states"]["weekly"] == "DATA_INSUFFICIENT"
    assert rows["detail.timeframe.momentum_divergence"]["timeframe_canonical_paths"]["weekly"] == ()
    assert rows["detail.timeframe.trend_ma"]["timeframe_states"]["weekly"] == "EVIDENCE_AVAILABLE"
    assert rows["detail.timeframe.volume_price"]["timeframe_states"]["weekly"] == "EVIDENCE_AVAILABLE"
    assert rows["detail.timeframe.cost_structure"]["timeframe_states"]["weekly"] == "DATA_INSUFFICIENT"
    assert all(row["projection_state"] == "NOT_RENDERED" for row in rows.values())


def test_product_coverage_exposes_only_current_intraday_context_families():
    # The existing EVIDENCE_AVAILABLE assertions require a lawful synthetic share unit.
    factor = native_summary_with_mtf(intraday=True, volume_unit="share")
    coverage = compile_product_coverage(factor)
    rows = {row["slot_id"]: row for row in coverage["slots"]}

    for timeframe in ("60m", "30m", "15m", "5m"):
        assert rows["detail.timeframe.trend_ma"]["timeframe_states"][timeframe] == "EVIDENCE_AVAILABLE"
        assert rows["detail.timeframe.volume_price"]["timeframe_states"][timeframe] == "EVIDENCE_AVAILABLE"
        assert rows["detail.timeframe.momentum_divergence"]["timeframe_states"][timeframe] == "EVIDENCE_AVAILABLE"
        assert rows["detail.timeframe.cost_structure"]["timeframe_states"][timeframe] == "EVIDENCE_AVAILABLE"
        cost = factor["multi_timeframe_structure_context"]["timeframes"][timeframe]["cost_bar_reference"]
        assert cost["status"] == "PARTIAL"
        assert cost["data_rights_admitted"] is False
        assert cost["independent_action_authority"] is False
        assert rows["detail.timeframe.price_structure"]["timeframe_states"][timeframe] == "DATA_INSUFFICIENT"
        # Bollinger is source-bound descriptive envelope evidence, not a
        # candlestick/structure trigger. The family is visible but remains
        # nonrendered and has no independent action authority.
        assert rows["detail.timeframe.pattern_trigger"]["timeframe_states"][timeframe] == "EVIDENCE_AVAILABLE"
        methods = {
            row["method_id"]: row
            for row in next(
                cell for cell in factor["evidence_traceability"]["timeframe_family_matrix"]["cells"]
                if (cell["timeframe"], cell["family"]) == (timeframe, "PATTERN")
            )["method_coverage"]
        }
        assert methods["BOLLINGER"]["state"] == "READY"
        assert methods["CANDLESTICK"]["state"] == "MISSING"
        assert methods["BOLLINGER"]["independent_action_authority"] is False
    assert coverage["foundation_evidence_receipt"]["policy"]["independent_action_authority"] is False
    assert all(row["projection_state"] == "NOT_RENDERED" for row in rows.values())


def test_product_coverage_exposes_source_bound_30m_candlestick_as_nonrendered_nontrigger():
    factor = _factor_with_source_bound_30m_candlestick()
    coverage = compile_product_coverage(factor)
    rows = {row["slot_id"]: row for row in coverage["slots"]}
    pattern = rows["detail.timeframe.pattern_trigger"]
    assert pattern["timeframe_states"]["30m"] == "EVIDENCE_AVAILABLE"
    assert any(
        path.endswith("30m.pattern_trigger.candlestick")
        for path in pattern["timeframe_canonical_paths"]["30m"]
    )
    assert pattern["projection_state"] == "NOT_RENDERED"
    frame = factor["multi_timeframe_structure_context"]["timeframes"]["30m"]
    assert frame["pattern_trigger"]["candlestick"]["status"] == "READY"
    assert frame["pattern_trigger"]["broad_patterns"]["status"] == "NOT_ADMITTED"
    assert frame["pattern_trigger"]["independent_action_authority"] is False
    assert frame["strategy_admitted"] is False
    assert coverage["rendered"] is False


def test_product_coverage_rejects_unknown_intraday_volume_without_erasing_price_momentum():
    # Same deterministic MTF producer, but without an admitted source volume unit.
    factor = native_summary_with_mtf(intraday=True, volume_unit="UNKNOWN")
    coverage = compile_product_coverage(factor)
    rows = {row["slot_id"]: row for row in coverage["slots"]}
    matrix = factor["evidence_traceability"]["timeframe_family_matrix"]
    assert len(matrix["cells"]) == 56
    assert coverage["timeframe_family_matrix_hash"] == matrix["matrix_hash"]

    for timeframe in ("60m", "30m", "15m", "5m"):
        produced = factor["multi_timeframe_structure_context"]["timeframes"][timeframe]
        assert produced["status"] == "PARTIAL"
        assert "VOLUME" not in produced["admitted_methods"]
        assert produced["trend"]["volume_status"] is None
        assert produced["strategy_admitted"] is False
        assert produced["learning_admitted"] is False

        assert rows["detail.timeframe.volume_price"]["timeframe_states"][timeframe] == "DATA_INSUFFICIENT"
        assert rows["detail.timeframe.trend_ma"]["timeframe_states"][timeframe] == "EVIDENCE_AVAILABLE"
        assert rows["detail.timeframe.momentum_divergence"]["timeframe_states"][timeframe] == "EVIDENCE_AVAILABLE"
        assert rows["detail.timeframe.cost_structure"]["timeframe_states"][timeframe] == "DATA_INSUFFICIENT"
        assert rows["detail.timeframe.price_structure"]["timeframe_states"][timeframe] == "DATA_INSUFFICIENT"
        # Unknown volume blocks OBV/ADL/MFI and cost/supply, but does not erase
        # the price-only Bollinger envelope.
        assert rows["detail.timeframe.pattern_trigger"]["timeframe_states"][timeframe] == "EVIDENCE_AVAILABLE"

    assert all(row["projection_state"] == "NOT_RENDERED" for row in rows.values())


def _v25_result(*, factor=None, code="600519", name="测试股票", delivery=None):
    delivery = dict(delivery or {})
    delivery.setdefault("selection_source", "SPECIFIED_CODES")
    delivery.setdefault("delivery_envelope", "ASSET_RESEARCH_BRIEF_WATCHLIST")
    delivery.setdefault("asset_type", "stock")
    delivery.setdefault("asset_identity_text", "A股｜主板")
    delivery.setdefault("product_group", "WATCHLIST_STOCK")
    return SimpleNamespace(
        code=code,
        name=name,
        dashboard={
            "factor_decision": factor or native_summary(),
            "research_delivery": delivery,
        },
    )


def test_product_coverage_binds_effective_7tf_eight_family_foundation_receipt():
    factor = native_summary()
    coverage = compile_product_coverage(factor)
    foundation = coverage["foundation_evidence_receipt"]

    assert foundation["target_method_timeframe_pairs"] == 207
    assert foundation["declared_execution_pairs"] == 69
    assert foundation["effective_execution_pairs"] == 189
    assert foundation["all_56_cells_executable"] is True
    assert foundation["regime_shared_projection_count"] == 7
    assert foundation["matrix_cell_count"] == 56
    assert foundation["product_coverage_binding"]["slot_count"] == 27
    assert sum(
        foundation["product_coverage_binding"]["slot_state_counts"].values()
    ) == 27
    assert foundation["proof_scope"] == (
        "CURRENT_FACTOR_RUNTIME_NOT_HISTORICAL_RECEIPT_REUSE"
    )
    assert foundation["policy"]["seven_by_eight_is_coverage_not_votes"] is True
    assert len(foundation["receipt_hash"]) == 64


def test_partial_current_product_preserves_only_admitted_valuation_layers():
    ready = {
        "asset_type": "etf",
        "fact_mode": "PARTIALLY_ADMITTED",
        "factor": {
            "business_valuation": {
                "status": "READY",
                "product_admitted": True,
            },
            "index_sector_valuations": [{
                "status": "READY",
                "product_admitted": True,
                "scope": "etf_underlying",
            }],
        },
        "brief": {
            "valuation": {
                "status": "READY",
                "summary": "ETF交易价/NAV与底层沪深300估值分别已准入。",
            },
        },
    }
    assert v25._v25_valuation_line(ready, "watch-etf-z", 0) == (
        "ETF交易价/NAV与底层沪深300估值分别已准入。"
    )

    hidden = deepcopy(ready)
    hidden["factor"]["business_valuation"]["product_admitted"] = False
    hidden["factor"]["index_sector_valuations"][0]["product_admitted"] = False
    assert "未绑定" in v25._v25_valuation_line(hidden, "watch-etf-z", 0)
    assert "分别已准入" not in v25._v25_valuation_line(hidden, "watch-etf-z", 0)


def test_partial_current_product_requires_method_execution_before_showing_leaf():
    material = {
        "asset_type": "stock",
        "fact_mode": "PARTIALLY_ADMITTED",
        "gate": {
            "current_product_admitted_dataset_ids": ("P_DAILY",),
            "unresolved_product_dataset_ids": ("G_VIX",),
        },
        "mtf": {
            "timeframes": {
                "daily": {
                    "status": "READY",
                    "role": "PRIMARY_SETUP",
                    "summary": "LEGAL_DAILY_MA_VISIBLE",
                    "source_alignment": {"status": "SINGLE_SOURCE"},
                    "completed_through": "2026-10-23",
                    "reason": "READY",
                }
            }
        },
        "brief": {},
    }

    not_executed = v25._v25_timeframe(material, "daily", 0)
    assert "LEGAL_DAILY_MA_VISIBLE" not in not_executed
    assert "当前Product数值未准入" not in not_executed
    assert "方法执行 METHOD_NOT_EXECUTED" in not_executed

    unrelated_only = deepcopy(material)
    unrelated_only["gate"]["current_product_admitted_dataset_ids"] = ("G_VIX",)
    hidden = v25._v25_timeframe(unrelated_only, "daily", 0)
    assert "LEGAL_DAILY_MA_VISIBLE" not in hidden
    assert "当前Product数值未准入" in hidden

    intraday = deepcopy(material)
    intraday["mtf"]["timeframes"] = {
        "5m": {
            "status": "READY",
            "role": "MICRO_TIMING",
            "summary": "LEGAL_5M_VISIBLE",
        }
    }
    hidden_5m = v25._v25_timeframe(intraday, "5m", 0)
    assert "LEGAL_5M_VISIBLE" not in hidden_5m
    intraday["gate"]["current_product_admitted_dataset_ids"] = ("P_5M",)
    not_executed_5m = v25._v25_timeframe(intraday, "5m", 0)
    assert "LEGAL_5M_VISIBLE" not in not_executed_5m
    assert "方法执行 METHOD_NOT_EXECUTED" in not_executed_5m


def test_v25_all_seven_timeframes_consume_their_own_verified_numeric_family_leaves():
    """Synthetic consumer oracle: different period values, not copied daily facts."""
    timeframes = ("monthly", "weekly", "daily", "60m", "30m", "15m", "5m")
    families = ("SUPPLY", "COST", "MOMENTUM", "PATTERN")
    frames, cells = {}, []
    for i, timeframe in enumerate(timeframes, 1):
        frames[timeframe] = {
            "status": "READY", "role": "SYNTHETIC_TIMEFRAME_" + timeframe,
            "completed_through": "2026-10-30",
            "supply_demand": {
                "status": "READY",
                "relative_volume": {"volume_ratio_20d": i + 0.125},
                "directional_volume": {"signed_volume_balance": i + 0.25},
                "close_location_flow": {"cmf_20": i / 100},
            },
            "cost_bar_reference": {
                "status": "READY",
                "window_20": {"rolling_reference_price": 120 + i},
                "window_60": {"rolling_reference_price": 100 + i},
            },
            "volatility_momentum": {
                "status": "READY",
                "momentum": {
                    "macd": {"dif": i / 10, "dea": i / 20, "bar": i / 30},
                    "rsi": {"rsi_6": 50 + i, "rsi_12": 45 + i, "rsi_24": 40 + i},
                    "roc_20_completed_bars_pct": i + 3.125,
                },
                "volatility": {"atr_wilder_14": i + 0.75},
                "extended_indicators": {"values": {
                    "adx": 20 + i, "plus_di": 30 + i, "minus_di": 10 + i,
                    "bollinger_upper": 140 + i, "bollinger_lower": 80 + i,
                    "kdj_k": 60 + i, "kdj_d": 50 + i, "kdj_j": 80 + i,
                    "obv": 100000 + i, "adl": 50000 + i, "mfi": 55 + i,
                }},
            },
            "pattern_trigger": {
                "status": "READY", "primary_pattern": {
                    "pattern_type": "SYNTHETIC_" + timeframe,
                    "lifecycle": "OBSERVATION",
                }, "candlestick": {"status": "READY"},
            },
        }
        for family in families:
            method = "SYNTHETIC_" + family + "_" + timeframe
            cells.append({
                "family": family, "timeframe": timeframe, "state": "READY",
                "runtime_binding": {"runtime_source_bound": True},
                "current_method_ids": (method,),
                "method_coverage": ({
                    "method_id": method, "state": "READY",
                    "method_receipt_hash": "a" * 64,
                    "runtime_source_binding": {"runtime_source_bound": True},
                },),
            })
    material = {
        "code": "TEST", "asset_type": "stock", "fact_mode": "SYNTHETIC_TEST",
        "factor": {"method_execution_receipt_policy": "REQUIRED"},
        "mtf": {"timeframes": frames},
        "trace": {"timeframe_family_matrix": {"cells": cells}},
        "gate": {"current_product_admitted_dataset_ids": ("P_DAILY", "P_5M")},
    }
    for i, timeframe in enumerate(timeframes, 1):
        leaf = v25._v25_timeframe_leaf_payload(material, timeframe)
        reader = v25._v25_timeframe_leaf_text(leaf)
        assert all(leaf["family_execution_verified"][f] for f in families)
        volume_label = "20日量比" if timeframe == "daily" else "20根量比"
        assert f"{volume_label}={i + 0.125}" in reader
        assert f"成本20={120 + i}" in reader
        assert f"ROC20%={i + 3.125}" in reader
        assert f"形态=SYNTHETIC_{timeframe}" in reader
        assert "DIF=" in reader and "RSI6=" in reader and "ATR14=" in reader
        assert f"ADX14={20 + i}" in reader
        assert f"+DI14={30 + i}" in reader and f"-DI14={10 + i}" in reader
        assert f"KDJ-K={60 + i}" in reader and f"KDJ-D={50 + i}" in reader
        assert f"KDJ-J={80 + i}" in reader
        assert f"OBV={100000 + i}" in reader and f"ADL={50000 + i}" in reader
        assert f"MFI14={55 + i}" in reader
        assert f"BOLL上轨={140 + i}" in reader and f"BOLL下轨={80 + i}" in reader
    # A forged/missing method receipt hides the numerical family, even if the
    # source object and product flag still claim READY.
    damaged = deepcopy(material)
    cell = next(c for c in damaged["trace"]["timeframe_family_matrix"]["cells"]
                if c["family"] == "COST" and c["timeframe"] == "30m")
    cell["method_coverage"] = ({**cell["method_coverage"][0],
                                "method_receipt_hash": None},)
    reader = v25._v25_timeframe_leaf_text(
        v25._v25_timeframe_leaf_payload(damaged, "30m")
    )
    assert "成本20=" not in reader and "成本60=" not in reader


def test_v25_each_of_seven_periods_exposes_all_eight_family_execution_states():
    timeframes = ("monthly", "weekly", "daily", "60m", "30m", "15m", "5m")
    cells = []
    frames = {}
    for timeframe in timeframes:
        frames[timeframe] = {
            "status": "PARTIAL",
            "role": "ROLE_" + timeframe,
            "completed_through": "2026-10-30",
            "relative_strength": {
                "status": "MISSING",
                "reason": "TIMEFRAME_RELATIVE_STRENGTH_CONTEXT_MISSING",
                "timeframe": timeframe,
            },
        }
        for family in reg.TECHNICAL_EVIDENCE_FAMILIES:
            method = "METHOD_" + family + "_" + timeframe
            cells.append({
                "cell_id": timeframe + ":" + family,
                "family": family,
                "timeframe": timeframe,
                "state": "MISSING",
                "reason": "EXPLICIT_INPUT_MISSING",
                "projection_scope": (
                    "SHARED_ASSET_CONTEXT" if family == "REGIME" else "TIMEFRAME_LOCAL"
                ),
                "shared_parent_context": family == "REGIME",
                "runtime_binding": {"runtime_source_bound": False},
                "current_method_ids": (),
                "method_coverage": ({
                    "method_id": method,
                    "state": "MISSING",
                    "reason": "EXPLICIT_INPUT_MISSING",
                    "method_receipt_hash": "a" * 64,
                    "runtime_source_binding": {"runtime_source_bound": False},
                },),
            })
    material = {
        "asset_type": "stock",
        "fact_mode": "PARTIALLY_ADMITTED",
        "factor": {"method_execution_receipt_policy": "REQUIRED"},
        "mtf": {"timeframes": frames},
        "trace": {"timeframe_family_matrix": {"cells": cells}},
        "gate": {"current_product_admitted_dataset_ids": ("P_DAILY", "P_5M")},
    }
    for timeframe in timeframes:
        payload = v25._v25_timeframe_leaf_payload(material, timeframe)
        reader = v25._v25_timeframe_leaf_text(payload)
        assert set(payload["family_states"]) == set(reg.TECHNICAL_EVIDENCE_FAMILIES)
        assert "八族执行" in reader
        for family in reg.TECHNICAL_EVIDENCE_FAMILIES:
            assert family + "=MISSING" in reader
        assert "环境/行业=共享父上下文，不跨周期重复计票" in reader
        assert "相对强弱=MISSING(TIMEFRAME_RELATIVE_STRENGTH_CONTEXT_MISSING)" in reader


def test_v25_timeframe_etf_peer_rs_metrics_require_exact_ready_method_receipt():
    method = {
        "method_id": "RELATIVE_STRENGTH",
        "state": "READY",
        "reason": "TIMEFRAME_ETF_PEER_UNIVERSE_READY",
        "method_receipt_hash": "a" * 64,
        "runtime_source_binding": {"runtime_source_bound": True},
    }
    material = {
        "asset_type": "etf",
        "fact_mode": "PARTIALLY_ADMITTED",
        "factor": {"method_execution_receipt_policy": "REQUIRED"},
        "gate": {"current_product_admitted_dataset_ids": ("E_PRICE_5M",)},
        "trace": {"timeframe_family_matrix": {"cells": [{
            "family": "TREND_RS",
            "timeframe": "30m",
            "state": "READY",
            "reason": "ALL_CURRENT_METHODS_READY",
            "runtime_binding": {"runtime_source_bound": True},
            "current_method_ids": ("RELATIVE_STRENGTH",),
            "method_coverage": (method,),
        }]}},
        "mtf": {"timeframes": {"30m": {
            "status": "PARTIAL",
            "role": "MAIN_STRUCTURE",
            "relative_strength": {
                "status": "READY",
                "reason": "TIMEFRAME_ETF_PEER_UNIVERSE_READY",
                "relative": {
                    "state": "OUTPERFORMING",
                    "rank": 1,
                    "rank_denominator": 4,
                    "percentile": 100.0,
                    "excess_vs_peer_median_pct_points": 2.75,
                },
            },
        }}},
    }
    leaf = v25._v25_timeframe_leaf_payload(material, "30m")
    reader = v25._v25_timeframe_leaf_text(leaf)
    assert "Peer排名=1" in reader
    assert "Peer样本=4" in reader
    assert "Peer百分位=100" in reader
    assert "超Peer中位收益百分点=2.75" in reader
    audit = v25._v25_reader_numeric_leaf_audit(leaf, reader)
    assert audit["missing_numeric_labels"] == ()

    forged = deepcopy(material)
    forged["trace"]["timeframe_family_matrix"]["cells"][0][
        "method_coverage"
    ][0]["method_receipt_hash"] = None
    hidden = v25._v25_timeframe_leaf_text(
        v25._v25_timeframe_leaf_payload(forged, "30m")
    )
    assert "Peer排名=" not in hidden
    assert "超Peer中位收益百分点=" not in hidden


def test_partial_family_cannot_leak_unready_rsi_or_cmf_while_preserving_ready_macd_ma_volume():
    """Independent method receipts, not a PARTIAL family flag, authorize each metric."""
    def method(name, state):
        return {
            "method_id": name, "state": state,
            "method_receipt_hash": "a" * 64,
            "runtime_source_binding": {"runtime_source_bound": True},
        }

    def cell(family, methods):
        return {
            "family": family, "timeframe": "30m", "state": "PARTIAL",
            "runtime_binding": {"runtime_source_bound": True},
            "current_method_ids": tuple(row["method_id"] for row in methods),
            "method_coverage": tuple(methods),
        }

    material = {
        "asset_type": "stock", "fact_mode": "PARTIALLY_ADMITTED",
        "factor": {"method_execution_receipt_policy": "REQUIRED"},
        "gate": {"current_product_admitted_dataset_ids": ("P_5M",)},
        "trace": {"timeframe_family_matrix": {"cells": [
            cell("TREND_RS", (method("MTF_MA_LEVEL_ALIGNMENT", "READY"),
                              method("RELATIVE_STRENGTH", "PARTIAL"))),
            cell("SUPPLY", (method("SUPPLY_RELATIVE_VOLUME", "READY"),
                            method("SUPPLY_CMF", "PARTIAL"))),
            cell("MOMENTUM", (method("MACD", "READY"),
                              method("RSI", "PARTIAL"))),
        ]}},
        "mtf": {"timeframes": {"30m": {
            "status": "PARTIAL", "role": "MAIN_STRUCTURE",
            "ma_structure": {"ma_values": {"ma5": 110.25}},
            "supply_demand": {
                "relative_volume": {"volume_ratio_20d": 1.25},
                "close_location_flow": {"cmf_20": 0.22},
            },
            "volatility_momentum": {"momentum": {
                "macd": {"dif": 0.35},
                "rsi": {"rsi_6": 66.5},
            }},
        }}},
    }
    leaf = v25._v25_timeframe_leaf_payload(material, "30m")
    reader = v25._v25_timeframe_leaf_text(leaf)
    assert "SMA " in reader and "MA5=110.25" in reader
    assert "20根量比=1.25" in reader and "DIF=0.35" in reader
    assert "CMF20=" not in reader
    assert "RSI6=" not in reader
    assert leaf["family_execution_verified"]["MOMENTUM"] is True

    forged = deepcopy(material)
    methods = list(forged["trace"]["timeframe_family_matrix"]["cells"][2]["method_coverage"])
    methods[0] = {**methods[0], "method_receipt_hash": None}
    forged["trace"]["timeframe_family_matrix"]["cells"][2]["method_coverage"] = tuple(methods)
    forged_reader = v25._v25_timeframe_leaf_text(
        v25._v25_timeframe_leaf_payload(forged, "30m")
    )
    assert "DIF=0.35" not in forged_reader and "RSI6=" not in forged_reader


def test_v25_status_only_diagnostic_cannot_claim_full_research_or_executed_auto():
    """Original V2.5 geometry is no substitute for readable authorized facts."""
    result = render_v2_5_dynamic_no_send(
        [_v25_result()], report_date="2026-10-23",
    )
    receipt, html = result["receipt"], result["html"]
    assert receipt["all_accepted_slot_roles_projected"] is True
    assert receipt["reader_value_audit"]["actual_rendered_numeric_leaf_count"] == 0
    assert receipt["reader_value_audit"]["full_readable_research_ready"] is False
    assert receipt["reader_value_audit"]["mode"] == "STATUS_ONLY_DIAGNOSTIC"
    assert receipt["reader_value_audit"]["auto_execution_state"] == "UNVERIFIED_NOT_RUN"
    assert "未执行AUTO_SCREEN" in html
    assert "非完整事实研究" in html
    assert "Product未准入｜技术载体=" in html

    assert receipt["no_send"] is True and receipt["delivery_authority"] is False


def test_v25_unadmitted_card_roles_are_distinct_not_repeated_policy_filler():
    material = v25._v25_material(_v25_result())
    lines = tuple(
        v25._v25_card_missing_line(material, "watch-x", ordinal)
        for ordinal in range(3)
    )
    assert len(set(lines)) == 3
    assert "全域Product尚未准入" in lines[0]
    assert "7周期" in lines[1]
    assert "veto" in lines[2].lower() or "否决" in lines[2]
    assert all("价格=" not in line and "MACD=" not in line for line in lines)


def test_v25_readability_independently_rejects_trimmed_admitted_numeric_leaf():
    """Input receipt hashes and slot counts cannot stand in for visible values."""
    leaf = {
        "timeframe": "30m", "product_admitted": True,
        "family_execution_verified": {
            "TREND_RS": False, "SUPPLY": True, "COST": True,
            "STRUCTURE": False, "MOMENTUM": True, "PATTERN": False, "MTF": True,
        },
        "relative_volume_20d": 2.75,
        "cmf_20": 0.25,
        "cost_reference_20": 123.45,
        "macd_dif": 0.31,
        "rsi_6": 51.2,
        "roc_20_pct": 1.75,
        "atr_wilder_14": 2.1,
    }
    readable = v25._v25_timeframe_leaf_text(leaf)
    exact = v25._v25_reader_numeric_leaf_audit(leaf, readable)
    assert exact["input_numeric_count"] >= 6
    assert exact["visible_numeric_count"] == exact["input_numeric_count"]
    assert exact["missing_numeric_labels"] == ()
    with pytest.raises(reg.TraceabilityError, match="V25_READER_NUMERIC_LEAF_NOT_VISIBLE"):
        v25._v25_reader_numeric_leaf_audit(
            leaf, readable.replace("DIF=0.31", "DIF=0.30"),
        )
    # Missing price Product rights must never reveal the same source numbers.
    blocked = dict(leaf, product_admitted=False)
    with pytest.raises(reg.TraceabilityError, match="V25_READER_UNADMITTED_LEAF_VISIBLE"):
        v25._v25_reader_numeric_leaf_audit(blocked, readable)


def test_actual_material_path_preserves_admitted_daily_leaf_before_recording():
    factor, _minute = _closed_typed_stock_source_factor(source_version_known=True)
    _source_row, _receipt, admitted = _p_daily_operation_admission(factor)
    template = native_summary()
    admitted["strategy_id"] = template["strategy_id"]
    admitted["canonical_decision"] = deepcopy(template["canonical_decision"])
    admitted["strategy_eligibility"] = deepcopy(template["strategy_eligibility"])
    admitted["evidence_traceability"] = reg.build_runtime_trace(admitted)
    admitted["investor_brief"] = _build_asset_research_brief_v1(
        SimpleNamespace(current_price=None), admitted,
    )
    binding = canonical_factor_binding(admitted)
    admitted["canonical_decision_identity"] = binding
    admitted["investor_brief"]["canonical_binding"] = dict(binding)
    material = v25._v25_material(_v25_result(factor=admitted))

    assert material["fact_mode"] == "PARTIALLY_ADMITTED"
    assert material["gate"]["current_product_admitted_dataset_ids"] == ("P_DAILY",)
    assert set(material["gate"]["unresolved_record_dataset_ids"]) == {
        "L_LEDGER", "L_OUTCOME", "L_PIT_MANIFEST", "L_IMMUTABLE_BYTES",
    }
    daily = v25._v25_timeframe(material, "daily", 0)
    assert "当前Product数值未准入" not in daily
    assert "方法执行 TREND_RS/MTF" in daily
    assert "SMA " in daily
    assert "MA斜率% " in daily
    assert "source=SINGLE_SOURCE" in daily
    minute = v25._v25_timeframe(material, "5m", 0)
    assert "当前Product数值未准入" in minute

    projected = render_v2_5_dynamic_no_send(
        [_v25_result(factor=admitted)], report_date="2026-10-23",
    )
    receipt = projected["receipt"]
    assert receipt["results"][0]["fact_mode"] == "PARTIALLY_ADMITTED"
    assert receipt["results"][0]["current_product_admitted_count"] == 1
    projected_objects = {
        row["id"]: row
        for row in receipt["whole_scope_projection"]["data_object_projections"]
    }
    assert projected_objects["P_DAILY"]["state"] == "READY"
    assert projected_objects["P_5M"]["state"] != "READY"
    assert projected_objects["L_OUTCOME"]["state"] != "READY"
    assert receipt["all_input_facts_product_ready"] is False
    # One genuinely admitted synthetic P_DAILY must reach visible reader text.
    # Other periods stay denied; a rendered card count does not prove this.
    audit = receipt["reader_value_audit"]
    assert audit["actual_rendered_numeric_leaf_count"] > 0
    assert audit["full_readable_research_ready"] is False
    assert "DSA部分事实研究" in projected["html"]

    assert "SMA " in projected["html"]
    assert "MA斜率%" in projected["html"]



def _sse_admitted_v25_factor(asset_type):
    from src.services.data_object_admission import (
        compile_b1_runtime_admission_receipts,
    )

    bundle = _build_sse_fixture_bundle()
    if asset_type == "stock":
        event_bundle = _build_sse_event_fixture_bundle(reconciled=False)
        factor = deepcopy(native_summary())
        observations = {
            **bundle["stock_observations"],
            **event_bundle["stock_observations"],
        }
    else:
        factor, _minute = _closed_typed_etf_source_factor()
        template = native_summary()
        factor["strategy_id"] = template["strategy_id"]
        factor["canonical_decision"] = deepcopy(template["canonical_decision"])
        factor["strategy_eligibility"] = deepcopy(template["strategy_eligibility"])
        observations = bundle["etf_observations"]
    factor["data_object_source_observations"] = deepcopy(observations)
    b1 = _sse_b1_context()
    objects = reg._compile_accepted_data_object_scope(factor)
    receipts = compile_b1_runtime_admission_receipts(
        objects, b1_context=b1,
    )
    factor.update({
        "research_as_of": b1["research_as_of"],
        "research_operation_id": b1["operation_id"],
        "research_intended_use": b1["intended_use"],
        "data_object_admission_receipts": receipts,
    })
    factor["evidence_traceability"] = reg.build_runtime_trace(factor)
    factor["investor_brief"] = _build_asset_research_brief_v1(
        SimpleNamespace(current_price=None), factor,
    )
    binding = canonical_factor_binding(factor)
    factor["canonical_decision_identity"] = binding
    factor["investor_brief"]["canonical_binding"] = dict(binding)
    return factor


def test_v25_never_renders_sse_self_observed_vintage_as_admitted_product():
    stock = _sse_admitted_v25_factor("stock")
    etf = _sse_admitted_v25_factor("etf")
    projected = render_v2_5_dynamic_no_send(
        [
            _v25_result(factor=stock, code="600519", name="贵州茅台"),
            _v25_result(
                factor=etf,
                code="510300",
                name="沪深300ETF",
                delivery={
                    "asset_type": "etf",
                    "asset_identity_text": "A股ETF｜宽基",
                    "product_group": "WATCHLIST_ETF",
                },
            ),
        ],
        report_date="2026-10-31",
    )
    html = projected["html"]
    # Source-observed values cannot silently become readable Product facts.
    # Response SHA, local observed_at and ex-dividend dates are not the
    # publisher's revision/release vintage. Keep the real values in the
    # source manifest for later authorized reentry, never in a false mail.
    assert "证券身份：600519 贵州茅台" not in html
    assert "全称 贵州茅台酒股份有限公司" not in html
    assert "现金分红：600519" not in html
    assert "每股含税红利 28.02423元" not in html
    assert "ETF申赎篮子：510300" not in html
    assert "成分 2只" not in html
    assert "PCF文件 1508913" not in html
    # Other source observations without real Product are also withheld.
    assert "行业归属：" not in html
    assert "ETF净值：510300" not in html
    assert "ETF申赎份额：510300" not in html
    assert "NAV 4.1176" not in html
    assert "股本事件：600519" not in html

    stage = projected["receipt"]["stage1_vertical_product_receipt"]
    by_code = {row["code"]: row for row in stage["materials"]}
    stock_rows = {
        row["dataset_id"]: row
        for row in by_code["600519"]["data_object_rows"]
    }
    etf_rows = {
        row["dataset_id"]: row
        for row in by_code["510300"]["data_object_rows"]
    }
    assert stock_rows["P_CODE_IDENTITY"]["product_admitted"] is False
    assert stock_rows["P_CODE_IDENTITY"]["v25_data_object_fact_consumed"] is False
    assert stock_rows["P_DIVIDEND"]["product_admitted"] is False
    assert stock_rows["P_DIVIDEND"]["v25_data_object_fact_consumed"] is False
    assert stock_rows["P_SHARES_EVENTS"]["product_admitted"] is False
    assert etf_rows["E_BASKET"]["product_admitted"] is False
    assert etf_rows["E_BASKET"]["v25_data_object_fact_consumed"] is False
    assert stock_rows["P_INDUSTRY"]["product_admitted"] is False
    assert etf_rows["E_NAV_IOPV"]["product_admitted"] is False

    stock_trace = reg.validate_runtime_trace(stock)
    stock_universe = stock_trace["complete_research_universe"]
    source_row = next(
        row for row in stock_universe["data_objects"]
        if row["dataset_id"] == "P_CODE_IDENTITY"
    )
    admission = next(
        row for row in stock_universe["data_object_admission"]["objects"]
        if row["dataset_id"] == "P_CODE_IDENTITY"
    )
    projection = next(
        row for row in projected["receipt"]["whole_scope_projection"][
            "data_object_projections"
        ]
        if row["id"] == "P_CODE_IDENTITY"
    )
    rendered = {
        row["block_id"]: deepcopy(row)
        for row in projected["receipt"]["rendered_blocks"]
    }
    location = projection["location_ids"][0]
    rendered[location]["input_data_object_fact_hashes"] = ()
    missing_visible_fact = v25._stage1_data_object_consumer_row(
        source_row,
        product_admitted=True,
        admission=admission,
        scope_projection=projection,
        rendered_lookup=rendered,
        direct_leaf_dataset_ids=set(),
    )
    assert missing_visible_fact["v25_consumer_bound"] is False
    # Missing Product admission fails before the later visible-fact hash gate.
    assert missing_visible_fact["v25_consumer_state"] == (
        "V25_CONSUMER_BINDING_MISSING"
    )

    forged_stock = deepcopy(stock)
    forged_stock["data_object_source_observations"]["P_CODE_IDENTITY"][
        "facts"
    ]["short_name"] = "伪造简称"
    with pytest.raises(
        reg.TraceabilityError,
        match=(
            "V25_DYNAMIC_CANONICAL_BINDING_INVALID|"
            "V25_ADMITTED_DATA_OBJECT_FACT_BINDING_MISSING"
        ),
    ):
        render_v2_5_dynamic_no_send(
            [_v25_result(factor=forged_stock)],
            report_date="2026-10-31",
        )


def test_stage1_vertical_receipt_blocks_registered_historical_and_carrier_substitution():
    factor = native_summary_with_mtf(intraday=True, volume_unit="share")
    factor["investor_brief"] = _build_asset_research_brief_v1(
        SimpleNamespace(current_price=None), factor,
    )
    binding = canonical_factor_binding(factor)
    factor["canonical_decision_identity"] = binding
    factor["investor_brief"]["canonical_binding"] = dict(binding)
    projected = render_v2_5_dynamic_no_send(
        [_v25_result(factor=factor)], report_date="2099-01-01",
    )
    stage1 = projected["receipt"]["stage1_vertical_product_receipt"]
    material = stage1["materials"][0]

    assert stage1["state"] == "HOLD"
    assert len(material["method_rows"]) == 43
    assert len(material["cell_rows"]) == 56
    assert material["actual_invocation_verified_count"] == 0
    assert "METHOD_OUTPUT_NOT_ACTUAL_INVOCATION_VERIFIED" in material["blocker_codes"]
    assert "RESEARCH_ASOF_MISSING_OR_REPORT_DATE_MISMATCH" in material["blocker_codes"]
    assert material["carrier_trigger_separation"]["carrier_state"] in {
        "READY", "PARTIAL",
    }
    assert material["carrier_trigger_separation"]["carrier_is_not_trigger"] is True
    assert material["carrier_trigger_separation"]["carrier_strategy_admitted"] is False
    assert stage1["policy"]["registered_is_not_executed"] is True
    assert stage1["policy"]["historical_execution_never_backfills_current_report"] is True
    assert v25.validate_stage1_vertical_product_receipt(stage1) == stage1


def test_stage1_method_invocation_uses_exact_method_receipts_not_parent_rollup():
    rows = (
        {
            "state": "READY",
            "method_receipt_hash": "a" * 64,
            "runtime_source_bound": True,
        },
        {
            "state": "MISSING",
            "method_receipt_hash": None,
            "runtime_source_bound": False,
        },
    )
    assert v25._stage1_exact_method_invocation_verified(
        rows, strict=True, runtime_state="READY",
    ) is True

    forged = (
        {**rows[0], "method_receipt_hash": None},
        rows[1],
    )
    assert v25._stage1_exact_method_invocation_verified(
        forged, strict=True, runtime_state="READY",
    ) is False
    assert v25._stage1_exact_method_invocation_verified(
        rows, strict=False, runtime_state="READY",
    ) is False


def test_stage1_vertical_receipt_separates_partial_product_from_v25_leaf_consumption():
    factor, _minute = _closed_typed_stock_source_factor(source_version_known=True)
    _source_row, _receipt, admitted = _p_daily_operation_admission(factor)
    template = native_summary()
    admitted["strategy_id"] = template["strategy_id"]
    admitted["canonical_decision"] = deepcopy(template["canonical_decision"])
    admitted["strategy_eligibility"] = deepcopy(template["strategy_eligibility"])
    admitted["evidence_traceability"] = reg.build_runtime_trace(admitted)
    admitted["investor_brief"] = _build_asset_research_brief_v1(
        SimpleNamespace(current_price=None), admitted,
    )
    binding = canonical_factor_binding(admitted)
    admitted["canonical_decision_identity"] = binding
    admitted["investor_brief"]["canonical_binding"] = dict(binding)

    projected = render_v2_5_dynamic_no_send(
        [_v25_result(factor=admitted)], report_date="2026-10-02",
    )
    stage1 = projected["receipt"]["stage1_vertical_product_receipt"]
    material = stage1["materials"][0]
    objects = {row["dataset_id"]: row for row in material["data_object_rows"]}

    assert stage1["state"] == "HOLD"
    assert objects["P_DAILY"]["product_admitted"] is True
    assert objects["P_5M"]["product_admitted"] is False
    assert objects["P_DAILY"]["v25_leaf_consumed"] is True
    assert objects["P_DAILY"]["v25_scope_audit_consumed"] is True
    assert objects["P_DAILY"]["v25_consumer_bound"] is True
    assert objects["P_DAILY"]["v25_consumer_state"] == (
        "V25_PRICE_TIMEFRAME_LEAF_CONSUMED"
    )
    assert objects["P_5M"]["v25_leaf_consumed"] is False
    assert objects["P_5M"]["v25_consumer_state"] == "NOT_PRODUCT_ADMITTED"
    assert material["research_as_of_current"] is True
    assert "FULL_DOMAIN_PRODUCT_DATA_NOT_READY" in material["blocker_codes"]
    assert "PRODUCT_DATA_OBJECT_V25_CONSUMER_BINDING_INCOMPLETE" not in (
        material["blocker_codes"]
    )
    assert "V25_AVAILABLE_SLOT_LEAF_CONSUMPTION_INCOMPLETE" not in (
        material["blocker_codes"]
    )
    assert any(
        row["coverage_state"] == "EVIDENCE_AVAILABLE"
        and row["projection_state"] == "RENDERED_MISSINGNESS"
        and row["leaf_consumed"] is False
        for row in material["slot_rows"]
    )
    assert any(
        row["projection_state"] == "RENDERED_PARTIAL_EVIDENCE"
        and row["consumer_state"] == "V25_PARTIAL_LEAF_CONSUMED"
        and row["timeframe_leaf_hashes"]
        for row in material["slot_rows"]
    )
    assert any(
        row["timeframe"] == "daily"
        and row["price_dataset_product_admitted"] is True
        for row in material["cell_rows"]
    )
    assert any(
        row["timeframe"] == "5m"
        and row["price_dataset_product_admitted"] is False
        for row in material["cell_rows"]
    )


def test_stage1_nonprice_object_uses_exact_machine_scope_consumer_not_leaf():
    rendered = {
        "block-7": {"projection_sha256": "a" * 64},
    }
    row = v25._stage1_data_object_consumer_row(
        {
            "dataset_id": "G_VIX",
            "domain": "GLOBAL",
            "state": "READY",
            "reason": "PRODUCT_OPERATION_ADMITTED",
        },
        product_admitted=True,
        admission={
            "dataset_id": "G_VIX",
            "product_admitted": True,
            "admission_hash": "b" * 64,
        },
        scope_projection={
            "id": "G_VIX",
            "domain": "GLOBAL",
            "state": "READY",
            "consumer_projection": (
                "MACHINE_RECEIPT_WITH_ROLE_SUMMARIZED_EMAIL"
            ),
            "location_ids": ("block-7",),
            "projection_sha256": "a" * 64,
            "product_admission_hashes": ("b" * 64,),
        },
        rendered_lookup=rendered,
        direct_leaf_dataset_ids=set(),
    )

    assert row["v25_leaf_consumed"] is False
    assert row["v25_scope_audit_consumed"] is True
    assert row["v25_consumer_bound"] is True
    assert row["v25_consumer_state"] == "V25_MACHINE_SCOPE_CONSUMED"
    assert row["scope_product_admission_hashes"] == ("b" * 64,)
    assert len(row["v25_consumer_binding_hash"]) == 64

    rejected = v25._stage1_data_object_consumer_row(
        {
            "dataset_id": "G_VIX", "domain": "GLOBAL",
            "state": "READY", "reason": "PRODUCT_OPERATION_ADMITTED",
        },
        product_admitted=True,
        admission={
            "dataset_id": "G_VIX", "product_admitted": True,
            "admission_hash": "b" * 64,
        },
        scope_projection={
            "id": "G_VIX", "domain": "GLOBAL", "state": "READY",
            "consumer_projection": "ROLE_SUMMARY_ONLY",
            "location_ids": ("block-7",), "projection_sha256": "a" * 64,
            "product_admission_hashes": ("b" * 64,),
        },
        rendered_lookup=rendered,
        direct_leaf_dataset_ids=set(),
    )
    assert rejected["v25_consumer_bound"] is False
    assert rejected["v25_consumer_state"] == "V25_CONSUMER_BINDING_MISSING"


def test_stage1_vertical_receipt_rejects_forged_v25_leaf_consumption():
    # Every accepted slot class must have a real Stage-1 consumer proof route;
    # no slot may remain permanently HOLD merely because the compiler omitted it.
    slot_ids = {
        row["id"] for row in reg.load_slot_map()["slots"]
    }
    accounted = (
        set(v25._STAGE1_DIRECT_SLOT_CONSUMERS)
        | set(v25._STAGE1_TIMEFRAME_COMPOSITE_SLOT_CONSUMERS)
        | {
            slot_id for slot_id in slot_ids
            if slot_id.startswith(v25._STAGE1_TIMEFRAME_SLOT_PREFIX)
        }
    )
    assert slot_ids == accounted

    projected = render_v2_5_dynamic_no_send(
        [_v25_result()], report_date="2026-10-23",
    )
    forged = deepcopy(projected["receipt"]["stage1_vertical_product_receipt"])
    slot = forged["materials"][0]["slot_rows"][0]
    slot["consumer_state"] = "V25_LEAF_CONSUMED"
    slot["leaf_consumed"] = True
    slot["location_projection_hashes"] = ("",)
    slot.pop("consumer_binding_hash", None)
    slot["consumer_binding_hash"] = reg.digest(slot)
    material = forged["materials"][0]
    material.pop("material_hash", None)
    material["material_hash"] = reg.digest(material)
    body = dict(forged)
    body.pop("receipt_hash", None)
    forged["receipt_hash"] = reg.digest(body)

    with pytest.raises(
        reg.TraceabilityError,
        match="STAGE1_VERTICAL_FALSE_V25_CONSUMPTION",
    ):
        v25.validate_stage1_vertical_product_receipt(forged)


def test_stage1_vertical_rejects_direct_slot_without_rendered_input_binding():
    projected = render_v2_5_dynamic_no_send(
        [_v25_result()], report_date="2026-10-23",
    )
    forged = deepcopy(projected["receipt"]["stage1_vertical_product_receipt"])
    slot = next(
        row for row in forged["materials"][0]["slot_rows"]
        if row["slot_id"] in v25._STAGE1_DIRECT_SLOT_CONSUMERS
        and row["canonical_path_hashes"]
    )
    slot["consumer_state"] = "V25_LEAF_CONSUMED"
    slot["leaf_consumed"] = True
    slot["location_projection_hashes"] = ("a" * 64,)
    slot["rendered_canonical_path_hashes"] = ()
    slot.pop("consumer_binding_hash", None)
    slot["consumer_binding_hash"] = reg.digest(slot)
    material = forged["materials"][0]
    material.pop("material_hash", None)
    material["material_hash"] = reg.digest(material)
    body = dict(forged)
    body.pop("receipt_hash", None)
    forged["receipt_hash"] = reg.digest(body)

    with pytest.raises(
        reg.TraceabilityError,
        match="STAGE1_VERTICAL_FALSE_V25_CONSUMPTION",
    ):
        v25.validate_stage1_vertical_product_receipt(forged)


def test_dynamic_v25_projects_complete_original_frame_and_truthful_missingness():
    original_path = ROOT / "templates/v2_5/STOCK_DSA_V2_5_PRODUCTION_MAIL_BASELINE_R001.html"
    before = original_path.read_bytes()
    projected = render_v2_5_dynamic_no_send(
        [_v25_result()], report_date="2026-10-23",
    )
    html = projected["html"]
    receipt = projected["receipt"]

    assert original_path.read_bytes() == before
    assert len(before) == 125919
    assert receipt["baseline_sha256"] == reg.BASELINE_SHA256
    assert receipt["frame_contract"]["mail_count"] == 3
    assert len(receipt["frame_contract"]["card_ids"]) == 10
    assert len(receipt["frame_contract"]["cycle_ids"]) == 84
    assert receipt["slot_projection_count"] == 270
    assert receipt["all_accepted_slot_roles_projected"] is True
    assert receipt["all_whole_scope_obligations_projected"] is True
    assert receipt["whole_scope_projection"]["requirement_count"] == 26
    assert receipt["whole_scope_projection"]["product_slot_count"] == 27
    assert len(receipt["whole_scope_projection"]["atomic_subfields"]) == 7
    scope = receipt["whole_scope_projection"]
    assert len(scope["requirement_projections"]) == 26
    assert len(scope["data_object_projections"]) == 76
    assert len(scope["valuation_route_projections"]) == 9
    assert len(scope["atomic_subfield_projections"]) == 7
    assert scope["data_object_catalog_binding_sha256"] == (
        reg.ACCEPTED_DATA_OBJECT_SUBSET_SHA256
    )
    assert tuple(row["id"] for row in scope["requirement_projections"]) == tuple(
        sorted(identifier for identifier, _role in reg.ACCEPTED_REQUIREMENT_ROLES)
    )
    scope_rows = (
        list(scope["requirement_projections"])
        + list(scope["data_object_projections"])
        + list(scope["valuation_route_projections"])
        + list(scope["atomic_subfield_projections"])
    )
    assert len(scope_rows) == 118
    assert all(row["location_ids"] for row in scope_rows)
    assert all(len(row["projection_sha256"]) == 64 for row in scope_rows)
    assert {
        row["consumer_projection"] for row in scope_rows
    } == {"MACHINE_RECEIPT_WITH_ROLE_SUMMARIZED_EMAIL"}
    quality = receipt["email_consumer_quality"]
    assert quality == {
        "projection_mode": "ROLE_SUMMARIZED_EMAIL_PLUS_MACHINE_RECEIPT",
        "raw_scope_row_count": 0,
        "blank_inline_tag_count": 0,
        "blank_unclassified_span_count": 0,
        "indicator_family_labels": tuple(
            label for label, _requirement in v25._V25_FAMILY_REQUIREMENTS
        ),
        "missing_indicator_family_labels": (),
        "repeated_policy_filler_count": 0,
        "generic_placeholder_count": 0,
        "ordinary_language_roles_preserved": True,
    }
    assert not any(prefix in html for prefix in (
        "研究要求 ", "数据对象 ", "估值路线 ", "Golden原子字段 ",
    ))
    assert receipt["results"][0]["fact_mode"] == "MISSINGNESS_ONLY"
    assert receipt["all_input_facts_product_ready"] is False
    assert receipt["no_send"] is True
    assert receipt["network_calls"] == receipt["database_writes"] == 0
    assert "600519" in html
    assert "全域Product尚未准入" in html
    assert {
        row["id"] for row in scope["atomic_subfield_projections"]
    } == set(reg.ACCEPTED_WHOLE_PRODUCT_ATOMIC_SUBFIELDS)
    assert "另有7项全球细字段待独立绑定" in html
    stage1 = receipt["stage1_vertical_product_receipt"]
    atomic_rows = stage1["materials"][0]["atomic_subfield_rows"]
    assert {row["v25_consumer_state"] for row in atomic_rows} == {
        "V25_ATOMIC_MISSINGNESS_CONSUMED"
    }
    assert all(row["v25_consumer_bound"] is True for row in atomic_rows)
    assert all(row["v25_atomic_fact_consumed"] is False for row in atomic_rows)
    assert "ATOMIC_SUBFIELD_V25_CONSUMER_BINDING_INCOMPLETE" not in (
        stage1["blocker_codes"]
    )
    assert not any(fragment in html for fragment in (
        "示例成长科技ETF", "1.264元", "自选股X（6008X9）",
    ))
    original_markup = re.findall(r"<[^>]+>", before.decode("utf-8"))
    projected_markup = re.findall(r"<[^>]+>", html)
    assert projected_markup == original_markup


def test_atomic_fact_value_and_hash_are_required_for_reader_and_stage1_consumption():
    identifier = "GLOBAL_US_ACTUAL_INFLATION"
    facts = {
        "series_id": "SYNTHETIC_CPI",
        "reference_period": "2026-09",
        "index_value": 321.5,
        "yoy_pct": 2.4,
        "release_published_at": "2026-10-23T08:30:00+00:00",
        "seasonal_adjustment": "SEASONALLY_ADJUSTED",
        "value_unit": "INDEX_1982_84_100",
    }
    fact_hash = reg.digest(facts)
    summary = v25._v25_atomic_fact_summary(identifier, facts)
    assert "美国整体通胀：同比 2.40%" in summary
    assert "2026-09" in summary
    assert "2026-10-23T08:30:00+00:00" in summary

    universe = {
        "data_objects": [],
        "atomic_subfield_admission_rows": [{
            "id": identifier,
            "state": "BOUND",
            "facts": facts,
            "source_fact_hash": fact_hash,
            "fact_summary": summary,
        }],
    }
    assert summary in v25._v25_domain_line(universe, 0)

    scope = {
        "id": identifier,
        "state": "BOUND",
        "consumer_projection": "MACHINE_RECEIPT_WITH_ROLE_SUMMARIZED_EMAIL",
        "location_ids": ("block-1",),
        "projection_sha256": "b" * 64,
        "product_admission_hashes": ("a" * 64,),
        "source_fact_hash": fact_hash,
        "fact_summary": summary,
    }
    rendered = {
        "block-1": {
            "projection_sha256": "b" * 64,
            "input_atomic_fact_hashes": (fact_hash,),
        },
    }
    row = v25._stage1_atomic_subfield_consumer_row(
        identifier,
        admission={
            "dataset_id": identifier,
            "product_admitted": True,
            "admission_hash": "a" * 64,
        },
        scope_projection=scope,
        rendered_lookup=rendered,
    )
    assert row["v25_atomic_fact_consumed"] is True
    assert row["v25_atomic_fact_hash_consumed"] is True
    assert row["source_fact_hash"] == fact_hash

    missing_hash = deepcopy(rendered)
    missing_hash["block-1"]["input_atomic_fact_hashes"] = ()
    blocked = v25._stage1_atomic_subfield_consumer_row(
        identifier,
        admission={
            "dataset_id": identifier,
            "product_admitted": True,
            "admission_hash": "a" * 64,
        },
        scope_projection=scope,
        rendered_lookup=missing_hash,
    )
    assert blocked["v25_atomic_fact_consumed"] is False


def test_stage1_rejects_atomic_product_admission_without_fact_consumption():
    projected = render_v2_5_dynamic_no_send(
        [_v25_result()], report_date="2026-10-23",
    )
    forged = deepcopy(projected["receipt"]["stage1_vertical_product_receipt"])
    row = forged["materials"][0]["atomic_subfield_rows"][0]
    row["product_admitted"] = True
    row["admission_hash"] = "a" * 64
    row["v25_atomic_fact_consumed"] = False
    row.pop("v25_consumer_binding_hash", None)
    row["v25_consumer_binding_hash"] = reg.digest(row)
    material = forged["materials"][0]
    material.pop("material_hash", None)
    material["material_hash"] = reg.digest(material)
    body = dict(forged)
    body.pop("receipt_hash", None)
    forged["receipt_hash"] = reg.digest(body)

    with pytest.raises(
        reg.TraceabilityError,
        match="STAGE1_VERTICAL_FALSE_ATOMIC_CONSUMPTION",
    ):
        v25.validate_stage1_vertical_product_receipt(forged)


def test_dynamic_v25_email_is_reader_facing_while_full_scope_stays_in_receipt():
    projected = render_v2_5_dynamic_no_send(
        [_v25_result()], report_date="2026-10-23",
    )
    html = projected["html"]
    receipt = projected["receipt"]
    mails = html.split('<div class="mail">')
    assert len(mails) == 4
    market, auto, watch = mails[1], mails[2], mails[3]

    assert "测试股票（600519）" not in market
    assert "测试股票（600519）" in watch
    assert "代表指数综合｜市场价格、宽度与七周期事实未准入" in market
    assert "市场宽度、分母、新高新低和均线上方比例未准入" in market
    assert "全球证据只能确认、降级或失效，不能独立升级BUY" in market
    assert "ETF重点空位1｜本轮无已准入研究对象" in auto
    assert "股票重点空位1｜本轮无已准入研究对象" in auto
    assert "全域Product尚未准入" in watch
    assert all(
        label in html for label, _requirement in v25._V25_FAMILY_REQUIREMENTS
    )
    assert not re.search(r"<(?:b|strong)>\s*</(?:b|strong)>", html)
    assert not re.search(r"<span>\s*</span>", html)
    assert "结论：大盘、红利和工业方向优先" not in html
    assert "这是箱体内向上修复，不是已经确认的箱体突破" not in html
    assert "沪深300" in market
    assert "上证50" in market
    assert "证据、反证、风险、触发和失效必须同时保留；缺失不被技术分抵消。" not in html
    assert not any(prefix in html for prefix in (
        "研究要求 ", "数据对象 ", "估值路线 ", "Golden原子字段 ",
    ))

    scope = receipt["whole_scope_projection"]
    rows = (
        list(scope["requirement_projections"])
        + list(scope["data_object_projections"])
        + list(scope["valuation_route_projections"])
        + list(scope["atomic_subfield_projections"])
    )
    assert len(rows) == 118
    assert all(row["location_ids"] and row["projection_sha256"] for row in rows)
    assert receipt["all_whole_scope_obligations_projected"] is True


@pytest.mark.parametrize(
    ("mutation", "error"),
    [
        (lambda html: html + "数据对象 G_EQ_US", "V25_DYNAMIC_RAW_SCOPE_ROW_LEAK"),
        (lambda html: html + "<strong></strong>", "V25_DYNAMIC_BLANK_INLINE_TAG"),
        (lambda html: html + "<span></span>", "V25_DYNAMIC_BLANK_INLINE_TAG"),
        (
            lambda html: html + "证据、反证、风险、触发和失效必须同时保留；缺失不被技术分抵消。",
            "V25_DYNAMIC_REPEATED_POLICY_FILLER",
        ),
        (
            lambda html: html + "本次无已准入候选",
            "V25_DYNAMIC_GENERIC_PLACEHOLDER_REPETITION",
        ),
        (
            lambda html: html.replace("市场/行业环境", "环境"),
            "V25_DYNAMIC_INDICATOR_FAMILY_SUMMARY_MISSING",
        ),
        (
            lambda html: html.replace("五、全球背景怎样影响A股方向", "全球", 1),
            "V25_DYNAMIC_ORDINARY_LANGUAGE_ROLE_LOST",
        ),
    ],
)
def test_dynamic_v25_email_quality_gate_rejects_reader_contract_regressions(
    mutation, error,
):
    html = render_v2_5_dynamic_no_send(
        [_v25_result()], report_date="2026-10-23",
    )["html"]
    with pytest.raises(reg.TraceabilityError, match=error):
        v25._v25_email_consumer_quality(mutation(html))


def test_dynamic_v25_empty_and_partial_candidate_routes_are_legal_nontriggers():
    projected = render_v2_5_dynamic_no_send([], report_date="2026-10-23")
    receipt = projected["receipt"]
    assert receipt["envelopes"]["AUTO"] == {
        "mail_index": 1,
        "focus_etf_count": 0,
        "remaining_etf_count": 0,
        "focus_stock_count": 0,
        "remaining_stock_count": 0,
    }
    assert receipt["envelopes"]["WATCHLIST"]["result_count"] == 0
    assert receipt["slot_projection_count"] == 270
    assert {row["projection_state"] for row in receipt["slot_projections"]} == {
        "RENDERED_MISSINGNESS"
    }
    assert "本次无第4名ETF候选" in projected["html"]
    assert "本次无第4名股票候选" in projected["html"]
    assert "示例成长科技ETF" not in projected["html"]
    one_result = render_v2_5_dynamic_no_send(
        [_v25_result()], report_date="2026-10-23",
    )
    assert (
        receipt["frame_contract"]["fixed_region_sha256"]
        == one_result["receipt"]["frame_contract"]["fixed_region_sha256"]
    )
    assert receipt["whole_scope_projection"]["requirement_count"] == 26
    assert len(receipt["whole_scope_projection"]["data_object_projections"]) == 76
    assert {
        row["state"]
        for row in receipt["whole_scope_projection"]["data_object_projections"]
    } == {"UNKNOWN"}


def test_dynamic_v25_rejects_original_canonical_and_asset_identity_tamper():
    original = (
        ROOT / "templates/v2_5/STOCK_DSA_V2_5_PRODUCTION_MAIL_BASELINE_R001.html"
    ).read_bytes()
    with pytest.raises(reg.TraceabilityError, match="ORIGINAL_BYTES_MISMATCH"):
        render_v2_5_dynamic_no_send(
            [_v25_result()], original=original.replace(b".mail{", b".mail-x{")
        )

    factor = native_summary()
    factor["canonical_decision"]["action"] = "TAKE"
    with pytest.raises(reg.TraceabilityError, match="CANONICAL_BINDING_INVALID"):
        render_v2_5_dynamic_no_send([_v25_result(factor=factor)])

    with pytest.raises(reg.TraceabilityError, match="ASSET_IDENTITY_MISMATCH"):
        render_v2_5_dynamic_no_send([_v25_result(code="000001")])


def test_dynamic_v25_candidate_capacity_is_fail_closed_without_forced_fill():
    focus = [
        {
            "code": f"51{i:04d}",
            "asset_type": "etf",
            "delivery": {
                "selection_source": "AUTO_SCREEN",
                "product_group": "AUTO_ETF_FOCUS",
                "group_rank": i + 1,
            },
        }
        for i in range(4)
    ]
    with pytest.raises(reg.TraceabilityError, match="FOCUS_CAPACITY"):
        _v25_assign(focus)

    watch = [
        {
            "code": f"60{i:04d}",
            "asset_type": "stock",
            "delivery": {"selection_source": "SPECIFIED_CODES"},
        }
        for i in range(5)
    ]
    with pytest.raises(reg.TraceabilityError, match="WATCHLIST_CAPACITY"):
        _v25_assign(watch)


def test_notification_v25_builder_is_no_send_and_uses_same_full_text_consumer(monkeypatch):
    from src.notification import NotificationService

    result = _v25_result()
    service = object.__new__(NotificationService)
    monkeypatch.setattr(
        service,
        "generate_dashboard_report",
        lambda results, report_date=None: "FULL-CANONICAL-TEXT",
    )
    monkeypatch.setattr(
        service,
        "build_research_email_subject",
        lambda results, report_date=None: "V2.5 SUBJECT",
    )
    sent = []
    monkeypatch.setattr(service, "send_to_email", lambda *args, **kwargs: sent.append((args, kwargs)))

    bundle = service.build_v2_5_dynamic_no_send(
        [result], report_date="2026-10-23",
    )
    assert bundle["subject"] == "V2.5 SUBJECT"
    assert bundle["text_content"] == "FULL-CANONICAL-TEXT"
    assert bundle["receipt"]["rendered"] is True
    assert bundle["receipt"]["no_send"] is True
    assert bundle["no_send"] is True
    assert sent == []
