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
        date(2026, 9, 28),  # XSHG 2026-09-25 Mid-Autumn holiday
    ]


def _pipeline_canonical_5m(
    session_dates: list[date], *, requested_start: date | None = None,
) -> pd.DataFrame:
    observed = pd.Timestamp(
        f"{session_dates[-1].isoformat()} 16:00:00",
        tz="Asia/Shanghai",
    )
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
        requested_start=(requested_start or session_dates[0]).isoformat(),
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



def test_intraday_window_plan_is_method_driven_not_magic_ten() -> None:
    from src.core import pipeline as pipeline_module

    current = pipeline_module._current_intraday_window_plan()
    assert current["required_bars"] == 40
    assert current["required_sessions"] == 10
    assert current["limiting_timeframes"] == ("60m",)
    assert current["method_required_bars"]["MA_COMPRESSION_RELEASE"] == 40
    assert "VARIABLE_STRUCTURE" in current["deferred_window_classes"]

    # A future owner warm-up change must expand the data request instead of
    # silently preserving the old ten-session literal.
    with patch.object(pipeline_module, "MA_COMPRESSION_READY_BARS", 61):
        expanded = pipeline_module._current_intraday_window_plan()
    assert expanded["required_bars"] == 61
    assert expanded["required_sessions"] == 16
    assert expanded["sessions_by_timeframe"]["60m"] == 16


def test_daily_and_minute_recursive_owner_require_distinct_readiness_windows() -> None:
    from src.core import pipeline as owner
    from src.services.volatility_momentum_service import (
        rsi_startup_comparison_required_bars,
        atr_startup_comparison_required_bars,
    )

    current = owner._current_daily_window_plan(
        market="cn", target_date=date(2026, 10, 12),
    )
    daily = current["method_required_bars"]["daily"]
    assert daily["MACD"] == owner.macd_startup_comparison_required_bars()
    assert daily["RSI"] == rsi_startup_comparison_required_bars()
    assert daily["ATR_WILDER"] == atr_startup_comparison_required_bars()
    assert len({daily["MACD"], daily["RSI"], daily["ATR_WILDER"]}) == 3
    assert current["timeframe_required_bars"]["daily"] == max(daily.values())
    minute = owner._current_intraday_window_plan()
    for timeframe, values in minute["method_required_bars_by_timeframe"].items():
        assert values["RSI_STARTUP_COMPARISON"] == rsi_startup_comparison_required_bars()
        assert values["MACD_STARTUP_COMPARISON"] == owner.macd_startup_comparison_required_bars()
    assert minute["admission_target_required_bars"] == max(
        minute["method_required_bars_by_timeframe"]["60m"].values()
    )


def test_b5_monthly_macd_drift_proposes_bounded_native_35_bar_extensions_not_global_220() -> None:
    """A numeric DRIFT is the only legitimate trigger for a source-only extension."""
    from src.core import pipeline as owner

    base = owner._current_daily_window_plan(
        market="cn", target_date=date(2026, 10, 12),
    )
    first = base["timeframe_required_bars"]["monthly"]
    assert first == owner.macd_startup_comparison_required_bars()
    receipt = {
        "method_owner": "StockTrendAnalyzer._calculate_macd",
        "status": "UNKNOWN",
        "reason": "MACD_MULTI_START_NUMERIC_OR_STATE_DRIFT",
        "observations": first,
        "required_observations": first,
        "readiness_scope": "OBSERVED_COMPLETED_PREFIXES_ONLY",
    }
    first_extension = owner._next_bounded_monthly_macd_window(
        base_plan=base, target_date=date(2026, 10, 12),
        receipt=receipt, attempted_monthly_bars=(first,),
    )
    assert first_extension["status"] == "READ_ONLY_EXTENSION_AVAILABLE"
    increment = owner.StockTrendAnalyzer.MACD_SLOW + owner.StockTrendAnalyzer.MACD_SIGNAL
    assert first_extension["proposed_monthly_bars"] == first + increment
    assert first_extension["new_start_date"] < base["start_date"]
    assert first_extension["strategy_admitted"] is False
    assert first_extension["storage_admitted"] is False
    assert first_extension["numerical_convergence_proven"] is False

    second_receipt = dict(receipt, observations=first + increment)
    second = owner._next_bounded_monthly_macd_window(
        base_plan=base, target_date=date(2026, 10, 12),
        receipt=second_receipt, attempted_monthly_bars=(first, first + increment),
    )
    assert second["proposed_monthly_bars"] == first + 2 * increment
    assert second["new_start_date"] < first_extension["new_start_date"]


