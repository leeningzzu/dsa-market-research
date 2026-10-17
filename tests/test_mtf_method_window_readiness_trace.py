from datetime import date
import sys
import types

from tests.litellm_stub import ensure_litellm_stub


class _NoNews:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("NEWS_NOT_USED_IN_WINDOW_READINESS_TEST")


newspaper = types.ModuleType("newspaper")
newspaper.Article = _NoNews
newspaper.Config = _NoNews
sys.modules.setdefault("newspaper", newspaper)
ensure_litellm_stub()


from src.core import pipeline as pipeline_owner
from src.services.evidence_traceability_registry import (
    compile_mtf_method_window_readiness_trace,
)


def _receipt(status, reason, observations, required):
    return {
        "status": status,
        "reason": reason,
        "observations": observations,
        "required_observations": required,
        "strategy_admitted": False,
        "learning_admitted": False,
    }


def _context(target="2026-10-12"):
    counts = {
        "monthly": 140,
        "weekly": 173,
        "daily": 815,
        "60m": 268,
        "30m": 536,
        "15m": 1072,
        "5m": 3216,
    }
    frames = {}
    for timeframe, observations in counts.items():
        frames[timeframe] = {
            "status": "READY" if timeframe in {"weekly", "daily"} else "PARTIAL",
            "observations": observations,
            "macd_startup_stability": _receipt(
                "READY", "MACD_OBSERVED_MULTI_START_NUMERIC_AND_STATE_AGREE",
                observations, pipeline_owner.macd_startup_comparison_required_bars(),
            ),
            "rsi_startup_stability": _receipt(
                "MISSING" if timeframe in {"monthly", "weekly"} else "READY",
                "RSI_MULTI_START_HISTORY_INSUFFICIENT"
                if timeframe in {"monthly", "weekly"}
                else "RSI_OBSERVED_MULTI_START_NUMERIC_AND_STATE_AGREE",
                observations, pipeline_owner.rsi_startup_comparison_required_bars(),
            ),
        }
    return {
        "stock_code": "600519",
        "target_date": target,
        "timeframes": frames,
    }


def test_trace_joins_existing_plans_actual_bars_status_and_consumers():
    target = date(2026, 10, 12)
    daily = pipeline_owner._current_daily_window_plan(market="cn", target_date=target)
    minute = pipeline_owner._current_intraday_window_plan()
    trace = compile_mtf_method_window_readiness_trace(
        asset_route="STOCK",
        instrument="600519",
        target_date=target,
        daily_window_plan=daily,
        intraday_window_plan=minute,
        multi_timeframe_structure_context=_context(),
    )
    assert trace["schema_version"] == "asset-timeframe-method-window-readiness-v1"
    assert trace["status"] == "PARTIAL"
    assert len(trace["rows"]) == 14
    rows = {(row["timeframe"], row["component"]): row for row in trace["rows"]}
    assert rows[("monthly", "MACD_STARTUP_COMPARISON")]["required_bars"] == 140
    assert rows[("monthly", "RSI_STARTUP_COMPARISON")]["required_bars"] == 268
    assert rows[("monthly", "RSI_STARTUP_COMPARISON")]["returned_bars"] == 140
    assert rows[("monthly", "RSI_STARTUP_COMPARISON")]["status"] == "MISSING"
    assert rows[("60m", "MACD_STARTUP_COMPARISON")]["requested_bars"] == 268
    assert rows[("60m", "MACD_STARTUP_COMPARISON")]["returned_bars"] == 268
    assert rows[("60m", "MACD_STARTUP_COMPARISON")]["consumer_path"].endswith(
        ".60m.macd_startup_stability"
    )
    assert trace["strategy_admitted"] is False
    assert trace["learning_admitted"] is False
    assert len(trace["trace_hash"]) == 64


def test_trace_downgrades_ready_claim_when_returned_bars_are_short():
    target = date(2026, 10, 12)
    context = _context()
    context["timeframes"]["60m"]["observations"] = 20
    context["timeframes"]["60m"]["macd_startup_stability"]["status"] = "READY"
    context["timeframes"]["60m"]["macd_startup_stability"]["observations"] = 20
    trace = compile_mtf_method_window_readiness_trace(
        asset_route="ETF",
        instrument="510300",
        target_date=target,
        daily_window_plan=pipeline_owner._current_daily_window_plan(
            market="cn", target_date=target,
        ),
        intraday_window_plan=pipeline_owner._current_intraday_window_plan(),
        multi_timeframe_structure_context=context,
    )
    row = next(
        item for item in trace["rows"]
        if item["timeframe"] == "60m"
        and item["component"] == "MACD_STARTUP_COMPARISON"
    )
    assert row["status"] == "UNKNOWN"
    assert row["reason"] == "OBSERVATIONS_BELOW_REQUIRED_BARS"
    assert trace["strategy_admitted"] is False


