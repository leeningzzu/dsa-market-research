# -*- coding: utf-8 -*-
"""
===================================
A股自选股智能分析系统 - 核心分析流水线
===================================

职责：
1. 管理整个分析流程
2. 协调数据获取、存储、搜索、分析、通知等模块
3. 实现并发控制和异常处理
4. 提供股票分析的核心功能
"""

import json
import logging
import math
import inspect
import threading
import time
import uuid
from copy import deepcopy
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from typing import List, Dict, Any, Mapping, Optional, Tuple, Callable

import pandas as pd

from src.config import FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT, get_config, Config
from src.storage import get_db
from data_provider import DataFetcherManager
from data_provider.base import is_bse_code, normalize_stock_code
from src.data.stock_mapping import resolve_cn_price_asset

from data_provider.daily_data_identity import (
    DAILY_ACQUISITION_RECEIPT_SCHEMA_VERSION,
    DAILY_WINDOW_EXTENSION_RECEIPT_SCHEMA_VERSION,
    DailyDataIdentityError,
    attach_daily_acquisition_receipt,
    attach_daily_data_identity,
    extract_daily_acquisition_receipt,
    extract_daily_data_identity,
    extract_daily_data_identity_from_records,
    rebind_daily_data_identity,
    seal_daily_acquisition_receipt,
    seal_daily_window_extension_receipt,
    verify_observed_daily_frame_request,
)
from data_provider.us_index_mapping import is_us_index_code
from data_provider.realtime_types import ChipDistribution
from src.analyzer import (
    GeminiAnalyzer,
    AnalysisResult,
    fill_price_position_if_needed,
    normalize_chip_structure_availability,
    populate_decision_action_fields,
    stabilize_decision_with_structure,
)
from src.notification import NotificationService, NotificationChannel
from src.schemas.decision_action import (
    has_canonical_action_authority_for_result,
    normalize_decision_action,
)
from src.report_language import (
    get_placeholder_text,
    get_unknown_text,
    infer_decision_type_from_advice,
    localize_confidence_level,
    localize_operation_advice,
    localize_trend_prediction,
    normalize_report_language,
)
from src.search_service import SearchService
from src.analysis_context_pack_prompt import format_analysis_context_pack_prompt_section
from src.analysis_context_pack_overview import render_analysis_context_pack_overview
from src.market_phase_summary import MARKET_PHASE_SUMMARY_KEY, render_market_phase_summary
from src.daily_market_context_guardrail import apply_daily_market_context_guardrail
from src.agent.final_explanation import (
    PipelineActionAdjustment,
    build_pipeline_final_explanation,
    capture_pipeline_action_adjustment,
)
from src.formatters import strip_hidden_markdown_metadata
from src.phase_decision_guardrail import apply_phase_decision_guardrails
from src.services.daily_market_context import (
    DailyMarketContext,
    DailyMarketContextService,
    format_daily_market_context_prompt_section,
)
from src.services.social_sentiment_service import SocialSentimentService
from src.services.intelligence_service import IntelligenceService
from src.services.market_hotspot_service import MarketHotspotService
from src.services.analysis_context_builder import (
    AnalysisContextBuilder,
    PipelineAnalysisArtifacts,
)
from src.services.market_structure_service import MarketStructureService
from src.services.relative_strength_service import (
    DEFAULT_HORIZON_SESSIONS as RELATIVE_STRENGTH_HORIZON_SESSIONS,
    RelativeStrengthService,
)
from src.services.supply_demand_service import (
    REQUIRED_OBSERVATIONS as SUPPLY_REQUIRED_OBSERVATIONS,
    build_supply_demand_context,
)
from src.services.cost_structure_service import (
    REFERENCE_WINDOWS as COST_REFERENCE_WINDOWS,
    build_cost_structure_context,
)
from src.services.price_structure_service import build_price_structure_context
from src.services.price_structure_service import (
    MIN_OBSERVATIONS as PRICE_STRUCTURE_MIN_OBSERVATIONS,
)
from src.services.volatility_momentum_service import build_volatility_momentum_context
from src.services.volatility_momentum_service import (
    READY_OBSERVATIONS as MOMENTUM_READY_OBSERVATIONS,
    WILDER_ATR_PERIOD,
    rsi_startup_comparison_required_bars,
    atr_startup_comparison_required_bars,
)
from src.services.pattern_trigger_service import build_pattern_trigger_context
from src.services.business_valuation_service import compile_business_valuation
from src.services.data_object_admission import (
    DataObjectAdmissionError,
    bind_b1_rights_context_as_of,
    load_b1_rights_context,
    validate_b1_rights_context,
    validate_b1_provider_preflight,
)
from src.services.fundamental_quality_service import compile_fundamental_quality
from src.services.pattern_trigger_service import (
    CUP_MAX_DURATION_SESSIONS,
    DOUBLE_BOTTOM_MAX_SEPARATION_SESSIONS,
    FLAT_BASE_WINDOW,
    MIN_OBSERVATIONS as PATTERN_MIN_OBSERVATIONS,
    PRIOR_TREND_WINDOW,
    TIGHT_CONSOLIDATION_WINDOW,
)
from src.services.candlestick_pattern_service import (
    MIN_OBSERVATIONS as CANDLESTICK_MIN_OBSERVATIONS,
)
from src.services.multi_timeframe_structure_service import (
    MA_COMPRESSION_READY_BARS,
    MA_LEVEL_READY_BARS,
    MA_SLOPE_CROSS_READY_BARS,
    TREND_MIN_BARS,
    build_multi_timeframe_structure_context,
    macd_startup_comparison_required_bars,
)
from src.services.intraday_bar_service import (
    IntradayBarError,
    aggregate_completed_intraday_bars,
    validate_complete_cn_5m_sessions,
)
from src.services.evidence_traceability_registry import (
    build_method_execution_receipt,
    compile_method_window_policy_view,
    compile_mtf_method_window_readiness_trace,
)
from src.services.pit_identity import (
    build_completed_history_identity,
    normalize_research_selection_context,
    sha256_payload,
)
from src.services.run_diagnostics import (
    activate_run_diagnostic_context,
    current_diagnostic_snapshot,
    get_current_diagnostic_context,
    record_history_run,
    record_llm_run,
    record_llm_run_started,
    record_notification_run,
    reset_run_diagnostic_context,
    sanitize_diagnostic_text,
)
from src.services.decision_signal_extractor import (
    extract_and_persist_from_analysis_result,
    resolve_decision_signal_action_fields,
)
from src.services.decision_signal_summary import summarize_decision_signal
from src.services.factor_decision_summary import (
    apply_canonical_decision_to_result,
    assert_canonical_consumer_consistency,
    build_stock_factor_decision_summary,
    canonical_explanation_degradation_eligible,
    validate_canonical_factor_binding,
    validate_investor_brief_binding,
)
from src.enums import ReportType
from src.stock_analyzer import StockTrendAnalyzer, TrendAnalysisResult
from src.core.trading_calendar import (
    build_market_phase_context,
    get_effective_trading_date,
    get_market_for_stock,
    get_market_now,
    resolve_forward_sessions_fail_closed,
    resolve_latest_completed_session_fail_closed,
    resolve_required_daily_history_start_fail_closed,
    resolve_session_range_fail_closed,
    is_market_open,
)
from data_provider.us_index_mapping import is_us_stock_code
from bot.models import BotMessage


logger = logging.getLogger(__name__)

# Daily acquisition is compiled from the current method owners and their
# currently admitted timeframes.  No replacement calendar-day magic number is
# allowed: the exchange calendar maps D/W/M bar requirements to one exact
# completed-daily start session.
DAILY_WINDOW_PLAN_VERSION = "method-owner-timeframe-recursive-calendar-v2"
_DAILY_SOURCE_TIMEFRAMES = ("daily", "weekly", "monthly")


def _current_method_required_bars() -> Dict[str, Any]:
    """Return leaf warm-ups; a method may override them by timeframe.

    Scalar values apply to every currently routed timeframe.  Mapping values
    permit one owner to expose distinct K-line counts per timeframe without
    changing this compiler or introducing a new calendar-day constant.
    """

    return {
        "RELATIVE_STRENGTH": RELATIVE_STRENGTH_HORIZON_SESSIONS + 1,
        "MA_LEVEL_ALIGNMENT_DAILY": StockTrendAnalyzer.MA_ALIGNMENT_REQUIRED_BARS,
        "SUPPLY_RELATIVE_VOLUME": SUPPLY_REQUIRED_OBSERVATIONS,
        "SUPPLY_DIRECTIONAL_VOLUME": SUPPLY_REQUIRED_OBSERVATIONS,
        "SUPPLY_CMF": SUPPLY_REQUIRED_OBSERVATIONS,
        "COST_ROLLING_REFERENCE": max(COST_REFERENCE_WINDOWS),
        "PRICE_PIVOT_SWING": PRICE_STRUCTURE_MIN_OBSERVATIONS,
        "BREAKOUT_RETEST_FAILED": PRICE_STRUCTURE_MIN_OBSERVATIONS,
        "MACD": macd_startup_comparison_required_bars(),
        "RSI": rsi_startup_comparison_required_bars(),
        "ROC": MOMENTUM_READY_OBSERVATIONS,
        "VOLATILITY_TR_SMA": MOMENTUM_READY_OBSERVATIONS,
        "CONFIRMED_DIVERGENCE": MOMENTUM_READY_OBSERVATIONS,
        "ATR_WILDER": atr_startup_comparison_required_bars(),
        "CUP_HANDLE": PRIOR_TREND_WINDOW + CUP_MAX_DURATION_SESSIONS,
        "DOUBLE_BOTTOM": PRIOR_TREND_WINDOW + DOUBLE_BOTTOM_MAX_SEPARATION_SESSIONS,
        "VCP": PATTERN_MIN_OBSERVATIONS,
        "FLAT_BASE": max(PATTERN_MIN_OBSERVATIONS, FLAT_BASE_WINDOW),
        "TIGHT_CONSOLIDATION": max(
            PATTERN_MIN_OBSERVATIONS,
            TIGHT_CONSOLIDATION_WINDOW,
        ),
        "MTF_MA_LEVEL_ALIGNMENT": MA_LEVEL_READY_BARS,
        "MTF_MOMENTUM_CONTEXT": TREND_MIN_BARS,
        "MTF_PRICE_STRUCTURE": PRICE_STRUCTURE_MIN_OBSERVATIONS,
        "MA_SLOPE_CROSS": MA_SLOPE_CROSS_READY_BARS,
        "MA_COMPRESSION_RELEASE": MA_COMPRESSION_READY_BARS,
        "CANDLESTICK": CANDLESTICK_MIN_OBSERVATIONS,
    }


def _current_daily_window_plan(
    *,
    market: Optional[str],
    target_date: date,
    method_rows: Optional[Any] = None,
) -> Dict[str, Any]:
    """Compile exact D/W/M source history from admitted leaf-method owners."""

    rows = tuple(
        method_rows
        if method_rows is not None
        else compile_method_window_policy_view()["methods"]
    )
    requirements = _current_method_required_bars()
    method_required_bars: Dict[str, Dict[str, int]] = {
        timeframe: {} for timeframe in _DAILY_SOURCE_TIMEFRAMES
    }
    method_window_classes: Dict[str, Dict[str, Tuple[str, ...]]] = {
        timeframe: {} for timeframe in _DAILY_SOURCE_TIMEFRAMES
    }
    unresolved: List[str] = []
    for row in rows:
        if row.get("leaf_implementation_state") != "EXISTING_REUSED":
            continue
        timeframes = tuple(row.get("current_execution_timeframes") or ())
        relevant = tuple(
            timeframe for timeframe in timeframes
            if timeframe in _DAILY_SOURCE_TIMEFRAMES
        )
        if not relevant:
            continue
        method_id = str(row.get("method_id") or "").strip()
        requirement = requirements.get(method_id)
        window_classes = tuple(row.get("window_classes") or ())
        for timeframe in relevant:
            if isinstance(requirement, Mapping):
                required = requirement.get(timeframe, requirement.get("*"))
            else:
                required = requirement
            if (
                isinstance(required, bool)
                or not isinstance(required, int)
                or required <= 0
            ):
                unresolved.append(f"{method_id or 'UNKNOWN_METHOD'}@{timeframe}")
                continue
            method_required_bars[timeframe][method_id] = required
            method_window_classes[timeframe][method_id] = window_classes
    if unresolved:
        raise RuntimeError(
            "DAILY_WINDOW_REQUIREMENT_MISSING:" + ",".join(sorted(set(unresolved)))
        )

    source_only_timeframe_required_bars = {
        timeframe: max(values.values(), default=0)
        for timeframe, values in method_required_bars.items()
    }
    # Preserve the legal MA/structure baseline even when a native recursive
    # MACD/RSI comparison needs more completed W/M bars. These are SOURCE
    # acquisition targets, not algorithm readiness, PIT or trading authority.
    timeframe_required_bars = dict(source_only_timeframe_required_bars)
    calendar_plan = resolve_required_daily_history_start_fail_closed(
        market, target_date, timeframe_required_bars,
    )
    if calendar_plan is None:
        raise RuntimeError("DAILY_HISTORY_CALENDAR_PLAN_UNAVAILABLE")

    mtf_recursive_comparison_required_bars: Dict[str, int] = {}
    mtf_recursive_calendar_status: Dict[str, Dict[str, str]] = {}
    routed_higher_timeframes = tuple(
        timeframe for timeframe in ("weekly", "monthly")
        if "MTF_MOMENTUM_CONTEXT" in method_required_bars[timeframe]
    )
    if routed_higher_timeframes:
        mtf_recursive_comparison_required_bars = {
            "MACD_STARTUP_COMPARISON": macd_startup_comparison_required_bars(),
            "RSI_STARTUP_COMPARISON": rsi_startup_comparison_required_bars(),
        }
        # Attempt longer native RSI then MACD. The installed exchange
        # calendar may not reach 268 completed months; that must not erase
        # a reachable 140-month MACD, 40-month MA or source-only evidence.
        for timeframe in routed_higher_timeframes:
            status_by_component: Dict[str, str] = {}
            for component, required in sorted(
                mtf_recursive_comparison_required_bars.items(),
                key=lambda item: (-item[1], item[0]),
            ):
                candidate_required = max(timeframe_required_bars[timeframe], required)
                if candidate_required == timeframe_required_bars[timeframe]:
                    status_by_component[component] = "ACQUISITION_PLANNED"
                    continue
                tentative = dict(timeframe_required_bars)
                tentative[timeframe] = candidate_required
                planned = resolve_required_daily_history_start_fail_closed(
                    market, target_date, tentative,
                )
                if planned is None:
                    status_by_component[component] = "CALENDAR_HISTORY_OUT_OF_RANGE"
                    continue
                timeframe_required_bars = tentative
                calendar_plan = planned
                status_by_component[component] = "ACQUISITION_PLANNED"
            mtf_recursive_calendar_status[timeframe] = status_by_component

    identity = {
        "version": DAILY_WINDOW_PLAN_VERSION,
        "market": str(market or "").strip().lower() or None,
        "target_date": target_date.isoformat(),
        "timeframe_required_bars": timeframe_required_bars,
        "source_only_timeframe_required_bars": source_only_timeframe_required_bars,
        "method_required_bars": method_required_bars,
        "method_window_classes": method_window_classes,
        "mtf_recursive_comparison_required_bars": mtf_recursive_comparison_required_bars,
        "mtf_recursive_calendar_status": mtf_recursive_calendar_status,
    }
    return {
        **calendar_plan,
        **identity,
        "plan_hash": sha256_payload(identity),
    }

def _next_bounded_monthly_macd_window(
    *,
    base_plan: Mapping[str, Any],
    target_date: date,
    receipt: Mapping[str, Any],
    attempted_monthly_bars: Tuple[int, ...],
) -> Dict[str, Any]:
    """Advise a bounded read-only extension after an actual native MACD drift.

    A minimum recursive warm-up is not a convergence claim. Only an exact
    native multi-start comparison showing drift requests more completed
    months, in steps derived from the existing MACD owner formula. This
    function NEVER fetches, persists, labels, trades or upgrades evidence.
    The caller separately owns provider rights, source-identity checks and
    actual fresh execution at the proposed date.
    """
    base: Dict[str, Any] = {
        "status": "NO_EXTENSION",
        "reason": "PROOF_NOT_ADMITTED",
        "storage_admitted": False,
        "strategy_admitted": False,
        "training_admitted": False,
        "numerical_convergence_proven": False,
        "external_fetch_performed": False,
    }

    def reject(reason: str) -> Dict[str, Any]:
        return {**base, "reason": reason}

    if (
        not isinstance(base_plan, Mapping)
        or not isinstance(target_date, date)
        or isinstance(target_date, datetime)
        or base_plan.get("market") != "cn"
        or base_plan.get("target_date") != target_date.isoformat()
    ):
        return reject("PLAN_CURRENTNESS_UNPROVEN")
    method_rows = base_plan.get("method_required_bars")
    monthly = method_rows.get("monthly") if isinstance(method_rows, Mapping) else None
    if not isinstance(monthly, Mapping) or "MTF_MOMENTUM_CONTEXT" not in monthly:
        return reject("MONTHLY_MTF_METHOD_NOT_ROUTED")
    calendar_status = base_plan.get("mtf_recursive_calendar_status")
    states = calendar_status.get("monthly") if isinstance(calendar_status, Mapping) else None
    if not isinstance(states, Mapping) or states.get("MACD_STARTUP_COMPARISON") != "ACQUISITION_PLANNED":
        return reject("NATIVE_RECURSIVE_STARTUP_NOT_AVAILABLE")
    if not isinstance(attempted_monthly_bars, (tuple, list)) or not attempted_monthly_bars:
        return reject("ATTEMPT_IDENTITY_MISSING")
    attempts = tuple(attempted_monthly_bars)
    if any(isinstance(count, bool) or not isinstance(count, int) or count <= 0 for count in attempts):
        return reject("ATTEMPT_IDENTITY_INVALID")
    timeframe_rows = base_plan.get("timeframe_required_bars")
    baseline = timeframe_rows.get("monthly") if isinstance(timeframe_rows, Mapping) else None
    if attempts[0] != baseline or any(a >= b for a, b in zip(attempts, attempts[1:])):
        return reject("ATTEMPT_SEQUENCE_UNPROVEN")
    if not isinstance(receipt, Mapping):
        return reject("NATIVE_EXECUTION_RECEIPT_MISSING")
    if receipt.get("status") == "READY":
        return reject("NATIVE_MACD_ALREADY_READY")
    if len(attempts) - 1 >= 3:
        return reject("BOUNDED_RETRY_BUDGET_EXHAUSTED")
    if not (
        receipt.get("status") == "UNKNOWN"
        and receipt.get("method_owner") == "StockTrendAnalyzer._calculate_macd"
        and receipt.get("reason") == "MACD_MULTI_START_NUMERIC_OR_STATE_DRIFT"
        and receipt.get("readiness_scope") == "OBSERVED_COMPLETED_PREFIXES_ONLY"
        and receipt.get("required_observations") == macd_startup_comparison_required_bars()
        and type(receipt.get("observations")) is int
        and receipt["observations"] == attempts[-1]
    ):
        return reject("NATIVE_DRIFT_RECEIPT_NOT_PROVEN")
    increment = StockTrendAnalyzer.MACD_SLOW + StockTrendAnalyzer.MACD_SIGNAL
    if len(attempts) - 1 == 2:
        # Exactly one final, smaller-granularity attempt is possible only
        # when native independent comparison proves state agreement and a
        # finite numeric difference just above (within 2x) its tolerance.
        # The signal period is a native increment, NOT a magic 220-month
        # universal floor or relaxation of the convergence tolerance.
        difference = receipt.get("max_endpoint_difference")
        tolerance = receipt.get("absolute_tolerance")
        if not (
            type(difference) in (int, float)
            and type(tolerance) in (int, float)
            and math.isfinite(difference)
            and math.isfinite(tolerance)
            and tolerance > 0
            and tolerance < difference <= 2 * tolerance
            and receipt.get("startup_state_agreement") is True
            and receipt.get("numeric_agreement") is False
        ):
            return reject("BOUNDED_RETRY_BUDGET_EXHAUSTED")
        increment = StockTrendAnalyzer.MACD_SIGNAL
    if increment <= 0:
        return reject("INVALID_NATIVE_FORMULA")
    next_count = attempts[-1] + increment
    proposed = dict(timeframe_rows)
    proposed["monthly"] = max(int(proposed["monthly"]), next_count)
    calendar_plan = resolve_required_daily_history_start_fail_closed(
        "cn", target_date, proposed,
    )
    if calendar_plan is None:
        return reject("EXCHANGE_CALENDAR_EXTENSION_UNAVAILABLE")
    return {
        **base,
        "status": "READ_ONLY_EXTENSION_AVAILABLE",
        "reason": "NATIVE_MACD_NUMERIC_DRIFT_NEEDS_MORE_OBSERVED_HISTORY",
        "proposed_monthly_bars": next_count,
        "increment_bars": increment,
        "new_start_date": calendar_plan["start_date"],
        "start_by_timeframe": calendar_plan["start_by_timeframe"],
        "attempted_monthly_bars": attempts,
        "next_attempt_number": len(attempts),
    }


def _daily_plan_with_monthly_macd_bars(
    *, base_plan: Mapping[str, Any], target_date: date, monthly_bars: int,
) -> Dict[str, Any]:
    """Rebuild one exact calendar plan after the existing MACD owner requests more months."""
    if (
        not isinstance(base_plan, Mapping)
        or isinstance(monthly_bars, bool)
        or not isinstance(monthly_bars, int)
        or monthly_bars <= 0
        or base_plan.get("market") != "cn"
        or base_plan.get("target_date") != target_date.isoformat()
    ):
        raise RuntimeError("DAILY_MONTHLY_EXTENSION_PLAN_INVALID")
    required = dict(base_plan.get("timeframe_required_bars") or {})
    current = required.get("monthly")
    if not isinstance(current, int) or monthly_bars < current:
        raise RuntimeError("DAILY_MONTHLY_EXTENSION_BARS_INVALID")
    required["monthly"] = monthly_bars
    calendar_plan = resolve_required_daily_history_start_fail_closed(
        "cn", target_date, required,
    )
    if calendar_plan is None:
        raise RuntimeError("DAILY_MONTHLY_EXTENSION_CALENDAR_UNAVAILABLE")
    identity = {
        "version": base_plan.get("version"),
        "market": base_plan.get("market"),
        "target_date": base_plan.get("target_date"),
        "timeframe_required_bars": required,
        "source_only_timeframe_required_bars": dict(
            base_plan.get("source_only_timeframe_required_bars") or {}
        ),
        "method_required_bars": deepcopy(base_plan.get("method_required_bars") or {}),
        "method_window_classes": deepcopy(base_plan.get("method_window_classes") or {}),
        "mtf_recursive_comparison_required_bars": dict(
            base_plan.get("mtf_recursive_comparison_required_bars") or {}
        ),
        "mtf_recursive_calendar_status": deepcopy(
            base_plan.get("mtf_recursive_calendar_status") or {}
        ),
    }
    return {
        **calendar_plan,
        **identity,
        "plan_hash": sha256_payload(identity),
    }


def _monthly_macd_receipt_from_daily_history(
    *, frame: pd.DataFrame, code: str, market: str, target_date: date,
    trend_analyzer: Any, source_observed_at: Any,
) -> Tuple[Mapping[str, Any], int]:
    trend_result = trend_analyzer.analyze(frame.copy(), code)
    observed = pd.to_datetime(source_observed_at, utc=True, errors="raise").to_pydatetime()
    context = build_multi_timeframe_structure_context(
        stock_code=code,
        history=frame,
        target_date=target_date,
        market=market,
        trend_analyzer=trend_analyzer,
        daily_trend_result=trend_result,
        snapshot_observed_at=observed,
    )
    monthly = (context.get("timeframes") or {}).get("monthly") or {}
    receipt = monthly.get("macd_startup_stability")
    observations = monthly.get("observations")
    if not isinstance(receipt, Mapping) or not isinstance(observations, int):
        raise DailyDataIdentityError("MONTHLY_MACD_EXECUTION_RECEIPT_MISSING")
    return receipt, observations


