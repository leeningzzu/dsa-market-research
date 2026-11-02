# -*- coding: utf-8 -*-
from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.services.volatility_momentum_service import (
    _confirmed_divergence,
    build_completed_timeframe_volatility_momentum_context,
    build_volatility_momentum_context,
)


def _history(*, source: str | None = "Fetcher", periods: int = 80) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-02", periods=periods)
    rows = []
    for i, day in enumerate(dates):
        close = 100.0 + i * 0.35 + ((i % 7) - 3) * 0.22
        row = {
            "date": day,
            "open": close - 0.1,
            "high": close + 0.9,
            "low": close - 0.8,
            "close": close,
            "volume": 1_000_000 + i * 1000,
        }
        if source is not None:
            row["data_source"] = source
        rows.append(row)
    return pd.DataFrame(rows)


def _trend_projection():
    return SimpleNamespace(
        macd_status=SimpleNamespace(value="bullish"),
        rsi_status=SimpleNamespace(value="neutral"),
    )


def _completed_30m_history(periods: int = 320) -> pd.DataFrame:
    dates = pd.date_range("2026-03-02 10:00", periods=periods, freq="30min", tz="Asia/Shanghai")
    close = pd.Series([100.0 + i * 0.08 + ((i % 11) - 5) * 0.12 for i in range(periods)])
    return pd.DataFrame({
        "date": dates,
        "open": close - 0.10,
        "high": close + 0.75,
        "low": close - 0.65,
        "close": close,
        "volume": [100_000.0 + i * 100 for i in range(periods)],
        "data_source": ["SyntheticTypedFetcher"] * periods,
    })


def test_completed_timeframe_momentum_uses_own_bar_windows_and_no_authority():
    from src.stock_analyzer import StockTrendAnalyzer, TrendAnalysisResult
    from src.services.multi_timeframe_structure_service import (
        _macd_startup_stability,
    )
    from src.services.volatility_momentum_service import _rsi_startup_stability

    bars = _completed_30m_history()
    analyzer = StockTrendAnalyzer()
    calculated = analyzer._calculate_rsi(analyzer._calculate_macd(bars.copy()))
    trend = TrendAnalysisResult(code="510300")
    analyzer._analyze_macd(calculated, trend)
    analyzer._analyze_rsi(calculated, trend)
    macd = _macd_startup_stability(bars, analyzer)
    rsi = _rsi_startup_stability(bars, analyzer)

    result = build_completed_timeframe_volatility_momentum_context(
        stock_code="510300",
        bars=bars,
        timeframe="30m",
        target_bar_end=bars.iloc[-1]["date"],
        source_identity_hash="a" * 64,
        trend_result=trend,
        price_structure_context={"status": "MISSING", "pivots": []},
        macd_startup=macd,
        rsi_startup=rsi,
    )

    closes = bars["close"].astype(float)
    expected_roc20 = (closes.iloc[-1] / closes.iloc[-21] - 1.0) * 100.0
    expected_roc60 = (closes.iloc[-1] / closes.iloc[-61] - 1.0) * 100.0
    assert result["status"] == "READY"
    assert result["timeframe"] == "30m"
    assert result["momentum"]["roc_20_completed_bars_pct"] == pytest.approx(expected_roc20)
    assert result["momentum"]["roc_60_completed_bars_pct"] == pytest.approx(expected_roc60)
    assert result["volatility"]["true_range_sma_20_completed_bars_pct"] is not None
    assert result["volatility"]["atr_wilder_14"] is not None
    assert result["volatility"]["atr_wilder_admission"]["status"] == "READY"
    assert result["momentum"]["macd"]["admission"]["status"] == "READY"
    assert result["momentum"]["rsi"]["admission"]["status"] == "READY"
    assert result["start_bar_end"] != result["end_bar_end"]
    assert result["independent_action_authority"] is False
    assert result["strategy_admitted"] is False
    assert result["learning_admitted"] is False
    assert result["data_rights_admitted"] is False




