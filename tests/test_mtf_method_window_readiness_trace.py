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


def _context(target="2026-10-12", instrument="600519"):
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
        "stock_code": instrument,
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
    assert rows[("60m", "MACD_STARTUP_COMPARISON")]["planned_bars"] == 268
    assert rows[("60m", "MACD_STARTUP_COMPARISON")]["requested_bars"] is None
    assert rows[("60m", "MACD_STARTUP_COMPARISON")]["returned_bars"] == 268
    assert rows[("60m", "MACD_STARTUP_COMPARISON")]["consumer_path"].endswith(
        ".60m.macd_startup_stability"
    )
    assert trace["strategy_admitted"] is False
    assert trace["learning_admitted"] is False
    assert len(trace["trace_hash"]) == 64


def test_trace_downgrades_ready_claim_when_returned_bars_are_short():
    target = date(2026, 10, 12)
    context = _context(instrument="510300")
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
        context = deepcopy(_context(instrument="510300"))
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
        fundamental_context=None,  # legal no-fundamentals MTF nontrigger
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
            fundamental_context=None,  # does not synthesize quality/valuation
        )
    )
    assert changed_receipts["MTF"]["output_hash"] != receipts["MTF"]["output_hash"]


def test_trace_rejects_cross_asset_instrument_mismatch_before_method_rows():
    target = date(2026, 10, 12)
    trace = compile_mtf_method_window_readiness_trace(
        asset_route="ETF",
        instrument="510300",
        target_date=target,
        daily_window_plan=pipeline_owner._current_daily_window_plan(
            market="cn", target_date=target,
        ),
        intraday_window_plan=pipeline_owner._current_intraday_window_plan(),
        # This context is for a different security (600519), not ETF 510300.
        multi_timeframe_structure_context=_context(),
    )
    assert trace["status"] == "UNKNOWN"
    assert trace["reason"] == "MTF_INSTRUMENT_IDENTITY_MISMATCH"
    assert trace["rows"] == []
    assert trace["strategy_admitted"] is False
    assert trace["learning_admitted"] is False


def test_trace_never_invents_dispatch_from_an_acquisition_plan():
    target = date(2026, 10, 12)
    trace = compile_mtf_method_window_readiness_trace(
        asset_route="STOCK", instrument="600519", target_date=target,
        daily_window_plan=pipeline_owner._current_daily_window_plan(
            market="cn", target_date=target,
        ),
        intraday_window_plan=pipeline_owner._current_intraday_window_plan(),
        multi_timeframe_structure_context=_context(),
    )
    # A plan plus method output is not a provider invocation receipt.
    assert all(row["requested_bars"] is None for row in trace["rows"])
    assert all(row["request_evidence_status"] == "NOT_OBSERVED" for row in trace["rows"])
    assert trace["status"] == "PARTIAL"  # Do not erase legitimate method evidence.


def _actual_short_dispatch(*, wrong_instrument=False):
    from unittest.mock import patch
    import exchange_calendars as xcals
    import pandas as pd
    from tests.test_pipeline_daily_market_context import (
        _intraday_pipeline_for_helper, _pipeline_canonical_5m,
    )
    calendar = xcals.get_calendar("XSHG")
    end = calendar.date_to_session(date(2026, 10, 8))
    sessions = [bar.date() for bar in calendar.sessions_window(end, -67)]
    source = _pipeline_canonical_5m(sessions[-10:], requested_start=sessions[0])
    pipeline = _intraday_pipeline_for_helper()
    pipeline.fetcher_manager._call_fetcher_method.return_value = source
    with patch.object(pipeline_owner, "resolve_latest_completed_session_fail_closed", return_value=sessions[-1]), patch.object(pipeline_owner.SearchService, "is_index_or_etf", return_value=False):
        frames = pipeline._load_current_stock_intraday_timeframes(
            code="600000" if wrong_instrument else "600519", stock_name="SYNTHETIC",
            market="cn", target_date=sessions[-1],
            completed_daily_history=pd.DataFrame({"date": sessions}),
        )
    pipeline.fetcher_manager._call_fetcher_method.assert_called_once()
    return frames, sessions