def test_b5_monthly_macd_actual_near_tolerance_can_propose_one_signal_period_refinement() -> None:
    """The third and last read uses current MACD signal bars, never magic 220."""
    from src.core import pipeline as owner

    base = owner._current_daily_window_plan(
        market="cn", target_date=date(2026, 10, 12),
    )
    native = {
        "method_owner": "StockTrendAnalyzer._calculate_macd",
        "status": "UNKNOWN",
        "reason": "MACD_MULTI_START_NUMERIC_OR_STATE_DRIFT",
        "observations": 210,
        "required_observations": owner.macd_startup_comparison_required_bars(),
        "readiness_scope": "OBSERVED_COMPLETED_PREFIXES_ONLY",
        "max_endpoint_difference": 0.145479132355,
        "absolute_tolerance": 0.125862,
        "startup_state_agreement": True,
        "numeric_agreement": False,
    }
    refinement = owner._next_bounded_monthly_macd_window(
        base_plan=base, target_date=date(2026, 10, 12),
        receipt=native, attempted_monthly_bars=(140, 175, 210),
    )
    assert refinement["status"] == "READ_ONLY_EXTENSION_AVAILABLE"
    assert refinement["increment_bars"] == owner.StockTrendAnalyzer.MACD_SIGNAL
    assert refinement["proposed_monthly_bars"] == 210 + owner.StockTrendAnalyzer.MACD_SIGNAL
    assert refinement["storage_admitted"] is False
    assert refinement["strategy_admitted"] is False

    for forged in (
        {**native, "max_endpoint_difference": float("nan")},
        {**native, "max_endpoint_difference": 9.0},
        {**native, "startup_state_agreement": False},
        {**native, "numeric_agreement": True},
        {**native, "absolute_tolerance": 0.0},
    ):
        blocked = owner._next_bounded_monthly_macd_window(
            base_plan=base, target_date=date(2026, 10, 12),
            receipt=forged, attempted_monthly_bars=(140, 175, 210),
        )
        assert blocked["status"] == "NO_EXTENSION"
        assert blocked["storage_admitted"] is False
    exhausted = owner._next_bounded_monthly_macd_window(
        base_plan=base, target_date=date(2026, 10, 12),
        receipt={**native, "observations": refinement["proposed_monthly_bars"]},
        attempted_monthly_bars=(140, 175, 210, refinement["proposed_monthly_bars"]),
    )
    assert exhausted["status"] == "NO_EXTENSION"
    assert exhausted["reason"] == "BOUNDED_RETRY_BUDGET_EXHAUSTED"
    # A real READY must be recorded as convergence, not misdiagnosed as
    # exhaustion just because the final read happened to hit the limit.
    converged = owner._next_bounded_monthly_macd_window(
        base_plan=base, target_date=date(2026, 10, 12),
        receipt={
            **native,
            "status": "READY",
            "reason": "MACD_OBSERVED_MULTI_START_NUMERIC_AND_STATE_AGREE",
            "observations": refinement["proposed_monthly_bars"],
            "max_endpoint_difference": 0.0568,
            "startup_state_agreement": True,
            "numeric_agreement": True,
        },
        attempted_monthly_bars=(140, 175, 210, refinement["proposed_monthly_bars"]),
    )
    assert converged["status"] == "NO_EXTENSION"
    assert converged["reason"] == "NATIVE_MACD_ALREADY_READY"


def test_b5_monthly_macd_ready_or_forged_receipt_never_requeries_source() -> None:
    """A caller flag cannot make unrelated or already READY evidence request more data."""
    from src.core import pipeline as owner

    base = owner._current_daily_window_plan(
        market="cn", target_date=date(2026, 10, 12),
    )
    template = {
        "method_owner": "StockTrendAnalyzer._calculate_macd",
        "status": "UNKNOWN",
        "reason": "MACD_MULTI_START_NUMERIC_OR_STATE_DRIFT",
        "observations": 140,
        "required_observations": 140,
        "readiness_scope": "OBSERVED_COMPLETED_PREFIXES_ONLY",
    }
    for mutated in (
        {**template, "status": "READY"},
        {**template, "method_owner": "VendorOptimisticMACD"},
        {**template, "reason": "PROVIDER_LOGIN_FAILURE"},
        {**template, "observations": 139},
        {**template, "readiness_scope": "FUTURE_KNOWN"},
    ):
        denied = owner._next_bounded_monthly_macd_window(
            base_plan=base, target_date=date(2026, 10, 12),
            receipt=mutated, attempted_monthly_bars=(140,),
        )
        assert denied["status"] == "NO_EXTENSION"
        assert denied["storage_admitted"] is False
    capped = owner._next_bounded_monthly_macd_window(
        base_plan=base, target_date=date(2026, 10, 12),
        receipt={**template, "observations": 210},
        attempted_monthly_bars=(140, 175, 210),
    )
    assert capped["status"] == "NO_EXTENSION"
    assert capped["reason"] == "BOUNDED_RETRY_BUDGET_EXHAUSTED"


def test_b5_monthly_macd_calendar_and_non_mtf_method_do_not_erase_ma_source() -> None:
    """Early dates, unrelated methods and missing calendar do not trigger broad fetch."""
    from src.core import pipeline as owner
    from src.services.evidence_traceability_registry import compile_method_window_policy_view

    early = owner._current_daily_window_plan(
        market="cn", target_date=date(2012, 10, 12),
    )
    receipt = {
        "method_owner": "StockTrendAnalyzer._calculate_macd",
        "status": "UNKNOWN",
        "reason": "MACD_MULTI_START_NUMERIC_OR_STATE_DRIFT",
        "observations": 40,
        "required_observations": 140,
        "readiness_scope": "OBSERVED_COMPLETED_PREFIXES_ONLY",
    }
    no_calendar = owner._next_bounded_monthly_macd_window(
        base_plan=early, target_date=date(2012, 10, 12),
        receipt=receipt, attempted_monthly_bars=(40,),
    )
    assert no_calendar["status"] == "NO_EXTENSION"
    assert no_calendar["reason"] == "NATIVE_RECURSIVE_STARTUP_NOT_AVAILABLE"
    assert early["source_only_timeframe_required_bars"]["monthly"] == 40

    candle = next(row for row in compile_method_window_policy_view()["methods"]
                  if row["method_id"] == "CANDLESTICK")
    no_mtf = owner._current_daily_window_plan(
        market="cn", target_date=date(2026, 10, 12),
        method_rows=(candle,),
    )
    nontrigger = owner._next_bounded_monthly_macd_window(
        base_plan=no_mtf, target_date=date(2026, 10, 12),
        receipt=receipt, attempted_monthly_bars=(40,),
    )
    assert nontrigger["status"] == "NO_EXTENSION"
    assert nontrigger["reason"] == "MONTHLY_MTF_METHOD_NOT_ROUTED"