def test_registry_consumes_macd_rsi_recursive_starts_independently_per_timeframe():
    """Non-daily MACD and RSI must have independent native receipts, not MA proxies."""
    from datetime import date, datetime, timezone
    from src.core.trading_calendar import resolve_session_range_fail_closed
    from tests.test_intraday_bar_service import _canonical_session_set
    from src.services.intraday_bar_service import aggregate_completed_intraday_bars
    from src.services.multi_timeframe_structure_service import _build_intraday_timeframe
    from src.services.evidence_traceability_registry import compile_timeframe_family_matrix
    from src.stock_analyzer import StockTrendAnalyzer

    # Business weekdays are not exchange sessions: 2026-09-25 is an
    # independently verified XSHG holiday. The original fixture falsely
    # certified that session and produced PARTIAL instead of READY.
    exchange_sessions = resolve_session_range_fail_closed(
        "cn", date(2026, 9, 1), date(2026, 9, 30)
    )
    assert exchange_sessions is not None and len(exchange_sessions) >= 20
    assert date(2026, 9, 25) not in exchange_sessions
    assert date(2026, 9, 28) in exchange_sessions
    days = exchange_sessions[:20]
    five = _canonical_session_set(days, volume_unit="share")
    frames = {}
    for timeframe in ("15m", "60m"):
        bars = aggregate_completed_intraday_bars(five, target_timeframe=timeframe)
        frames[timeframe] = _build_intraday_timeframe(
            key=timeframe, stock_code="600519", target_date=days[-1],
            bars=bars, trend_analyzer=StockTrendAnalyzer(),
            snapshot_observed_at=datetime(2026, 10, 1, 8, tzinfo=timezone.utc),
            daily_adjustment_basis="qfq",
        )
    matrix = compile_timeframe_family_matrix({
        "multi_timeframe_structure_context": {
            "status": "PARTIAL", "timeframes": frames,
        },
    })
    for timeframe in ("15m", "60m"):
        cell = next(x for x in matrix["cells"]
                    if x["timeframe"] == timeframe and x["family"] == "MOMENTUM")
        methods = {m["method_id"]: m for m in cell["method_coverage"]}
        assert "MACD" in methods and "RSI" in methods
        assert all(m["independent_action_authority"] is False for m in methods.values())
        assert all(m["runtime_source_binding"]["data_rights_admitted"] is False
                   for m in methods.values())
        if timeframe == "15m":
            assert methods["MACD"]["state"] == "READY"
            assert methods["RSI"]["state"] == "READY"
        else:
            assert methods["MACD"]["state"] == "MISSING"
            assert methods["RSI"]["state"] == "MISSING"
            assert cell["state"] != "READY"

def test_completed_timeframe_momentum_rejects_duplicate_and_untyped_source():
    bars = _completed_30m_history(80)
    duplicate = bars.copy()
    duplicate.loc[duplicate.index[-1], "date"] = duplicate.iloc[-2]["date"]
    duplicate_result = build_completed_timeframe_volatility_momentum_context(
        stock_code="510300", bars=duplicate, timeframe="30m",
        target_bar_end=bars.iloc[-1]["date"], source_identity_hash="a" * 64,
        trend_result=None, price_structure_context=None,
        macd_startup={"status": "MISSING"}, rsi_startup={"status": "MISSING"},
    )
    assert duplicate_result["status"] == "UNKNOWN"
    assert duplicate_result["reason"] == "BAR_TIME_DUPLICATE_OR_OUT_OF_ORDER"

    untyped = build_completed_timeframe_volatility_momentum_context(
        stock_code="510300", bars=bars, timeframe="30m",
        target_bar_end=bars.iloc[-1]["date"], source_identity_hash=None,
        trend_result=None, price_structure_context=None,
        macd_startup={"status": "MISSING"}, rsi_startup={"status": "MISSING"},
    )
    assert untyped["status"] == "UNKNOWN"
    assert untyped["reason"] == "TYPED_SOURCE_IDENTITY_MISSING"


