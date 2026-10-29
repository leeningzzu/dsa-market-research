"""Fail-closed source-only research for index 5m via documented Eastmoney route.

Synthetic API responses only: real provider rights, historical PIT, and
recursive readiness are not inferred from a successful fetch or AKShare docs.
"""

from __future__ import annotations

from datetime import datetime, date, time, timedelta
import json
from zoneinfo import ZoneInfo

import pytest

from data_provider.cn_index_minute_source import (
    IndexMinuteSourceError,
    get_cn_index_completed_5m_source,
    verify_cn_index_completed_5m_source_frame,
)
from data_provider.intraday_data_identity import extract_intraday_data_identity
from src.services.intraday_bar_service import (
    aggregate_completed_intraday_bars,
    validate_complete_cn_5m_sessions,
)


_TARGET = date(2026, 10, 16)
_SHANGHAI = ZoneInfo("Asia/Shanghai")
_OBSERVED = datetime(2026, 10, 16, 19, 30, tzinfo=_SHANGHAI)


def _klines(
    days: tuple[date, ...] = (date(2026, 10, 15), date(2026, 10, 16)),
) -> list[str]:
    rows = []
    for day in days:
        for first in (time(9, 35), time(13, 5)):
            start = datetime.combine(day, first)
            for step in range(24):
                instant = start + timedelta(minutes=step * 5)
                # 11 fields, exactly the documented Eastmoney index kline
                # payload; numerical values are synthetic and never licensed.
                row = [
                    instant.strftime("%Y-%m-%d %H:%M"),
                    "3000.00", "3001.00", "3002.00", "2999.00",
                    "123", "456000", "0.1", "0", "1", "0",
                ]
                rows.append(",".join(row))
    return rows


class _Response:
    def __init__(self, klines, *, code="000001", market=1):
        self.payload = json.dumps(
            {"rc": 0, "data": {"code": code, "market": market, "klines": klines}},
        ).encode("utf-8")
        self.headers = {"content-type": "application/json; charset=UTF-8"}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, limit=-1):
        return self.payload[:limit]


def _read_fixture(monkeypatch, klines=None, *, code="000001", market=1, observed=_OBSERVED):
    calls = []

    def fetch(req, *, timeout):
        assert "push2his.eastmoney.com" in req.full_url
        assert "secid=1.000001" in req.full_url
        assert "klt=5" in req.full_url
        assert "fqt=0" in req.full_url
        assert timeout <= 25
        calls.append(req.full_url)
        return _Response(_klines() if klines is None else klines, code=code, market=market)

    monkeypatch.setattr("data_provider.cn_index_minute_source._source_urlopen", fetch)
    frame = get_cn_index_completed_5m_source(
        "sh.000001", target_date=_TARGET, observed_at=observed,
    )
    assert len(calls) == 1
    return frame


def test_source_5m_only_completed_exact_sessions_and_native_derived_frames(monkeypatch):
    frame = _read_fixture(monkeypatch)
    identity = extract_intraday_data_identity(frame, strict=True)
    assert identity["provider_identity"] == "EastmoneyIndex5mAdapter"
    assert identity["identity_state"] == "UNCLASSIFIED"
    assert identity["requested_adjustment_basis"] == "raw"
    assert identity["observed_adjustment_basis"] is None
    assert identity["volume_unit"] == identity["amount_unit"] == "UNKNOWN"
    assert identity["currency"] == "UNKNOWN"
    assert len(frame) == 96
    assert validate_complete_cn_5m_sessions(
        frame, expected_session_dates=[date(2026, 10, 15), date(2026, 10, 16)],
    )["session_count"] == 2
    for tf, count in (("15m", 32), ("30m", 16), ("60m", 8)):
        projected = aggregate_completed_intraday_bars(frame, target_timeframe=tf)
        assert len(projected) == count
        assert extract_intraday_data_identity(projected, strict=True)["parent_identity_hash"] == identity["identity_hash"]

    proof = verify_cn_index_completed_5m_source_frame(frame, trade_date=_TARGET.isoformat())
    assert proof["dataset_id"] == "M_INDEX_BARS"
    assert proof["source_state"] == "PARTIAL"
    assert proof["completed_through"] == _TARGET.isoformat()
    assert proof["session_count"] == 2
    assert proof["source_data_rights"] == "UNKNOWN"
    assert proof["historical_pit_eligible"] is False
    assert proof["strategy_upgrade_authority"] is False
    assert proof["independent_source_value_admitted"] is False
    assert proof["native_recursive_stability_admitted"] is False
    assert "close" not in proof and "score" not in proof and "BUY" not in repr(proof)


