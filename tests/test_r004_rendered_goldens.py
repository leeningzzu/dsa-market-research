from __future__ import annotations

import ast

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional

import pytest

pytestmark = pytest.mark.r004_forensic
from jinja2 import Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[1]

_JINJA_ENV = Environment(loader=FileSystemLoader(str(ROOT / "templates")))
_REPORT_TEMPLATE = _JINJA_ENV.get_template("report_markdown.j2")
_BRIEF_TEMPLATE = _JINJA_ENV.get_template("report_brief.j2")


class _Labels:
    def __getattr__(self, name: str) -> str:
        return name


def _brief() -> dict:
    return {
        "schema_version": "investor-brief-v1",
        "asset_type": "stock",
        "data_clock": {
            "schema_version": "product-data-clock-v1",
            "data_usage_mode": "PRODUCTION_LATEST",
            "state": "LATEST_COMPLETED",
            "product_current": True,
            "target_date": "2026-09-22",
            "data_as_of": "2026-09-22",
            "completed_through": "2026-09-22",
            "available_at_max": "2026-09-22T10:00:00",
            "provider_identity": "FORENSIC_FIXTURE",
            "adjustment_basis": "qfq",
            "data_snapshot_identity": "r004-forensic-snapshot",
            "reason": "TARGET_COMPLETED_BAR_READY",
        },
        "one_line_conclusion": "高周期结构仍可，但等待量价确认，不因短线信号直接升级。",
        "fused_paragraph": "周线结构仍偏强；日线缩量回踩，等待量价重新确认。",
        "coverage_text": (
            "本次可用周期：月线、周线、日线。"
            "60分钟、30分钟、15分钟和5分钟本次暂无可用证据。"
        ),
        "timeframe_thesis": {
            "monthly": {"status": "READY", "role": "LONG_TERM_CONTEXT", "summary": "月线平台收敛"},
            "weekly": {"status": "READY", "role": "PRIMARY_TREND_CONTEXT", "summary": "周线多头排列"},
            "daily": {"status": "PARTIAL_CURRENT", "role": "PRIMARY_SETUP", "summary": "日线缩量回踩"},
            "60m": {"status": "MISSING", "role": "OPTIONAL_BRIDGE", "summary": None},
        },
        "short_term_execution_panel": {
            "status": "MISSING",
            "state": "DATA_INSUFFICIENT",
            "30m": {"status": "MISSING", "role": "PRIMARY_STRUCTURE"},
            "15m": {"status": "MISSING", "role": "TRIGGER_CONFIRMATION"},
            "5m": {"status": "MISSING", "role": "MICRO_TIMING"},
            "summary": None,
        },
        "current_price": {"value": 10.50, "source_state": "AVAILABLE"},
        "valuation": {
            "status": "PARTIAL_CURRENT",
            "label": "估值",
            "summary": "PE 18.0；PB 2.1",
            "uncertainty": "尚无可靠合理价区间",
        },
        "key_levels": {
            "support": "10.00",
            "resistance": "11.20",
            "support_label": "结构支撑",
            "resistance_label": "结构压力",
        },
        "trigger": "放量站回 11.20 且结构确认",
        "invalidation": "放量跌破 10.00",
        "risk_notes": ["高周期与短周期仍可能冲突", "估值证据有限"],
        "scenario": {"alternative": {"status": "MISSING", "condition": None}},
        "historical_reference": {
            "available": False,
            "reason": "同策略/同期限/PIT一致样本不足",
        },
        "asset_specific": {},
        "material_events": [],
    }