def test_ready_context_reuses_existing_formulas_and_keeps_process_diagnostics_observational():
    history = _history(periods=320)
    target = history.iloc[-1]["date"].date()
    trend = _trend_projection()

    context = build_volatility_momentum_context(
        stock_code="600519",
        history=history,
        target_date=target,
        market="cn",
        trend_result=trend,
        price_structure_context={"pivots": []},
    )

    assert context["status"] == "READY"
    assert context["hard_veto"] is False
    assert context["independent_action_authority"] is False
    assert context["timeframe"] == "1d"
    assert context["volatility"]["realized_volatility_20d_annualized_pct"] is not None
    assert context["volatility"]["true_range_sma_20_pct"] is not None
    assert context["volatility"]["true_range_semantics"] == "SIMPLE_MEAN_TRUE_RANGE_20_NOT_WILDER_ATR"
    assert context["momentum"]["roc_20_pct"] is not None
    assert context["momentum"]["roc_60_pct"] is not None
    assert context["momentum"]["macd"]["formula_owner"] == "StockTrendAnalyzer._calculate_macd"
    assert context["momentum"]["rsi"]["formula_owner"] == "StockTrendAnalyzer._calculate_rsi"
    diagnostics = context["process_diagnostics"]
    assert diagnostics["classification_authority"] == "OBSERVATIONAL_ONLY"
    assert diagnostics["world_classification"] == "NOT_PERFORMED"
    assert diagnostics["automatic_strategy_switching"] is False
    assert diagnostics["variance_scaling_semantics"].endswith("NOT_SIGNIFICANCE_TEST")
    assert "ADF_STATIONARITY" in diagnostics["deferred_models"]
    assert "GARCH" in diagnostics["deferred_models"]


def _recursive_seed_counterexample():
    """Identical latest 80 completed bars, different older historical seeds."""
    import math

    dates = pd.bdate_range(end="2026-10-12", periods=280)
    recent = [100.0 + .3 * math.sin(i * .3) + i * .01 for i in range(80)]
    earlier = [60.0 + .4 * math.sin(i * .37) for i in range(200)]

    def rows(values, selected_dates):
        prefix = len(values) - 80
        return pd.DataFrame([
            {
                "date": dt, "open": v,
                "high": v + (20.0 if i < prefix else .8),
                "low": v - (20.0 if i < prefix else .8),
                "close": v, "volume": 400_000.0,
                "data_source": "SYNTHETIC_NO_MARKET_RIGHTS",
            }
            for i, (dt, v) in enumerate(zip(selected_dates, values))
        ])

    return rows(recent, dates[-80:]), rows(earlier + recent, dates)


def _native_recursive_context(frame):
    from src.stock_analyzer import StockTrendAnalyzer, TrendAnalysisResult

    analyzer = StockTrendAnalyzer()
    computed = analyzer._calculate_rsi(analyzer._calculate_macd(frame))
    trend = TrendAnalysisResult(code="SYN")
    analyzer._analyze_macd(computed, trend)
    analyzer._analyze_rsi(computed, trend)
    return build_volatility_momentum_context(
        stock_code="SYN", history=frame,
        target_date=frame.iloc[-1]["date"].date(), market="cn",
        trend_result=trend,
    )


def test_daily_recursive_short_seed_does_not_certify_whole_family_or_trading() -> None:
    """A formula has a value at 80 bars, not necessarily numerical READY."""
    short, _ = _recursive_seed_counterexample()
    item = _native_recursive_context(short)
    assert item["status"] == "PARTIAL"
    for method in ("macd", "rsi"):
        assert item["momentum"][method]["admission"]["status"] != "READY"
        assert item["momentum"][method]["status"] is None
    assert item["volatility"]["atr_wilder_admission"]["status"] != "READY"
    assert item["volatility"]["atr_wilder_14"] is None
    assert item["momentum"]["roc_20_pct"] is not None
    assert item["volatility"]["true_range_sma_20_pct"] is not None
    assert item["independent_action_authority"] is False
    assert item["historical_replay_eligible"] is False


