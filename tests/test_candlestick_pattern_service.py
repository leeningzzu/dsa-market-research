# -*- coding: utf-8 -*-
from __future__ import annotations

import pandas as pd

from src.services.candlestick_pattern_service import build_candlestick_pattern_context


def _history(periods: int = 18, *, source: str = "Fetcher") -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-02", periods=periods)
    rows = []
    for i, day in enumerate(dates):
        close = 100.0 + i * 0.2
        rows.append(
            {
                "date": day,
                "open": close - 0.2,
                "high": close + 0.8,
                "low": close - 0.8,
                "close": close,
                "volume": 1_000_000 + i * 1000,
                "data_source": source,
            }
        )
    return pd.DataFrame(rows)


def _alignment(history: pd.DataFrame) -> dict:
    return {
        "status": "SINGLE_SOURCE",
        "sources": ["Fetcher"],
        "rows_complete": True,
        "coverage": "FULL_NORMALIZED_HISTORY",
        "observations": len(history),
        "start_date": pd.to_datetime(history.iloc[0]["date"]).date().isoformat(),
        "end_date": pd.to_datetime(history.iloc[-1]["date"]).date().isoformat(),
    }


def _price_context(
    history: pd.DataFrame,
    *,
    direction: str,
    level_kind: str,
    level: float,
    pivot_index: int = 8,
) -> dict:
    pivot = {
        "kind": level_kind,
        "price": level,
        "origin_time": pd.to_datetime(history.iloc[pivot_index]["date"]).date().isoformat(),
        "confirmed_at": pd.to_datetime(history.iloc[pivot_index + 2]["date"]).date().isoformat(),
        "provisional": False,
        "algorithm_version": "confirmed-pivot-v2",
        "config_hash": "price-structure-config",
    }
    swing = {
        "direction": direction,
        "origin_time": pd.to_datetime(history.iloc[pivot_index - 3]["date"]).date().isoformat(),
        "confirmed_at": pd.to_datetime(history.iloc[pivot_index + 2]["date"]).date().isoformat(),
        "start_kind": "HIGH" if direction == "DOWN" else "LOW",
        "start_price": level + 5 if direction == "DOWN" else level - 5,
        "end_kind": level_kind,
        "end_price": level,
    }
    return {
        "schema_version": "price-structure-v1",
        "status": "READY",
        "target_date": pd.to_datetime(history.iloc[-1]["date"]).date().isoformat(),
        "historical_replay_eligible": True,
        "source_alignment": _alignment(history),
        "pivots": [pivot],
        "swings": [swing],
        "structure_event": {"state": "NONE", "provisional": False},
    }


def _supply(history: pd.DataFrame, *, state: str = "BALANCED") -> dict:
    return {
        "status": "READY",
        "target_date": pd.to_datetime(history.iloc[-1]["date"]).date().isoformat(),
        "state": state,
    }


def test_bullish_engulfing_at_confirmed_support_has_replay_safe_lifecycle():
    history = _history()
    origin_index = 13
    support = 101.5
    history.loc[origin_index - 1, ["open", "close", "high", "low"]] = [103.0, 102.0, 103.4, 101.8]
    history.loc[origin_index, ["open", "close", "high", "low"]] = [101.8, 103.4, 103.6, 101.4]
    history.loc[origin_index + 1, ["open", "close", "high", "low"]] = [103.5, 103.9, 104.1, 103.2]
    completed = history.iloc[: origin_index + 2].copy()
    price = _price_context(completed, direction="DOWN", level_kind="LOW", level=support)
    context = build_candlestick_pattern_context(
        stock_code="600519",
        history=completed,
        target_date=pd.to_datetime(completed.iloc[-1]["date"]).date(),
        market="cn",
        price_structure_context=price,
        supply_demand_context=_supply(completed),
    )
    event = next(
        item
        for item in context["events"]
        if item["origin_time"] == pd.to_datetime(history.iloc[origin_index]["date"]).date().isoformat()
    )
    assert event["event_type"] == "BULLISH_ENGULFING"
    assert event["direction"] == "BULLISH"
    assert event["location_ref"]["state"] == "AT_CONFIRMED_SUPPORT"
    assert event["lifecycle"] == "CONFIRMED"
    assert event["confirmed_at"] == pd.to_datetime(history.iloc[origin_index + 1]["date"]).date().isoformat()
    assert event["independent_action_authority"] is False
    assert context["historical_replay_eligible"] is True


