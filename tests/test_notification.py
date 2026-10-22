# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 通知服务单元测试
===================================

职责：
1. 验证通知服务的配置检测逻辑
2. 验证通知服务的渠道检测逻辑
3. 验证通知服务的消息发送逻辑

TODO: 
1. 添加发送渠道以外的测试，如：
    - 生成日报
2. 添加 send_to_context 的测试
"""
import os
import sys
import unittest
from copy import deepcopy
from datetime import date
from unittest import mock
from types import SimpleNamespace
from typing import Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

# Keep this test runnable when optional LLM/runtime deps are not installed.
for optional_module in ("litellm", "json_repair"):
    try:
        __import__(optional_module)
    except ModuleNotFoundError:
        sys.modules[optional_module] = mock.MagicMock()

from src.config import Config
from src.notification import (
    NotificationBuilder,
    NotificationChannel,
    NotificationService,
    _append_factor_decision_block,
    _append_strategy_synthesis_block,
    _get_valid_investor_brief,
)
from src.notification_noise import reset_notification_noise_state
from src.analyzer import AnalysisResult
from src.services.factor_decision_summary import (
    apply_canonical_decision_to_result,
    assert_canonical_consumer_consistency,
    build_stock_factor_decision_summary,
    canonical_factor_binding,
)
from src.services.evidence_traceability_registry import build_runtime_trace
from src.report_language import get_report_labels, localize_strategy_conflict_description
from src.share_image import build_share_image_html
from bot.models import BotMessage, ChatType
import requests


def _make_config(**overrides) -> Config:
    """Create a Config instance overriding only notification-related fields."""
    return Config(stock_list=[], **overrides)


def _make_response(status_code: int, json: Optional[dict] = None) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    if json:
        response.json = lambda: json
    return response


def _notification_canonical_5m(session_dates: list[date]):
    """Build exact completed 5m fixture without importing the full Pipeline."""
    import pandas as pd
    from data_provider.intraday_data_identity import (
        attach_intraday_data_identity,
        build_intraday_data_identity,
    )

    observed = pd.Timestamp(
        f"{session_dates[-1].isoformat()} 16:00:00", tz="Asia/Shanghai",
    )
    rows = []
    for session_date in session_dates:
        labels = list(pd.date_range(
            f"{session_date.isoformat()} 09:35:00",
            f"{session_date.isoformat()} 11:30:00",
            freq="5min", tz="Asia/Shanghai",
        ))
        labels += list(pd.date_range(
            f"{session_date.isoformat()} 13:05:00",
            f"{session_date.isoformat()} 15:00:00",
            freq="5min", tz="Asia/Shanghai",
        ))
        for index, bar_end in enumerate(labels):
            close = 100.0 + index * 0.01
            rows.append({
                "code": "600519", "bar_end": bar_end,
                "open": close - 0.1, "high": close + 0.5,
                "low": close - 0.5, "close": close,
                "volume": 1000.0 + index,
                "session": (
                    f"{session_date.isoformat()}:AM" if bar_end.hour < 12
                    else f"{session_date.isoformat()}:PM"
                ),
                "available_at": observed,
                "adjustflag": "2", "data_source": "BaostockFetcher",
            })
    frame = pd.DataFrame(rows)
    identity = build_intraday_data_identity(
        frame,
        provider_identity="BaostockFetcher",
        provider_route="unit.notification",
        package_version="0.9.4",
        query_identity={"code": "sh.600519", "frequency": "5", "adjustflag": "2"},
        requested_adjustment_basis="qfq",
        observed_adjustment_basis="qfq",
        basis_evidence="unit-test",
        timeframe="5m", timezone_name="Asia/Shanghai",
        session_calendar="XSHG", currency="CNY",
        volume_unit="UNKNOWN", amount_unit="UNKNOWN",
        requested_start=session_dates[0].isoformat(),
        requested_end=session_dates[-1].isoformat(),
        identity_state="OBSERVED",
        observed_at=observed.tz_convert("UTC").to_pydatetime(),
        source_rows_sha256="7" * 64,
    )
    attach_intraday_data_identity(frame, identity)
    frame["intraday_data_identity_hash"] = identity["identity_hash"]
    return frame


def _attach_decision_signal_summary(result: AnalysisResult) -> AnalysisResult:
    result.decision_signal_summary = {
        "action": "sell",
        "action_label": "卖出",
        "horizon": "1d",
        "reason": "技术面走弱",
    }
    return result


def _make_investor_brief_result() -> AnalysisResult:
    result = AnalysisResult(
        code="600519",
        name="贵州茅台",
        sentiment_score=43,
        trend_prediction="震荡",
        operation_advice="回避",
        analysis_summary="旧分析摘要（不得作为第一屏结论）",
        report_language="zh",
        dashboard={
            "core_conclusion": {
                "one_sentence": "旧核心结论（不得出现）",
                "time_sensitivity": "旧时效提示（不得出现）",
                "position_advice": {
                    "no_position": "不新增仓位；等待趋势和量价重新确认。",
                    "has_position": "按既有风险计划管理；不因本报告生成新的卖出指令。",
                },
            },
            "intelligence": {},
            "battle_plan": {},
            "research_delivery": {
                "schema_version": "research-delivery-v1",
                "selection_source": "SPECIFIED_CODES",
                "asset_type": "stock",
                "asset_identity": {"listing_market": "A股", "listing_board": "主板"},
                "asset_identity_text": "A股｜主板",
            },
            "factor_decision": {
                "conclusion": "旧因子结论（不得重复）",
                "composite_score": 43,
                "historical_reference": {"display": "样本不足，暂不展示"},
                "current_probability": {"display": "暂不提供（尚未完成独立校准）"},
                "investor_brief": {
                    "schema_version": "investor-brief-v1",
                    "coverage": {
                        "monthly": "MISSING",
                        "weekly": "MISSING",
                        "daily": "PROVEN_CURRENT",
                        "60m": "MISSING",
                        "30m": "MISSING",
                        "15m": "MISSING",
                        "5m": "MISSING",
                    },
                    "coverage_text": "本次可用周期：日线。月线、周线、60分钟、30分钟、15分钟和5分钟本次暂无可用证据。",
                    "canonical": {
                        "authority": "stock_trend_quality_pullback_v1",
                        "action": "PASS",
                        "public_action": "avoid",
                        "evidence_state": "PROVEN",
                        "hard_veto": True,
                        "reason_codes": ["WEAK_TREND"],
                    },
                    "one_line_conclusion": "当前偏弱，暂不追高，等待趋势重新转强。",
                    "fused_paragraph": "日线趋势仍偏弱，量价尚未确认重新转强；当前以等待确认为主。",
                    "timeframe_thesis": {
                        "monthly": {
                            "status": "MISSING",
                            "role": "LONG_TERM_CONTEXT",
                            "summary": None,
                        },
                        "weekly": {
                            "status": "MISSING",
                            "role": "PRIMARY_TREND_CONTEXT",
                            "summary": None,
                        },
                        "daily": {
                            "status": "PARTIAL_CURRENT",
                            "role": "PRIMARY_SETUP",
                            "summary": "趋势偏弱；量价尚未确认重新转强",
                        },
                        "60m": {
                            "status": "MISSING",
                            "role": "OPTIONAL_BRIDGE",
                            "summary": None,
                        },
                    },
                    "short_term_execution_panel": {
                        "status": "MISSING",
                        "state": "DATA_INSUFFICIENT",
                        "summary": None,
                    },
                    "current_price": {"value": 1450.0, "source_state": "PROVEN_CURRENT"},
                    "key_levels": {"support": 1400.0, "resistance": 1500.0},
                    "trigger": "重新站上 1500 且放量确认",
                    "invalidation": "跌破 1400 且放量",
                    "risk_notes": [
                        "弱趋势仍未修复",
                        "估值证据仍有限",
                        "第三条风险不应进入第一屏",
                    ],
                    "valuation": {
                        "bucket": "合理",
                        "summary": "PE/PB 仅作保守参考",
                        "uncertainty": "缺少历史分位",
                    },
                    "historical_reference": {
                        "available": False,
                        "reason": "尚未完成 PIT 同策略验证",
                    },
                },
            },
        },
    )


    factor = result.dashboard["factor_decision"]
    factor["strategy_id"] = "stock_trend_quality_pullback_v1"
    factor["canonical_decision"] = deepcopy(factor["investor_brief"]["canonical"])
    factor["evidence_traceability"] = build_runtime_trace(factor)
    factor["canonical_decision_identity"] = canonical_factor_binding(factor)
    factor["investor_brief"]["canonical_binding"] = deepcopy(
        factor["canonical_decision_identity"]
    )
    return result






def test_formal_report_email_is_required_and_telegram_is_optional():
    """Email is the required Product sink; legacy push must never substitute."""
    samples = (
        ("both", [NotificationChannel.WECHAT, NotificationChannel.TELEGRAM,
                   NotificationChannel.EMAIL, NotificationChannel.CUSTOM],
         [], True, True, "sent", True, ["email", "telegram"]),
        ("tg_failure", [NotificationChannel.TELEGRAM, NotificationChannel.EMAIL,
                         NotificationChannel.CUSTOM],
         [], True, False, "partial_failed", True, ["email", "telegram"]),
        ("email_failure", [NotificationChannel.TELEGRAM, NotificationChannel.EMAIL,
                            NotificationChannel.WECHAT],
         [], False, True, "partial_failed", False, ["email", "telegram"]),
        ("no_email", [NotificationChannel.WECHAT, NotificationChannel.TELEGRAM,
                       NotificationChannel.CUSTOM],
         [], True, True, "email_required", False, []),
        ("only_email", [NotificationChannel.EMAIL, NotificationChannel.WECHAT,
                         NotificationChannel.CUSTOM],
         ["custom"], True, True, "sent", True, ["email"]),
    )
    for name, installed, configured, email_ok, tg_ok, status, success, channels in samples:
        with mock.patch("src.notification.get_config", return_value=_make_config(
            notification_report_channels=configured,
        )):
            notifier = NotificationService()
        notifier._available_channels = installed
        notifier._markdown_to_image_channels = set()
        notifier.send_to_context = mock.MagicMock(return_value=True)
        notifier.should_broadcast_static_channels = mock.MagicMock(return_value=False)
        notifier.evaluate_noise_control = mock.MagicMock(
            return_value=SimpleNamespace(should_send=True, message="allowed")
        )
        notifier.record_noise_control = mock.MagicMock()
        notifier.release_noise_control = mock.MagicMock()
        notifier.send_to_email = mock.MagicMock(return_value=email_ok)
        notifier.send_to_telegram = mock.MagicMock(return_value=tg_ok)
        notifier.send_to_wechat = mock.MagicMock(return_value=True)
        notifier.send_to_custom = mock.MagicMock(return_value=True)
        receipt = notifier.send_with_results("SYNTHETIC_REPORT", route_type="report")
        assert receipt.status == status, name
        assert receipt.success is success, name
        assert [item.channel for item in receipt.channel_results] == channels, name
        notifier.send_to_context.assert_not_called()
        notifier.send_to_wechat.assert_not_called()
        notifier.send_to_custom.assert_not_called()
        assert notifier.send_to_email.call_count == int("email" in channels)
        assert notifier.send_to_telegram.call_count == int("telegram" in channels)


def test_canonical_notification_send_with_results_keeps_email_and_telegram_text_not_image() -> None:
    """Root consumer: a valid canonical report cannot be replaced by image-only content."""
    with mock.patch("src.notification.get_config", return_value=_make_config()):
        service = NotificationService()
    channels = [NotificationChannel.EMAIL, NotificationChannel.TELEGRAM]
    service._available_channels = channels
    service._markdown_to_image_channels = {"email", "telegram"}
    service._stock_email_groups = []
    service.get_channels_for_route = mock.MagicMock(return_value=channels)
    service.send_to_context = mock.MagicMock(return_value=False)
    service.should_broadcast_static_channels = mock.MagicMock(return_value=True)
    service.evaluate_noise_control = mock.MagicMock(return_value=SimpleNamespace(
        should_send=True, message=""
    ))
    service.record_noise_control = mock.MagicMock()
    service.release_noise_control = mock.MagicMock()
    service.require_current_asset_data_for_send = mock.MagicMock()
    service.send_to_email = mock.MagicMock(return_value=True)
    service.send_to_telegram = mock.MagicMock(return_value=True)
    service._send_email_with_inline_image = mock.MagicMock(return_value=True)
    service._send_telegram_photo = mock.MagicMock(return_value=True)
    material = "GLOBAL_UNKNOWN\\nSEVEN_TF\\nCOUNTEREVIDENCE\\nRISK_INVALIDATION\\nSOURCE_TIME"
    with mock.patch("src.md2img.markdown_to_image", return_value=b"png") as converter:
        delivery = service.send_with_results(
            material, route_type="report", asset_results=[_make_investor_brief_result()]
        )
    assert delivery.success
    service.send_to_email.assert_called_once_with(material, receivers=None)
    service.send_to_telegram.assert_called_once_with(material)
    service._send_email_with_inline_image.assert_not_called()
    service._send_telegram_photo.assert_not_called()
    converter.assert_not_called()
    # Deliberate legacy compression config must be rejected before any channel.
    service._report_summary_only = True
    service.send_to_email.reset_mock()
    service.send_to_telegram.reset_mock()
    with mock.patch("src.md2img.markdown_to_image", return_value=b"png") as second_converter:
        blocked = service.send_with_results(
            material, route_type="report", asset_results=[_make_investor_brief_result()]
        )
    assert blocked.status == "invalid_report"
    assert not blocked.success and not blocked.dispatched
    service.send_to_email.assert_not_called()
    service.send_to_telegram.assert_not_called()
    second_converter.assert_not_called()



def test_typed_completed_daily_5m_source_observations_survive_existing_full_email_leaf():
    """Typed positive source receipts are projected as PARTIAL, never as fully usable PIT."""
    from copy import deepcopy
    from html.parser import HTMLParser
    from src.notification import _append_complete_research_universe_appendix
    from src.formatters import markdown_to_html_document
    from src.services.evidence_traceability_registry import build_runtime_trace
    from tests.test_evidence_traceability_registry import _closed_typed_stock_source_factor

    class Reader(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []

        def handle_data(self, item):
            self.parts.append(item)

    factor, minute = _closed_typed_stock_source_factor()
    factor["evidence_traceability"] = build_runtime_trace(factor)
    lines = []
    _append_complete_research_universe_appendix(lines, factor, {"asset_type": "stock"})
    document = "\n".join(lines)
    reader = Reader()
    reader.feed(markdown_to_html_document(document))
    visible = "".join(reader.parts)
    for item in ("P_DAILY", "P_5M", "PARTIAL", "2026-10", "PIT", "权利"):
        assert item in document and item in visible
    assert "量额单位未知" in visible
    assert "不构成完整准入" in visible
    assert "已完整准入" not in visible
    assert "G_VIX" in visible and "UNKNOWN" in visible

    unbound = deepcopy(factor)
    del unbound["method_execution_receipts"]
    unbound["evidence_traceability"] = build_runtime_trace(unbound)
    blocked = []
    _append_complete_research_universe_appendix(blocked, unbound, {"asset_type": "stock"})
    assert "P_DAILY" in "\n".join(blocked)
    blocked_scope = [line for line in blocked if "P_DAILY" in line or "P_5M" in line]
    assert len(blocked_scope) == 2
    assert all("PARTIAL／" not in line for line in blocked_scope)


def test_b8_stale_current_risk_must_not_render_abbreviated_report_or_dispatch():
    """Current signed risk facts fail closed at ALL existing Product exits."""
    import pytest
    from src.enums import ReportType

    source = _make_investor_brief_result()
    with mock.patch("src.notification.get_config", return_value=_make_config(
        report_renderer_enabled=False, report_history_compare_n=0,
        report_summary_only=False,
    )):
        service = NotificationService()
        baseline = service.generate_aggregate_report(
            [source], ReportType.FULL, "2026-10-14"
        )
        assert len(baseline) > 5000
        assert "弱趋势仍未修复" in baseline

        for target in ("factor", "brief"):
            poisoned = deepcopy(source)
            if target == "factor":
                poisoned.dashboard["factor_decision"]["risk_notes"] = [
                    "FORGED_CURRENT_FACTOR_RISK"
                ]
            else:
                poisoned.dashboard["factor_decision"]["investor_brief"]["risk_notes"][0] = (
                    "FORGED_CURRENT_BRIEF_RISK"
                )
            for factory in (
                lambda: service.generate_aggregate_report(
                    [poisoned], ReportType.FULL, "2026-10-14"
                ),
                lambda: service.generate_daily_report([poisoned], "2026-10-14"),
                lambda: service.generate_brief_report([poisoned], "2026-10-14"),
                lambda: service.generate_single_stock_report(poisoned),
            ):
                with pytest.raises(ValueError, match="canonical|material"):
                    factory()

            # Source/available-at veto runs first; risk-material veto follows,
            # before any channel. No abbreviated report is an allowed fallback.
            service.send_to_context = mock.MagicMock(return_value=False)
            service.send_to_email = mock.MagicMock(return_value=True)
            service.send_to_telegram = mock.MagicMock(return_value=True)
            service.require_current_asset_data_for_send = mock.MagicMock()
            blocked = service.send_with_results(
                "FORGED_REPORT_MUST_NOT_SEND", route_type="report",
                asset_results=[poisoned],
            )
            assert blocked.status == "invalid_report"
            assert not blocked.success and not blocked.dispatched
            service.send_to_context.assert_not_called()
            service.send_to_email.assert_not_called()
            service.send_to_telegram.assert_not_called()
            service.require_current_asset_data_for_send.assert_called_once_with([poisoned])

        # No canonical object: historical descriptive-only report remains
        # readable without upgrading the old record to current authority.
        legacy = AnalysisResult(
            code="600519", name="historical-only", sentiment_score=50,
            trend_prediction="数据不足", operation_advice="观望",
            dashboard={}, report_language="zh",
        )
        historical = service.generate_dashboard_report([legacy], "2026-10-14")
        assert "600519" in historical


def test_b5_b8_seven_completed_timeframes_strict_mtf_to_canonical_full_report_html_no_send() -> None:
    """One producer invocation, 7 frames, 76 product obligations, no source-rights laundering."""
    from datetime import datetime, timezone
    from html.parser import HTMLParser

    import exchange_calendars as xcals
    import numpy as np
    import pandas as pd
    import pytest

    from data_provider.daily_data_identity import (
        attach_daily_data_identity, build_daily_data_identity,
    )
    from src.services.intraday_bar_service import (
        aggregate_completed_intraday_bars, validate_complete_cn_5m_sessions,
    )
    from src.services.multi_timeframe_structure_service import build_multi_timeframe_structure_context
    from src.services.pit_identity import build_completed_history_identity
    from src.services.evidence_traceability_registry import (
        TraceabilityError, build_method_execution_receipt, build_runtime_trace,
        validate_runtime_trace,
    )
    from src.services.factor_decision_summary import validate_canonical_factor_binding
    from src.stock_analyzer import StockTrendAnalyzer
    from src.enums import ReportType
    from src.formatters import markdown_to_html_document
    from tests.test_factor_decision_summary import _trend

    # All dates are independently verified XSHG sessions, unlike the legacy
    # 2026-09-25 holiday fixtures; no outside price feed or business database.
    target = date(2026, 10, 15)
    sessions = [d.date() for d in xcals.get_calendar("XSHG").sessions_in_range(
        "2023-01-01", target.isoformat(),
    )]
    recent = sessions[-10:]
    assert len(recent) == 10 and recent[-1] == target
    x = np.arange(len(sessions), dtype=float)
    closes = 90 + .065 * x + np.sin(x / 24) * 1.8
    daily = pd.DataFrame({
        "date": pd.to_datetime(sessions),
        "open": closes - .2, "high": closes + .6,
        "low": closes - .7, "close": closes,
        "volume": 100000 + 9 * x,
        "amount": closes * (100000 + 9 * x),
        "data_source": "BaostockFetcher",
    })
    observed = datetime(2026, 10, 16, 2, tzinfo=timezone.utc)
    source_identity = build_daily_data_identity(
        daily, provider_identity="BaostockFetcher",
        provider_route="fixture.offline.no_provider_call",
        actual_response_branch="SYNTHETIC_ONLY;adjustflag=2",
        requested_adjustment_basis="qfq", observed_adjustment_basis="qfq",
        basis_evidence="SYNTHETIC_TEST_ONLY",
        requested_start=sessions[0].isoformat(),
        requested_end=target.isoformat(),
        currency="CNY", volume_unit="share", amount_unit="CNY",
        identity_state="OBSERVED", observed_at=observed,
    )
    attach_daily_data_identity(daily, source_identity)
    # Match the actual BaoStock normalized adapter response: typed attrs
    # retain original content identity, but there is no row data_source label.
    daily = daily.drop(columns=["data_source"])
    assert "data_source" not in daily.columns
    assert "daily_data_identity" in daily.attrs
    five = _notification_canonical_5m(recent)
    assert validate_complete_cn_5m_sessions(
        five, expected_session_dates=recent,
    )["row_count"] == 480
    intraday = {"5m": five}
    for frequency in ("15m", "30m", "60m"):
        intraday[frequency] = aggregate_completed_intraday_bars(
            five, target_timeframe=frequency,
        )
    assert {k: len(v) for k, v in intraday.items()} == {
        "5m": 480, "15m": 160, "30m": 80, "60m": 40,
    }

    analyzer = StockTrendAnalyzer()
    mtf = build_multi_timeframe_structure_context(
        stock_code="600519", history=daily, target_date=target,
        market="cn", trend_analyzer=analyzer,
        daily_trend_result=analyzer.analyze(daily.copy(), "600519"),
        snapshot_observed_at=observed,
        intraday_timeframes=intraday,
    )
    assert set(mtf["timeframes"]) == {
        "monthly", "weekly", "daily", "60m", "30m", "15m", "5m",
    }
    input_identity = build_completed_history_identity(
        daily, stock_code="600519", market="cn",
        target_date=target, observed_at=observed,
    )
    input_identity.update(stock_code="600519", market="cn",
                          target_date=target.isoformat())
    receipt = build_method_execution_receipt(
        "MTF", output=mtf, asset_route="STOCK", stock_code="600519",
        market="cn", target_date=target, timeframe="multi",
        input_identity=input_identity,
    )
    factor = build_stock_factor_decision_summary(
        _trend(), include_canonical=True,
        multi_timeframe_structure_context=mtf,
        method_execution_receipts={"MTF": receipt},
    )
    trace = validate_runtime_trace(factor)
    validate_canonical_factor_binding(factor)
    mtf_observation = next(x for x in trace["observations"] if x["requirement_id"] == "MTF")
    assert (mtf_observation["state"], mtf_observation["reason"]) == (
        "READY", "METHOD_INVOCATION_VERIFIED",
    )
    assert factor["method_execution_receipt_policy"] == "REQUIRED"
    assert factor["canonical_decision"]["action"] == "WAIT"
    assert len(trace["timeframe_family_matrix"]["cells"]) == 56
    objects = {x["dataset_id"]: x for x in
               trace["complete_research_universe"]["data_objects"]}
    assert len(objects) == 76
    assert objects["P_DAILY"]["state"] == "PARTIAL"
    assert objects["P_5M"]["state"] == "PARTIAL"
    assert objects["G_VIX"]["state"] == "UNKNOWN"
    assert objects["P_FIN_VINTAGE"]["state"] == "UNKNOWN"
    assert objects["E_NAV_IOPV"]["state"] == "NOT_APPLICABLE"
    assert all(not objects[k]["strategy_upgrade_authority"]
               for k in ("P_DAILY", "P_5M", "G_VIX", "E_NAV_IOPV"))

    result = _make_investor_brief_result()
    result.dashboard["factor_decision"] = factor
    apply_canonical_decision_to_result(result, factor, scope="production")
    with mock.patch("src.notification.get_config", return_value=_make_config(
        report_renderer_enabled=False, report_history_compare_n=0,
    )):
        notifier = NotificationService()
        full = notifier.generate_aggregate_report(
            [result], ReportType.FULL, report_date=target.isoformat(),
        )
        mail = notifier.generate_brief_report([result], target.isoformat())
        single = notifier.generate_single_stock_report(result)
    class Visible(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []
        def handle_data(self, text):
            self.parts.append(text)
    view = Visible()
    view.feed(markdown_to_html_document(mail))
    visible = "".join(view.parts)
    assert len(full) > 5000 and len(mail) > 5000
    # A seven-timeframe headline or a 56-cell static matrix does not provide
    # actual MACD/RSI/MA evidence. Verify the current SAME producer's method
    # detail survives FULL, required Email, single and HTML visible text.
    for projection in (full, mail, single, visible):
        assert "七周期逐指标方法明细" in projection
        for label in ("月线", "周线", "日线", "60分钟", "30分钟", "15分钟", "5分钟"):
            assert label + "：已完成K线" in projection
        for method in ("MA5=", "MA10=", "MA20=", "MACD", "RSI",
                       "量价", "价格结构", "形态", "Chan", "独立交易"):
            assert method in projection, method
        assert "RSI：MISSING" in projection
        assert "尚无独立交易权限" in projection
        # Exact *native* owner values must be the display values; no second
        # MACD/RSI formula or LLM arithmetic in the report projection.
        for tf in ("monthly", "weekly", "daily", "60m", "30m", "15m", "5m"):
            row = mtf["timeframes"][tf]
            ma = row.get("ma_structure") or {}
            ma_readiness = (ma.get("readiness") or {}).get("level") or {}
            if ma_readiness.get("status") == "READY" and ma.get("status") in {"READY", "PARTIAL"}:
                for period in (5, 10, 20):
                    assert "MA%d=%.3f" % (period, ma["ma_values"]["ma%d" % period]) in projection
            macd = row.get("macd_startup_stability") or {}
            if macd.get("status") == "READY":
                endpoint = macd.get("native_endpoint_values") or {}
                assert "DIF=%.3f" % endpoint["MACD_DIF"] in projection
                assert "DEA=%.3f" % endpoint["MACD_DEA"] in projection
                assert "BAR=%.3f" % endpoint["MACD_BAR"] in projection
            rsi = row.get("rsi_startup_stability") or {}
            if rsi.get("status") == "READY":
                numbers = rsi.get("reference_values") or {}
                for period in (6, 12, 24):
                    assert "RSI%d=%.2f" % (
                        period, numbers["RSI_%d_latest" % period],
                    ) in projection
    for dataset_id in objects:
        assert chr(96) + dataset_id + chr(96) in full
        assert chr(96) + dataset_id + chr(96) in mail
        assert dataset_id in visible
    for required in ("P_DAILY", "P_5M", "G_VIX", "E_NAV_IOPV",
                     "P_FIN_VINTAGE", "UNKNOWN", "30m", "15m", "5m"):
        assert all(required in output for output in (full, mail, single, visible))
    assert "BUY" not in str(factor["canonical_decision"]["action"])
    assert all(mtf["timeframes"][tf].get("strategy_admitted") is False
               for tf in ("60m", "30m", "15m", "5m"))

    # A tampered 5m complete-session proof invalidates its MTF parent.
    # It may not leave P_DAILY / P_5M PARTIAL, nor preserve downstream READY.
    broken = deepcopy(factor)
    broken["multi_timeframe_structure_context"]["timeframes"]["5m"][
        "completed_source_session_receipt"
    ]["session_count"] = 9
    poisoned = build_runtime_trace(broken)
    bad_objects = {x["dataset_id"]: x for x in
                   poisoned["complete_research_universe"]["data_objects"]}
    assert bad_objects["P_DAILY"]["state"] == "UNKNOWN"
    assert bad_objects["P_5M"]["state"] == "UNKNOWN"
    with pytest.raises(TraceabilityError):
        validate_runtime_trace(broken)

    # Same graph, different ETF route: stock source can't forge ETF holdings
    # or company-only financials into ETF facts.
    etf = deepcopy(factor)
    etf["asset_type"] = "ETF"
    etf_objects = {x["dataset_id"]: x for x in
                   build_runtime_trace(etf)["complete_research_universe"]["data_objects"]}
    assert etf_objects["E_NAV_IOPV"]["state"] == "UNKNOWN"
    assert etf_objects["P_FIN_VINTAGE"]["state"] == "NOT_APPLICABLE"
    assert etf_objects["G_VIX"]["state"] == "UNKNOWN"


def test_b8a_accepted_76_data_objects_survive_runtime_full_report_email_html():
    """Original 76 dataset obligations cannot be replaced by 26 runtime rollups."""
    from src.enums import ReportType
    from src.formatters import markdown_to_html_document
    from src.services.evidence_traceability_registry import validate_runtime_trace

    accepted = {
        "GLOBAL": "G_EQ_US G_EQ_ASIA G_VIX G_VIX3M_MOVE G_UST_NOMINAL G_UST_REAL G_INFL_BREAKEVEN G_USD_BROAD G_USDCNH G_A50 G_CRUDE G_GOLD G_COPPER G_HYOAS G_NFCI G_US_POLICY G_US_SENTIMENT G_US_DISCLOSURE",
        "CN_MARKET": "M_INDEX_CURRENT M_INDEX_BARS M_BASIC_BREADTH M_UP_ABOVE_MA M_NEW_HILO M_MEDIAN_EQUAL M_LIMIT M_BROKEN_LIMIT M_TURNOVER M_MARGIN M_ETF_SHARES M_NORTHBOUND M_PBOC_MONEY M_CPI_PPI_PMI M_INTERBANK M_CN_BOND M_ERP_VAL M_ASOF_UNIVERSE",
        "SECTOR": "S_CLASSIFICATION S_PRICES_RS S_BREADTH S_TURNOVER S_FUND_FLOW S_LEADER S_VALUATION",
        "ETF": "E_PRICE_1D E_PRICE_5M E_NAV_IOPV E_SHARES E_PREMIUM E_SPREAD E_TRACKING E_BASKET E_PEER_RS E_UNDERLYING_VAL E_FX_CROSS E_FEES",
        "STOCK": "P_DAILY P_5M P_PRICE_FACTOR P_CODE_IDENTITY P_QUOTE_LIQUID P_FIN_VINTAGE P_EARNINGS_GROWTH P_CASH_FLOW P_BALANCE_SHEET P_SHARES_EVENTS P_DIVIDEND P_INDUSTRY P_VAL_RATIO P_ORDERFLOW_PROXY P_COST_PROXY P_EVENT_NEWS P_EXEC_RISK",
        "RECORD": "L_LEDGER L_OUTCOME L_PIT_MANIFEST L_IMMUTABLE_BYTES",
    }
    assert {key: len(items.split()) for key, items in accepted.items()} == {
        "GLOBAL": 18, "CN_MARKET": 18, "SECTOR": 7, "ETF": 12,
        "STOCK": 17, "RECORD": 4,
    }
    expected_ids = {item for items in accepted.values() for item in items.split()}
    assert len(expected_ids) == 76
    result = _make_investor_brief_result()
    with mock.patch("src.notification.get_config", return_value=_make_config(
        report_renderer_enabled=False, report_history_compare_n=0,
    )):
        service = NotificationService()
        full = service.generate_aggregate_report([result], ReportType.SIMPLE, "2026-10-12")
        mail = service.generate_brief_report([result], "2026-10-12")
    html = markdown_to_html_document(mail)
    universe = validate_runtime_trace(result.dashboard["factor_decision"])["complete_research_universe"]
    objects = universe.get("data_objects") or []
    assert len(objects) == 76, "76 accepted dataset identities are missing from runtime"
    assert {item["dataset_id"] for item in objects} == expected_ids
    by_id = {item["dataset_id"]: item for item in objects}
    assert by_id["G_VIX"]["state"] != "READY"
    assert by_id["E_NAV_IOPV"]["state"] == "NOT_APPLICABLE"
    assert by_id["P_FIN_VINTAGE"]["state"] != "READY"
    for dataset_id in expected_ids:
        tag = chr(96) + dataset_id + chr(96)
        assert tag in full, dataset_id
        assert tag in mail, dataset_id
        assert "<code>" + dataset_id + "</code>" in html, dataset_id
    assert "VIX" in mail and "ETF" in mail and "财报" in mail
    # Verify each full material row, not merely the existence of 76 labels.
    # HTML is checked through its visible text; markup shape is not authority.
    from html.parser import HTMLParser

    class VisibleHTML(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)

    reader = VisibleHTML()
    reader.feed(html)
    visible = "".join(reader.parts)
    for row in objects:
        state_text = (
            "当前资产路径不适用" if row["state"] == "NOT_APPLICABLE"
            else "UNKNOWN／缺独立来源、单位、时点及权利回执"
        )
        literal_fields = (
            " ｜" + row["fact_or_fields"]
            + " ｜原生频率：" + row["native_frequency"]
            + " ｜消费者：" + row["product_consumer"]
            + " ｜状态：" + state_text
        )
        markdown_fields = (
            " ｜" + row["fact_or_fields"]
            + " ｜原生频率：" + chr(96) + row["native_frequency"] + chr(96)
            + " ｜消费者：" + chr(96) + row["product_consumer"] + chr(96)
            + " ｜状态：" + state_text
        )
        assert chr(96) + row["dataset_id"] + chr(96) + markdown_fields in full
        assert chr(96) + row["dataset_id"] + chr(96) + markdown_fields in mail
        assert row["dataset_id"] + literal_fields in visible





def test_b8a_dataset_catalog_tamper_and_forged_runtime_ready_are_rejected():
    """Source list and numeric readiness cannot be silently rewritten by output."""
    import pytest
    import src.services.evidence_traceability_registry as registry

    factor = _make_investor_brief_result().dashboard["factor_decision"]
    original_catalog = registry._DATA_OBJECT_CATALOG
    with mock.patch.object(registry, "_DATA_OBJECT_CATALOG", original_catalog[:-1]):
        with pytest.raises(
            registry.TraceabilityError,
            match="ACCEPTED_DATA_OBJECT_CATALOG_MISMATCH",
        ):
            registry.build_runtime_trace(factor)

    # Re-signing a presentation object without a typed source receipt must
    # not turn a real-data UNKNOWN into an admissible VIX observation.
    forged = deepcopy(factor)
    trace = forged["evidence_traceability"]
    universe = trace["complete_research_universe"]
    vix = next(x for x in universe["data_objects"] if x["dataset_id"] == "G_VIX")
    vix["state"] = "READY"
    universe["universe_hash"] = registry.digest({
        k: v for k, v in universe.items() if k != "universe_hash"
    })
    trace["complete_research_universe_hash"] = universe["universe_hash"]
    trace["runtime_trace_hash"] = registry.digest({
        k: v for k, v in trace.items() if k != "runtime_trace_hash"
    })
    with pytest.raises(
        registry.TraceabilityError, match="RUNTIME_TRACE_MISSING_OR_STALE"
    ):
        registry.validate_runtime_trace(forged)

    # Nontrigger: on this STOCK route, specific ETF-only data is N/A;
    # the independent stock source obligations remain UNKNOWN (not zero).
    accepted = registry.validate_runtime_trace(factor)
    data_objects = accepted["complete_research_universe"]["data_objects"]
    by_id = {item["dataset_id"]: item for item in data_objects}
    assert by_id["E_NAV_IOPV"]["state"] == "NOT_APPLICABLE"
    assert by_id["P_FIN_VINTAGE"]["state"] == "UNKNOWN"
    assert by_id["G_VIX"]["state"] == "UNKNOWN"
    assert all(item["independent_source_value_admitted"] is False for item in data_objects)


def test_b8a_notification_refuses_forged_dataset_id_even_with_same_76_count():
    """The actual appendix must not render a plausible but forged 76-item list."""
    import pytest
    from src.notification import _append_complete_research_universe_appendix

    factor = _make_investor_brief_result().dashboard["factor_decision"]
    poisoned = deepcopy(factor)
    objects = poisoned["evidence_traceability"]["complete_research_universe"]["data_objects"]
    assert len(objects) == 76
    objects[2]["dataset_id"] = "G_FAKE_VIX_CLAIM"
    lines = []
    with pytest.raises(ValueError, match="ACCEPTED_DATA_OBJECT_SCOPE"):
        _append_complete_research_universe_appendix(
            lines, poisoned, poisoned["investor_brief"],
        )
    assert not any("G_FAKE_VIX_CLAIM" in line for line in lines)


def test_b8a_distinct_producer_factor_material_survives_report_email_html_and_single():
    """Independent synthetic facts are retained by every existing projection."""
    from html.parser import HTMLParser
    from src.enums import ReportType
    from src.formatters import markdown_to_html_document
    from src.services.factor_decision_summary import canonical_factor_binding

    result = _make_investor_brief_result()
    factor = result.dashboard["factor_decision"]
    material = {
        "why_a": "DISTINCT_VOLUME_OPPOSITION_A4F9",
        "why_b": "DISTINCT_VIX_MISSING_B7E2",
        "condition": "DISTINCT_EXECUTION_TRIGGER_C914",
        "invalid": "DISTINCT_INVALIDATION_D213",
        "valuation": "DISTINCT_FINANCIAL_VERSION_F2A8",
        "cost": "DISTINCT_COST_PROXY_E8F5",
        "risk": "DISTINCT_LIQUIDITY_VETO_G1C4",
    }
    factor["why"] = [material["why_a"], material["why_b"]]
    factor["action_condition"] = material["condition"]
    factor["invalidation_condition"] = material["invalid"]
    factor["valuation"] = material["valuation"]
    factor["cost_structure"] = material["cost"]
    factor["risk_notes"] = [material["risk"]]
    factor["evidence_traceability"] = build_runtime_trace(factor)
    factor["canonical_decision_identity"] = canonical_factor_binding(factor)
    factor["investor_brief"]["canonical_binding"] = deepcopy(factor["canonical_decision_identity"])
    with mock.patch("src.notification.get_config", return_value=_make_config(
        report_renderer_enabled=False, report_history_compare_n=0,
    )):
        service = NotificationService()
        full = service.generate_aggregate_report([result], ReportType.SIMPLE, "2026-10-12")
        email = service.generate_brief_report([result], "2026-10-12")
        single = service.generate_single_stock_report(result)

    class VisibleText(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []
        def handle_data(self, data):
            self.parts.append(data)

    visible = VisibleText()
    visible.feed(markdown_to_html_document(email))
    rendered = "".join(visible.parts)
    for text in material.values():
        assert text in full, text
        assert text in email, text
        assert text in single, text
        assert text in rendered, text
    assert "UNKNOWN／缺独立来源、单位、时点及权利回执" in email

    # Nontrigger: a material string already in the brief is not appended again.
    factor["action_condition"] = factor["investor_brief"]["trigger"]
    factor["evidence_traceability"] = build_runtime_trace(factor)
    factor["canonical_decision_identity"] = canonical_factor_binding(factor)
    factor["investor_brief"]["canonical_binding"] = deepcopy(factor["canonical_decision_identity"])
    with mock.patch("src.notification.get_config", return_value=_make_config(
        report_renderer_enabled=False, report_history_compare_n=0,
    )):
        once = NotificationService().generate_brief_report([result], "2026-10-12")
    assert once.count(factor["investor_brief"]["trigger"]) == 1



def test_b8a_fused_canonical_facts_preserve_literal_state_ids_in_html():
    """Source-owned technical IDs must survive actual FULL-to-HTML rendering."""
    from html.parser import HTMLParser
    from src.enums import ReportType
    from src.formatters import markdown_to_html_document
    from src.services.factor_decision_summary import (
        canonical_factor_binding,
        validate_canonical_factor_binding,
    )

    atoms = (
        "支持：当前仅有已完成bar研究证据；S_UP38",
        "反证：上下周期冲突，不能独立计票；C_RT51",
        "数据时点：2026-10-14完成，2026-10-15观察；T_CU72",
        "缺口：分钟量额单位和使用权未知；U_QK94",
        "结构状态：FAILED_UP_BREAKOUT；失效：COMPLETED_CLOSE_BELOW_BROKEN_RESISTANCE",
    )
    result = _make_investor_brief_result()
    factor = result.dashboard["factor_decision"]
    factor["investor_brief"]["fused_paragraph"] = "；".join(atoms)
    factor["evidence_traceability"] = build_runtime_trace(factor)
    factor["canonical_decision_identity"] = canonical_factor_binding(factor)
    factor["investor_brief"]["canonical_binding"] = deepcopy(
        factor["canonical_decision_identity"]
    )
    validate_canonical_factor_binding(factor)
    with mock.patch(
        "src.notification.get_config",
        return_value=_make_config(report_renderer_enabled=False, report_history_compare_n=0),
    ):
        markdown = NotificationService().generate_aggregate_report(
            [result], ReportType.FULL, "2026-10-14"
        )

    class VisibleHTML(HTMLParser):
        def __init__(self):
            super().__init__(convert_charrefs=True)
            self.parts = []
        def handle_data(self, text):
            self.parts.append(text)

    parser = VisibleHTML()
    parser.feed(markdown_to_html_document(markdown))
    visible = "".join(parser.parts)
    for atom in atoms:
        assert atom in markdown, atom
        assert atom in visible, atom
    assert factor["canonical_decision"]["action"] == "PASS"
    assert factor["canonical_decision"]["hard_veto"] is True
    assert "<em>斜体</em>" in markdown_to_html_document("合法 *斜体* 路径")
    assert "<code>MA_COMPRESSION_RELEASE</code>" in markdown_to_html_document(
        "已引用 " + chr(96) + "MA_COMPRESSION_RELEASE" + chr(96)
    )


def test_b8a_etf_legitimate_route_keeps_stock_object_not_applicable():
    """Asset-specific data objects must retain asset route despite global context."""
    from src.services.evidence_traceability_registry import build_runtime_trace

    factor = deepcopy(_make_investor_brief_result().dashboard["factor_decision"])
    factor["asset_type"] = "ETF"
    universe = build_runtime_trace(factor)["complete_research_universe"]
    objects = {obj["dataset_id"]: obj for obj in universe["data_objects"]}
    assert len(objects) == 76
    assert objects["E_NAV_IOPV"]["state"] == "UNKNOWN"
    assert objects["P_FIN_VINTAGE"]["state"] == "NOT_APPLICABLE"
    assert objects["G_VIX"]["state"] == "UNKNOWN"
    assert all(not obj["strategy_upgrade_authority"] for obj in objects.values())


def _make_canonical_wait_contaminated_result() -> AnalysisResult:
    result = _make_investor_brief_result()
    factor = result.dashboard["factor_decision"]
    decision = {
        "authority": "stock_trend_quality_pullback_v1",
        "action": "WAIT",
        "public_action": "watch",
        "evidence_state": "PROVEN",
        "hard_veto": False,
        "reason_codes": ["WAIT_CONFIRMATION"],
    }
    factor["canonical_decision"] = deepcopy(decision)
    factor["conclusion"] = "CANONICAL_WAIT_CONCLUSION"
    factor["action_condition"] = "CANONICAL_TRIGGER"
    factor["invalidation_condition"] = "CANONICAL_INVALIDATION"
    factor["investor_brief"]["canonical"] = deepcopy(decision)
    factor["investor_brief"]["one_line_conclusion"] = "CANONICAL_WAIT_CONCLUSION"
    factor["investor_brief"]["fused_paragraph"] = "CANONICAL_FUSED_FACTS"
    factor["investor_brief"]["risk_notes"] = ["CANONICAL_RISK"]
    factor["evidence_traceability"] = build_runtime_trace(factor)
    factor["canonical_decision_identity"] = canonical_factor_binding(factor)
    factor["investor_brief"]["canonical_binding"] = deepcopy(
        factor["canonical_decision_identity"]
    )
    apply_canonical_decision_to_result(result, factor, scope="research")

    # Deliberate post-canonical legacy contamination.
    result.sentiment_score = 99
    result.trend_prediction = "LEGACY_STRONG_BULLISH"
    result.operation_advice = "LEGACY_BUY_NOW"
    result.analysis_summary = "LEGACY_ANALYSIS_SUMMARY_MUST_NOT_RENDER"
    result.buy_reason = "LEGACY_BUY_REASON_MUST_NOT_RENDER"
    result.trend_analysis = "LEGACY_TREND_ANALYSIS_MUST_NOT_RENDER"
    return result
def _make_builder_risk_factor(risks):
    trend = SimpleNamespace(
        signal_score=82,
        trend_status=SimpleNamespace(value="多头排列"),
        buy_signal=SimpleNamespace(value="买入"),
        ma_alignment="MA5 > MA10 > MA20",
        trend_strength=78,
        current_price=10.5,
        support_levels=[10.0],
        resistance_levels=[11.0],
        volume_status=SimpleNamespace(value="缩量回调"),
        volume_ratio_5d=0.62,
        volume_trend="缩量回调，卖压收缩；是否属于洗盘需后续确认",
        macd_signal="MACD多头结构",
        rsi_signal="RSI中性偏强",
        risk_factors=list(risks),
    )
    return build_stock_factor_decision_summary(trend, include_canonical=True)

def _make_feishu_message() -> BotMessage:
    return BotMessage(
        platform="feishu",
        message_id="msg-1",
        user_id="user-1",
        user_name="tester",
        chat_id="chat-1",
        chat_type=ChatType.GROUP,
        content="/a 600519",
    )


def _make_dingtalk_message() -> BotMessage:
    return BotMessage(
        platform="dingtalk",
        message_id="msg-2",
        user_id="user-2",
        user_name="tester",
        chat_id="dingtalk-chat",
        chat_type=ChatType.GROUP,
        content="/a 600519",
        raw_data={
            "sessionWebhook": "https://oapi.dingtalk.com/robot/sendBySession?session=abc123",
        },
    )


def _make_telegram_message() -> BotMessage:
    return BotMessage(
        platform="telegram",
        message_id="msg-3",
        user_id="user-3",
        user_name="tester",
        chat_id="100200300",
        chat_type=ChatType.PRIVATE,
        content="/a 600519",
        raw_data={"chat_id": "100200300"},
    )


class TestNotificationServiceSendToMethods(unittest.TestCase):
    """测试通知发送服务

    测试设计：

    测试按照渠道的字母顺序排列，在合适位置添加新的测试方法。
    如果采用长消息分批发送，必须单独测试分批发送的逻辑，
        e.g. test_send_to_discord_via_notification_service_with_bot_requires_chunking

    1. 添加模拟配置：
    使用 mock.patch 装饰器来模拟 get_config 函数，
    使用 _make_config 函数添加配置，并返回 Config 实例。

    2. 检查配置是否正确：
    使用 assertIn 检查 NotificationChannel.xxxx 是否在
    `NotificationService.get_available_channels()` 返回值中。

    3. 模拟请求响应：
    使用 mock.patch 装饰器来模拟 requests.post 函数，
    使用 _make_response 函数模拟请求响应，并返回 Response 实例。
    若使用其他函数模拟请求响应，则使用 mock.patch 装饰器来模拟该函数。

    4. 使用 assertTrue 检查 send 的返回值。

    5. 使用 assert_called_once 检查请求函数是否被调用一次。
    测试分批发送时，使用 assertAlmostEqual(mock_post.call_count, ...) 检查请求函数被调用次数

    """

    def setUp(self):
        reset_notification_noise_state()

    @mock.patch("src.notification.get_config")
    def test_no_channels_service_unavailable_and_send_returns_false(self, mock_get_config):
        mock_get_config.return_value = _make_config()

        service = NotificationService()

        self.assertFalse(service.is_available())
        result = service.send("test content")
        self.assertFalse(result)

    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_astrbot_via_notification_service(self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock):
        cfg = _make_config(astrbot_url="https://astrbot.example")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200)

        service = NotificationService()
        self.assertIn(NotificationChannel.ASTRBOT, service.get_available_channels())

        ok = service.send("astrbot content")

        self.assertTrue(ok)
        mock_post.assert_called_once()

    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_custom_webhook_via_notification_service(
        self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock
    ):
        cfg = _make_config(custom_webhook_urls=["https://example.com/webhook"])
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200)

        service = NotificationService()
        self.assertIn(NotificationChannel.CUSTOM, service.get_available_channels())

        ok = service.send("custom content")

        self.assertTrue(ok)
        mock_post.assert_called_once()

    @mock.patch("src.notification.get_config")
    def test_send_isolates_channel_exceptions(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            wechat_webhook_url="https://wechat.example/hook",
            custom_webhook_urls=["https://example.com/webhook"],
        )
        mock_get_config.return_value = cfg

        service = NotificationService()
        self.assertIn(NotificationChannel.WECHAT, service.get_available_channels())
        self.assertIn(NotificationChannel.CUSTOM, service.get_available_channels())

        with mock.patch.object(service, "send_to_wechat", side_effect=RuntimeError("boom")), \
             mock.patch.object(service, "send_to_custom", return_value=True) as mock_custom:
            ok = service.send("content")

        self.assertTrue(ok)
        mock_custom.assert_called_once_with("content")

    @mock.patch("src.notification.get_config")
    def test_send_with_results_reports_per_channel_attempts(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            wechat_webhook_url="https://wechat.example/hook",
            custom_webhook_urls=["https://example.com/webhook"],
        )
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_wechat", side_effect=RuntimeError("token=secret-token failed")), \
             mock.patch.object(service, "send_to_custom", return_value=True):
            result = service.send_with_results("content")

        self.assertTrue(result.dispatched)
        self.assertTrue(result.success)
        self.assertEqual(result.status, "partial_failed")
        by_channel = {item.channel: item for item in result.channel_results}
        self.assertFalse(by_channel["wechat"].success)
        self.assertEqual(by_channel["wechat"].error_code, "exception")
        self.assertNotIn("secret-token", by_channel["wechat"].diagnostics)
        self.assertTrue(by_channel["custom"].success)

    @mock.patch("src.notification.get_config")
    def test_send_with_results_reports_route_no_channel(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            custom_webhook_urls=["https://example.com/webhook"],
            notification_report_channels=["unknown-route-channel"],
        )
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_custom", return_value=True) as mock_custom:
            result = service.send_with_results("content", route_type="report")

        self.assertFalse(result.dispatched)
        self.assertFalse(result.success)
        self.assertEqual(result.status, "email_required")
        self.assertEqual(result.channel_results, [])
        mock_custom.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_send_alert_route_empty_keeps_all_configured_channels(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            wechat_webhook_url="https://wechat.example/hook",
            custom_webhook_urls=["https://example.com/webhook"],
        )
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_wechat", return_value=True) as mock_wechat, \
             mock.patch.object(service, "send_to_custom", return_value=True) as mock_custom:
            ok = service.send("content", route_type="alert")

        self.assertTrue(ok)
        mock_wechat.assert_called_once_with("content")
        mock_custom.assert_called_once_with("content")

    @mock.patch("src.notification.get_config")
    def test_send_report_route_filters_static_channels(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            wechat_webhook_url="https://wechat.example/hook",
            custom_webhook_urls=["https://example.com/webhook"],
            email_sender="user@qq.com",
            email_password="PASS",
            email_receivers=["default@example.com"],
            notification_report_channels=["custom"],
        )
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_wechat", return_value=True) as mock_wechat, \
             mock.patch.object(service, "send_to_custom", return_value=True) as mock_custom, \
             mock.patch.object(service, "send_to_email", return_value=True) as mock_email:
            ok = service.send("content", route_type="report")

        self.assertTrue(ok)
        mock_wechat.assert_not_called()
        mock_custom.assert_not_called()
        mock_email.assert_called_once_with("content", receivers=None)

    @mock.patch("src.notification.get_config")
    def test_send_alert_and_system_error_routes_filter_independently(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            wechat_webhook_url="https://wechat.example/hook",
            custom_webhook_urls=["https://example.com/webhook"],
            notification_alert_channels=["wechat"],
            notification_system_error_channels=["custom"],
        )
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_wechat", return_value=True) as mock_wechat, \
             mock.patch.object(service, "send_to_custom", return_value=True) as mock_custom:
            self.assertTrue(service.send("alert", route_type="alert"))
            self.assertTrue(service.send("system", route_type="system_error"))

        mock_wechat.assert_called_once_with("alert")
        mock_custom.assert_called_once_with("system")

    @mock.patch("src.notification.get_config")
    def test_send_route_with_no_matching_channel_does_not_fallback(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            custom_webhook_urls=["https://example.com/webhook"],
            notification_report_channels=["unknown-route-channel"],
        )
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_custom", return_value=True) as mock_custom:
            ok = service.send("content", route_type="report")

        self.assertFalse(ok)
        mock_custom.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_send_to_context_is_not_limited_by_route(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            custom_webhook_urls=["https://example.com/webhook"],
            notification_report_channels=["telegram"],
        )
        mock_get_config.return_value = cfg

        service = NotificationService(source_message=_make_dingtalk_message())

        with mock.patch.object(service, "send_to_context", return_value=True) as mock_context, \
             mock.patch.object(service, "send_to_custom", return_value=True) as mock_custom:
            ok = service.send("content", route_type="alert")

        self.assertTrue(ok)
        mock_context.assert_called_once_with("content")
        mock_custom.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_feishu_context_response_strips_hidden_metadata_and_skips_static_webhook(
        self,
        mock_get_config: mock.MagicMock,
    ):
        cfg = _make_config(
            feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test-token",
            feishu_app_id="cli_test",
            feishu_app_secret="app-secret",
        )
        mock_get_config.return_value = cfg
        service = NotificationService(source_message=_make_feishu_message())
        content = "[dsa-market-region]: # (cn)\n\n# 市场复盘\n\n正文"

        with mock.patch.object(service, "_send_feishu_stream_reply", return_value=True) as mock_reply, \
             mock.patch.object(service, "send_to_feishu", return_value=True) as mock_webhook:
            result = service.send_with_results(content, route_type="alert")

        self.assertTrue(result.dispatched)
        self.assertTrue(result.success)
        self.assertEqual(result.status, "sent")
        self.assertEqual([item.channel for item in result.channel_results], ["__context__"])
        mock_reply.assert_called_once_with("chat-1", "# 市场复盘\n\n正文")
        mock_webhook.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_send_feishu_stream_reply_strips_hidden_metadata_before_chunking(
        self,
        mock_get_config: mock.MagicMock,
    ):
        cfg = _make_config(
            feishu_app_id="cli_test",
            feishu_app_secret="app-secret",
            feishu_max_bytes=10,
        )
        mock_get_config.return_value = cfg
        service = NotificationService()
        reply_client = mock.MagicMock()
        content = "[dsa-market-region]: # (cn)\n\n# 市场复盘\n\n正文"

        with mock.patch("bot.platforms.feishu_stream.FEISHU_SDK_AVAILABLE", True), \
             mock.patch("src.config.get_config", return_value=cfg), \
             mock.patch("bot.platforms.feishu_stream.FeishuReplyClient", return_value=reply_client), \
             mock.patch.object(service, "_send_feishu_stream_chunked", return_value=True) as mock_chunked:
            result = service._send_feishu_stream_reply("chat-1", content)

        self.assertTrue(result)
        mock_chunked.assert_called_once_with(reply_client, "chat-1", "# 市场复盘\n\n正文", 10)
        reply_client.send_to_chat.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_feishu_context_failure_does_not_fallback_to_static_webhook(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test-token",
            feishu_app_id="cli_test",
            feishu_app_secret="app-secret",
        )
        mock_get_config.return_value = cfg
        service = NotificationService(source_message=_make_feishu_message())

        with mock.patch.object(service, "_send_feishu_stream_reply", return_value=False), \
             mock.patch.object(service, "send_to_feishu", return_value=True) as mock_webhook:
            result = service.send_with_results("content", route_type="alert")

        self.assertTrue(result.dispatched)
        self.assertFalse(result.success)
        self.assertEqual(result.status, "all_failed")
        self.assertEqual([item.channel for item in result.channel_results], ["__context__"])
        mock_webhook.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_dingtalk_context_response_skips_static_webhook(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test-token",
            dingtalk_app_key="dingtalk-key",
            dingtalk_app_secret="dingtalk-secret",
            wechat_webhook_url="https://wechat.example/hook",
        )
        mock_get_config.return_value = cfg
        service = NotificationService(source_message=_make_dingtalk_message())

        with mock.patch.object(service, "_send_dingtalk_chunked", return_value=True) as mock_dingtalk, \
             mock.patch.object(service, "send_to_wechat", return_value=True) as mock_wechat:
            result = service.send_with_results("content", route_type="alert")

        self.assertTrue(result.dispatched)
        self.assertTrue(result.success)
        self.assertEqual([item.channel for item in result.channel_results], ["__context__"])
        mock_dingtalk.assert_called_once_with("https://oapi.dingtalk.com/robot/sendBySession?session=abc123", "content", max_bytes=20000)
        mock_wechat.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_telegram_context_response_skips_static_webhook(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            telegram_bot_token="TOKEN",
            telegram_chat_id="123456",
            wechat_webhook_url="https://wechat.example/hook",
        )
        mock_get_config.return_value = cfg
        service = NotificationService(source_message=_make_telegram_message())

        with mock.patch.object(service, "send_to_telegram", return_value=True) as mock_telegram, \
             mock.patch.object(service, "send_to_wechat", return_value=True) as mock_wechat:
            result = service.send_with_results("content", route_type="alert")

        self.assertTrue(result.dispatched)
        self.assertTrue(result.success)
        self.assertEqual([item.channel for item in result.channel_results], ["__context__"])
        mock_telegram.assert_called_once_with("content", chat_id="100200300")
        mock_wechat.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_feishu_webhook_still_sends_without_source_context(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            feishu_webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test-token",
            feishu_app_id="cli_test",
            feishu_app_secret="app-secret",
        )
        mock_get_config.return_value = cfg
        service = NotificationService()

        with mock.patch.object(service, "send_to_feishu", return_value=True) as mock_webhook:
            result = service.send_with_results("content", route_type="alert")

        self.assertTrue(result.dispatched)
        self.assertTrue(result.success)
        self.assertEqual([item.channel for item in result.channel_results], ["feishu"])
        mock_webhook.assert_called_once_with("content")

    @mock.patch("src.notification.get_config")
    def test_formal_report_never_uses_feishu_file_without_required_email(self, mock_get_config):
        """The accepted report contract blocks legacy Feishu-file-only delivery."""
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            feishu_send_as_file=True,
        )
        mock_get_config.return_value = cfg
        service = NotificationService()
        with mock.patch.object(service, "send_feishu_file", return_value=True) as mock_file, \
             mock.patch.object(service, "save_report_to_file", return_value="/tmp/report.md") as mock_save:
            result = service.send_with_results("report content", route_type="report")
        self.assertFalse(result.success)
        self.assertEqual(result.status, "email_required")
        mock_file.assert_not_called()
        mock_save.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_formal_report_blocks_feishu_file_before_hidden_metadata_save(self, mock_get_config):
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            feishu_send_as_file=True,
        )
        mock_get_config.return_value = cfg
        service = NotificationService()
        content = "[dsa-market-region]: # (cn)\n\n# 市场复盘\n\n正文"

        with mock.patch.object(service, "send_feishu_file", return_value=True), \
             mock.patch.object(service, "save_report_to_file", return_value="/tmp/report.md") as mock_save:
            result = service.send_with_results(content, route_type="report")

        self.assertFalse(result.success)
        self.assertEqual(result.status, "email_required")
        mock_save.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_feishu_send_as_file_route_alert_calls_send_to_feishu(self, mock_get_config):
        """When FEISHU_SEND_AS_FILE=true but route_type=alert, use text sending."""
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            feishu_send_as_file=True,
        )
        mock_get_config.return_value = cfg
        service = NotificationService()
        with mock.patch.object(service, "send_to_feishu", return_value=True) as mock_text, \
             mock.patch.object(service, "save_report_to_file") as mock_save:
            result = service.send_with_results("alert content", route_type="alert")
        self.assertTrue(result.success)
        mock_text.assert_called_once_with("alert content")
        mock_save.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_feishu_send_as_file_route_none_uses_text(self, mock_get_config):
        """When FEISHU_SEND_AS_FILE=true and route_type=None, use text (not file)."""
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            feishu_send_as_file=True,
        )
        mock_get_config.return_value = cfg
        service = NotificationService()
        with mock.patch.object(service, "send_to_feishu", return_value=True) as mock_text, \
             mock.patch.object(service, "save_report_to_file") as mock_save:
            result = service.send_with_results("report content")
        self.assertTrue(result.success)
        mock_text.assert_called_once_with("report content")
        mock_save.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_formal_report_never_uses_feishu_text_without_required_email(self, mock_get_config):
        """Disabling file mode does not make Feishu a formal report sink."""
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            feishu_send_as_file=False,
        )
        mock_get_config.return_value = cfg
        service = NotificationService()
        with mock.patch.object(service, "send_to_feishu", return_value=True) as mock_text, \
             mock.patch.object(service, "save_report_to_file") as mock_save:
            result = service.send_with_results("report content", route_type="report")
        self.assertFalse(result.success)
        self.assertEqual(result.status, "email_required")
        mock_text.assert_not_called()
        mock_save.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_feishu_send_as_file_alert_uses_text_and_does_not_leak_into_other_channels(self, mock_get_config):
        """File mode does not affect alerts; all alert channels remain text."""
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            custom_webhook_urls=["https://example.com/hook"],
            feishu_send_as_file=True,
        )
        mock_get_config.return_value = cfg
        service = NotificationService()
        with mock.patch.object(service, "send_to_feishu", return_value=True) as mock_text, \
             mock.patch.object(service, "send_to_custom", return_value=True) as mock_custom, \
             mock.patch.object(service, "send_feishu_file", return_value=True) as mock_file, \
             mock.patch.object(service, "save_report_to_file") as mock_save:
            result = service.send_with_results("content", route_type="alert")
        self.assertTrue(result.success)
        mock_text.assert_called_once_with("content")
        mock_custom.assert_called_once_with("content")
        mock_file.assert_not_called()
        mock_save.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_feishu_send_as_file_system_error_uses_text(self, mock_get_config):
        """FEISHU_SEND_AS_FILE does not activate for system_error routes."""
        cfg = _make_config(
            feishu_webhook_url="https://feishu.example/hook",
            feishu_send_as_file=True,
        )
        mock_get_config.return_value = cfg
        service = NotificationService()
        with mock.patch.object(service, "send_to_feishu", return_value=True) as mock_text, \
             mock.patch.object(service, "save_report_to_file") as mock_save:
            result = service.send_with_results("error", route_type="system_error")
        self.assertTrue(result.success)
        mock_text.assert_called_once_with("error")
        mock_save.assert_not_called()

    @mock.patch("src.notification.get_config")
    def test_send_dedup_suppresses_static_channels_after_success(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            custom_webhook_urls=["https://example.com/webhook"],
            notification_dedup_ttl_seconds=60,
        )
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_custom", return_value=True) as mock_custom:
            self.assertTrue(service.send("content at 12:00", route_type="alert", dedup_key="alert:600519"))
            self.assertFalse(service.send("content at 12:01", route_type="alert", dedup_key="alert:600519"))

        mock_custom.assert_called_once_with("content at 12:00")

    @mock.patch("src.notification.get_config")
    def test_send_releases_noise_reservation_when_static_channels_fail(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            custom_webhook_urls=["https://example.com/webhook"],
            notification_dedup_ttl_seconds=60,
        )
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_custom", side_effect=[False, True]) as mock_custom:
            self.assertFalse(
                service.send(
                    "content at 12:00",
                    route_type="alert",
                    dedup_key="alert:600519",
                )
            )
            self.assertTrue(
                service.send(
                    "content at 12:01",
                    route_type="alert",
                    dedup_key="alert:600519",
                )
            )

        self.assertEqual(mock_custom.call_count, 2)

    @mock.patch("src.notification.get_config")
    def test_send_to_context_is_not_limited_by_noise_controls(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(
            custom_webhook_urls=["https://example.com/webhook"],
            notification_dedup_ttl_seconds=60,
        )
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch.object(service, "send_to_context", return_value=True) as mock_context, \
             mock.patch.object(service, "send_to_custom", return_value=True) as mock_custom:
            self.assertTrue(service.send("content at 12:00", route_type="alert", dedup_key="alert:600519"))
            self.assertTrue(service.send("content at 12:01", route_type="alert", dedup_key="alert:600519"))

        self.assertEqual(mock_context.call_count, 2)
        mock_custom.assert_called_once_with("content at 12:00")

    @mock.patch("src.notification.get_config")
    def test_noise_check_failure_does_not_block_static_send(self, mock_get_config: mock.MagicMock):
        cfg = _make_config(custom_webhook_urls=["https://example.com/webhook"])
        mock_get_config.return_value = cfg

        service = NotificationService()

        with mock.patch("src.notification_noise._evaluate_notification_noise", side_effect=RuntimeError("boom")), \
             mock.patch.object(service, "send_to_custom", return_value=True) as mock_custom:
            ok = service.send("content", route_type="alert")

        self.assertTrue(ok)
        mock_custom.assert_called_once_with("content")

    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_discord_via_notification_service_with_webhook(
        self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock
    ):
        cfg = _make_config(discord_webhook_url="https://discord.example/webhook")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(204)

        service = NotificationService()
        self.assertIn(NotificationChannel.DISCORD, service.get_available_channels())

        ok = service.send("discord content")

        self.assertTrue(ok)
        mock_post.assert_called_once()

    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_discord_via_notification_service_with_bot(
        self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock
    ):
        cfg = _make_config(discord_bot_token="TOKEN", discord_main_channel_id="123")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200)

        service = NotificationService()
        self.assertIn(NotificationChannel.DISCORD, service.get_available_channels())

        ok = service.send("discord content")

        self.assertTrue(ok)
        mock_post.assert_called_once()
        
    @mock.patch("src.notification.get_config")
    @mock.patch("src.notification_sender.discord_sender.time.sleep", return_value=None)
    @mock.patch("requests.post")
    def test_send_to_discord_via_notification_service_with_bot_requires_chunking(
        self,
        mock_post: mock.MagicMock,
        _mock_sleep: mock.MagicMock,
        mock_get_config: mock.MagicMock,
    ):
        cfg = _make_config(
            discord_bot_token="TOKEN",
            discord_main_channel_id="123",
            discord_max_words=2000,
        )
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200)

        service = NotificationService()
        self.assertIn(NotificationChannel.DISCORD, service.get_available_channels())

        ok = service.send("A" * 6000)

        self.assertTrue(ok)
        self.assertAlmostEqual(mock_post.call_count, 4, delta=1)


class TestNotificationServiceReportGeneration(unittest.TestCase):

    @mock.patch("src.notification.get_config")
    def test_canonical_wait_blocks_legacy_score_trend_and_advice_across_public_reports(
        self, mock_get_config: mock.MagicMock
    ):
        result = _make_canonical_wait_contaminated_result()

        for renderer_enabled in (False, True):
            mock_get_config.return_value = _make_config(
                report_renderer_enabled=renderer_enabled
            )
            service = NotificationService()
            outputs = {
                "daily": service.generate_daily_report([result], report_date="2025-09-30"),
                "dashboard": service.generate_dashboard_report([result], report_date="2025-09-30"),
                "brief": service.generate_brief_report([result], report_date="2025-09-30"),
                "wechat_dashboard": service.generate_wechat_dashboard([result]),
                "wechat_summary": service.generate_wechat_summary([result]),
                "single": service.generate_single_stock_report(result),
                "quick_summary": NotificationBuilder.build_stock_summary([result]),
            }

            for name, rendered in outputs.items():
                self.assertIn("观望", rendered, msg=name)
                self.assertNotIn("LEGACY_STRONG_BULLISH", rendered, msg=name)
                self.assertNotIn("LEGACY_BUY_NOW", rendered, msg=name)
                self.assertNotIn("LEGACY_ANALYSIS_SUMMARY_MUST_NOT_RENDER", rendered, msg=name)
                self.assertNotIn("LEGACY_BUY_REASON_MUST_NOT_RENDER", rendered, msg=name)
                self.assertNotIn("LEGACY_TREND_ANALYSIS_MUST_NOT_RENDER", rendered, msg=name)
                self.assertNotIn("评分 99", rendered, msg=name)
                self.assertNotIn("评分:99", rendered, msg=name)
                self.assertNotIn("评分：99", rendered, msg=name)

            self.assertIn("CANONICAL_WAIT_CONCLUSION", outputs["daily"])
            self.assertIn("CANONICAL_WAIT_CONCLUSION", outputs["dashboard"])
            self.assertIn("CANONICAL_WAIT_CONCLUSION", outputs["brief"])

    @mock.patch("src.notification.get_config")
    def test_stale_canonical_brief_cannot_fall_back_to_legacy_buy_content(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=True)
        result = _make_canonical_wait_contaminated_result()
        result.dashboard["factor_decision"]["investor_brief"]["canonical_binding"] = {
            "stale": True
        }

        service = NotificationService()
        # Stronger than returning an abbreviated 'safe' report: the current
        # canonical is invalid, so there is no Product text to contaminate.
        with self.assertRaisesRegex(ValueError, "canonical report material binding invalid"):
            service.generate_dashboard_report([result], report_date="2025-09-30")
        with self.assertRaisesRegex(ValueError, "canonical report material binding invalid"):
            service.generate_brief_report([result], report_date="2025-09-30")

        # The original legacy-BUY contamination must not escape through direct
        # dispatch either, even if the caller already supplied stale text.
        service.send_to_context = mock.MagicMock(return_value=False)
        service.send_to_email = mock.MagicMock(return_value=True)
        service.send_to_telegram = mock.MagicMock(return_value=True)
        service.require_current_asset_data_for_send = mock.MagicMock()
        blocked = service.send_with_results(
            "LEGACY_STRONG_BULLISH LEGACY_BUY_NOW LEGACY_ANALYSIS_SUMMARY_MUST_NOT_RENDER 评分 99",
            route_type="report", asset_results=[result],
        )
        self.assertEqual(blocked.status, "invalid_report")
        self.assertFalse(blocked.success)
        self.assertFalse(blocked.dispatched)
        service.send_to_context.assert_not_called()
        service.send_to_email.assert_not_called()
        service.send_to_telegram.assert_not_called()
        service.require_current_asset_data_for_send.assert_called_once_with([result])

    def test_investor_brief_requires_current_canonical_trace_binding(self):
        result = _make_investor_brief_result()
        factor = result.dashboard["factor_decision"]
        brief = factor["investor_brief"]

        self.assertIs(_get_valid_investor_brief(factor, "zh"), brief)

        forged = deepcopy(factor)
        forged["investor_brief"]["canonical_binding"]["runtime_trace_hash"] = "forged"
        self.assertIsNone(_get_valid_investor_brief(forged, "zh"))

        legacy = deepcopy(factor)
        legacy.pop("canonical_decision_identity", None)
        legacy["investor_brief"].pop("canonical_binding", None)
        self.assertIsNone(_get_valid_investor_brief(legacy, "zh"))

    @mock.patch("src.notification.get_config")
    def test_research_email_subject_distinguishes_auto_and_watchlist(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = _make_investor_brief_result()
        delivery = result.dashboard["research_delivery"]
        delivery.update(
            {
                "selection_source": "AUTO_SCREEN",
                "delivery_envelope": "ASSET_RESEARCH_BRIEF_AUTO",
                "product_group": "AUTO_STOCK_FOCUS",
                "group_rank": 1,
            }
        )

        self.assertEqual(
            service.build_research_email_subject([result], report_date="2026-09-24"),
            "【晚间自动发现｜2026-09-24】0只ETF + 1只股票｜完整研究",
        )

        delivery.update(
            {
                "selection_source": "SPECIFIED_CODES",
                "delivery_envelope": "ASSET_RESEARCH_BRIEF_WATCHLIST",
                "product_group": "WATCHLIST_STOCK",
            }
        )
        self.assertEqual(
            service.build_research_email_subject([result], report_date="2026-09-24"),
            "【我的自选研究｜2026-09-24】1只材料变化标的",
        )

    @mock.patch("src.notification.get_config")
    def test_auto_remaining_rows_keep_reason_risk_and_invalidation(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()

        focus = _make_investor_brief_result()
        focus.code = "510300"
        focus.name = "沪深300ETF"
        focus.dashboard["factor_decision"]["investor_brief"]["asset_type"] = "etf"
        focus.dashboard["research_delivery"].update(
            {
                "selection_source": "AUTO_SCREEN",
                "delivery_envelope": "ASSET_RESEARCH_BRIEF_AUTO",
                "product_group": "AUTO_ETF_FOCUS",
                "group_rank": 1,
            }
        )

        remaining = deepcopy(_make_investor_brief_result())
        remaining.code = "600000"
        remaining.name = "浦发银行"
        remaining.dashboard["research_delivery"].update(
            {
                "selection_source": "AUTO_SCREEN",
                "delivery_envelope": "ASSET_RESEARCH_BRIEF_AUTO",
                "product_group": "AUTO_STOCK_REMAINING",
                "group_rank": 4,
            }
        )
        remaining_brief = remaining.dashboard["factor_decision"]["investor_brief"]
        remaining_brief["fused_paragraph"] = "R004_REMAINING_REASON"
        remaining_brief["risk_notes"] = ["R004_REMAINING_RISK"]
        remaining_brief["invalidation"] = "R004_REMAINING_INVALIDATION"
        # Current AUTO fixture facts are authored BEFORE canonical sealing.
        # Preserve all existing display assertions; never trust stale bytes.
        remaining_factor = remaining.dashboard["factor_decision"]
        remaining_factor["canonical_decision_identity"] = canonical_factor_binding(remaining_factor)
        remaining_brief["canonical_binding"] = deepcopy(
            remaining_factor["canonical_decision_identity"]
        )

        out = service.generate_brief_report(
            [focus, remaining],
            report_date="2026-09-24",
        )

        self.assertIn("### ETF重点 Top 3", out)
        self.assertIn("### 股票其余候选（含分类不足）", out)
        self.assertIn("**浦发银行（600000）**", out)
        self.assertIn("核心理由：R004_REMAINING_REASON", out)
        self.assertIn("主要风险：R004_REMAINING_RISK", out)
        self.assertIn("失效条件：R004_REMAINING_INVALIDATION", out)

    @mock.patch("src.notification.get_config")
    def test_watchlist_envelope_keeps_complete_delivered_asset_cards(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        first = _make_investor_brief_result()
        first.dashboard["research_delivery"].update(
            {
                "selection_source": "SPECIFIED_CODES",
                "delivery_envelope": "ASSET_RESEARCH_BRIEF_WATCHLIST",
                "product_group": "WATCHLIST_STOCK",
            }
        )
        second = deepcopy(first)
        second.code = "000001"
        second.name = "平安银行"
        second.dashboard["factor_decision"]["investor_brief"][
            "one_line_conclusion"
        ] = "R004_WATCHLIST_SECOND_CONCLUSION"
        second.dashboard["factor_decision"]["investor_brief"][
            "fused_paragraph"
        ] = "R004_WATCHLIST_SECOND_DETAIL"

        out = service.generate_brief_report(
            [first, second],
            report_date="2026-09-24",
        )

        self.assertIn("## ⭐ 我的自选研究", out)
        self.assertIn("贵州茅台", out)
        self.assertIn("平安银行", out)
        self.assertIn("R004_WATCHLIST_SECOND_CONCLUSION", out)
        self.assertIn("R004_WATCHLIST_SECOND_DETAIL", out)
    """报告生成与选路相关测试。"""

    def test_signal_metadata_uses_resolved_eight_state_action(self):
        service = NotificationService()
        cases = [
            ("avoid", "Avoid", 90, ("Avoid", "🟡", "hold")),
            ("add", "Add", 50, ("Add", "🟢", "buy")),
            ("alert", "Alert", 85, ("Alert", "🔴", "sell")),
        ]

        for action, action_label, score, expected in cases:
            with self.subTest(action=action):
                result = AnalysisResult(
                    code="AAPL",
                    name="Apple",
                    sentiment_score=score,
                    trend_prediction="Neutral",
                    operation_advice="Hold",
                    report_language="en",
                    action=action,
                    action_label=action_label,
                )

                self.assertEqual(service._get_signal_level(result), expected)

    def test_build_stock_summary_uses_resolved_eight_state_action(self):
        summary = NotificationBuilder.build_stock_summary(
            [
                AnalysisResult(
                    code="AVOID",
                    name="Avoid Corp",
                    sentiment_score=90,
                    trend_prediction="Neutral",
                    operation_advice="Avoid",
                    report_language="en",
                    action="avoid",
                    action_label="Avoid",
                ),
                AnalysisResult(
                    code="ALERT",
                    name="Alert Corp",
                    sentiment_score=85,
                    trend_prediction="Neutral",
                    operation_advice="Alert",
                    report_language="en",
                    action="alert",
                    action_label="Alert",
                ),
                AnalysisResult(
                    code="ADD",
                    name="Add Corp",
                    sentiment_score=50,
                    trend_prediction="Neutral",
                    operation_advice="Add",
                    report_language="en",
                    action="add",
                    action_label="Add",
                ),
            ]
        )

        self.assertIn("🟡 Avoid Corp(AVOID): Avoid | Score 90", summary)
        self.assertIn("🔴 Alert Corp(ALERT): Alert | Score 85", summary)
        self.assertIn("🟢 Add Corp(ADD): Add | Score 50", summary)
        self.assertNotIn("Buy | Score 50", summary)
        self.assertNotIn("Strong Buy", summary)

    @mock.patch("src.notification.get_config")
    def test_report_rows_and_summary_use_same_score_aligned_action(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="AAPL",
            name="Apple",
            sentiment_score=72,
            trend_prediction="Bullish",
            operation_advice="Hold",
            decision_type="hold",
            report_language="en",
        )

        out = service.generate_brief_report([result], report_date="2026-07-11")

        self.assertIn("🟢1 🟡0 🔴0", out)
        self.assertIn("Buy | Score 72", out)
        self.assertNotIn("Hold | Score 72", out)

    @mock.patch("src.notification.get_config")
    def test_generate_aggregate_report_routes_by_report_type(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config()
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )

        with mock.patch.object(service, "generate_dashboard_report", return_value="dashboard") as mock_dashboard, mock.patch.object(
            service, "generate_brief_report", return_value="brief"
        ) as mock_brief:
            self.assertEqual(service.generate_aggregate_report([result], "simple"), "dashboard")
            self.assertEqual(service.generate_aggregate_report([result], "full"), "dashboard")
            self.assertEqual(service.generate_aggregate_report([result], "detailed"), "dashboard")
            self.assertEqual(service.generate_aggregate_report([result], "brief"), "brief")

        self.assertEqual(mock_dashboard.call_count, 3)
        mock_brief.assert_called_once()

    @mock.patch("src.notification.get_config")
    def test_asset_investor_brief_renders_once_in_fallback_and_suppresses_legacy(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        out = NotificationService().generate_dashboard_report(
            [_make_investor_brief_result()],
            report_date="2026-09-14",
        )

        self.assertEqual(out.count("当前偏弱，暂不追高，等待趋势重新转强。"), 1)
        self.assertEqual(out.count("日线趋势仍偏弱，量价尚未确认重新转强；当前以等待确认为主。"), 1)
        self.assertIn("**技术参考分**: 43/100", out)
        self.assertIn("**当前价格**: 1450.0", out)
        self.assertIn("**估值**: 合理｜PE/PB 仅作保守参考（不确定性：缺少历史分位）", out)
        self.assertIn("**关键位置**: 结构支撑 1400.0｜结构压力 1500.0", out)
        self.assertIn(
            "**周期覆盖**: 本次可用周期：日线。月线、周线、60分钟、30分钟、15分钟和5分钟本次暂无可用证据。",
            out,
        )
        self.assertNotIn("MISSING", out)
        self.assertIn("**多周期量价与形态**:", out)
        self.assertIn("- 日线：趋势偏弱；量价尚未确认重新转强", out)
        self.assertNotIn("**短线波段 30/15/5m**", out)
        self.assertIn("**历史参考胜率**: 暂不提供（尚未完成 PIT 同策略验证）", out)
        self.assertIn("**当前机会概率**: 暂不提供（尚未完成独立校准）", out)
        self.assertIn("- 弱趋势仍未修复", out)
        self.assertIn("- 估值证据仍有限", out)
        self.assertIn("第三条风险不应进入第一屏", out)
        self.assertNotIn("旧核心结论（不得出现）", out)
        self.assertNotIn("旧因子结论（不得重复）", out)
        self.assertNotIn("旧空仓建议（不得出现）", out)
        self.assertNotIn("### 📌 核心结论", out)
        self.assertNotIn("### 🧭 综合评估", out)

    @mock.patch("src.notification.get_config")
    def test_full_research_universe_deferred_obligations_visible_without_admitting_them(
        self, mock_get_config: mock.MagicMock
    ):
        # Independent accepted Source/Factor obligations: a 7x8 technical matrix
        # must not certify omission of asset, classic-method or research planes.
        result = _make_investor_brief_result()
        required = (
            "QUALITY：证据不足", "VALUATION：证据不足",
            "GLOBAL：未准入", "BREADTH：未准入",
            "CHAN：未准入", "WAVE：未准入",
            "EXTRA_INDICATORS：未准入", "ATR_WILDER", "ADX_DMI",
            "BOLLINGER", "KDJ", "OBV_ADL", "MFI",
            "AVWAP_PROFILE：未准入", "VWAP", "AVWAP_VOLUME_PROFILE",
            "INTRADAY_30m：未准入", "RISK_REWARD：未准入",
            "PROBABILITY：未准入", "ETF_SPECIFIC：股票路径不适用",
        )
        # These two methods are implemented, but this fixture has no
        # admissible dated financial facts. No source => no READY verdict.
        from src.services.evidence_traceability_registry import compile_method_window_policy_view
        profiles = {
            item["method_id"]: item["leaf_implementation_state"]
            for item in compile_method_window_policy_view()["methods"]
        }
        self.assertEqual(profiles["QUALITY"], "EXISTING_REUSED")
        self.assertEqual(profiles["VALUATION"], "EXISTING_REUSED")

        from html.parser import HTMLParser
        from src.formatters import markdown_to_html_document

        class VisibleText(HTMLParser):
            def __init__(self):
                super().__init__(convert_charrefs=True)
                self.parts = []

            def handle_data(self, value):
                self.parts.append(value)

        for renderer_enabled in (False, True):
            mock_get_config.return_value = _make_config(
                report_renderer_enabled=renderer_enabled
            )
            service = NotificationService()
            full = service.generate_dashboard_report([result], report_date="2025-09-30")
            compact = service.generate_brief_report([result], report_date="2025-09-30")
            visible = VisibleText()
            visible.feed(markdown_to_html_document(full))
            for token in required:
                for name, output in (
                    ("full", full), ("compact", compact), ("decoded_html", "".join(visible.parts))
                ):
                    self.assertIn(token, output, msg=f"{name}: {token}")
            self.assertEqual(full.count("当前偏弱，暂不追高，等待趋势重新转强。"), 1)
            self.assertNotIn("MISSING", full)
            self.assertNotIn("**ETF专属交易质量**", full)

    @mock.patch("src.notification.get_config")
    def test_builder_material_risks_survive_full_and_compact_complete_outputs(
        self, mock_get_config: mock.MagicMock
    ):
        risks = [
            "RISK_LIQUIDITY_DISTINCT",
            "RISK_FINANCIAL_VINTAGE_DISTINCT",
            "RISK_SUPPORT_BREAK_DISTINCT",
            "RISK_VALUATION_ASSUMPTION_DISTINCT",
        ]
        factor = _make_builder_risk_factor(risks)
        result = _make_investor_brief_result()
        apply_canonical_decision_to_result(result, factor, scope="production")

        for renderer_enabled in (False, True):
            with self.subTest(report_renderer_enabled=renderer_enabled):
                mock_get_config.return_value = _make_config(
                    report_renderer_enabled=renderer_enabled
                )
                service = NotificationService()
                full = service.generate_dashboard_report(
                    [result], report_date="2026-09-14"
                )
                compact = service.generate_brief_report(
                    [result], report_date="2026-09-14"
                )
                for risk in risks:
                    self.assertIn(risk, full)
                    self.assertIn(risk, compact)

    def test_legacy_factor_fallback_preserves_all_material_reasons_risks_and_conflicts(self):
        reasons = [f"WHY_MATERIAL_{index}_DISTINCT" for index in range(1, 6)]
        risks = [
            "RISK_LIQUIDITY_DISTINCT",
            "RISK_FINANCIAL_VINTAGE_DISTINCT",
            "RISK_SUPPORT_BREAK_DISTINCT",
            "RISK_VALUATION_ASSUMPTION_DISTINCT",
        ]
        lines = []
        _append_factor_decision_block(
            lines,
            {
                "conclusion": "fixture",
                "historical_reference": {},
                "current_probability": {},
                "why": reasons,
                "action_condition": "",
                "invalidation_condition": "",
                "valuation": "",
                "cost_structure": "",
                "risk_notes": risks,
            },
            "zh",
        )
        output = "\n".join(lines)
        for item in reasons + risks:
            self.assertIn(item, output)

        conflict_types = [
            "directional_opposition",
            "wide_score_dispersion",
            "high_confidence_dissent",
            "adjustment_contradiction",
        ]
        conflict_lines = []
        _append_strategy_synthesis_block(
            conflict_lines,
            {
                "final_signal": "hold",
                "consensus_level": "mixed",
                "conflict_severity": "high",
                "conflict_count": len(conflict_types),
                "confidence": 0.42,
                "supporting_skills": [],
                "opposing_skills": [],
                "conflicts": [
                    {
                        "conflict_type": conflict_type,
                        "severity": "high",
                        "participants": [f"SKILL_{index}_A", f"SKILL_{index}_B"],
                    }
                    for index, conflict_type in enumerate(conflict_types, start=1)
                ],
            },
            get_report_labels("zh"),
            "zh",
        )
        conflict_output = "\n".join(conflict_lines)
        for conflict_type in conflict_types:
            self.assertIn(
                localize_strategy_conflict_description(conflict_type, "zh"),
                conflict_output,
            )

    @mock.patch("src.notification.get_config")
    def test_legacy_single_stock_and_brief_preserve_material_text_without_fixed_budgets(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        one_sentence = "ONE_SENTENCE_FULL_" + "完整结论不能按字符预算删减" * 8
        earnings = "EARNINGS_FULL_" + "公告事实和不确定性必须全部保留" * 8
        sentiment = "SENTIMENT_FULL_" + "情绪材料仅作上下文但不能静默截断" * 8
        risks = [
            f"RISK_{index}_FULL_" + "不同风险有独立条件和影响，不能只保留前三项" * 7
            for index in range(1, 5)
        ]
        catalysts = [
            f"CATALYST_{index}_FULL_" + "不同事件有独立日期和条件，不能只保留前三项" * 7
            for index in range(1, 5)
        ]
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=42,
            trend_prediction="偏弱",
            operation_advice="观望",
            analysis_summary=one_sentence,
            dashboard={
                "core_conclusion": {"one_sentence": one_sentence},
                "intelligence": {
                    "earnings_outlook": earnings,
                    "sentiment_summary": sentiment,
                    "risk_alerts": risks,
                    "positive_catalysts": catalysts,
                },
            },
        )

        service = NotificationService()
        brief = service.generate_brief_report([result], report_date="2026-10-08")
        single = service.generate_single_stock_report(result)

        self.assertIn(one_sentence, brief)
        for material in [earnings, sentiment, *risks, *catalysts]:
            self.assertIn(material, single)

    @mock.patch("src.notification.get_config")
    def test_asset_investor_brief_template_matches_fallback_semantics(
        self, mock_get_config: mock.MagicMock
    ):
        result = _make_investor_brief_result()
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        fallback = NotificationService().generate_dashboard_report(
            [result],
            report_date="2026-09-14",
        )

        mock_get_config.return_value = _make_config(report_renderer_enabled=True)
        rendered = NotificationService().generate_dashboard_report(
            [result],
            report_date="2026-09-14",
        )

        shared_markers = [
            "当前偏弱，暂不追高，等待趋势重新转强。",
            "日线趋势仍偏弱，量价尚未确认重新转强；当前以等待确认为主。",
            "**技术参考分**: 43/100",
            "**当前价格**: 1450.0",
            "合理",
            "PE/PB 仅作保守参考",
            "结构支撑 1400.0",
            "结构压力 1500.0",
            "重新站上 1500 且放量确认",
            "跌破 1400 且放量",
            "本次可用周期：日线。月线、周线、60分钟、30分钟、15分钟和5分钟本次暂无可用证据。",
            "尚未完成 PIT 同策略验证",
            "暂不提供（尚未完成独立校准）",
            "弱趋势仍未修复",
            "估值证据仍有限",
        ]
        for marker in shared_markers:
            with self.subTest(marker=marker):
                self.assertIn(marker, fallback)
                self.assertIn(marker, rendered)

        for output in (fallback, rendered):
            self.assertEqual(output.count("当前偏弱，暂不追高，等待趋势重新转强。"), 1)
            self.assertEqual(output.count("日线趋势仍偏弱，量价尚未确认重新转强；当前以等待确认为主。"), 1)
            self.assertNotIn("旧核心结论（不得出现）", output)
            self.assertNotIn("旧因子结论（不得重复）", output)
            self.assertNotIn("### 📌 核心结论", output)
            self.assertNotIn("### 🧭 综合评估", output)

    @mock.patch("src.notification.get_config")
    def test_investor_notification_projection_is_compact_ordered_and_missingness_safe(
        self, mock_get_config: mock.MagicMock
    ):
        result = _make_investor_brief_result()

        for renderer_enabled in (False, True):
            with self.subTest(report_renderer_enabled=renderer_enabled):
                mock_get_config.return_value = _make_config(
                    report_renderer_enabled=renderer_enabled
                )
                out = NotificationService().generate_brief_report(
                    [result],
                    report_date="2026-09-14",
                )

                markers = [
                    "**身份**: A股｜主板",
                    "**综合结论**: 当前偏弱，暂不追高，等待趋势重新转强。",
                    "日线趋势仍偏弱，量价尚未确认重新转强；当前以等待确认为主。",
                    "**估值**: 合理｜PE/PB 仅作保守参考（不确定性：缺少历史分位）",
                    "**多周期**: 本次可用周期：日线。月线、周线、60分钟、30分钟、15分钟和5分钟本次暂无可用证据。",
                    "**关键位置**: 结构支撑 1400.0｜结构压力 1500.0",
                    "**触发条件**: 重新站上 1500 且放量确认",
                    "**失效条件**: 跌破 1400 且放量",
                    "**操作**: 空仓：不新增仓位；等待趋势和量价重新确认。｜持仓：按既有风险计划管理；不因本报告生成新的卖出指令。",
                    "**主要风险**:",
                    "- 弱趋势仍未修复",
                    "- 估值证据仍有限",
                ]
                for marker in markers:
                    self.assertIn(marker, out)

                positions = [out.index(marker) for marker in markers[:10]]
                self.assertEqual(positions, sorted(positions))
                self.assertIn("第三条风险不应进入第一屏", out)
                self.assertNotIn("MISSING", out)
                self.assertNotIn("**技术参考分**", out)
                self.assertNotIn("**趋势/量价**", out)
                self.assertIn("**历史参考胜率**", out)
                self.assertIn("**当前机会概率**", out)
                self.assertNotIn("旧核心结论（不得出现）", out)
                self.assertNotIn("旧因子结论（不得重复）", out)
                self.assertNotIn("### 📊 数据透视", out)
                self.assertNotIn("盘中决策护栏", out)

    @mock.patch("src.notification.get_config")
    def test_future_ready_mtf_and_short_term_schema_renders_without_new_authority(
        self, mock_get_config: mock.MagicMock
    ):
        result = _make_investor_brief_result()
        brief = result.dashboard["factor_decision"]["investor_brief"]
        brief["timeframe_thesis"]["monthly"] = {
            "status": "READY",
            "role": "LONG_TERM_CONTEXT",
            "summary": "月线平台收敛，等待方向确认",
        }
        brief["timeframe_thesis"]["weekly"] = {
            "status": "READY",
            "role": "PRIMARY_TREND_CONTEXT",
            "summary": "周线趋势仍受压",
        }
        brief["short_term_execution_panel"] = {
            "status": "READY",
            "state": "WAIT_FOR_TRIGGER",
            "summary": "30m 主结构未转强；15m 等待确认；5m 仅作时点。",
        }

        for renderer_enabled in (False, True):
            mock_get_config.return_value = _make_config(
                report_renderer_enabled=renderer_enabled
            )
            out = NotificationService().generate_dashboard_report(
                [result],
                report_date="2026-09-14",
            )
            self.assertIn("月线：月线平台收敛，等待方向确认", out)
            self.assertIn("周线：周线趋势仍受压", out)
            self.assertIn("短线波段 30/15/5m", out)
            self.assertEqual(
                out.count("当前偏弱，暂不追高，等待趋势重新转强。"),
                1,
            )



    @mock.patch("src.notification.get_config")
    def test_partial_current_intraday_context_renders_without_claiming_trigger(
        self, mock_get_config: mock.MagicMock
    ):
        result = _make_investor_brief_result()
        brief = result.dashboard["factor_decision"]["investor_brief"]
        brief["timeframe_thesis"]["60m"] = {
            "status": "PARTIAL_CURRENT",
            "role": "OPTIONAL_BRIDGE",
            "summary": "均线多头；MACD偏强；分钟结构尚未准入",
        }
        brief["short_term_execution_panel"] = {
            "status": "READY",
            "state": "CONTEXT_ONLY_NO_TRIGGER_AUTHORITY",
            "independent_action_authority": False,
            "30m": {
                "status": "PARTIAL_CURRENT",
                "role": "PRIMARY_STRUCTURE",
                "summary": "均线压缩；RSI中性；分钟结构尚未准入",
            },
            "15m": {
                "status": "PARTIAL_CURRENT",
                "role": "TRIGGER_CONFIRMATION",
                "summary": "量能正常；MACD偏强；分钟结构尚未准入",
            },
            "5m": {
                "status": "PARTIAL_CURRENT",
                "role": "MICRO_TIMING",
                "summary": "均线交叉；RSI中性；分钟结构尚未准入",
            },
            "summary": "低周期仅作上下文，不构成独立触发。",
        }
        for renderer_enabled in (False, True):
            mock_get_config.return_value = _make_config(
                report_renderer_enabled=renderer_enabled
            )
            service = NotificationService()
            full = service.generate_dashboard_report([result], report_date="2026-09-14")
            compact = service.generate_brief_report([result])
            for rendered in (full, compact):
                self.assertIn("30分钟：均线压缩；RSI中性；分钟结构尚未准入", rendered)
                self.assertIn("15分钟：量能正常；MACD偏强；分钟结构尚未准入", rendered)
                self.assertIn("5分钟：均线交叉；RSI中性；分钟结构尚未准入", rendered)
                self.assertNotIn("TRIGGERED", rendered)
            self.assertIn("60分钟：均线多头；MACD偏强；分钟结构尚未准入", full)
    @mock.patch("src.notification.get_config")
    def test_etf_valuation_label_and_explicit_short_timeframes_render_from_same_brief(
        self, mock_get_config: mock.MagicMock
    ):
        result = _make_investor_brief_result()
        brief = result.dashboard["factor_decision"]["investor_brief"]
        brief["asset_type"] = "etf"
        brief["valuation"] = {
            "status": "PARTIAL_CURRENT",
            "label": "底层估值",
            "summary": "近十年62%分位",
            "uncertainty": "",
        }
        brief["asset_specific"] = {
            "premium_discount": {"status": "READY", "summary": "折价0.15%"},
            "liquidity_spread": {"status": "READY", "summary": "买卖价差0.03%"},
            "tracking_quality": {"status": "READY", "summary": "近20日跟踪误差0.18%"},
            "underlying_valuation": {"status": "READY", "summary": "底层估值近十年62%分位"},
        }
        brief["timeframe_thesis"]["60m"] = {
            "status": "READY",
            "role": "OPTIONAL_BRIDGE",
            "summary": "回踩结构未破",
        }
        brief["short_term_execution_panel"] = {
            "status": "READY",
            "state": "WAIT_FOR_TRIGGER",
            "30m": {
                "status": "READY",
                "role": "PRIMARY_STRUCTURE",
                "summary": "顶背离已经确认",
            },
            "15m": {
                "status": "READY",
                "role": "TRIGGER_CONFIRMATION",
                "summary": "死叉后进入整理",
            },
            "5m": {
                "status": "READY",
                "role": "MICRO_TIMING",
                "summary": "量能继续收缩",
            },
            "summary": None,
        }

        for renderer_enabled in (False, True):
            mock_get_config.return_value = _make_config(
                report_renderer_enabled=renderer_enabled
            )
            service = NotificationService()
            full = service.generate_dashboard_report(
                [result],
                report_date="2026-09-14",
            )
            compact = service.generate_brief_report([result])

            for rendered in (full, compact):
                self.assertIn("**底层估值**: 近十年62%分位", rendered)
                self.assertIn(
                    "**ETF专属交易质量**: 折溢价：折价0.15%｜买卖价差0.03%｜跟踪质量：近20日跟踪误差0.18%",
                    rendered,
                )
                self.assertIn("30分钟：顶背离已经确认", rendered)
                self.assertIn("15分钟：死叉后进入整理", rendered)
                self.assertIn("5分钟：量能继续收缩", rendered)
            self.assertIn("60分钟：回踩结构未破", full)
            self.assertNotIn("- 60m：", full)

    @mock.patch("src.notification.get_config")
    def test_direct_full_canonical_reports_reject_legacy_summary_only_at_renderer_owner(
        self, mock_get_config: mock.MagicMock,
    ):
        """Full Report itself must fail-closed, not rely on Pipeline/SMTP guards."""
        from src.schemas.decision_action import has_canonical_action_authority_for_result

        result = _make_investor_brief_result()
        factor = result.dashboard["factor_decision"]
        brief = factor["investor_brief"]
        factor.update({
            "conclusion": brief["one_line_conclusion"],
            "action_condition": brief["trigger"],
            "invalidation_condition": brief["invalidation"],
            "canonical_decision": {
                "authority": "stock_trend_quality_pullback_v1",
                "action": "PASS", "public_action": "avoid",
                "evidence_state": "PROVEN", "hard_veto": True,
                "reason_codes": ["WEAK_TREND"],
            },
        })
        apply_canonical_decision_to_result(result, factor, scope="production")
        assert_canonical_consumer_consistency(result, scope="production")
        self.assertTrue(has_canonical_action_authority_for_result(result))
        for renderer_enabled in (False, True):
            mock_get_config.return_value = _make_config(
                report_renderer_enabled=renderer_enabled,
                report_summary_only=True,
            )
            notifier = NotificationService()
            for generator in (notifier.generate_dashboard_report, notifier.generate_daily_report):
                with self.subTest(renderer_enabled=renderer_enabled, generator=generator.__name__):
                    with self.assertRaisesRegex(ValueError, "canonical research cannot use report_summary_only"):
                        generator([result], report_date="2026-10-14")

    @mock.patch("src.notification.get_config")
    def test_direct_noncanonical_legacy_summary_only_retains_independent_legal_route(
        self, mock_get_config: mock.MagicMock,
    ):
        """The old noncanonical compact feature is not globally removed."""
        from src.schemas.decision_action import has_canonical_action_authority_for_result

        result = AnalysisResult(
            code="600519", name="贵州茅台",
            sentiment_score=48, trend_prediction="震荡",
            operation_advice="观望", analysis_summary="仅历史简报",
            report_language="zh",
        )
        self.assertFalse(has_canonical_action_authority_for_result(result))
        for renderer_enabled in (False, True):
            mock_get_config.return_value = _make_config(
                report_renderer_enabled=renderer_enabled,
                report_summary_only=True,
            )
            notifier = NotificationService()
            for generator in (notifier.generate_dashboard_report, notifier.generate_daily_report):
                with self.subTest(renderer_enabled=renderer_enabled, generator=generator.__name__):
                    rendered = generator([result], report_date="2026-10-14")
                    self.assertIsInstance(rendered, str)
                    self.assertIn("600519", rendered)

    @mock.patch("src.notification.get_config")
    def test_full_and_compact_reports_preserve_canonical_material_fact_parity(
        self, mock_get_config: mock.MagicMock
    ):
        result = _make_investor_brief_result()
        factor = result.dashboard["factor_decision"]
        brief = factor["investor_brief"]
        brief["asset_type"] = "etf"
        brief["asset_specific"] = {
            "premium_discount": {"status": "READY", "summary": "折价0.15%"},
            "liquidity_spread": {"status": "READY", "summary": "买卖价差0.03%"},
            "tracking_quality": {"status": "READY", "summary": "近20日跟踪误差0.18%"},
        }
        brief["short_term_execution_panel"] = {
            "status": "READY",
            "state": "WAIT_FOR_TRIGGER",
            "30m": {
                "status": "READY",
                "role": "PRIMARY_STRUCTURE",
                "summary": "顶背离已经确认",
            },
            "15m": {
                "status": "READY",
                "role": "TRIGGER_CONFIRMATION",
                "summary": "死叉后进入整理",
            },
            "5m": {
                "status": "READY",
                "role": "MICRO_TIMING",
                "summary": "量能继续收缩",
            },
            "summary": None,
        }
        factor.update(
            {
                "conclusion": brief["one_line_conclusion"],
                "action_condition": brief["trigger"],
                "invalidation_condition": brief["invalidation"],
                "canonical_decision": {
                    "authority": "stock_trend_quality_pullback_v1",
                    "action": "PASS",
                    "public_action": "avoid",
                    "evidence_state": "PROVEN",
                    "hard_veto": True,
                    "reason_codes": ["WEAK_TREND"],
                },
            }
        )
        apply_canonical_decision_to_result(result, factor, scope="production")
        assert_canonical_consumer_consistency(result, scope="production")

        for renderer_enabled in (False, True):
            with self.subTest(report_renderer_enabled=renderer_enabled):
                mock_get_config.return_value = _make_config(
                    report_renderer_enabled=renderer_enabled
                )
                service = NotificationService()
                full = service.generate_dashboard_report(
                    [result],
                    report_date="2026-09-14",
                )
                compact = service.generate_brief_report(
                    [result],
                    report_date="2026-09-14",
                )

                shared_markers = (
                    brief["one_line_conclusion"],
                    brief["fused_paragraph"],
                    "PE/PB 仅作保守参考",
                    "结构支撑 1400.0",
                    "结构压力 1500.0",
                    brief["trigger"],
                    brief["invalidation"],
                    brief["coverage_text"],
                    "弱趋势仍未修复",
                    "估值证据仍有限",
                    "折溢价：折价0.15%",
                    "买卖价差0.03%",
                    "跟踪质量：近20日跟踪误差0.18%",
                    "30分钟：顶背离已经确认",
                    "15分钟：死叉后进入整理",
                    "5分钟：量能继续收缩",
                )
                for marker in shared_markers:
                    with self.subTest(marker=marker):
                        self.assertIn(marker, full)
                        self.assertIn(marker, compact)

                for shared_detail in (
                    "**当前价格**: 1450.0",
                    "**历史参考胜率**:",
                    "**当前机会概率**:",
                    "日线：趋势偏弱；量价尚未确认重新转强",
                    "第三条风险不应进入第一屏",
                ):
                    self.assertIn(shared_detail, full)
                    self.assertIn(shared_detail, compact)

                self.assertIn("**技术参考分**: 43/100", full)
                self.assertNotIn("**技术参考分**: 43/100", compact)

                for forbidden in (
                    "旧核心结论（不得出现）",
                    "旧因子结论（不得重复）",
                    "UNBOUND_",
                ):
                    self.assertNotIn(forbidden, full)
                    self.assertNotIn(forbidden, compact)

    @mock.patch("src.notification.get_config")
    def test_degraded_explanation_status_is_transparent_and_does_not_duplicate_legacy_sections(
        self, mock_get_config: mock.MagicMock
    ):
        result = _make_investor_brief_result()
        factor = result.dashboard["factor_decision"]
        factor["explanation_status"] = {
            "state": "UNAVAILABLE",
            "mode": "DETERMINISTIC_DEGRADED",
            "reason": "LLM_EXPLANATION_UNAVAILABLE",
        }
        factor["investor_brief"]["explanation_status"] = dict(
            factor["explanation_status"]
        )

        for renderer_enabled in (False, True):
            mock_get_config.return_value = _make_config(
                report_renderer_enabled=renderer_enabled
            )
            out = NotificationService().generate_dashboard_report(
                [result],
                report_date="2026-09-14",
            )
            self.assertEqual(
                out.count("LLM 暂不可用；本报告仅依据已证明的确定性证据生成。"),
                1,
            )
            self.assertNotIn("旧核心结论（不得出现）", out)
            self.assertNotIn("旧因子结论（不得重复）", out)
            compact = NotificationService().generate_brief_report([result])
            self.assertNotIn("LLM 暂不可用", compact)
            self.assertNotIn("解释层", compact)

    @mock.patch("src.notification.get_config")
    def test_asset_investor_brief_suppresses_unbound_llm_evidence_panels_and_generic_levels(
        self, mock_get_config: mock.MagicMock
    ):
        result = _make_investor_brief_result()
        result.dashboard["intelligence"] = {
            "sentiment_summary": "UNBOUND_NEWS_SENTIMENT",
            "earnings_outlook": "UNBOUND_EARNINGS_OUTLOOK",
            "risk_alerts": ["UNBOUND_INTEL_RISK"],
            "positive_catalysts": ["UNBOUND_VALUATION_CATALYST"],
            "latest_news": "UNBOUND_LATEST_NEWS",
        }
        result.dashboard["signal_attribution"] = {
            "technical_indicators": 20,
            "news_sentiment": 30,
            "fundamentals": 30,
            "market_conditions": 20,
            "strongest_bullish_signal": "UNBOUND_HISTORICAL_VALUATION",
            "strongest_bearish_signal": "UNBOUND_BEARISH_SIGNAL",
        }
        result.dashboard["data_perspective"] = {
            "price_position": {
                "current_price": 1450.0,
                "ma5": 1451.0,
                "ma10": 1448.0,
                "ma20": 1440.0,
                "bias_ma5": -0.07,
                "bias_status": "安全",
                "support_level": 1399.0,
                "resistance_level": 1499.0,
            }
        }

        for enabled in (False, True):
            with self.subTest(report_renderer_enabled=enabled):
                mock_get_config.return_value = _make_config(report_renderer_enabled=enabled)
                out = NotificationService().generate_dashboard_report(
                    [result],
                    report_date="2026-09-14",
                )
                self.assertIn("结构支撑 1400.0", out)
                self.assertIn("结构压力 1500.0", out)
                self.assertNotIn("UNBOUND_NEWS_SENTIMENT", out)
                self.assertNotIn("UNBOUND_EARNINGS_OUTLOOK", out)
                self.assertNotIn("UNBOUND_INTEL_RISK", out)
                self.assertNotIn("UNBOUND_VALUATION_CATALYST", out)
                self.assertNotIn("UNBOUND_LATEST_NEWS", out)
                self.assertNotIn("UNBOUND_HISTORICAL_VALUATION", out)
                self.assertNotIn("UNBOUND_BEARISH_SIGNAL", out)
                self.assertNotIn("| 支撑位 | 1399.0 |", out)
                self.assertNotIn("| 压力位 | 1499.0 |", out)

    @mock.patch("src.notification.get_config")
    def test_asset_canonical_without_investor_brief_fails_closed_not_legacy(
        self, mock_get_config: mock.MagicMock
    ):
        result = _make_investor_brief_result()
        factor_decision = dict(result.dashboard["factor_decision"])
        factor_decision.pop("investor_brief")
        result.dashboard["factor_decision"] = factor_decision
        result.sentiment_score = 99
        result.trend_prediction = "LEGACY_STRONG_BULLISH"
        result.operation_advice = "LEGACY_BUY_NOW"

        for enabled in (False, True):
            with self.subTest(report_renderer_enabled=enabled):
                mock_get_config.return_value = _make_config(report_renderer_enabled=enabled)
                out = NotificationService().generate_dashboard_report(
                    [result],
                    report_date="2026-09-14",
                )
                self.assertIn("当前确定性证据摘要不可用，本次保持观望", out)
                self.assertNotIn("旧核心结论（不得出现）", out)
                self.assertNotIn("旧因子结论（不得重复）", out)
                self.assertNotIn("LEGACY_STRONG_BULLISH", out)
                self.assertNotIn("LEGACY_BUY_NOW", out)
                self.assertNotIn("评分 99", out)
                self.assertNotIn("### 🧭 投资者简报", out)

    @mock.patch("src.notification.get_config")
    def test_generate_single_stock_report_keeps_legacy_simple_format(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=True)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )

        with mock.patch("src.services.report_renderer.render") as mock_render:
            out = service.generate_single_stock_report(result)

        mock_render.assert_not_called()
        self.assertIn("贵州茅台", out)
        self.assertIn("600519", out)

    @mock.patch("src.notification.get_config")
    def test_generate_brief_report_shows_model_by_default(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
            model_used="gemini/gemini-2.5-flash",
        )

        out = service.generate_brief_report([result], report_date="2026-02-01")

        self.assertIn("*分析模型: gemini/gemini-2.5-flash*", out)

    @mock.patch("src.notification.get_config")
    def test_generate_dashboard_report_shows_model_by_default(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
            model_used="gemini/gemini-2.5-flash",
        )

        out = service.generate_dashboard_report([result], report_date="2026-02-01")

        self.assertIn("*分析模型：gemini/gemini-2.5-flash*", out)

    @mock.patch("src.notification.get_config")
    def test_generate_dashboard_report_shows_phase_decision_in_default_renderer(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="等待确认",
            dashboard={
                "core_conclusion": {"one_sentence": "等待确认"},
                "phase_decision": {
                    "action_window": "盘中跟踪",
                    "immediate_action": "等待确认",
                    "watch_conditions": ["放量突破"],
                    "next_check_time": "14:30",
                    "confidence_reason": "数据质量可用",
                    "data_limitations": ["quote: stale"],
                },
            },
        )

        out = service.generate_dashboard_report([result], report_date="2026-02-01")

        self.assertIn("盘中决策护栏", out)
        self.assertIn("盘中跟踪", out)
        self.assertIn("等待确认", out)
        self.assertIn("放量突破", out)
        self.assertIn("quote: stale", out)

    @mock.patch("src.notification.get_config")
    def test_generate_dashboard_report_skips_context_only_phase_decision_default_renderer(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="等待确认",
            dashboard={
                "core_conclusion": {"one_sentence": "等待确认"},
                "phase_decision": {
                    "phase_context": {"phase": "intraday", "market": "cn"},
                    "watch_conditions": [],
                    "data_limitations": [],
                },
            },
        )

        out = service.generate_dashboard_report([result], report_date="2026-02-01")

        self.assertNotIn("盘中决策护栏", out)

    @mock.patch("src.notification.get_config")
    def test_generate_dashboard_report_omits_decision_signal_excerpt_fallback(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = _attach_decision_signal_summary(AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        ))

        out = service.generate_dashboard_report([result], report_date="2026-02-01")

        self.assertNotIn("AI 决策信号", out)
        self.assertNotIn("理由: 技术面走弱", out)

    @mock.patch("src.notification.get_config")
    def test_generate_daily_report_omits_decision_signal_excerpt_fallback(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        result = _attach_decision_signal_summary(AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        ))

        for summary_only in (True, False):
            service = NotificationService()
            service._report_summary_only = summary_only
            out = service.generate_daily_report([result], report_date="2026-02-01")
            self.assertNotIn("AI 决策信号", out)
            self.assertNotIn("理由: 技术面走弱", out)

    @mock.patch("src.notification.get_config")
    def test_generate_wechat_dashboard_omits_decision_signal_excerpt_fallback(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        result = _attach_decision_signal_summary(AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        ))

        for summary_only in (True, False):
            service = NotificationService()
            service._report_summary_only = summary_only
            out = service.generate_wechat_dashboard([result])
            self.assertNotIn("AI 决策信号", out)
            self.assertNotIn("理由: 技术面走弱", out)

    @mock.patch("src.notification.get_config")
    def test_strategy_synthesis_legacy_shapes_are_safe_in_fallback_reports(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()

        for malformed in ("bad-shape", ["bad-shape"], 42, True):
            result = AnalysisResult(
                code="600519",
                name="贵州茅台",
                sentiment_score=50,
                trend_prediction="震荡",
                operation_advice="观望",
                report_language="zh",
                dashboard={
                    "core_conclusion": {"one_sentence": "测试"},
                    "intelligence": {},
                    "battle_plan": {},
                    "strategy_synthesis": malformed,
                },
            )

            markdown = service.generate_dashboard_report([result], report_date="2026-07-19")
            wechat = service.generate_wechat_dashboard([result])

            self.assertNotIn("多策略综合", markdown)
            self.assertNotIn("多策略综合", wechat)

        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=50,
            trend_prediction="震荡",
            operation_advice="观望",
            report_language="zh",
            dashboard={
                "core_conclusion": {"one_sentence": "测试"},
                "intelligence": {},
                "battle_plan": {},
                "strategy_synthesis": {
                    "final_signal": "hold",
                    "consensus_level": "insufficient",
                    "conflict_severity": "none",
                    "conflict_count": 0,
                    "supporting_skills": "bad-shape",
                    "opposing_skills": ["bad-shape"],
                    "conflicts": "bad-shape",
                    "summary_params": {"invalid_opinion_count": "3"},
                },
            },
        )

        markdown = service.generate_dashboard_report([result], report_date="2026-07-19")
        wechat = service.generate_wechat_dashboard([result])

        self.assertIn("另有 3 个策略解析失败", markdown)
        self.assertIn("另有 3 个策略解析失败", wechat)

    @mock.patch("src.notification.get_config")
    def test_generate_wechat_summary_omits_decision_signal_excerpt(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = _attach_decision_signal_summary(AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        ))

        out = service.generate_wechat_summary([result])

        self.assertNotIn("AI 决策信号", out)
        self.assertNotIn("动作: 卖出", out)

    @mock.patch("src.notification.get_config")
    def test_generate_dashboard_report_omits_decision_signal_excerpt_with_renderer(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=True)
        service = NotificationService()
        result = _attach_decision_signal_summary(AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        ))

        out = service.generate_dashboard_report([result], report_date="2026-02-01")

        self.assertNotIn("AI 决策信号", out)
        self.assertNotIn("理由: 技术面走弱", out)

    @mock.patch("src.notification.get_config")
    def test_aggregate_reports_show_compact_market_status_only(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )
        result.market_phase_summary = {
            "phase": "intraday",
            "market": "cn",
            "trigger_source": "portfolio",
            "is_partial_bar": True,
        }
        result.analysis_context_pack_overview = {
            "data_quality": {
                "level": "limited",
                "limitations": ["quote: stale", "news: missing", "portfolio_context: hidden"],
            }
        }
        result.raw_response = "raw context pack and prompt should not appear"

        out = service.generate_brief_report([result], report_date="2026-02-01")

        self.assertIn("市场状态：A股 · 盘中", out)
        self.assertNotIn("阶段：intraday", out)
        self.assertNotIn("触发来源：portfolio", out)
        self.assertNotIn("盘中数据提示", out)
        self.assertNotIn("数据质量: limited", out)
        self.assertNotIn("限制: quote: stale", out)
        self.assertNotIn("限制: news: missing", out)
        self.assertNotIn("portfolio_context: hidden", out)
        self.assertNotIn("raw context pack", out)
        self.assertNotIn("prompt", out.lower())

    @mock.patch("src.notification.get_config")
    def test_template_dashboard_report_uses_single_market_status_line(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=True)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )
        result.market_phase_summary = {
            "phase": "postmarket",
            "market": "cn",
            "trigger_source": "cli",
        }
        result.analysis_context_pack_overview = {
            "data_quality": {
                "level": "good",
                "limitations": ["technical: partial"],
            }
        }

        out = service.generate_dashboard_report([result], report_date="2026-02-01")

        self.assertIn("市场状态：A股 · 盘后", out)
        self.assertEqual(out.count("市场状态："), 1)
        self.assertNotIn("阶段：postmarket", out)
        self.assertNotIn("触发来源：cli", out)
        self.assertNotIn("数据质量: good", out)
        self.assertNotIn("technical: partial", out)

    @mock.patch("src.notification.get_config")
    def test_generated_reports_skip_phase_pack_excerpt_when_summary_missing(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )

        out = service.generate_brief_report([result], report_date="2026-02-01")

        self.assertNotIn("摘要来源", out)
        self.assertNotIn("评估器快照", out)

    @mock.patch("src.notification.get_config")
    def test_generate_dashboard_report_collapses_unavailable_chip_structure(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
            dashboard={
                "data_perspective": {
                    "chip_structure": {
                        "profit_ratio": "数据缺失，无法判断",
                        "avg_cost": "数据缺失，无法判断",
                        "concentration": "数据缺失，无法判断",
                        "chip_health": "数据缺失，无法判断",
                    }
                }
            },
        )

        out = service.generate_dashboard_report([result], report_date="2026-02-01")

        self.assertIn("**筹码**: 筹码分布未启用或数据源暂不可用，未纳入筹码判断。", out)
        self.assertEqual(out.count("数据缺失，无法判断"), 0)

    @mock.patch("src.notification.get_config")
    def test_generate_reports_hide_model_when_disabled(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(
            report_renderer_enabled=False,
            report_show_llm_model=False,
        )
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
            model_used="gemini/gemini-2.5-flash",
        )

        dashboard = service.generate_dashboard_report([result], report_date="2026-02-01")
        single = service.generate_single_stock_report(result)

        self.assertNotIn("分析模型", dashboard)
        self.assertNotIn("gemini/gemini-2.5-flash", dashboard)
        self.assertNotIn("分析模型", single)
        self.assertNotIn("gemini/gemini-2.5-flash", single)

    @mock.patch("src.notification.get_config")
    def test_generate_dashboard_report_localizes_english_fallback(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False, report_language="en")
        service = NotificationService()
        result = AnalysisResult(
            code="AAPL",
            name="Apple",
            sentiment_score=78,
            trend_prediction="Bullish",
            operation_advice="Buy",
            analysis_summary="Momentum remains constructive.",
            decision_type="buy",
            report_language="en",
            dashboard={
                "core_conclusion": {
                    "one_sentence": "Favor buying on pullbacks.",
                    "position_advice": {
                        "no_position": "Open a starter position.",
                        "has_position": "Hold and trail the stop.",
                    },
                },
                "battle_plan": {
                    "sniper_points": {
                        "ideal_buy": "180-182",
                        "stop_loss": "172",
                        "take_profit": "195",
                    }
                },
            },
        )

        out = service.generate_dashboard_report([result], report_date="2026-03-18")

        self.assertIn("Decision Dashboard", out)
        self.assertIn("Summary", out)
        self.assertIn("Action Levels", out)
        self.assertIn("Buy", out)

    @mock.patch("src.notification.get_config")
    def test_generate_dashboard_report_localizes_english_no_dashboard_fallback(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False, report_language="en")
        service = NotificationService()
        result = AnalysisResult(
            code="AAPL",
            name="Apple",
            sentiment_score=61,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="Wait for confirmation.",
            report_language="en",
            buy_reason="Momentum remains constructive.",
            risk_warning="Watch for a failed breakout.",
            ma_analysis="Price remains above MA20.",
            volume_analysis="Volume is steady.",
            news_summary="Product cycle remains supportive.",
        )

        out = service.generate_dashboard_report([result], report_date="2026-03-19")

        self.assertIn("Rationale", out)
        self.assertIn("Risk Warning", out)
        self.assertIn("Technicals", out)
        self.assertIn("Moving Averages", out)
        self.assertIn("Volume", out)
        self.assertIn("News Flow", out)
        self.assertNotIn("操作理由", out)
        self.assertNotIn("风险提示", out)
        self.assertNotIn("技术面", out)
        self.assertNotIn("消息面", out)

    @mock.patch("src.notification.get_config")
    def test_generate_single_stock_report_aligns_english_fallback_with_score(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False, report_language="en")
        service = NotificationService()
        result = AnalysisResult(
            code="AAPL",
            name="Apple",
            sentiment_score=65,
            trend_prediction="Sideways",
            operation_advice="Hold",
            analysis_summary="Wait for a cleaner breakout.",
            report_language="en",
            dashboard={
                "core_conclusion": {"one_sentence": "Wait for confirmation."},
                "battle_plan": {
                    "sniper_points": {
                        "ideal_buy": "190",
                        "stop_loss": "182",
                        "take_profit": "205",
                    }
                },
            },
        )

        out = service.generate_single_stock_report(result)

        self.assertIn("Core Conclusion", out)
        self.assertIn("Action Levels", out)
        self.assertIn("Buy", out)

    @mock.patch("src.notification.get_config")
    def test_single_stock_share_image_reads_trend_from_generated_report(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="回调到支撑区可分批关注。",
            dashboard={
                "core_conclusion": {"one_sentence": "回调到支撑区可分批关注。"},
                "battle_plan": {
                    "sniper_points": {
                        "ideal_buy": "1420-1450",
                        "stop_loss": "1350",
                        "take_profit": "1580",
                    }
                },
            },
        )

        markdown = service.generate_single_stock_report(result)
        html = build_share_image_html(markdown, generated_on=date(2026, 7, 31))

        self.assertIn('class="signal-trend positive"', html)
        self.assertIn(">看多<", html)
        self.assertIn("个股决策卡", html)

    @mock.patch("src.notification.get_config")
    def test_dashboard_share_image_reads_chinese_volume_line_from_generated_report(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健持有。",
            dashboard={
                "core_conclusion": {"one_sentence": "稳健持有。"},
                "data_perspective": {
                    "trend_status": {
                        "ma_alignment": "多头排列",
                        "is_bullish": True,
                        "trend_score": 78,
                    },
                    "price_position": {
                        "current_price": "1450",
                        "ma5": "1442",
                        "ma10": "1430",
                        "ma20": "1408",
                        "bias_ma5": "0.55",
                        "bias_status": "偏强",
                        "support_level": "1420",
                        "resistance_level": "1490",
                    },
                    "volume_analysis": {
                        "volume_ratio": "1.35",
                        "volume_status": "放量",
                        "turnover_rate": "0.82",
                        "volume_meaning": "量能配合上攻",
                    },
                },
                "battle_plan": {
                    "sniper_points": {
                        "ideal_buy": "1420-1450",
                        "stop_loss": "1350",
                        "take_profit": "1580",
                    }
                },
            },
        )

        markdown = service.generate_dashboard_report([result], report_date="2026-07-31")
        html = build_share_image_html(markdown, generated_on=date(2026, 7, 31))

        self.assertIn("技术参考", html)
        self.assertIn(">量能<", html)
        self.assertIn("1.35", html)
        self.assertIn("0.82%", html)

    def _make_fundamental_context(self) -> dict:
        return {
            "earnings": {
                "status": "ok",
                "data": {
                    "financial_report": {
                        "report_date": "2024-09-30",
                        "revenue": 1_236_000_000_000.0,  # 1.236 万亿 -> 12360.00 亿元
                        "net_profit_parent": 60_800_000_000.0,
                        "operating_cash_flow": 72_500_000_000.0,
                        "roe": 22.45,
                    },
                    "dividend": {
                        "events": [
                            {
                                "event_date": "2024-06-26",
                                "ex_dividend_date": "2024-06-26",
                                "cash_dividend_per_share": 30.876,
                                "is_pre_tax": True,
                            }
                        ],
                        "ttm_event_count": 1,
                        "ttm_cash_dividend_per_share": 30.876,
                        "ttm_dividend_yield_pct": 1.85,
                    },
                },
            },
            "growth": {
                "status": "ok",
                "data": {
                    "revenue_yoy": 15.23,
                    "net_profit_yoy": 19.87,
                    "roe": 22.45,
                    "gross_margin": 91.55,
                },
            },
            "boards": {
                "status": "ok",
                "data": {
                    "top": [
                        {"name": "白酒", "change_pct": 3.42},
                        {"name": "食品饮料", "change_pct": 2.10},
                    ],
                    "bottom": [
                        {"name": "光伏设备", "change_pct": -2.65},
                    ],
                },
            },
            "concept_boards": {
                "status": "ok",
                "data": {
                    "top": [
                        {"name": "MSCI中国", "change_pct": 1.23},
                    ],
                    "bottom": [],
                },
            },
            "belong_boards": [
                {"name": "白酒", "code": "BK0596", "type": "行业"},
                {"name": "MSCI中国", "code": "BK0805", "type": "概念"},
            ],
        }

    @mock.patch("src.notification.get_config")
    def test_generate_single_stock_report_appends_fundamental_blocks(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )
        result.fundamental_context = self._make_fundamental_context()

        out = service.generate_single_stock_report(result)

        # 财务摘要
        self.assertIn("财务摘要", out)
        self.assertIn("2024-09-30", out)
        self.assertIn("12360.00 亿元", out)
        self.assertIn("22.45%", out)
        self.assertIn("15.23%", out)
        self.assertIn("91.55%", out)
        # 股东回报
        self.assertIn("股东回报", out)
        self.assertIn("30.8760 元", out)
        self.assertIn("1.85%", out)
        self.assertIn("2024-06-26", out)
        # 关联板块（白酒带行业信号；MSCI中国 带概念信号）
        self.assertIn("关联板块", out)
        self.assertIn("白酒", out)
        self.assertIn("领涨", out)
        self.assertIn("+3.42%", out)
        self.assertIn("MSCI中国", out)
        self.assertIn("- 白酒 (行业板块 领涨 +3.42%)", out)
        self.assertIn("- MSCI中国 (概念板块 领涨 +1.23%)", out)
        self.assertIn("+1.23%", out)
        self.assertNotIn("| 板块 | 类型 | 板块表现 | 板块涨跌幅 |", out)

    @mock.patch("src.notification.get_config")
    def test_related_boards_uses_concept_rankings_for_concept_boards(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )
        result.fundamental_context = {
            "boards": {"status": "ok", "data": {
                "top": [{"name": "白酒", "change_pct": 2.31}],
                "bottom": [],
            }},
            "concept_boards": {"status": "ok", "data": {
                "top": [],
                "bottom": [{"name": "白酒", "change_pct": -3.2}],
            }},
            "belong_boards": [{"name": "白酒", "type": "概念"}],
        }

        out = service.generate_single_stock_report(result)

        self.assertIn("关联板块", out)
        self.assertIn("- 白酒 (概念板块 领跌 -3.20%)", out)
        self.assertNotIn("| 白酒 | 概念 |", out)
        self.assertNotIn("+2.31%", out)

    @mock.patch("src.notification.get_config")
    def test_generate_single_stock_report_skips_fundamental_blocks_when_missing(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )

        out = service.generate_single_stock_report(result)

        self.assertNotIn("财务摘要", out)
        self.assertNotIn("股东回报", out)
        self.assertNotIn("关联板块", out)

    @mock.patch("src.notification.get_config")
    def test_generate_single_stock_report_handles_partial_fundamental_context(
        self, mock_get_config: mock.MagicMock
    ):
        """Only dividend data present — render shareholder return, skip the other two."""
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )
        result.fundamental_context = {
            "earnings": {
                "status": "partial",
                "data": {
                    "dividend": {
                        "events": [],
                        "ttm_event_count": 0,
                        "ttm_cash_dividend_per_share": 0.5,
                    }
                },
            },
        }

        out = service.generate_single_stock_report(result)

        self.assertNotIn("财务摘要", out)
        self.assertIn("股东回报", out)
        self.assertIn("0.5000 元", out)
        self.assertNotIn("关联板块", out)

    @mock.patch("src.notification.get_config")
    def test_generate_single_stock_report_uses_currency_for_us(
        self, mock_get_config: mock.MagicMock
    ):
        """USD currency on financial_report yields 亿美元 suffix instead of 亿元."""
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="AAPL",
            name="Apple Inc.",
            sentiment_score=64,
            trend_prediction="震荡",
            operation_advice="观望",
            analysis_summary="观望等待 AI 兑现节奏。",
        )
        result.fundamental_context = {
            "earnings": {
                "status": "ok",
                "data": {
                    "financial_report": {
                        "report_date": "2026-03-31",
                        "revenue": 1.11e11,
                        "net_profit_parent": 2.95e10,
                        "operating_cash_flow": 2.87e10,
                        "roe": 141.47,
                        "currency": "USD",
                    },
                    "dividend": {
                        "events": [{
                            "event_date": "2026-05-11",
                            "ex_dividend_date": "2026-05-11",
                            "cash_dividend_per_share": 0.27,
                            "is_pre_tax": True,
                        }],
                        "ttm_event_count": 4,
                        "ttm_cash_dividend_per_share": 1.05,
                        "ttm_dividend_yield_pct": 0.36,
                    },
                },
            },
            "growth": {"status": "ok", "data": {"revenue_yoy": 16.60, "roe": 141.47, "gross_margin": 47.86}},
            "belong_boards": [
                {"name": "Technology", "type": "行业"},
                {"name": "Consumer Electronics", "type": "概念"},
            ],
        }

        out = service.generate_single_stock_report(result)

        self.assertIn("财务摘要", out)
        self.assertIn("亿美元", out)
        self.assertNotIn("12360.00 亿元", out)
        # Sample expected formatted values
        self.assertIn("1110.00 亿美元", out)
        self.assertIn("141.47%", out)
        # Dividend per share also picks up currency suffix
        self.assertIn("1.0500 美元", out)
        # Sector + industry render as belong_boards
        self.assertIn("Technology", out)
        self.assertIn("Consumer Electronics", out)

    @mock.patch("src.notification.get_config")
    def test_related_boards_drops_signal_columns_when_no_sector_data(
        self, mock_get_config: mock.MagicMock
    ):
        """HK/US lack 板块涨跌榜 — drop status / change_pct columns entirely."""
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="AAPL",
            name="Apple Inc.",
            sentiment_score=64,
            trend_prediction="震荡",
            operation_advice="观望",
            analysis_summary="观望等待 AI 兑现节奏。",
        )
        result.fundamental_context = {
            "earnings": {"status": "ok", "data": {
                "financial_report": {
                    "report_date": "2026-03-31",
                    "revenue": 1.11e11,
                    "currency": "USD",
                },
            }},
            "growth": {"status": "ok", "data": {"revenue_yoy": 16.60}},
            "belong_boards": [
                {"name": "Technology", "type": "行业"},
                {"name": "Consumer Electronics", "type": "概念"},
            ],
        }

        out = service.generate_single_stock_report(result)

        self.assertIn("关联板块", out)
        self.assertIn("Technology / Consumer Electronics", out)
        self.assertIn("Technology", out)
        self.assertIn("Consumer Electronics", out)
        # When no sector ranking data is available, drop the 4-col layout.
        self.assertNotIn("板块表现", out)
        self.assertNotIn("板块涨跌幅", out)
        # And no table/type noise either.
        self.assertNotIn("| 板块 | 类型 |", out)
        self.assertNotIn("| -- | -- |", out)

    @mock.patch("src.notification.get_config")
    def test_related_boards_without_type_infers_concepts_and_keeps_unknowns(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )
        result.fundamental_context = {
            "earnings": {"status": "ok", "data": {}},
            "growth": {"status": "ok", "data": {}},
            "belong_boards": [
                {"name": "白酒Ⅲ"},
                {"name": "白酒Ⅱ"},
                {"name": "食品饮料"},
                {"name": "贵州板块"},
                {"name": "酿酒概念"},
            ],
        }

        out = service.generate_single_stock_report(result)

        self.assertIn("关联板块", out)
        self.assertIn("白酒Ⅲ / 白酒Ⅱ / 食品饮料 / 贵州板块 / 酿酒概念", out)
        self.assertNotIn("| 板块 | 类型 |", out)
        self.assertNotIn("| 白酒Ⅲ | N/A |", out)

    @mock.patch("src.notification.get_config")
    def test_related_boards_renders_each_board_signal_without_placeholder(
        self, mock_get_config: mock.MagicMock
    ):
        """Rows without a matching change_pct stay as plain board entries."""
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )
        result.fundamental_context = {
            "earnings": {"status": "ok", "data": {}},
            "growth": {"status": "ok", "data": {}},
            "boards": {"status": "ok", "data": {
                "top": [{"name": "白酒", "change_pct": 3.42}],
                "bottom": [],
            }},
            "belong_boards": [
                {"name": "白酒", "code": "BK0596", "type": "行业"},
                {"name": "MSCI中国", "code": "BK0805", "type": "概念"},
            ],
        }

        out = service.generate_single_stock_report(result)

        self.assertNotIn("板块表现", out)
        self.assertNotIn("板块涨跌幅", out)
        self.assertIn("领涨", out)
        self.assertIn("+3.42%", out)
        self.assertIn("- 白酒 (行业板块 领涨 +3.42%)", out)
        self.assertIn("MSCI中国", out)
        self.assertIn("- MSCI中国", out)
        self.assertNotIn("- MSCI中国 (", out)
        self.assertNotIn("| MSCI中国 | 概念 | -- | -- |", out)

    @mock.patch("src.notification.get_config")
    def test_related_boards_ignores_matching_signal_without_change_pct(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )
        result.fundamental_context = {
            "earnings": {"status": "ok", "data": {}},
            "growth": {"status": "ok", "data": {}},
            "boards": {"status": "ok", "data": {
                "top": [{"name": "白酒"}],
                "bottom": [],
            }},
            "belong_boards": [
                {"name": "白酒", "code": "BK0596", "type": "行业"},
            ],
        }

        out = service.generate_single_stock_report(result)

        self.assertIn("关联板块", out)
        self.assertIn("白酒", out)
        self.assertNotIn("| 白酒 | 行业 |", out)
        self.assertNotIn("领涨", out)
        self.assertNotIn("板块涨跌幅", out)

    @mock.patch("src.notification.get_config")
    def test_generate_single_stock_report_uses_currency_for_hk(
        self, mock_get_config: mock.MagicMock
    ):
        """HK ADRs have financialCurrency=CNY but trade/pay dividends in HKD.

        The financial summary must render in 元 (CNY income statement) while
        dividends must render in 港元 — they are NOT the same currency on
        yfinance HK payloads, so the renderer must read each block's own
        ``currency`` field rather than assuming a single global currency.
        """
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="HK09988",
            name="阿里巴巴-W",
            sentiment_score=68,
            trend_prediction="看多",
            operation_advice="逢低买入",
            analysis_summary="云业务回正，回购持续。",
        )
        result.fundamental_context = {
            "earnings": {
                "status": "ok",
                "data": {
                    "financial_report": {
                        "report_date": "2026-03-31",
                        "revenue": 1.02e12,
                        "net_profit_parent": 1.04e11,
                        "operating_cash_flow": 3.6e10,
                        "roe": 9.22,
                        "currency": "CNY",
                    },
                    "dividend": {
                        "events": [{
                            "event_date": "2025-06-11",
                            "ex_dividend_date": "2025-06-11",
                            "cash_dividend_per_share": 1.95812,
                            "is_pre_tax": True,
                        }],
                        "ttm_event_count": 1,
                        "ttm_cash_dividend_per_share": 1.95812,
                        "ttm_dividend_yield_pct": 1.75,
                        "currency": "HKD",
                    },
                },
            },
            "growth": {"status": "ok", "data": {"revenue_yoy": 2.9, "roe": 9.22, "gross_margin": 39.81}},
            "belong_boards": [
                {"name": "Consumer Cyclical", "type": "行业"},
                {"name": "Internet Retail", "type": "概念"},
            ],
        }

        out = service.generate_single_stock_report(result)

        # Income statement still rendered in CNY (financialCurrency).
        self.assertIn("10200.00 亿元", out)
        # Dividend per share follows the dividend currency, NOT the financial currency.
        self.assertIn("1.9581 港元", out)
        self.assertNotIn("1.9581 元 ", out)
        self.assertIn("Consumer Cyclical", out)

    @mock.patch("src.notification.get_config")
    def test_dividend_currency_falls_back_to_financial_when_missing(
        self, mock_get_config: mock.MagicMock
    ):
        """A-share AkShare payload has no dividend.currency — fall back to financial_report.currency."""
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
        )
        result.fundamental_context = {
            "earnings": {
                "status": "ok",
                "data": {
                    "financial_report": {"report_date": "2026-03-31", "revenue": 4.5e10},
                    "dividend": {
                        "events": [{"event_date": "2025-06-20", "ex_dividend_date": "2025-06-20"}],
                        "ttm_event_count": 1,
                        "ttm_cash_dividend_per_share": 27.6,
                    },
                },
            },
        }

        out = service.generate_single_stock_report(result)

        # Without explicit dividend currency, default to 元 (matches AkShare A-share semantics).
        self.assertIn("27.6000 元", out)

    @mock.patch("src.notification.get_config")
    def test_generate_dashboard_report_appends_fundamental_blocks(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(report_renderer_enabled=False)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
            dashboard={
                "core_conclusion": {"one_sentence": "稳健持有"},
                "battle_plan": {
                    "sniper_points": {
                        "ideal_buy": "1600",
                        "stop_loss": "1500",
                        "take_profit": "1800",
                    }
                },
            },
        )
        result.fundamental_context = self._make_fundamental_context()

        out = service.generate_dashboard_report([result], report_date="2026-05-20")

        self.assertIn("财务摘要", out)
        self.assertIn("股东回报", out)
        self.assertIn("关联板块", out)
        self.assertIn("白酒", out)
        self.assertIn("领涨", out)

    @mock.patch("src.notification.get_config")
    def test_history_compare_context_uses_cache(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(report_history_compare_n=3)
        service = NotificationService()
        result = AnalysisResult(
            code="600519",
            name="贵州茅台",
            sentiment_score=72,
            trend_prediction="看多",
            operation_advice="持有",
            analysis_summary="稳健",
            query_id="q-1",
        )

        with mock.patch(
            "src.services.history_comparison_service.get_signal_changes_batch",
            return_value={"600519": []},
        ) as mock_batch:
            first = service._get_history_compare_context([result])
            second = service._get_history_compare_context([result])

        self.assertEqual(first, {"history_by_code": {"600519": []}})
        self.assertEqual(second, {"history_by_code": {"600519": []}})
        mock_batch.assert_called_once()

    @mock.patch("src.notification.get_config")
    @mock.patch("smtplib.SMTP_SSL")
    def test_send_to_email_via_notification_service(
        self, mock_smtp_ssl: mock.MagicMock, mock_get_config: mock.MagicMock
    ):
        cfg = _make_config(
            email_sender="user@qq.com",
            email_password="PASS",
            email_receivers=["default@example.com"],
        )
        mock_get_config.return_value = cfg

        service = NotificationService()
        self.assertIn(NotificationChannel.EMAIL, service.get_available_channels())

        ok = service.send("email content")

        self.assertTrue(ok)
        mock_smtp_ssl.assert_called_once()
        mock_smtp_ssl.return_value.send_message.assert_called_once()

    @mock.patch("src.notification.get_config")
    @mock.patch("smtplib.SMTP_SSL")
    def test_send_to_email_via_notification_service_strips_hidden_market_metadata(
        self, mock_smtp_ssl: mock.MagicMock, mock_get_config: mock.MagicMock
    ):
        cfg = _make_config(
            email_sender="user@qq.com",
            email_password="PASS",
            email_receivers=["default@example.com"],
        )
        mock_get_config.return_value = cfg

        service = NotificationService()

        ok = service.send("[dsa-market-region]: # (cn)\n\n# 🎯 Market Review\n\nBody")

        self.assertTrue(ok)
        msg = mock_smtp_ssl.return_value.send_message.call_args[0][0]
        payloads = msg.get_payload()
        self.assertNotIn("[dsa-market-region]", payloads[0].get_payload(decode=True).decode("utf-8"))
        self.assertNotIn("[dsa-market-region]", payloads[1].get_payload(decode=True).decode("utf-8"))

    @mock.patch("src.notification.get_config")
    def test_email_text_fallback_strips_hidden_market_metadata_before_sender(
        self, mock_get_config: mock.MagicMock
    ):
        cfg = _make_config(
            email_sender="user@qq.com",
            email_password="PASS",
            email_receivers=["default@example.com"],
        )
        mock_get_config.return_value = cfg

        service = NotificationService()
        service.send_to_email = mock.MagicMock(return_value=True)

        ok = service._send_to_static_channel(
            NotificationChannel.EMAIL,
            "[dsa-market-region]: # (cn)\n\n# 🎯 Market Review\n\nBody",
            image_bytes=None,
            email_stock_codes=None,
            email_send_to_all=False,
            route_type="report",
        )

        self.assertTrue(ok)
        service.send_to_email.assert_called_once_with(
            "# 🎯 Market Review\n\nBody",
            receivers=None,
        )

    @mock.patch("src.notification.get_config")
    def test_static_text_channels_strip_hidden_market_metadata_before_sender(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config()
        service = NotificationService()
        raw_content = "[dsa-market-region]: # (cn)\n\n# 🎯 Market Review\n\nBody"
        expected = "# 🎯 Market Review\n\nBody"
        cases = [
            (NotificationChannel.FEISHU, "send_to_feishu"),
            (NotificationChannel.DINGTALK, "send_to_dingtalk"),
            (NotificationChannel.NTFY, "send_to_ntfy"),
            (NotificationChannel.GOTIFY, "send_to_gotify"),
            (NotificationChannel.PUSHPLUS, "send_to_pushplus"),
            (NotificationChannel.SERVERCHAN3, "send_to_serverchan3"),
            (NotificationChannel.CUSTOM, "send_to_custom"),
            (NotificationChannel.ASTRBOT, "send_to_astrbot"),
        ]

        for channel, method_name in cases:
            with self.subTest(channel=channel.value), mock.patch.object(
                service, method_name, return_value=True
            ) as mock_sender:
                ok = service._send_to_static_channel(
                    channel,
                    raw_content,
                    image_bytes=None,
                    email_stock_codes=None,
                    email_send_to_all=False,
                    route_type="report",
                )

                self.assertTrue(ok)
                mock_sender.assert_called_once_with(expected)

    @mock.patch("src.notification.get_config")
    @mock.patch("smtplib.SMTP_SSL")
    def test_send_to_email_with_stock_group_routing(
        self, mock_smtp_ssl: mock.MagicMock, mock_get_config: mock.MagicMock
    ):
        cfg = _make_config(
            email_sender="user@qq.com",
            email_password="PASS",
            email_receivers=["default@example.com"],
            stock_email_groups=[(["000001", "600519"], ["group@example.com"])],
        )
        mock_get_config.return_value = cfg

        service = NotificationService()
        self.assertIn(NotificationChannel.EMAIL, service.get_available_channels())

        server = mock_smtp_ssl.return_value

        ok = service.send("content", email_stock_codes=["000001"])

        self.assertTrue(ok)
        mock_smtp_ssl.assert_called_once()
        server.send_message.assert_called_once()
        msg = server.send_message.call_args[0][0]
        self.assertIn("group@example.com", msg["To"])

    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_feishu_via_notification_service(self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock):
        cfg = _make_config(feishu_webhook_url="https://feishu.example")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200, {"code": 0})

        service = NotificationService()
        self.assertIn(NotificationChannel.FEISHU, service.get_available_channels())

        ok = service.send("hello feishu")

        self.assertTrue(ok)
        mock_post.assert_called_once()
        
    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_feishu_via_notification_service_requires_chunking(self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock):
        cfg = _make_config(feishu_webhook_url="https://feishu.example", feishu_max_bytes=2000)
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200, {"code": 0})

        service = NotificationService()
        self.assertIn(NotificationChannel.FEISHU, service.get_available_channels())

        ok = service.send("A" * 6000)

        self.assertTrue(ok)
        self.assertAlmostEqual(mock_post.call_count, 4, delta=1)

    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_gotify_via_notification_service(
        self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock
    ):
        cfg = _make_config(gotify_url="https://gotify.example", gotify_token="secret-token")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200)

        service = NotificationService()
        self.assertIn(NotificationChannel.GOTIFY, service.get_available_channels())

        ok = service.send("gotify content")

        self.assertTrue(ok)
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args.args[0], "https://gotify.example/message")
        self.assertEqual(mock_post.call_args.kwargs["headers"]["X-Gotify-Key"], "secret-token")
        self.assertEqual(mock_post.call_args.kwargs["json"]["message"], "gotify content")
        self.assertEqual(
            mock_post.call_args.kwargs["json"]["extras"]["client::display"]["contentType"],
            "text/markdown",
        )

    @mock.patch("src.notification.get_config")
    def test_gotify_without_token_is_not_available(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(gotify_url="https://gotify.example")

        service = NotificationService()

        self.assertNotIn(NotificationChannel.GOTIFY, service.get_available_channels())
        self.assertFalse(service.is_available())

    @mock.patch("src.notification.get_config")
    def test_gotify_blank_token_is_not_available(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(
            gotify_url="https://gotify.example",
            gotify_token="   ",
        )

        service = NotificationService()

        self.assertNotIn(NotificationChannel.GOTIFY, service.get_available_channels())
        self.assertFalse(service.is_available())

    @mock.patch("src.notification.get_config")
    def test_gotify_message_endpoint_is_not_available(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(
            gotify_url="https://gotify.example/message",
            gotify_token="secret-token",
        )

        service = NotificationService()

        self.assertNotIn(NotificationChannel.GOTIFY, service.get_available_channels())
        self.assertFalse(service.is_available())

    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_gotify_does_not_trigger_markdown_to_image(
        self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock
    ):
        cfg = _make_config(
            gotify_url="https://gotify.example",
            gotify_token="secret-token",
            markdown_to_image_channels=["gotify"],
        )
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200)

        service = NotificationService()
        with mock.patch("src.md2img.markdown_to_image", return_value=b"png") as mock_md2img:
            ok = service.send("gotify content")

        self.assertTrue(ok)
        mock_md2img.assert_not_called()
        mock_post.assert_called_once()

    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_ntfy_via_notification_service(
        self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock
    ):
        cfg = _make_config(ntfy_url="https://ntfy.sh/dsa-topic")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200)

        service = NotificationService()
        self.assertIn(NotificationChannel.NTFY, service.get_available_channels())

        ok = service.send("ntfy content")

        self.assertTrue(ok)
        mock_post.assert_called_once()
        self.assertEqual(mock_post.call_args.args[0], "https://ntfy.sh")
        self.assertEqual(mock_post.call_args.kwargs["json"]["topic"], "dsa-topic")

    @mock.patch("src.notification.get_config")
    def test_ntfy_url_without_topic_is_not_available(self, mock_get_config: mock.MagicMock):
        mock_get_config.return_value = _make_config(ntfy_url="https://ntfy.sh")

        service = NotificationService()

        self.assertNotIn(NotificationChannel.NTFY, service.get_available_channels())
        self.assertFalse(service.is_available())

    @mock.patch("src.notification.get_config")
    def test_ntfy_url_with_unsupported_scheme_is_not_available(
        self, mock_get_config: mock.MagicMock
    ):
        mock_get_config.return_value = _make_config(ntfy_url="ntfy://ntfy.sh/dsa-topic")

        service = NotificationService()

        self.assertNotIn(NotificationChannel.NTFY, service.get_available_channels())
        self.assertFalse(service.is_available())

    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_ntfy_does_not_trigger_markdown_to_image(
        self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock
    ):
        cfg = _make_config(
            ntfy_url="https://ntfy.sh/dsa-topic",
            markdown_to_image_channels=["ntfy"],
        )
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200)

        service = NotificationService()
        with mock.patch("src.md2img.markdown_to_image", return_value=b"png") as mock_md2img:
            ok = service.send("ntfy content")

        self.assertTrue(ok)
        mock_md2img.assert_not_called()
        mock_post.assert_called_once()

    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_pushover_via_notification_service(
        self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock
    ):
        cfg = _make_config(
            pushover_user_key="USER",
            pushover_api_token="TOKEN",
        )
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200, {"status": 1})

        service = NotificationService()
        self.assertIn(NotificationChannel.PUSHOVER, service.get_available_channels())

        ok = service.send("pushover content")

        self.assertTrue(ok)
        mock_post.assert_called_once()

    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_pushplus_via_notification_service(
        self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock
    ):
        cfg = _make_config(pushplus_token="TOKEN")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200, {"code": 200})

        service = NotificationService()
        self.assertIn(NotificationChannel.PUSHPLUS, service.get_available_channels())

        ok = service.send("pushplus content")

        self.assertTrue(ok)
        mock_post.assert_called_once()

    @mock.patch("src.notification_sender.pushplus_sender.time.sleep")
    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_pushplus_via_notification_service_requires_chunking(
        self,
        mock_post: mock.MagicMock,
        mock_get_config: mock.MagicMock,
        _mock_sleep: mock.MagicMock,
    ):
        cfg = _make_config(pushplus_token="TOKEN")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200, {"code": 200})

        service = NotificationService()
        self.assertIn(NotificationChannel.PUSHPLUS, service.get_available_channels())

        ok = service.send("A" * 25000)

        self.assertTrue(ok)
        self.assertGreaterEqual(mock_post.call_count, 2)

    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_slack_via_notification_service_with_webhook(
        self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock
    ):
        cfg = _make_config(slack_webhook_url="https://hooks.slack.com/services/T/B/xxx")
        mock_get_config.return_value = cfg
        resp = mock.MagicMock()
        resp.status_code = 200
        resp.text = "ok"
        mock_post.return_value = resp

        service = NotificationService()
        self.assertIn(NotificationChannel.SLACK, service.get_available_channels())

        ok = service.send("slack content")

        self.assertTrue(ok)
        mock_post.assert_called_once()

    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_slack_via_notification_service_with_bot(
        self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock
    ):
        cfg = _make_config(slack_bot_token="xoxb-test", slack_channel_id="C123")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200, {"ok": True})

        service = NotificationService()
        self.assertIn(NotificationChannel.SLACK, service.get_available_channels())

        ok = service.send("slack bot content")

        self.assertTrue(ok)
        mock_post.assert_called_once()

    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_serverchan3_via_notification_service(
        self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock
    ):
        cfg = _make_config(serverchan3_sendkey="SCTKEY")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200, {"code": 0})

        service = NotificationService()
        self.assertIn(NotificationChannel.SERVERCHAN3, service.get_available_channels())

        ok = service.send("serverchan content")

        self.assertTrue(ok)
        mock_post.assert_called_once()

    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_telegram_via_notification_service(
        self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock
    ):
        cfg = _make_config(telegram_bot_token="TOKEN", telegram_chat_id="123")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200, {"ok": True})

        service = NotificationService()
        self.assertIn(NotificationChannel.TELEGRAM, service.get_available_channels())

        ok = service.send("hello telegram")

        self.assertTrue(ok)
        mock_post.assert_called_once()

    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_wechat_via_notification_service(self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock):
        cfg = _make_config(wechat_webhook_url="https://wechat.example")
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200, {"errcode": 0})

        service = NotificationService()
        self.assertIn(NotificationChannel.WECHAT, service.get_available_channels())

        ok = service.send("hello wechat")

        self.assertTrue(ok)
        mock_post.assert_called_once()

    @mock.patch("src.notification.get_config")
    @mock.patch("requests.post")
    def test_send_to_wechat_via_notification_service_requires_chunking(self, mock_post: mock.MagicMock, mock_get_config: mock.MagicMock):
        cfg = _make_config(wechat_webhook_url="https://wechat.example", wechat_max_bytes=2000)
        mock_get_config.return_value = cfg
        mock_post.return_value = _make_response(200, {"errcode": 0})

        service = NotificationService()
        self.assertIn(NotificationChannel.WECHAT, service.get_available_channels())

        ok = service.send("A" * 6000)

        self.assertTrue(ok)
        self.assertAlmostEqual(mock_post.call_count, 4, delta=1)


if __name__ == "__main__":
    unittest.main()