def test_daily_rsi_cross_threshold_under_same_last_80_requires_owner_receipt() -> None:
    """Previously both histories became READY even though native RSI state differs."""
    short, full = _recursive_seed_counterexample()
    a = _native_recursive_context(short)
    b = _native_recursive_context(full)
    short_native_state = a["momentum"]["rsi"]["raw_observation"]["status"]
    long_native_state = (
        b["momentum"]["rsi"]["status"]
        if b["momentum"]["rsi"]["admission"]["status"] == "READY"
        else b["momentum"]["rsi"]["raw_observation"]["status"]
    )
    assert short_native_state != long_native_state
    assert not (
        a["momentum"]["rsi"]["admission"]["status"] == "READY"
        and b["momentum"]["rsi"]["admission"]["status"] == "READY"
        and a["momentum"]["rsi"]["status"] != b["momentum"]["rsi"]["status"]
    )
    assert a["momentum"]["rsi"]["admission"]["status"] != "READY"


def test_long_independent_daily_recursive_nontrigger_keeps_admitted_values() -> None:
    """Benign 320 bars retain native recursive values without action authority."""
    hist = _history(periods=320)
    actual = _native_recursive_context(hist)
    assert actual["status"] == "READY"
    assert actual["momentum"]["macd"]["admission"]["status"] == "READY"
    assert actual["momentum"]["rsi"]["admission"]["status"] == "READY"
    assert actual["volatility"]["atr_wilder_admission"]["status"] == "READY"
    assert actual["volatility"]["atr_wilder_14"] is not None
    assert actual["momentum"]["roc_20_pct"] is not None
    assert actual["hard_veto"] is False
    assert actual["independent_action_authority"] is False


def test_wilder_atr14_is_recursive_distinct_from_tr_sma_and_future_safe():
    history = _history(periods=90)
    target_index = 70
    target = history.iloc[target_index]["date"].date()
    prefix = history.iloc[: target_index + 1].copy()

    context = build_volatility_momentum_context(
        stock_code="600519",
        history=history,
        target_date=target,
        market="cn",
        trend_result=_trend_projection(),
        price_structure_context={"pivots": []},
    )
    volatility = context["volatility"]

    high = prefix["high"].astype(float)
    low = prefix["low"].astype(float)
    close = prefix["close"].astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    ).max(axis=1)
    expected = float(true_range.iloc[1:15].mean())
    for value in true_range.iloc[15:]:
        expected = (expected * 13.0 + float(value)) / 14.0
    expected_pct = expected / float(close.iloc[-1]) * 100.0

    assert volatility["atr_wilder_14"] is None
    assert volatility["atr_wilder_admission"]["status"] == "MISSING"
    # Original Wilder formula oracle is exact in diagnostic observation only.
    assert abs(volatility["atr_wilder_raw_observation"]["value"] - expected) < 1e-12
    assert abs(volatility["atr_wilder_raw_observation"]["pct"] - expected_pct) < 1e-12
    assert volatility["atr_wilder_period"] == 14
    assert volatility["atr_wilder_semantics"] == (
        "TR_TRANSITIONS_1_TO_14_MEAN_THEN_WILDER_RMA_ALPHA_1_OVER_14"
    )
    tr_sma_value = volatility["true_range_sma_20_pct"] * float(close.iloc[-1]) / 100.0
    assert abs(volatility["atr_wilder_raw_observation"]["value"] - tr_sma_value) > 1e-9

    future = history.copy()
    future.loc[len(future)] = {
        "date": pd.Timestamp(target) + pd.Timedelta(days=1),
        "open": 1000.0,
        "high": 1500.0,
        "low": 500.0,
        "close": 1200.0,
        "volume": 99_999_999,
        "data_source": "UNRELATED_FUTURE_TEST",
    }
    future_context = build_volatility_momentum_context(
        stock_code="600519",
        history=future,
        target_date=target,
        market="cn",
        trend_result=_trend_projection(),
        price_structure_context={"pivots": []},
    )
    assert future_context["volatility"]["atr_wilder_14"] == volatility["atr_wilder_14"]
    assert future_context["volatility"]["atr_wilder_14_pct"] == volatility["atr_wilder_14_pct"]
    assert future_context["volatility"]["atr_wilder_raw_observation"] == volatility["atr_wilder_raw_observation"]