def test_b5_month_week_recursive_momentum_components_drive_real_daily_fetch_horizon() -> None:
    """Independent native MACD/RSI convergence inputs cannot vanish behind 40-bar MA."""
    from src.core import pipeline as owner
    from src.services.volatility_momentum_service import rsi_startup_comparison_required_bars

    current = owner._current_daily_window_plan(
        market="cn", target_date=date(2026, 10, 12),
    )
    macd = owner.macd_startup_comparison_required_bars()
    rsi = rsi_startup_comparison_required_bars()
    assert current["source_only_timeframe_required_bars"] == {
        "daily": 268, "weekly": 40, "monthly": 40,
    }
    assert current["mtf_recursive_comparison_required_bars"] == {
        "MACD_STARTUP_COMPARISON": macd,
        "RSI_STARTUP_COMPARISON": rsi,
    }
    assert current["timeframe_required_bars"] == {
        "daily": 268, "weekly": rsi, "monthly": macd,
    }
    assert current["mtf_recursive_calendar_status"]["weekly"] == {
        "MACD_STARTUP_COMPARISON": "ACQUISITION_PLANNED",
        "RSI_STARTUP_COMPARISON": "ACQUISITION_PLANNED",
    }
    assert current["mtf_recursive_calendar_status"]["monthly"] == {
        "MACD_STARTUP_COMPARISON": "ACQUISITION_PLANNED",
        "RSI_STARTUP_COMPARISON": "CALENDAR_HISTORY_OUT_OF_RANGE",
    }
    assert current["start_by_timeframe"]["monthly"] == date(2015, 2, 2)
    assert current["start_date"] == date(2015, 2, 2)


def test_b5_early_asof_partial_calendar_keeps_legal_ma_and_structure_nontrigger() -> None:
    """An older as-of cannot turn unavailable 140-month MACD into total study failure."""
    from src.core import pipeline as owner
    from src.services.evidence_traceability_registry import compile_method_window_policy_view

    early = owner._current_daily_window_plan(
        market="cn", target_date=date(2012, 10, 12),
    )
    assert early["source_only_timeframe_required_bars"]["monthly"] == 40
    assert early["timeframe_required_bars"]["monthly"] == 40
    assert early["mtf_recursive_calendar_status"]["monthly"] == {
        "MACD_STARTUP_COMPARISON": "CALENDAR_HISTORY_OUT_OF_RANGE",
        "RSI_STARTUP_COMPARISON": "CALENDAR_HISTORY_OUT_OF_RANGE",
    }
    assert early["timeframe_required_bars"]["weekly"] >= owner.macd_startup_comparison_required_bars()
    assert early["start_date"] <= date(2009, 6, 1)

    # An independently routed 2-bar candlestick may not inherit monthly
    # oscillators or demand 11+ years of data.
    candlestick = next(row for row in compile_method_window_policy_view()["methods"]
                       if row["method_id"] == "CANDLESTICK")
    bounded = owner._current_daily_window_plan(
        market="cn", target_date=date(2026, 10, 12),
        method_rows=(candlestick,),
    )
    assert bounded["timeframe_required_bars"] == {"daily": 2, "weekly": 0, "monthly": 0}
    assert bounded["mtf_recursive_comparison_required_bars"] == {}
    assert bounded["mtf_recursive_calendar_status"] == {}


def test_b5_native_rsi_warmup_change_replans_weekly_without_fake_monthly_ready() -> None:
    """Calendar feasibility and dynamic owner convergence are distinct facts."""
    from src.core import pipeline as owner

    with patch.object(owner, "rsi_startup_comparison_required_bars", return_value=300):
        changed = owner._current_daily_window_plan(
            market="cn", target_date=date(2026, 10, 12),
        )
    assert changed["mtf_recursive_comparison_required_bars"]["RSI_STARTUP_COMPARISON"] == 300
    assert changed["timeframe_required_bars"]["weekly"] == 300
    assert changed["timeframe_required_bars"]["monthly"] == owner.macd_startup_comparison_required_bars()
    assert changed["mtf_recursive_calendar_status"]["monthly"]["RSI_STARTUP_COMPARISON"] == "CALENDAR_HISTORY_OUT_OF_RANGE"