@pytest.mark.parametrize(
    "change",
    ["missing", "duplicate", "mixed_market", "mixed_code", "outside_session", "early_observation"],
)
def test_source_rejects_incomplete_or_malformed_market_index(monkeypatch, change):
    klines = _klines()
    code, market = "000001", 1
    observed = _OBSERVED
    if change == "missing":
        klines.pop()
    elif change == "duplicate":
        klines[-1] = klines[-2]
    elif change == "mixed_market":
        market = 0
    elif change == "mixed_code":
        code = "399001"
    elif change == "outside_session":
        klines[-1] = klines[-1].replace("15:00", "15:01")
    else:
        observed = datetime(2026, 10, 16, 14, 10, tzinfo=_SHANGHAI)
    with pytest.raises(IndexMinuteSourceError):
        _read_fixture(monkeypatch, klines, code=code, market=market, observed=observed)


def test_source_identity_cannot_survive_changed_prices_or_target_clock(monkeypatch):
    frame = _read_fixture(monkeypatch)
    altered = frame.copy()
    altered.loc[0, "close"] += 100
    with pytest.raises(IndexMinuteSourceError):
        verify_cn_index_completed_5m_source_frame(altered, trade_date=_TARGET.isoformat())
    with pytest.raises(IndexMinuteSourceError):
        verify_cn_index_completed_5m_source_frame(frame, trade_date="2026-10-15")