def test_missing_mixed_source_and_target_date_fail_closed():
    missing_source = _history(source=None)
    target = missing_source.iloc[-1]["date"].date()
    trend = _trend_projection()
    partial = build_volatility_momentum_context(
        stock_code="600519", history=missing_source, target_date=target, trend_result=trend
    )
    assert partial["status"] == "PARTIAL"
    assert partial["reason"] == "SOURCE_ALIGNMENT_UNPROVEN"
    assert partial["historical_replay_eligible"] is False

    mixed = _history()
    mixed.loc[mixed.index[-1], "data_source"] = "OtherFetcher"
    mixed_trend = _trend_projection()
    mixed_context = build_volatility_momentum_context(
        stock_code="600519", history=mixed, target_date=target, trend_result=mixed_trend
    )
    assert mixed_context["status"] == "PARTIAL"

    future_target = (pd.Timestamp(target) + pd.Timedelta(days=1)).date()
    missing = build_volatility_momentum_context(
        stock_code="600519", history=mixed, target_date=future_target, trend_result=mixed_trend
    )
    assert missing["status"] == "MISSING"
    assert missing["reason"] == "TARGET_DATE_BAR_MISSING"


def test_target_date_trim_makes_full_history_equal_to_prefix_history():
    history = _history(periods=90)
    target_index = 70
    target = history.iloc[target_index]["date"].date()
    prefix = history.iloc[: target_index + 1].copy()
    price_structure = {
        "schema_version": "price-structure-v1",
        "status": "READY",
        "historical_replay_eligible": True,
        "pivots": [],
        "target_date": target.isoformat(),
    }
    full = build_volatility_momentum_context(
        stock_code="600519",
        history=history,
        target_date=target,
        trend_result=_trend_projection(),
        price_structure_context=price_structure,
    )
    prefix_only = build_volatility_momentum_context(
        stock_code="600519",
        history=prefix,
        target_date=target,
        trend_result=_trend_projection(),
        price_structure_context=price_structure,
    )
    for key in ("status", "reason", "observations", "start_date", "end_date", "source_alignment", "volatility", "momentum", "confirmed_divergence", "process_diagnostics", "historical_replay_eligible"):
        assert full[key] == prefix_only[key]


def test_builder_refuses_price_structure_from_a_different_target_date():
    history = _history()
    target = history.iloc[-1]["date"].date()
    context = build_volatility_momentum_context(
        stock_code="600519",
        history=history,
        target_date=target,
        trend_result=_trend_projection(),
        price_structure_context={
            "schema_version": "price-structure-v1",
            "status": "READY",
            "historical_replay_eligible": True,
            "target_date": history.iloc[-2]["date"].date().isoformat(),
            "pivots": [],
        },
    )
    assert context["confirmed_divergence"]["status"] == "MISSING"
    assert context["confirmed_divergence"]["reason"] == "PRICE_STRUCTURE_TARGET_DATE_UNPROVEN"


def test_confirmed_divergence_normalizes_macd_and_rsi_on_the_same_swing():
    dates = pd.bdate_range("2026-01-02", periods=8)
    indicators = pd.DataFrame(
        {
            "date": dates,
            "MACD_DIF": [-0.4, -0.3, -0.2, -0.1, -0.1, 0.1, 0.2, 0.3],
            "RSI_12": [30, 32, 34, 36, 38, 45, 48, 50],
        }
    )
    price_structure = {
        "schema_version": "price-structure-v1",
        "status": "READY",
        "historical_replay_eligible": True,
        "pivots": [
            {
                "kind": "LOW",
                "price": 10.0,
                "origin_time": dates[1].date().isoformat(),
                "confirmed_at": dates[3].date().isoformat(),
                "provisional": False,
            },
            {
                "kind": "LOW",
                "price": 9.0,
                "origin_time": dates[5].date().isoformat(),
                "confirmed_at": dates[7].date().isoformat(),
                "provisional": False,
            },
        ]
    }

    divergence = _confirmed_divergence(indicators, price_structure)

    assert divergence["state"] == "BULLISH_DIVERGENCE"
    assert divergence["confirmed_at"] == dates[7].date().isoformat()
    assert divergence["provisional"] is False
    assert {item["oscillator"] for item in divergence["signals"]} == {"MACD_DIF", "RSI_12"}
    assert len({item["correlation_group"] for item in divergence["signals"]}) == 1
    assert divergence["independent_confirmation_group_count"] == 1
    assert divergence["same_swing_double_counting"] == "PROHIBITED"