def test_trace_joins_actual_shared_dispatch_and_short_return():
    frames, sessions = _actual_short_dispatch()
    acquisition = frames["5m"].attrs["dsa_intraday_acquisition_receipt"]
    assert acquisition["requested_session_count"] == 67
    assert acquisition["returned_base_bars"] == 480
    assert acquisition["provider_http_request_count"] is None
    context = _context(target=sessions[-1].isoformat())
    for timeframe, frame in frames.items():
        item = context["timeframes"][timeframe]
        item["observations"] = len(frame)
        for name in ("macd_startup_stability", "rsi_startup_stability"):
            item[name]["observations"] = len(frame)
            item[name]["status"] = "MISSING" if len(frame) < item[name]["required_observations"] else "READY"
    trace = compile_mtf_method_window_readiness_trace(
        asset_route="STOCK", instrument="600519", target_date=sessions[-1],
        daily_window_plan=pipeline_owner._current_daily_window_plan(market="cn", target_date=sessions[-1]),
        intraday_window_plan=pipeline_owner._current_intraday_window_plan(),
        multi_timeframe_structure_context=context, intraday_source_frames=frames,
    )
    rows = {row["timeframe"]: row for row in trace["rows"]}
    assert rows["5m"]["requested_bars"] == 3216  # Actual shared 67-session request.
    assert rows["5m"]["planned_bars"] == 288  # This method's smaller planning target.
    assert rows["5m"]["returned_bars"] == 480
    assert rows["60m"]["requested_bars"] == 268
    assert rows["60m"]["returned_bars"] == 40
    assert rows["daily"]["requested_bars"] is None
    assert rows["5m"]["request_evidence_status"] == "VERIFIED_PROVIDER_CALL"
    assert rows["5m"]["actual_request_start"] == sessions[0].isoformat()
    assert trace["strategy_admitted"] is False


def test_actual_dispatch_rejects_mutation_and_keeps_unobserved_nontrigger():
    from copy import deepcopy
    from data_provider.daily_data_identity import sha256_payload

    frames, sessions = _actual_short_dispatch()
    daily = pipeline_owner._current_daily_window_plan(market="cn", target_date=sessions[-1])
    minute = pipeline_owner._current_intraday_window_plan()

    def compile_source(source):
        return compile_mtf_method_window_readiness_trace(
            asset_route="STOCK", instrument="600519", target_date=sessions[-1],
            daily_window_plan=daily, intraday_window_plan=minute,
            multi_timeframe_structure_context=_context(target=sessions[-1].isoformat()),
            intraday_source_frames=source,
        )

    for mutation in (
        {"instrument": "510300"}, {"asset_route": "ETF"},
        {"requested_session_count": 66}, {"returned_base_bars": 481},
        {"requested_end": "2026-10-09"}, {"provider_http_request_count": 1},
    ):
        changed = deepcopy(frames)
        receipt = changed["5m"].attrs["dsa_intraday_acquisition_receipt"]
        receipt.update(mutation)
        receipt["receipt_hash"] = sha256_payload({k: v for k, v in receipt.items() if k != "receipt_hash"})
        rejected = compile_source(changed)
        assert rejected["reason"] == "ACQUISITION_BINDING_INVALID", mutation
        assert rejected["rows"] == []
    changed = deepcopy(frames)
    changed["5m"].loc[0, "close"] += 0.01
    assert compile_source(changed)["reason"] == "ACQUISITION_BINDING_INVALID"
    changed = deepcopy(frames)
    del changed["5m"].attrs["dsa_intraday_acquisition_receipt"]
    unobserved = compile_source(changed)
    assert len(unobserved["rows"]) == 14
    assert all(row["requested_bars"] is None for row in unobserved["rows"])
    assert unobserved["strategy_admitted"] is False


def test_original_intraday_loader_rejects_wrong_response_instrument():
    frames, _ = _actual_short_dispatch(wrong_instrument=True)
    assert frames == {}


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