def _render_asset(
    brief: dict | None = None,
    *,
    market_status_line: str = "",
    code: str = "600000",
    stock_name: str = "测试股票",
    composite_score: int | None = 78,
    current_probability: str = "暂不提供（尚未完成独立校准）",
    compact: bool = False,
) -> str:
    brief = deepcopy(brief or _brief())
    factor = {
        "investor_brief": brief,
        "composite_score": composite_score,
        "current_probability": {"display": current_probability},
    }
    result = SimpleNamespace(
        code=code,
        sentiment_score=composite_score or 0,
        dashboard={"factor_decision": factor},
        trend_prediction="震荡",
        analysis_summary="",
        buy_reason="",
        risk_warning="",
        market_snapshot=None,
    )
    enriched = SimpleNamespace(
        result=result,
        stock_name=stock_name,
        signal_emoji="🟡",
        signal_text="观察",
        localized_trend_prediction="震荡",
        localized_operation_advice="观察",
        canonical_authority=True,
        investor_brief_valid=True,
        investor_brief=brief,
        product_data_current=True,
        data_clock_text="数据截至：2026-09-22（最新已完成交易日；价格口径 qfq）",
    )
    template = _BRIEF_TEMPLATE if compact else _REPORT_TEMPLATE
    return template.render(
        report_date="2026-09-22",
        labels=_Labels(),
        results=[result],
        buy_count=0,
        hold_count=1,
        sell_count=0,
        market_status_line=market_status_line,
        enriched=[enriched],
        summary_only=False,
        report_language="zh",
        report_timestamp="2026-09-22 19:00",
        show_llm_model=False,
        models_used=[],
        history_by_code={},
        signal_attribution_weight_items=lambda value: [],
        signal_attribution_has_content=lambda value: False,
        normalize_strategy_synthesis_payload=lambda value: None,
    )


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_gf01_full_mtf_alignment() -> None:
    brief = _brief()
    brief["timeframe_thesis"]["60m"] = {
        "status": "READY",
        "role": "OPTIONAL_BRIDGE",
        "summary": "60分钟回踩结构未破",
    }
    brief["short_term_execution_panel"] = {
        "status": "READY",
        "state": "WAIT_FOR_TRIGGER",
        "30m": {"status": "READY", "role": "PRIMARY_STRUCTURE", "summary": "30分钟平台收敛"},
        "15m": {"status": "READY", "role": "TRIGGER_CONFIRMATION", "summary": "15分钟等待量价确认"},
        "5m": {"status": "READY", "role": "MICRO_TIMING", "summary": "5分钟仅作时点"},
        "summary": None,
    }
    out = _render_asset(brief)
    markers = [
        "月线：月线平台收敛",
        "周线：周线多头排列",
        "日线：日线缩量回踩",
        "60分钟：60分钟回踩结构未破",
        "30分钟：30分钟平台收敛",
        "15分钟：15分钟等待量价确认",
        "5分钟：5分钟仅作时点",
    ]
    positions = [out.index(marker) for marker in markers]
    assert positions == sorted(positions)


def test_gf02_zero_axis_below_golden_cross_rebound_only() -> None:
    brief = _brief()
    brief["timeframe_thesis"]["daily"]["summary"] = "日线MACD零轴下金叉，仅视作反弹确认，不定义趋势反转"
    out = _render_asset(brief)
    assert "MACD零轴下金叉，仅视作反弹确认" in out
    assert "金叉=反转" not in out


def test_gf03_confirmed_divergence_with_higher_trend() -> None:
    brief = _brief()
    brief["timeframe_thesis"]["weekly"]["summary"] = "周线上升结构仍在"
    brief["timeframe_thesis"]["daily"]["summary"] = "日线顶背离已经确认，短期动量转弱"
    out = _render_asset(brief)
    assert "周线：周线上升结构仍在" in out
    assert "日线：日线顶背离已经确认，短期动量转弱" in out


def test_gf04_pattern_forming_confirmed_failed() -> None:
    brief = _brief()
    brief["timeframe_thesis"]["monthly"]["summary"] = "月线杯柄形成中"
    brief["timeframe_thesis"]["weekly"]["summary"] = "周线双底已经确认"
    brief["timeframe_thesis"]["daily"]["summary"] = "日线突破失败，回到平台内"
    out = _render_asset(brief)
    for marker in ("月线杯柄形成中", "周线双底已经确认", "日线突破失败"):
        assert marker in out