def test_b5_monthly_recursive_window_is_consumed_by_real_fetch_entry_without_db_rights_bypass() -> None:
    """The real Pipeline-to-fetcher seam consumes the owner plan, then vendor DB veto holds."""
    from src.storage import DatabaseManager

    target = date(2026, 10, 12)
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.fetcher_manager = MagicMock()
    pipeline.fetcher_manager.get_stock_name.return_value = "SYNTHETIC_STOCK"
    pipeline._resolve_resume_target_date = MagicMock(return_value=target)
    pipeline.db = MagicMock()
    pipeline.db.has_today_data.return_value = False
    frame = pd.DataFrame([{
        "date": target, "open": 10., "high": 11., "low": 9.,
        "close": 10.5, "volume": 1000., "amount": 10500.,
    }])
    pipeline.fetcher_manager.get_daily_data.return_value = (
        frame, "BaostockFetcher",
    )

    # Use the ACTUAL production DatabaseManager method; intercept the SQL
    # transaction itself to prove that no unlicensed row can enter it.
    writer = object.__new__(DatabaseManager)
    writer._run_write_transaction = MagicMock(
        side_effect=AssertionError("UNLICENSED_DB_TRANSACTION_REACHED")
    )
    pipeline.db.save_daily_data.side_effect = (
        lambda df, code, source: DatabaseManager.save_daily_data(
            writer, df, code, source,
        )
    )
    succeeded, error = pipeline.fetch_and_save_stock_data("600519")
    assert succeeded is False
    assert error and "SOURCE_STORAGE_RIGHTS_NOT_ADMITTED" in error
    pipeline.fetcher_manager.get_daily_data.assert_called_once_with(
        "600519", start_date="2015-02-02", end_date="2026-10-12",
    )
    writer._run_write_transaction.assert_not_called()


def test_b5_latest_only_sqlite_cache_cannot_hide_new_recursive_history_window() -> None:
    """A genuine one-day SQLite cache cannot suppress 11 years of required research."""
    import os
    import tempfile
    from src.storage import DatabaseManager

    target = date(2026, 10, 12)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as td:
        DatabaseManager.reset_instance()
        db = DatabaseManager(
            db_url="sqlite:///" + os.path.join(td, "source_window_test.sqlite").replace(os.sep, "/"),
        )
        try:
            one_bar = pd.DataFrame([{
                "date": target, "open": 10., "high": 11., "low": 9.,
                "close": 10.5, "volume": 1000., "amount": 10500.,
            }])
            assert db.save_daily_data(one_bar, "600519", data_source="test") == 1
            assert db.has_today_data("600519", target)
            pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
            pipeline.db = db
            pipeline.fetcher_manager = MagicMock()
            pipeline.fetcher_manager.get_stock_name.return_value = "SYNTHETIC"
            pipeline._resolve_resume_target_date = MagicMock(return_value=target)
            pipeline.fetcher_manager.get_daily_data.return_value = (
                one_bar, "BaostockFetcher",
            )
            succeeded, error = pipeline.fetch_and_save_stock_data("600519")
            assert succeeded is False
            assert "SOURCE_STORAGE_RIGHTS_NOT_ADMITTED" in (error or "")
            pipeline.fetcher_manager.get_daily_data.assert_called_once_with(
                "600519", start_date="2015-02-02", end_date="2026-10-12",
            )
            # B1 true vendor-rights gate is still in the actual writer.
            with db.get_session() as session:
                from src.storage import StockDaily
                assert session.query(StockDaily).count() == 1
        finally:
            DatabaseManager.reset_instance()


def test_b5_full_completed_session_cache_keeps_lawful_no_provider_call() -> None:
    """A truly complete source window still qualifies for fast resume."""
    import exchange_calendars as xcals

    target = date(2026, 10, 12)
    calendar = xcals.get_calendar("XSHG")
    sessions = [
        value.date()
        for value in calendar.sessions_in_range(
            date(2015, 2, 2), target,
        )
    ]
    assert sessions[0] == date(2015, 2, 2) and sessions[-1] == target
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.db = MagicMock()
    pipeline.db.has_today_data.return_value = True
    pipeline.db.get_data_range.return_value = [
        SimpleNamespace(date=value) for value in sessions
    ]
    pipeline.fetcher_manager = MagicMock()
    pipeline._resolve_resume_target_date = MagicMock(return_value=target)
    result = pipeline.fetch_and_save_stock_data("600519")
    assert result == (True, None)
    pipeline.fetcher_manager.get_daily_data.assert_not_called()
    pipeline.db.save_daily_data.assert_not_called()


def test_b5_missing_internal_completed_session_does_not_fake_cache_coverage() -> None:
    """First/last bar alone must not certify a hole in source daily sessions."""
    import exchange_calendars as xcals

    target = date(2026, 10, 12)
    sessions = [
        value.date() for value in xcals.get_calendar("XSHG").sessions_in_range(
            date(2015, 2, 2), target,
        )
    ]
    sessions.pop(200)
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.db = MagicMock()
    pipeline.db.has_today_data.return_value = True
    pipeline.db.get_data_range.return_value = [
        SimpleNamespace(date=value) for value in sessions
    ]
    pipeline.fetcher_manager = MagicMock()
    pipeline.fetcher_manager.get_daily_data.return_value = (
        pd.DataFrame(), "BaostockFetcher",
    )
    pipeline._resolve_resume_target_date = MagicMock(return_value=target)
    result = pipeline.fetch_and_save_stock_data("600519")
    assert result[0] is False and "获取数据为空" in (result[1] or "")
    pipeline.fetcher_manager.get_daily_data.assert_called_once_with(
        "600519", start_date="2015-02-02", end_date="2026-10-12",
    )
    pipeline.db.save_daily_data.assert_not_called()