def test_trace_rejects_tampered_daily_plan_identity():
    from copy import deepcopy

    target = date(2026, 10, 12)
    plan = pipeline_owner._current_daily_window_plan(
        market="cn", target_date=target,
    )
    forged = deepcopy(plan)
    forged["timeframe_required_bars"]["daily"] += 1
    trace = compile_mtf_method_window_readiness_trace(
        asset_route="STOCK",
        instrument="600519",
        target_date=target,
        daily_window_plan=forged,
        intraday_window_plan=pipeline_owner._current_intraday_window_plan(),
        multi_timeframe_structure_context=_context(),
    )
    assert trace["status"] == "UNKNOWN"
    assert trace["reason"] == "DAILY_PLAN_HASH_MISMATCH"
    assert trace["rows"] == []


def test_trace_rejects_method_receipt_count_or_authority_forgery():
    from copy import deepcopy

    target = date(2026, 10, 12)
    for mutation, expected in (
        ({"required_observations": 999}, "METHOD_REQUIRED_BARS_MISMATCH"),
        ({"observations": 999}, "METHOD_OBSERVATION_COUNT_MISMATCH"),
        ({"strategy_admitted": True}, "METHOD_RECEIPT_AUTHORITY_ESCALATION"),
    ):
        context = deepcopy(_context())
        context["timeframes"]["60m"]["macd_startup_stability"].update(mutation)
        trace = compile_mtf_method_window_readiness_trace(
            asset_route="ETF",
            instrument="510300",
            target_date=target,
            daily_window_plan=pipeline_owner._current_daily_window_plan(
                market="cn", target_date=target,
            ),
            intraday_window_plan=pipeline_owner._current_intraday_window_plan(),
            multi_timeframe_structure_context=context,
        )
        row = next(
            item for item in trace["rows"]
            if item["timeframe"] == "60m"
            and item["component"] == "MACD_STARTUP_COMPARISON"
        )
        assert row["status"] == "UNKNOWN"
        assert row["reason"] == expected


def test_trace_is_content_bound_by_existing_mtf_execution_receipt():
    from copy import deepcopy

    from src.services.evidence_traceability_registry import digest

    target = date(2026, 10, 12)
    context = _context()
    context["method_window_readiness_trace"] = (
        compile_mtf_method_window_readiness_trace(
            asset_route="STOCK",
            instrument="600519",
            target_date=target,
            daily_window_plan=pipeline_owner._current_daily_window_plan(
                market="cn", target_date=target,
            ),
            intraday_window_plan=pipeline_owner._current_intraday_window_plan(),
            multi_timeframe_structure_context=context,
        )
    )
    receipts = pipeline_owner.StockAnalysisPipeline._build_direct_method_execution_receipts(
        code="600519",
        market="cn",
        asset_route="STOCK",
        target_date=target,
        completed_history_identity={},
        chip_data=None,
        canonical_trend_result=None,
        supply_demand_context=None,
        cost_structure_context=None,
        price_structure_context=None,
        volatility_momentum_context=None,
        pattern_trigger_context=None,
        multi_timeframe_structure_context=context,
    )
    assert receipts["MTF"]["output_hash"] == digest(context)
    changed = deepcopy(context)
    changed["method_window_readiness_trace"]["rows"][0]["status"] = "UNKNOWN"
    changed_receipts = (
        pipeline_owner.StockAnalysisPipeline._build_direct_method_execution_receipts(
            code="600519",
            market="cn",
            asset_route="STOCK",
            target_date=target,
            completed_history_identity={},
            chip_data=None,
            canonical_trend_result=None,
            supply_demand_context=None,
            cost_structure_context=None,
            price_structure_context=None,
            volatility_momentum_context=None,
            pattern_trigger_context=None,
            multi_timeframe_structure_context=changed,
        )
    )
    assert changed_receipts["MTF"]["output_hash"] != receipts["MTF"]["output_hash"]


def test_trace_target_mismatch_is_fail_closed_and_has_no_rows():
    target = date(2026, 10, 12)
    trace = compile_mtf_method_window_readiness_trace(
        asset_route="MARKET",
        instrument="sh.000001",
        target_date=target,
        daily_window_plan=pipeline_owner._current_daily_window_plan(
            market="cn", target_date=target,
        ),
        intraday_window_plan=pipeline_owner._current_intraday_window_plan(),
        multi_timeframe_structure_context=_context(target="2026-10-11"),
    )
    assert trace["status"] == "UNKNOWN"
    assert trace["reason"] == "TARGET_DATE_MISMATCH"
    assert trace["rows"] == []
    assert trace["strategy_admitted"] is False