def test_gf05_lower_timeframe_conflict() -> None:
    brief = _brief()
    brief["one_line_conclusion"] = "周线下降未修复，5分钟反弹不能升级为买入。"
    brief["timeframe_thesis"]["weekly"]["summary"] = "周线下降结构未修复"
    brief["short_term_execution_panel"] = {
        "status": "READY",
        "30m": {"status": "READY", "summary": "30分钟弱反弹"},
        "15m": {"status": "READY", "summary": "15分钟金叉"},
        "5m": {"status": "READY", "summary": "5分钟放量上冲"},
        "summary": None,
    }
    out = _render_asset(brief)
    assert "周线下降未修复，5分钟反弹不能升级为买入" in out
    assert "15分钟：15分钟金叉" in out


def test_gf06_same_swing_no_double_count() -> None:
    brief = _brief()
    brief["fused_paragraph"] = "日线同一摆动上的MACD/RSI顶背离合并为一次确认。"
    brief["timeframe_thesis"]["daily"]["summary"] = "日线动量减弱，等待重新确认"
    out = _render_asset(brief)
    assert out.count("合并为一次确认") == 1


def test_gf07_large_cap_strong_small_cap_weak() -> None:
    analyzer = _source("src/market_analyzer.py")
    renderer = _source("src/core/market_review.py")
    assert "large_cap_role" in analyzer and "small_cap_role" in analyzer
    assert "representative_index_roles" in analyzer
    assert "代表指数职责" in renderer
    assert "代表指数缺口" in renderer


def test_gf08_speculative_heat_breadth_weak() -> None:
    analyzer = _source("src/market_analyzer.py")
    renderer = _source("src/core/market_review.py")
    assert '"speculative_heat"' in analyzer and '"breadth_state"' in analyzer
    assert "投机热度代理（涨跌停结构）" in renderer


@pytest.mark.xfail(
    reason="R004 SCHEMA_NOW_DATA_LATER: margin/ETF-share/credit/ERP evidence is not yet bound to the market brief.",
    strict=True,
)
def test_gf09_margin_acceleration_price_inefficient() -> None:
    source = _source("src/market_analyzer.py") + _source("src/market_context.py")
    assert "margin_financing" in source and "equity_risk_premium" in source


@pytest.mark.xfail(
    reason="R004 SCHEMA_NOW_DATA_LATER: dated rates/USD/CNH/commodity transmission is not yet a deterministic market-brief input.",
    strict=True,
)
def test_gf10_global_support_and_rate_fx_pressure() -> None:
    source = _source("src/market_analyzer.py") + _source("src/core/market_review.py")
    assert "US10Y" in source and "USD/CNH" in source


def test_gf11_intraday_missing_daily_first() -> None:
    out = _render_asset(_brief())
    assert "日线：日线缩量回踩" in out
    assert "60分钟、30分钟、15分钟和5分钟本次暂无可用证据" in out
    assert "**短线波段**:" not in out


def test_gf12_etf_specific_fields() -> None:
    brief = _brief()
    brief["asset_type"] = "etf"
    brief["valuation"]["label"] = "底层估值"
    brief["asset_specific"] = {
        "premium_discount": {"status": "READY", "summary": "折价0.15%"},
        "liquidity_spread": {"status": "READY", "summary": "买卖价差0.03%"},
        "tracking_quality": {"status": "READY", "summary": "近20日跟踪误差0.18%"},
        "underlying_valuation": {"status": "READY", "summary": "底层估值近十年62%分位"},
    }
    for out in (
        _render_asset(brief, code="510300", stock_name="沪深300ETF"),
        _render_asset(brief, code="510300", stock_name="沪深300ETF", compact=True),
    ):
        assert "折价0.15%" in out
        assert "买卖价差0.03%" in out
        assert "跟踪误差0.18%" in out
        assert "买卖价差：买卖价差0.03%" not in out
        assert "买卖价差0.03%" in out
    missing = deepcopy(brief)
    for item in missing["asset_specific"].values():
        item["status"] = "MISSING"
    for missing_out in (
        _render_asset(missing, code="510300", stock_name="沪深300ETF"),
        _render_asset(missing, code="510300", stock_name="沪深300ETF", compact=True),
    ):
        assert "ETF专属交易质量" not in missing_out
        assert "折价0.15%" not in missing_out