def test_b5_seven_timeframe_indicator_windows_keep_method_specific_inputs() -> None:
    """Three higher and four minute frames have distinct owner-driven bar demands."""
    from src.core import pipeline as owner

    higher = owner._current_daily_window_plan(
        market="cn", target_date=date(2026, 10, 12),
    )
    assert higher["source_only_timeframe_required_bars"] == {
        "daily": 268, "weekly": 40, "monthly": 40,
    }
    assert higher["timeframe_required_bars"] == {
        "daily": 268, "weekly": 268, "monthly": 140,
    }
    assert higher["method_required_bars"]["daily"]["CUP_HANDLE"] == 110
    assert higher["method_required_bars"]["daily"]["DOUBLE_BOTTOM"] == 90
    assert higher["method_required_bars"]["daily"]["CANDLESTICK"] == 2

    minute = owner._current_intraday_window_plan()
    assert minute["required_bars"] == 40  # Lawful minimum source-only plan.
    assert minute["required_sessions"] == 10
    assert minute["admission_target_required_bars"] == 268
    assert minute["admission_target_sessions"] == 67
    assert minute["admission_sessions_by_timeframe"] == {
        "5m": 6, "15m": 17, "30m": 34, "60m": 67,
    }
    for timeframe in ("5m", "15m", "30m", "60m"):
        methods = minute["method_required_bars_by_timeframe"][timeframe]
        assert methods["MA_COMPRESSION_RELEASE"] == 40
        assert methods["RSI_FORMULA_MINIMUM"] == 24
        assert methods["MACD_FORMULA_MINIMUM"] == 35
        assert methods["MACD_STARTUP_COMPARISON"] == 4 * (26 + 9)
        assert methods["RSI_STARTUP_COMPARISON"] == 268
    # Changes to the native MACD owner, not magic constants, change the horizon.
    with patch.object(owner.StockTrendAnalyzer, "MACD_SIGNAL", 17):
        longer = owner._current_intraday_window_plan()
    assert longer["method_required_bars_by_timeframe"]["60m"]["MACD_STARTUP_COMPARISON"] == 4 * (26 + 17)
    assert longer["admission_target_required_bars"] == 268
    assert longer["admission_target_sessions"] == 67
    with patch.object(owner, "MA_COMPRESSION_READY_BARS", 90):
        owner_ma = owner._current_intraday_window_plan()
    assert owner_ma["required_bars"] == 90
    assert owner_ma["required_sessions"] == 23
    assert owner_ma["admission_target_sessions"] == 67
    with patch.object(owner, "MA_COMPRESSION_READY_BARS", 300):
        ma_domination = owner._current_intraday_window_plan()
    assert ma_domination["admission_target_required_bars"] == 300
    assert ma_domination["admission_target_sessions"] == 75