def test_bullish_engulfing_failure_is_material_only_on_failure_date():
    history = _history()
    origin_index = 13
    support = 101.5
    history.loc[origin_index - 1, ["open", "close", "high", "low"]] = [103.0, 102.0, 103.4, 101.8]
    history.loc[origin_index, ["open", "close", "high", "low"]] = [101.8, 103.4, 103.6, 101.4]
    history.loc[origin_index + 1, ["open", "close", "high", "low"]] = [102.0, 100.8, 102.2, 100.6]
    completed = history.iloc[: origin_index + 2].copy()
    price = _price_context(completed, direction="DOWN", level_kind="LOW", level=support)
    context = build_candlestick_pattern_context(
        stock_code="600519",
        history=completed,
        target_date=pd.to_datetime(completed.iloc[-1]["date"]).date(),
        market="cn",
        price_structure_context=price,
        supply_demand_context=_supply(completed),
    )
    event = next(
        item for item in context["events"]
        if item["origin_time"] == pd.to_datetime(history.iloc[origin_index]["date"]).date().isoformat()
    )
    assert event["lifecycle"] == "FAILED"
    assert event["invalidated_at"] == pd.to_datetime(completed.iloc[-1]["date"]).date().isoformat()
    assert event["material"] is True
    assert context["primary_event"]["lifecycle"] == "FAILED"


def test_same_geometry_wrong_location_is_not_material():
    history = _history()
    origin_index = len(history) - 1
    history.loc[origin_index - 1, ["open", "close", "high", "low"]] = [103.0, 102.0, 103.4, 101.8]
    history.loc[origin_index, ["open", "close", "high", "low"]] = [101.8, 103.4, 103.6, 101.4]
    price = _price_context(history, direction="DOWN", level_kind="LOW", level=90.0)
    context = build_candlestick_pattern_context(
        stock_code="600519",
        history=history,
        target_date=pd.to_datetime(history.iloc[-1]["date"]).date(),
        market="cn",
        price_structure_context=price,
        supply_demand_context=_supply(history),
    )
    assert "BULLISH_ENGULFING" in {
        item["event_type"] for item in context["target_geometry"]["directional_candidates"]
    }
    assert context["material"] is False
    assert context["summary"] is None


def test_same_structure_bar_dedup_prefers_engulfing_over_rejection():
    history = _history()
    last = len(history) - 1
    support = 99.0
    history.loc[last - 1, ["open", "close", "high", "low"]] = [103.0, 102.0, 103.2, 101.5]
    history.loc[last, ["open", "close", "high", "low"]] = [101.8, 103.2, 103.3, 98.5]
    price = _price_context(history, direction="DOWN", level_kind="LOW", level=support)
    context = build_candlestick_pattern_context(
        stock_code="600519",
        history=history,
        target_date=pd.to_datetime(history.iloc[-1]["date"]).date(),
        market="cn",
        price_structure_context=price,
        supply_demand_context=_supply(history),
    )
    target = pd.to_datetime(history.iloc[-1]["date"]).date().isoformat()
    events = [item for item in context["events"] if item["origin_time"] == target]
    assert len(events) == 1
    assert events[0]["event_type"] == "BULLISH_ENGULFING"
    assert events[0]["material"] is True


def test_target_date_volume_conflict_suppresses_material_projection_without_changing_geometry():
    history = _history()
    origin_index = len(history) - 1
    support = 101.5
    history.loc[origin_index - 1, ["open", "close", "high", "low"]] = [103.0, 102.0, 103.4, 101.8]
    history.loc[origin_index, ["open", "close", "high", "low"]] = [101.8, 103.4, 103.6, 101.4]
    price = _price_context(history, direction="DOWN", level_kind="LOW", level=support)
    context = build_candlestick_pattern_context(
        stock_code="600519",
        history=history,
        target_date=pd.to_datetime(history.iloc[-1]["date"]).date(),
        market="cn",
        price_structure_context=price,
        supply_demand_context=_supply(history, state="SUPPLY_PRESSURE"),
    )
    target_events = [
        item
        for item in context["events"]
        if item["origin_time"] == pd.to_datetime(history.iloc[-1]["date"]).date().isoformat()
    ]
    assert target_events
    assert target_events[-1]["volume_evidence_ref"]["conflict"] is True
    assert target_events[-1]["material"] is False
    assert context["material"] is False


def test_prefix_and_future_perturbation_do_not_backfill_confirmation():
    history = _history()
    origin_index = 13
    support = 101.5
    history.loc[origin_index - 1, ["open", "close", "high", "low"]] = [103.0, 102.0, 103.4, 101.8]
    history.loc[origin_index, ["open", "close", "high", "low"]] = [101.8, 103.4, 103.6, 101.4]
    target = pd.to_datetime(history.iloc[origin_index]["date"]).date()
    prefix = history.iloc[: origin_index + 1].copy()
    price_prefix = _price_context(
        prefix,
        direction="DOWN",
        level_kind="LOW",
        level=support,
        pivot_index=8,
    )
    forming = build_candlestick_pattern_context(
        stock_code="600519",
        history=history,
        target_date=target,
        market="cn",
        price_structure_context=price_prefix,
        supply_demand_context=_supply(prefix),
    )
    assert forming["primary_event"]["lifecycle"] == "FORMING"
    assert forming["primary_event"]["confirmed_at"] is None

    future = history.copy()
    future.loc[origin_index + 1, "close"] = 110.0
    still_forming = build_candlestick_pattern_context(
        stock_code="600519",
        history=future,
        target_date=target,
        market="cn",
        price_structure_context=price_prefix,
        supply_demand_context=_supply(prefix),
    )
    assert still_forming["primary_event"] == forming["primary_event"]