def test_gf13_watchlist_quota_independent() -> None:
    phase_b = _source("tests/test_phase_b_groups_envelopes_contract.py")
    selection = _source("tests/test_research_selection_route.py")
    assert "ETF重点 Top 3" in phase_b
    assert "股票重点 Top 3" in phase_b
    assert "我的自选研究" in phase_b
    assert "specified_codes_watchlist_envelope_is_identity_bound" in selection


def test_gf14_report_email_telegram_same_authority() -> None:
    pipeline = _source("src/core/pipeline.py")
    notification = _source("src/notification.py")
    semantic_tests = _source("tests/test_notification.py")
    routing_tests = _source("tests/test_pipeline_notification_image_routing.py")
    assert "_save_local_report" in pipeline
    assert "_send_notifications" in pipeline
    assert "dashboard.get(\"factor_decision\")" in notification
    assert "investor_brief" in notification
    assert "send_to_telegram" in notification
    assert "send_to_email" in notification
    assert "test_full_and_compact_reports_preserve_canonical_material_fact_parity" in semantic_tests
    assert "test_saved_full_report_and_email_telegram_compact_share_exact_result_set" in routing_tests


def test_gf15_no_internal_language_or_repeated_disclaimer() -> None:
    out = _render_asset(_brief())
    for forbidden in ("READY", "MISSING", "P0", "独立动作权限"):
        assert forbidden not in out
    assert out.count("暂不提供（尚未完成独立校准）") == 1
    assert out.count("同策略/同期限/PIT一致样本不足") == 1


def test_gf16_market_brief_no_new_asset_upgrade() -> None:
    brief = _brief()
    brief["one_line_conclusion"] = "资产证据仍不足，继续等待。"
    out = _render_asset(brief, market_status_line="市场状态：A股 · 盘后；大盘环境偏强")
    assert "大盘环境偏强" in out
    assert "资产证据仍不足，继续等待" in out
    assert "买入" not in brief["one_line_conclusion"]


def test_gf17_explicit_timeframe_naming() -> None:
    brief = _brief()
    brief["timeframe_thesis"]["60m"] = {
        "status": "READY",
        "role": "OPTIONAL_BRIDGE",
        "summary": "回踩结构未破",
    }
    out = _render_asset(brief)
    for marker in ("月线：", "周线：", "日线：", "60分钟："):
        assert marker in out
    assert "- 60m：" not in out
    assert "周期覆盖" in out


def test_gf18_breadth_concrete_facts() -> None:
    analyzer = _source("src/market_analyzer.py")
    renderer = _source("src/core/market_review.py")
    assert '"breadth_denominator"' in analyzer and '"breadth_ratio"' in analyzer
    assert "上涨 {breadth.get('up_count', 0)} / " in renderer
    assert "总参与 {denominator}" in renderer


def test_gf19_material_event_must_render() -> None:
    brief = _brief()
    brief["fused_paragraph"] = "日线顶背离已经确认；周线突破仍有效。"
    brief["material_events"] = ["日线顶背离已经确认"]
    out = _render_asset(brief)
    assert "日线顶背离已经确认" in out


def test_gf20_stable_state_compress_no_noise() -> None:
    brief = _brief()
    brief["fused_paragraph"] = "周线结构稳定，日线缩量整理，等待下一次有效触发。"
    out = _render_asset(brief)
    for forbidden in ("无背离", "无VCP", "无金叉", "无死叉"):
        assert forbidden not in out


def test_gf21_stock_and_etf_valuation_render() -> None:
    stock_out = _render_asset(_brief())
    etf = _brief()
    etf["asset_type"] = "etf"
    etf["valuation"] = {
        "status": "PARTIAL_CURRENT",
        "label": "底层估值",
        "summary": "近十年62%分位",
        "uncertainty": "缺少完整同类比较",
    }
    etf_out = _render_asset(etf, code="510300", stock_name="沪深300ETF")
    assert "**估值**: PE 18.0；PB 2.1" in stock_out
    assert "**底层估值**: 近十年62%分位" in etf_out