def test_b5_live_pipeline_chooses_verified_longer_macd_source_when_available() -> None:
    """Extra legitimate sessions cause one larger 5m query; no second source owner."""
    import exchange_calendars as xcals
    from datetime import timedelta
    from src.core.trading_calendar import resolve_forward_sessions_fail_closed

    calendar = xcals.get_calendar("XSHG")
    end = calendar.date_to_session(date(2026, 10, 8))
    sessions = [bar.date() for bar in calendar.sessions_window(end, -67)]
    assert len(sessions) == 67 and sessions[-1] == date(2026, 10, 8)
    assert resolve_forward_sessions_fail_closed(
        "cn", sessions[0] - timedelta(days=1), 67,
    ) == sessions

    pipeline = _intraday_pipeline_for_helper()
    pipeline.fetcher_manager._call_fetcher_method.return_value = (
        _pipeline_canonical_5m(sessions)
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

    pipeline.fetcher_manager._call_fetcher_method.assert_called_once_with(
        pipeline.fetcher_manager._get_fetcher_by_name.return_value,
        "get_intraday_data", "600519",
        start_date=sessions[0].isoformat(),
        end_date=sessions[-1].isoformat(), frequency="5",
    )
    assert {key: len(value) for key, value in result.items()} == {
        "5m": 3216, "15m": 1072, "30m": 536, "60m": 268,
    }


def test_b5_completed_18_sessions_preserve_short_methods_but_not_140_bar_macd() -> None:
    """A valid partial history must not claim the new longer MACD readiness."""
    import exchange_calendars as xcals

    calendar = xcals.get_calendar("XSHG")
    end = calendar.date_to_session(date(2026, 10, 8))
    sessions = [bar.date() for bar in calendar.sessions_window(end, -18)]
    assert len(sessions) == 18
    pipeline = _intraday_pipeline_for_helper()
    pipeline.fetcher_manager._call_fetcher_method.return_value = (
        _pipeline_canonical_5m(sessions[-10:])
    )
    with patch(
        "src.core.pipeline.resolve_latest_completed_session_fail_closed",
        return_value=sessions[-1],
    ), patch(
        "src.core.pipeline.SearchService.is_index_or_etf",
        return_value=False,
    ):
        result = pipeline._load_current_stock_intraday_timeframes(
            code="600519", stock_name="贵州茅台", market="cn",
            target_date=sessions[-1],
            completed_daily_history=pd.DataFrame({"date": sessions}),
        )
    pipeline.fetcher_manager._call_fetcher_method.assert_called_once_with(
        pipeline.fetcher_manager._get_fetcher_by_name.return_value,
        "get_intraday_data", "600519",
        start_date=sessions[-10].isoformat(),
        end_date=sessions[-1].isoformat(), frequency="5",
    )
    assert {key: len(value) for key, value in result.items()} == {
        "5m": 480, "15m": 160, "30m": 80, "60m": 40,
    }


def test_b5_provider_returns_exact_verified_short_suffix_of_long_request() -> None:
    """One 67-session request returning complete last-10 preserves valid short MA input.

    The source identity records the actual long query separately from the
    independently validated returned ten-session content; never claim MACD140.
    """
    import exchange_calendars as xcals
    from data_provider.intraday_data_identity import extract_intraday_data_identity

    calendar = xcals.get_calendar("XSHG")
    end = calendar.date_to_session(date(2026, 10, 8))
    sessions = [bar.date() for bar in calendar.sessions_window(end, -67)]
    returned = _pipeline_canonical_5m(
        sessions[-10:], requested_start=sessions[0],
    )
    identity = extract_intraday_data_identity(returned, strict=True)
    assert identity["requested_start"] == sessions[0].isoformat()
    assert len(returned) == 480

    pipeline = _intraday_pipeline_for_helper()
    pipeline.fetcher_manager._call_fetcher_method.return_value = returned
    with patch(
        "src.core.pipeline.resolve_latest_completed_session_fail_closed",
        return_value=sessions[-1],
    ), patch(
        "src.core.pipeline.SearchService.is_index_or_etf",
        return_value=False,
    ):
        actual = pipeline._load_current_stock_intraday_timeframes(
            code="600519", stock_name="SYNTHETIC", market="cn",
            target_date=sessions[-1],
            completed_daily_history=pd.DataFrame({"date": sessions}),
        )
    pipeline.fetcher_manager._call_fetcher_method.assert_called_once_with(
        pipeline.fetcher_manager._get_fetcher_by_name.return_value,
        "get_intraday_data", "600519",
        start_date=sessions[0].isoformat(),
        end_date=sessions[-1].isoformat(), frequency="5",
    )
    assert {key: len(value) for key, value in actual.items()} == {
        "5m": 480, "15m": 160, "30m": 80, "60m": 40,
    }


def test_b5_provider_short_incomplete_return_still_rejected() -> None:
    """A partial 5m session is not repaired by a short-horizon fallback."""
    import exchange_calendars as xcals

    calendar = xcals.get_calendar("XSHG")
    end = calendar.date_to_session(date(2026, 10, 8))
    sessions = [bar.date() for bar in calendar.sessions_window(end, -67)]
    returned = _pipeline_canonical_5m(
        sessions[-10:], requested_start=sessions[0],
    )
    returned = returned.drop(returned.index[24]).reset_index(drop=True)
    assert len(returned) == 479
    pipeline = _intraday_pipeline_for_helper()
    pipeline.fetcher_manager._call_fetcher_method.return_value = returned
    with patch(
        "src.core.pipeline.resolve_latest_completed_session_fail_closed",
        return_value=sessions[-1],
    ), patch(
        "src.core.pipeline.SearchService.is_index_or_etf",
        return_value=False,
    ):
        actual = pipeline._load_current_stock_intraday_timeframes(
            code="600519", stock_name="SYNTHETIC", market="cn",
            target_date=sessions[-1],
            completed_daily_history=pd.DataFrame({"date": sessions}),
        )
    pipeline.fetcher_manager._call_fetcher_method.assert_called_once()
    assert actual == {}


def test_b5_missing_older_sessions_does_not_erase_complete_short_window() -> None:
    """A broken 18-session candidate must not fake MACD history or lose valid MA/RSI."""
    import exchange_calendars as xcals

    calendar = xcals.get_calendar("XSHG")
    end = calendar.date_to_session(date(2026, 10, 8))
    nineteen = [session.date() for session in calendar.sessions_window(end, -19)]
    valid_eighteen = nineteen[1:]
    with_one_older_gap = [nineteen[0], *nineteen[2:]]
    assert len(with_one_older_gap) == 18
    assert len(valid_eighteen) == 18
    assert with_one_older_gap[-10:] == valid_eighteen[-10:]
    pipeline = _intraday_pipeline_for_helper()
    pipeline.fetcher_manager._call_fetcher_method.return_value = (
        _pipeline_canonical_5m(valid_eighteen[-10:])
    )
    with patch(
        "src.core.pipeline.resolve_latest_completed_session_fail_closed",
        return_value=valid_eighteen[-1],
    ), patch(
        "src.core.pipeline.SearchService.is_index_or_etf",
        return_value=False,
    ):
        frames = pipeline._load_current_stock_intraday_timeframes(
            code="600519", stock_name="贵州茅台", market="cn",
            target_date=valid_eighteen[-1],
            completed_daily_history=pd.DataFrame({"date": with_one_older_gap}),
        )
    pipeline.fetcher_manager._call_fetcher_method.assert_called_once_with(
        pipeline.fetcher_manager._get_fetcher_by_name.return_value,
        "get_intraday_data", "600519",
        start_date=valid_eighteen[-10].isoformat(),
        end_date=valid_eighteen[-1].isoformat(), frequency="5",
    )
    assert {key: len(value) for key, value in frames.items()} == {
        "5m": 480, "15m": 160, "30m": 80, "60m": 40,
    }


def test_daily_window_plan_is_method_and_timeframe_driven_not_magic_days() -> None:
    from src.core import pipeline as pipeline_module
    from src.services.evidence_traceability_registry import compile_method_window_policy_view

    target = date(2026, 10, 12)
    current = pipeline_module._current_daily_window_plan(
        market="cn",
        target_date=target,
    )
    assert current["source_only_timeframe_required_bars"] == {
        "daily": 268, "weekly": 40, "monthly": 40,
    }
    assert current["timeframe_required_bars"] == {
        "daily": 268, "weekly": 268, "monthly": 140,
    }
    assert current["start_by_timeframe"]["monthly"] == date(2015, 2, 2)
    assert current["start_date"] == date(2015, 2, 2)
    assert current["method_required_bars"]["daily"]["CUP_HANDLE"] == 110
    assert current["method_required_bars"]["monthly"]["MA_COMPRESSION_RELEASE"] == 40
    assert current["method_window_classes"]["daily"]["CUP_HANDLE"] == (
        "VARIABLE_STRUCTURE",
    )
    assert current["method_window_classes"]["monthly"][
        "MA_COMPRESSION_RELEASE"
    ] == (
        "FIXED_ROLLING",
        "ADAPTIVE_CONTEXT",
        "INCREMENTAL_STATE_MACHINE",
    )

    rows = compile_method_window_policy_view()["methods"]
    candlestick = next(row for row in rows if row["method_id"] == "CANDLESTICK")
    minimal = pipeline_module._current_daily_window_plan(
        market="cn",
        target_date=target,
        method_rows=(candlestick,),
    )
    assert minimal["timeframe_required_bars"] == {
        "daily": 2,
        "weekly": 0,
        "monthly": 0,
    }
    assert minimal["start_date"] == date(2026, 10, 9)

    cup = next(row for row in rows if row["method_id"] == "CUP_HANDLE")
    monthly_cup = pipeline_module._current_daily_window_plan(
        market="cn",
        target_date=target,
        method_rows=({**cup, "current_execution_timeframes": ("monthly",)},),
    )
    assert monthly_cup["timeframe_required_bars"]["monthly"] == 110
    assert monthly_cup["method_required_bars"]["monthly"]["CUP_HANDLE"] == 110
    # Compare hypothetical 110-month CUP_HANDLE to its own 40-month
    # source-only MA baseline, not to the unrelated full MTF recursive plan.
    ma_compression = next(
        row for row in rows if row["method_id"] == "MA_COMPRESSION_RELEASE"
    )
    monthly_ma = pipeline_module._current_daily_window_plan(
        market="cn", target_date=target,
        method_rows=({**ma_compression, "current_execution_timeframes": ("monthly",)},),
    )
    assert monthly_ma["timeframe_required_bars"]["monthly"] == 40
    assert monthly_cup["start_date"] < monthly_ma["start_date"]

    dual_timeframe_cup = {
        **cup,
        "current_execution_timeframes": ("daily", "monthly"),
    }
    with patch.object(
        pipeline_module,
        "_current_method_required_bars",
        return_value={"CUP_HANDLE": {"daily": 110, "monthly": 75}},
    ):
        timeframe_specific = pipeline_module._current_daily_window_plan(
            market="cn",
            target_date=target,
            method_rows=(dual_timeframe_cup,),
        )
    assert timeframe_specific["timeframe_required_bars"] == {
        "daily": 110,
        "weekly": 0,
        "monthly": 75,
    }
    assert timeframe_specific["method_required_bars"]["daily"]["CUP_HANDLE"] == 110
    assert timeframe_specific["method_required_bars"]["monthly"]["CUP_HANDLE"] == 75

    original_cup_only = pipeline_module._current_daily_window_plan(
        market="cn", target_date=target, method_rows=(cup,),
    )
    assert original_cup_only["timeframe_required_bars"]["daily"] == 110
    with patch.object(pipeline_module, "CUP_MAX_DURATION_SESSIONS", 130):
        expanded_daily_cup = pipeline_module._current_daily_window_plan(
            market="cn",
            target_date=target,
            method_rows=(cup,),
        )
    assert expanded_daily_cup["timeframe_required_bars"]["daily"] == 150
    assert expanded_daily_cup["start_date"] < original_cup_only["start_date"]
    assert not hasattr(pipeline_module, "DEEP_HISTORY_LOOKBACK_CALENDAR_DAYS")


def test_intraday_wiring_refuses_unverified_exchange_sessions_before_provider() -> None:
    """Missing one real XSHG session must never be hidden by 10 available daily rows."""
    from src.core.trading_calendar import resolve_forward_sessions_fail_closed

    true_sessions = resolve_forward_sessions_fail_closed(
        "cn", date(2026, 9, 11), 11,
    )
    assert true_sessions is not None and len(true_sessions) == 11
    assert true_sessions[-1] == date(2026, 9, 29)
    missing_history = [d for d in true_sessions if d != date(2026, 9, 23)]
    assert len(missing_history) == 10 and missing_history[-1] == true_sessions[-1]
    pipeline = _intraday_pipeline_for_helper()

    with patch(
        "src.core.pipeline.resolve_latest_completed_session_fail_closed",
        return_value=true_sessions[-1],
    ), patch(
        "src.core.pipeline.SearchService.is_index_or_etf",
        return_value=False,
    ):
        context = pipeline._load_current_stock_intraday_timeframes(
            code="600519",
            stock_name="贵州茅台",
            market="cn",
            target_date=true_sessions[-1],
            completed_daily_history=pd.DataFrame({"date": missing_history}),
        )

    assert context == {}
    pipeline.fetcher_manager._get_fetcher_by_name.assert_not_called()
    pipeline.fetcher_manager._call_fetcher_method.assert_not_called()


def test_intraday_wiring_refuses_holiday_row_before_provider() -> None:
    """A ten-row frame with 2026-09-25 (XSHG holiday) must not pass as ten sessions."""
    from src.core.trading_calendar import resolve_forward_sessions_fail_closed

    true_sessions = resolve_forward_sessions_fail_closed(
        "cn", date(2026, 9, 11), 10,
    )
    assert true_sessions is not None and true_sessions[-1] == date(2026, 9, 28)
    holiday_history = sorted(
        d for d in true_sessions if d != date(2026, 9, 23)
    ) + [date(2026, 9, 25)]
    holiday_history = sorted(holiday_history)
    assert len(holiday_history) == 10 and holiday_history[-1] == true_sessions[-1]
    pipeline = _intraday_pipeline_for_helper()

    with patch(
        "src.core.pipeline.resolve_latest_completed_session_fail_closed",
        return_value=true_sessions[-1],
    ), patch(
        "src.core.pipeline.SearchService.is_index_or_etf",
        return_value=False,
    ):
        context = pipeline._load_current_stock_intraday_timeframes(
            code="600519",
            stock_name="贵州茅台",
            market="cn",
            target_date=true_sessions[-1],
            completed_daily_history=pd.DataFrame({"date": holiday_history}),
        )

    assert context == {}
    pipeline.fetcher_manager._get_fetcher_by_name.assert_not_called()
    pipeline.fetcher_manager._call_fetcher_method.assert_not_called()



def test_intraday_wiring_refuses_unavailable_exchange_calendar_before_provider() -> None:
    """Even a legal looking ten-row daily history cannot bypass missing calendar."""
    pipeline = _intraday_pipeline_for_helper()
    sessions = _pipeline_intraday_sessions()
    with patch(
        "src.core.pipeline.resolve_latest_completed_session_fail_closed",
        return_value=sessions[-1],
    ), patch(
        "src.core.pipeline.resolve_forward_sessions_fail_closed",
        return_value=None,
    ), patch(
        "src.core.pipeline.SearchService.is_index_or_etf",
        return_value=False,
    ):
        context = pipeline._load_current_stock_intraday_timeframes(
            code="600519",
            stock_name="贵州茅台",
            market="cn",
            target_date=sessions[-1],
            completed_daily_history=pd.DataFrame({"date": sessions}),
        )
    assert context == {}
    pipeline.fetcher_manager._get_fetcher_by_name.assert_not_called()
    pipeline.fetcher_manager._call_fetcher_method.assert_not_called()


def test_intraday_wiring_expands_actual_query_when_owner_warmup_grows() -> None:
    from src.core import pipeline as pipeline_module
    from src.core.trading_calendar import resolve_forward_sessions_fail_closed

    sessions = resolve_forward_sessions_fail_closed(
        "cn", date(2026, 9, 8), 16,
    )
    assert sessions is not None and sessions[-1] == date(2026, 10, 8)
    frame = _pipeline_canonical_5m(sessions)
    pipeline = _intraday_pipeline_for_helper()
    pipeline.fetcher_manager._call_fetcher_method.return_value = frame

    with patch.object(
        pipeline_module, "MA_COMPRESSION_READY_BARS", 61,
    ), patch(
        "src.core.pipeline.resolve_latest_completed_session_fail_closed",
        return_value=sessions[-1],
    ), patch(
        "src.core.pipeline.SearchService.is_index_or_etf",
        return_value=False,
    ):
        context = pipeline._load_current_stock_intraday_timeframes(
            code="600519",
            stock_name="贵州茅台",
            market="cn",
            target_date=sessions[-1],
            completed_daily_history=pd.DataFrame({"date": sessions}),
        )

    pipeline.fetcher_manager._call_fetcher_method.assert_called_once_with(
        pipeline.fetcher_manager._get_fetcher_by_name.return_value,
        "get_intraday_data",
        "600519",
        start_date=sessions[0].isoformat(),
        end_date=sessions[-1].isoformat(),
        frequency="5",
    )
    assert {key: len(value) for key, value in context.items()} == {
        "5m": 768,
        "15m": 256,
        "30m": 128,
        "60m": 64,
    }


def test_current_cn_stock_wiring_uses_named_baostock_and_method_window_sessions() -> None:
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
        return_value=date(2026, 9, 29),
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

    daily_history_start = date(2023, 6, 1)

    with patch(
        "src.core.pipeline._current_daily_window_plan",
        return_value={"start_date": daily_history_start},
    ) as daily_plan, patch(
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
    daily_plan.assert_called_once_with(
        market="cn",
        target_date=date(2026, 9, 25),
    )
    pipeline.db.get_data_range.assert_called_once_with(
        "600519",
        daily_history_start,
        date(2026, 9, 25),
    )
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