def test_mixed_full_history_source_and_invalid_ohlc_fail_closed():
    history = _history()
    price = _price_context(history, direction="DOWN", level_kind="LOW", level=101.5)
    mixed = history.copy()
    mixed.loc[mixed.index[0], "data_source"] = "OtherFetcher"
    context = build_candlestick_pattern_context(
        stock_code="600519",
        history=mixed,
        target_date=pd.to_datetime(mixed.iloc[-1]["date"]).date(),
        market="cn",
        price_structure_context=price,
        supply_demand_context=_supply(mixed),
    )
    assert context["status"] == "PARTIAL"
    assert context["historical_replay_eligible"] is False

    bad = history.copy()
    bad.loc[bad.index[-1], "high"] = bad.loc[bad.index[-1], "low"] - 1
    unknown = build_candlestick_pattern_context(
        stock_code="600519",
        history=bad,
        target_date=pd.to_datetime(bad.iloc[-1]["date"]).date(),
        market="cn",
        price_structure_context=price,
        supply_demand_context=_supply(bad),
    )
    assert unknown["status"] == "UNKNOWN"
    assert unknown["reason"] == "INVALID_OHLC"


def test_doji_inside_is_observable_but_neutral():
    history = _history()
    last = len(history) - 1
    history.loc[last - 1, ["open", "close", "high", "low"]] = [103.0, 103.4, 104.0, 102.0]
    history.loc[last, ["open", "close", "high", "low"]] = [103.0, 103.02, 103.8, 102.2]
    price = _price_context(history, direction="DOWN", level_kind="LOW", level=101.5)
    context = build_candlestick_pattern_context(
        stock_code="600519",
        history=history,
        target_date=pd.to_datetime(history.iloc[-1]["date"]).date(),
        market="cn",
        price_structure_context=price,
        supply_demand_context=_supply(history),
    )
    assert "DOJI_INDECISION" in context["target_geometry"]["neutral_geometry"]
    assert "INSIDE_BAR" in context["target_geometry"]["neutral_geometry"]
    assert context["independent_action_authority"] is False


def test_stable_nontrigger_is_ready_without_material_product_noise():
    history = _history()
    price = _price_context(history, direction="DOWN", level_kind="LOW", level=90.0)
    context = build_candlestick_pattern_context(
        stock_code="600519",
        history=history,
        target_date=pd.to_datetime(history.iloc[-1]["date"]).date(),
        market="cn",
        price_structure_context=price,
        supply_demand_context=_supply(history),
    )
    assert context["status"] == "READY"
    assert context["reason"] == "NO_MATERIAL_CANDLE_EVENT"
    assert context["material"] is False
    assert context["summary"] is None
    assert context["primary_event"] is None


def test_product_projection_is_exactly_once_and_canonical_decision_is_unchanged(monkeypatch):
    import src.stock_analyzer as stock_analyzer
    from src.config import Config
    from src.services.factor_decision_summary import build_stock_factor_decision_summary

    monkeypatch.setattr(stock_analyzer, "get_config", lambda: Config())
    dates = pd.bdate_range("2025-01-02", periods=90)
    close = [100 + i * 0.2 for i in range(90)]
    frame = pd.DataFrame(
        {
            "date": dates,
            "open": [value - 0.1 for value in close],
            "high": [value + 0.8 for value in close],
            "low": [value - 0.8 for value in close],
            "close": close,
            "volume": [1_000_000 + i * 1000 for i in range(90)],
        }
    )
    trend = stock_analyzer.StockTrendAnalyzer().analyze(frame, "600519")
    base_pattern = {
        "status": "READY",
        "reason": "NO_SUPPORTED_PATTERN",
        "patterns": [],
        "primary_pattern": None,
        "historical_replay_eligible": True,
    }
    candle_summary = "日线看涨吞没在确认支撑附近已确认；仅作结构上下文，不独立构成操作信号"
    with_candle = dict(
        base_pattern,
        candlestick={
            "status": "READY",
            "material": True,
            "summary": candle_summary,
            "completed_bar_only": True,
            "independent_action_authority": False,
        },
    )
    without = build_stock_factor_decision_summary(
        trend, pattern_trigger_context=base_pattern, include_canonical=True
    )
    with_event = build_stock_factor_decision_summary(
        trend, pattern_trigger_context=with_candle, include_canonical=True
    )
    assert candle_summary in with_event["sections"]["pattern_trigger"]
    assert with_event["investor_brief"]["material_events"].count(candle_summary) == 1
    assert with_event["investor_brief"]["fused_paragraph"].count(candle_summary) == 1
    assert without["canonical_decision"] == with_event["canonical_decision"]


def test_pipeline_builds_independent_candlestick_receipt_without_new_top_level_factor():
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "src" / "core" / "pipeline.py").read_text(encoding="utf-8")
    assert 'receipts["CANDLESTICK"] = build_method_execution_receipt(' in source
    assert '"CANDLESTICK",' in source
    assert 'candlestick_context = pattern_trigger_context.get("candlestick")' in source