def test_gf22_conditional_watchlist_envelope() -> None:
    source = _source("tests/test_phase_b_groups_envelopes_contract.py")
    assert "watchlist_material_change_uses_history_and_skips_same_run_auto" in source
    assert "schedule_runs_fixed_auto_then_conditional_watchlist" in source
    assert "WATCHLIST_HAS_CODES" in source


def test_gf23_donor_name_not_data_source() -> None:
    out = _render_asset(_brief())
    for donor in ("Qlib", "LEAN", "CZSC", "AlphaSift", "FinanceToolkit"):
        assert donor not in out


def test_gf24_market_global_etf_stock_order() -> None:
    main_path = ROOT / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))
    composer_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_compose_fused_research_notification"
    )
    namespace = {"List": List, "Optional": Optional}
    exec(compile(ast.Module(body=[composer_node], type_ignores=[]), str(main_path), "exec"), namespace)
    compose = namespace["_compose_fused_research_notification"]

    investor_content = (
        "## 🔎 晚间自动发现\n\n"
        "### ETF重点 Top 3\n\nETF_SECTION\n\n"
        "### 股票重点 Top 3\n\nSTOCK_SECTION"
    )
    rendered = compose(
        market_report="MARKET_SECTION",
        global_context="GLOBAL_SECTION",
        investor_content=investor_content,
    )
    markers = ("MARKET_SECTION", "GLOBAL_SECTION", "ETF_SECTION", "STOCK_SECTION")
    positions = [rendered.index(marker) for marker in markers]
    assert positions == sorted(positions)

    missing_global = compose(
        market_report="MARKET_SECTION",
        global_context=None,
        investor_content=investor_content,
    )
    assert "全球环境" not in missing_global
    assert "GLOBAL_SECTION" not in missing_global


def test_gf24_missing_global_preserves_legacy_two_block_projection() -> None:
    main_path = ROOT / "main.py"
    tree = ast.parse(main_path.read_text(encoding="utf-8"))
    composer_node = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_compose_fused_research_notification"
    )
    namespace = {"List": List, "Optional": Optional}
    exec(compile(ast.Module(body=[composer_node], type_ignores=[]), str(main_path), "exec"), namespace)
    compose = namespace["_compose_fused_research_notification"]
    investor_content = "ETF_SECTION\n\nSTOCK_SECTION"
    rendered = compose(
        market_report="MARKET_SECTION",
        global_context=None,
        investor_content=investor_content,
    )
    assert rendered == (
        "# 📈 大盘复盘\n\nMARKET_SECTION\n\n---\n\n"
        "# 🚀 个股投资者简报\n\nETF_SECTION\n\nSTOCK_SECTION"
    )


def test_gf25_information_moved_not_dropped() -> None:
    brief = _brief()
    brief["fused_paragraph"] = "R004_INFO_FUSED"
    brief["timeframe_thesis"]["monthly"]["summary"] = "R004_INFO_MONTHLY"
    brief["timeframe_thesis"]["weekly"]["summary"] = "R004_INFO_WEEKLY"
    brief["timeframe_thesis"]["daily"]["summary"] = "R004_INFO_DAILY"
    brief["trigger"] = "R004_INFO_TRIGGER"
    brief["invalidation"] = "R004_INFO_INVALIDATION"
    brief["risk_notes"] = ["R004_INFO_RISK"]
    for out in (_render_asset(brief), _render_asset(brief, compact=True)):
        for marker in (
            "R004_INFO_FUSED",
            "R004_INFO_MONTHLY",
            "R004_INFO_WEEKLY",
            "R004_INFO_DAILY",
            "R004_INFO_TRIGGER",
            "R004_INFO_INVALIDATION",
            "R004_INFO_RISK",
        ):
            assert marker in out