def _daily_row_record(row: Any) -> Dict[str, Any]:
    to_dict = getattr(row, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
        return dict(value) if isinstance(value, Mapping) else {}
    return {
        key: getattr(row, key, None)
        for key in (
            "code", "date", "open", "high", "low", "close", "volume",
            "amount", "pct_chg", "ma5", "ma10", "ma20", "volume_ratio",
            "data_source", "data_identity_json", "data_identity_hash",
        )
    }


def _daily_observation_ceiling(current_time: Optional[datetime]) -> datetime:
    value = current_time or datetime.now(timezone.utc)
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _provider_observation_ceiling(
    current_time: Optional[datetime],
    *,
    market: Optional[str],
    target_date: date,
    observation_time_frozen: bool,
) -> datetime:
    """Keep replay clocks frozen while current runs use actual response time."""
    if observation_time_frozen:
        return _daily_observation_ceiling(current_time)
    latest = resolve_latest_completed_session_fail_closed(market)
    if latest != target_date:
        return _daily_observation_ceiling(current_time)
    return datetime.now(timezone.utc)


def _load_verified_cached_daily_history(
    *,
    db: Any,
    code: str,
    market: Optional[str],
    target_date: date,
    window_plan: Mapping[str, Any],
    current_time: Optional[datetime] = None,
) -> Optional[pd.DataFrame]:
    """Return a content-bound current daily cache view or fail closed.

    Date coverage alone is never sufficient. The original persisted response is
    reconstructed and verified before a bounded run-local view is admitted.
    This read proves neither vendor storage rights nor historical PIT.
    """
    start = window_plan.get("start_date")
    asset_admission = resolve_cn_price_asset(code)
    if str(market or "").lower() == "cn" and asset_admission["status"] != "ADMITTED":
        return None
    normalized_code = normalize_stock_code(str(code or ""))
    if (
        not normalized_code
        or not isinstance(start, date)
        or isinstance(start, datetime)
        or start > target_date
    ):
        return None
    try:
        expected = resolve_session_range_fail_closed(market, start, target_date)
        if not expected:
            return None
        candidate_rows = db.get_data_range(code, start, target_date)
        if not isinstance(candidate_rows, (list, tuple)) or not candidate_rows:
            return None
        candidate_records = [_daily_row_record(row) for row in candidate_rows]
        if any(
            normalize_stock_code(str(record.get("code") or "")) != normalized_code
            for record in candidate_records
        ):
            return None
        origin_identity = extract_daily_data_identity_from_records(
            candidate_records, strict=True,
        )
        if (
            origin_identity is None
            or origin_identity.get("identity_state") != "OBSERVED"
            or normalize_stock_code(str(origin_identity.get("instrument") or ""))
            != normalized_code
            or origin_identity.get("returned_end") != target_date.isoformat()
            or (
                asset_admission["asset_route"] == "ETF"
                and origin_identity.get("currency")
                != asset_admission["product_profile"]["currency"]
            )
        ):
            return None
        origin_start = date.fromisoformat(str(origin_identity.get("returned_start") or ""))
        origin_end = date.fromisoformat(str(origin_identity.get("returned_end") or ""))
        if origin_start > start or origin_end != target_date:
            return None
        full_rows = (
            candidate_rows
            if origin_start == start and origin_end == target_date
            else db.get_data_range(code, origin_start, origin_end)
        )
        if not isinstance(full_rows, (list, tuple)) or not full_rows:
            return None
        full_records = [_daily_row_record(row) for row in full_rows]
        if any(
            normalize_stock_code(str(record.get("code") or "")) != normalized_code
            for record in full_records
        ):
            return None
        full_frame = pd.DataFrame(full_records)
        verify_observed_daily_frame_request(
            full_frame,
            instrument=normalized_code,
            provider_identity=origin_identity.get("provider_identity"),
            requested_start=origin_identity.get("requested_start"),
            requested_end=origin_identity.get("requested_end"),
            target_date=origin_end,
            observed_at_ceiling=_daily_observation_ceiling(current_time),
        )
        dates = pd.to_datetime(full_frame["date"], errors="coerce")
        if dates.isna().any():
            return None
        mask = dates.dt.date.between(start, target_date)
        selected = full_frame.loc[mask].copy()
        selected_dates = [value.date() for value in pd.to_datetime(selected["date"])]
        if selected_dates != expected:
            return None
        selected.drop(
            columns=["data_identity_json", "data_identity_hash"],
            errors="ignore",
            inplace=True,
        )
        view_identity = rebind_daily_data_identity(origin_identity, selected)
        attach_daily_data_identity(selected, view_identity)
        receipt = seal_daily_acquisition_receipt({
            "schema_version": DAILY_ACQUISITION_RECEIPT_SCHEMA_VERSION,
            "caller": "StockAnalysisPipeline.fetch_and_save_stock_data",
            "asset_route": asset_admission["asset_route"] if market == "cn" else "STOCK",
            "asset_admission": asset_admission,
            "market": str(market or "").strip().lower(),
            "instrument": normalized_code,
            "target_date": target_date.isoformat(),
            "acquisition_mode": "VERIFIED_CACHE_HIT",
            "request_evidence_status": "VERIFIED_LAWFUL_CACHE_HIT",
            "plan_hash": str(window_plan.get("plan_hash") or ""),
            "planned_start": start.isoformat(),
            "planned_end": target_date.isoformat(),
            "planned_session_count": len(expected),
            "actual_request_start": None,
            "actual_request_end": None,
            "requested_session_count": None,
            "cache_lookup_start": start.isoformat(),
            "cache_lookup_end": target_date.isoformat(),
            "returned_start": selected_dates[0].isoformat(),
            "returned_end": selected_dates[-1].isoformat(),
            "returned_base_bars": len(selected),
            "admitted_base_bars": len(selected),
            "origin_returned_base_bars": len(full_frame),
            "source_identity_hash": view_identity["identity_hash"],
            "source_origin_identity_hash": origin_identity["identity_hash"],
            "source_observed_at": origin_identity.get("observed_at"),
            "source_adjustment_basis": origin_identity.get("observed_adjustment_basis"),
            "provider_identity": origin_identity["provider_identity"],
            "provider_route": origin_identity["provider_route"],
            "provider_method_call_count": 0,
            "provider_http_request_count": None,
            "persistence_status": "VERIFIED_EXISTING_CACHE_NO_WRITE",
            "actual_consumer": "StockAnalysisPipeline.analyze_stock",
        })
        attach_daily_acquisition_receipt(selected, receipt)
        return selected
    except (
        DailyDataIdentityError, ValueError, TypeError, KeyError, AttributeError,
    ):
        logger.warning("Cannot admit typed daily cache for %s", code)
        return None
    except Exception:
        logger.warning("Cannot verify source-window cache coverage for %s", code)
        return None


def _cached_daily_history_covers_current_window(
    *,
    db: Any,
    code: str,
    market: Optional[str],
    target_date: date,
    window_plan: Mapping[str, Any],
) -> bool:
    """Compatibility bool over the strict content-bound cache loader."""
    return _load_verified_cached_daily_history(
        db=db,
        code=code,
        market=market,
        target_date=target_date,
        window_plan=window_plan,
    ) is not None


# Canonical 5m remains the single intraday source.  The fetch horizon is
# compiled from the currently admitted minute consumers, rather than treating
# ten sessions as a universal pattern/indicator window.  Dynamic price
# structure/pattern and recursive-stability admissions remain separate gates.
_INTRADAY_BARS_PER_SESSION = {
    "5m": 48,
    "15m": 16,
    "30m": 8,
    "60m": 4,
}


def _current_intraday_window_plan() -> Dict[str, Any]:
    """Compile the minimum current-minute acquisition horizon from owners.

    This is only the input/warm-up contract for methods already consumed by
    ``_build_intraday_timeframe``.  It is not the evaluation window for every
    future structure/pattern method and cannot admit those deferred methods.
    """

    method_required_bars = {
        "TREND": TREND_MIN_BARS,
        "MA_LEVEL_ALIGNMENT": MA_LEVEL_READY_BARS,
        "MA_SLOPE_CROSS": MA_SLOPE_CROSS_READY_BARS,
        "MA_COMPRESSION_RELEASE": MA_COMPRESSION_READY_BARS,
        "VOLUME": 5,
        "MACD_FORMULA_MINIMUM": (
            StockTrendAnalyzer.MACD_SLOW + StockTrendAnalyzer.MACD_SIGNAL
        ),
        "RSI_FORMULA_MINIMUM": StockTrendAnalyzer.RSI_LONG,
    }
    required_bars = max(method_required_bars.values())
    sessions_by_timeframe = {
        timeframe: (required_bars + bars_per_session - 1) // bars_per_session
        for timeframe, bars_per_session in _INTRADAY_BARS_PER_SESSION.items()
    }
    required_sessions = max(sessions_by_timeframe.values())
    limiting_timeframes = tuple(
        timeframe
        for timeframe, sessions in sessions_by_timeframe.items()
        if sessions == required_sessions
    )
    # A source-only diagnostic can still use the historical 10-session minimum.
    # Real evidence consumers prefer the independent recursive comparison target
    # only when the completed XSHG sessions are actually available.
    admission_requirements = {
        **method_required_bars,
        "MACD_STARTUP_COMPARISON": macd_startup_comparison_required_bars(),
        "RSI_STARTUP_COMPARISON": rsi_startup_comparison_required_bars(),
    }
    admission_target_required_bars = max(admission_requirements.values())
    admission_sessions_by_timeframe = {
        timeframe: (admission_target_required_bars + bars - 1) // bars
        for timeframe, bars in _INTRADAY_BARS_PER_SESSION.items()
    }
    admission_target_sessions = max(admission_sessions_by_timeframe.values())
    return {
        "required_bars": required_bars,
        "required_sessions": required_sessions,
        "method_required_bars": method_required_bars,
        "method_required_bars_by_timeframe": {
            timeframe: dict(admission_requirements)
            for timeframe in _INTRADAY_BARS_PER_SESSION
        },
        "admission_target_required_bars": admission_target_required_bars,
        "admission_target_sessions": admission_target_sessions,
        "admission_sessions_by_timeframe": admission_sessions_by_timeframe,
        "bars_per_session": dict(_INTRADAY_BARS_PER_SESSION),
        "sessions_by_timeframe": sessions_by_timeframe,
        "limiting_timeframes": limiting_timeframes,
        "deferred_window_classes": (
            "VARIABLE_STRUCTURE",
            "EVENT_ANCHORED",
            "RECURSIVE_SENSITIVITY",
        ),
    }


class P0BoundedTrialError(RuntimeError):
    """Fail-closed boundary error for the manual P0 product trial."""


class _B1NoPersistenceDatabase:
    """Fail-closed sentinel: B1 NO_SEND cannot touch durable storage."""

    def __getattr__(self, name: str) -> Any:
        raise DataObjectAdmissionError(
            "B1_NO_SEND_PERSISTENCE_FORBIDDEN:" + str(name or "unknown")
        )


class _B1NoModelAnalyzer:
    """Fail-closed sentinel used instead of constructing LiteLLM/Gemini."""

    p0_model_request_count = 0

    def analyze(self, *_args: Any, **_kwargs: Any) -> Any:
        raise DataObjectAdmissionError("B1_NO_SEND_MODEL_CALL_FORBIDDEN")


def _share_image_payload(result: Any) -> Optional[Dict[str, Any]]:
    """Return structured poster data when the result exposes the real contract."""

    to_dict = getattr(result, "to_dict", None)
    if not callable(to_dict):
        return None
    try:
        payload = to_dict()
    except Exception as exc:
        logger.debug("构建分享图片结构化数据失败，回退 Markdown: %s", exc)
        return None
    return payload if isinstance(payload, dict) and payload else None


def _supports_explicit_keyword(callable_obj: Any, keyword: str) -> bool:
    """Avoid breaking custom notifier overrides that predate an optional kwarg."""

    try:
        return keyword in inspect.signature(callable_obj).parameters
    except (TypeError, ValueError):
        return False


# 防御性 guard：当实例绕过 __init__（如测试中 __new__）构造时，
# double-check 初始化 _single_stock_notify_lock 仍然线程安全。
_SINGLE_STOCK_NOTIFY_LOCK_INIT_GUARD = threading.Lock()
_DAILY_MARKET_CONTEXT_SERVICE_LOCK_INIT_GUARD = threading.Lock()
_RUN_LOCAL_DAILY_HISTORY_LOCK_INIT_GUARD = threading.Lock()
_RUN_LOCAL_INTRADAY_TIMEFRAMES_LOCK_INIT_GUARD = threading.Lock()


def _symbol_scope_lookup_values(code: str, market: str) -> List[str]:
    """Return accepted persisted-intelligence symbol spellings for lookup."""
    raw = str(code or "").strip()
    normalized = normalize_stock_code(raw) if raw else ""
    values: List[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            values.append(text)

    def add_case_variants(value: str) -> None:
        text = str(value or "").strip()
        if not text:
            return
        add(text)
        add(text.upper())
        add(text.lower())

    add_case_variants(normalized)
    add_case_variants(raw)

    normalized_upper = normalized.upper()
    if normalized_upper.startswith("HK") and normalized_upper[2:].isdigit():
        digits = normalized_upper[2:]
        trimmed_digits = digits.lstrip("0") or digits
        add_case_variants(normalized_upper)
        add_case_variants(digits)
        add_case_variants(trimmed_digits)
        add_case_variants(f"HK{trimmed_digits}")
        add_case_variants(f"{trimmed_digits}.HK")
        add_case_variants(f"{digits}.HK")
        return values

    if (market or "").strip().lower() != "cn":
        return values
    if not (normalized.isdigit() and len(normalized) == 6):
        return values

    raw_upper = raw.upper()
    exchange = ""
    if raw_upper.startswith(("SH", "SS")) or raw_upper.endswith((".SH", ".SS")):
        exchange = "SH"
    elif raw_upper.startswith("SZ") or raw_upper.endswith(".SZ"):
        exchange = "SZ"
    elif raw_upper.startswith("BJ") or raw_upper.endswith(".BJ"):
        exchange = "BJ"
    elif is_bse_code(normalized):
        exchange = "BJ"
    elif normalized.startswith(("5", "6", "9")):
        exchange = "SH"
    else:
        exchange = "SZ"

    add_case_variants(f"{exchange}{normalized}")
    add_case_variants(f"{exchange}.{normalized}")
    add_case_variants(f"{normalized}.{exchange}")
    if exchange == "SH":
        add_case_variants(f"SS.{normalized}")
        add_case_variants(f"{normalized}.SS")
    return values


class StockAnalysisPipeline:
    """
    股票分析主流程调度器
    
    职责：
    1. 管理整个分析流程
    2. 协调数据获取、存储、搜索、分析、通知等模块
    3. 实现并发控制和异常处理
    """

    p0_bounded_trial = False
    p0_stock_codes: Tuple[str, ...] = ()
    p0_suppress_notification = False
    p0_receipt_only = False
    p0_acceptance_context: Optional[Dict[str, Any]] = None
    
    def __init__(
        self,
        config: Optional[Config] = None,
        max_workers: Optional[int] = None,
        source_message: Optional[BotMessage] = None,
        query_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        query_source: Optional[str] = None,
        save_context_snapshot: Optional[bool] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None,
        analysis_skills: Optional[List[str]] = None,
        analysis_phase: str = "auto",
        portfolio_context: Optional[Dict[str, Any]] = None,
        daily_market_context_enabled: Optional[bool] = None,
        daily_market_context_allow_generate: bool = True,
        p0_bounded_trial: bool = False,
        p0_stock_codes: Optional[List[str]] = None,
        p0_suppress_notification: bool = False,
        p0_receipt_only: bool = False,
        p0_acceptance_context: Optional[Dict[str, Any]] = None,
        p0_model_request_budget: Optional[int] = None,
        research_selection_context: Optional[Dict[str, Any]] = None,
        research_code_sha: Optional[str] = None,
        b1_rights_context: Optional[Dict[str, Any]] = None,
    ):
        """
        初始化调度器
        
        Args:
            config: 配置对象（可选，默认使用全局配置）
            max_workers: 最大并发线程数（可选，默认从配置读取）
        """
        self.config = config or get_config()
        self.p0_bounded_trial = bool(p0_bounded_trial)
        self.p0_stock_codes = list(p0_stock_codes or [])
        self.p0_suppress_notification = bool(p0_suppress_notification)
        self.p0_receipt_only = bool(p0_receipt_only)
        self.p0_acceptance_context = (
            dict(p0_acceptance_context) if isinstance(p0_acceptance_context, dict) else {}
        )
        self.p0_model_request_budget = p0_model_request_budget
        self.research_selection_context = normalize_research_selection_context(
            research_selection_context
        )
        self.research_code_sha = str(research_code_sha or "").strip() or None
        # Explicit external source grant for bounded B1 NO_SEND; absent by default.
        # Never infer a provider license from code availability or the user query.
        self.b1_rights_context = (
            dict(b1_rights_context)
            if isinstance(b1_rights_context, Mapping)
            else load_b1_rights_context(
                getattr(self.config, "b1_rights_evidence_path", None)
            )
        )
        b1_no_send_mode = (
            isinstance(self.b1_rights_context, Mapping)
            and not self.p0_bounded_trial
        )
        if self.p0_acceptance_context and not (
            self.p0_bounded_trial and self.p0_suppress_notification
        ):
            raise P0BoundedTrialError(
                "AUTO_SCREEN acceptance context requires bounded notification-suppressed mode"
            )
        if self.p0_suppress_notification and not self.p0_bounded_trial:
            raise P0BoundedTrialError("P0 notification suppression requires bounded mode")
        if self.p0_receipt_only and not (
            self.p0_bounded_trial and self.p0_suppress_notification
        ):
            raise P0BoundedTrialError(
                "P0 receipt-only mode requires bounded notification-suppressed mode"
            )
        if self.p0_bounded_trial and not 1 <= len(self.p0_stock_codes) <= 2:
            raise P0BoundedTrialError("P0 requires exactly one or two target stocks")
        self.max_workers = 1 if (self.p0_bounded_trial or b1_no_send_mode) else (max_workers or self.config.max_workers)
        self.source_message = source_message
        self.query_id = query_id
        self.trace_id = trace_id or query_id
        self.query_source = self._resolve_query_source(query_source)
        if (
            self.b1_rights_context is not None
            and self.query_source in {"bot", "web", "api"}
        ):
            raise DataObjectAdmissionError(
                "B1_NO_SEND_INTERACTIVE_ROUTE_FORBIDDEN"
            )
        self.save_context_snapshot = (
            self.config.save_context_snapshot if save_context_snapshot is None else save_context_snapshot
        )
        self.progress_callback = progress_callback
        self.analysis_skills = list(analysis_skills) if analysis_skills is not None else None
        self.analysis_phase = analysis_phase or "auto"
        self.portfolio_context = dict(portfolio_context) if isinstance(portfolio_context, dict) else None
        self.daily_market_context_enabled = (
            bool(getattr(self.config, "daily_market_context_enabled", True))
            if daily_market_context_enabled is None
            else bool(daily_market_context_enabled)
        )
        self.daily_market_context_allow_generate = daily_market_context_allow_generate
        
        # 初始化各模块。B1 NO_SEND在构造阶段就隔离业务DB、模型与
        # 其他未经准入的数据服务；原Provider/技术Owner/报告Consumer继续复用。
        self.db = _B1NoPersistenceDatabase() if b1_no_send_mode else get_db()
        self.fetcher_manager = DataFetcherManager()
        # 不再单独创建 akshare_fetcher，统一使用 fetcher_manager 获取增强数据
        self.trend_analyzer = StockTrendAnalyzer()  # 技术分析器
        if b1_no_send_mode:
            self.analyzer = _B1NoModelAnalyzer()
        else:
            analyzer_kwargs: Dict[str, Any] = {
                "config": self.config,
                "skills": self.analysis_skills,
                "p0_bounded_trial": self.p0_bounded_trial,
            }
            if self.p0_model_request_budget is not None:
                analyzer_kwargs["p0_model_request_budget"] = self.p0_model_request_budget
            self.analyzer = GeminiAnalyzer(**analyzer_kwargs)
        self.notifier = NotificationService(source_message=source_message)
        self.market_structure_service = (
            None if b1_no_send_mode
            else MarketStructureService(fetcher_manager=self.fetcher_manager)
        )
        self.relative_strength_service = (
            None if b1_no_send_mode
            else RelativeStrengthService(fetcher_manager=self.fetcher_manager)
        )
        self.market_hotspot_service: Optional[MarketHotspotService] = None
        if not self.p0_bounded_trial and not b1_no_send_mode:
            try:
                self.market_hotspot_service = MarketHotspotService(
                    fetcher_manager=self.fetcher_manager,
                )
            except Exception as exc:
                logger.debug("market hotspot service init failed (fail-open): %s", exc)
        self._single_stock_notify_lock = threading.Lock()
        self._daily_market_context_service_lock = threading.Lock()
        self._run_local_daily_history_lock = threading.Lock()
        self._run_local_daily_history: Dict[Tuple[str, str], pd.DataFrame] = {}
        self._run_local_intraday_timeframes_lock = threading.Lock()
        self._run_local_intraday_timeframes: Dict[
            Tuple[str, str], Dict[str, pd.DataFrame]
        ] = {}
        self._concept_rankings_cache_lock = threading.Lock()
        self._concept_rankings_cache: Dict[str, Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]] = {}
        
        # 初始化搜索服务（可选，初始化失败不应阻断主分析流程）
        self.search_service = None
        if not self.p0_bounded_trial and not b1_no_send_mode:
            try:
                self.search_service = SearchService(
                    bocha_keys=self.config.bocha_api_keys,
                    tavily_keys=self.config.tavily_api_keys,
                    anspire_keys=self.config.anspire_api_keys,
                    brave_keys=self.config.brave_api_keys,
                    serpapi_keys=self.config.serpapi_keys,
                    minimax_keys=self.config.minimax_api_keys,
                    searxng_base_urls=self.config.searxng_base_urls,
                    searxng_public_instances_enabled=self.config.searxng_public_instances_enabled,
                    news_max_age_days=self.config.news_max_age_days,
                    news_strategy_profile=getattr(self.config, "news_strategy_profile", "short"),
                )
            except Exception as exc:
                logger.warning("搜索服务初始化失败，将以无搜索模式运行: %s", exc, exc_info=True)
        
        logger.info(f"调度器初始化完成，最大并发数: {self.max_workers}")
        logger.info("已启用技术分析引擎（均线/趋势/量价指标）")
        # 打印实时行情/筹码配置状态
        if self.config.enable_realtime_quote:
            logger.info(f"实时行情已启用 (优先级: {self.config.realtime_source_priority})")
        else:
            logger.info("实时行情已禁用，将使用历史收盘价")
        if self.config.enable_chip_distribution:
            logger.info("筹码分布分析已启用")
        else:
            logger.info("筹码分布分析已禁用")
        if self.search_service is None:
            logger.warning("搜索服务未启用（初始化失败或依赖缺失）")
        elif self.search_service.is_available:
            logger.info("搜索服务已启用")
        else:
            logger.warning("搜索服务未启用（未配置搜索能力）")

        if b1_no_send_mode:
            logger.info(
                "B1 NO_SEND构造隔离已启用：无业务DB、模型、搜索、热点、相对强弱或市场结构服务初始化"
            )

        # 初始化社交舆情服务（仅美股，可选）
        self.social_sentiment_service = None
        if not self.p0_bounded_trial and not b1_no_send_mode:
            try:
                self.social_sentiment_service = SocialSentimentService(
                    api_key=self.config.social_sentiment_api_key,
                    api_url=self.config.social_sentiment_api_url,
                )
                if self.social_sentiment_service.is_available:
                    logger.info("Social sentiment service enabled (Reddit/X/Polymarket, US stocks only)")
            except Exception as exc:
                logger.warning(
                    "社交舆情服务初始化失败，将跳过舆情分析: %s",
                    exc,
                    exc_info=True,
                )

    def _emit_progress(self, progress: int, message: str) -> None:
        """Best-effort bridge from pipeline stages to task SSE progress."""
        callback = getattr(self, "progress_callback", None)
        if callback is None:
            return
        try:
            callback(progress, message)
        except Exception as exc:
            query_id = getattr(self, "query_id", None)
            logger.warning(
                "[pipeline] progress callback failed: %s (progress=%s, message=%r, query_id=%s)",
                exc,
                progress,
                message,
                query_id,
                extra={
                    "progress": progress,
                    "progress_message": message,
                    "query_id": query_id,
                },
            )

    def _ensure_run_local_daily_history_state(self) -> None:
        if hasattr(self, "_run_local_daily_history_lock") and hasattr(
            self, "_run_local_daily_history"
        ):
            return
        with _RUN_LOCAL_DAILY_HISTORY_LOCK_INIT_GUARD:
            if not hasattr(self, "_run_local_daily_history_lock"):
                self._run_local_daily_history_lock = threading.Lock()
            if not hasattr(self, "_run_local_daily_history"):
                self._run_local_daily_history = {}

    @staticmethod
    def _run_local_daily_history_key(code: str, target_date: date) -> Tuple[str, str]:
        return normalize_stock_code(str(code or "")), target_date.isoformat()

    def _remember_run_local_daily_history(
        self, *, code: str, target_date: date, frame: pd.DataFrame,
    ) -> None:
        self._ensure_run_local_daily_history_state()
        copied = frame.copy(deep=True)
        copied.attrs = deepcopy(frame.attrs)
        key = self._run_local_daily_history_key(code, target_date)
        with self._run_local_daily_history_lock:
            self._run_local_daily_history[key] = copied

    def _get_run_local_daily_history(
        self, *, code: str, target_date: date,
    ) -> Optional[pd.DataFrame]:
        self._ensure_run_local_daily_history_state()
        key = self._run_local_daily_history_key(code, target_date)
        with self._run_local_daily_history_lock:
            frame = self._run_local_daily_history.get(key)
            if frame is None:
                return None
            copied = frame.copy(deep=True)
            copied.attrs = deepcopy(frame.attrs)
            return copied

    def _clear_run_local_daily_history(self, *, code: str, target_date: date) -> None:
        self._ensure_run_local_daily_history_state()
        key = self._run_local_daily_history_key(code, target_date)
        with self._run_local_daily_history_lock:
            self._run_local_daily_history.pop(key, None)

    def _ensure_run_local_intraday_timeframes_state(self) -> None:
        if hasattr(self, "_run_local_intraday_timeframes_lock") and hasattr(
            self, "_run_local_intraday_timeframes"
        ):
            return
        with _RUN_LOCAL_INTRADAY_TIMEFRAMES_LOCK_INIT_GUARD:
            if not hasattr(self, "_run_local_intraday_timeframes_lock"):
                self._run_local_intraday_timeframes_lock = threading.Lock()
            if not hasattr(self, "_run_local_intraday_timeframes"):
                self._run_local_intraday_timeframes = {}

    def _remember_run_local_intraday_timeframes(
        self, *, code: str, target_date: date,
        timeframes: Mapping[str, pd.DataFrame],
    ) -> None:
        self._ensure_run_local_intraday_timeframes_state()
        copied: Dict[str, pd.DataFrame] = {}
        for key, frame in timeframes.items():
            if not isinstance(frame, pd.DataFrame):
                continue
            value = frame.copy(deep=True)
            value.attrs = deepcopy(frame.attrs)
            copied[str(key)] = value
        cache_key = self._run_local_daily_history_key(code, target_date)
        with self._run_local_intraday_timeframes_lock:
            self._run_local_intraday_timeframes[cache_key] = copied

    def _get_run_local_intraday_timeframes(
        self, *, code: str, target_date: date,
    ) -> Optional[Dict[str, pd.DataFrame]]:
        self._ensure_run_local_intraday_timeframes_state()
        cache_key = self._run_local_daily_history_key(code, target_date)
        with self._run_local_intraday_timeframes_lock:
            cached = self._run_local_intraday_timeframes.get(cache_key)
            if cached is None:
                return None
            copied: Dict[str, pd.DataFrame] = {}
            for key, frame in cached.items():
                value = frame.copy(deep=True)
                value.attrs = deepcopy(frame.attrs)
                copied[key] = value
            return copied

    def _extend_daily_history_for_monthly_macd(
        self,
        *,
        frame: pd.DataFrame,
        source_name: str,
        identity: Mapping[str, Any],
        code: str,
        market: str,
        target_date: date,
        current_time: Optional[datetime],
        observation_time_frozen: bool,
        base_plan: Mapping[str, Any],
        requested_sessions: List[date],
        returned_dates: List[date],
    ) -> Tuple[
        pd.DataFrame, Mapping[str, Any], Dict[str, Any], List[date], List[date],
        Optional[Dict[str, Any]],
    ]:
        """Boundedly extend the same daily source only after native monthly MACD drift."""
        analyzer = getattr(self, "trend_analyzer", None)
        if analyzer is None or market != "cn" or bool(getattr(self, "p0_receipt_only", False)):
            return frame, identity, dict(base_plan), requested_sessions, returned_dates, None
        if (
            not observation_time_frozen
            and resolve_latest_completed_session_fail_closed(market) != target_date
        ):
            return frame, identity, dict(base_plan), requested_sessions, returned_dates, None
        current_frame = frame
        current_identity = dict(identity)
        current_plan = dict(base_plan)
        current_requested = list(requested_sessions)
        current_returned = list(returned_dates)
        initial_identity = dict(identity)
        attempts: List[Dict[str, Any]] = []
        final_status = "NO_EXTENSION"
        final_reason = "NATIVE_MACD_NOT_EVALUATED"
        while True:
            receipt, completed_months = _monthly_macd_receipt_from_daily_history(
                frame=current_frame,
                code=code,
                market=market,
                target_date=target_date,
                trend_analyzer=analyzer,
                source_observed_at=current_identity.get("observed_at"),
            )
            requested_months = int(current_plan["timeframe_required_bars"]["monthly"])
            attempts.append({
                "attempt_number": len(attempts) + 1,
                "requested_monthly_bars": requested_months,
                "request_start": current_plan["start_date"].isoformat(),
                "request_end": target_date.isoformat(),
                "returned_start": current_returned[0].isoformat(),
                "returned_end": current_returned[-1].isoformat(),
                "returned_base_bars": len(current_frame),
                "completed_monthly_bars": completed_months,
                "source_identity_hash": current_identity["identity_hash"],
                "provider_identity": current_identity["provider_identity"],
                "provider_route": current_identity["provider_route"],
                "adjustment_basis": current_identity.get("observed_adjustment_basis"),
                "currency": current_identity.get("currency"),
                "volume_unit": current_identity.get("volume_unit"),
                "amount_unit": current_identity.get("amount_unit"),
                "macd_status": receipt.get("status"),
                "macd_reason": receipt.get("reason"),
                "macd_observations": receipt.get("observations"),
                "macd_required_observations": receipt.get("required_observations"),
                "max_endpoint_difference": receipt.get("max_endpoint_difference"),
                "absolute_tolerance": receipt.get("absolute_tolerance"),
                "startup_state_agreement": receipt.get("startup_state_agreement"),
                "numeric_agreement": receipt.get("numeric_agreement"),
            })
            if completed_months < requested_months:
                final_status = "SOURCE_HISTORY_SHORT"
                final_reason = "SOURCE_RETURNED_FEWER_COMPLETED_MONTHS_THAN_REQUESTED"
                break
            advice = _next_bounded_monthly_macd_window(
                base_plan=base_plan,
                target_date=target_date,
                receipt=receipt,
                attempted_monthly_bars=tuple(
                    item["requested_monthly_bars"] for item in attempts
                ),
            )
            if advice.get("status") != "READ_ONLY_EXTENSION_AVAILABLE":
                final_status = "READY" if receipt.get("status") == "READY" else "NO_EXTENSION"
                final_reason = str(advice.get("reason") or receipt.get("reason") or "NO_EXTENSION")
                break
            next_plan = _daily_plan_with_monthly_macd_bars(
                base_plan=base_plan,
                target_date=target_date,
                monthly_bars=int(advice["proposed_monthly_bars"]),
            )
            source_fetcher = self.fetcher_manager._get_fetcher_by_name(
                source_name, capability="daily_data",
            )
            if source_fetcher is None:
                raise DailyDataIdentityError(
                    "DAILY_MONTHLY_EXTENSION_SELECTED_PROVIDER_UNAVAILABLE"
                )
            next_frame = self.fetcher_manager._call_fetcher_method(
                source_fetcher,
                "get_daily_data",
                code,
                start_date=next_plan["start_date"].isoformat(),
                end_date=target_date.isoformat(),
            )
            next_identity = verify_observed_daily_frame_request(
                next_frame,
                instrument=code,
                provider_identity=source_name,
                requested_start=next_plan["start_date"],
                requested_end=target_date,
                target_date=target_date,
                observed_at_ceiling=_provider_observation_ceiling(
                    current_time,
                    market=market,
                    target_date=target_date,
                    observation_time_frozen=observation_time_frozen,
                ),
            )
            for key in (
                "provider_identity", "provider_route", "instrument",
                "requested_adjustment_basis", "observed_adjustment_basis", "currency",
                "volume_unit", "amount_unit",
            ):
                if next_identity.get(key) != initial_identity.get(key):
                    raise DailyDataIdentityError(
                        "DAILY_MONTHLY_EXTENSION_SOURCE_IDENTITY_MISMATCH:" + key
                    )
            next_sessions = resolve_session_range_fail_closed(
                market, next_plan["start_date"], target_date,
            )
            if not next_sessions:
                raise DailyDataIdentityError("daily extension exchange-session range unavailable")
            next_dates = [
                value.date() for value in pd.to_datetime(next_frame["date"], errors="raise")
            ]
            if (
                len(next_dates) > len(next_sessions)
                or next_dates != next_sessions[-len(next_dates):]
            ):
                raise DailyDataIdentityError(
                    "daily extension response is not an exact completed-session suffix"
                )
            current_frame = next_frame
            current_identity = next_identity
            current_plan = next_plan
            current_requested = next_sessions
            current_returned = next_dates
        if len(attempts) < 2:
            return (
                current_frame, current_identity, current_plan,
                current_requested, current_returned, None,
            )
        extension = seal_daily_window_extension_receipt({
            "schema_version": DAILY_WINDOW_EXTENSION_RECEIPT_SCHEMA_VERSION,
            "caller": "StockAnalysisPipeline.fetch_and_save_stock_data",
            "instrument": normalize_stock_code(code),
            "market": market,
            "target_date": target_date.isoformat(),
            "method_id": "MTF_MOMENTUM_CONTEXT.MONTHLY.MACD_STARTUP_COMPARISON",
            "method_owner": "StockTrendAnalyzer._calculate_macd",
            "provider_identity": initial_identity["provider_identity"],
            "provider_route": initial_identity["provider_route"],
            "initial_plan_hash": base_plan["plan_hash"],
            "final_plan_hash": current_plan["plan_hash"],
            "initial_source_identity_hash": initial_identity["identity_hash"],
            "final_source_identity_hash": current_identity["identity_hash"],
            "initial_request_start": base_plan["start_date"].isoformat(),
            "final_request_start": current_plan["start_date"].isoformat(),
            "final_requested_monthly_bars": int(
                current_plan["timeframe_required_bars"]["monthly"]
            ),
            "provider_method_call_count": len(attempts),
            "attempts": attempts,
            "final_status": final_status,
            "final_reason": final_reason,
            "storage_admitted": False,
            "strategy_admitted": False,
            "training_admitted": False,
            "independent_action_authority": False,
        })
        return (
            current_frame, current_identity, current_plan,
            current_requested, current_returned, extension,
        )

    def _bind_b1_run_context(
        self, current_time: Optional[datetime] = None,
        *,
        persist: bool = True,
        force_as_of: bool = False,
    ) -> Optional[Dict[str, Any]]:
        context = getattr(self, "b1_rights_context", None)
        if not isinstance(context, Mapping):
            return None
        required_fields = (
            "schema_version", "operation_id", "intended_use",
            "allowed_instruments", "rights_by_dataset",
        )
        allowed_fields = {
            *required_fields,
            "rights_context_sha256",
            "research_as_of",
            "source_metadata_by_dataset",
        }
        if set(context) - allowed_fields:
            raise DataObjectAdmissionError(
                "B1_RIGHTS_CONTEXT_SCHEMA_MISMATCH"
            )
        base = validate_b1_rights_context({
            key: context[key] for key in required_fields if key in context
        })
        supplied_hash = str(context.get("rights_context_sha256") or "").strip()
        if supplied_hash and supplied_hash != base["rights_context_sha256"]:
            raise DataObjectAdmissionError(
                "B1_RIGHTS_CONTEXT_HASH_MISMATCH"
            )
        raw_as_of = context.get("research_as_of")
        if raw_as_of and not force_as_of:
            if isinstance(raw_as_of, datetime):
                instant = raw_as_of
            elif isinstance(raw_as_of, str):
                try:
                    instant = datetime.fromisoformat(
                        raw_as_of.strip().replace("Z", "+00:00")
                    )
                except ValueError as exc:
                    raise DataObjectAdmissionError(
                        "B1_RESEARCH_ASOF_INVALID"
                    ) from exc
            else:
                raise DataObjectAdmissionError("B1_RESEARCH_ASOF_INVALID")
        else:
            instant = current_time or datetime.now(timezone.utc)
        bound = bind_b1_rights_context_as_of(base, instant)
        duplicate = context.get("source_metadata_by_dataset")
        if isinstance(duplicate, Mapping):
            bound["source_metadata_by_dataset"] = dict(duplicate)
        if persist:
            self.b1_rights_context = bound
        return bound

    @staticmethod
    def _b1_target_contract(
        code: str,
    ) -> Tuple[str, Tuple[Tuple[str, str], ...]]:
        asset = resolve_cn_price_asset(code)
        if asset["market"] != "cn" or asset["status"] != "ADMITTED":
            raise DataObjectAdmissionError("B1_TARGET_ASSET_NOT_ADMITTED")
        instrument = str(asset["instrument"] or "").strip().upper()
        datasets = (
            (
                ("E_PRICE_1D", "ETF_ASSET_BRIEF"),
                ("E_PRICE_5M", "ETF_ASSET_BRIEF"),
            )
            if asset["asset_route"] == "ETF"
            else (
                ("P_DAILY", "ASSET_RESEARCH_BRIEF"),
                ("P_5M", "ASSET_RESEARCH_BRIEF"),
            )
        )
        return instrument, datasets

    def _validate_b1_targets(
        self,
        codes: List[str],
        *,
        current_time: datetime,
        persist_context: bool = True,
    ) -> None:
        context = self._bind_b1_run_context(
            current_time, persist=persist_context,
        )
        if not isinstance(context, Mapping) or not codes:
            raise DataObjectAdmissionError("B1_SPECIFIED_TARGETS_REQUIRED")
        allowed = {
            str(item or "").strip().upper()
            for item in context.get("allowed_instruments") or ()
        }
        for code in codes:
            instrument, datasets = self._b1_target_contract(code)
            if instrument not in allowed:
                raise DataObjectAdmissionError(
                    "B1_TARGET_INSTRUMENT_NOT_ADMITTED:" + instrument
                )
            for dataset_id, consumer_scope in datasets:
                validate_b1_provider_preflight(
                    (context.get("rights_by_dataset") or {}).get(dataset_id),
                    operation_id=context.get("operation_id"),
                    intended_use=context.get("intended_use"),
                    dataset_id=dataset_id,
                    consumer_scope=consumer_scope,
                    research_as_of=context.get("research_as_of"),
                )

    @staticmethod
    def _b1_aware_clock(value: Any, *, field: str) -> datetime:
        try:
            instant = datetime.fromisoformat(
                str(value or "").strip().replace("Z", "+00:00")
            )
        except (TypeError, ValueError) as exc:
            raise DataObjectAdmissionError(
                "B1_PREACQUIRED_SOURCE_CLOCK_INVALID:" + field
            ) from exc
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise DataObjectAdmissionError(
                "B1_PREACQUIRED_SOURCE_CLOCK_NAIVE:" + field
            )
        return instant.astimezone(timezone.utc)

    def _preacquire_b1_sources(
        self,
        codes: List[str],
        *,
        target_reference_time: datetime,
    ) -> datetime:
        """Acquire all admitted 1d/5m sources, then freeze one decision as-of.

        The target-session clock is fixed before network work.  The research
        decision clock is fixed only after every response is observed, so no
        provider response can be backdated into known-at evidence.
        """
        self._validate_b1_targets(
            codes,
            current_time=target_reference_time,
            persist_context=False,
        )
        targets: Dict[str, date] = {}
        self._b1_preacquisition_active = True
        try:
            for requested_code in codes:
                instrument, _datasets = self._b1_target_contract(requested_code)
                target_date = self._resolve_resume_target_date(
                    instrument, current_time=target_reference_time,
                )
                targets[instrument] = target_date
                success, error = self.fetch_and_save_stock_data(
                    instrument,
                    current_time=target_reference_time,
                    observation_time_frozen=False,
                )
                if not success:
                    raise DataObjectAdmissionError(
                        "B1_PREACQUIRE_DAILY_FAILED:"
                        + instrument + ":" + str(error or "UNKNOWN")
                    )
                daily = self._get_run_local_daily_history(
                    code=instrument, target_date=target_date,
                )
                if not isinstance(daily, pd.DataFrame) or daily.empty:
                    raise DataObjectAdmissionError(
                        "B1_PREACQUIRE_DAILY_RUN_LOCAL_MISSING:" + instrument
                    )
                timeframes = self._load_current_stock_intraday_timeframes(
                    code=instrument,
                    stock_name=instrument,
                    market=get_market_for_stock(
                        normalize_stock_code(instrument)
                    ),
                    target_date=target_date,
                    completed_daily_history=daily,
                    current_time=None,
                )
                if not isinstance(timeframes.get("5m"), pd.DataFrame):
                    raise DataObjectAdmissionError(
                        "B1_PREACQUIRE_5M_FAILED:" + instrument
                    )

            decision_time = datetime.now(timezone.utc)
            for instrument, target_date in targets.items():
                if self._resolve_resume_target_date(
                    instrument, current_time=decision_time,
                ) != target_date:
                    raise DataObjectAdmissionError(
                        "B1_TARGET_SESSION_CHANGED_DURING_ACQUISITION:"
                        + instrument
                    )
                daily = self._get_run_local_daily_history(
                    code=instrument, target_date=target_date,
                )
                intraday = self._get_run_local_intraday_timeframes(
                    code=instrument, target_date=target_date,
                )
                if daily is None or intraday is None or "5m" not in intraday:
                    raise DataObjectAdmissionError(
                        "B1_PREACQUIRED_SOURCE_CACHE_INCOMPLETE:" + instrument
                    )
                daily_identity = extract_daily_data_identity(daily, strict=True)
                from data_provider.intraday_data_identity import (
                    extract_intraday_data_identity,
                )
                intraday_identity = extract_intraday_data_identity(
                    intraday["5m"], strict=True,
                )
                for field, value in (
                    ("daily.observed_at", daily_identity.get("observed_at")),
                    ("5m.observed_at", intraday_identity.get("observed_at")),
                    ("5m.available_at_max", intraday_identity.get("available_at_max")),
                ):
                    if self._b1_aware_clock(value, field=field) > decision_time:
                        raise DataObjectAdmissionError(
                            "B1_PREACQUIRED_SOURCE_AFTER_DECISION:" + field
                        )

            self._bind_b1_run_context(
                decision_time,
                persist=True,
                force_as_of=True,
            )
            self._b1_target_reference_time = target_reference_time
            self._b1_decision_time = decision_time
            return decision_time
        finally:
            self._b1_preacquisition_active = False

    def _assert_b1_results_product_admitted(
        self,
        results: List[AnalysisResult],
        codes: List[str],
    ) -> None:
        by_code = {
            normalize_stock_code(str(getattr(result, "code", "") or "")): result
            for result in results
        }
        for code in codes:
            instrument, datasets = self._b1_target_contract(code)
            result = by_code.get(normalize_stock_code(instrument))
            dashboard = getattr(result, "dashboard", None)
            factor = (
                dashboard.get("factor_decision")
                if isinstance(dashboard, dict) else None
            )
            trace = (
                factor.get("evidence_traceability")
                if isinstance(factor, dict) else None
            )
            universe = (
                trace.get("complete_research_universe")
                if isinstance(trace, dict) else None
            )
            admission = (
                universe.get("data_object_admission")
                if isinstance(universe, dict) else None
            )
            rows = {
                str(row.get("dataset_id") or ""): row
                for row in (
                    admission.get("objects")
                    if isinstance(admission, dict) else []
                )
                if isinstance(row, dict)
            }
            missing = [
                dataset_id
                for dataset_id, _consumer_scope in datasets
                if rows.get(dataset_id, {}).get("product_admitted") is not True
            ]
            if missing:
                raise DataObjectAdmissionError(
                    "B1_REQUIRED_PRICE_DATASETS_NOT_PRODUCT_ADMITTED:"
                    + instrument + ":" + ",".join(missing)
                )
            if any(
                row.get("learning_admitted") is True
                for row in rows.values()
            ):
                raise DataObjectAdmissionError(
                    "B1_NO_SEND_LEARNING_ADMISSION_FORBIDDEN"
                )

    def fetch_and_save_stock_data(
        self,
        code: str,
        force_refresh: bool = False,
        current_time: Optional[datetime] = None,
        observation_time_frozen: Optional[bool] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Acquire one daily window with request/cache/source identity separated.

        A verified read-only provider response remains available to this run when
        the independent B1 storage gate rejects persistence. Invalid or untyped
        caches may never suppress a provider call or feed strict current evidence.
        """
        stock_name = code
        target_date: Optional[date] = None
        if observation_time_frozen is None:
            observation_time_frozen = current_time is not None
        try:
            asset_admission = resolve_cn_price_asset(code)
            if asset_admission["market"] == "cn":
                if asset_admission["status"] != "ADMITTED":
                    return False, "PRICE_ASSET_NOT_ADMITTED: " + asset_admission["reason"]
                code = asset_admission["instrument"]
            # B1 is an explicit operation-specific NO_SEND route.  Do not
            # reinterpret every legacy/current CN source-identity consumer as
            # carrying an external rights grant; its original strict source
            # and method-window checks remain below regardless of B1 mode.
            b1_active = (
                asset_admission["market"] == "cn"
                and isinstance(getattr(self, "b1_rights_context", None), Mapping)
            )
            b1_daily_grant = None
            if b1_active:
                b1 = self._bind_b1_run_context(
                    current_time,
                    persist=not bool(getattr(self, "_b1_preacquisition_active", False)),
                )
                dataset = "E_PRICE_1D" if asset_admission["asset_route"] == "ETF" else "P_DAILY"
                consumer = "ETF_ASSET_BRIEF" if asset_admission["asset_route"] == "ETF" else "ASSET_RESEARCH_BRIEF"
                try:
                    if not isinstance(b1, Mapping):
                        raise DataObjectAdmissionError("B1_OPERATION_CONTEXT_MISSING")
                    allowed = {
                        str(item or "").strip().upper()
                        for item in b1.get("allowed_instruments") or ()
                    }
                    if str(code or "").strip().upper() not in allowed:
                        raise DataObjectAdmissionError(
                            "B1_TARGET_INSTRUMENT_NOT_ADMITTED"
                        )
                    b1_daily_grant = validate_b1_provider_preflight(
                        (b1.get("rights_by_dataset") or {}).get(dataset),
                        operation_id=b1.get("operation_id"),
                        intended_use=b1.get("intended_use"),
                        dataset_id=dataset, consumer_scope=consumer,
                        research_as_of=b1.get("research_as_of"),
                    )
                except (DataObjectAdmissionError, AttributeError, TypeError) as exc:
                    return False, str(exc) if str(exc).startswith("B1_") else "B1_OPERATION_RIGHTS_INVALID"
                # Stock name lookups can themselves call other providers.
                # In strict research, the user-facing code is used until a
                # name is obtained from an independently admitted source.
                stock_name = code
            else:
                stock_name = self.fetcher_manager.get_stock_name(
                    code, allow_realtime=False,
                )
            target_date = self._resolve_resume_target_date(
                code, current_time=current_time,
            )
            if b1_active and not force_refresh:
                cached_run_local = self._get_run_local_daily_history(
                    code=code, target_date=target_date,
                )
                if cached_run_local is not None:
                    cached_identity = extract_daily_data_identity(
                        cached_run_local, strict=True,
                    )
                    cached_receipt = extract_daily_acquisition_receipt(
                        cached_run_local, strict=True,
                    )
                    if (
                        cached_identity.get("provider_identity")
                        != b1_daily_grant["provider_identity"]
                        or cached_receipt.get("instrument")
                        != normalize_stock_code(code)
                        or cached_receipt.get("target_date")
                        != target_date.isoformat()
                    ):
                        raise DailyDataIdentityError(
                            "B1_RUN_LOCAL_DAILY_CACHE_IDENTITY_MISMATCH"
                        )
                    return True, None
            self._clear_run_local_daily_history(
                code=code, target_date=target_date,
            )
            market = get_market_for_stock(normalize_stock_code(code))
            strict_current = (
                str(market or "").lower() == "cn"
                and (
                    not bool(getattr(self, "p0_bounded_trial", False))
                    or bool(getattr(self, "p0_receipt_only", False))
                )
            )
            daily_window_plan: Optional[Dict[str, Any]] = None

            b1_storage_allowed = (
                b1_daily_grant is None
                or b1_daily_grant["rights"]["storage"] == "ALLOWED"
            )
            if (
                b1_storage_allowed
                and not force_refresh
                and self.db.has_today_data(code, target_date)
            ):
                if not strict_current:
                    logger.info(
                        "%s(%s) %s legacy resume cache present; skip fetch",
                        stock_name, code, target_date,
                    )
                    return True, None
                daily_window_plan = _current_daily_window_plan(
                    market=market, target_date=target_date,
                )
                cached = _load_verified_cached_daily_history(
                    db=self.db,
                    code=code,
                    market=market,
                    target_date=target_date,
                    window_plan=daily_window_plan,
                    current_time=current_time,
                )
                if cached is not None:
                    self._remember_run_local_daily_history(
                        code=code, target_date=target_date, frame=cached,
                    )
                    logger.info(
                        "%s(%s) %s typed current method window cache verified; skip provider",
                        stock_name, code, target_date,
                    )
                    return True, None

            logger.info("%s(%s) 开始从数据源获取数据...", stock_name, code)
            if daily_window_plan is None:
                daily_window_plan = _current_daily_window_plan(
                    market=market, target_date=target_date,
                )
            history_start = daily_window_plan["start_date"]
            df, source_name = self.fetcher_manager.get_daily_data(
                code,
                start_date=history_start.isoformat(),
                end_date=target_date.isoformat(),
                **({"allowed_provider_names": {b1_daily_grant["provider_identity"]}}
                   if b1_daily_grant is not None else {}),
            )
            if b1_daily_grant is not None and source_name != b1_daily_grant["provider_identity"]:
                raise DailyDataIdentityError("B1_UNADMITTED_PROVIDER_FALLBACK")
            if df is None or df.empty:
                return False, "获取数据为空"

            if bool(getattr(self, "p0_receipt_only", False)):
                if "date" not in df.columns:
                    return False, "receipt-only historical replay requires dated bars"
                source_identity = extract_daily_data_identity(df, strict=True)
                if source_identity is None:
                    return False, "receipt-only historical replay requires typed source identity"
                bounded = df.copy()
                bounded_dates = pd.to_datetime(
                    bounded["date"], errors="coerce",
                ).dt.date
                mask = (
                    bounded_dates.notna()
                    & (bounded_dates >= history_start)
                    & (bounded_dates <= target_date)
                )
                bounded = bounded.loc[mask].copy()
                if bounded.empty:
                    return False, "receipt-only historical replay returned no in-window bars"
                # Future-row removal changes the content bytes/range. Rebind the
                # existing OBSERVED source identity before the normal strict CN
                # request/calendar/receipt checks below; no storage or PIT right
                # is granted by this content identity.
                attach_daily_data_identity(
                    bounded,
                    rebind_daily_data_identity(source_identity, bounded),
                )
                df = bounded

            if not strict_current:
                saved_count = self.db.save_daily_data(df, code, source_name)
                logger.info(
                    "%s(%s) 数据保存成功（来源: %s，新增 %s 条）",
                    stock_name, code, source_name, saved_count,
                )
                return True, None

            identity = verify_observed_daily_frame_request(
                df,
                instrument=code,
                provider_identity=source_name,
                requested_start=history_start,
                requested_end=target_date,
                target_date=target_date,
                observed_at_ceiling=_provider_observation_ceiling(
                    current_time,
                    market=market,
                    target_date=target_date,
                    observation_time_frozen=bool(observation_time_frozen),
                ),
            )
            if (
                asset_admission["asset_route"] == "ETF"
                and identity.get("currency") != asset_admission["product_profile"]["currency"]
            ):
                raise DailyDataIdentityError("ETF_DAILY_LISTING_CURRENCY_MISMATCH")
            requested_sessions = resolve_session_range_fail_closed(
                market, history_start, target_date,
            )
            if not requested_sessions:
                raise DailyDataIdentityError(
                    "daily request exchange-session range is unavailable"
                )
            returned_dates = [
                value.date()
                for value in pd.to_datetime(df["date"], errors="raise")
            ]
            if (
                len(returned_dates) > len(requested_sessions)
                or returned_dates != requested_sessions[-len(returned_dates):]
            ):
                raise DailyDataIdentityError(
                    "daily response is not an exact completed-session suffix"
                )
            storage_frame = df.copy(deep=True)
            storage_frame.attrs = deepcopy(df.attrs)
            (
                df, identity, daily_window_plan, requested_sessions, returned_dates,
                window_extension,
            ) = self._extend_daily_history_for_monthly_macd(
                frame=df,
                source_name=source_name,
                identity=identity,
                code=code,
                market=str(market or "").strip().lower(),
                target_date=target_date,
                current_time=current_time,
                base_plan=daily_window_plan,
                observation_time_frozen=bool(observation_time_frozen),
                requested_sessions=requested_sessions,
                returned_dates=returned_dates,
            )
            history_start = daily_window_plan["start_date"]
            receipt = seal_daily_acquisition_receipt({
                "schema_version": DAILY_ACQUISITION_RECEIPT_SCHEMA_VERSION,
                "caller": "StockAnalysisPipeline.fetch_and_save_stock_data",
                "asset_route": asset_admission["asset_route"],
                "asset_admission": asset_admission,
                "market": str(market or "").strip().lower(),
                "instrument": normalize_stock_code(code),
                "target_date": target_date.isoformat(),
                "acquisition_mode": "VERIFIED_PROVIDER_CALL",
                "request_evidence_status": "VERIFIED_PROVIDER_CALL",
                "plan_hash": str(daily_window_plan.get("plan_hash") or ""),
                "planned_start": history_start.isoformat(),
                "planned_end": target_date.isoformat(),
                "planned_session_count": len(requested_sessions),
                "actual_request_start": history_start.isoformat(),
                "actual_request_end": target_date.isoformat(),
                "requested_session_count": len(requested_sessions),
                "cache_lookup_start": None,
                "cache_lookup_end": None,
                "returned_start": returned_dates[0].isoformat(),
                "returned_end": returned_dates[-1].isoformat(),
                "returned_base_bars": len(df),
                "admitted_base_bars": len(df),
                "source_identity_hash": identity["identity_hash"],
                "source_observed_at": identity.get("observed_at"),
                "source_adjustment_basis": identity.get(
                    "observed_adjustment_basis"
                ),
                "provider_identity": identity["provider_identity"],
                "provider_route": identity["provider_route"],
                "provider_method_call_count": (
                    window_extension["provider_method_call_count"]
                    if window_extension is not None else 1
                ),
                "provider_http_request_count": None,
                "persistence_status": "NOT_ATTEMPTED",
                **(
                    {"bounded_window_extension": window_extension}
                    if window_extension is not None else {}
                ),
                "actual_consumer": "StockAnalysisPipeline.analyze_stock",
            })
            attach_daily_acquisition_receipt(df, receipt)
            self._remember_run_local_daily_history(
                code=code, target_date=target_date, frame=df,
            )
            if (b1_daily_grant is not None
                and b1_daily_grant["rights"]["storage"] != "ALLOWED"):
                receipt = seal_daily_acquisition_receipt({
                    **receipt, "persistence_status": "RIGHTS_NOT_ADMITTED_RUN_LOCAL_ONLY",
                })
                attach_daily_acquisition_receipt(df, receipt)
                self._remember_run_local_daily_history(
                    code=code, target_date=target_date, frame=df,
                )
                return True, None
            try:
                saved_count = self.db.save_daily_data(storage_frame, code, source_name)
            except PermissionError as exc:
                if "SOURCE_STORAGE_RIGHTS_NOT_ADMITTED" not in str(exc):
                    self._clear_run_local_daily_history(
                        code=code, target_date=target_date,
                    )
                    raise
                receipt = seal_daily_acquisition_receipt({
                    **receipt,
                    "persistence_status": "RIGHTS_NOT_ADMITTED_RUN_LOCAL_ONLY",
                })
                attach_daily_acquisition_receipt(df, receipt)
                self._remember_run_local_daily_history(
                    code=code, target_date=target_date, frame=df,
                )
                logger.info(
                    "%s(%s) typed daily data admitted run-local; vendor storage rights remain HOLD",
                    stock_name, code,
                )
                return True, None
            except Exception:
                self._clear_run_local_daily_history(
                    code=code, target_date=target_date,
                )
                raise

            receipt = seal_daily_acquisition_receipt({
                **receipt,
                "persistence_status": (
                    "BASE_WINDOW_PERSISTED_EXTENSION_RUN_LOCAL"
                    if window_extension is not None
                    else "PERSISTED_BY_EXISTING_STORAGE_GATE"
                ),
                "persisted_rows": int(saved_count),
            })
            attach_daily_acquisition_receipt(df, receipt)
            self._remember_run_local_daily_history(
                code=code, target_date=target_date, frame=df,
            )
            logger.info(
                "%s(%s) typed daily base saved（来源: %s，新增 %s 条；动态续长仅run-local=%s）",
                stock_name, code, source_name, saved_count, window_extension is not None,
            )
            return True, None

        except DailyDataIdentityError as exc:
            if target_date is not None:
                self._clear_run_local_daily_history(
                    code=code, target_date=target_date,
                )
            error_msg = f"DAILY_ACQUISITION_BINDING_INVALID: {exc}"
            logger.error("%s(%s) %s", stock_name, code, error_msg)
            return False, error_msg
        except Exception as exc:
            if target_date is not None:
                self._clear_run_local_daily_history(
                    code=code, target_date=target_date,
                )
            error_msg = f"获取/保存数据失败: {exc}"
            logger.error("%s(%s) %s", stock_name, code, error_msg)
            return False, error_msg
    
    def _load_current_stock_intraday_timeframes(
        self,
        *,
        code: str,
        stock_name: str,
        market: Optional[str],
        target_date: date,
        completed_daily_history: Optional[pd.DataFrame],
        current_time: Optional[datetime] = None,
    ) -> Dict[str, pd.DataFrame]:
        """Reuse one current CN stock/qualified-ETF price path; missing stays missing."""
        if bool(getattr(self, "p0_bounded_trial", False)) or bool(
            getattr(self, "p0_receipt_only", False)
        ):
            return {}
        if str(market or "").strip().lower() != "cn":
            return {}
        asset_admission = resolve_cn_price_asset(code)
        if asset_admission["status"] != "ADMITTED":
            logger.info("%s price admission: %s", code, asset_admission["reason"])
            return {}
        code = asset_admission["instrument"]
        daily_identity = None
        if asset_admission["asset_route"] == "ETF":
            try:
                from data_provider.daily_data_identity import extract_daily_data_identity
                daily_identity = extract_daily_data_identity(completed_daily_history, strict=True)
                verify_observed_daily_frame_request(
                    completed_daily_history, instrument=code,
                    provider_identity=daily_identity["provider_identity"],
                    requested_start=daily_identity["requested_start"],
                    requested_end=daily_identity["requested_end"], target_date=target_date,
                    observed_at_ceiling=_daily_observation_ceiling(current_time),
                )
                if daily_identity.get("currency") != asset_admission["product_profile"]["currency"]:
                    raise DailyDataIdentityError("ETF_DAILY_LISTING_CURRENCY_MISMATCH")
            except (DailyDataIdentityError, KeyError, TypeError, ValueError):
                return {}

        latest_completed = resolve_latest_completed_session_fail_closed("cn")
        if latest_completed is None or target_date != latest_completed:
            return {}
        if (
            not isinstance(completed_daily_history, pd.DataFrame)
            or completed_daily_history.empty
            or "date" not in completed_daily_history.columns
        ):
            return {}

        parsed_dates = pd.to_datetime(
            completed_daily_history["date"],
            errors="coerce",
        )
        if parsed_dates.isna().any():
            return {}
        available_sessions = sorted(
            {
                value.date()
                for value in parsed_dates.tolist()
                if value.date() <= target_date
            }
        )
        window_plan = _current_intraday_window_plan()
        base_sessions = int(window_plan["required_sessions"])
        if len(available_sessions) < base_sessions:
            return {}
        # Pick the most informative *verified* completed window, once per
        # asset. Insufficient older bars only withhold MACD; valid MA/RSI are
        # not discarded and we never silently fill a missing XSHG session.
        candidate_sizes = sorted({
            base_sessions, int(window_plan["admission_target_sessions"]),
        }, reverse=True)
        expected_sessions = None
        for count in candidate_sizes:
            if len(available_sessions) < count:
                continue
            candidate = available_sessions[-count:]
            if candidate[-1] != target_date:
                continue
            exact = resolve_forward_sessions_fail_closed(
                "cn", candidate[0] - timedelta(days=1), count,
            )
            if exact == candidate:
                expected_sessions = candidate
                break
        if expected_sessions is None:
            logger.warning(
                "%s(%s) intraday context blocked: no independently verified "
                "completed XSHG window for current minute methods",
                stock_name, code,
            )
            return {}

        # The 5m source has independent rights from the daily source only
        # inside an explicitly selected B1 operation.  Outside B1, preserve
        # the accepted B5 source-identity/method-window path without inventing
        # a grant or treating its absence as a provider failure.
        minute_grant = None
        if isinstance(getattr(self, "b1_rights_context", None), Mapping):
            b1 = self._bind_b1_run_context(
                current_time,
                persist=not bool(getattr(self, "_b1_preacquisition_active", False)),
            )
            try:
                if not isinstance(b1, Mapping):
                    raise DataObjectAdmissionError("B1_OPERATION_CONTEXT_MISSING")
                allowed = {
                    str(item or "").strip().upper()
                    for item in b1.get("allowed_instruments") or ()
                }
                if str(code or "").strip().upper() not in allowed:
                    raise DataObjectAdmissionError(
                        "B1_TARGET_INSTRUMENT_NOT_ADMITTED"
                    )
                minute_dataset = "E_PRICE_5M" if asset_admission["asset_route"] == "ETF" else "P_5M"
                minute_consumer = "ETF_ASSET_BRIEF" if asset_admission["asset_route"] == "ETF" else "ASSET_RESEARCH_BRIEF"
                minute_grant = validate_b1_provider_preflight(
                    (b1.get("rights_by_dataset") or {}).get(minute_dataset),
                    operation_id=b1.get("operation_id"),
                    intended_use=b1.get("intended_use"),
                    dataset_id=minute_dataset, consumer_scope=minute_consumer,
                    research_as_of=b1.get("research_as_of"),
                )
                if minute_grant["provider_identity"] != "BaostockFetcher":
                    raise DataObjectAdmissionError("B1_INTRADAY_PROVIDER_MISMATCH")
            except (DataObjectAdmissionError, AttributeError, TypeError):
                return {}
        cached_timeframes = self._get_run_local_intraday_timeframes(
            code=code, target_date=target_date,
        )
        if cached_timeframes is not None:
            from data_provider.intraday_data_identity import (
                extract_intraday_data_identity,
            )
            cached_identity = extract_intraday_data_identity(
                cached_timeframes.get("5m"), strict=True,
            )
            if (
                minute_grant is not None
                and cached_identity.get("provider_identity")
                != minute_grant["provider_identity"]
            ):
                raise DataObjectAdmissionError(
                    "B1_RUN_LOCAL_5M_CACHE_PROVIDER_MISMATCH"
                )
            return cached_timeframes

        try:
            fetcher = self.fetcher_manager._get_fetcher_by_name(
                "BaostockFetcher",
                capability="intraday_5m",
            )
            if fetcher is None:
                return {}
            # Capture the actual call range before a valid shorter response may
            # narrow expected_sessions. A method's desired window is not this call.
            requested_sessions = tuple(expected_sessions)
            five_minute = self.fetcher_manager._call_fetcher_method(
                fetcher,
                "get_intraday_data",
                code,
                start_date=expected_sessions[0].isoformat(),
                end_date=expected_sessions[-1].isoformat(),
                frequency="5",
            )
            from data_provider.intraday_data_identity import extract_intraday_data_identity

            source_identity = extract_intraday_data_identity(five_minute, strict=True)
            if (
                source_identity is None
                or source_identity.get("timeframe") != "5m"
                or source_identity.get("provider_identity") != "BaostockFetcher"
                or source_identity.get("requested_start") != requested_sessions[0].isoformat()
                or source_identity.get("requested_end") != requested_sessions[-1].isoformat()
                or set(five_minute["code"].astype(str)) != {str(code).strip()}
            ):
                raise ValueError("INTRADAY_RESPONSE_REQUEST_IDENTITY_MISMATCH")
            if daily_identity is not None:
                query = source_identity.get("query_identity") or {}
                exchange = asset_admission["product_profile"]["listing_exchange"].lower()
                if (
                    str(query.get("code") or "").lower() != f"{exchange}.{code}"
                    or str(query.get("frequency") or "") != "5"
                    or str(query.get("adjustflag") or "") != {
                        "qfq": "2", "hfq": "1", "raw": "3",
                    }.get(source_identity.get("observed_adjustment_basis"))
                    or source_identity.get("requested_adjustment_basis")
                    != source_identity.get("observed_adjustment_basis")
                ):
                    raise ValueError("ETF_MINUTE_QUERY_ASSET_OR_BASIS_MISMATCH")
                ceiling = pd.Timestamp(_daily_observation_ceiling(current_time))
                for clock_key in ("observed_at", "available_at_max"):
                    observed = pd.to_datetime(source_identity.get(clock_key), utc=True, errors="coerce")
                    if pd.isna(observed) or observed > ceiling:
                        raise ValueError("ETF_MINUTE_OBSERVATION_NOT_KNOWN_AT_EXECUTION")
            if daily_identity is not None and any(
                source_identity.get(minute_key) != daily_identity.get(daily_key)
                for minute_key, daily_key in (
                    ("provider_identity", "provider_identity"),
                    ("observed_adjustment_basis", "observed_adjustment_basis"),
                    ("currency", "currency"),
                )
            ):
                raise ValueError("ETF_DAILY_MINUTE_SOURCE_BASIS_MISMATCH")
            try:
                validate_complete_cn_5m_sessions(
                    five_minute,
                    expected_session_dates=expected_sessions,
                )
            except IntradayBarError:
                # A provider can return only the latest fully completed source
                # suffix even when the checked daily calendar supports more.
                # Reuse that exact response once; never fabricate old 5m bars,
                # request a second source or accept a partial/foreign session.
                if len(expected_sessions) <= base_sessions:
                    raise
                short_sessions = expected_sessions[-base_sessions:]
                if resolve_forward_sessions_fail_closed(
                    "cn", short_sessions[0] - timedelta(days=1),
                    base_sessions,
                ) != short_sessions:
                    raise
                validate_complete_cn_5m_sessions(
                    five_minute,
                    expected_session_dates=short_sessions,
                )
                logger.info(
                    "%s(%s) minute source returned one verified short suffix: "
                    "%s/%s XSHG sessions; recursive MACD readiness checked "
                    "independently against actual received bars",
                    stock_name, code, base_sessions, len(expected_sessions),
                )
                expected_sessions = short_sessions
            derived = {
                "5m": five_minute,
                "15m": aggregate_completed_intraday_bars(
                    five_minute,
                    target_timeframe="15m",
                ),
                "30m": aggregate_completed_intraday_bars(
                    five_minute,
                    target_timeframe="30m",
                ),
                "60m": aggregate_completed_intraday_bars(
                    five_minute,
                    target_timeframe="60m",
                ),
            }
            expected_rows = {
                "5m": 48 * len(expected_sessions),
                "15m": 16 * len(expected_sessions),
                "30m": 8 * len(expected_sessions),
                "60m": 4 * len(expected_sessions),
            }
            if any(len(derived[key]) != count for key, count in expected_rows.items()):
                raise ValueError(
                    f"intraday aggregation cardinality mismatch: "
                    f"{ {key: len(value) for key, value in derived.items()} }"
                )
            acquisition = {
                "schema_version": "dsa-intraday-acquisition-v1",
                "caller": "StockAnalysisPipeline._load_current_stock_intraday_timeframes",
                "asset_route": asset_admission["asset_route"],
                "asset_admission": asset_admission,
                "instrument": str(code).strip(),
                "target_date": target_date.isoformat(),
                "requested_start": requested_sessions[0].isoformat(),
                "requested_end": requested_sessions[-1].isoformat(),
                "requested_session_dates": [day.isoformat() for day in requested_sessions],
                "requested_session_count": len(requested_sessions),
                "returned_base_bars": len(five_minute),
                "admitted_session_count": len(expected_sessions),
                "source_identity_hash": source_identity["identity_hash"],
                "provider_identity": source_identity["provider_identity"],
                "provider_route": source_identity["provider_route"],
                "provider_method_call_count": 1,
                # A provider-method call is not a count of HTTP requests/retries.
                "provider_http_request_count": None,
            }
            acquisition["receipt_hash"] = sha256_payload(acquisition)
            five_minute.attrs["dsa_intraday_acquisition_receipt"] = acquisition
            self._remember_run_local_intraday_timeframes(
                code=code, target_date=target_date, timeframes=derived,
            )
            return derived
        except Exception as exc:
            logger.warning(
                "%s(%s) current intraday context unavailable; keep intraday MISSING: %s",
                stock_name,
                code,
                exc,
            )
            return {}

    def analyze_stock(
        self,
        code: str,
        report_type: ReportType,
        query_id: str,
        current_time: Optional[datetime] = None,
        observation_time_frozen: Optional[bool] = None,
    ) -> Optional[AnalysisResult]:
        """
        分析单只股票（增强版：含量比、换手率、筹码分析、多维度情报）
        
        流程：
        1. 获取实时行情（量比、换手率）- 通过 DataFetcherManager 自动故障切换
        2. 获取筹码分布 - 通过 DataFetcherManager 带熔断保护
        3. 进行趋势分析（基于交易理念）
        4. 多维度情报搜索（最新消息+风险排查+业绩预期）
        5. 从数据库获取分析上下文
        6. 调用 AI 进行综合分析
        
        Args:
            query_id: 查询链路关联 id
            code: 股票代码
            report_type: 报告类型
            current_time: 本轮运行冻结的参考时间，用于统一市场阶段上下文
            
        Returns:
            AnalysisResult 或 None（如果分析失败）
        """
        stock_name = code
        try:
            asset_admission = resolve_cn_price_asset(code)
            if observation_time_frozen is None:
                observation_time_frozen = current_time is not None
            if asset_admission["market"] == "cn":
                if asset_admission["status"] != "ADMITTED":
                    logger.warning("%s price admission: %s", code, asset_admission["reason"])
                    return None
                code = asset_admission["instrument"]
            b1_no_send = (
                asset_admission["market"] == "cn"
                and not bool(getattr(self, "p0_bounded_trial", False))
                and isinstance(getattr(self, "b1_rights_context", None), Mapping)
            )
            if b1_no_send:
                self._bind_b1_run_context(current_time)
            portfolio_context = getattr(self, "portfolio_context", None)
            if not isinstance(portfolio_context, dict):
                portfolio_context = None
            market = get_market_for_stock(normalize_stock_code(code))
            market_phase_context = build_market_phase_context(
                market=market,
                current_time=current_time,
                trigger_source=self.query_source,
                analysis_phase=getattr(self, "analysis_phase", "auto"),
            )
            market_phase_context_dict = market_phase_context.to_dict()
            market_phase_summary = render_market_phase_summary(market_phase_context_dict)
            report_language = normalize_report_language(getattr(self.config, "report_language", "zh"))
            daily_market_target_date = self._coerce_daily_market_context_date(
                getattr(market_phase_context, "effective_daily_bar_date", None)
                or market_phase_context_dict.get("effective_daily_bar_date")
            )
            if daily_market_target_date is None:
                daily_market_target_date = get_effective_trading_date(
                    market,
                    current_time=current_time,
                )
            daily_market_context = (
                None
                if b1_no_send
                else self._load_daily_market_context(
                    market,
                    target_date=daily_market_target_date,
                )
            )

            self._emit_progress(18, f"{code}：正在获取行情与筹码数据")
            # Name/realtime endpoints have independent rights from price bars.
            stock_name = (
                code
                if b1_no_send
                else self.fetcher_manager.get_stock_name(code, allow_realtime=False)
            )

            # Step 1: 获取实时行情（量比、换手率等）- 使用统一入口，自动故障切换
            realtime_quote = None
            try:
                if not b1_no_send and self.config.enable_realtime_quote:
                    realtime_quote = self.fetcher_manager.get_realtime_quote(code, log_final_failure=False)
                    if realtime_quote:
                        # 使用实时行情返回的真实股票名称
                        if realtime_quote.name:
                            stock_name = realtime_quote.name
                        # 兼容不同数据源的字段（有些数据源可能没有 volume_ratio）
                        volume_ratio = getattr(realtime_quote, 'volume_ratio', None)
                        turnover_rate = getattr(realtime_quote, 'turnover_rate', None)
                        logger.info(f"{stock_name}({code}) 实时行情: 价格={realtime_quote.price}, "
                                  f"量比={volume_ratio}, 换手率={turnover_rate}% "
                                  f"(来源: {realtime_quote.source.value if hasattr(realtime_quote, 'source') else 'unknown'})")
                    else:
                        logger.warning(f"{stock_name}({code}) 所有实时行情数据源均不可用，已降级为历史收盘价继续分析")
                else:
                    logger.info(f"{stock_name}({code}) 实时行情已禁用，使用历史收盘价继续分析")
            except Exception as e:
                logger.warning(f"{stock_name}({code}) 实时行情链路异常，已降级为历史收盘价继续分析: {e}")

            # 如果还是没有名称，使用代码作为名称
            if not stock_name:
                stock_name = f'股票{code}'

            # Step 2: 获取筹码分布 - 使用统一入口，带熔断保护
            chip_data = None
            try:
                if b1_no_send:
                    chip_data = None
                else:
                    chip_data = self.fetcher_manager.get_chip_distribution(code)
                if chip_data:
                    logger.info(f"{stock_name}({code}) 筹码分布: 获利比例={chip_data.profit_ratio:.1%}, "
                              f"90%集中度={chip_data.concentration_90:.2%}")
                else:
                    logger.debug(f"{stock_name}({code}) 筹码分布获取失败或已禁用")
            except Exception as e:
                logger.warning(f"{stock_name}({code}) 获取筹码分布失败: {e}")

            # If agent mode is explicitly enabled, or specific agent skills are configured, use the Agent analysis pipeline.
            # NOTE: use config.agent_mode (explicit opt-in) instead of
            # config.is_agent_available() so that users who only configured an
            # API Key for the traditional analysis path are not silently
            # switched to Agent mode (which is slower and more expensive).
            use_agent = (
                False
                if self.p0_bounded_trial or b1_no_send
                else getattr(self.config, 'agent_mode', False)
            )
            if not self.p0_bounded_trial and not b1_no_send and not use_agent:
                if self.analysis_skills:
                    use_agent = True
                    logger.info(f"{stock_name}({code}) Auto-enabled agent mode due to request skills: {self.analysis_skills}")
            if not self.p0_bounded_trial and not b1_no_send and not use_agent:
                # Auto-enable agent mode when specific skills are configured (e.g., scheduled task with strategy)
                configured_skills = getattr(self.config, 'agent_skills', [])
                if configured_skills and configured_skills != ['all']:
                    use_agent = True
                    logger.info(f"{stock_name}({code}) Auto-enabled agent mode due to configured skills: {configured_skills}")

            self._emit_progress(32, f"{stock_name}：正在聚合基本面与趋势数据")

            # Step 2.5: 基本面能力聚合（统一入口，异常降级）
            # - 失败时返回 partial/failed，不影响既有技术面/新闻链路
            # - 关闭开关时仍返回 not_supported 结构
            fundamental_context = None
            if b1_no_send:
                fundamental_context = {
                    "market": market,
                    "status": "NOT_ADMITTED",
                    "reason": "B1_FUNDAMENTAL_SOURCE_NOT_ADMITTED",
                    "source_chain": [],
                    "coverage": {},
                    "belong_boards": [],
                }
                market_structure_context = None
            else:
                try:
                    fundamental_context = self.fetcher_manager.get_fundamental_context(
                        code,
                        budget_seconds=getattr(
                            self.config,
                            'fundamental_stage_timeout_seconds',
                            FUNDAMENTAL_STAGE_TIMEOUT_SECONDS_DEFAULT,
                        ),
                    )
                except Exception as e:
                    logger.warning(f"{stock_name}({code}) 基本面聚合失败: {e}")
                    fundamental_context = self.fetcher_manager.build_failed_fundamental_context(code, str(e))

                fundamental_context = self._attach_belong_boards_to_fundamental_context(
                    code,
                    fundamental_context,
                )
                market_structure_context = self._build_market_structure_context(
                    code=code,
                    stock_name=stock_name,
                    market=market,
                    fundamental_context=fundamental_context,
                    trade_date=daily_market_target_date,
                    market_phase_summary=market_phase_summary,
                )

            # B1 NO_SEND has no database durability. Other routes keep the
            # original best-effort snapshot behavior.
            if not b1_no_send:
                try:
                    self.db.save_fundamental_snapshot(
                        query_id=query_id,
                        code=code,
                        payload=fundamental_context,
                        source_chain=fundamental_context.get("source_chain", []),
                        coverage=fundamental_context.get("coverage", {}),
                    )
                except Exception as e:
                    logger.debug(f"{stock_name}({code}) 基本面快照写入失败: {e}")

            # Step 3: canonical daily evidence consumes only a verified provider
            # call or a content-bound typed cache. Legacy rows may still support
            # display compatibility, but cannot enter strict methods/canonical.
            trend_result: Optional[TrendAnalysisResult] = None
            canonical_trend_result: Optional[TrendAnalysisResult] = None
            completed_daily_history: Optional[pd.DataFrame] = None
            legacy_trend_history: Optional[pd.DataFrame] = None
            daily_window_plan: Optional[Dict[str, Any]] = None
            try:
                from src.services.history_loader import get_frozen_target_date
                _mkt = get_market_for_stock(normalize_stock_code(code))
                frozen = get_frozen_target_date()
                end_date = frozen if frozen else daily_market_target_date
                daily_window_plan = _current_daily_window_plan(
                    market=_mkt,
                    target_date=end_date,
                )
                completed_daily_history = self._get_run_local_daily_history(
                    code=code, target_date=end_date,
                )
                if completed_daily_history is None and not b1_no_send:
                    completed_daily_history = _load_verified_cached_daily_history(
                        db=self.db,
                        code=code,
                        market=_mkt,
                        target_date=end_date,
                        window_plan=daily_window_plan,
                        current_time=current_time,
                    )
                    if completed_daily_history is not None:
                        self._remember_run_local_daily_history(
                            code=code,
                            target_date=end_date,
                            frame=completed_daily_history,
                        )
                if completed_daily_history is not None:
                    daily_receipt = extract_daily_acquisition_receipt(
                        completed_daily_history, strict=True,
                    )
                    extension = (
                        daily_receipt.get("bounded_window_extension")
                        if isinstance(daily_receipt, Mapping) else None
                    )
                    if isinstance(extension, Mapping):
                        daily_window_plan = _daily_plan_with_monthly_macd_bars(
                            base_plan=daily_window_plan,
                            target_date=end_date,
                            monthly_bars=int(extension["final_requested_monthly_bars"]),
                        )
                        if daily_window_plan["plan_hash"] != daily_receipt.get("plan_hash"):
                            raise DailyDataIdentityError(
                                "DAILY_MONTHLY_EXTENSION_PLAN_RECEIPT_MISMATCH"
                            )
                start_date = daily_window_plan["start_date"]
                if completed_daily_history is None:
                    if not b1_no_send:
                        historical_bars = self.db.get_data_range(
                            code, start_date, end_date,
                        )
                        if historical_bars:
                            legacy_trend_history = pd.DataFrame(
                                [bar.to_dict() for bar in historical_bars]
                            )
                else:
                    legacy_trend_history = completed_daily_history.copy()
                    if len(completed_daily_history) >= 60:
                        canonical_trend_result = self.trend_analyzer.analyze(
                            completed_daily_history.copy(), code
                        )
                if isinstance(legacy_trend_history, pd.DataFrame) and not legacy_trend_history.empty:
                    trend_df = legacy_trend_history.copy()
                    if self.config.enable_realtime_quote and realtime_quote:
                        trend_df = self._augment_historical_with_realtime(
                            trend_df, realtime_quote, code,
                        )
                        trend_result = self.trend_analyzer.analyze(trend_df, code)
                    else:
                        trend_result = canonical_trend_result or self.trend_analyzer.analyze(
                            trend_df, code,
                        )
                    logger.info(
                        "%s(%s) 趋势分析: %s, 买入信号=%s, 评分=%s",
                        stock_name, code, trend_result.trend_status.value,
                        trend_result.buy_signal.value, trend_result.signal_score,
                    )
            except Exception as exc:
                logger.warning(
                    "%s(%s) 趋势分析失败: %s",
                    stock_name, code, exc, exc_info=True,
                )

            relative_strength_context = None
            if (
                not b1_no_send
                and canonical_trend_result is not None
                and not SearchService.is_index_or_etf(code, stock_name)
            ):
                try:
                    relative_strength_context = self.relative_strength_service.build_context(
                        stock_code=code,
                        market=market,
                        stock_history=completed_daily_history,
                        target_date=daily_market_target_date,
                    )
                except Exception as e:
                    logger.warning(f"{stock_name}({code}) 相对强弱证据构建失败，按缺失处理: {e}")

            intraday_timeframes = self._load_current_stock_intraday_timeframes(
                code=code,
                stock_name=stock_name,
                market=market,
                target_date=daily_market_target_date,
                completed_daily_history=completed_daily_history,
                current_time=(current_time if observation_time_frozen else None),
            )
            # Replay callers retain their exact known-at cutoff; live runs use
            # wall clock here would make an otherwise frozen source snapshot
            # appear after its decision/send time and can also cross a later
            # exchange session while the target bars remain unchanged.
            market_data_snapshot_observed_at = _provider_observation_ceiling(
                current_time,
                market=market,
                target_date=daily_market_target_date,
                observation_time_frozen=bool(observation_time_frozen),
            )
            completed_history_identity = (
                build_completed_history_identity(
                    completed_daily_history,
                    stock_code=code,
                    market=market,
                    target_date=daily_market_target_date,
                    observed_at=market_data_snapshot_observed_at,
                )
                if isinstance(completed_daily_history, pd.DataFrame)
                and not completed_daily_history.empty
                else {}
            )
            if completed_history_identity:
                completed_history_identity = {
                    **completed_history_identity,
                    "stock_code": str(code or "").strip(),
                    "market": str(market or "").strip().lower() or None,
                    "target_date": daily_market_target_date.isoformat(),
                }
            receipt_asset_route = (
                asset_admission["asset_route"] if market == "cn"
                else ("ETF" if SearchService.is_index_or_etf(code, stock_name) else "STOCK")
            )
            supply_demand_context = build_supply_demand_context(
                stock_code=code,
                history=completed_daily_history,
                target_date=daily_market_target_date,
            )
            cost_structure_context = build_cost_structure_context(
                stock_code=code,
                history=completed_daily_history,
                chip_data=chip_data,
                target_date=daily_market_target_date,
                market=market,
            )
            price_structure_context = build_price_structure_context(
                stock_code=code,
                history=completed_daily_history,
                target_date=daily_market_target_date,
                market=market,
            )
            volatility_momentum_context = build_volatility_momentum_context(
                stock_code=code,
                history=completed_daily_history,
                target_date=daily_market_target_date,
                market=market,
                trend_result=canonical_trend_result,
                price_structure_context=price_structure_context,
            )
            pattern_trigger_context = build_pattern_trigger_context(
                stock_code=code,
                history=completed_daily_history,
                target_date=daily_market_target_date,
                market=market,
                price_structure_context=price_structure_context,
                supply_demand_context=supply_demand_context,
            )
            multi_timeframe_structure_context = build_multi_timeframe_structure_context(
                stock_code=code,
                history=completed_daily_history,
                target_date=daily_market_target_date,
                market=market,
                trend_analyzer=self.trend_analyzer,
                daily_trend_result=canonical_trend_result,
                daily_price_structure_context=price_structure_context,
                snapshot_observed_at=market_data_snapshot_observed_at,
                intraday_timeframes=intraday_timeframes,
            )
            if market == "cn":
                multi_timeframe_structure_context["asset_admission"] = asset_admission
            multi_timeframe_structure_context["method_window_readiness_trace"] = (
                compile_mtf_method_window_readiness_trace(
                    asset_route=receipt_asset_route,
                    instrument=code,
                    target_date=daily_market_target_date,
                    daily_window_plan=daily_window_plan,
                    intraday_window_plan=_current_intraday_window_plan(),
                    multi_timeframe_structure_context=multi_timeframe_structure_context,
                    intraday_source_frames=intraday_timeframes,
                    daily_source_frame=completed_daily_history,
                )
            )
            fundamental_context = self._compile_business_valuation_context(
                fundamental_context,
                target_date=daily_market_target_date,
            )

            method_execution_receipts = self._build_direct_method_execution_receipts(
                code=code,
                market=market,
                asset_route=receipt_asset_route,
                target_date=daily_market_target_date,
                completed_history_identity=completed_history_identity,
                chip_data=chip_data,
                canonical_trend_result=canonical_trend_result,
                supply_demand_context=supply_demand_context,
                cost_structure_context=cost_structure_context,
                price_structure_context=price_structure_context,
                volatility_momentum_context=volatility_momentum_context,
                pattern_trigger_context=pattern_trigger_context,
                multi_timeframe_structure_context=multi_timeframe_structure_context,
                fundamental_context=fundamental_context,
            )

            if use_agent:
                logger.info(f"{stock_name}({code}) 启用 Agent 模式进行分析")
                self._emit_progress(58, f"{stock_name}：正在切换 Agent 分析链路")
                return self._analyze_with_agent(
                    code,
                    report_type,
                    query_id,
                    stock_name,
                    realtime_quote,
                    chip_data,
                    fundamental_context,
                    trend_result,
                    market_phase_context=market_phase_context_dict,
                    market_phase_summary=market_phase_summary,
                    daily_market_context=daily_market_context,
                    portfolio_context=portfolio_context,
                    market_structure_context=market_structure_context,
                    canonical_trend_result=canonical_trend_result,
                    relative_strength_context=relative_strength_context,
                    supply_demand_context=supply_demand_context,
                    cost_structure_context=cost_structure_context,
                    price_structure_context=price_structure_context,
                    volatility_momentum_context=volatility_momentum_context,
                    pattern_trigger_context=pattern_trigger_context,
                    multi_timeframe_structure_context=multi_timeframe_structure_context,
                    method_execution_receipts=method_execution_receipts,
                )

            # Step 4: 多维度情报搜索（最新消息+风险排查+业绩预期）
            news_context = None
            persisted_intelligence_context = ""
            if not self.p0_bounded_trial and not b1_no_send:
                persisted_intelligence_context = self._load_persisted_intelligence_context(
                    code=code,
                    stock_name=stock_name,
                    market=market or "cn",
                )
            news_result_count: Optional[int] = None
            self._emit_progress(46, f"{stock_name}：正在检索新闻与舆情")
            if (
                not b1_no_send
                and self.search_service is not None
                and self.search_service.is_available
            ):
                logger.info(f"{stock_name}({code}) 开始多维度情报搜索...")

                # 使用多维度搜索（最多5次搜索）
                intel_results = self.search_service.search_comprehensive_intel(
                    stock_code=code,
                    stock_name=stock_name,
                    max_searches=5
                )

                # 格式化情报报告
                if intel_results:
                    news_context = self.search_service.format_intel_report(intel_results, stock_name)
                    total_results = sum(
                        len(r.results) for r in intel_results.values() if r.success
                    )
                    news_result_count = total_results
                    logger.info(f"{stock_name}({code}) 情报搜索完成: 共 {total_results} 条结果")
                    logger.debug(f"{stock_name}({code}) 情报搜索结果:\n{news_context}")

                    # 保存新闻情报到数据库（用于后续复盘与查询）
                    try:
                        query_context = self._build_query_context(query_id=query_id)
                        for dim_name, response in intel_results.items():
                            if response and response.success and response.results:
                                self.db.save_news_intel(
                                    code=code,
                                    name=stock_name,
                                    dimension=dim_name,
                                    query=response.query,
                                    response=response,
                                    query_context=query_context
                                )
                    except Exception as e:
                        logger.warning(f"{stock_name}({code}) 保存新闻情报失败: {e}")
            else:
                logger.info(f"{stock_name}({code}) 搜索服务不可用，跳过情报搜索")

            # Step 4.5: Social sentiment intelligence (US stocks only)
            if self.social_sentiment_service is not None and self.social_sentiment_service.is_available and is_us_stock_code(code):
                try:
                    social_context = self.social_sentiment_service.get_social_context(code)
                    if social_context:
                        logger.info(f"{stock_name}({code}) Social sentiment data retrieved")
                        if news_context:
                            news_context = news_context + "\n\n" + social_context
                        else:
                            news_context = social_context
                except Exception as e:
                    logger.warning(f"{stock_name}({code}) Social sentiment fetch failed: {e}")

            if persisted_intelligence_context:
                news_context = (
                    f"{news_context}\n\n{persisted_intelligence_context}"
                    if news_context
                    else persisted_intelligence_context
                )

            # Step 5: 获取分析上下文（技术面数据）
            self._emit_progress(58, f"{stock_name}：正在整理分析上下文")
            context = (
                self._build_analysis_context_from_daily_df(code, completed_daily_history)
                if b1_no_send
                else self._get_analysis_context_with_market_fallback(code)
            )

            if context is None:
                logger.warning(f"{stock_name}({code}) 无法获取历史行情数据，将仅基于新闻和实时行情分析")
                _mkt_date = get_market_now(
                    get_market_for_stock(normalize_stock_code(code))
                ).date()
                context = {
                    'code': code,
                    'stock_name': stock_name,
                    'date': _mkt_date.isoformat(),
                    'data_missing': True,
                    'today': {},
                    'yesterday': {}
                }
            
            # Step 6: 增强上下文数据（添加实时行情、筹码、趋势分析结果、股票名称）
            enhanced_context = self._enhance_context(
                context, 
                realtime_quote, 
                chip_data,
                trend_result,
                stock_name,  # 传入股票名称
                fundamental_context,
                market_phase_context=market_phase_context_dict,
                portfolio_context=portfolio_context,
            )
            enhanced_context["market_phase_context"] = market_phase_context_dict
            self._attach_daily_market_context(
                enhanced_context,
                daily_market_context,
                report_language=report_language,
            )
            if portfolio_context is not None:
                enhanced_context["portfolio_context"] = dict(portfolio_context)
            if isinstance(market_structure_context, dict):
                enhanced_context["market_structure_context"] = market_structure_context
            
            # Step 7: 调用 AI 分析（传入增强的上下文和新闻）
            (
                analysis_context_pack_summary,
                analysis_context_pack_overview,
            ) = self._build_analysis_context_pack_outputs(
                self._build_legacy_analysis_artifacts(
                    code=code,
                    stock_name=stock_name,
                    market=market,
                    phase=market_phase_context_dict,
                    context=context,
                    enhanced_context=enhanced_context,
                    realtime_quote=realtime_quote,
                    trend_result=trend_result,
                    chip_data=chip_data,
                    fundamental_context=fundamental_context,
                    news_context=news_context,
                    news_result_count=news_result_count,
                    query_id=query_id,
                    portfolio_context=portfolio_context,
                ),
                report_language=report_language,
                code=code,
                query_id=query_id,
            )
            llm_progress_state = {"last_progress": 64}

            def _on_llm_stream(chars_received: int) -> None:
                dynamic_progress = min(92, 64 + min(chars_received // 80, 28))
                if dynamic_progress <= llm_progress_state["last_progress"]:
                    return
                llm_progress_state["last_progress"] = dynamic_progress
                self._emit_progress(
                    dynamic_progress,
                    f"{stock_name}：LLM 正在生成分析结果（已接收 {chars_received} 字符）",
                )

            if bool(getattr(self, "p0_receipt_only", False)) or b1_no_send:
                # Receipt-only is a deterministic evidence/rights probe, not an
                # explanation request.  Build one neutral result container and
                # let the existing Factor/canonical owner below replace every
                # public action field.  Never call a model, invent a score, or
                # fork a second analysis/report authority for this mode.
                result = AnalysisResult(
                    code=code,
                    name=stock_name,
                    sentiment_score=0,
                    trend_prediction="未知",
                    operation_advice="观望",
                    decision_type="hold",
                    confidence_level="低",
                    report_language=report_language,
                    action="watch",
                    action_label="观望",
                    analysis_summary=(
                        "仅消费获准的确定性价格证据；未调用模型或其他未准入来源"
                        if b1_no_send
                        else "仅记录确定性证据回执；未调用模型生成解释"
                    ),
                    buy_reason="",
                    dashboard={},
                    success=True,
                )
                result.query_id = query_id
                result.current_price = (enhanced_context.get("realtime") or {}).get("price")
                result.change_pct = (enhanced_context.get("realtime") or {}).get("change_pct")
                logger.info(
                    "[%s] deterministic no-model mode: continue with canonical evidence",
                    code,
                )
            else:
                self._emit_progress(64, f"{stock_name}：正在请求 LLM 生成报告")
                llm_started_at = time.monotonic()
                try:
                    record_llm_run_started(
                        model=getattr(self.config, "litellm_model", None),
                        call_type="analysis",
                    )
                    result = self.analyzer.analyze(
                        enhanced_context,
                        news_context=news_context,
                        progress_callback=self._emit_progress,
                        stream_progress_callback=(
                            None if self.p0_bounded_trial else _on_llm_stream
                        ),
                        analysis_context_pack_summary=analysis_context_pack_summary,
                        p0_bounded_trial=self.p0_bounded_trial,
                    )
                    llm_duration_ms = int((time.monotonic() - llm_started_at) * 1000)
                    record_llm_run(
                        success=bool(result and getattr(result, "success", True)),
                        model=getattr(result, "model_used", None) if result else None,
                        call_type="analysis",
                        duration_ms=llm_duration_ms,
                        error_type=(
                            None
                            if result and getattr(result, "success", True)
                            else "AnalysisResultError"
                        ),
                        error_message=(
                            getattr(result, "error_message", None)
                            if result and not getattr(result, "success", True)
                            else ("LLM returned empty result" if result is None else None)
                        ),
                    )
                except Exception as exc:
                    record_llm_run(
                        success=False,
                        model=getattr(self.config, "litellm_model", None),
                        call_type="analysis",
                        duration_ms=int((time.monotonic() - llm_started_at) * 1000),
                        error_type=type(exc).__name__,
                        error_message=exc,
                    )
                    raise

            # Step 7.5: 填充分析时的价格信息到 result
            if result:
                self._emit_progress(94, f"{stock_name}：正在校验并整理分析结果")
                result.query_id = query_id
                realtime_data = enhanced_context.get('realtime', {})
                result.current_price = realtime_data.get('price')
                result.change_pct = realtime_data.get('change_pct')

            # Step 7.6: chip_structure fallback (Issue #589) and unavailable collapse
            if result:
                normalize_chip_structure_availability(result, chip_data)

            # Step 7.7: price_position fallback
            if result:
                fill_price_position_if_needed(result, trend_result, realtime_quote)
                action_source_advice = getattr(result, "operation_advice", None)
                stabilize_decision_with_structure(result, trend_result, fundamental_context)
                adjustments = apply_phase_decision_guardrails(
                    result,
                    market_phase_summary=market_phase_summary,
                    analysis_context_pack_overview=analysis_context_pack_overview,
                    report_language=getattr(result, "report_language", None)
                    or getattr(self.config, "report_language", "zh"),
                )
                if adjustments:
                    logger.info("[phase_decision_guardrail] Applied adjustments for %s: %s", code, adjustments)
                market_context_adjustments = apply_daily_market_context_guardrail(
                    result,
                    daily_market_context=enhanced_context.get("daily_market_context"),
                    report_language=getattr(result, "report_language", None)
                    or getattr(self.config, "report_language", "zh"),
                )
                if market_context_adjustments:
                    logger.info(
                        "[daily_market_context_guardrail] Applied adjustments for %s: %s",
                        code,
                        market_context_adjustments,
                    )
                if isinstance(fundamental_context, dict):
                    result.fundamental_context = fundamental_context
                if isinstance(market_structure_context, dict):
                    result.market_structure_context = market_structure_context
                result.market_phase_summary = market_phase_summary
                result.analysis_context_pack_overview = analysis_context_pack_overview
                self._refresh_decision_action_for_final_result(
                    result,
                    report_type=report_type.value,
                    previous_operation_advice=action_source_advice,
                )
                self._attach_factor_decision_summary(
                    result,
                    code=code,
                    trend_result=canonical_trend_result,
                    fundamental_context=fundamental_context,
                    chip_data=chip_data,
                    daily_market_context=daily_market_context,
                    market_structure_context=market_structure_context,
                    relative_strength_context=relative_strength_context,
                    supply_demand_context=supply_demand_context,
                    cost_structure_context=cost_structure_context,
                    price_structure_context=price_structure_context,
                    volatility_momentum_context=volatility_momentum_context,
                    pattern_trigger_context=pattern_trigger_context,
                    multi_timeframe_structure_context=multi_timeframe_structure_context,
                    method_execution_receipts=method_execution_receipts,
                )
                if b1_no_send:
                    # Direct analyze_stock/process_single_stock callers cannot
                    # bypass the same 1d+5m Registry Product admission enforced
                    # by run() before the original report is exposed.
                    self._assert_b1_results_product_admitted(
                        [result], [code],
                    )
                self._attach_research_delivery_state(
                    result,
                    query_id=query_id,
                )
                self._promote_p0_deterministic_result_after_explanation_failure(
                    result,
                    code=code,
                )

            # Step 8: 保存分析历史记录
            if result and result.success and not b1_no_send:
                try:
                    self._emit_progress(97, f"{stock_name}：正在保存分析报告")
                    context_snapshot = self._build_context_snapshot(
                        enhanced_context=enhanced_context,
                        news_content=news_context,
                        news_result_count=news_result_count,
                        realtime_quote=realtime_quote,
                        chip_data=chip_data,
                        analysis_context_pack_overview=analysis_context_pack_overview,
                        market_phase_summary=market_phase_summary,
                    )
                    context_snapshot["research_decision_time_utc"] = datetime.now(
                        timezone.utc
                    ).isoformat()
                    result.diagnostic_context_snapshot = context_snapshot
                    recording_intent = self._compile_learning_recording_intent(
                        result=result,
                        query_id=query_id,
                        report_type=report_type.value,
                    )
                    saved_history_id = self.db.save_analysis_history(
                        result=result,
                        query_id=query_id,
                        report_type=report_type.value,
                        news_content=news_context,
                        context_snapshot=context_snapshot,
                        save_snapshot=self.save_context_snapshot,
                        recording_intent=recording_intent,
                    )
                    valid_saved_history_id = (
                        isinstance(saved_history_id, int)
                        and not isinstance(saved_history_id, bool)
                        and saved_history_id > 0
                    )
                    record_history_run(
                        report_saved=bool(saved_history_id),
                        metadata_saved=bool(saved_history_id),
                        analysis_history_id=(
                            saved_history_id if valid_saved_history_id else None
                        ),
                    )
                    if valid_saved_history_id:
                        decision_signal_result = self._extract_decision_signal_after_history_save(
                            result=result,
                            query_id=query_id,
                            source_report_id=saved_history_id,
                            report_type=report_type.value,
                            context_snapshot=context_snapshot,
                            portfolio_context=portfolio_context,
                        )
                        self._persist_prediction_ledger_after_history_save(
                            result=result,
                            analysis_history_id=saved_history_id,
                            decision_signal=decision_signal_result,
                        )
                except Exception as e:
                    record_history_run(
                        report_saved=False,
                        metadata_saved=False,
                        error_message=e,
                    )
                    logger.warning(f"{stock_name}({code}) 保存分析历史失败: {e}")

            return result

        except Exception as e:
            logger.error(f"{stock_name}({code}) 分析失败: {e}")
            logger.exception(f"{stock_name}({code}) 详细错误信息:")
            return None
    
    def _enhance_context(
        self,
        context: Dict[str, Any],
        realtime_quote,
        chip_data: Optional[ChipDistribution],
        trend_result: Optional[TrendAnalysisResult],
        stock_name: str = "",
        fundamental_context: Optional[Dict[str, Any]] = None,
        market_phase_context: Optional[Dict[str, Any]] = None,
        portfolio_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        增强分析上下文
        
        将实时行情、筹码分布、趋势分析结果、股票名称添加到上下文中
        
        Args:
            context: 原始上下文
            realtime_quote: 实时行情数据（UnifiedRealtimeQuote 或 None）
            chip_data: 筹码分布数据
            trend_result: 趋势分析结果
            stock_name: 股票名称
            market_phase_context: 已构建的市场阶段上下文，用于标记盘中 partial bar
            
        Returns:
            增强后的上下文
        """
        enhanced = context.copy()
        enhanced["report_language"] = normalize_report_language(getattr(self.config, "report_language", "zh"))
        
        # 添加股票名称
        if stock_name:
            enhanced['stock_name'] = stock_name
        elif realtime_quote and getattr(realtime_quote, 'name', None):
            enhanced['stock_name'] = realtime_quote.name
        if isinstance(portfolio_context, dict):
            enhanced["portfolio_context"] = dict(portfolio_context)

        # 将运行时搜索窗口透传给 analyzer，避免与全局配置重新读取产生窗口不一致
        enhanced['news_window_days'] = getattr(self.search_service, "news_window_days", 3)
        
        # 添加实时行情（兼容不同数据源的字段差异）
        if realtime_quote:
            # 使用 getattr 安全获取字段，缺失字段返回 None 或默认值
            volume_ratio = getattr(realtime_quote, 'volume_ratio', None)
            quote_source = getattr(realtime_quote, 'source', None)
            quote_source_name = getattr(quote_source, 'value', quote_source)
            quote_source_name = str(quote_source_name) if quote_source_name is not None else None
            enhanced['realtime'] = {
                'name': getattr(realtime_quote, 'name', ''),
                'price': getattr(realtime_quote, 'price', None),
                'change_pct': getattr(realtime_quote, 'change_pct', None),
                'volume_ratio': volume_ratio,
                'volume_ratio_desc': self._describe_volume_ratio(volume_ratio) if volume_ratio else '无数据',
                'turnover_rate': getattr(realtime_quote, 'turnover_rate', None),
                'pe_ratio': getattr(realtime_quote, 'pe_ratio', None),
                'pb_ratio': getattr(realtime_quote, 'pb_ratio', None),
                'total_mv': getattr(realtime_quote, 'total_mv', None),
                'circ_mv': getattr(realtime_quote, 'circ_mv', None),
                'change_60d': getattr(realtime_quote, 'change_60d', None),
                'source': quote_source_name,
                'fetched_at': getattr(realtime_quote, 'fetched_at', None),
                'provider_timestamp': getattr(realtime_quote, 'provider_timestamp', None),
                'is_stale': getattr(realtime_quote, 'is_stale', None),
                'stale_seconds': getattr(realtime_quote, 'stale_seconds', None),
                'fallback_from': getattr(realtime_quote, 'fallback_from', None),
            }
            # 移除 None 值以减少上下文大小
            enhanced['realtime'] = {k: v for k, v in enhanced['realtime'].items() if v is not None}
        
        # 添加筹码分布
        if chip_data:
            current_price = getattr(realtime_quote, 'price', 0) if realtime_quote else 0
            enhanced['chip'] = {
                'profit_ratio': chip_data.profit_ratio,
                'avg_cost': chip_data.avg_cost,
                'concentration_90': chip_data.concentration_90,
                'concentration_70': chip_data.concentration_70,
                'chip_status': chip_data.get_chip_status(current_price or 0),
            }
        
        # 添加趋势分析结果
        if trend_result:
            enhanced['trend_analysis'] = {
                'trend_status': trend_result.trend_status.value,
                'ma_alignment': trend_result.ma_alignment,
                'trend_strength': trend_result.trend_strength,
                'bias_ma5': trend_result.bias_ma5,
                'bias_ma10': trend_result.bias_ma10,
                'volume_status': trend_result.volume_status.value,
                'volume_trend': trend_result.volume_trend,
                'buy_signal': trend_result.buy_signal.value,
                'signal_score': trend_result.signal_score,
                'signal_reasons': trend_result.signal_reasons,
                'risk_factors': trend_result.risk_factors,
            }

        # Issue #234：盘中分析使用实时 OHLC 与趋势 MA 覆盖 today。
        # 防护条件：trend_result.ma5 > 0 表示 MA 计算已成功且数据量充足。
        if realtime_quote and trend_result and trend_result.ma5 > 0:
            price = getattr(realtime_quote, 'price', None)
            if price is not None and price > 0:
                yesterday_close = None
                if enhanced.get('yesterday') and isinstance(enhanced['yesterday'], dict):
                    yesterday_close = enhanced['yesterday'].get('close')
                orig_today = enhanced.get('today') or {}
                market_today = get_market_now(
                    get_market_for_stock(normalize_stock_code(enhanced.get('code', '')))
                ).date().isoformat()
                source = getattr(realtime_quote, 'source', None)
                source_name = getattr(source, 'value', source)
                source_name = str(source_name) if source_name is not None else 'unknown'
                open_p = getattr(realtime_quote, 'open_price', None) or getattr(
                    realtime_quote, 'pre_close', None
                ) or yesterday_close or orig_today.get('open') or price
                high_p = getattr(realtime_quote, 'high', None) or price
                low_p = getattr(realtime_quote, 'low', None) or price
                vol = getattr(realtime_quote, 'volume', None)
                amt = getattr(realtime_quote, 'amount', None)
                pct = getattr(realtime_quote, 'change_pct', None)
                fetched_at = getattr(realtime_quote, 'fetched_at', None)
                provider_timestamp = getattr(realtime_quote, 'provider_timestamp', None)
                fallback_from = getattr(realtime_quote, 'fallback_from', None)
                realtime_today = {
                    'close': price,
                    'open': open_p,
                    'high': high_p,
                    'low': low_p,
                    'ma5': trend_result.ma5,
                    'ma10': trend_result.ma10,
                    'ma20': trend_result.ma20,
                    'date': market_today,
                    'data_source': f"realtime:{source_name}",
                    'realtime_source': source_name,
                    'is_estimated': True,
                }
                estimated_fields = [
                    'close', 'open', 'high', 'low', 'ma5', 'ma10', 'ma20',
                ]
                if vol is not None:
                    realtime_today['volume'] = vol
                    estimated_fields.append('volume')
                if amt is not None:
                    realtime_today['amount'] = amt
                    estimated_fields.append('amount')
                if pct is not None:
                    realtime_today['pct_chg'] = pct
                    estimated_fields.append('pct_chg')
                realtime_today['estimated_fields'] = estimated_fields
                if isinstance(market_phase_context, dict) and "is_partial_bar" in market_phase_context:
                    realtime_today['is_partial_bar'] = market_phase_context.get("is_partial_bar")
                if fetched_at is not None:
                    realtime_today['fetched_at'] = fetched_at
                if provider_timestamp is not None:
                    realtime_today['provider_timestamp'] = provider_timestamp
                if fallback_from is not None:
                    realtime_today['fallback_from'] = fallback_from
                realtime_owned_fields = {
                    'open', 'high', 'low', 'close',
                    'volume', 'amount', 'pct_chg', 'pctChg',
                    'date', 'data_source', 'dataSource', 'source',
                    'realtime_source', 'realtimeSource',
                    'is_partial_bar', 'isPartialBar', 'is_estimated',
                    'isEstimated', 'estimated_fields', 'estimatedFields',
                    'fetched_at', 'fetchedAt', 'provider_timestamp',
                    'providerTimestamp', 'fallback_from', 'fallbackFrom',
                }
                for k, v in orig_today.items():
                    if k not in realtime_today and k not in realtime_owned_fields and v is not None:
                        realtime_today[k] = v
                enhanced['today'] = realtime_today
                enhanced['ma_status'] = self._compute_ma_status(
                    price, trend_result.ma5, trend_result.ma10, trend_result.ma20
                )
                enhanced['date'] = market_today
                if yesterday_close is not None:
                    try:
                        yc = float(yesterday_close)
                        if yc > 0:
                            enhanced['price_change_ratio'] = round(
                                (price - yc) / yc * 100, 2
                            )
                    except (TypeError, ValueError):
                        pass
                if vol is not None and enhanced.get('yesterday'):
                    yest_vol = enhanced['yesterday'].get('volume') if isinstance(
                        enhanced['yesterday'], dict
                    ) else None
                    if yest_vol is not None:
                        try:
                            yv = float(yest_vol)
                            if yv > 0:
                                enhanced['volume_change_ratio'] = round(
                                    float(vol) / yv, 2
                                )
                        except (TypeError, ValueError):
                            pass

        # ETF/index flag for analyzer prompt (Fixes #274)
        enhanced['is_index_etf'] = SearchService.is_index_or_etf(
            context.get('code', ''), enhanced.get('stock_name', stock_name)
        )

        # P0: append unified fundamental block; keep as additional context only
        enhanced["fundamental_context"] = (
            fundamental_context
            if isinstance(fundamental_context, dict)
            else self.fetcher_manager.build_failed_fundamental_context(
                context.get("code", ""),
                "invalid fundamental context",
            )
        )

        return enhanced

    def _attach_belong_boards_to_fundamental_context(
        self,
        code: str,
        fundamental_context: Optional[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Attach A-share board membership as a top-level supplemental field.

        Keep this as a shallow copy so cached fundamental contexts are not
        mutated in place after retrieval.
        """
        if isinstance(fundamental_context, dict):
            enriched_context = dict(fundamental_context)
        else:
            enriched_context = self.fetcher_manager.build_failed_fundamental_context(
                code,
                "invalid fundamental context",
            )

        market = enriched_context.get("market")
        if not isinstance(market, str) or not market.strip():
            market = get_market_for_stock(normalize_stock_code(code))

        existing_boards = enriched_context.get("belong_boards")
        existing_board_list = list(existing_boards) if isinstance(existing_boards, list) else None
        if existing_board_list:
            enriched_context["belong_boards"] = existing_board_list
            self._attach_concept_rankings_to_fundamental_context(code, enriched_context, market)
            return enriched_context

        boards_block = enriched_context.get("boards")
        boards_status = boards_block.get("status") if isinstance(boards_block, dict) else None
        coverage = enriched_context.get("coverage")
        boards_coverage = coverage.get("boards") if isinstance(coverage, dict) else None

        # For HK/US: the offshore adapter already populates belong_boards from
        # yfinance sector/industry. Don't overwrite it (and we have no AkShare
        # 板块 endpoint for those markets anyway). Default to [] when callers
        # pass a minimal context without the key.
        if market != "cn":
            enriched_context["belong_boards"] = existing_board_list or []
            return enriched_context

        if boards_status == "not_supported" or boards_coverage == "not_supported":
            enriched_context["belong_boards"] = existing_board_list or []
            return enriched_context

        boards: List[Dict[str, Any]] = []
        try:
            raw_boards = self.fetcher_manager.get_belong_boards(code)
            if isinstance(raw_boards, list):
                boards = raw_boards
        except Exception as e:
            logger.debug("%s attach belong_boards failed (fail-open): %s", code, e)

        enriched_context["belong_boards"] = boards or existing_board_list or []
        self._attach_concept_rankings_to_fundamental_context(code, enriched_context, market)
        return enriched_context

    def _attach_concept_rankings_to_fundamental_context(
        self,
        code: str,
        enriched_context: Dict[str, Any],
        market: str,
    ) -> None:
        """Attach concept/theme rankings for A-share related-board signals."""
        if market != "cn" or isinstance(enriched_context.get("concept_boards"), dict):
            return

        top_concepts, bottom_concepts = self._get_concept_rankings_for_market(market)

        concept_data: Dict[str, Any] = {
            "top": top_concepts,
            "bottom": bottom_concepts,
        }
        if not top_concepts and not bottom_concepts:
            # Empty lists are removed while fundamental contexts are merged.
            # Keep a non-empty internal marker so downstream consumers can
            # distinguish an attempted empty result from a missing preload.
            concept_data["fetch_attempted"] = True
        enriched_context["concept_boards"] = {
            "status": "ok" if top_concepts and bottom_concepts else "partial",
            "data": concept_data,
        }

    def _get_concept_rankings_for_market(
        self,
        market: str,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Fetch market-wide concept rankings once per pipeline run."""
        if market != "cn" or self.p0_bounded_trial:
            return [], []

        service = getattr(self, "market_hotspot_service", None)
        if service is None:
            try:
                service = MarketHotspotService(fetcher_manager=self.fetcher_manager)
            except Exception as exc:
                logger.debug(
                    "market hotspot service init failed in concept ranking path (fail-open): %s",
                    exc,
                )
                service = None
            else:
                self.market_hotspot_service = service

        cache = getattr(self, "_concept_rankings_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._concept_rankings_cache = cache

        lock = getattr(self, "_concept_rankings_cache_lock", None)
        if lock is None:
            lock = threading.Lock()
            self._concept_rankings_cache_lock = lock

        with lock:
            if market in cache:
                top_concepts, bottom_concepts = cache[market]
                return list(top_concepts), list(bottom_concepts)

            top_concepts: List[Dict[str, Any]] = []
            bottom_concepts: List[Dict[str, Any]] = []
            try:
                if service is None:
                    fetch_rankings = getattr(self.fetcher_manager, "get_concept_rankings", None)
                    if callable(fetch_rankings):
                        rankings = fetch_rankings(5)
                        if isinstance(rankings, tuple) and len(rankings) == 2:
                            raw_top, raw_bottom = rankings
                            if isinstance(raw_top, list):
                                top_concepts = list(raw_top)
                            if isinstance(raw_bottom, list):
                                bottom_concepts = list(raw_bottom)
                else:
                    top_concepts, bottom_concepts = service.get_concept_rankings(5)
            except Exception as e:
                logger.debug("attach concept_rankings failed (fail-open): %s", e)

            cache[market] = (top_concepts, bottom_concepts)
            return list(top_concepts), list(bottom_concepts)

    def _build_market_structure_context(
        self,
        *,
        code: str,
        stock_name: str,
        market: str,
        fundamental_context: Optional[Dict[str, Any]],
        trade_date: Any = None,
        market_phase_summary: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Build market structure context without blocking the main analysis."""
        service = getattr(self, "market_structure_service", None)
        if service is None:
            try:
                service = MarketStructureService(fetcher_manager=self.fetcher_manager)
                self.market_structure_service = service
            except Exception as exc:
                logger.debug("market structure service init failed (fail-open): %s", exc)
                return None
        try:
            return service.build_context(
                code=code,
                stock_name=stock_name,
                market=market,
                fundamental_context=fundamental_context,
                trade_date=trade_date,
                market_phase_summary=market_phase_summary,
            )
        except Exception as exc:
            logger.debug(
                "%s market structure context build failed (fail-open): %s",
                code,
                exc,
                exc_info=True,
            )
            return None

    def _ensure_agent_history(self, code: str, min_days: int = 240) -> None:
        """Ensure at least *min_days* of K-line history is in DB for agent tools."""
        from src.services.history_loader import get_frozen_target_date

        target = get_frozen_target_date()
        if target is None:
            target = self._resolve_resume_target_date(code)
        start = target - timedelta(days=int(min_days * 1.8))
        bars = self.db.get_data_range(code, start, target)
        if bars and len(bars) >= min(min_days, 200):
            logger.debug("[%s] Agent history: %d bars in DB, sufficient", code, len(bars))
            return
        try:
            df, source = self.fetcher_manager.get_daily_data(code, days=min_days)
            if df is not None and not df.empty:
                self.db.save_daily_data(df, code, source)
                logger.info("[%s] Prefetched %d rows of history for agent (source: %s)", code, len(df), source)
        except Exception as e:
            logger.warning("[%s] Agent history prefetch failed: %s", code, e)

    def _analyze_with_agent(
        self, 
        code: str, 
        report_type: ReportType, 
        query_id: str,
        stock_name: str,
        realtime_quote: Any,
        chip_data: Optional[ChipDistribution],
        fundamental_context: Optional[Dict[str, Any]] = None,
        trend_result: Optional[TrendAnalysisResult] = None,
        *,
        market_phase_context: Optional[Dict[str, Any]] = None,
        market_phase_summary: Optional[Dict[str, Any]] = None,
        daily_market_context: Optional[DailyMarketContext] = None,
        portfolio_context: Optional[Dict[str, Any]] = None,
        market_structure_context: Optional[Dict[str, Any]] = None,
        canonical_trend_result: Optional[TrendAnalysisResult] = None,
        relative_strength_context: Optional[Dict[str, Any]] = None,
        supply_demand_context: Optional[Dict[str, Any]] = None,
        cost_structure_context: Optional[Dict[str, Any]] = None,
        price_structure_context: Optional[Dict[str, Any]] = None,
        volatility_momentum_context: Optional[Dict[str, Any]] = None,
        pattern_trigger_context: Optional[Dict[str, Any]] = None,
        multi_timeframe_structure_context: Optional[Dict[str, Any]] = None,
        method_execution_receipts: Optional[Dict[str, Any]] = None,
    ) -> Optional[AnalysisResult]:
        """
        使用 Agent 模式分析单只股票。
        """
        try:
            from src.agent.factory import build_agent_executor
            report_language = normalize_report_language(getattr(self.config, "report_language", "zh"))

            requested_skills = (
                self.analysis_skills
                if self.analysis_skills is not None
                else (getattr(self.config, 'agent_skills', None) or None)
            )
            # Build executor from shared factory (ToolRegistry and SkillManager prototype are cached)
            executor = build_agent_executor(self.config, requested_skills)

            # Build initial context to avoid redundant tool calls
            initial_context = {
                "stock_code": code,
                "stock_name": stock_name,
                "report_type": report_type.value,
                "report_language": report_language,
                "fundamental_context": fundamental_context,
            }
            if isinstance(portfolio_context, dict):
                initial_context["portfolio_context"] = dict(portfolio_context)
            if self.analysis_skills is not None:
                initial_context["skills"] = self.analysis_skills
            if market_phase_context is not None:
                initial_context["market_phase_context"] = market_phase_context
            if isinstance(market_structure_context, dict):
                initial_context["market_structure_context"] = market_structure_context
            self._attach_daily_market_context(
                initial_context,
                daily_market_context,
                report_language=report_language,
            )
            
            if realtime_quote:
                initial_context["realtime_quote"] = self._safe_to_dict(realtime_quote)
            if chip_data:
                initial_context["chip_distribution"] = self._safe_to_dict(chip_data)
            if trend_result:
                initial_context["trend_result"] = self._safe_to_dict(trend_result)

            # Agent path: inject social sentiment as news_context so both
            # executor (_build_user_message) and orchestrator (ctx.set_data)
            # can consume it through the existing news_context channel
            if self.social_sentiment_service is not None and self.social_sentiment_service.is_available and is_us_stock_code(code):
                try:
                    social_context = self.social_sentiment_service.get_social_context(code)
                    if social_context:
                        existing = initial_context.get("news_context")
                        if existing:
                            initial_context["news_context"] = existing + "\n\n" + social_context
                        else:
                            initial_context["news_context"] = social_context
                        logger.info(f"[{code}] Agent mode: social sentiment data injected into news_context")
                except Exception as e:
                    logger.warning(f"[{code}] Agent mode: social sentiment fetch failed: {e}")

            persisted_intelligence_context = self._load_persisted_intelligence_context(
                code=code,
                stock_name=stock_name,
                market=get_market_for_stock(normalize_stock_code(code)) or "cn",
            )
            if persisted_intelligence_context:
                existing = initial_context.get("news_context")
                initial_context["news_context"] = (
                    f"{existing}\n\n{persisted_intelligence_context}"
                    if existing
                    else persisted_intelligence_context
                )
                logger.info(f"[{code}] Agent mode: local intelligence evidence injected into news_context")

            # Issue #1066: ensure deep history is in DB before agent tools run
            self._ensure_agent_history(code)

            analysis_context = self._load_agent_analysis_context(code, stock_name)
            market = get_market_for_stock(normalize_stock_code(code))
            (
                analysis_context_pack_summary,
                analysis_context_pack_overview,
            ) = self._build_analysis_context_pack_outputs(
                self._build_agent_analysis_artifacts(
                    code=code,
                    stock_name=stock_name,
                    market=market,
                    phase=market_phase_context,
                    initial_context=initial_context,
                    fundamental_context=fundamental_context,
                    query_id=query_id,
                    base_context=analysis_context,
                    portfolio_context=portfolio_context,
                ),
                report_language=report_language,
                code=code,
                query_id=query_id,
            )
            if analysis_context_pack_summary:
                initial_context["analysis_context_pack_summary"] = analysis_context_pack_summary

            # 运行 Agent
            if report_language in ("en", "ko"):
                message = f"Analyze stock {code} ({stock_name}) and return the full decision dashboard JSON."
            else:
                message = f"请分析股票 {code} ({stock_name})，并生成决策仪表盘报告。"
            llm_started_at = time.monotonic()
            try:
                record_llm_run_started(
                    model=getattr(self.config, "agent_litellm_model", None),
                    call_type="agent_analysis",
                )
                agent_result = executor.run(message, context=initial_context)
            except Exception as exc:
                record_llm_run(
                    success=False,
                    model=getattr(self.config, "agent_litellm_model", None),
                    call_type="agent_analysis",
                    duration_ms=int((time.monotonic() - llm_started_at) * 1000),
                    error_type=type(exc).__name__,
                    error_message=exc,
                )
                raise

            # 转换为 AnalysisResult
            result = self._agent_result_to_analysis_result(
                agent_result,
                code,
                stock_name,
                report_type,
                query_id,
                trend_result=trend_result,
            )
            record_llm_run(
                success=bool(result and getattr(result, "success", True)),
                model=getattr(result, "model_used", None) if result else getattr(agent_result, "model", None),
                call_type="agent_analysis",
                duration_ms=int((time.monotonic() - llm_started_at) * 1000),
                error_type=(
                    None
                    if result and getattr(result, "success", True)
                    else "AgentResultError"
                ),
                error_message=(
                    getattr(result, "error_message", None)
                    if result and not getattr(result, "success", True)
                    else ("Agent returned empty result" if result is None else None)
                ),
            )
            if result:
                result.query_id = query_id
            # Agent weak integrity: placeholder fill only, no LLM retry
            if result and getattr(self.config, "report_integrity_enabled", False):
                from src.analyzer import check_content_integrity, apply_placeholder_fill

                pass_integrity, missing = check_content_integrity(
                    result,
                    require_phase_decision=isinstance(market_phase_summary, dict),
                )
                if not pass_integrity:
                    apply_placeholder_fill(result, missing)
                    logger.info(
                        "[LLM完整性] integrity_mode=agent_weak 必填字段缺失 %s，已占位补全",
                        missing,
                    )
            # chip_structure fallback (Issue #589), before save_analysis_history
            if result and chip_data is not None:
                normalize_chip_structure_availability(result, chip_data)

            # price_position fallback (same as non-agent path Step 7.7)
            if result:
                pipeline_adjustments: list[PipelineActionAdjustment] = []
                runtime_facts = getattr(agent_result, "runtime_facts", None)
                pipeline_start_signal = getattr(result, "decision_type", "hold")
                risk_application = (
                    getattr(runtime_facts, "risk_override_application", None)
                    if runtime_facts is not None
                    else None
                )
                if risk_application is not None:
                    pipeline_start_signal = risk_application.post_risk_signal.value
                initial_action_advice = getattr(result, "operation_advice", None)
                self._refresh_decision_action_for_final_result(
                    result,
                    report_type=report_type.value,
                    previous_operation_advice=initial_action_advice,
                )
                pipeline_start_action = normalize_decision_action(
                    getattr(result, "action", None)
                )
                action_chain_valid = pipeline_start_action is not None
                fill_price_position_if_needed(result, trend_result, realtime_quote)
                realtime_data = initial_context.get("realtime_quote", {})
                if isinstance(realtime_data, dict):
                    result.current_price = realtime_data.get("price")
                    result.change_pct = realtime_data.get("change_pct")
                action_before_guardrail = getattr(result, "action", None)
                advice_before_guardrail = getattr(result, "operation_advice", None)
                stabilize_decision_with_structure(result, trend_result, fundamental_context)
                self._refresh_decision_action_for_final_result(
                    result,
                    report_type=report_type.value,
                    previous_operation_advice=advice_before_guardrail,
                )
                action_after_guardrail = normalize_decision_action(
                    getattr(result, "action", None)
                )
                if action_chain_valid and action_after_guardrail is not None:
                    capture_pipeline_action_adjustment(
                        pipeline_adjustments,
                        source="structure_and_fundamentals",
                        before=action_before_guardrail,
                        after=action_after_guardrail,
                    )
                else:
                    action_chain_valid = False
                action_before_guardrail = getattr(result, "action", None)
                advice_before_guardrail = getattr(result, "operation_advice", None)
                adjustments = apply_phase_decision_guardrails(
                    result,
                    market_phase_summary=market_phase_summary,
                    analysis_context_pack_overview=analysis_context_pack_overview,
                    report_language=getattr(result, "report_language", None)
                    or getattr(self.config, "report_language", "zh"),
                )
                if adjustments:
                    logger.info("[phase_decision_guardrail] Applied agent adjustments for %s: %s", code, adjustments)
                self._refresh_decision_action_for_final_result(
                    result,
                    report_type=report_type.value,
                    previous_operation_advice=advice_before_guardrail,
                )
                action_after_guardrail = normalize_decision_action(
                    getattr(result, "action", None)
                )
                if action_chain_valid and action_after_guardrail is not None:
                    capture_pipeline_action_adjustment(
                        pipeline_adjustments,
                        source="market_phase",
                        before=action_before_guardrail,
                        after=action_after_guardrail,
                    )
                else:
                    action_chain_valid = False
                action_before_guardrail = getattr(result, "action", None)
                advice_before_guardrail = getattr(result, "operation_advice", None)
                market_context_adjustments = apply_daily_market_context_guardrail(
                    result,
                    daily_market_context=initial_context.get("daily_market_context"),
                    report_language=getattr(result, "report_language", None)
                    or getattr(self.config, "report_language", "zh"),
                )
                if market_context_adjustments:
                    logger.info(
                        "[daily_market_context_guardrail] Applied agent adjustments for %s: %s",
                        code,
                        market_context_adjustments,
                    )
                self._refresh_decision_action_for_final_result(
                    result,
                    report_type=report_type.value,
                    previous_operation_advice=advice_before_guardrail,
                )
                action_after_guardrail = normalize_decision_action(
                    getattr(result, "action", None)
                )
                if action_chain_valid and action_after_guardrail is not None:
                    capture_pipeline_action_adjustment(
                        pipeline_adjustments,
                        source="daily_market_context",
                        before=action_before_guardrail,
                        after=action_after_guardrail,
                    )
                else:
                    action_chain_valid = False
                if isinstance(fundamental_context, dict):
                    result.fundamental_context = fundamental_context
                if isinstance(market_structure_context, dict):
                    result.market_structure_context = market_structure_context
                result.market_phase_summary = market_phase_summary
                result.analysis_context_pack_overview = analysis_context_pack_overview
                final_action = normalize_decision_action(getattr(result, "action", None))
                if isinstance(result.dashboard, dict):
                    result.dashboard.pop("agent_disagreement_explanation", None)
                if (
                    runtime_facts is not None
                    and action_chain_valid
                    and pipeline_start_action is not None
                    and final_action is not None
                ):
                    if not isinstance(result.dashboard, dict):
                        result.dashboard = {}
                    result.dashboard["agent_disagreement_explanation"] = (
                        build_pipeline_final_explanation(
                            runtime_facts=runtime_facts,
                            pipeline_start_signal=pipeline_start_signal,
                            pipeline_start_action=pipeline_start_action,
                            final_action=final_action,
                            pipeline_adjustments=pipeline_adjustments,
                            data_quality=(
                                analysis_context_pack_overview.get("data_quality")
                                if isinstance(analysis_context_pack_overview, dict)
                                else None
                            ),
                        )
                    )
                self._attach_factor_decision_summary(
                    result,
                    code=code,
                    trend_result=canonical_trend_result,
                    fundamental_context=fundamental_context,
                    chip_data=chip_data,
                    daily_market_context=daily_market_context,
                    market_structure_context=market_structure_context,
                    relative_strength_context=relative_strength_context,
                    supply_demand_context=supply_demand_context,
                    cost_structure_context=cost_structure_context,
                    price_structure_context=price_structure_context,
                    volatility_momentum_context=volatility_momentum_context,
                    pattern_trigger_context=pattern_trigger_context,
                    multi_timeframe_structure_context=multi_timeframe_structure_context,
                    method_execution_receipts=method_execution_receipts,
                )

            resolved_stock_name = result.name if result and result.name else stock_name

            # 保存新闻情报到数据库（Agent 工具结果仅用于 LLM 上下文，未持久化，Fixes #396）
            # 使用 search_stock_news（与 Agent 工具调用逻辑一致），仅 1 次 API 调用，无额外延迟
            if self.search_service is not None and self.search_service.is_available:
                try:
                    news_response = self.search_service.search_stock_news(
                        stock_code=code,
                        stock_name=resolved_stock_name,
                        max_results=5
                    )
                    if news_response.success and news_response.results:
                        query_context = self._build_query_context(query_id=query_id)
                        self.db.save_news_intel(
                            code=code,
                            name=resolved_stock_name,
                            dimension="latest_news",
                            query=news_response.query,
                            response=news_response,
                            query_context=query_context
                        )
                        logger.info(f"[{code}] Agent 模式: 新闻情报已保存 {len(news_response.results)} 条")
                except Exception as e:
                    logger.warning(f"[{code}] Agent 模式保存新闻情报失败: {e}")

            # 保存分析历史记录
            if result and result.success:
                try:
                    agent_context_snapshot = self._build_context_snapshot(
                        enhanced_context={
                            **self._without_runtime_prompt_context(initial_context),
                            "stock_name": resolved_stock_name,
                        },
                        news_content=initial_context.get("news_context"),
                        realtime_quote=realtime_quote,
                        chip_data=chip_data,
                        analysis_context_pack_overview=analysis_context_pack_overview,
                        market_phase_summary=market_phase_summary,
                    )
                    agent_context_snapshot["research_decision_time_utc"] = datetime.now(
                        timezone.utc
                    ).isoformat()
                    result.diagnostic_context_snapshot = agent_context_snapshot
                    agent_context_snapshot["stock_name"] = resolved_stock_name
                    recording_intent = self._compile_learning_recording_intent(
                        result=result,
                        query_id=query_id,
                        report_type=report_type.value,
                    )
                    saved_history_id = self.db.save_analysis_history(
                        result=result,
                        query_id=query_id,
                        report_type=report_type.value,
                        news_content=None,
                        context_snapshot=agent_context_snapshot,
                        save_snapshot=self.save_context_snapshot,
                        recording_intent=recording_intent,
                    )
                    valid_saved_history_id = (
                        isinstance(saved_history_id, int)
                        and not isinstance(saved_history_id, bool)
                        and saved_history_id > 0
                    )
                    record_history_run(
                        report_saved=bool(saved_history_id),
                        metadata_saved=bool(saved_history_id),
                        analysis_history_id=(
                            saved_history_id if valid_saved_history_id else None
                        ),
                    )
                    if valid_saved_history_id:
                        self._persist_skill_opinion_samples_after_history_save(
                            runtime_facts=getattr(agent_result, "runtime_facts", None),
                            analysis_history_id=saved_history_id,
                            stock_code=code,
                            analysis_context_pack_overview=analysis_context_pack_overview,
                        )
                        decision_signal_result = self._extract_decision_signal_after_history_save(
                            result=result,
                            query_id=query_id,
                            source_report_id=saved_history_id,
                            report_type=report_type.value,
                            context_snapshot=agent_context_snapshot,
                            portfolio_context=portfolio_context,
                        )
                        self._persist_prediction_ledger_after_history_save(
                            result=result,
                            analysis_history_id=saved_history_id,
                            decision_signal=decision_signal_result,
                        )
                    latest_diagnostic_snapshot = current_diagnostic_snapshot()
                    if latest_diagnostic_snapshot is not None:
                        agent_context_snapshot["diagnostics"] = latest_diagnostic_snapshot
                        result.diagnostic_context_snapshot = agent_context_snapshot
                except Exception as e:
                    record_history_run(
                        report_saved=False,
                        metadata_saved=False,
                        error_message=e,
                    )
                    logger.warning(f"[{code}] 保存 Agent 分析历史失败: {e}")

            return result

        except Exception as e:
            logger.error(f"[{code}] Agent 分析失败: {e}")
            logger.exception(f"[{code}] Agent 详细错误信息:")
            return None

    def _load_agent_analysis_context(self, code: str, stock_name: str) -> Dict[str, Any]:
        """Load daily-bar context for Agent pack summaries without blocking analysis."""
        try:
            context = self._get_analysis_context_with_market_fallback(code)
        except Exception as exc:
            logger.warning(
                "[%s] Agent analysis context load failed; daily_bars will be marked missing: %s",
                code,
                exc,
            )
            context = None

        if isinstance(context, dict) and context:
            enriched = dict(context)
            enriched.setdefault("code", code)
            if stock_name:
                enriched.setdefault("stock_name", stock_name)
            return enriched

        return {
            "code": code,
            "stock_name": stock_name,
            "data_missing": True,
            "today": {},
            "yesterday": {},
        }

    def _get_analysis_context_with_market_fallback(self, code: str) -> Optional[Dict[str, Any]]:
        """Load analysis context, fetching JP/KR/TW daily bars when DB has no context."""
        context = self.db.get_analysis_context(code)
        if isinstance(context, dict) and context:
            return context

        market = get_market_for_stock(normalize_stock_code(code))
        if market not in {"jp", "kr", "tw"}:
            return context

        try:
            df, source_name = self.fetcher_manager.get_daily_data(code, days=60)
        except Exception as exc:
            logger.warning("[%s] JP/KR daily fallback fetch failed: %s", code, exc)
            return context

        if df is None or df.empty:
            logger.warning("[%s] JP/KR daily fallback returned empty data", code)
            return context

        try:
            self.db.save_daily_data(df, code, source_name)
            refreshed = self.db.get_analysis_context(code)
            if isinstance(refreshed, dict) and refreshed:
                return refreshed
        except Exception as exc:
            logger.warning("[%s] JP/KR daily fallback persistence failed: %s", code, exc)

        return self._build_analysis_context_from_daily_df(code, df)

    def _build_analysis_context_from_daily_df(self, code: str, df: pd.DataFrame) -> Optional[Dict[str, Any]]:
        if df is None or df.empty:
            return None

        frame = df.copy()
        frame.columns = [str(column).lower() for column in frame.columns]
        if "date" in frame.columns:
            frame = frame.sort_values("date")
        frame = frame.tail(2)
        rows = frame.to_dict(orient="records")
        if not rows:
            return None

        def normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
            normalized: Dict[str, Any] = {"code": row.get("code") or code}
            for key in ("open", "high", "low", "close", "volume", "amount", "pct_chg", "ma5", "ma10", "ma20", "volume_ratio"):
                value = row.get(key)
                if pd.notna(value):
                    normalized[key] = float(value)
            row_date = row.get("date")
            if hasattr(row_date, "date"):
                row_date = row_date.date()
            normalized["date"] = row_date.isoformat() if hasattr(row_date, "isoformat") else str(row_date)
            return normalized

        today = normalize_row(rows[-1])
        context: Dict[str, Any] = {
            "code": code,
            "date": today.get("date"),
            "today": today,
        }
        if len(rows) > 1:
            yesterday = normalize_row(rows[-2])
            context["yesterday"] = yesterday
            yesterday_volume = yesterday.get("volume")
            if yesterday_volume:
                context["volume_change_ratio"] = round(float(today.get("volume", 0)) / float(yesterday_volume), 2)
            yesterday_close = yesterday.get("close")
            if yesterday_close:
                context["price_change_ratio"] = round(
                    (float(today.get("close", 0)) - float(yesterday_close)) / float(yesterday_close) * 100,
                    2,
                )
            if isinstance(getattr(self, "b1_rights_context", None), Mapping):
                # B1 NO_SEND has no business DB: reuse the identical native
                # MA logic without initializing storage or a second indicator.
                context["ma_status"] = self._compute_ma_status(
                    float(today.get("close", 0) or 0),
                    float(today.get("ma5", 0) or 0),
                    float(today.get("ma10", 0) or 0),
                    float(today.get("ma20", 0) or 0),
                )
            else:
                # Preserve the original non-B1 DB-owned calculation path.
                context["ma_status"] = self.db._analyze_ma_status(
                    SimpleNamespace(**today)
                )

        return context

    def _load_daily_market_context(
        self,
        market: str,
        *,
        force_refresh: bool = False,
        target_date: Optional[date] = None,
    ) -> Optional[DailyMarketContext]:
        """Load/generate today's market context when market review is explicitly enabled."""
        if getattr(self, "daily_market_context_enabled", True) is not True:
            return None
        if getattr(self.config, "daily_market_context_enabled", True) is not True:
            return None
        if getattr(self.config, "market_review_enabled", None) is not True:
            return None

        try:
            service = getattr(self, "_daily_market_context_service", None)
            if service is None:
                service_lock = self._get_daily_market_context_service_lock()
                with service_lock:
                    service = getattr(self, "_daily_market_context_service", None)
                    if service is None:
                        service = DailyMarketContextService(db_manager=self.db)
                        self._daily_market_context_service = service
            get_context_kwargs = {
                "region": market,
                "config": self.config,
                "notifier": self.notifier,
                "analyzer": self.analyzer,
                "search_service": self.search_service,
                "force_refresh": force_refresh,
                "allow_generate": getattr(self, "daily_market_context_allow_generate", True),
                "target_date": target_date,
            }
            current_query_id = getattr(self, "query_id", None)
            if isinstance(current_query_id, str) and current_query_id.strip():
                get_context_kwargs["current_query_id"] = current_query_id
            return service.get_context(**get_context_kwargs)
        except Exception as exc:
            logger.warning("加载大盘环境上下文失败，个股分析继续: %s", exc, exc_info=True)
            return None

    def _get_daily_market_context_service_lock(self) -> threading.Lock:
        service_lock = getattr(self, "_daily_market_context_service_lock", None)
        if service_lock is not None:
            return service_lock
        with _DAILY_MARKET_CONTEXT_SERVICE_LOCK_INIT_GUARD:
            service_lock = getattr(self, "_daily_market_context_service_lock", None)
            if service_lock is None:
                service_lock = threading.Lock()
                self._daily_market_context_service_lock = service_lock
            return service_lock

    @staticmethod
    def _coerce_daily_market_context_date(value: Any) -> Optional[date]:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str):
            try:
                return date.fromisoformat(value[:10])
            except ValueError:
                return None
        return None

    @staticmethod
    def _attach_daily_market_context(
        target_context: Dict[str, Any],
        daily_market_context: Optional[DailyMarketContext],
        *,
        report_language: str,
    ) -> None:
        """Attach only the safe daily market summary to runtime analysis context."""
        if daily_market_context is None:
            return
        safe_context = daily_market_context.to_safe_dict()
        prompt_section = format_daily_market_context_prompt_section(
            safe_context,
            report_language=report_language,
        )
        if not prompt_section:
            return
        target_context["daily_market_context"] = safe_context
        target_context["daily_market_context_summary"] = prompt_section

    def _agent_result_to_analysis_result(
        self,
        agent_result,
        code: str,
        stock_name: str,
        report_type: ReportType,
        query_id: str,
        trend_result: Optional[TrendAnalysisResult] = None,
    ) -> AnalysisResult:
        """
        将 AgentResult 转换为 AnalysisResult。
        """
        report_language = normalize_report_language(getattr(self.config, "report_language", "zh"))
        dash = None
        result = AnalysisResult(
            code=code,
            name=stock_name,
            sentiment_score=50,
            trend_prediction=get_unknown_text(report_language),
            operation_advice=localize_operation_advice("观望", report_language),
            confidence_level=localize_confidence_level("medium", report_language),
            report_language=report_language,
            success=agent_result.success,
            error_message=agent_result.error or None,
            data_sources=f"agent:{agent_result.provider}",
            model_used=agent_result.model or None,
        )

        if agent_result.success and agent_result.dashboard:
            dash = agent_result.dashboard
            ai_stock_name = str(dash.get("stock_name", "")).strip()
            if ai_stock_name and self._is_placeholder_stock_name(stock_name, code):
                result.name = ai_stock_name

            nested_dashboard = dash.get("dashboard") if isinstance(dash, dict) else None

            raw_score = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "sentiment_score",
                scalar=True,
            )
            if self._is_agent_field_missing(raw_score, scalar=True):
                fallback_score = self._trend_score_fallback(trend_result)
                if fallback_score is not None:
                    result.sentiment_score = fallback_score
                    self._mark_trend_fallback_source(result)
            else:
                result.sentiment_score = self._safe_int(raw_score, 50)

            raw_trend = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "trend_prediction",
                scalar=True,
                expect_text=True,
            )
            if self._is_agent_field_missing(raw_trend, scalar=True, expect_text=True):
                trend_label = self._trend_label_fallback(
                    trend_result,
                    report_language,
                )
                if trend_label:
                    result.trend_prediction = trend_label
                    self._mark_trend_fallback_source(result)
            else:
                result.trend_prediction = str(raw_trend)

            raw_advice = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "operation_advice",
                scalar=True,
                allow_dict=True,
                expect_text=True,
            )
            extracted_advice = ""
            if isinstance(raw_advice, dict):
                # LLM may return {"no_position": "...", "has_position": "..."}
                extracted_advice = self._extract_advice_text_from_dict(raw_advice)
                if extracted_advice:
                    result.operation_advice = localize_operation_advice(
                        extracted_advice,
                        report_language,
                    )
                else:
                    signal_label = self._trend_signal_fallback(
                        trend_result,
                        report_language,
                    )
                    if signal_label:
                        result.operation_advice = signal_label
                        self._mark_trend_fallback_source(result)
            elif not self._is_agent_field_missing(
                raw_advice,
                scalar=True,
                allow_dict=True,
                expect_text=True,
            ):
                result.operation_advice = str(raw_advice) if raw_advice else (localize_operation_advice("观望", report_language))
            else:
                signal_label = self._trend_signal_fallback(trend_result, report_language)
                if signal_label:
                    result.operation_advice = signal_label
                    self._mark_trend_fallback_source(result)
            from src.agent.protocols import normalize_decision_signal

            raw_decision = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "decision_type",
                scalar=True,
                expect_text=True,
            )
            if self._is_agent_field_missing(raw_decision, scalar=True, expect_text=True):
                trend_decision = self._trend_decision_fallback(trend_result)
                decision_from_advice = infer_decision_type_from_advice(
                    result.operation_advice,
                    default="",
                )
                if decision_from_advice:
                    result.decision_type = decision_from_advice
                    if (
                        self._is_agent_field_missing(
                            raw_advice,
                            scalar=True,
                            allow_dict=True,
                            expect_text=True,
                        )
                        and not extracted_advice
                        and trend_decision
                    ):
                        self._mark_trend_fallback_source(result)
                else:
                    result.decision_type = trend_decision or "hold"
                    if trend_decision:
                        self._mark_trend_fallback_source(result)
            else:
                result.decision_type = normalize_decision_signal(raw_decision)
            result.confidence_level = localize_confidence_level(
                self._agent_dashboard_value(dash, nested_dashboard, "confidence_level")
                or result.confidence_level,
                report_language,
            )
            raw_summary = self._agent_dashboard_value(
                dash,
                nested_dashboard,
                "analysis_summary",
                scalar=True,
                expect_text=True,
            )
            if not self._is_agent_field_missing(raw_summary, scalar=True, expect_text=True):
                result.analysis_summary = str(raw_summary)
            else:
                result.analysis_summary = self._summary_fallback_from_result(result, report_language)
            top_level_phase_decision = dash.get("phase_decision") if isinstance(dash, dict) else None
            if isinstance(nested_dashboard, dict) and isinstance(top_level_phase_decision, dict):
                nested_dashboard = dict(nested_dashboard)
                nested_dashboard.setdefault("phase_decision", top_level_phase_decision)

            # The AI returns a top-level dict that contains a nested 'dashboard' sub-key
            # with core_conclusion / battle_plan / intelligence.  AnalysisResult's helper
            # methods (get_sniper_points, get_core_conclusion, etc.) expect that inner
            # structure, so we unwrap it here.
            result.dashboard = nested_dashboard or dash
            self._backfill_agent_dashboard_fields(result, trend_result, report_language)
        else:
            self._apply_trend_fallback(result, trend_result, report_language)
            if trend_result is not None:
                result.analysis_summary = (
                    result.analysis_summary
                    or self._summary_fallback_from_result(result, report_language)
                )
                self._backfill_agent_dashboard_fields(result, trend_result, report_language)
            if not result.error_message:
                result.error_message = (
                    "Agent failed to generate a valid decision dashboard" if report_language == "en"
                    else "에이전트가 유효한 결정 대시보드를 생성하지 못했습니다" if report_language == "ko"
                    else "Agent 未能生成有效的决策仪表盘"
                )

        explicit_action = dash.get("action") if isinstance(dash, dict) else None
        if explicit_action is None and isinstance(getattr(result, "dashboard", None), dict):
            explicit_action = result.dashboard.get("action")
        return populate_decision_action_fields(result, explicit_action=explicit_action)

    @staticmethod
    def _compile_business_valuation_context(
        fundamental_context: Optional[Dict[str, Any]],
        *,
        target_date: date,
    ) -> Optional[Dict[str, Any]]:
        """Invoke dated quality and business-valuation owners before receipts."""
        if not isinstance(fundamental_context, dict):
            return fundamental_context
        result = dict(fundamental_context)
        quality_request = result.get("quality_growth_input")
        if (
            isinstance(quality_request, dict)
            and not isinstance(result.get("fundamental_quality"), dict)
        ):
            result["fundamental_quality"] = compile_fundamental_quality(
                quality_request,
                as_of=target_date,
            )
        request = result.get("business_valuation_input")
        if (
            isinstance(request, dict)
            and not isinstance(result.get("business_valuation"), dict)
        ):
            # The Pipeline's frozen research date is the authoritative known-at
            # ceiling.  A provider/request payload may carry a later wall-clock
            # as_of for provenance, but it must never move valuation beyond the
            # completed research snapshot and silently admit future disclosures.
            result["business_valuation"] = compile_business_valuation(
                request,
                as_of=target_date,
            )
        return result

    @staticmethod
    def _build_direct_method_execution_receipts(
        *,
        code: str,
        market: Optional[str],
        asset_route: str,
        target_date: date,
        completed_history_identity: Dict[str, Any],
        chip_data: Any,
        canonical_trend_result: Optional[TrendAnalysisResult],
        supply_demand_context: Optional[Dict[str, Any]],
        cost_structure_context: Optional[Dict[str, Any]],
        price_structure_context: Optional[Dict[str, Any]],
        volatility_momentum_context: Optional[Dict[str, Any]],
        pattern_trigger_context: Optional[Dict[str, Any]],
        multi_timeframe_structure_context: Optional[Dict[str, Any]],
        fundamental_context: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Bind only producer outputs that actually returned from Pipeline calls."""

        def optional_hash(value: Any) -> Optional[str]:
            """Hash deterministic parent payloads; opaque/mock objects stay unbound."""
            if value is None:
                return None
            payload = value
            if not isinstance(payload, (dict, list, tuple, str, int, float, bool)):
                payload = StockAnalysisPipeline._safe_to_dict(value)
            if payload is None or not isinstance(
                payload, (dict, list, tuple, str, int, float, bool)
            ):
                return None
            try:
                return sha256_payload(payload)
            except (TypeError, ValueError, RecursionError):
                return None

        chip_hash = optional_hash(chip_data)
        trend_hash = optional_hash(
            canonical_trend_result.to_dict()
            if canonical_trend_result is not None
            and hasattr(canonical_trend_result, "to_dict")
            else None
        )
        price_structure_hash = optional_hash(price_structure_context)
        supply_demand_hash = optional_hash(supply_demand_context)
        common = {
            "asset_route": asset_route,
            "stock_code": code,
            "market": market,
            "target_date": target_date,
            "input_identity": completed_history_identity,
        }
        receipts: Dict[str, Dict[str, Any]] = {}
        if isinstance(supply_demand_context, dict):
            receipts["SUPPLY"] = build_method_execution_receipt(
                "SUPPLY", output=supply_demand_context, timeframe="daily", **common,
            )
        if isinstance(cost_structure_context, dict):
            receipts["COST"] = build_method_execution_receipt(
                "COST",
                output=cost_structure_context,
                timeframe="daily",
                upstream_hashes={
                    **({"chip_data": chip_hash} if chip_hash else {}),
                },
                **common,
            )
        if isinstance(price_structure_context, dict):
            receipts["STRUCTURE"] = build_method_execution_receipt(
                "STRUCTURE", output=price_structure_context, timeframe="daily", **common,
            )
        if isinstance(volatility_momentum_context, dict):
            receipts["MOMENTUM"] = build_method_execution_receipt(
                "MOMENTUM",
                output=volatility_momentum_context,
                timeframe="daily",
                upstream_hashes={
                    **({"trend_result": trend_hash} if trend_hash else {}),
                    **(
                        {"price_structure_context": price_structure_hash}
                        if price_structure_hash
                        else {}
                    ),
                },
                **common,
            )
        if isinstance(pattern_trigger_context, dict):
            receipts["PATTERN"] = build_method_execution_receipt(
                "PATTERN",
                output=pattern_trigger_context,
                timeframe="daily",
                upstream_hashes={
                    **(
                        {"price_structure_context": price_structure_hash}
                        if price_structure_hash
                        else {}
                    ),
                    **(
                        {"supply_demand_context": supply_demand_hash}
                        if supply_demand_hash
                        else {}
                    ),
                },
                **common,
            )
            candlestick_context = pattern_trigger_context.get("candlestick")
            if isinstance(candlestick_context, dict):
                receipts["CANDLESTICK"] = build_method_execution_receipt(
                    "CANDLESTICK",
                    output=candlestick_context,
                    timeframe="daily",
                    upstream_hashes={
                        **(
                            {"price_structure_context": price_structure_hash}
                            if price_structure_hash
                            else {}
                        ),
                        **(
                            {"supply_demand_context": supply_demand_hash}
                            if supply_demand_hash
                            else {}
                        ),
                    },
                    **common,
                )
        quality = (
            fundamental_context.get("fundamental_quality")
            if isinstance(fundamental_context, dict) else None
        )
        if isinstance(quality, dict) and quality.get("status") == "READY":
            quality_admission_hashes = tuple(
                str(value) for value in quality.get("data_admission_hashes") or ()
            )
            receipts["QUALITY"] = build_method_execution_receipt(
                "QUALITY",
                output=quality,
                timeframe="asset",
                input_identity={
                    "quality_receipt_hash": quality.get("receipt_hash"),
                    "data_admission_hashes": quality_admission_hashes,
                    "asset_id": quality.get("asset_id"),
                    "as_of": quality.get("as_of"),
                },
                upstream_hashes={
                    "data_admission_hashes": quality_admission_hashes,
                },
                asset_route=asset_route,
                stock_code=code,
                market=market,
                target_date=target_date,
            )

        valuation = (
            fundamental_context.get("business_valuation")
            if isinstance(fundamental_context, dict) else None
        )
        if isinstance(valuation, dict) and valuation.get("status") == "READY":
            admission_hashes = tuple(
                str(value) for value in valuation.get("data_admission_hashes") or ()
            )
            receipts["VALUATION"] = build_method_execution_receipt(
                "VALUATION",
                output=valuation,
                timeframe="asset",
                input_identity={
                    "valuation_receipt_hash": valuation.get("receipt_hash"),
                    "data_admission_hashes": admission_hashes,
                    "route_id": valuation.get("route_id"),
                    "asset_id": valuation.get("asset_id"),
                    "as_of": valuation.get("as_of"),
                },
                upstream_hashes={"data_admission_hashes": admission_hashes},
                asset_route=asset_route,
                stock_code=code,
                market=market,
                target_date=target_date,
            )

        if isinstance(multi_timeframe_structure_context, dict):
            receipts["MTF"] = build_method_execution_receipt(
                "MTF",
                output=multi_timeframe_structure_context,
                timeframe="multi",
                upstream_hashes={
                    **({"trend_result": trend_hash} if trend_hash else {}),
                    **(
                        {"price_structure_context": price_structure_hash}
                        if price_structure_hash
                        else {}
                    ),
                },
                **common,
            )
        return receipts

    def _attach_factor_decision_summary(
        self,
        result: AnalysisResult,
        *,
        code: str,
        trend_result: Optional[TrendAnalysisResult],
        fundamental_context: Optional[Dict[str, Any]],
        chip_data: Optional[ChipDistribution],
        daily_market_context: Optional[DailyMarketContext] = None,
        market_structure_context: Optional[Dict[str, Any]] = None,
        relative_strength_context: Optional[Dict[str, Any]] = None,
        supply_demand_context: Optional[Dict[str, Any]] = None,
        cost_structure_context: Optional[Dict[str, Any]] = None,
        price_structure_context: Optional[Dict[str, Any]] = None,
        volatility_momentum_context: Optional[Dict[str, Any]] = None,
        pattern_trigger_context: Optional[Dict[str, Any]] = None,
        multi_timeframe_structure_context: Optional[Dict[str, Any]] = None,
        method_execution_receipts: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Attach one asset-aware factor summary and finalize canonical public actions."""
        is_index_or_etf = SearchService.is_index_or_etf(
            code, getattr(result, "name", "")
        )
        normalized_code = str(code or "").strip().split(".")[0]
        asset_admission = resolve_cn_price_asset(code)
        is_market_index = (
            is_us_index_code(normalized_code)
            or asset_admission["asset_route"] == "MARKET"
        )
        if is_market_index:
            if self.p0_bounded_trial:
                raise P0BoundedTrialError(f"P0 rejects ETF or index result: {code}")
            return
        if is_index_or_etf and self.p0_bounded_trial:
            raise P0BoundedTrialError(f"P0 rejects ETF or index result: {code}")
        asset_type = (
            asset_admission["asset_route"].lower() if asset_admission["market"] == "cn"
            else ("etf" if is_index_or_etf else "stock")
        )
        if asset_type not in {"stock", "etf"}:
            return
        # Missing deterministic trend evidence must still finalize an explicit
        # UNKNOWN/watch canonical state; never preserve a stale legacy BUY/SELL.
        report_language = normalize_report_language(
            getattr(result, "report_language", None)
            or getattr(self.config, "report_language", "zh")
        )
        # First slice is intentionally Chinese-only; other report languages keep
        # their existing output instead of receiving mixed-language content.
        if report_language != "zh":
            if self.p0_bounded_trial:
                raise P0BoundedTrialError("P0 canonical report requires Chinese output")
            return
        try:
            summary = build_stock_factor_decision_summary(
                trend_result,
                fundamental_context=fundamental_context,
                chip_data=chip_data,
                daily_market_context=daily_market_context,
                market_structure_context=market_structure_context,
                relative_strength_context=relative_strength_context,
                supply_demand_context=supply_demand_context,
                cost_structure_context=cost_structure_context,
                price_structure_context=price_structure_context,
                volatility_momentum_context=volatility_momentum_context,
                pattern_trigger_context=pattern_trigger_context,
                multi_timeframe_structure_context=multi_timeframe_structure_context,
                method_execution_receipts=method_execution_receipts,
                include_canonical=True,
                asset_type=asset_type,
                **({"b1_rights_context": getattr(self, "b1_rights_context", None)}
                   if getattr(self, "b1_rights_context", None) is not None else {}),
            )
        except Exception as exc:
            if self.p0_bounded_trial:
                raise P0BoundedTrialError(
                    f"P0 canonical decision finalization failed for {code}: {exc}"
                ) from exc
            logger.error(
                "[%s] 构建确定性综合评估失败，终止本次结果以避免保留旧报告: %s",
                code,
                exc,
            )
            raise
        if not isinstance(result.dashboard, dict):
            result.dashboard = {}
        result.dashboard["factor_decision"] = summary
        canonical_scope = "p0" if self.p0_bounded_trial else "production"
        apply_canonical_decision_to_result(result, summary, scope=canonical_scope)
        assert_canonical_consumer_consistency(result, scope=canonical_scope)

    @staticmethod
    def _delivery_fact_projection(factor: Any) -> Dict[str, Any]:
        """Project deterministic material-change facts without LLM prose."""
        if not isinstance(factor, dict):
            return {}

        try:
            canonical_identity = validate_canonical_factor_binding(factor)
            brief = validate_investor_brief_binding(factor)
        except ValueError:
            return {}
        canonical = factor.get("canonical_decision")
        canonical_projection = {
            key: canonical.get(key)
            for key in (
                "authority",
                "action",
                "public_action",
                "evidence_state",
                "hard_veto",
                "reason_codes",
            )
        }

        def compact_state(value: Any) -> Any:
            if isinstance(value, dict):
                kept = {}
                for key, item in value.items():
                    key_text = str(key or "")
                    if key_text in {
                        "state",
                        "status",
                        "evidence_state",
                        "hard_veto",
                        "reason_codes",
                        "lifecycle",
                        "pattern_state",
                        "confirmed",
                        "provisional",
                        "invalidation",
                        "action",
                        "public_action",
                    }:
                        kept[key_text] = item
                    elif isinstance(item, (dict, list)):
                        nested = compact_state(item)
                        if nested not in ({}, [], None):
                            kept[key_text] = nested
                return kept
            if isinstance(value, list):
                compacted = [compact_state(item) for item in value]
                return [item for item in compacted if item not in ({}, [], None)]
            return None

        evidence_projection = {}
        for key in (
            "market_sector_regime",
            "trend_relative_strength",
            "supply_demand_volume_price",
            "cost_structure_evidence",
            "price_structure_evidence",
            "volatility_momentum_evidence",
            "pattern_trigger_evidence",
            "multi_timeframe_structure_context",
        ):
            compacted = compact_state(factor.get(key))
            if compacted not in ({}, [], None):
                evidence_projection[key] = compacted

        brief_projection = {}
        if isinstance(brief, dict):
            valuation = brief.get("valuation")
            key_levels = brief.get("key_levels")
            brief_projection = {
                "asset_type": brief.get("asset_type"),
                "trigger": brief.get("trigger"),
                "invalidation": brief.get("invalidation"),
                "valuation": (
                    {
                        key: valuation.get(key)
                        for key in ("status", "bucket")
                        if valuation.get(key) not in (None, "")
                    }
                    if isinstance(valuation, dict)
                    else {}
                ),
                "key_levels": (
                    {
                        key: key_levels.get(key)
                        for key in ("support", "resistance")
                        if key_levels.get(key) not in (None, "")
                    }
                    if isinstance(key_levels, dict)
                    else {}
                ),
            }

        return {
            "strategy_id": factor.get("strategy_id"),
            "asset_type": factor.get("asset_type"),
            "canonical_decision": canonical_projection,
            "canonical_identity": canonical_identity,
            "evidence_states": evidence_projection,
            "brief": brief_projection,
        }

    @classmethod
    def _delivery_fact_hash(cls, factor: Any) -> Optional[str]:
        projection = cls._delivery_fact_projection(factor)
        if not projection or not projection.get("canonical_decision"):
            return None
        return sha256_payload(projection)

    @staticmethod
    def _dashboard_from_history_record(record: Any) -> Optional[Dict[str, Any]]:
        raw = getattr(record, "raw_result", None)
        if not raw:
            return None
        try:
            payload = json.loads(raw) if isinstance(raw, str) else raw
        except (TypeError, ValueError):
            return None
        if not isinstance(payload, dict):
            return None
        dashboard = payload.get("dashboard")
        return dashboard if isinstance(dashboard, dict) else None

    @classmethod
    def _factor_from_history_record(cls, record: Any) -> Optional[Dict[str, Any]]:
        dashboard = cls._dashboard_from_history_record(record)
        if not isinstance(dashboard, dict):
            return None
        factor = dashboard.get("factor_decision")
        if not isinstance(factor, dict):
            return None
        try:
            validate_canonical_factor_binding(factor)
            validate_investor_brief_binding(factor)
        except ValueError:
            return None
        return factor

    @classmethod
    def _history_selection_source(cls, record: Any) -> str:
        dashboard = cls._dashboard_from_history_record(record)
        if not isinstance(dashboard, dict):
            return ""
        delivery = dashboard.get("research_delivery")
        if isinstance(delivery, dict):
            source = str(delivery.get("selection_source") or "").strip()
            if source:
                return source
        context = dashboard.get("research_selection_context")
        if isinstance(context, dict):
            return str(context.get("selection_source") or "").strip()
        return ""

    @staticmethod
    def _research_asset_identity(
        result: AnalysisResult,
        *,
        asset_type: str,
    ) -> Dict[str, Any]:
        code = normalize_stock_code(str(getattr(result, "code", "") or ""))
        market = get_market_for_stock(str(getattr(result, "code", "") or ""))
        market_label = {
            "cn": "A股",
            "hk": "港股",
            "us": "美股",
            "jp": "日股",
            "kr": "韩股",
            "tw": "台股",
        }.get(str(market or "").lower(), "市场待确认")

        if asset_type == "etf":
            return {
                "listing_market": "A股ETF" if market == "cn" else market_label,
                "listing_board": "ETF",
            }

        listing_board = "UNKNOWN"
        if market == "cn" and code.isdigit() and len(code) == 6:
            if is_bse_code(code):
                listing_board = "北交所"
            elif code.startswith(("688", "689")):
                listing_board = "科创板"
            elif code.startswith(("300", "301")):
                listing_board = "创业板"
            elif code.startswith(("600", "601", "603", "605", "000", "001", "002", "003")):
                listing_board = "主板"
        return {
            "listing_market": market_label,
            "listing_board": listing_board,
        }

    @staticmethod
    def _research_asset_identity_text(identity: Any) -> str:
        if not isinstance(identity, dict):
            return ""
        market = str(identity.get("listing_market") or "").strip()
        board = str(identity.get("listing_board") or "").strip()
        market_text = market if market and market != "UNKNOWN" else "市场待确认"
        board_text = board if board and board != "UNKNOWN" else "板块待确认"
        return f"{market_text}｜{board_text}"

    def _attach_research_delivery_state(
        self,
        result: AnalysisResult,
        *,
        query_id: str,
    ) -> None:
        context = normalize_research_selection_context(
            getattr(self, "research_selection_context", None)
        )
        selection_source = str(context.get("selection_source") or "SPECIFIED_CODES")
        envelope = str(context.get("delivery_envelope") or "")
        dashboard = (
            result.dashboard
            if isinstance(getattr(result, "dashboard", None), dict)
            else {}
        )
        if not isinstance(result.dashboard, dict):
            result.dashboard = dashboard
        dashboard["research_selection_context"] = context

        factor = dashboard.get("factor_decision")
        if not isinstance(factor, dict):
            return
        asset_type = str(factor.get("asset_type") or "stock")
        current_hash = self._delivery_fact_hash(factor)
        asset_identity = self._research_asset_identity(
            result,
            asset_type=asset_type,
        )
        delivery = {
            "schema_version": "research-delivery-v1",
            "selection_source": selection_source,
            "delivery_envelope": envelope or None,
            "asset_type": asset_type,
            "asset_identity": asset_identity,
            "asset_identity_text": self._research_asset_identity_text(asset_identity),
            "canonical_fact_hash": current_hash,
            "product_group": None,
            "group_rank": None,
            "material_change": None,
            "material_change_reason": "NOT_APPLICABLE",
        }

        if selection_source == "AUTO_SCREEN":
            screening = context.get("screening")
            selected = (
                screening.get("selected_candidates")
                if isinstance(screening, dict)
                else []
            )
            normalized_code = normalize_stock_code(
                str(getattr(result, "code", "") or "")
            )
            for candidate in selected or []:
                if not isinstance(candidate, dict):
                    continue
                candidate_code = normalize_stock_code(
                    str(candidate.get("code") or "")
                )
                if candidate_code != normalized_code:
                    continue
                delivery["product_group"] = candidate.get("product_group")
                delivery["group_rank"] = candidate.get("group_rank")
                delivery.update({
                    "candidate_name": candidate.get("name"),
                    "candidate_score": candidate.get("score"),
                    "screen_score": candidate.get("screen_score"),
                    "candidate_reason": candidate.get("reason"),
                    "candidate_risk_level": candidate.get("risk_level"),
                    "candidate_risk_flags": list(candidate.get("risk_flags") or ()),
                    "candidate_industry": candidate.get("industry"),
                    "preference_status": candidate.get("preference_status"),
                    "classification_status": candidate.get("classification_status"),
                    "focus_eligible": candidate.get("focus_eligible"),
                })
                candidate_identity = {
                    "listing_market": candidate.get("listing_market"),
                    "listing_board": candidate.get("listing_board"),
                }
                candidate_identity = {
                    key: value
                    for key, value in candidate_identity.items()
                    if str(value or "").strip()
                }
                if candidate_identity:
                    delivery["asset_identity"] = candidate_identity
                    delivery["asset_identity_text"] = self._research_asset_identity_text(
                        candidate_identity
                    )
                break
        elif envelope == "ASSET_RESEARCH_BRIEF_WATCHLIST":
            delivery["product_group"] = (
                "WATCHLIST_ETF" if asset_type == "etf" else "WATCHLIST_STOCK"
            )
            previous_factor = None
            history_comparison_failed = False
            try:
                records = self.db.get_analysis_history(
                    code=str(getattr(result, "code", "") or ""),
                    days=30,
                    limit=5,
                    exclude_query_id=query_id,
                )
                for record in records:
                    if self._history_selection_source(record) == "AUTO_SCREEN":
                        continue
                    previous_factor = self._factor_from_history_record(record)
                    if previous_factor is not None:
                        break
            except Exception as exc:
                history_comparison_failed = True
                logger.warning(
                    "Watchlist material-change history lookup failed for %s: %s",
                    getattr(result, "code", ""),
                    exc,
                )
            previous_hash = self._delivery_fact_hash(previous_factor)
            if not current_hash:
                delivery["material_change"] = True
                delivery["material_change_reason"] = "CURRENT_CANONICAL_FACTS_UNAVAILABLE"
            elif history_comparison_failed:
                delivery["material_change"] = True
                delivery["material_change_reason"] = "HISTORY_COMPARISON_UNAVAILABLE"
            elif not previous_hash:
                delivery["material_change"] = True
                delivery["material_change_reason"] = "FIRST_COMPARABLE_ANALYSIS"
            elif previous_hash != current_hash:
                delivery["material_change"] = True
                delivery["material_change_reason"] = "CANONICAL_FACTS_CHANGED"
            else:
                delivery["material_change"] = False
                delivery["material_change_reason"] = "UNCHANGED"

        dashboard["research_delivery"] = delivery

    def _promote_p0_deterministic_result_after_explanation_failure(
        self,
        result: Optional[AnalysisResult],
        *,
        code: str,
    ) -> bool:
        """Allow a proven P0 deterministic result to survive explanation-only failure."""
        if not self.p0_bounded_trial or result is None or bool(getattr(result, "success", False)):
            return False
        dashboard = result.dashboard if isinstance(getattr(result, "dashboard", None), dict) else {}
        summary = dashboard.get("factor_decision")
        if not canonical_explanation_degradation_eligible(summary):
            return False

        explanation_status = {
            "state": "UNAVAILABLE",
            "mode": "DETERMINISTIC_DEGRADED",
            "reason": "LLM_EXPLANATION_UNAVAILABLE",
        }
        summary["explanation_status"] = dict(explanation_status)
        investor_brief = summary.get("investor_brief")
        if isinstance(investor_brief, dict):
            investor_brief["explanation_status"] = dict(explanation_status)
        dashboard["explanation_status"] = dict(explanation_status)

        result.success = True
        result.error_message = None
        apply_canonical_decision_to_result(result, summary)
        assert_canonical_consumer_consistency(result)
        logger.warning(
            "[%s] LLM explanation unavailable; continuing with proven deterministic P0 evidence only",
            code,
        )
        return True

    @staticmethod
    def _refresh_decision_action_for_final_result(
        result: AnalysisResult,
        *,
        report_type: Any,
        previous_operation_advice: Any,
    ) -> AnalysisResult:
        # A guardrail may rewrite the advice after the Agent action was parsed;
        # discard that stale action before using the same resolver as the
        # downstream DecisionSignal builder.
        previous_advice = str(previous_operation_advice or "").strip()
        current_advice = str(getattr(result, "operation_advice", None) or "").strip()
        if previous_advice != current_advice:
            result.action = None
            result.action_label = None
        fields = resolve_decision_signal_action_fields(
            result,
            report_type=str(report_type or ""),
        )
        result.action = fields["action"]
        result.action_label = fields["action_label"]
        return result

    @staticmethod
    def _agent_dashboard_value(
        dash: Dict[str, Any],
        nested_dashboard: Any,
        key: str,
        *,
        scalar: bool = False,
        allow_dict: bool = False,
        expect_text: bool = False,
    ) -> Any:
        """Read a scalar from top-level agent payload, then nested dashboard fallback."""
        value = dash.get(key) if isinstance(dash, dict) else None
        if isinstance(nested_dashboard, dict) and StockAnalysisPipeline._is_agent_field_missing(
            value,
            scalar=scalar,
            allow_dict=allow_dict,
            expect_text=expect_text,
        ):
            nested_value = nested_dashboard.get(key)
            if not StockAnalysisPipeline._is_agent_field_missing(
                nested_value,
                scalar=scalar,
                allow_dict=allow_dict,
                expect_text=expect_text,
            ):
                value = nested_value
        return value

    @staticmethod
    def _extract_advice_text_from_dict(raw_advice: dict) -> str:
        for field in ("has_position", "no_position"):
            if isinstance(raw_advice.get(field), str):
                text = raw_advice[field].strip()
                if not StockAnalysisPipeline._is_agent_placeholder_text(text):
                    return text

        for value in raw_advice.values():
            if isinstance(value, str):
                text = value.strip()
                if not StockAnalysisPipeline._is_agent_placeholder_text(text):
                    return text

        return ""

    @staticmethod
    def _is_agent_placeholder_text(text: str) -> bool:
        if not text:
            return True
        return text.lower() in {"n/a", "na", "none", "null", "unknown", "tbd"} or text in {
            "未知",
            "待补充",
            "数据缺失",
            "无",
        }

    @staticmethod
    def _is_agent_field_missing(
        value: Any,
        *,
        scalar: bool = False,
        allow_dict: bool = False,
        expect_text: bool = False,
    ) -> bool:
        if scalar and isinstance(value, dict):
            if not allow_dict or not value:
                return True
            return not StockAnalysisPipeline._extract_advice_text_from_dict(value)
        if value is None:
            return True
        if expect_text and scalar:
            if not isinstance(value, str):
                return True
        if isinstance(value, str):
            text = value.strip()
            return StockAnalysisPipeline._is_agent_placeholder_text(text)
        if isinstance(value, dict):
            if scalar:
                return not allow_dict
            return not value
        if scalar and isinstance(value, (list, tuple, set)):
            return True
        return False

    @staticmethod
    def _trend_score_fallback(trend_result: Optional[TrendAnalysisResult]) -> Optional[int]:
        if trend_result is None:
            return None
        try:
            score = int(getattr(trend_result, "signal_score", 0))
        except (TypeError, ValueError):
            return None
        return score if score > 0 else None

    @staticmethod
    def _trend_label_fallback(
        trend_result: Optional[TrendAnalysisResult],
        report_language: str = "zh",
    ) -> str:
        if trend_result is None:
            return ""
        trend_status = getattr(trend_result, "trend_status", None)
        value = getattr(trend_status, "value", None) or str(trend_status or "").strip()
        if report_language != "en":
            return value
        return localize_trend_prediction(value, report_language)

    @staticmethod
    def _trend_signal_fallback(
        trend_result: Optional[TrendAnalysisResult],
        report_language: str = "zh",
    ) -> str:
        if trend_result is None:
            return ""
        buy_signal = getattr(trend_result, "buy_signal", None)
        value = getattr(buy_signal, "value", None) or str(buy_signal or "").strip()
        return localize_operation_advice(value, report_language)

    @staticmethod
    def _trend_decision_fallback(trend_result: Optional[TrendAnalysisResult]) -> Optional[str]:
        if trend_result is None:
            return None
        signal_name = getattr(getattr(trend_result, "buy_signal", None), "name", "").lower()
        return {
            "strong_buy": "buy",
            "buy": "buy",
            "hold": "hold",
            "wait": "hold",
            "sell": "sell",
            "strong_sell": "sell",
        }.get(signal_name)

    @staticmethod
    def _mark_trend_fallback_source(result: AnalysisResult) -> None:
        if "trend:fallback" in (result.data_sources or ""):
            return
        result.data_sources = (
            f"{result.data_sources},trend:fallback"
            if result.data_sources
            else "trend:fallback"
        )

    @staticmethod
    def _summary_fallback_from_result(result: AnalysisResult, report_language: str) -> str:
        trend = (result.trend_prediction or "").strip()
        advice = (result.operation_advice or "").strip()
        if trend and advice:
            if report_language == "en":
                return f"Trend view: {trend}; action advice: {advice}."
            if report_language == "ko":
                return f"추세 결론: {trend}; 대응 전략: {advice}."
            return f"趋势结论：{trend}；操作建议：{advice}。"
        return ""

    def _backfill_agent_dashboard_fields(
        self,
        result: AnalysisResult,
        trend_result: Optional[TrendAnalysisResult],
        report_language: str,
    ) -> None:
        if not isinstance(result.dashboard, dict):
            result.dashboard = {}
        dashboard = result.dashboard

        for key in (
            "sentiment_score",
            "trend_prediction",
            "operation_advice",
            "decision_type",
            "confidence_level",
            "analysis_summary",
        ):
            current = dashboard.get(key)
            if key == "sentiment_score":
                if self._is_agent_field_missing(current, scalar=True):
                    dashboard[key] = getattr(result, key)
            elif self._is_agent_field_missing(current, scalar=True, expect_text=True):
                dashboard[key] = getattr(result, key)

        core = dashboard.get("core_conclusion")
        if not isinstance(core, dict):
            core = {}
            dashboard["core_conclusion"] = core
        if self._is_agent_field_missing(core.get("one_sentence"), scalar=True):
            core["one_sentence"] = result.analysis_summary or self._summary_fallback_from_result(
                result,
                report_language,
            ) or (
                "Analysis pending" if report_language == "en"
                else "분석 보완 예정" if report_language == "ko"
                else "分析待补充"
            )

        intelligence = dashboard.get("intelligence")
        if not isinstance(intelligence, dict):
            intelligence = {}
            dashboard["intelligence"] = intelligence
        risk_alerts = intelligence.get("risk_alerts")
        if (
            "risk_alerts" not in intelligence
            or self._is_agent_field_missing(risk_alerts)
            or not isinstance(risk_alerts, list)
        ):
            risk_factors = getattr(trend_result, "risk_factors", None) or []
            intelligence["risk_alerts"] = list(risk_factors)

        if result.decision_type in ("buy", "hold"):
            battle = dashboard.get("battle_plan")
            if not isinstance(battle, dict):
                battle = {}
                dashboard["battle_plan"] = battle
            sniper_points = battle.get("sniper_points")
            if not isinstance(sniper_points, dict):
                sniper_points = {}
                battle["sniper_points"] = sniper_points
            if self._is_agent_field_missing(sniper_points.get("stop_loss"), scalar=True):
                sniper_points["stop_loss"] = self._stop_loss_fallback_from_trend(
                    trend_result,
                    report_language,
                )

    @staticmethod
    def _stop_loss_fallback_from_trend(
        trend_result: Optional[TrendAnalysisResult],
        report_language: str,
    ) -> Any:
        levels = getattr(trend_result, "support_levels", None) if trend_result else None
        if levels:
            return levels[0]
        return get_placeholder_text(report_language)

    @staticmethod
    def _apply_trend_fallback(
        result: AnalysisResult,
        trend_result: Optional[TrendAnalysisResult],
        report_language: str,
    ) -> None:
        if trend_result is None:
            result.sentiment_score = 50
            result.operation_advice = localize_operation_advice("观望", report_language)
            return

        score = getattr(trend_result, "signal_score", None)
        try:
            numeric_score = int(score)
        except (TypeError, ValueError):
            numeric_score = 50
        result.sentiment_score = numeric_score if numeric_score > 0 else 50

        trend_label = StockAnalysisPipeline._trend_label_fallback(trend_result, report_language)
        if trend_label:
            result.trend_prediction = trend_label

        buy_signal = getattr(trend_result, "buy_signal", None)
        signal_label = StockAnalysisPipeline._trend_signal_fallback(
            trend_result,
            report_language,
        )
        if signal_label:
            result.operation_advice = signal_label
        else:
            result.operation_advice = localize_operation_advice("观望", report_language)

        from src.agent.protocols import normalize_decision_signal

        signal_name = getattr(buy_signal, "name", "").lower()
        signal_to_decision = {
            "strong_buy": "buy",
            "buy": "buy",
            "hold": "hold",
            "wait": "hold",
            "sell": "sell",
            "strong_sell": "sell",
        }
        result.decision_type = signal_to_decision.get(signal_name, result.decision_type or "hold")
        result.decision_type = normalize_decision_signal(result.decision_type)
        result.data_sources = f"{result.data_sources},trend:fallback" if result.data_sources else "trend:fallback"

    @staticmethod
    def _is_placeholder_stock_name(name: str, code: str) -> bool:
        """Return True when the stock name is missing or placeholder-like."""
        if not name:
            return True
        normalized = str(name).strip()
        if not normalized:
            return True
        if normalized == code:
            return True
        if normalized.startswith("股票"):
            return True
        if "Unknown" in normalized:
            return True
        return False

    @staticmethod
    def _safe_int(value: Any, default: int = 50) -> int:
        """安全地将值转换为整数。"""
        if value is None:
            return default
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            return int(value)
        if isinstance(value, str):
            import re
            match = re.search(r'-?\d+', value)
            if match:
                return int(match.group())
        return default
    
    def _describe_volume_ratio(self, volume_ratio: float) -> str:
        """
        量比描述
        
        量比 = 当前成交量 / 过去5日平均成交量
        """
        if volume_ratio < 0.5:
            return "极度萎缩"
        elif volume_ratio < 0.8:
            return "明显萎缩"
        elif volume_ratio < 1.2:
            return "正常"
        elif volume_ratio < 2.0:
            return "温和放量"
        elif volume_ratio < 3.0:
            return "明显放量"
        else:
            return "巨量"

    @staticmethod
    def _compute_ma_status(close: float, ma5: float, ma10: float, ma20: float) -> str:
        """
        Compute MA alignment status from price and MA values.
        Logic mirrors storage._analyze_ma_status (Issue #234).
        """
        close = close or 0
        ma5 = ma5 or 0
        ma10 = ma10 or 0
        ma20 = ma20 or 0
        if close > ma5 > ma10 > ma20 > 0:
            return "多头排列 📈"
        elif close < ma5 < ma10 < ma20 and ma20 > 0:
            return "空头排列 📉"
        elif close > ma5 and ma5 > ma10:
            return "短期向好 🔼"
        elif close < ma5 and ma5 < ma10:
            return "短期走弱 🔽"
        else:
            return "震荡整理 ↔️"

    def _augment_historical_with_realtime(
        self, df: pd.DataFrame, realtime_quote: Any, code: str
    ) -> pd.DataFrame:
        """
        使用当日实时行情补齐历史 OHLCV，用于盘中 MA 计算。
        Issue #234：技术指标使用实时价格，而不是沿用昨日收盘价。
        """
        if df is None or df.empty or 'close' not in df.columns:
            return df
        if realtime_quote is None:
            return df
        price = getattr(realtime_quote, 'price', None)
        if price is None or not (isinstance(price, (int, float)) and price > 0):
            return df

        # 非交易日可跳过实时补齐；异常情况下保持失败开放。
        enable_realtime_tech = getattr(
            self.config, 'enable_realtime_technical_indicators', True
        )
        if not enable_realtime_tech:
            return df
        market = get_market_for_stock(code)
        market_today = get_market_now(market).date()
        if market and not is_market_open(market, market_today):
            return df

        last_val = df['date'].max()
        last_date = (
            last_val.date() if hasattr(last_val, 'date') else
            (last_val if isinstance(last_val, date) else pd.Timestamp(last_val).date())
        )
        yesterday_close = float(df.iloc[-1]['close']) if len(df) > 0 else price
        open_p = getattr(realtime_quote, 'open_price', None) or getattr(
            realtime_quote, 'pre_close', None
        ) or yesterday_close
        high_p = getattr(realtime_quote, 'high', None) or price
        low_p = getattr(realtime_quote, 'low', None) or price
        vol = getattr(realtime_quote, 'volume', None) or 0
        amt = getattr(realtime_quote, 'amount', None)
        pct = getattr(realtime_quote, 'change_pct', None)

        if last_date >= market_today:
            # 使用实时收盘价更新最后一行；先复制，避免修改调用方传入的 df。
            df = df.copy()
            idx = df.index[-1]
            df.loc[idx, 'close'] = price
            if open_p is not None:
                df.loc[idx, 'open'] = open_p
            if high_p is not None:
                df.loc[idx, 'high'] = high_p
            if low_p is not None:
                df.loc[idx, 'low'] = low_p
            if vol:
                df.loc[idx, 'volume'] = vol
            if amt is not None:
                df.loc[idx, 'amount'] = amt
            if pct is not None:
                df.loc[idx, 'pct_chg'] = pct
        else:
            # 追加一行虚拟的当日实时 K 线。
            new_row = {
                'code': code,
                'date': market_today,
                'open': open_p,
                'high': high_p,
                'low': low_p,
                'close': price,
                'volume': vol,
                'amount': amt if amt is not None else 0,
                'pct_chg': pct if pct is not None else 0,
            }
            new_df = pd.DataFrame([new_row])
            df = pd.concat([df, new_df], ignore_index=True)
        return df

    def _build_context_snapshot(
        self,
        enhanced_context: Dict[str, Any],
        news_content: Optional[str],
        realtime_quote: Any,
        chip_data: Optional[ChipDistribution],
        news_result_count: Optional[int] = None,
        analysis_context_pack_overview: Optional[Dict[str, Any]] = None,
        market_phase_summary: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        构建分析上下文快照
        """
        snapshot = {
            "enhanced_context": self._without_runtime_prompt_context(enhanced_context),
            "news_content": news_content,
            "realtime_quote_raw": self._safe_to_dict(realtime_quote),
            "chip_distribution_raw": self._safe_to_dict(chip_data),
        }
        market_structure_context = enhanced_context.get("market_structure_context")
        if isinstance(market_structure_context, dict):
            snapshot["market_structure_context"] = market_structure_context
        if news_content is not None:
            snapshot["news_retrieval_content"] = news_content
        if news_result_count is not None:
            snapshot["news_result_count"] = news_result_count
        if analysis_context_pack_overview is not None:
            snapshot["analysis_context_pack_overview"] = analysis_context_pack_overview
        if market_phase_summary is not None:
            snapshot[MARKET_PHASE_SUMMARY_KEY] = market_phase_summary
        diagnostic_snapshot = current_diagnostic_snapshot()
        if diagnostic_snapshot is not None:
            snapshot["diagnostics"] = diagnostic_snapshot
        if self.analysis_skills is not None:
            snapshot["skills"] = list(self.analysis_skills)
        return snapshot

    def _persist_skill_opinion_samples_after_history_save(
        self,
        *,
        runtime_facts: Any,
        analysis_history_id: int,
        stock_code: str,
        analysis_context_pack_overview: Optional[Dict[str, Any]],
    ) -> None:
        """Best-effort persistence for valid individual skill opinions."""
        opinions = getattr(runtime_facts, "skill_opinions", ()) if runtime_facts else ()
        if not opinions:
            return

        quality_level = None
        if isinstance(analysis_context_pack_overview, dict):
            quality = analysis_context_pack_overview.get("data_quality")
            if isinstance(quality, dict):
                quality_level = quality.get("level")

        try:
            from src.services.skill_opinion_sample_service import SkillOpinionSampleService

            SkillOpinionSampleService(db_manager=self.db).persist(
                analysis_history_id=analysis_history_id,
                stock_code=stock_code,
                opinions=opinions,
                data_quality_level=quality_level,
            )
        except Exception as exc:
            logger.warning(
                "Skill opinion sample persistence skipped after history save: "
                "stock_code=%s error_type=%s",
                stock_code,
                type(exc).__name__,
            )

    def _compile_learning_recording_intent(
        self,
        *,
        result: AnalysisResult,
        query_id: str,
        report_type: str,
    ) -> Dict[str, Any]:
        """Compile one stable learning-recording identity before History is committed."""
        from src.services.learning_recording_service import LearningRecordingService

        cohort_query_id = (
            str(getattr(self, "research_recording_run_id", None) or "").strip()
            or query_id
        )
        intent = LearningRecordingService.compile_intent(
            result=result,
            query_id=cohort_query_id,
            report_type=report_type,
            selection_context=getattr(self, "research_selection_context", None),
            code_sha=getattr(self, "research_code_sha", None),
            intended_cohort_id=getattr(
                self,
                "research_intended_cohort_id",
                None,
            ),
        )
        setattr(result, "learning_recording_intent", dict(intent))
        setattr(
            result,
            "learning_recording_intent_hash",
            intent["recording_intent_hash"],
        )
        setattr(result, "intended_cohort_id", intent.get("intended_cohort_id"))
        return intent

    def _transition_learning_recording(
        self,
        *,
        result: AnalysisResult,
        status: str,
        reason_code: Optional[str] = None,
        prediction_hash: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """Best-effort transition of an already-durable same-DB recording intent."""
        from src.services.learning_recording_service import LearningRecordingService

        intent_hash = str(
            getattr(result, "learning_recording_intent_hash", "") or ""
        ).strip()
        if not intent_hash:
            return None
        service = LearningRecordingService(db_manager=self.db)
        target = str(status or "").strip().upper()
        if target == "RECORDED" and prediction_hash:
            receipt = service.mark_recorded(
                intent_hash,
                prediction_hash=prediction_hash,
            )
        elif target == "LAWFULLY_REJECTED":
            receipt = service.mark_lawfully_rejected(
                intent_hash,
                reason_code=reason_code or "LEDGER_LAWFUL_REJECTION",
            )
        else:
            receipt = service.mark_technically_lost(
                intent_hash,
                reason_code=reason_code or "LEARNING_RECORDING_TECHNICAL_LOSS",
            )
        if isinstance(receipt, dict):
            setattr(result, "learning_recording_receipt", dict(receipt))
            return dict(receipt)
        return None

    def _extract_decision_signal_after_history_save(
        self,
        *,
        result: AnalysisResult,
        query_id: str,
        source_report_id: int,
        report_type: str,
        context_snapshot: Dict[str, Any],
        portfolio_context: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        """Best-effort DecisionSignal extraction after analysis history is saved."""

        assert (
            isinstance(source_report_id, int)
            and not isinstance(source_report_id, bool)
            and source_report_id > 0
        )

        try:
            diagnostic_context = get_current_diagnostic_context()
            trace_id = (
                getattr(diagnostic_context, "trace_id", None)
                or getattr(self, "trace_id", None)
                or query_id
            )
            signal_result = extract_and_persist_from_analysis_result(
                result,
                context_snapshot=context_snapshot,
                source_report_id=source_report_id,
                trace_id=str(trace_id),
                query_source=getattr(self, "query_source", None) or "system",
                report_type=report_type,
                portfolio_context=portfolio_context,
                profile_source="auto_default",
            )
            if isinstance(signal_result, dict):
                summary = summarize_decision_signal(signal_result.get("item"))
                if summary:
                    setattr(result, "decision_signal_summary", summary)
                return signal_result
            self._transition_learning_recording(
                result=result,
                status="TECHNICALLY_LOST",
                reason_code="DECISION_SIGNAL_NOT_RECORDED",
            )
            return None
        except Exception as exc:
            logger.warning(
                "Decision signal extraction skipped after history save: query_id=%s stock_code=%s error=%s",
                query_id,
                getattr(result, "code", None),
                exc,
                exc_info=True,
            )
            self._transition_learning_recording(
                result=result,
                status="TECHNICALLY_LOST",
                reason_code="DECISION_SIGNAL_TECHNICAL_FAILURE",
            )
            return None

    def _persist_prediction_ledger_after_history_save(
        self,
        *,
        result: AnalysisResult,
        analysis_history_id: int,
        decision_signal: Optional[Dict[str, Any]],
    ) -> Optional[Dict[str, Any]]:
        """Persist one typed Ledger outcome and reconcile the durable recording intent."""
        try:
            from src.services.prediction_ledger_service import PredictionLedgerService

            if not isinstance(decision_signal, dict):
                self._transition_learning_recording(
                    result=result,
                    status="TECHNICALLY_LOST",
                    reason_code="DECISION_SIGNAL_READBACK_MISSING",
                )
                return None

            receipt = PredictionLedgerService(db_manager=self.db).persist(
                analysis_history_id=analysis_history_id,
                result=result,
                decision_signal=decision_signal,
                code_sha=getattr(self, "research_code_sha", None),
                selection_context=getattr(self, "research_selection_context", None),
                recording_intent_hash=getattr(
                    result,
                    "learning_recording_intent_hash",
                    None,
                ),
                intended_cohort_id=getattr(result, "intended_cohort_id", None),
            )
            if not isinstance(receipt, dict):
                self._transition_learning_recording(
                    result=result,
                    status="TECHNICALLY_LOST",
                    reason_code="LEDGER_TYPED_OUTCOME_MISSING",
                )
                return None

            status = str(receipt.get("status") or "").strip().upper()
            reason_code = str(receipt.get("reason_code") or "").strip() or None
            if status == "RECORDED":
                prediction_hash = str(receipt.get("prediction_hash") or "").strip()
                self._transition_learning_recording(
                    result=result,
                    status="RECORDED",
                    prediction_hash=prediction_hash,
                )
                setattr(result, "prediction_ledger_receipt", dict(receipt))
                return dict(receipt)
            if status == "LAWFULLY_REJECTED":
                self._transition_learning_recording(
                    result=result,
                    status=status,
                    reason_code=reason_code or "LEDGER_LAWFUL_REJECTION",
                )
                return None

            self._transition_learning_recording(
                result=result,
                status="TECHNICALLY_LOST",
                reason_code=reason_code or "LEDGER_TECHNICAL_FAILURE",
            )
            return None
        except Exception as exc:
            logger.warning(
                "Prediction ledger snapshot skipped after history save: "
                "history_id=%s stock_code=%s error_type=%s",
                analysis_history_id,
                getattr(result, "code", None),
                type(exc).__name__,
            )
            self._transition_learning_recording(
                result=result,
                status="TECHNICALLY_LOST",
                reason_code="LEDGER_TECHNICAL_EXCEPTION",
            )
            return None

    @staticmethod
    def _build_notification_run_snapshot(
        *,
        channel: str,
        status: str,
        success: bool,
        attempts: int = 1,
        error_message: Optional[Any] = None,
    ) -> Dict[str, Any]:
        payload = {
            "channel": channel,
            "status": status,
            "success": success,
            "attempts": attempts,
            "created_at": datetime.now().isoformat(),
        }
        sanitized_error = sanitize_diagnostic_text(error_message)
        if sanitized_error:
            payload["error_message_sanitized"] = sanitized_error
        return payload

    def _refresh_saved_diagnostic_snapshot(
        self,
        *,
        result: Optional[AnalysisResult] = None,
        results: Optional[List[AnalysisResult]] = None,
        fallback_code: Optional[str] = None,
        notification_run: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Patch persisted history diagnostics with notification outcomes."""
        if not getattr(self, "save_context_snapshot", True):
            return

        db = getattr(self, "db", None)
        updater = getattr(db, "update_analysis_history_diagnostics", None)
        if not callable(updater):
            return

        diagnostic_snapshot = current_diagnostic_snapshot()
        if diagnostic_snapshot is not None:
            query_id = (
                diagnostic_snapshot.get("query_id")
                or getattr(result, "query_id", None)
                or getattr(self, "query_id", None)
            )
            code = (
                getattr(result, "code", None)
                or fallback_code
                or diagnostic_snapshot.get("stock_code")
            )
            if not query_id:
                return
            try:
                updater(query_id=query_id, code=code, diagnostics=diagnostic_snapshot)
            except Exception as exc:
                logger.warning("回写运行诊断快照失败（fail-open）: %s", exc)
            return

        if notification_run is None:
            return

        target_results = list(results or ([] if result is None else [result]))
        for item in target_results:
            query_id = getattr(item, "query_id", None) or getattr(self, "query_id", None)
            if not query_id:
                continue
            code = getattr(item, "code", None) or fallback_code
            try:
                updater(
                    query_id=query_id,
                    code=code,
                    notification_runs=[notification_run],
                )
            except Exception as exc:
                logger.warning("回写通知诊断快照失败（fail-open）: %s", exc)

    def _load_persisted_intelligence_context(
        self,
        *,
        code: str,
        stock_name: str,
        market: str,
        limit: int = 6,
    ) -> Optional[str]:
        """Load locally persisted intelligence as fail-open evidence context."""
        try:
            service = IntelligenceService(config=self.config)
            service.refresh_auto_sources()
            days = max(1, int(self.config.get_effective_news_window_days() or 1))
            collected: list[Dict[str, Any]] = []
            seen_urls: set[str] = set()
            symbol_filters = [
                {"scope_type": "symbol", "scope_value": scope_value, "market": market}
                for scope_value in _symbol_scope_lookup_values(code, market)
            ]
            for filters in symbol_filters + [{"scope_type": "market", "market": market}]:
                payload = service.list_items(published_days=days, page=1, page_size=limit, **filters)
                for item in payload.get("items", []):
                    if not isinstance(item, dict):
                        continue
                    url = str(item.get("url") or "")
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)
                    collected.append(item)
                    if len(collected) >= limit:
                        break
                if len(collected) >= limit:
                    break
            if not collected:
                return None
            lines = [f"## 本地资讯证据池（{stock_name}/{code}）"]
            for idx, item in enumerate(collected[:limit], 1):
                title = str(item.get("title") or "未命名资讯").strip()
                summary = str(item.get("summary") or "").strip()
                source = str(item.get("source") or item.get("source_name") or "local-intel").strip()
                published = str(item.get("published_at") or "").strip()
                url = str(item.get("url") or "").strip()
                meta = " / ".join(part for part in (source, published) if part)
                lines.append(f"{idx}. {title}" + (f"（{meta}）" if meta else ""))
                if summary:
                    lines.append(f"   摘要：{summary[:220]}")
                if url and not url.startswith("no-url:intel:"):
                    lines.append(f"   来源：{url}")
            return "\n".join(lines)
        except Exception as exc:
            logger.debug("读取本地资讯证据失败（fail-open）: %s", exc)
            return None

    def _build_legacy_analysis_artifacts(
        self,
        *,
        code: str,
        stock_name: str,
        market: str,
        phase: Optional[Dict[str, Any]],
        context: Dict[str, Any],
        enhanced_context: Dict[str, Any],
        realtime_quote: Any,
        trend_result: Optional[TrendAnalysisResult],
        chip_data: Optional[ChipDistribution],
        fundamental_context: Optional[Dict[str, Any]],
        news_context: Optional[str],
        news_result_count: Optional[int],
        query_id: str,
        portfolio_context: Optional[Dict[str, Any]] = None,
    ) -> PipelineAnalysisArtifacts:
        return PipelineAnalysisArtifacts(
            code=code,
            stock_name=stock_name,
            market=market,
            phase=phase,
            base_context=context,
            enhanced_context=enhanced_context,
            realtime_quote=realtime_quote,
            trend_result=trend_result,
            chip_data=chip_data,
            fundamental_context=fundamental_context,
            news_context=news_context,
            news_result_count=news_result_count,
            metadata={
                "query_id": query_id,
                "trigger_source": self.query_source,
            },
            portfolio_context=dict(portfolio_context) if isinstance(portfolio_context, dict) else None,
        )

    def _build_agent_analysis_artifacts(
        self,
        *,
        code: str,
        stock_name: str,
        market: str,
        phase: Optional[Dict[str, Any]],
        initial_context: Dict[str, Any],
        fundamental_context: Optional[Dict[str, Any]],
        query_id: str,
        base_context: Optional[Dict[str, Any]] = None,
        portfolio_context: Optional[Dict[str, Any]] = None,
    ) -> PipelineAnalysisArtifacts:
        context_candidate = base_context
        if not isinstance(context_candidate, dict):
            context_candidate = initial_context.get("analysis_context")
        if isinstance(context_candidate, dict) and context_candidate:
            daily_context = dict(context_candidate)
            daily_context.setdefault("code", code)
            if stock_name:
                daily_context.setdefault("stock_name", stock_name)
        else:
            daily_context = {
                "code": code,
                "stock_name": stock_name,
                "data_missing": True,
                "today": {},
                "yesterday": {},
            }

        return PipelineAnalysisArtifacts(
            code=code,
            stock_name=stock_name,
            market=market,
            phase=phase,
            base_context=daily_context,
            enhanced_context={},
            realtime_quote=initial_context.get("realtime_quote"),
            trend_result=initial_context.get("trend_result"),
            chip_data=initial_context.get("chip_distribution"),
            fundamental_context=fundamental_context,
            news_context=initial_context.get("news_context"),
            news_result_count=None,
            metadata={
                "query_id": query_id,
                "trigger_source": self.query_source,
            },
            portfolio_context=dict(portfolio_context) if isinstance(portfolio_context, dict) else None,
        )

    def _build_analysis_context_pack_outputs(
        self,
        artifacts: PipelineAnalysisArtifacts,
        *,
        report_language: str,
        code: str,
        query_id: str,
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        try:
            pack = AnalysisContextBuilder.build(artifacts)
            summary = format_analysis_context_pack_prompt_section(
                pack,
                report_language=report_language,
            )
            overview = render_analysis_context_pack_overview(
                pack,
                report_language=report_language,
            )
            return summary, overview
        except Exception as exc:
            logger.warning(
                "AnalysisContextPack output generation failed for %s query_id=%s: %s",
                code,
                query_id,
                exc,
            )
            return "", None

    @staticmethod
    def _without_runtime_prompt_context(context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Return a shallow copy without runtime-only prompt context.

        Market phase and AnalysisContextPack summaries are prompt inputs only.
        P4 stores only the separately rendered public overview at snapshot top level.
        """
        sanitized = dict(context)
        sanitized.pop("market_phase_context", None)
        sanitized.pop("portfolio_context", None)
        sanitized.pop("analysis_context_pack", None)
        sanitized.pop("analysis_context_pack_summary", None)
        sanitized.pop("daily_market_context_summary", None)
        enhanced_context = sanitized.get("enhanced_context")
        if isinstance(enhanced_context, dict):
            enhanced_context = dict(enhanced_context)
            enhanced_context.pop("daily_market_context_summary", None)
            sanitized["enhanced_context"] = enhanced_context
        return sanitized

    _without_market_phase_context = _without_runtime_prompt_context

    @staticmethod
    def _resolve_resume_target_date(
        code: str, current_time: Optional[datetime] = None
    ) -> date:
        """
        Resolve the trading date used by checkpoint/resume checks.
        """
        market = get_market_for_stock(normalize_stock_code(code))
        return get_effective_trading_date(market, current_time=current_time)

    @staticmethod
    def _safe_to_dict(value: Any) -> Optional[Dict[str, Any]]:
        """
        安全转换为字典
        """
        if value is None:
            return None
        if hasattr(value, "to_dict"):
            try:
                return value.to_dict()
            except Exception:
                return None
        if hasattr(value, "__dict__"):
            try:
                return dict(value.__dict__)
            except Exception:
                return None
        return None

    def _resolve_query_source(self, query_source: Optional[str] = None) -> str:
        """
        解析请求来源。

        优先级（从高到低）：
        1. 显式传入的 query_source：调用方明确指定时优先使用，便于覆盖推断结果或兼容未来 source_message 来自非 bot 的场景
        2. 存在 source_message 时推断为 "bot"：当前约定为机器人会话上下文
        3. 存在 query_id 时推断为 "web"：Web 触发的请求会带上 query_id
        4. 默认 "system"：定时任务或 CLI 等无上述上下文时

        Args:
            query_source: 调用方显式指定的来源，如 "bot" / "web" / "cli" / "system"

        Returns:
            归一化后的来源标识字符串，如 "bot" / "web" / "cli" / "system"
        """
        if query_source:
            return query_source
        if getattr(self, "source_message", None):
            return "bot"
        if getattr(self, "query_id", None):
            return "web"
        return "system"

    def _build_query_context(self, query_id: Optional[str] = None) -> Dict[str, str]:
        """
        生成用户查询关联信息
        """
        effective_query_id = query_id or self.query_id or ""

        context: Dict[str, str] = {
            "query_id": effective_query_id,
            "query_source": self.query_source or "",
        }

        if self.source_message:
            context.update({
                "requester_platform": self.source_message.platform or "",
                "requester_user_id": self.source_message.user_id or "",
                "requester_user_name": self.source_message.user_name or "",
                "requester_chat_id": self.source_message.chat_id or "",
                "requester_message_id": self.source_message.message_id or "",
                "requester_query": self.source_message.content or "",
            })

        return context
    
    def process_single_stock(
        self,
        code: str,
        skip_analysis: bool = False,
        single_stock_notify: bool = False,
        report_type: ReportType = ReportType.SIMPLE,
        analysis_query_id: Optional[str] = None,
        current_time: Optional[datetime] = None,
        observation_time_frozen: Optional[bool] = None,
    ) -> Optional[AnalysisResult]:
        """
        处理单只股票的完整流程

        包括：
        1. 获取数据
        2. 保存数据
        3. AI 分析
        4. 单股推送（可选，#55）

        此方法会被线程池调用，需要处理好异常

        Args:
            analysis_query_id: 查询链路关联 id
            code: 股票代码
            skip_analysis: 是否跳过 AI 分析
            single_stock_notify: 是否启用单股推送模式（每分析完一只立即推送）
            report_type: 报告类型枚举（从配置读取，Issue #119）
            current_time: 本轮运行冻结的参考时间，用于统一断点续传目标交易日判断

        Returns:
            AnalysisResult 或 None
        """
        logger.info(f"========== 开始处理 {code} ==========")
        if getattr(self, "b1_rights_context", None) is not None:
            if single_stock_notify:
                logger.warning("B1_NO_SEND_RESEARCH_FORBIDS_SINGLE_STOCK_NOTIFICATION")
                return None
            self._bind_b1_run_context(current_time)
        if observation_time_frozen is None:
            observation_time_frozen = current_time is not None

        from src.services.history_loader import set_frozen_target_date, reset_frozen_target_date
        frozen_td = self._resolve_resume_target_date(code, current_time=current_time)
        token = set_frozen_target_date(frozen_td)
        effective_query_id = analysis_query_id or getattr(self, "query_id", None) or uuid.uuid4().hex
        effective_trace_id = getattr(self, "trace_id", None) or effective_query_id
        diag_token = None
        if get_current_diagnostic_context() is None:
            diag_token = activate_run_diagnostic_context(
                trace_id=effective_trace_id,
                query_id=effective_query_id,
                stock_code=code,
                trigger_source=getattr(self, "query_source", None),
            )
        try:
            self._emit_progress(12, f"{code}：正在准备分析任务")
            # Step 1: 获取并保存数据
            fetch_kwargs = {"current_time": current_time}
            if observation_time_frozen != (current_time is not None):
                fetch_kwargs["observation_time_frozen"] = observation_time_frozen
            success, error = self.fetch_and_save_stock_data(code, **fetch_kwargs)
            
            if not success:
                logger.warning(f"[{code}] 数据获取失败: {error}")
                if str(error or "").startswith("B1_"):
                    # A missing/invalid grant cannot be bypassed by stale DB,
                    # LLM analysis, historical reports or notification.
                    return None
                # Older non-B1 acquisition errors may still use admitted history.
            else:
                self._emit_progress(16, f"{code}：行情数据准备完成")
            
            # Step 2: AI 分析
            if skip_analysis:
                logger.info(f"[{code}] 跳过 AI 分析（dry-run 模式）")
                return None
            
            analyze_kwargs = {"query_id": effective_query_id}
            if current_time is not None:
                analyze_kwargs["current_time"] = current_time
            if observation_time_frozen != (current_time is not None):
                analyze_kwargs["observation_time_frozen"] = observation_time_frozen
            result = self.analyze_stock(code, report_type, **analyze_kwargs)
            
            if result and result.success:
                logger.info(
                    f"[{code}] 分析完成: {result.operation_advice}, "
                    f"评分 {result.sentiment_score}"
                )
                
                # 单股推送模式（#55）：每分析完一只股票立即推送
                if single_stock_notify:
                    self._send_single_stock_notification(
                        result,
                        report_type=report_type,
                        fallback_code=code,
                    )
            elif result:
                logger.warning(
                    f"[{code}] 分析未成功: {result.error_message or '未知错误'}"
                )
            
            return result
            
        except Exception as e:
            # 捕获所有异常，确保单股失败不影响整体
            logger.exception(f"[{code}] 处理过程发生未知异常: {e}")
            return None
        finally:
            reset_run_diagnostic_context(diag_token)
            reset_frozen_target_date(token)
    
    def run(
        self,
        stock_codes: Optional[List[str]] = None,
        dry_run: bool = False,
        send_notification: bool = True,
        merge_notification: bool = False,
        current_time: Optional[datetime] = None,
    ) -> List[AnalysisResult]:
        """
        运行完整的分析流程

        流程：
        1. 获取待分析的股票列表
        2. 使用线程池并发处理
        3. 收集分析结果
        4. 发送通知

        Args:
            stock_codes: 股票代码列表（可选，默认使用配置中的自选股）
            dry_run: 是否仅获取数据不分析
            send_notification: 是否发送推送通知
            merge_notification: 是否合并推送（跳过本次推送，由 main 层合并个股+大盘后统一发送，Issue #190）
            current_time: 本轮运行冻结的参考时间；为空时在 run 内生成

        Returns:
            分析结果列表
        """
        start_time = time.time()
        if getattr(self, "b1_rights_context", None) is not None and send_notification:
            raise ValueError("B1_NO_SEND_RESEARCH_FORBIDS_OUTBOUND_NOTIFICATION")

        if self.p0_bounded_trial:
            if stock_codes is None or list(stock_codes) != self.p0_stock_codes:
                raise P0BoundedTrialError(
                    "P0 run must use the exact per-run target list supplied at construction"
                )
            if dry_run or merge_notification:
                raise P0BoundedTrialError("P0 requires live analysis without merge notification")
            if self.p0_suppress_notification:
                if send_notification:
                    raise P0BoundedTrialError("P0 suppressed-notification mode forbids outbound notification")
            elif not send_notification:
                raise P0BoundedTrialError(
                    "P0 requires analysis plus one direct aggregate Email notification"
                )
            if self.max_workers != 1:
                raise P0BoundedTrialError("P0 requires max_workers=1")
        
        # 使用配置中的股票列表
        if stock_codes is None:
            self.config.refresh_stock_list()
            stock_codes = self.config.stock_list
        
        if not stock_codes:
            logger.error("未配置自选股列表，请在 .env 文件中设置 STOCK_LIST")
            return []
        
        logger.info(f"===== 开始分析 {len(stock_codes)} 只股票 =====")
        logger.info(f"股票列表: {', '.join(stock_codes)}")
        logger.info(f"并发数: {self.max_workers}, 模式: {'仅获取数据' if dry_run else '完整分析'}")

        # 目标交易日先冻结；B1的研究decision-as-of必须等所有真实
        # Provider响应都已观测后再统一冻结，不能把响应回填到运行开始时点。
        resume_reference_time = current_time or datetime.now(timezone.utc)
        worker_reference_time = resume_reference_time
        b1_run = isinstance(getattr(self, "b1_rights_context", None), Mapping)
        if b1_run:
            if dry_run:
                raise DataObjectAdmissionError(
                    "B1_NO_SEND_DRY_RUN_CANNOT_PROVE_PRODUCT"
                )
            if self.p0_bounded_trial:
                self._bind_b1_run_context(resume_reference_time)
            else:
                worker_reference_time = self._preacquire_b1_sources(
                    list(stock_codes),
                    target_reference_time=resume_reference_time,
                )
        observation_time_frozen = (
            True if b1_run and not self.p0_bounded_trial
            else current_time is not None
        )
        # One Pipeline.run invocation is one intended learning cohort even though
        # each worker receives its own AnalysisHistory query id.  An explicit
        # research_intended_cohort_id (for example historical replay) remains
        # authoritative; this seed is only the default cohort boundary.
        self.research_recording_run_id = (
            str(getattr(self, "query_id", None) or "").strip()
            or f"pipeline-run-{uuid.uuid4().hex}"
        )
        
        # === 批量预取实时行情（优化：避免每只股票都触发全量拉取）===
        # 只有股票数量 >= 5 时才进行预取，少量股票直接逐个查询更高效
        prefetch_codes = []
        b1_prefetch_veto = isinstance(
            getattr(self, "b1_rights_context", None), Mapping
        )
        for candidate in stock_codes:
            admission = resolve_cn_price_asset(candidate)
            # Only an explicit B1 operation suppresses CN bulk quote/name/daily
            # prefetch: those endpoints have independent grants and cannot
            # borrow the admitted 1d/5m price-bar rights. Legacy/non-B1 routes
            # retain their accepted prefetch and dry-run accounting behavior.
            if b1_prefetch_veto and admission["market"] == "cn":
                continue
            prefetch_codes.append(candidate)
        # Keep ALL requests for worker failure reporting and the denominator;
        # only the eager acquisition optimization is filtered here.
        if len(prefetch_codes) >= 5:
            daily_prefetch_count = self.fetcher_manager.prefetch_daily_klines(prefetch_codes, days=30)
            if daily_prefetch_count > 0:
                logger.info(
                    "[prefetch] component=daily_kline_prefetch action=complete "
                    "provider=TickFlowFetcher cached=%d stock_count=%d",
                    daily_prefetch_count,
                    len(prefetch_codes),
                )

            prefetch_count = self.fetcher_manager.prefetch_realtime_quotes(prefetch_codes)
            if prefetch_count > 0:
                logger.info(f"已启用批量预取架构：一次拉取全市场数据，{len(stock_codes)} 只股票共享缓存")

        # Issue #455: 预取股票名称，避免并发分析时显示「股票xxxxx」
        # dry_run 仅做数据拉取，不需要名称预取，避免额外网络开销
        if not dry_run and prefetch_codes:
            self.fetcher_manager.prefetch_stock_names(prefetch_codes, use_bulk=False)

        # 单股推送模式（#55）：从配置读取
        single_stock_notify = getattr(self.config, 'single_stock_notify', False)
        delivery_envelope = str(
            (getattr(self, "research_selection_context", None) or {}).get(
                "delivery_envelope"
            )
            or ""
        )
        if delivery_envelope == "ASSET_RESEARCH_BRIEF_WATCHLIST":
            single_stock_notify = False
        # Issue #119: 从配置读取报告类型
        report_type_str = getattr(self.config, 'report_type', 'simple').lower()
        if report_type_str == 'brief':
            report_type = ReportType.BRIEF
        elif report_type_str == 'full':
            report_type = ReportType.FULL
        else:
            report_type = ReportType.SIMPLE
        # Issue #128: 从配置读取分析间隔
        analysis_delay = getattr(self.config, 'analysis_delay', 0)

        if single_stock_notify:
            logger.info(
                "已启用单股推送模式：分析仍并发执行，通知改为在结果收集侧串行发送（报告类型: %s）",
                report_type_str,
            )
        
        results: List[AnalysisResult] = []
        
        # 仅向显式支持新时间标记的worker传参，兼容原有调用入口。
        # 原生worker始终获取该标记，避免实时运行被误判为历史冻结回放。
        worker_observation_kwargs = (
            {"observation_time_frozen": observation_time_frozen}
            if _supports_explicit_keyword(self.process_single_stock, "observation_time_frozen")
            else {}
        )
        # 使用线程池并发处理
        # 注意：max_workers 设置较低（默认3）以避免触发反爬
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # 提交任务
            future_to_code = {
                executor.submit(
                    self.process_single_stock,
                    code,
                    skip_analysis=dry_run,
                    single_stock_notify=False,
                    report_type=report_type,  # Issue #119: 传递报告类型
                    analysis_query_id=uuid.uuid4().hex,
                    current_time=worker_reference_time,
                    **worker_observation_kwargs,
                ): code
                for code in stock_codes
            }
            
            # 收集结果
            for idx, future in enumerate(as_completed(future_to_code)):
                code = future_to_code[future]
                try:
                    result = future.result()
                    if result and result.success:
                        results.append(result)
                        if single_stock_notify and send_notification and not dry_run:
                            self._send_single_stock_notification(
                                result,
                                report_type=report_type,
                                fallback_code=code,
                            )
                    elif result and not result.success:
                        logger.warning(
                            f"[{code}] 分析结果标记为失败，不计入汇总: "
                            f"{result.error_message or '未知原因'}"
                        )

                    # Issue #128: 分析间隔 - 在个股分析和大盘分析之间添加延迟
                    if idx < len(stock_codes) - 1 and analysis_delay > 0:
                        # 注意：此 sleep 发生在“主线程收集 future 的循环”中，
                        # 并不会阻止线程池中的任务同时发起网络请求。
                        # 因此它对降低并发请求峰值的效果有限；真正的峰值主要由 max_workers 决定。
                        # 该行为目前保留（按需求不改逻辑）。
                        logger.debug(f"等待 {analysis_delay} 秒后继续下一只股票...")
                        time.sleep(analysis_delay)

                except Exception as e:
                    logger.error(f"[{code}] 任务执行失败: {e}")
        
        # 统计
        elapsed_time = time.time() - start_time
        
        # dry-run 模式下，数据获取成功即视为成功
        if dry_run:
            # 检查哪些股票的最新可复用交易日数据已存在
            success_count = sum(
                1
                for code in stock_codes
                if code in prefetch_codes and self.db.has_today_data(
                    code,
                    self._resolve_resume_target_date(
                        code, current_time=resume_reference_time
                    ),
                )
            )
            fail_count = len(stock_codes) - success_count
        else:
            success_count = len(results)
            fail_count = len(stock_codes) - success_count
        
        if (
            getattr(self, "b1_rights_context", None) is not None
            and not self.p0_bounded_trial
            and not dry_run
        ):
            self._assert_b1_results_product_admitted(
                results, list(stock_codes),
            )

        logger.info("===== 分析完成 =====")
        logger.info(f"成功: {success_count}, 失败: {fail_count}, 耗时: {elapsed_time:.2f} 秒")

        if self.p0_bounded_trial:
            self._finalize_p0_bounded_run(
                results,
                stock_codes,
                report_type,
                send_notification=send_notification,
            )
            return results
        
        # B1 NO_SEND may render the original Product only in memory. File/DB/
        # recipient durability remains independently gated by storage/redistribution.
        if results and not dry_run:
            if getattr(self, "b1_rights_context", None) is not None:
                self.b1_no_send_report = self._generate_aggregate_report(
                    results, report_type
                )
            else:
                self._save_local_report(results, report_type)

        # 发送通知（单股推送模式下跳过汇总推送，避免重复）
        notification_results = list(results)
        if delivery_envelope == "ASSET_RESEARCH_BRIEF_WATCHLIST":
            notification_results = [
                result
                for result in results
                if isinstance(getattr(result, "dashboard", None), dict)
                and isinstance(result.dashboard.get("research_delivery"), dict)
                and result.dashboard["research_delivery"].get("material_change") is True
            ]
            if not notification_results:
                logger.info(
                    "自选计划无材料变化；完整审计已保存，本轮不发送 WATCHLIST 通知。"
                )
        if notification_results and send_notification and not dry_run:
            if single_stock_notify:
                # 单股推送模式：只保存汇总报告，不再重复推送
                logger.info("单股推送模式：跳过汇总推送，仅保存报告到本地")
                self._send_notifications(notification_results, report_type, skip_push=True)
            elif merge_notification:
                # 合并模式（Issue #190）：仅保存，不推送，由 main 层合并个股+大盘后统一发送
                logger.info("合并推送模式：跳过本次推送，将在个股+大盘复盘后统一发送")
                self._send_notifications(notification_results, report_type, skip_push=True)
            else:
                self._send_notifications(notification_results, report_type)
        
        return results

    def _build_auto_screen_acceptance_receipt(
        self,
        result: AnalysisResult,
    ) -> Optional[Dict[str, Any]]:
        """Project one sanitized AUTO_SCREEN acceptance receipt from existing authorities."""
        context = getattr(self, "p0_acceptance_context", None)
        if not isinstance(context, dict) or not context:
            return None

        dashboard = getattr(result, "dashboard", None)
        factor = dashboard.get("factor_decision") if isinstance(dashboard, dict) else None
        canonical = factor.get("canonical_decision") if isinstance(factor, dict) else None
        if not isinstance(canonical, dict):
            raise P0BoundedTrialError("AUTO_SCREEN acceptance receipt requires canonical decision")

        canonical_receipt = {
            key: canonical.get(key)
            for key in (
                "authority",
                "action",
                "public_action",
                "evidence_state",
                "hard_veto",
                "reason_codes",
            )
        }
        return {
            "schema_version": "auto-screen-acceptance-receipt-v1",
            "github": dict(context.get("github") or {}),
            "screening": dict(context.get("screening") or {}),
            "deep_analysis": {
                "status": "success",
                "model_id": str(
                    context.get("model_id")
                    or getattr(self.config, "litellm_model", "")
                    or ""
                ),
                "model_request_count": int(
                    getattr(self.analyzer, "p0_model_request_count", 0) or 0
                ),
                "worker_count": int(self.max_workers),
                "agent_effect_count": 0,
                "search_effect_count": 0,
                "model_router_effect_count": 0,
                "model_fallback_effect_count": 0,
                "model_retry_effect_count": 0,
                "parameter_recovery_effect_count": 0,
                "integrity_completion_retry_effect_count": 0,
                "market_data_retry_fallback_scope": "allowed_outside_model_effect_boundary",
            },
            "canonical_decision": canonical_receipt,
            "notifications": {
                "suppressed": True,
                "email_count": 0,
                "telegram_count": 0,
                "other_count": 0,
            },
        }

    def _finalize_p0_bounded_run(
        self,
        results: List[AnalysisResult],
        stock_codes: List[str],
        report_type: ReportType,
        *,
        send_notification: bool = True,
    ) -> str:
        """Fail closed, save the full audit, and optionally send one compact investor Email."""
        result_codes = [str(getattr(result, "code", "") or "") for result in results]
        if (
            len(results) != len(stock_codes)
            or len(set(result_codes)) != len(result_codes)
            or set(result_codes) != set(stock_codes)
        ):
            raise P0BoundedTrialError("P0 requires every target stock to succeed exactly once")
        if report_type is not ReportType.SIMPLE:
            raise P0BoundedTrialError("P0 requires the aggregate simple report")
        for result in results:
            assert_canonical_consumer_consistency(result)

        if bool(getattr(self, "p0_receipt_only", False)):
            logger.info(
                "P0 bounded receipt-only run; report rendering, local report files, and outbound notifications are suppressed"
            )
            return "P0_RECEIPT_ONLY"

        audit_report = self._generate_aggregate_report(results, report_type)
        if not isinstance(audit_report, str) or not audit_report.strip():
            raise P0BoundedTrialError("P0 full audit report is empty")
        if not send_notification:
            self.notifier.save_report_to_file(audit_report)
            receipt = self._build_auto_screen_acceptance_receipt(results[0])
            if receipt is not None:
                logger.info(
                    "AUTO_SCREEN_ACCEPTANCE_RECEIPT_JSON=%s",
                    json.dumps(
                        receipt,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                )
            logger.info("P0 bounded audit saved; outbound notification suppressed for this acceptance run")
            return audit_report

        notification_report = self.notifier.generate_brief_report(results)
        if not isinstance(notification_report, str) or not notification_report.strip():
            raise P0BoundedTrialError("P0 investor notification report is empty")
        self.notifier.save_report_to_file(audit_report)
        guard = getattr(self.notifier, "require_current_asset_data_for_send", None)
        if callable(guard):
            try:
                guard(results)
            except ValueError as exc:
                raise P0BoundedTrialError(
                    "P0 asset data is not current for investor Email"
                ) from exc
        if not self.notifier.send_to_email(notification_report):
            raise P0BoundedTrialError("P0 investor Email send failed")
        return audit_report

    def _send_single_stock_notification(
        self,
        result: AnalysisResult,
        report_type: ReportType = ReportType.SIMPLE,
        fallback_code: Optional[str] = None,
    ) -> None:
        """发送单股通知，供直接单股入口和批量串行推送共用。"""
        if not self.notifier.is_available():
            notification_run = self._build_notification_run_snapshot(
                channel="report",
                status="not_configured",
                success=False,
                attempts=0,
            )
            record_notification_run(
                channel="report",
                status="not_configured",
                success=False,
                attempts=0,
            )
            self._refresh_saved_diagnostic_snapshot(
                result=result,
                fallback_code=fallback_code,
                notification_run=notification_run,
            )
            return

        stock_code = getattr(result, "code", None) or fallback_code or "unknown"
        notify_lock = getattr(self, "_single_stock_notify_lock", None)
        if notify_lock is None:
            with _SINGLE_STOCK_NOTIFY_LOCK_INIT_GUARD:
                notify_lock = getattr(self, "_single_stock_notify_lock", None)
                if notify_lock is None:
                    notify_lock = threading.Lock()
                    setattr(self, "_single_stock_notify_lock", notify_lock)

        with notify_lock:
            try:
                if has_canonical_action_authority_for_result(result):
                    if getattr(self.notifier, "_report_summary_only", False) is True:
                        raise ValueError("canonical research cannot use report_summary_only")
                    report_content = self._generate_aggregate_report([result], ReportType.FULL)
                    logger.info(f"[{stock_code}] 使用完整 canonical 研究报告")
                elif report_type == ReportType.FULL:
                    report_content = self.notifier.generate_dashboard_report([result])
                    logger.info(f"[{stock_code}] 使用完整报告格式")
                elif report_type == ReportType.BRIEF:
                    report_content = self.notifier.generate_brief_report([result])
                    logger.info(f"[{stock_code}] 使用简洁报告格式")
                else:
                    report_content = self.notifier.generate_brief_report([result])
                    logger.info(f"[{stock_code}] 使用投资者简报格式")

                if not isinstance(report_content, str) or not report_content.strip():
                    raise ValueError("single-stock investor notification projection is empty")

                send_kwargs: Dict[str, Any] = {
                    "email_stock_codes": [stock_code],
                    "route_type": "report",
                    "severity": "info",
                    "dedup_key": f"report:single:{stock_code}:{report_type.value}",
                    "cooldown_key": f"report:single:{stock_code}:{report_type.value}",
                }
                if _supports_explicit_keyword(self.notifier.send, "structured_payload"):
                    send_kwargs["structured_payload"] = _share_image_payload(result)
                if _supports_explicit_keyword(self.notifier.send, "asset_results"):
                    send_kwargs["asset_results"] = [result]
                sent = self.notifier.send(report_content, **send_kwargs)
                notification_run = self._build_notification_run_snapshot(
                    channel="report",
                    status="success" if sent else "failed",
                    success=sent,
                )
                record_notification_run(
                    channel="report",
                    status="success" if sent else "failed",
                    success=sent,
                )
                self._refresh_saved_diagnostic_snapshot(
                    result=result,
                    fallback_code=fallback_code,
                    notification_run=notification_run,
                )
                if sent:
                    logger.info(f"[{stock_code}] 单股推送成功")
                else:
                    logger.warning(f"[{stock_code}] 单股推送失败")
            except Exception as e:
                notification_run = self._build_notification_run_snapshot(
                    channel="report",
                    status="failed",
                    success=False,
                    error_message=e,
                )
                record_notification_run(
                    channel="report",
                    status="failed",
                    success=False,
                    error_message=e,
                )
                self._refresh_saved_diagnostic_snapshot(
                    result=result,
                    fallback_code=fallback_code,
                    notification_run=notification_run,
                )
                logger.error(f"[{stock_code}] 单股推送异常: {e}")

    def _save_local_report(
        self,
        results: List[AnalysisResult],
        report_type: ReportType = ReportType.SIMPLE,
    ) -> None:
        """保存分析报告到本地文件（与通知推送解耦）"""
        try:
            report = self._generate_aggregate_report(results, report_type)
            delivery_envelope = str(
                (getattr(self, "research_selection_context", None) or {}).get(
                    "delivery_envelope"
                )
                or ""
            )
            filename = None
            if delivery_envelope == "ASSET_RESEARCH_BRIEF_WATCHLIST":
                filename = f"report_{datetime.now().strftime('%Y%m%d')}_watchlist.md"
            filepath = self.notifier.save_report_to_file(report, filename=filename)
            logger.info(f"决策仪表盘日报已保存: {filepath}")
        except Exception as e:
            logger.error(f"保存本地报告失败: {e}")

    def _send_notifications(
        self,
        results: List[AnalysisResult],
        report_type: ReportType = ReportType.SIMPLE,
        skip_push: bool = False,
    ) -> None:
        """
        发送分析结果通知
        
        生成决策仪表盘格式的报告
        
        Args:
            results: 分析结果列表
            skip_push: 是否跳过推送（仅保存到本地，用于单股推送模式）
        """
        noise_decision = None
        noise_finalized = False
        try:
            logger.info("生成决策仪表盘日报...")
            report = self._generate_aggregate_report(results, report_type)

            # 跳过推送（单股推送模式 / 合并模式：报告已由 _save_local_report 保存）
            if skip_push:
                notification_run = self._build_notification_run_snapshot(
                    channel="report",
                    status="skipped",
                    success=False,
                    attempts=0,
                )
                record_notification_run(
                    channel="report",
                    status="skipped",
                    success=False,
                    attempts=0,
                )
                self._refresh_saved_diagnostic_snapshot(
                    results=results,
                    notification_run=notification_run,
                )
                return
            
            # 推送通知
            if self.notifier.is_available():
                freshness_guard = getattr(
                    self.notifier, "require_current_asset_data_for_send", None,
                )
                if results and callable(freshness_guard):
                    freshness_guard(results)
                channels = self.notifier.get_available_channels()
                channels = self.notifier.get_channels_for_route("report", channels=channels)
                # Existing report consumer keeps only required Email plus
                # optional Telegram, even if a legacy adapter returns others.
                channels = [
                    ch for ch in (NotificationChannel.EMAIL, NotificationChannel.TELEGRAM)
                    if ch in channels
                ]
                if NotificationChannel.EMAIL not in channels:
                    raise ValueError("Email is required for full research reports")
                email_results: List[bool] = []


                # Complete research Email must consume the same full evidence
                # as the saved Report, including canonical SIMPLE asset runs.
                # A report_type=BRIEF flag cannot override canonical Product
                # completeness. Only noncanonical legacy routes remain scoped.
                # The immutable V2.5 original is not rendered by this route.
                full_email_required = (
                    report_type == ReportType.FULL
                    or any(
                        has_canonical_action_authority_for_result(item)
                        for item in results
                    )
                )
                if (
                    full_email_required
                    and any(
                        channel in channels
                        for channel in (NotificationChannel.EMAIL, NotificationChannel.TELEGRAM)
                    )
                    and getattr(self.notifier, "_report_summary_only", False) is True
                ):
                    raise ValueError(
                        "full research Email cannot use report_summary_only"
                    )
                compact_consumers = (
                    {NotificationChannel.TELEGRAM, NotificationChannel.EMAIL}
                    if not full_email_required else set()
                )
                compact_required = any(channel in compact_consumers for channel in channels)
                notification_report = report
                if compact_required and report_type != ReportType.BRIEF:
                    notification_report = self.notifier.generate_brief_report(results)
                if compact_required and (
                    not isinstance(notification_report, str)
                    or not notification_report.strip()
                ):
                    raise ValueError("investor notification projection is empty")

                subject_builder = getattr(
                    self.notifier,
                    "build_research_email_subject",
                    None,
                )
                email_subject = (
                    subject_builder(results)
                    if callable(subject_builder)
                    else None
                )

                stale_during_fanout = False

                def _send_channel_safely(
                    channel_label: str,
                    send_func: Callable[[], bool],
                ) -> tuple[bool, Optional[Exception]]:
                    nonlocal stale_during_fanout
                    if stale_during_fanout:
                        return False, ValueError("asset report expired before outbound channel")
                    if results and callable(freshness_guard):
                        try:
                            freshness_guard(results)
                        except ValueError as exc:
                            stale_during_fanout = True
                            logger.warning("Aggregate asset report expired before %s: %s", channel_label, exc)
                            return False, exc
                    try:
                        return bool(send_func()), None
                    except Exception as e:
                        logger.exception(
                            "通知渠道 %s 推送异常，继续尝试其他渠道: %s",
                            channel_label,
                            e,
                        )
                        return False, e

                def _record_channel_result(
                    channel_label: str,
                    success: bool,
                    error_message: Optional[Exception] = None,
                    target_results: Optional[List[AnalysisResult]] = None,
                ) -> None:
                    if (
                        channel_label == NotificationChannel.EMAIL.value
                        or channel_label.startswith(NotificationChannel.EMAIL.value + ":")
                    ):
                        email_results.append(bool(success))
                    notification_run = self._build_notification_run_snapshot(
                        channel=channel_label,
                        status="success" if success else "failed",
                        success=success,
                        error_message=error_message,
                    )
                    record_notification_run(
                        channel=channel_label,
                        status="success" if success else "failed",
                        success=success,
                        error_message=error_message,
                    )
                    self._refresh_saved_diagnostic_snapshot(
                        results=results if target_results is None else target_results,
                        notification_run=notification_run,
                    )

                # B8 transport is an existing DSA Product consumer.  Current
                # strict method receipts may leave only with the same FULL
                # bytes generated by THIS notifier for THIS ordered canonical
                # result set. Legacy/P0 fixtures do not create Production proof.
                strict_payload_required = any(
                    isinstance(getattr(item, "dashboard", None), dict)
                    and isinstance(item.dashboard.get("factor_decision"), dict)
                    and item.dashboard["factor_decision"].get(
                        "method_execution_receipt_policy"
                    ) == "REQUIRED"
                    for item in results
                )
                if strict_payload_required:
                    payload_guard = getattr(
                        self.notifier, "require_current_report_payload", None,
                    )
                    if not callable(payload_guard):
                        raise ValueError("strict canonical final payload consumer missing")
                    payload_guard(report, results)
                    if notification_report != report:
                        raise ValueError("strict canonical notification was replaced")

                # Formal Report is required to reach Email. An interactive
                # chat reply must not suppress or substitute for that channel.
                send_context = False

                if channels and hasattr(self.notifier, "evaluate_noise_control"):
                    report_type_key = report_type.value if isinstance(report_type, ReportType) else str(report_type)
                    codes_key = ",".join(
                        sorted(str(getattr(result, "code", "") or "") for result in results)
                    )
                    noise_key = f"report:aggregate:{report_type_key}:{codes_key}"
                    noise_decision = self.notifier.evaluate_noise_control(
                        report,
                        route_type="report",
                        severity="info",
                        dedup_key=noise_key,
                        cooldown_key=noise_key,
                    )
                    if not noise_decision.should_send:
                        notification_run = self._build_notification_run_snapshot(
                            channel="report",
                            status="skipped",
                            success=False,
                            attempts=0,
                        )
                        record_notification_run(
                            channel="report",
                            status="skipped",
                            success=False,
                            attempts=0,
                        )
                        self._refresh_saved_diagnostic_snapshot(
                            results=results,
                            notification_run=notification_run,
                        )
                        logger.info(noise_decision.message)
                        return

                # Issue #455: Markdown 转图片（与 notification.send 逻辑一致）
                from src.md2img import markdown_to_image

                channels_needing_image = {
                    ch for ch in channels
                    if ch.value in self.notifier._markdown_to_image_channels
                    and ch not in {NotificationChannel.NTFY, NotificationChannel.GOTIFY}
                }
                compact_image_channels = channels_needing_image & (
                    {NotificationChannel.TELEGRAM, NotificationChannel.EMAIL}
                    if not full_email_required
                    else set()
                )
                full_image_channels = channels_needing_image - {
                    NotificationChannel.WECHAT,
                    NotificationChannel.TELEGRAM,
                    NotificationChannel.EMAIL,
                }
                single_share_payload = (
                    _share_image_payload(results[0]) if len(results) == 1 else None
                )

                def _get_md2img_hint() -> str:
                    try:
                        engine = getattr(get_config(), "md2img_engine", "wkhtmltoimage")
                    except Exception:
                        engine = "wkhtmltoimage"
                    return (
                        "npm i -g markdown-to-file" if engine == "markdown-to-file"
                        else "wkhtmltopdf (apt install wkhtmltopdf / brew install wkhtmltopdf)"
                    )

                image_bytes = None
                if full_image_channels:
                    image_kwargs: Dict[str, Any] = {
                        "max_chars": self.notifier._markdown_to_image_max_chars,
                    }
                    if single_share_payload is not None:
                        image_kwargs["structured_payload"] = single_share_payload
                    image_bytes = markdown_to_image(report, **image_kwargs)
                    if image_bytes:
                        logger.info(
                            "完整报告 Markdown 已转换为图片，将向 %s 发送图片",
                            [ch.value for ch in full_image_channels],
                        )
                    else:
                        logger.warning(
                            "完整报告 Markdown 转图片失败，将回退为文本发送。请检查 MARKDOWN_TO_IMAGE_CHANNELS 配置并安装 %s",
                            _get_md2img_hint(),
                        )

                notification_image_bytes = None
                if compact_image_channels:
                    notification_image_kwargs: Dict[str, Any] = {
                        "max_chars": self.notifier._markdown_to_image_max_chars,
                    }
                    if single_share_payload is not None:
                        notification_image_kwargs["structured_payload"] = single_share_payload
                    notification_image_bytes = markdown_to_image(
                        notification_report,
                        **notification_image_kwargs,
                    )
                    if notification_image_bytes:
                        logger.info(
                            "投资者简报 Markdown 已转换为图片，将向 %s 发送图片",
                            [ch.value for ch in compact_image_channels],
                        )
                    else:
                        logger.warning(
                            "投资者简报 Markdown 转图片失败，将回退为文本发送。请检查 MARKDOWN_TO_IMAGE_CHANNELS 配置并安装 %s",
                            _get_md2img_hint(),
                        )

                # 企业微信：只发精简版（平台限制）
                wechat_success = False
                if NotificationChannel.WECHAT in channels:
                    def _send_wechat_report() -> bool:
                        if report_type == ReportType.BRIEF:
                            dashboard_content = self.notifier.generate_brief_report(results)
                        else:
                            dashboard_content = self.notifier.generate_wechat_dashboard(results)
                        logger.info(f"企业微信仪表盘长度: {len(dashboard_content)} 字符")
                        logger.debug(f"企业微信推送内容:\n{dashboard_content}")
                        wechat_image_bytes = None
                        if NotificationChannel.WECHAT in channels_needing_image:
                            wechat_image_kwargs: Dict[str, Any] = {
                                "max_chars": self.notifier._markdown_to_image_max_chars,
                            }
                            if single_share_payload is not None:
                                wechat_image_kwargs["structured_payload"] = single_share_payload
                            wechat_image_bytes = markdown_to_image(
                                dashboard_content,
                                **wechat_image_kwargs,
                            )
                            if wechat_image_bytes is None:
                                logger.warning(
                                    "企业微信 Markdown 转图片失败，将回退为文本发送。请检查 MARKDOWN_TO_IMAGE_CHANNELS 配置并安装 %s",
                                    _get_md2img_hint(),
                                )
                        use_image = self.notifier._should_use_image_for_channel(
                            NotificationChannel.WECHAT, wechat_image_bytes
                        )
                        if use_image:
                            return self.notifier._send_wechat_image(wechat_image_bytes)
                        return self.notifier.send_to_wechat(dashboard_content)

                    wechat_success, wechat_error = _send_channel_safely(
                        NotificationChannel.WECHAT.value,
                        _send_wechat_report,
                    )
                    _record_channel_result(
                        NotificationChannel.WECHAT.value,
                        wechat_success,
                        wechat_error,
                    )

                # 完整 canonical 研究在 Email/Telegram 均消费同一全文，图片不替代材料。
                non_wechat_success = False
                stock_email_groups = getattr(self.config, 'stock_email_groups', []) or []
                for channel in channels:
                    if channel == NotificationChannel.WECHAT:
                        continue
                    if channel == NotificationChannel.FEISHU:
                        def _send_feishu_report() -> bool:
                            if getattr(self.notifier, "_feishu_send_as_file", False):
                                date_str = datetime.now().strftime('%Y%m%d')
                                filepath = self.notifier.save_report_to_file(
                                    strip_hidden_markdown_metadata(report).strip(),
                                    filename=f"dashboard_{date_str}.md",
                                )
                                return self.notifier.send_feishu_file(filepath)
                            return self.notifier.send_to_feishu(report)

                        channel_success, channel_error = _send_channel_safely(
                            channel.value,
                            _send_feishu_report,
                        )
                        non_wechat_success = channel_success or non_wechat_success
                        _record_channel_result(
                            channel.value,
                            channel_success,
                            channel_error,
                        )
                    elif channel == NotificationChannel.TELEGRAM:
                        def _send_telegram_report() -> bool:
                            use_image = self.notifier._should_use_image_for_channel(
                                channel, notification_image_bytes
                            )
                            if use_image:
                                return self.notifier._send_telegram_photo(notification_image_bytes)
                            return self.notifier.send_to_telegram(notification_report)

                        channel_success, channel_error = _send_channel_safely(
                            channel.value,
                            _send_telegram_report,
                        )
                        non_wechat_success = channel_success or non_wechat_success
                        _record_channel_result(
                            channel.value,
                            channel_success,
                            channel_error,
                        )
                    elif channel == NotificationChannel.EMAIL:
                        if stock_email_groups:
                            code_to_emails: Dict[str, Optional[List[str]]] = {}
                            for r in results:
                                if r.code not in code_to_emails:
                                    canonical = normalize_stock_code(r.code)
                                    emails = []
                                    for stocks, emails_list in stock_email_groups:
                                        if canonical in stocks:
                                            emails.extend(emails_list)
                                    code_to_emails[r.code] = list(dict.fromkeys(emails)) if emails else None
                            emails_to_results: Dict[Optional[Tuple], List] = defaultdict(list)
                            for r in results:
                                recs = code_to_emails.get(r.code)
                                key = tuple(recs) if recs else None
                                emails_to_results[key].append(r)
                            for key, group_results in emails_to_results.items():
                                receivers = list(key) if key is not None else None

                                def _send_email_group(
                                    group_results=group_results,
                                    receivers=receivers,
                                ) -> bool:
                                    grp_report = (
                                        self._generate_aggregate_report(group_results, report_type)
                                        if full_email_required
                                        else self.notifier.generate_brief_report(group_results)
                                    )
                                    if not isinstance(grp_report, str) or not grp_report.strip():
                                        raise ValueError("email-group investor notification projection is empty")
                                    if strict_payload_required:
                                        payload_guard(grp_report, group_results)
                                    grp_image_bytes = None
                                    if (
                                        not full_email_required
                                        and channel.value in self.notifier._markdown_to_image_channels
                                    ):
                                        group_payload = (
                                            _share_image_payload(group_results[0])
                                            if len(group_results) == 1
                                            else None
                                        )
                                        group_image_kwargs: Dict[str, Any] = {
                                            "max_chars": self.notifier._markdown_to_image_max_chars,
                                        }
                                        if group_payload is not None:
                                            group_image_kwargs["structured_payload"] = group_payload
                                        grp_image_bytes = markdown_to_image(
                                            grp_report,
                                            **group_image_kwargs,
                                        )
                                    use_image = self.notifier._should_use_image_for_channel(
                                        channel, grp_image_bytes
                                    )
                                    group_subject = (
                                        subject_builder(group_results)
                                        if callable(subject_builder)
                                        else None
                                    )
                                    if use_image:
                                        image_kwargs: Dict[str, Any] = {
                                            "receivers": receivers,
                                        }
                                        if group_subject:
                                            image_kwargs["subject"] = group_subject
                                        return self.notifier._send_email_with_inline_image(
                                            grp_image_bytes,
                                            **image_kwargs,
                                        )
                                    email_kwargs: Dict[str, Any] = {
                                        "receivers": receivers,
                                    }
                                    if group_subject:
                                        email_kwargs["subject"] = group_subject
                                    return self.notifier.send_to_email(
                                        strip_hidden_markdown_metadata(grp_report).strip(),
                                        **email_kwargs,
                                    )

                                email_label = (
                                    f"{channel.value}:{','.join(receivers)}"
                                    if receivers else f"{channel.value}:default"
                                )
                                channel_success, channel_error = _send_channel_safely(
                                    email_label,
                                    _send_email_group,
                                )
                                non_wechat_success = channel_success or non_wechat_success
                                _record_channel_result(
                                    email_label,
                                    channel_success,
                                    channel_error,
                                    target_results=group_results,
                                )
                        else:
                            def _send_email_report() -> bool:
                                use_image = (
                                    not full_email_required
                                    and self.notifier._should_use_image_for_channel(
                                        channel, notification_image_bytes
                                    )
                                )
                                if use_image:
                                    if email_subject:
                                        return self.notifier._send_email_with_inline_image(
                                            notification_image_bytes,
                                            subject=email_subject,
                                        )
                                    return self.notifier._send_email_with_inline_image(
                                        notification_image_bytes
                                    )
                                email_body = (
                                    report if full_email_required else notification_report
                                )
                                if email_subject:
                                    return self.notifier.send_to_email(
                                        strip_hidden_markdown_metadata(email_body).strip(),
                                        subject=email_subject,
                                    )
                                return self.notifier.send_to_email(
                                    strip_hidden_markdown_metadata(email_body).strip()
                                )

                            channel_success, channel_error = _send_channel_safely(
                                channel.value,
                                _send_email_report,
                            )
                            non_wechat_success = channel_success or non_wechat_success
                            _record_channel_result(
                                channel.value,
                                channel_success,
                                channel_error,
                            )
                    elif channel == NotificationChannel.CUSTOM:
                        def _send_custom_report() -> bool:
                            use_image = self.notifier._should_use_image_for_channel(
                                channel, image_bytes
                            )
                            if use_image:
                                return self.notifier._send_custom_webhook_image(
                                    image_bytes, fallback_content=report
                                )
                            return self.notifier.send_to_custom(report)

                        channel_success, channel_error = _send_channel_safely(
                            channel.value,
                            _send_custom_report,
                        )
                        non_wechat_success = channel_success or non_wechat_success
                        _record_channel_result(
                            channel.value,
                            channel_success,
                            channel_error,
                        )
                    elif channel == NotificationChannel.PUSHPLUS:
                        channel_success, channel_error = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_pushplus(report),
                        )
                        non_wechat_success = channel_success or non_wechat_success
                        _record_channel_result(
                            channel.value,
                            channel_success,
                            channel_error,
                        )
                    elif channel == NotificationChannel.SERVERCHAN3:
                        channel_success, channel_error = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_serverchan3(report),
                        )
                        non_wechat_success = channel_success or non_wechat_success
                        _record_channel_result(
                            channel.value,
                            channel_success,
                            channel_error,
                        )
                    elif channel == NotificationChannel.DISCORD:
                        channel_success, channel_error = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_discord(report),
                        )
                        non_wechat_success = channel_success or non_wechat_success
                        _record_channel_result(
                            channel.value,
                            channel_success,
                            channel_error,
                        )
                    elif channel == NotificationChannel.PUSHOVER:
                        channel_success, channel_error = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_pushover(report),
                        )
                        non_wechat_success = channel_success or non_wechat_success
                        _record_channel_result(
                            channel.value,
                            channel_success,
                            channel_error,
                        )
                    elif channel == NotificationChannel.NTFY:
                        channel_success, channel_error = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_ntfy(report),
                        )
                        non_wechat_success = channel_success or non_wechat_success
                        _record_channel_result(
                            channel.value,
                            channel_success,
                            channel_error,
                        )
                    elif channel == NotificationChannel.GOTIFY:
                        channel_success, channel_error = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_gotify(report),
                        )
                        non_wechat_success = channel_success or non_wechat_success
                        _record_channel_result(
                            channel.value,
                            channel_success,
                            channel_error,
                        )
                    elif channel == NotificationChannel.ASTRBOT:
                        channel_success, channel_error = _send_channel_safely(
                            channel.value,
                            lambda: self.notifier.send_to_astrbot(report),
                        )
                        non_wechat_success = channel_success or non_wechat_success
                        _record_channel_result(
                            channel.value,
                            channel_success,
                            channel_error,
                        )
                    elif channel == NotificationChannel.SLACK:
                        def _send_slack_report() -> bool:
                            use_image = self.notifier._should_use_image_for_channel(
                                channel, image_bytes
                            )
                            if use_image and self.notifier._slack_bot_token and self.notifier._slack_channel_id:
                                return self.notifier._send_slack_image(
                                    image_bytes, fallback_content=report
                                )
                            return self.notifier.send_to_slack(report)

                        channel_success, channel_error = _send_channel_safely(
                            channel.value,
                            _send_slack_report,
                        )
                        non_wechat_success = channel_success or non_wechat_success
                        _record_channel_result(
                            channel.value,
                            channel_success,
                            channel_error,
                        )
                    else:
                        logger.warning(f"未知通知渠道: {channel}")

                has_targeted_channels = bool(channels)
                # Telegram (and any retired optional adapter) cannot turn a
                # failed Email group into a Product success.
                success = bool(email_results) and all(email_results)
                if (
                    success
                    and noise_decision is not None
                    and hasattr(self.notifier, "record_noise_control")
                ):
                    self.notifier.record_noise_control(noise_decision)
                    noise_finalized = True
                elif (
                    noise_decision is not None
                    and hasattr(self.notifier, "release_noise_control")
                ):
                    self.notifier.release_noise_control(noise_decision)
                    noise_finalized = True
                if success:
                    logger.info("决策仪表盘推送成功")
                else:
                    logger.warning("决策仪表盘推送失败")
                if not has_targeted_channels and not send_context:
                    channel_label = ",".join(channel.value for channel in channels) or "report"
                    notification_run = self._build_notification_run_snapshot(
                        channel=channel_label,
                        status="success" if success else "failed",
                        success=success,
                    )
                    record_notification_run(
                        channel=channel_label,
                        status="success" if success else "failed",
                        success=success,
                    )
                    self._refresh_saved_diagnostic_snapshot(
                        results=results,
                        notification_run=notification_run,
                    )
            else:
                notification_run = self._build_notification_run_snapshot(
                    channel="report",
                    status="not_configured",
                    success=False,
                    attempts=0,
                )
                record_notification_run(
                    channel="report",
                    status="not_configured",
                    success=False,
                    attempts=0,
                )
                self._refresh_saved_diagnostic_snapshot(
                    results=results,
                    notification_run=notification_run,
                )
                logger.info("通知渠道未配置，跳过推送")
                
        except Exception as e:
            notification_run = self._build_notification_run_snapshot(
                channel="report",
                status="failed",
                success=False,
                error_message=e,
            )
            record_notification_run(
                channel="report",
                status="failed",
                success=False,
                error_message=e,
            )
            self._refresh_saved_diagnostic_snapshot(
                results=results,
                notification_run=notification_run,
            )
            if (
                noise_decision is not None
                and not noise_finalized
                and hasattr(self.notifier, "release_noise_control")
            ):
                self.notifier.release_noise_control(noise_decision)
            import traceback
            logger.error(f"发送通知失败: {e}\n{traceback.format_exc()}")

    def _generate_aggregate_report(
        self,
        results: List[AnalysisResult],
        report_type: ReportType,
    ) -> str:
        """Use the existing full renderer whenever canonical research requires it."""
        if any(has_canonical_action_authority_for_result(item) for item in results):
            if getattr(self.notifier, "_report_summary_only", False) is True:
                raise ValueError("canonical research cannot use report_summary_only")
            if report_type == ReportType.BRIEF:
                report_type = ReportType.FULL
        generator = getattr(self.notifier, "generate_aggregate_report", None)
        if callable(generator):
            kwargs: Dict[str, Any] = {}
            bound_context = getattr(
                self, "_current_query_market_report_context", None,
            )
            if bound_context is not None:
                context_query_id = str(
                    getattr(bound_context, "query_id", "") or ""
                )
                pipeline_query_id = str(getattr(self, "query_id", "") or "")
                if (
                    pipeline_query_id and context_query_id
                    and pipeline_query_id != context_query_id
                ):
                    raise ValueError(
                        "pipeline current Market context query identity mismatch"
                    )
                trade_date = getattr(bound_context, "trade_date", None)
                if trade_date is None:
                    raise ValueError(
                        "pipeline current Market context trade date missing"
                    )
                kwargs.update({
                    "report_date": str(trade_date),
                    "market_context": bound_context,
                    "expected_query_id": pipeline_query_id or context_query_id,
                })
            elif results:
                # Direct save/send consumers may run after the local query context
                # object leaves scope. Reuse only the exact dates already sealed
                # into Factor; never default current Product research to today.
                target_dates = set()
                for result in results:
                    dashboard = getattr(result, "dashboard", None)
                    factor = (
                        dashboard.get("factor_decision")
                        if isinstance(dashboard, dict) else None
                    )
                    if not isinstance(factor, dict):
                        continue
                    shared = factor.get("shared_report_context")
                    mtf = factor.get("multi_timeframe_structure_context")
                    for value in (
                        shared.get("trade_date")
                        if isinstance(shared, dict) else None,
                        mtf.get("target_date")
                        if isinstance(mtf, dict) else None,
                    ):
                        if str(value or "").strip():
                            target_dates.add(str(value))
                if len(target_dates) > 1:
                    raise ValueError(
                        "pipeline current report target dates are inconsistent"
                    )
                if target_dates:
                    kwargs["report_date"] = next(iter(target_dates))
            return generator(results, report_type, **kwargs)
        if report_type == ReportType.BRIEF and hasattr(self.notifier, "generate_brief_report"):
            return self.notifier.generate_brief_report(results)
        return self.notifier.generate_dashboard_report(results)