def test_divergence_refuses_unproven_price_structure_authority():
    dates = pd.bdate_range("2026-01-02", periods=8)
    indicators = pd.DataFrame({"date": dates, "MACD_DIF": range(8), "RSI_12": range(30, 38)})
    unproven = {
        "schema_version": "price-structure-v1",
        "status": "PARTIAL",
        "historical_replay_eligible": False,
        "pivots": [
            {"kind": "LOW", "price": 10.0, "origin_time": dates[1].date().isoformat(), "confirmed_at": dates[3].date().isoformat(), "provisional": False},
            {"kind": "LOW", "price": 9.0, "origin_time": dates[5].date().isoformat(), "confirmed_at": dates[7].date().isoformat(), "provisional": False},
        ],
    }
    divergence = _confirmed_divergence(indicators, unproven)
    assert divergence["status"] == "MISSING"
    assert divergence["state"] == "NONE"
    assert divergence["independent_confirmation_group_count"] == 0


def _call_named(node: ast.Call, name: str) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Name) and func.id == name
    ) or (
        isinstance(func, ast.Attribute) and func.attr == name
    )


def _forwards_named_keyword(call: ast.Call, keyword: str) -> bool:
    item = next((kw for kw in call.keywords if kw.arg == keyword), None)
    return (
        item is not None
        and isinstance(item.value, ast.Name)
        and item.value.id == keyword
    )


def _final_factor_builder_forwards_vm_context(source: str) -> bool:
    tree = ast.parse(source)
    attach = next(
        (
            node
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_attach_factor_decision_summary"
        ),
        None,
    )
    if attach is None:
        return False
    calls = [
        node
        for node in ast.walk(attach)
        if isinstance(node, ast.Call)
        and _call_named(node, "build_stock_factor_decision_summary")
    ]
    return len(calls) == 1 and _forwards_named_keyword(
        calls[0], "volatility_momentum_context"
    )


def test_pipeline_wires_one_context_into_normal_and_agent_factor_paths():
    source = (Path(__file__).resolve().parents[1] / "src" / "core" / "pipeline.py").read_text(encoding="utf-8")
    assert "from src.services.volatility_momentum_service import build_volatility_momentum_context" in source
    assert "volatility_momentum_context = build_volatility_momentum_context(" in source

    tree = ast.parse(source)
    attach_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _call_named(node, "_attach_factor_decision_summary")
    ]
    assert len(attach_calls) >= 2
    assert all(
        _forwards_named_keyword(call, "volatility_momentum_context")
        for call in attach_calls
    )
    assert _final_factor_builder_forwards_vm_context(source)


def test_pipeline_final_factor_builder_handoff_guard_detects_missing_vm_keyword():
    source = (Path(__file__).resolve().parents[1] / "src" / "core" / "pipeline.py").read_text(encoding="utf-8")
    assert _final_factor_builder_forwards_vm_context(source)

    tree = ast.parse(source)
    attach = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_attach_factor_decision_summary"
    )
    calls = [
        node
        for node in ast.walk(attach)
        if isinstance(node, ast.Call)
        and _call_named(node, "build_stock_factor_decision_summary")
    ]
    assert len(calls) == 1
    calls[0].keywords = [
        kw for kw in calls[0].keywords if kw.arg != "volatility_momentum_context"
    ]
    broken = ast.unparse(tree)
    assert not _final_factor_builder_forwards_vm_context(broken)