def test_m2_completed_index5m_enters_market_review_runtime_registry_and_full_text(monkeypatch):
    """Producer → MarketReview → strict REGIME → 76 obligations; no index vote."""
    from unittest.mock import MagicMock
    from src.services.daily_market_context import DailyMarketContextService
    from src.services.factor_decision_summary import build_stock_factor_decision_summary
    from src.services.evidence_traceability_registry import (
        compile_complete_research_universe_view,
    )
    from src.market_analyzer import MarketOverview
    from tests.test_market_analyzer_generate_text import TestMarketAnalyzerBypassFix
    from tests.test_factor_decision_summary import _trend

    frame = _read_fixture(monkeypatch)
    ma = TestMarketAnalyzerBypassFix()._make_market_analyzer_with_mock_generate_text()
    overview = MarketOverview(date=_TARGET.isoformat())
    market_light = {
        "region": "cn", "trade_date": _TARGET.isoformat(),
        "status": "yellow", "score": 45, "label": "谨慎",
        "temperature_label": "谨慎", "reasons": ["来源不完整"],
        "guidance": "等待独立宽度与行业确认",
        "dimensions": {
            "breadth": {"score": 50, "available": False},
            "index": {"score": 50, "available": True},
            "limit": {"score": 50, "available": False},
        },
        "data_quality": "partial",
    }
    base = ma.build_market_review_payload(
        overview, [], "## 大盘市场复盘\\n\\n全市场宽度未知、行业未知、反证仍保留。",
        market_light,
    )
    with_source = ma.build_market_review_payload(
        overview, [], "## 大盘市场复盘\\n\\n全市场宽度未知、行业未知、反证仍保留。",
        market_light,
        cn_index_completed_5m_source=frame,
    )
    rec = with_source["cn_index_completed_5m_source"]
    assert "M_INDEX_BARS" in with_source["markdown_report"]
    assert "PARTIAL" in with_source["markdown_report"]
    assert "全市场宽度未知、行业未知、反证仍保留" in with_source["markdown_report"]
    assert with_source["market_light"] == base["market_light"]
    assert rec["session_count"] == 2

    svc = DailyMarketContextService(
        db_manager=MagicMock(), today_fn=lambda: _TARGET,
    )
    runtime = svc._build_context_from_payload(
        region="cn", trade_date=_TARGET,
        payload={**with_source, "summary": "市场震荡，等待确认。"},
        source="market_review_runtime",
    )
    assert runtime is not None
    assert runtime.cn_index_completed_5m_source == rec
    assert runtime.to_safe_dict()["cn_index_completed_5m_source"] == rec
    restored = svc._build_context_from_payload(
        region="cn", trade_date=_TARGET,
        payload={**with_source, "summary": "市场震荡，等待确认。"},
        source="analysis_history",
    )
    assert restored.cn_index_completed_5m_source is None
    assert "cn_index_completed_5m_source" not in restored.to_safe_dict()

    signal = _trend(signal_score=82)
    def factor(context):
        return build_stock_factor_decision_summary(
            signal,
            daily_market_context=context,
            market_structure_context={
                "status": "not_supported", "market": "cn",
                "trade_date": _TARGET.isoformat(),
            },
            method_execution_receipts={},
            include_canonical=True,
        )
    current = factor(runtime)
    without = factor(svc._build_context_from_payload(
        region="cn", trade_date=_TARGET,
        payload={**base, "summary": "市场震荡，等待确认。"},
        source="market_review_runtime",
    ))
    assert current["canonical_decision"] == without["canonical_decision"]
    objects = {
        row["dataset_id"]: row
        for row in compile_complete_research_universe_view(current)["data_objects"]
    }
    assert len(objects) == 76
    assert objects["M_INDEX_BARS"]["state"] == "PARTIAL"
    assert objects["M_INDEX_BARS"]["provider_identity"] == "EastmoneyIndex5mAdapter"
    assert objects["M_INDEX_BARS"]["strategy_upgrade_authority"] is False
    assert objects["M_INDEX_BARS"]["independent_source_value_admitted"] is False
    assert objects["M_INDEX_CURRENT"]["state"] == "UNKNOWN"
    assert objects["M_BASIC_BREADTH"]["state"] == "UNKNOWN"


    # FULL investor report and required Email consume the same 76 data
    # obligations; no independent score is manufactured in presentation.
    from unittest.mock import patch
    from tests.test_notification import (
        _make_investor_brief_result, _make_config,
    )
    from src.services.factor_decision_summary import apply_canonical_decision_to_result
    from src.notification import NotificationService
    from src.enums import ReportType
    from src.formatters import markdown_to_html_document

    result = _make_investor_brief_result()
    result.dashboard["factor_decision"] = current
    apply_canonical_decision_to_result(result, current, scope="production")
    with patch(
        "src.notification.get_config",
        return_value=_make_config(
            report_renderer_enabled=False,
            report_history_compare_n=0,
        ),
    ):
        notifier = NotificationService()
        full = notifier.generate_aggregate_report(
            [result], ReportType.FULL, _TARGET.isoformat(),
        )
        email = notifier.generate_brief_report([result], _TARGET.isoformat())
    rendered = markdown_to_html_document(email)
    for dataset_id in objects:
        assert chr(96) + dataset_id + chr(96) in full
        assert chr(96) + dataset_id + chr(96) in email
    for output in (full, email, rendered):
        assert "M_INDEX_BARS" in output
        assert "INDEX_5M_COMPLETED_SOURCE_VERIFIED_RECURSIVE_METHOD_NOT_ADMITTED" in output
        assert "M_BASIC_BREADTH" in output
    assert current["canonical_decision"]["action"] == without["canonical_decision"]["action"]

