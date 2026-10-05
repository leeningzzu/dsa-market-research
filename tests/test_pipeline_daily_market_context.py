# -*- coding: utf-8 -*-
"""Pipeline tests for Issue #1381 daily market context injection."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from data_provider.intraday_data_identity import (
    attach_intraday_data_identity,
    build_intraday_data_identity,
)

from src.analyzer import GeminiAnalyzer
from src.core.pipeline import StockAnalysisPipeline
from src.enums import ReportType
from src.services.daily_market_context import DailyMarketContext


def _pipeline_config(*, daily_market_context_enabled: bool) -> SimpleNamespace:
    return SimpleNamespace(
        max_workers=1,
        save_context_snapshot=False,
        bocha_api_keys=[],
        tavily_api_keys=[],
        anspire_api_keys=[],
        brave_api_keys=[],
        serpapi_keys=[],
        minimax_api_keys=[],
        searxng_base_urls=[],
        searxng_public_instances_enabled=False,
        news_max_age_days=3,
        news_strategy_profile="short",
        enable_realtime_quote=False,
        realtime_source_priority=[],
        enable_chip_distribution=False,
        social_sentiment_api_key="",
        social_sentiment_api_url="https://example.invalid/social",
        daily_market_context_enabled=daily_market_context_enabled,
    )


def _build_initialized_pipeline(
    config: SimpleNamespace,
    **kwargs,
) -> StockAnalysisPipeline:
    search_service = MagicMock()
    search_service.is_available = False
    social_sentiment_service = MagicMock()
    social_sentiment_service.is_available = False

    with patch("src.core.pipeline.get_db", return_value=MagicMock()), \
         patch("src.core.pipeline.DataFetcherManager", return_value=MagicMock()), \
         patch("src.core.pipeline.StockTrendAnalyzer", return_value=MagicMock()), \
         patch("src.core.pipeline.GeminiAnalyzer", return_value=MagicMock()), \
         patch("src.core.pipeline.NotificationService", return_value=MagicMock()), \
         patch("src.core.pipeline.SearchService", return_value=search_service), \
         patch("src.core.pipeline.SocialSentimentService", return_value=social_sentiment_service):
        return StockAnalysisPipeline(config=config, **kwargs)


def _market_context() -> DailyMarketContext:
    return DailyMarketContext(
        region="cn",
        trade_date=date(2026, 6, 6),
        summary="大盘退潮，高风险，建议观望，仓位上限30%。",
        risk_tags=["high_risk", "low_position_cap"],
        source="analysis_history",
    )


def test_pipeline_constructor_defaults_daily_context_flag_from_config() -> None:
    pipeline = _build_initialized_pipeline(
        _pipeline_config(daily_market_context_enabled=True)
    )

    assert pipeline.daily_market_context_enabled is True


def test_pipeline_constructor_keeps_config_disabled_by_default() -> None:
    pipeline = _build_initialized_pipeline(
        _pipeline_config(daily_market_context_enabled=False)
    )

    assert pipeline.daily_market_context_enabled is False


def test_pipeline_constructor_explicit_flag_overrides_config() -> None:
    pipeline = _build_initialized_pipeline(
        _pipeline_config(daily_market_context_enabled=True),
        daily_market_context_enabled=False,
    )

    assert pipeline.daily_market_context_enabled is False


def test_pipeline_loads_daily_market_context_when_market_review_enabled() -> None:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.config = SimpleNamespace(
        market_review_enabled=True,
        daily_market_context_enabled=True,
        report_language="zh",
    )
    pipeline.daily_market_context_enabled = True
    pipeline.db = MagicMock()
    pipeline.notifier = MagicMock()
    pipeline.analyzer = MagicMock()
    pipeline.search_service = MagicMock()
    pipeline.query_id = "pipeline-query"

    with patch("src.core.pipeline.DailyMarketContextService") as service_cls:
        service = service_cls.return_value
        service.get_context.return_value = _market_context()

        target_date = date(2026, 6, 6)

        context = pipeline._load_daily_market_context("cn", target_date=target_date)

    assert context is not None
    service_cls.assert_called_once_with(db_manager=pipeline.db)
    service.get_context.assert_called_once_with(
        region="cn",
        config=pipeline.config,
        notifier=pipeline.notifier,
        analyzer=pipeline.analyzer,
        search_service=pipeline.search_service,
        force_refresh=False,
        allow_generate=True,
        target_date=target_date,
        current_query_id="pipeline-query",
    )


def test_pipeline_can_load_daily_market_context_without_runtime_generation() -> None:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.config = SimpleNamespace(
        market_review_enabled=True,
        daily_market_context_enabled=True,
        report_language="zh",
    )
    pipeline.daily_market_context_enabled = True
    pipeline.db = MagicMock()
    pipeline.notifier = MagicMock()
    pipeline.analyzer = MagicMock()
    pipeline.search_service = MagicMock()
    pipeline.daily_market_context_allow_generate = False

    with patch("src.core.pipeline.DailyMarketContextService") as service_cls:
        service = service_cls.return_value
        service.get_context.return_value = None

        context = pipeline._load_daily_market_context(
            "cn",
            target_date=date(2026, 6, 6),
        )

    assert context is None
    service.get_context.assert_called_once()
    assert service.get_context.call_args.kwargs["allow_generate"] is False


def test_pipeline_skips_daily_market_context_when_context_is_disabled() -> None:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.config = SimpleNamespace(
        market_review_enabled=True,
        daily_market_context_enabled=True,
        report_language="zh",
    )
    pipeline.daily_market_context_enabled = False

    with patch("src.core.pipeline.DailyMarketContextService") as service_cls:
        context = pipeline._load_daily_market_context(
            "cn",
            target_date=date(2026, 6, 6),
        )

    assert context is None
    service_cls.assert_not_called()


def test_pipeline_skips_daily_market_context_when_config_is_disabled() -> None:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.config = SimpleNamespace(
        market_review_enabled=True,
        daily_market_context_enabled=False,
        report_language="zh",
    )
    pipeline.daily_market_context_enabled = True

    with patch("src.core.pipeline.DailyMarketContextService") as service_cls:
        context = pipeline._load_daily_market_context(
            "cn",
            target_date=date(2026, 6, 6),
        )

    assert context is None
    service_cls.assert_not_called()


def test_pipeline_initializes_daily_market_context_service_once_across_threads() -> None:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.config = SimpleNamespace(
        market_review_enabled=True,
        daily_market_context_enabled=True,
        report_language="zh",
    )
    pipeline.daily_market_context_enabled = True
    pipeline.db = MagicMock()
    pipeline.notifier = MagicMock()
    pipeline.analyzer = MagicMock()
    pipeline.search_service = MagicMock()

    service = MagicMock()
    service.get_context.return_value = _market_context()
    worker_count = 8
    start_barrier = threading.Barrier(worker_count)
    constructor_entered = threading.Event()
    release_constructor = threading.Event()

    def _load() -> DailyMarketContext:
        start_barrier.wait(timeout=2)
        return pipeline._load_daily_market_context(
            "cn",
            target_date=date(2026, 6, 6),
        )

    def _create_service(*args, **kwargs):
        constructor_entered.set()
        release_constructor.wait(timeout=2)
        return service

    with patch("src.core.pipeline.DailyMarketContextService", side_effect=_create_service) as service_cls:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [executor.submit(_load) for _ in range(worker_count)]
            assert constructor_entered.wait(timeout=2)
            time.sleep(0.05)
            release_constructor.set()
            contexts = [future.result(timeout=2) for future in futures]

    assert contexts == [_market_context()] * worker_count
    service_cls.assert_called_once_with(db_manager=pipeline.db)
    assert service.get_context.call_count == worker_count


def test_pipeline_uses_market_phase_effective_date_for_daily_market_context() -> None:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    phase_context = SimpleNamespace(
        effective_daily_bar_date=date(2026, 3, 26),
        to_dict=MagicMock(
            return_value={
                "market": "cn",
                "phase": "intraday",
                "market_local_time": "2026-03-27T10:00:00+08:00",
                "session_date": "2026-03-27",
                "effective_daily_bar_date": "2026-03-26",
                "is_trading_day": True,
                "is_market_open_now": True,
                "is_partial_bar": True,
                "minutes_to_open": None,
                "minutes_to_close": 300,
                "trigger_source": "system",
                "analysis_intent": "auto",
                "warnings": [],
            }
        ),
    )
    pipeline.config = SimpleNamespace(
        enable_realtime_quote=False,
        enable_chip_distribution=False,
        market_review_enabled=True,
        report_language="zh",
        agent_mode=False,
        save_context_snapshot=False,
        report_integrity_enabled=False,
        fundamental_stage_timeout_seconds=1,
    )
    pipeline.query_source = "system"
    pipeline.analysis_phase = "auto"
    pipeline.portfolio_context = None
    pipeline.fetcher_manager = MagicMock()
    pipeline.fetcher_manager.get_stock_name.return_value = "贵州茅台"
    pipeline.fetcher_manager.get_chip_distribution.return_value = None
    pipeline.fetcher_manager.get_fundamental_context.return_value = {}
    pipeline.fetcher_manager.build_failed_fundamental_context.return_value = {}
    pipeline.db = MagicMock()
    pipeline.db.get_analysis_context.return_value = {
        "code": "600519",
        "stock_name": "贵州茅台",
        "today": {},
        "yesterday": {},
    }
    pipeline.trend_analyzer = MagicMock()
    pipeline.analyzer = MagicMock()
    pipeline.analyzer.analyze.return_value = MagicMock(success=True)
    pipeline.search_service = MagicMock()
    pipeline.search_service.is_available = False
    pipeline.search_service.news_window_days = 3
    pipeline._emit_progress = MagicMock()
    pipeline._load_daily_market_context = MagicMock(return_value=_market_context())

    with patch("src.core.pipeline.build_market_phase_context", return_value=phase_context):
        pipeline.analyze_stock(
            "600519",
            ReportType.SIMPLE,
            "q-effective-date",
        )

    pipeline._load_daily_market_context.assert_called_once_with(
        "cn",
        target_date=date(2026, 3, 26),
    )




def _pipeline_intraday_sessions() -> list[date]:
    return [
        date(2026, 9, 14),
        date(2026, 9, 15),
        date(2026, 9, 16),
        date(2026, 9, 17),
        date(2026, 9, 18),
        date(2026, 9, 21),
        date(2026, 9, 22),
        date(2026, 9, 23),
        date(2026, 9, 24),
        date(2026, 9, 25),
    ]


def _pipeline_canonical_5m(session_dates: list[date]) -> pd.DataFrame:
    observed = pd.Timestamp("2026-09-25 16:00:00", tz="Asia/Shanghai")
    rows = []
    for session_date in session_dates:
        labels = list(
            pd.date_range(
                f"{session_date.isoformat()} 09:35:00",
                f"{session_date.isoformat()} 11:30:00",
                freq="5min",
                tz="Asia/Shanghai",
            )
        )
        labels += list(
            pd.date_range(
                f"{session_date.isoformat()} 13:05:00",
                f"{session_date.isoformat()} 15:00:00",
                freq="5min",
                tz="Asia/Shanghai",
            )
        )
        for index, bar_end in enumerate(labels):
            close = 100.0 + index * 0.01
            rows.append(
                {
                    "code": "600519",
                    "bar_end": bar_end,
                    "open": close - 0.1,
                    "high": close + 0.5,
                    "low": close - 0.5,
                    "close": close,
                    "volume": 1000.0 + index,
                    "session": (
                        f"{session_date.isoformat()}:AM"
                        if bar_end.hour < 12
                        else f"{session_date.isoformat()}:PM"
                    ),
                    "available_at": observed,
                    "adjustflag": "2",
                    "data_source": "BaostockFetcher",
                }
            )
    frame = pd.DataFrame(rows)
    identity = build_intraday_data_identity(
        frame,
        provider_identity="BaostockFetcher",
        provider_route="unit.pipeline",
        package_version="0.9.4",
        query_identity={"code": "sh.600519", "frequency": "5", "adjustflag": "2"},
        requested_adjustment_basis="qfq",
        observed_adjustment_basis="qfq",
        basis_evidence="unit-test",
        timeframe="5m",
        timezone_name="Asia/Shanghai",
        session_calendar="XSHG",
        currency="CNY",
        volume_unit="UNKNOWN",
        amount_unit="UNKNOWN",
        requested_start=session_dates[0].isoformat(),
        requested_end=session_dates[-1].isoformat(),
        identity_state="OBSERVED",
        observed_at=observed.tz_convert("UTC").to_pydatetime(),
        source_rows_sha256="7" * 64,
    )
    attach_intraday_data_identity(frame, identity)
    frame["intraday_data_identity_hash"] = identity["identity_hash"]
    return frame


def _intraday_pipeline_for_helper(*, p0_bounded_trial: bool = False) -> StockAnalysisPipeline:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.p0_bounded_trial = p0_bounded_trial
    pipeline.p0_receipt_only = p0_bounded_trial
    pipeline.fetcher_manager = MagicMock()
    pipeline.fetcher_manager._get_fetcher_by_name.return_value = object()
    return pipeline


def test_current_cn_stock_wiring_uses_named_baostock_and_exact_ten_sessions() -> None:
    sessions = _pipeline_intraday_sessions()
    target = sessions[-1]
    frame = _pipeline_canonical_5m(sessions)
    pipeline = _intraday_pipeline_for_helper()
    pipeline.fetcher_manager._call_fetcher_method.return_value = frame
    history = pd.DataFrame({"date": sessions})

    with patch(
        "src.core.pipeline.resolve_latest_completed_session_fail_closed",
        return_value=target,
    ), patch(
        "src.core.pipeline.SearchService.is_index_or_etf",
        return_value=False,
    ):
        intraday = pipeline._load_current_stock_intraday_timeframes(
            code="600519",
            stock_name="贵州茅台",
            market="cn",
            target_date=target,
            completed_daily_history=history,
        )

    pipeline.fetcher_manager._get_fetcher_by_name.assert_called_once_with(
        "BaostockFetcher",
        capability="intraday_5m",
    )
    pipeline.fetcher_manager._call_fetcher_method.assert_called_once_with(
        pipeline.fetcher_manager._get_fetcher_by_name.return_value,
        "get_intraday_data",
        "600519",
        start_date=sessions[0].isoformat(),
        end_date=sessions[-1].isoformat(),
        frequency="5",
    )
    assert {key: len(value) for key, value in intraday.items()} == {
        "5m": 480,
        "15m": 160,
        "30m": 80,
        "60m": 40,
    }


def test_intraday_wiring_skips_bounded_replay_without_provider_call() -> None:
    sessions = _pipeline_intraday_sessions()
    pipeline = _intraday_pipeline_for_helper(p0_bounded_trial=True)
    result = pipeline._load_current_stock_intraday_timeframes(
        code="600519",
        stock_name="贵州茅台",
        market="cn",
        target_date=sessions[-1],
        completed_daily_history=pd.DataFrame({"date": sessions}),
    )
    assert result == {}
    pipeline.fetcher_manager._get_fetcher_by_name.assert_not_called()
    pipeline.fetcher_manager._call_fetcher_method.assert_not_called()


def test_intraday_wiring_skips_backdated_target_and_etf_without_provider_call() -> None:
    sessions = _pipeline_intraday_sessions()
    pipeline = _intraday_pipeline_for_helper()
    history = pd.DataFrame({"date": sessions})

    with patch(
        "src.core.pipeline.resolve_latest_completed_session_fail_closed",
        return_value=date(2026, 9, 28),
    ), patch(
        "src.core.pipeline.SearchService.is_index_or_etf",
        return_value=False,
    ):
        backdated = pipeline._load_current_stock_intraday_timeframes(
            code="600519",
            stock_name="贵州茅台",
            market="cn",
            target_date=sessions[-1],
            completed_daily_history=history,
        )
    assert backdated == {}
    pipeline.fetcher_manager._get_fetcher_by_name.assert_not_called()

    with patch(
        "src.core.pipeline.resolve_latest_completed_session_fail_closed",
        return_value=sessions[-1],
    ), patch(
        "src.core.pipeline.SearchService.is_index_or_etf",
        return_value=True,
    ):
        etf = pipeline._load_current_stock_intraday_timeframes(
            code="588200",
            stock_name="科创芯片ETF",
            market="cn",
            target_date=sessions[-1],
            completed_daily_history=history,
        )
    assert etf == {}
    pipeline.fetcher_manager._get_fetcher_by_name.assert_not_called()


def test_intraday_wiring_skips_non_cn_and_unproven_calendar_without_provider_call() -> None:
    sessions = _pipeline_intraday_sessions()
    history = pd.DataFrame({"date": sessions})

    non_cn = _intraday_pipeline_for_helper()
    with patch(
        "src.core.pipeline.SearchService.is_index_or_etf",
        return_value=False,
    ):
        result = non_cn._load_current_stock_intraday_timeframes(
            code="AAPL",
            stock_name="Apple",
            market="us",
            target_date=sessions[-1],
            completed_daily_history=history,
        )
    assert result == {}
    non_cn.fetcher_manager._get_fetcher_by_name.assert_not_called()

    unknown_calendar = _intraday_pipeline_for_helper()
    with patch(
        "src.core.pipeline.resolve_latest_completed_session_fail_closed",
        return_value=None,
    ), patch(
        "src.core.pipeline.SearchService.is_index_or_etf",
        return_value=False,
    ):
        result = unknown_calendar._load_current_stock_intraday_timeframes(
            code="600519",
            stock_name="贵州茅台",
            market="cn",
            target_date=sessions[-1],
            completed_daily_history=history,
        )
    assert result == {}
    unknown_calendar.fetcher_manager._get_fetcher_by_name.assert_not_called()


def test_intraday_wiring_provider_error_returns_missing_without_fallback() -> None:
    sessions = _pipeline_intraday_sessions()
    pipeline = _intraday_pipeline_for_helper()
    pipeline.fetcher_manager._call_fetcher_method.side_effect = RuntimeError(
        "provider unavailable"
    )
    with patch(
        "src.core.pipeline.resolve_latest_completed_session_fail_closed",
        return_value=sessions[-1],
    ), patch(
        "src.core.pipeline.SearchService.is_index_or_etf",
        return_value=False,
    ):
        result = pipeline._load_current_stock_intraday_timeframes(
            code="600519",
            stock_name="贵州茅台",
            market="cn",
            target_date=sessions[-1],
            completed_daily_history=pd.DataFrame({"date": sessions}),
        )
    assert result == {}
    pipeline.fetcher_manager._get_fetcher_by_name.assert_called_once_with(
        "BaostockFetcher",
        capability="intraday_5m",
    )
    assert pipeline.fetcher_manager._call_fetcher_method.call_count == 1
    pipeline.fetcher_manager.get_daily_data.assert_not_called()
    pipeline.fetcher_manager.get_realtime_quote.assert_not_called()


def test_intraday_wiring_requires_ten_completed_daily_sessions_before_provider_call() -> None:
    sessions = _pipeline_intraday_sessions()
    pipeline = _intraday_pipeline_for_helper()
    with patch(
        "src.core.pipeline.resolve_latest_completed_session_fail_closed",
        return_value=sessions[-1],
    ), patch(
        "src.core.pipeline.SearchService.is_index_or_etf",
        return_value=False,
    ):
        result = pipeline._load_current_stock_intraday_timeframes(
            code="600519",
            stock_name="贵州茅台",
            market="cn",
            target_date=sessions[-1],
            completed_daily_history=pd.DataFrame({"date": sessions[-9:]}),
        )
    assert result == {}
    pipeline.fetcher_manager._get_fetcher_by_name.assert_not_called()
    pipeline.fetcher_manager._call_fetcher_method.assert_not_called()


def test_intraday_wiring_rejects_incomplete_5m_without_fallback() -> None:
    sessions = _pipeline_intraday_sessions()
    frame = _pipeline_canonical_5m(sessions).drop(index=[4]).reset_index(drop=True)
    pipeline = _intraday_pipeline_for_helper()
    pipeline.fetcher_manager._call_fetcher_method.return_value = frame
    with patch(
        "src.core.pipeline.resolve_latest_completed_session_fail_closed",
        return_value=sessions[-1],
    ), patch(
        "src.core.pipeline.SearchService.is_index_or_etf",
        return_value=False,
    ):
        result = pipeline._load_current_stock_intraday_timeframes(
            code="600519",
            stock_name="贵州茅台",
            market="cn",
            target_date=sessions[-1],
            completed_daily_history=pd.DataFrame({"date": sessions}),
        )
    assert result == {}
    assert pipeline.fetcher_manager._call_fetcher_method.call_count == 1
    pipeline.fetcher_manager.get_daily_data.assert_not_called()
    pipeline.fetcher_manager.get_realtime_quote.assert_not_called()


def test_analyze_stock_passes_intraday_after_acquisition_and_before_snapshot_to_mtf() -> None:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    phase_context = SimpleNamespace(
        effective_daily_bar_date=date(2026, 9, 25),
        to_dict=MagicMock(
            return_value={
                "market": "cn",
                "phase": "postmarket",
                "market_local_time": "2026-09-25T16:00:00+08:00",
                "session_date": "2026-09-25",
                "effective_daily_bar_date": "2026-09-25",
                "is_trading_day": True,
                "is_market_open_now": False,
                "is_partial_bar": False,
                "minutes_to_open": None,
                "minutes_to_close": None,
                "trigger_source": "system",
                "analysis_intent": "auto",
                "warnings": [],
            }
        ),
    )
    pipeline.config = SimpleNamespace(
        enable_realtime_quote=False,
        enable_chip_distribution=False,
        market_review_enabled=True,
        report_language="zh",
        agent_mode=False,
        save_context_snapshot=False,
        report_integrity_enabled=False,
        fundamental_stage_timeout_seconds=1,
    )
    pipeline.query_source = "system"
    pipeline.analysis_phase = "auto"
    pipeline.analysis_skills = None
    pipeline.portfolio_context = None
    pipeline.fetcher_manager = MagicMock()
    pipeline.fetcher_manager.get_stock_name.return_value = "贵州茅台"
    pipeline.fetcher_manager.get_chip_distribution.return_value = None
    pipeline.fetcher_manager.get_fundamental_context.return_value = {}
    pipeline.fetcher_manager.build_failed_fundamental_context.return_value = {}
    pipeline.db = MagicMock()
    pipeline.db.get_analysis_context.return_value = {
        "code": "600519",
        "stock_name": "贵州茅台",
        "today": {},
        "yesterday": {},
    }
    pipeline.db.get_data_range.return_value = []
    pipeline.trend_analyzer = MagicMock()
    pipeline.analyzer = MagicMock()
    pipeline.analyzer.analyze.return_value = MagicMock(success=True)
    pipeline.search_service = MagicMock()
    pipeline.search_service.is_available = False
    pipeline.search_service.news_window_days = 3
    pipeline._emit_progress = MagicMock()
    pipeline._load_daily_market_context = MagicMock(return_value=_market_context())
    pipeline._build_market_structure_context = MagicMock(return_value=None)
    observed = {}
    intraday = {"5m": object(), "15m": object(), "30m": object(), "60m": object()}

    def _load_intraday(**_kwargs):
        observed["loaded_at"] = datetime.now(timezone.utc)
        return intraday

    pipeline._load_current_stock_intraday_timeframes = MagicMock(
        side_effect=_load_intraday
    )

    with patch(
        "src.core.pipeline.build_market_phase_context",
        return_value=phase_context,
    ), patch(
        "src.core.pipeline.build_supply_demand_context",
        return_value={},
    ), patch(
        "src.core.pipeline.build_cost_structure_context",
        return_value={},
    ), patch(
        "src.core.pipeline.build_price_structure_context",
        return_value={},
    ), patch(
        "src.core.pipeline.build_volatility_momentum_context",
        return_value={},
    ), patch(
        "src.core.pipeline.build_pattern_trigger_context",
        return_value={},
    ), patch(
        "src.core.pipeline.build_multi_timeframe_structure_context",
        return_value={},
    ) as mtf_builder:
        pipeline.analyze_stock(
            "600519",
            ReportType.SIMPLE,
            "q-intraday-ordering",
        )

    pipeline._load_current_stock_intraday_timeframes.assert_called_once()
    kwargs = mtf_builder.call_args.kwargs
    assert kwargs["intraday_timeframes"] is intraday
    assert kwargs["snapshot_observed_at"] >= observed["loaded_at"]
def test_pipeline_attaches_low_sensitive_market_context_to_enhanced_context() -> None:
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    enhanced_context = {"code": "600519"}

    pipeline._attach_daily_market_context(
        enhanced_context,
        _market_context(),
        report_language="zh",
    )

    assert enhanced_context["daily_market_context"]["region"] == "cn"
    assert enhanced_context["daily_market_context"]["summary"].startswith("大盘退潮")
    assert "大盘环境摘要" in enhanced_context["daily_market_context_summary"]
    assert "market_review_payload" not in str(enhanced_context)


def test_analyzer_prompt_renders_daily_market_context_before_technical_data() -> None:
    analyzer = GeminiAnalyzer.__new__(GeminiAnalyzer)
    analyzer._get_skill_prompt_sections = lambda: ("", "", False)
    context = {
        "code": "600519",
        "stock_name": "贵州茅台",
        "date": "2026-06-06",
        "today": {"close": 1800, "open": 1790, "high": 1810, "low": 1780},
        "daily_market_context": _market_context().to_safe_dict(),
    }

    prompt = analyzer._format_prompt(context, "贵州茅台", report_language="zh")

    assert "大盘环境摘要" in prompt
    assert "大盘退潮" in prompt
    assert prompt.index("大盘环境摘要") < prompt.index("技术面数据")