def test_m2_forged_index5m_metadata_does_not_gain_market_rights(monkeypatch):
    from unittest.mock import MagicMock
    from src.services.daily_market_context import DailyMarketContextService

    frame = _read_fixture(monkeypatch)
    receipt = verify_cn_index_completed_5m_source_frame(
        frame, trade_date=_TARGET.isoformat(),
    )
    service = DailyMarketContextService(
        db_manager=MagicMock(), today_fn=lambda: _TARGET,
    )
    for forged in (
        {**receipt, "source_state": "READY"},
        {**receipt, "source_data_rights": "GRANTED"},
        {**receipt, "completed_through": "2026-10-15"},
        {**receipt, "native_recursive_stability_admitted": True},
        {**receipt, "session_count": 100},
        {**receipt, "identity_hash": "0" * 64},
    ):
        context = service._build_context_from_payload(
            region="cn", trade_date=_TARGET,
            payload={
                "summary": "市场风险未核",
                "cn_index_completed_5m_source": forged,
            },
            source="market_review_runtime",
        )
        assert context.cn_index_completed_5m_source is None

def test_m2_current_cn_review_actually_consumes_index_5m_source(monkeypatch):
    """Production MarketReview uses one strict source consumer; no second report."""
    from unittest.mock import patch
    from src.market_analyzer import MarketOverview
    from tests.test_market_analyzer_generate_text import TestMarketAnalyzerBypassFix

    frame = _read_fixture(monkeypatch)
    ma = TestMarketAnalyzerBypassFix()._make_market_analyzer_with_mock_generate_text()
    ma.region = "cn"
    overview = MarketOverview(date=_TARGET.isoformat())
    with (
        patch.object(ma, "get_market_overview", return_value=overview),
        patch.object(ma, "search_market_news", return_value=[]),
        patch.object(ma, "_merge_persisted_market_intelligence", return_value=[]),
        patch.object(
            ma, "generate_market_review",
            return_value="## A股复盘\n\n市场宽度、行业尚无独立来源。",
        ),
        patch.object(
            ma, "build_market_light_snapshot",
            return_value={"status": "yellow", "data_quality": "partial"},
        ),
        patch.object(ma, "_load_cn_index_completed_close_source", return_value=None),
        patch.object(ma, "_load_cn_index_completed_5m_source", return_value=frame) as loader,
    ):
        result = ma._run_daily_review_parts()
    loader.assert_called_once_with(overview)
    assert result.structured_payload["cn_index_completed_5m_source"]["source_state"] == "PARTIAL"
    assert "M_INDEX_BARS" in result.structured_payload["markdown_report"]
    assert "市场宽度、行业尚无独立来源" in result.structured_payload["markdown_report"]


def test_m2_completed_current_cn_review_reads_source_once_without_cache_laundering(monkeypatch):
    from unittest.mock import patch
    from src.market_analyzer import MarketOverview
    from tests.test_market_analyzer_generate_text import TestMarketAnalyzerBypassFix

    frame = _read_fixture(monkeypatch)
    ma = TestMarketAnalyzerBypassFix()._make_market_analyzer_with_mock_generate_text()
    ma.region = "cn"
    with (
        patch(
            "src.core.trading_calendar.resolve_latest_completed_session_fail_closed",
            return_value=_TARGET,
        ),
        patch(
            "data_provider.cn_index_minute_source.get_cn_index_completed_5m_source",
            return_value=frame,
        ) as read,
    ):
        actual = ma._load_cn_index_completed_5m_source(
            MarketOverview(date=_TARGET.isoformat()),
        )
    assert actual is frame
    read.assert_called_once()
    assert read.call_args.args == ("sh.000001",)
    assert read.call_args.kwargs["target_date"] == _TARGET
    assert read.call_args.kwargs["observed_at"].tzinfo is not None


def test_m2_index5m_historical_or_foreign_review_does_not_trigger_network():
    from unittest.mock import patch
    from src.market_analyzer import MarketOverview
    from tests.test_market_analyzer_generate_text import TestMarketAnalyzerBypassFix

    ma = TestMarketAnalyzerBypassFix()._make_market_analyzer_with_mock_generate_text()
    with patch(
        "data_provider.cn_index_minute_source.get_cn_index_completed_5m_source",
    ) as read:
        assert ma._load_cn_index_completed_5m_source(
            MarketOverview(date="2025-01-03"),
        ) is None
        ma.region = "us"
        assert ma._load_cn_index_completed_5m_source(
            MarketOverview(date="2026-10-16"),
        ) is None
        read.assert_not_called()


