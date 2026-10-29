from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from data_provider.daily_data_identity import (
    attach_daily_data_identity,
    build_daily_data_identity,
)
from data_provider.intraday_data_identity import (
    attach_intraday_data_identity,
    build_intraday_data_identity,
)

from src.services.multi_timeframe_structure_service import (
    ALGORITHM_VERSION,
    CROSS_RUN_PERSISTENCE_POLICY,
    MA_COMPRESSION_READY_BARS,
    MA_COMPRESSION_REFERENCE_WINDOW,
    MA_LEVEL_READY_BARS,
    MA_STRUCTURE_ALGORITHM_VERSION,
    MA_STRUCTURE_CONFIG_HASH,
    MA_SLOPE_CROSS_READY_BARS,
    _human_summary,
    _ma_release_confirmation,
    build_ma_structure_evidence,
    build_multi_timeframe_structure_context,
)
from src.stock_analyzer import StockTrendAnalyzer
from src.services.pit_identity import (
    build_completed_history_identity,
    proven_adjustment_basis,
)


def _history(periods: int = 150, *, future: int = 0) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=periods + future)
    rows = []
    for index, day in enumerate(dates):
        close = 80.0 + index * 0.25 + ((index % 9) - 4) * 0.12
        rows.append(
            {
                "date": day,
                "open": close - 0.2,
                "high": close + 0.8,
                "low": close - 0.8,
                "close": close,
                "volume": 1_000_000 + index * 1000,
                "data_source": "FixtureFetcher",
            }
        )
    return pd.DataFrame(rows)


def _close_frame(values) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.bdate_range("2025-01-02", periods=len(values)).date,
            "close": [float(value) for value in values],
        }
    )


def _with_observed_identity(
    frame: pd.DataFrame,
    *,
    provider: str = "FixtureFetcher",
    basis: str = "qfq",
    requested_end: str | None = None,
) -> pd.DataFrame:
    result = frame.copy()
    result["data_source"] = provider
    identity = build_daily_data_identity(
        result,
        provider_identity=provider,
        provider_route="unit.multi-timeframe",
        actual_response_branch=f"fixture:{basis}",
        requested_adjustment_basis=basis,
        observed_adjustment_basis=basis,
        basis_evidence="unit-test",
        requested_start="2025-01-01",
        requested_end=requested_end or str(pd.to_datetime(result["date"]).max().date()),
        currency="CNY",
        volume_unit="share",
        amount_unit="CNY",
        identity_state="OBSERVED",
        observed_at=datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc),
    )
    attach_daily_data_identity(result, identity)
    return result


def _trend_result(label: str = "多头排列"):
    return SimpleNamespace(
        trend_status=SimpleNamespace(value=label),
        ma_alignment="多头排列 MA5>MA10>MA20" if "多头" in label else "均线缠绕",
        trend_strength=75.0,
        ma5=10.5,
        ma10=10.0,
        ma20=9.5,
        volume_status=SimpleNamespace(value="量能正常"),
        volume_ratio_5d=1.0,
        macd_status=SimpleNamespace(value="多头"),
        macd_signal="MACD DIF/DEA 均位于零轴上方，动量偏强",
        rsi_status=SimpleNamespace(value="中性"),
        rsi_signal="RSI中性",
    )


class _FakeTrendAnalyzer:
    def analyze(self, _frame, _code):
        return _trend_result()


def _daily_trend(_history: pd.DataFrame, _target_index: int):
    return _trend_result()


def _intraday_fixture(
    timeframe: str, target, periods: int = 40, *, volume_unit: str = "UNKNOWN", zero_reference_volume: bool = False,
    closes: list[float] | None = None,
) -> pd.DataFrame:
    end = pd.Timestamp(target).tz_localize("Asia/Shanghai") + pd.Timedelta(hours=15)
    ends = pd.date_range(end=end, periods=periods, freq="30min")
    observed = end + pd.Timedelta(hours=1)
    rows = []
    for index, bar_end in enumerate(ends):
        close = float(closes[index]) if closes is not None else 100.0 + index * 0.2
        rows.append(
            {
                "code": "600519",
                "bar_end": bar_end,
                "open": close - 0.1,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 0.0 if zero_reference_volume else 1000.0 + index,
                "session": f"{bar_end.date().isoformat()}:FIXTURE",
                "available_at": observed,
                "adjustflag": "2",
                "data_source": "BaostockFetcher",
            }
        )
    frame = pd.DataFrame(rows)
    identity = build_intraday_data_identity(
        frame,
        provider_identity="BaostockFetcher",
        provider_route="unit.intraday",
        package_version="0.9.4",
        query_identity={"code": "sh.600519", "frequency": timeframe, "adjustflag": "2"},
        requested_adjustment_basis="qfq",
        observed_adjustment_basis="qfq",
        basis_evidence="unit-test",
        timeframe=timeframe,
        timezone_name="Asia/Shanghai",
        session_calendar="XSHG",
        currency="CNY",
        volume_unit=volume_unit,
        amount_unit="UNKNOWN",
        requested_start=str(ends[0].date()),
        requested_end=str(target),
        identity_state="OBSERVED",
        observed_at=observed.tz_convert("UTC").to_pydatetime(),
        source_rows_sha256="f" * 64,
    )
    attach_intraday_data_identity(frame, identity)
    frame["intraday_data_identity_hash"] = identity["identity_hash"]
    return frame


def test_canonical_intraday_bars_fill_partial_context_without_claiming_action() -> None:
    history = _history()
    target_index = len(history) - 1
    target = history.iloc[target_index]["date"].date()
    weekly_end = history.iloc[target_index - 4]["date"].date()
    monthly_end = history.iloc[target_index - 20]["date"].date()

    def _completed(_market, _target, timeframe):
        return weekly_end if timeframe == "1w" else monthly_end if timeframe == "1mo" else None

    intraday = {key: _intraday_fixture(key, target) for key in ("60m", "30m", "15m", "5m")}
    snapshot = datetime.combine(target, datetime.max.time(), tzinfo=timezone.utc)
    with patch(
        "src.services.multi_timeframe_structure_service.resolve_completed_timeframe_bar_date",
        side_effect=_completed,
    ):
        context = build_multi_timeframe_structure_context(
            stock_code="600519",
            history=history,
            target_date=target,
            market="cn",
            trend_analyzer=_FakeTrendAnalyzer(),
            daily_trend_result=_daily_trend(history, target_index),
            daily_price_structure_context={"historical_replay_eligible": True},
            snapshot_observed_at=snapshot,
            intraday_timeframes=intraday,
        )

    for key in ("60m", "30m", "15m", "5m"):
        item = context["timeframes"][key]
        assert context["algorithm_version"] == "completed-daily-plus-intraday-context-v2"
        assert ALGORITHM_VERSION == "completed-daily-plus-intraday-context-v2"
        assert item["status"] == "PARTIAL"
        assert item["reason"] == "INTRADAY_TREND_MA_PARTIAL_CURRENT"
        assert item["trend"]["status"] == "READY"
        assert item["ma_structure"]["status"] in {"READY", "PARTIAL"}
        assert item["price_structure"]["status"] == "MISSING"
        assert item["price_structure"]["reason"] == "NO_CONFIRMED_PIVOTS"
        assert item["pattern_trigger"]["status"] == "MISSING"
        assert item["independent_action_authority"] is False
        assert item["strategy_admitted"] is False
        assert item["learning_admitted"] is False
        assert item["historical_replay_eligible"] is False
        assert f"{key}尚无已确认枢轴" in item["summary"]
        assert "形态和独立交易触发仍缺少可核依据" in item["summary"]



def test_confirmed_60m_bridge_structure_reuses_pivots_without_trading_or_volume_authority() -> None:
    """Completed sixty-minute pivot confirmation must reach the existing MTF Registry."""
    from src.services.evidence_traceability_registry import compile_timeframe_family_matrix
    from src.services.price_structure_service import _confirmed_pivots

    history = _history()
    target = history.iloc[-1]["date"].date()
    closes = [100.0 + index * 0.01 for index in range(40)]
    closes[25] = 113.0  # A confirmed HIGH only once future completed bars exist.
    minute = _intraday_fixture(
        "60m", target, periods=len(closes), closes=closes, volume_unit="UNKNOWN",
    )
    weekly_end = history.iloc[-5]["date"].date()
    monthly_end = history.iloc[-21]["date"].date()

    def completed(_market, _target, timeframe):
        return weekly_end if timeframe == "1w" else monthly_end

    with patch(
        "src.services.multi_timeframe_structure_service.resolve_completed_timeframe_bar_date",
        side_effect=completed,
    ):
        parent = build_multi_timeframe_structure_context(
            stock_code="600519", history=history, target_date=target,
            market="cn", trend_analyzer=_FakeTrendAnalyzer(),
            daily_trend_result=_daily_trend(history, len(history)-1),
            daily_price_structure_context={"historical_replay_eligible": True},
            snapshot_observed_at=datetime.combine(
                target, datetime.max.time(), tzinfo=timezone.utc,
            ),
            intraday_timeframes={"60m": minute},
        )
    item = parent["timeframes"]["60m"]
    structure = item["price_structure"]
    assert structure["status"] == "READY"
    assert structure["timeframe"] == "60m"
    assert structure["independent_action_authority"] is False
    assert structure["historical_replay_eligible"] is False
    assert "PRICE_STRUCTURE" in item["admitted_methods"]
    assert "VOLUME" not in item["admitted_methods"]
    assert item["status"] == "PARTIAL"
    assert item["strategy_admitted"] is False and item["learning_admitted"] is False
    assert item["independent_action_authority"] is False
    assert "60m已确认枢轴" in item["summary"]

    frame = minute.rename(columns={"bar_end": "date"})
    pivot_date = minute.iloc[25]["bar_end"].isoformat()
    assert all(
        p["origin_time"] != pivot_date
        for p in _confirmed_pivots(frame.iloc[:27])
    )
    assert any(
        p["origin_time"] == pivot_date
        for p in _confirmed_pivots(frame.iloc[:28])
    )
    pivot = next(
        p for p in structure["pivots"]
        if pd.Timestamp(p["origin_time"]) == pd.Timestamp(pivot_date)
    )
    assert pivot["confirmed_available_at"] >= pivot["confirmed_at"]
    matrix = compile_timeframe_family_matrix({
        "multi_timeframe_structure_context": {"timeframes": {"60m": item}},
    })
    cell = next(c for c in matrix["cells"]
                if c["timeframe"] == "60m" and c["family"] == "STRUCTURE")
    method = next(m for m in cell["method_coverage"]
                  if m["method_id"] == "MTF_PRICE_STRUCTURE")
    assert method["state"] == "READY"
    assert method["independent_action_authority"] is False


def test_30m_confirmed_pivot_reaches_registered_structure_without_action_authority() -> None:
    """The same completed native 30m bars must reach the Registry, not just a new JSON field."""
    from src.services.evidence_traceability_registry import compile_timeframe_family_matrix
    from src.services.price_structure_service import _confirmed_pivots
    from tests.test_multi_timeframe_structure_service import _intraday_fixture

    closes = [100.0 + index * .01 for index in range(40)]
    closes[25] = 113.0
    item = _volume_unit_intraday_context("UNKNOWN", closes=closes)
    structure = item["price_structure"]

    assert structure["status"] == "READY"
    assert structure["timeframe"] == "30m"
    assert structure["completed_bar_only"] is True
    assert structure["independent_action_authority"] is False
    assert item["strategy_admitted"] is False
    assert item["learning_admitted"] is False
    high = next(pivot for pivot in structure["pivots"] if pivot["kind"] == "HIGH")
    assert high["provisional"] is False
    assert pd.Timestamp(high["confirmed_at"]) > pd.Timestamp(high["origin_time"])
    assert high["invalidation"]["state"] in {"ACTIVE", "TRIGGERED"}

    target = _history().iloc[-1]["date"].date()
    minute = _intraday_fixture("30m", target, closes=closes)
    price_frame = pd.DataFrame({
        "date": minute["bar_end"],
        "high": minute["high"],
        "low": minute["low"],
        "close": minute["close"],
    })
    origin = minute.iloc[25]["bar_end"].isoformat()
    assert not any(
        pivot["origin_time"] == origin
        for pivot in _confirmed_pivots(price_frame.iloc[:27])
    )
    assert any(
        pivot["origin_time"] == origin
        for pivot in _confirmed_pivots(price_frame.iloc[:28])
    )

    matrix = compile_timeframe_family_matrix({
        "multi_timeframe_structure_context": {"timeframes": {"30m": item}},
    })
    cell = next(cell for cell in matrix["cells"]
                if cell["timeframe"] == "30m" and cell["family"] == "STRUCTURE")
    method = next(method for method in cell["method_coverage"]
                  if method["method_id"] == "MTF_PRICE_STRUCTURE")
    assert method["state"] == "READY"
    assert method["independent_action_authority"] is False
    assert cell["state"] in {"READY", "PARTIAL"}

@pytest.mark.parametrize("timeframe", ("15m", "5m"))
def test_confirmed_lower_intraday_structure_reaches_matrix_without_action_authority(
    timeframe: str,
) -> None:
    """Completed 15m/5m pivots are descriptive evidence, never strategy triggers."""
    from src.services.evidence_traceability_registry import compile_timeframe_family_matrix

    history = _history()
    target = history.iloc[-1]["date"].date()
    closes = [100.0 + index * 0.01 for index in range(40)]
    closes[25] = 113.0
    weekly_end = history.iloc[-5]["date"].date()
    monthly_end = history.iloc[-21]["date"].date()

    def completed(_market, _target, requested):
        return weekly_end if requested == "1w" else monthly_end

    with patch(
        "src.services.multi_timeframe_structure_service.resolve_completed_timeframe_bar_date",
        side_effect=completed,
    ):
        parent = build_multi_timeframe_structure_context(
            stock_code="600519",
            history=history,
            target_date=target,
            market="cn",
            trend_analyzer=_FakeTrendAnalyzer(),
            daily_trend_result=_daily_trend(history, len(history) - 1),
            daily_price_structure_context={"historical_replay_eligible": True},
            snapshot_observed_at=datetime.combine(
                target, datetime.max.time(), tzinfo=timezone.utc,
            ),
            intraday_timeframes={
                timeframe: _intraday_fixture(
                    timeframe, target, periods=len(closes),
                    closes=closes, volume_unit="share",
                )
            },
        )

    item = parent["timeframes"][timeframe]
    structure = item["price_structure"]
    assert structure["status"] == "READY"
    assert structure["timeframe"] == timeframe
    assert structure["intraday_identity_hash"] == item["intraday_identity_hash"]
    assert structure["independent_action_authority"] is False
    pivot = structure["pivots"][0]
    assert pivot["provisional"] is False
    assert pivot["origin_time"] < pivot["confirmed_at"]
    assert pivot["confirmed_available_at"] >= pivot["confirmed_at"]
    assert structure["historical_replay_eligible"] is False
    assert item["strategy_admitted"] is False
    assert item["learning_admitted"] is False
    assert item["pattern_trigger"]["status"] == "MISSING"
    assert item["pattern_trigger"]["reason"] == "INTRADAY_PATTERN_NOT_ADMITTED"
    assert "PRICE_STRUCTURE" in item["admitted_methods"]

    matrix = compile_timeframe_family_matrix({
        "multi_timeframe_structure_context": parent,
    })
    cell = next(
        row for row in matrix["cells"]
        if (row["timeframe"], row["family"]) == (timeframe, "STRUCTURE")
    )
    method = next(
        row for row in cell["method_coverage"]
        if row["method_id"] == "MTF_PRICE_STRUCTURE"
    )
    assert method["state"] == "READY"
    assert method["independent_action_authority"] is False
    assert cell["state"] in {"READY", "PARTIAL"}


def test_intraday_unknown_volume_unit_does_not_reach_volume_evidence_consumer() -> None:
    item = _volume_unit_intraday_context("UNKNOWN")
    assert item["status"] == "PARTIAL"
    assert item["trend"]["status"] == "READY"
    assert item["trend"]["ma_alignment"]
    assert item["trend"]["macd_status"] is None
    assert item["trend"]["macd_raw_observation"]["status"] == "多头"
    assert item["trend"]["rsi_status"] is None
    assert item["trend"]["rsi_raw_observation"]["signal"]
    assert "RSI" not in item["admitted_methods"]
    assert item["trend"]["volume_status"] is None
    assert item["trend"]["volume_ratio_5bar"] is None
    assert item["ma_structure"].get("volume_price_context") is None
    assert "量能正常" not in (item["summary"] or "")
    assert "VOLUME" not in item["admitted_methods"]
    assert item["strategy_admitted"] is False
    assert item["learning_admitted"] is False


def test_intraday_known_share_volume_unit_keeps_legitimate_projection() -> None:
    item = _volume_unit_intraday_context("share")
    assert item["status"] == "PARTIAL"
    assert item["trend"]["status"] == "READY"
    assert item["trend"]["volume_status"] == "量能正常"
    assert item["trend"]["volume_ratio_5bar"] == 1.0
    assert item["ma_structure"].get("volume_price_context") == "量能正常"
    assert "量能正常" in (item["summary"] or "")
    assert "VOLUME" in item["admitted_methods"]
    assert item["strategy_admitted"] is False
    assert item["learning_admitted"] is False


def test_intraday_unsupported_volume_unit_is_not_a_ready_volume_method() -> None:
    item = _volume_unit_intraday_context("unverified-lot")
    assert item["status"] == "PARTIAL"
    assert item["trend"]["volume_status"] is None
    assert item["trend"]["volume_ratio_5bar"] is None
    assert item["ma_structure"].get("volume_price_context") is None
    assert "VOLUME" not in item["admitted_methods"]
    assert item["strategy_admitted"] is False


def test_intraday_real_trend_analyzer_cannot_resurrect_unknown_volume() -> None:
    item = _volume_unit_intraday_context("UNKNOWN", analyzer=StockTrendAnalyzer())
    assert item["status"] == "PARTIAL"
    assert item["trend"]["status"] == "READY"
    assert item["trend"]["ma_alignment"]
    assert item["trend"]["macd_status"] is None
    assert item["trend"]["rsi_status"] is None
    assert item["trend"]["macd_raw_observation"]["status"]
    assert item["trend"]["rsi_raw_observation"]["status"]
    assert item["trend"]["volume_status"] is None
    assert item["trend"]["volume_ratio_5bar"] is None
    assert item["ma_structure"].get("volume_price_context") is None
    assert "VOLUME" not in item["admitted_methods"]
    assert item["strategy_admitted"] is False
    assert item["learning_admitted"] is False


def test_intraday_macd_40_bar_formula_minimum_does_not_certify_recursive_startup() -> None:
    """The real MACD producer may describe 40 bars, but cannot self-certify its seed."""
    item = _volume_unit_intraday_context("UNKNOWN", analyzer=StockTrendAnalyzer())
    assert item["status"] == "PARTIAL"
    assert item["trend"]["macd_status"] is None
    assert item["trend"]["macd_raw_observation"]["status"]  # Native raw audit retained.
    assert item["macd_startup_stability"]["status"] == "MISSING"
    assert item["macd_startup_stability"]["formula_minimum_bars"] == (
        StockTrendAnalyzer.MACD_SLOW + StockTrendAnalyzer.MACD_SIGNAL
    )
    assert item["macd_startup_stability"]["required_observations"] == 4 * (
        StockTrendAnalyzer.MACD_SLOW + StockTrendAnalyzer.MACD_SIGNAL
    )
    assert "MACD" not in item["admitted_methods"]
    assert "RSI" not in item["admitted_methods"]
    assert item["rsi_startup_stability"]["status"] == "MISSING"
    assert item["trend"]["macd_signal"] is None
    assert "MACD递归" in item["summary"]
    assert item["price_structure"]["status"] == "MISSING"
    assert item["strategy_admitted"] is False


def test_b5_native_recursive_ready_exposes_exact_owner_endpoints_only_when_converged() -> None:
    """Render values only from a READY multi-start owner, never raw 40-bar guesses."""
    from src.services.multi_timeframe_structure_service import _macd_startup_stability
    from src.services.volatility_momentum_service import _rsi_startup_stability

    frame = _history(periods=360)
    analyzer = StockTrendAnalyzer()
    macd = _macd_startup_stability(frame, analyzer)
    rsi = _rsi_startup_stability(frame, analyzer)
    assert macd["status"] == rsi["status"] == "READY"
    native_macd = analyzer._calculate_macd(frame)
    native_rsi = analyzer._calculate_rsi(frame)
    assert macd["native_endpoint_values"] == pytest.approx({
        key: float(native_macd.iloc[-1][key])
        for key in ("MACD_DIF", "MACD_DEA", "MACD_BAR")
    })
    assert rsi["reference_values"] == pytest.approx({
        f"RSI_{period}_{side}": float(native_rsi.iloc[index][f"RSI_{period}"])
        for period in (analyzer.RSI_SHORT, analyzer.RSI_MID, analyzer.RSI_LONG)
        for side, index in (("latest", -1), ("previous", -2))
    })
    assert macd["strategy_admitted"] is rsi["strategy_admitted"] is False
    assert macd["learning_admitted"] is rsi["learning_admitted"] is False

    short = _history(periods=40)
    short_macd = _macd_startup_stability(short, analyzer)
    short_rsi = _rsi_startup_stability(short, analyzer)
    assert short_macd["status"] == short_rsi["status"] == "MISSING"
    assert not short_macd.get("native_endpoint_values")
    assert not short_rsi.get("reference_values")


def test_long_intraday_macd_prefix_agreement_keeps_only_descriptive_nontrigger() -> None:
    """Long stable synthetic history keeps legal price/RSI context, not 30m authority."""
    item = _volume_unit_intraday_context(
        "UNKNOWN", analyzer=StockTrendAnalyzer(), periods=240,
    )
    stability = item["macd_startup_stability"]
    assert stability["status"] == "READY"
    assert stability["observations"] == 240
    assert stability["comparison_observations"] == 120
    assert stability["evaluated_startup_lengths"] == [120, 160, 200, 240]
    assert stability["numeric_agreement"] is True
    assert stability["startup_state_agreement"] is True
    assert stability["max_endpoint_difference"] <= stability["absolute_tolerance"]
    assert "MACD" in item["admitted_methods"]
    assert "RSI" not in item["admitted_methods"]
    assert item["rsi_startup_stability"]["status"] == "MISSING"
    assert "VOLUME" not in item["admitted_methods"]
    assert item["price_structure"]["status"] == "MISSING"
    assert item["pattern_trigger"]["status"] == "MISSING"
    assert item["strategy_admitted"] is False
    assert item["learning_admitted"] is False


def test_weekly_native_recursive_macd_rsi_not_promoted_without_own_completed_history() -> None:
    """Valid weekly MA remains informative when native recursive seeds are unproven."""
    from src.services.multi_timeframe_structure_service import _build_higher_timeframe

    history = _history(periods=410)
    dates = pd.to_datetime(history["date"])
    last_friday = dates[dates.dt.weekday == 4].iloc[-1].date()
    history = history[dates.dt.date <= last_friday].copy()
    history["date"] = pd.to_datetime(history["date"]).dt.date
    with patch(
        "src.services.multi_timeframe_structure_service.resolve_completed_timeframe_bar_date",
        return_value=last_friday,
    ):
        weekly = _build_higher_timeframe(
            key="weekly", timeframe="1w", period_freq="W-FRI",
            stock_code="600519", market="cn", target_date=last_friday,
            daily_frame=history, trend_analyzer=StockTrendAnalyzer(),
        )
    assert 40 <= weekly["observations"] < 140
    assert weekly["macd_startup_stability"]["status"] == "MISSING"
    assert weekly["rsi_startup_stability"]["status"] == "MISSING"
    assert weekly["trend"]["macd_status"] is None
    assert weekly["trend"]["rsi_status"] is None
    assert weekly["trend"]["macd_raw_observation"]["admitted"] is False
    assert weekly["trend"]["ma_alignment"]
    assert "MACD DIF/DEA" not in (weekly["summary"] or "")
    assert weekly["independent_action_authority"] is False


def test_verified_long_intraday_rsi_keeps_legitimate_descriptive_nontrigger() -> None:
    """The native method meets its own input rule, without 30m action promotion."""
    item = _volume_unit_intraday_context(
        "UNKNOWN", analyzer=StockTrendAnalyzer(), periods=320,
    )
    assert item["rsi_startup_stability"]["status"] == "READY"
    assert item["trend"]["rsi_status"]
    assert "RSI" in item["admitted_methods"]
    assert item["strategy_admitted"] is False
    assert item["learning_admitted"] is False
    assert item["price_structure"]["status"] == "MISSING"


def test_same_last_40_bars_can_flip_native_macd_cross_under_different_prior_seed() -> None:
    """Historical seed can change a MACD cross without changing any recent bar."""
    from math import cos, sin
    from src.stock_analyzer import MACDStatus, TrendAnalysisResult
    from src.services.multi_timeframe_structure_service import _macd_startup_stability

    shared = [
        100.0 + .6 * sin(index * .33 + 17 * .19)
        + .13 * cos(index * .17 + 17 * .19)
        for index in range(40)
    ]
    shared[-1] += 1.0
    extended = [60.0] * 200 + shared
    analyzer = StockTrendAnalyzer()

    def status(prices):
        full = analyzer._calculate_macd(pd.DataFrame({"close": prices}))
        observation = TrendAnalysisResult(code="SYNTHETIC_ONLY")
        analyzer._analyze_macd(full, observation)
        return observation.macd_status

    assert extended[-40:] == shared
    assert status(shared) == MACDStatus.GOLDEN_CROSS_ZERO
    assert status(extended) != MACDStatus.GOLDEN_CROSS_ZERO
    assert _macd_startup_stability(
        pd.DataFrame({"close": shared}), analyzer,
    )["status"] == "MISSING"
    longer = _macd_startup_stability(pd.DataFrame({"close": extended}), analyzer)
    assert longer["status"] == "READY"
    assert longer["strategy_admitted"] is False


def test_unverified_minute_gold_cross_never_becomes_narrative_confirmation() -> None:
    """Real MTF consumer cannot turn a native startup-sensitive cross into prose fact."""
    from math import cos, sin

    closes = [
        100.0 + .6 * sin(index * .33 + 17 * .19)
        + .13 * cos(index * .17 + 17 * .19)
        for index in range(40)
    ]
    closes[-1] += 1.0
    frame = _volume_unit_intraday_context(
        "UNKNOWN", analyzer=StockTrendAnalyzer(), closes=closes,
    )
    assert frame["trend"]["macd_status"] is None
    assert frame["trend"]["macd_raw_observation"]["status"] == "零轴上金叉"  # Original raw audit retained.
    assert frame["trend"]["macd_raw_observation"]["status"] == "零轴上金叉"
    assert frame["macd_startup_stability"]["status"] == "MISSING"
    assert frame["trend"]["macd_signal"] is None
    assert "零轴上金叉" not in frame["summary"]
    assert "MACD递归启动稳定性未验证" in frame["summary"]
    assert "MACD" not in frame["admitted_methods"]
    assert "RSI" not in frame["admitted_methods"]
    assert frame["rsi_startup_stability"]["status"] == "MISSING"
    assert frame["strategy_admitted"] is False


def test_macd_two_prefix_ready_cannot_override_distant_seed_numeric_counterexample() -> None:
    """A 70-bar two-prefix agreement cannot certify a different older price seed."""
    from src.services.multi_timeframe_structure_service import _macd_startup_stability

    recent = [100.0] * 70
    historical = [60.0] * 200 + recent
    owner = StockTrendAnalyzer()
    prior = owner._calculate_macd(pd.DataFrame({"close": historical}))
    limited = owner._calculate_macd(pd.DataFrame({"close": recent}))
    receipt = _macd_startup_stability(pd.DataFrame({"close": recent}), owner)

    assert historical[-70:] == recent
    assert receipt["formula_minimum_bars"] == 35
    assert receipt["required_observations"] == 4 * (
        StockTrendAnalyzer.MACD_SLOW + StockTrendAnalyzer.MACD_SIGNAL
    )
    assert receipt["comparison_observations"] is None
    assert receipt["evaluated_startup_lengths"] == []
    maximum_drift = max(
        abs(float(prior.iloc[-1][key]) - float(limited.iloc[-1][key]))
        for key in ("MACD_DIF", "MACD_DEA", "MACD_BAR")
    )
    independent_tolerance = max(1e-10, abs(recent[-1]) * 1e-4)
    assert maximum_drift > independent_tolerance * 2
    assert receipt["status"] == "MISSING"
    assert receipt["reason"] == "MACD_MULTI_START_HISTORY_INSUFFICIENT"
    assert receipt["unobserved_prehistory_convergence"] == "NOT_PROVEN"
    # A bounded two-start numeric comparison alone may not claim independent
    # MACD convergence; keep the exact counterexample for the next admission gate.
    assert receipt["status"] != "READY"


def test_multi_startup_state_disagreement_rejects_intraday_cross_context() -> None:
    """A genuine full-vs-short native crossover disagreement is not an action."""
    from math import cos, sin
    from src.stock_analyzer import TrendAnalysisResult
    from src.services.multi_timeframe_structure_service import _macd_startup_stability

    close = [60.0] * 70 + [
        100.0 + .4 * sin(i * .38) + .17 * cos(i * .17)
        for i in range(70)
    ]
    close[-1] -= 2.0
    analyzer = StockTrendAnalyzer()

    def native_status(values):
        frame = analyzer._calculate_macd(pd.DataFrame({"close": values}))
        result = TrendAnalysisResult(code="SYNTHETIC_OBSERVER")
        analyzer._analyze_macd(frame, result)
        return result.macd_status

    assert native_status(close) != native_status(close[-70:])
    evidence = _macd_startup_stability(pd.DataFrame({"close": close}), analyzer)
    assert evidence["evaluated_startup_lengths"] == [70, 93, 116, 140]
    assert evidence["startup_state_agreement"] is False
    assert evidence["status"] == "UNKNOWN"
    assert evidence["strategy_admitted"] is False
    assert evidence["learning_admitted"] is False


def test_multi_startup_bounded_positive_does_not_claim_older_pit_or_action() -> None:
    """Four tested starts agree on a benign trend; no universal history guarantee."""
    from src.services.multi_timeframe_structure_service import _macd_startup_stability

    close = [100 + index * .2 for index in range(240)]
    evidence = _macd_startup_stability(pd.DataFrame({"close": close}), StockTrendAnalyzer())
    assert evidence["policy_version"] == "native-macd-multiple-startup-v2"
    assert evidence["status"] == "READY"
    assert evidence["evaluated_startup_lengths"] == [120, 160, 200, 240]
    assert evidence["numeric_agreement"] is True
    assert evidence["startup_state_agreement"] is True
    assert evidence["max_endpoint_difference"] <= evidence["absolute_tolerance"]
    assert evidence["readiness_scope"] == "OBSERVED_COMPLETED_PREFIXES_ONLY"
    assert evidence["unobserved_prehistory_convergence"] == "NOT_PROVEN"
    assert evidence["strategy_admitted"] is False
    assert evidence["learning_admitted"] is False


def test_multi_startup_numeric_drift_rejects_even_when_named_state_agrees() -> None:
    """Value instability independently vetoes descriptive admission."""
    from src.services.multi_timeframe_structure_service import _macd_startup_stability

    evidence = _macd_startup_stability(
        pd.DataFrame({"close": [100.0] * 70 + [120.0] * 70}),
        StockTrendAnalyzer(),
    )
    assert evidence["evaluated_startup_lengths"] == [70, 93, 116, 140]
    assert evidence["numeric_agreement"] is False
    assert evidence["startup_state_agreement"] is True
    assert evidence["max_endpoint_difference"] > evidence["absolute_tolerance"]
    assert evidence["status"] == "UNKNOWN"
    assert evidence["strategy_admitted"] is False


def test_intraday_known_unit_without_prior_volume_denominator_is_not_ready() -> None:
    item = _volume_unit_intraday_context(
        "share", analyzer=StockTrendAnalyzer(), zero_reference_volume=True,
    )
    assert item["status"] == "PARTIAL"
    assert item["trend"]["status"] == "READY"
    assert item["trend"]["ma_alignment"]
    assert item["trend"]["volume_status"] is None
    assert item["trend"]["volume_ratio_5bar"] is None
    assert item["ma_structure"].get("volume_price_context") is None
    assert "VOLUME" not in item["admitted_methods"]
    assert item["strategy_admitted"] is False


def _volume_unit_intraday_context(
    volume_unit: str, *, analyzer=None, zero_reference_volume: bool = False,
    periods: int = 40, closes: list[float] | None = None,
) -> dict:
    history = _history()
    target = history.iloc[-1]["date"].date()
    weekly_end = history.iloc[-5]["date"].date()
    monthly_end = history.iloc[-21]["date"].date()

    def _completed(_market, _target, timeframe):
        return weekly_end if timeframe == "1w" else monthly_end if timeframe == "1mo" else None

    with patch(
        "src.services.multi_timeframe_structure_service.resolve_completed_timeframe_bar_date",
        side_effect=_completed,
    ):
        context = build_multi_timeframe_structure_context(
            stock_code="600519",
            history=history,
            target_date=target,
            market="cn",
            trend_analyzer=analyzer or _FakeTrendAnalyzer(),
            daily_trend_result=_daily_trend(history, len(history) - 1),
            daily_price_structure_context={"historical_replay_eligible": True},
            snapshot_observed_at=datetime.combine(target, datetime.max.time(), tzinfo=timezone.utc),
            intraday_timeframes={
                "30m": _intraday_fixture("30m", target, periods=periods, volume_unit=volume_unit, zero_reference_volume=zero_reference_volume, closes=closes),
            },
        )
    return context["timeframes"]["30m"]


def test_intraday_identity_timeframe_mismatch_is_unknown_not_ready() -> None:
    history = _history()
    target_index = len(history) - 1
    target = history.iloc[target_index]["date"].date()
    weekly_end = history.iloc[target_index - 4]["date"].date()
    monthly_end = history.iloc[target_index - 20]["date"].date()

    def _completed(_market, _target, timeframe):
        return weekly_end if timeframe == "1w" else monthly_end if timeframe == "1mo" else None

    with patch(
        "src.services.multi_timeframe_structure_service.resolve_completed_timeframe_bar_date",
        side_effect=_completed,
    ):
        context = build_multi_timeframe_structure_context(
            stock_code="600519",
            history=history,
            target_date=target,
            market="cn",
            trend_analyzer=_FakeTrendAnalyzer(),
            daily_trend_result=_daily_trend(history, target_index),
            daily_price_structure_context={"historical_replay_eligible": True},
            intraday_timeframes={"30m": _intraday_fixture("15m", target)},
        )
    assert context["timeframes"]["30m"]["status"] == "UNKNOWN"
    assert context["timeframes"]["30m"]["reason"] == "INTRADAY_IDENTITY_TIMEFRAME_MISMATCH"


@pytest.mark.parametrize("timeframe", ("60m", "30m", "15m", "5m"))
def test_mtfs_reject_cross_adjustment_basis_without_dropping_valid_daily(timeframe: str) -> None:
    """Independent contract: qfq daily cannot consume raw/hfq minute prices."""
    history = _with_observed_identity(
        _history(), provider="BaostockFetcher", basis="qfq",
    )
    target = history.iloc[-1]["date"].date()
    minute = _intraday_fixture(timeframe, target, volume_unit="UNKNOWN")
    original_identity = minute.attrs["intraday_data_identity"]
    snapshot_at = datetime.combine(target, datetime.max.time(), tzinfo=timezone.utc)
    common = dict(
        stock_code="600519",
        history=history,
        target_date=target,
        market="cn",
        trend_analyzer=_FakeTrendAnalyzer(),
        daily_trend_result=_trend_result(),
        daily_price_structure_context={"historical_replay_eligible": False},
        snapshot_observed_at=snapshot_at,
    )

    with patch(
        "src.services.multi_timeframe_structure_service.resolve_completed_timeframe_bar_date",
        return_value=None,
    ):
        valid = build_multi_timeframe_structure_context(
            intraday_timeframes={timeframe: minute},
            **common,
        )
    assert valid["timeframes"]["daily"]["status"] == "READY"
    assert valid["timeframes"][timeframe]["status"] == "PARTIAL"
    assert "MA" in valid["timeframes"][timeframe]["admitted_methods"]
    assert "VOLUME" not in valid["timeframes"][timeframe]["admitted_methods"]

    for incompatible_basis, adjustflag in (("raw", "3"), ("hfq", "1")):
        incompatible = minute.copy()
        incompatible["adjustflag"] = adjustflag
        bad_identity = build_intraday_data_identity(
            incompatible,
            provider_identity="BaostockFetcher",
            provider_route="unit.intraday",
            package_version="0.9.4",
            query_identity={
                "code": "sh.600519", "frequency": timeframe,
                "adjustflag": adjustflag,
            },
            requested_adjustment_basis=incompatible_basis,
            observed_adjustment_basis=incompatible_basis,
            basis_evidence="independent-contract-negative",
            timeframe=timeframe,
            timezone_name="Asia/Shanghai",
            session_calendar="XSHG",
            currency="CNY",
            volume_unit="UNKNOWN",
            amount_unit="UNKNOWN",
            requested_start=original_identity["requested_start"],
            requested_end=original_identity["requested_end"],
            identity_state="OBSERVED",
            observed_at=datetime.fromisoformat(original_identity["observed_at"]),
            source_rows_sha256="f" * 64,
        )
        attach_intraday_data_identity(incompatible, bad_identity)
        incompatible["intraday_data_identity_hash"] = bad_identity["identity_hash"]
        with patch(
            "src.services.multi_timeframe_structure_service.resolve_completed_timeframe_bar_date",
            return_value=None,
        ):
            rejected = build_multi_timeframe_structure_context(
                intraday_timeframes={timeframe: incompatible},
                **common,
            )
        assert rejected["timeframes"]["daily"]["status"] == "READY"
        assert rejected["timeframes"][timeframe]["status"] == "UNKNOWN"
        assert (
            rejected["timeframes"][timeframe]["reason"]
            == "INTRADAY_ADJUSTMENT_BASIS_MISMATCH"
        )
        assert (
            rejected["timeframes"][timeframe]["admission_rule_version"]
            == "intraday-price-basis-admission-v1"
        )


def _startup_evidence(values, lengths=(40, 60, 80, 120)):
    frame = _close_frame(values)
    return {
        length: build_ma_structure_evidence(
            frame.iloc[-length:].reset_index(drop=True),
            timeframe="daily",
        )
        for length in lengths
    }


def _assert_same_ma_output(evidence_by_length, lengths, keys):
    baseline = evidence_by_length[lengths[-1]]
    for length in lengths[:-1]:
        actual = evidence_by_length[length]
        for key in keys:
            assert actual[key] == baseline[key], (length, key, actual[key], baseline[key])


def test_ma_structure_readiness_boundaries_separate_formula_slope_and_context():
    values = [50.0 + index * 0.5 for index in range(40)]

    evidence_19 = build_ma_structure_evidence(_close_frame(values[:19]), timeframe="daily")
    evidence_20 = build_ma_structure_evidence(_close_frame(values[:20]), timeframe="daily")
    evidence_25 = build_ma_structure_evidence(_close_frame(values[:25]), timeframe="daily")
    evidence_26 = build_ma_structure_evidence(_close_frame(values[:26]), timeframe="daily")
    evidence_39 = build_ma_structure_evidence(_close_frame(values[:39]), timeframe="daily")
    evidence_40 = build_ma_structure_evidence(_close_frame(values), timeframe="daily")

    assert MA_LEVEL_READY_BARS == 20
    assert MA_SLOPE_CROSS_READY_BARS == 26
    assert MA_COMPRESSION_READY_BARS == 40
    assert MA_COMPRESSION_REFERENCE_WINDOW == 20
    assert evidence_40["method_ids"] == ("MA_SLOPE_CROSS", "MA_COMPRESSION_RELEASE")

    assert evidence_19["status"] == "MISSING"
    assert evidence_19["readiness"]["level"]["status"] == "MISSING"

    for evidence in (evidence_20, evidence_25):
        assert evidence["status"] == "PARTIAL"
        assert evidence["readiness"]["level"]["status"] == "READY"
        assert evidence["readiness"]["slope_cross"]["status"] == "MISSING"
        assert evidence["readiness"]["compression_context"]["status"] == "MISSING"

    for evidence in (evidence_26, evidence_39):
        assert evidence["status"] == "PARTIAL"
        assert evidence["state"] == "UNKNOWN"
        assert evidence["readiness"]["slope_cross"]["status"] == "READY"
        assert evidence["readiness"]["compression_context"]["status"] == "MISSING"

    assert evidence_26["readiness"]["compression_context"]["reference_observations"] == 6
    assert evidence_39["readiness"]["compression_context"]["reference_observations"] == 19
    assert evidence_40["status"] == "READY"
    assert evidence_40["readiness"]["compression_context"]["status"] == "READY"
    assert evidence_40["readiness"]["compression_context"]["reference_observations"] == 20


def test_ma_structure_invalid_denominator_is_unknown_not_neutral():
    evidence = build_ma_structure_evidence(
        _close_frame([50.0 + index * 0.2 for index in range(39)] + [0.0]),
        timeframe="daily",
    )
    assert evidence["status"] == "UNKNOWN"
    assert evidence["state"] == "UNKNOWN"
    assert evidence["reason"] == "DENOMINATOR_INVALID"


def test_ma_structure_parallel_trend_is_not_false_compression():
    frame = _close_frame([50.0 + index * 0.5 for index in range(80)])
    evidence = build_ma_structure_evidence(
        frame,
        timeframe="daily",
        trend_result=_trend_result("盘整"),
    )

    assert evidence["status"] == "READY"
    assert evidence["state"] == "WIDE_TREND"
    assert evidence["ordering"] == "BULLISH"
    assert evidence["material"] is False
    assert evidence["independent_action_authority"] is False
    assert evidence["algorithm_version"] == MA_STRUCTURE_ALGORITHM_VERSION
    assert len(evidence["config_hash"]) == 64
    assert evidence["config_hash"] == MA_STRUCTURE_CONFIG_HASH


def test_ma_structure_flattening_reaches_compressed_state_with_identity():
    values = [50.0 + index * 0.5 for index in range(45)] + [72.0] * 35
    evidence = build_ma_structure_evidence(
        _close_frame(values),
        timeframe="daily",
    )

    assert evidence["status"] == "READY"
    assert evidence["state"] == "COMPRESSED"
    assert evidence["compression_persistence"] >= 3
    assert evidence["bundle_spread_abs"] <= evidence["compression_threshold_abs"]
    assert evidence["bundle_spread_pct"] <= evidence["compression_threshold_pct"]
    assert evidence["crossing_density"] >= 0
    assert evidence["material"] is True
    assert "压缩" in evidence["summary"]
    assert evidence["correlation_group"] == "price_contraction"
    assert evidence["learning_admitted"] is False
    assert evidence["strategy_admitted"] is False
    assert evidence["completed_bar_identity"]["completed_bar_only"] is True


def test_ma_structure_startup_lengths_are_stable_for_wide_trend_branch():
    evidence = _startup_evidence([40.0 + index * 0.3 for index in range(120)])
    assert {item["state"] for item in evidence.values()} == {"WIDE_TREND"}
    _assert_same_ma_output(
        evidence,
        (40, 60, 80, 120),
        (
            "state",
            "ordering",
            "bundle_spread_abs",
            "bundle_spread_pct",
            "compression_threshold_abs",
            "compression_threshold_pct",
            "slope_vector_pct",
            "crossing_density",
        ),
    )


def test_ma_structure_startup_lengths_are_stable_for_compressed_branch():
    values = [50.0 + index * 0.5 for index in range(80)] + [89.5] * 40
    evidence = _startup_evidence(values)
    assert {item["state"] for item in evidence.values()} == {"COMPRESSED"}
    _assert_same_ma_output(
        evidence,
        (40, 60, 80, 120),
        (
            "state",
            "ordering",
            "bundle_spread_abs",
            "bundle_spread_pct",
            "compression_threshold_abs",
            "compression_threshold_pct",
            "slope_vector_pct",
            "crossing_density",
        ),
    )


def test_ma_structure_release_branch_needs_lifecycle_history_then_stabilizes():
    values = (
        [30.0 + index * 0.5 for index in range(60)]
        + [59.5] * 50
        + [59.5 + index * 1.5 for index in range(1, 11)]
    )
    evidence = _startup_evidence(values)

    assert evidence[40]["state"] not in {"RELEASING_UP", "RELEASING_DOWN"}
    assert {evidence[length]["state"] for length in (60, 80, 120)} == {"RELEASING_UP"}
    _assert_same_ma_output(
        evidence,
        (60, 80, 120),
        (
            "state",
            "ordering",
            "bundle_spread_abs",
            "bundle_spread_pct",
            "compression_threshold_abs",
            "compression_threshold_pct",
            "slope_vector_pct",
            "crossing_density",
            "provisional",
        ),
    )


def test_ma_release_confirmation_requires_structure_and_volume_context():
    structure = {"structure_event": {"state": "UP_BREAKOUT"}}
    confirmed, structure_state, volume_state = _ma_release_confirmation(
        "RELEASING_UP",
        trend_result=_trend_result(),
        structure_context=structure,
    )
    assert confirmed is False
    assert structure_state == "UP_BREAKOUT"
    assert volume_state == "量能正常"

    heavy = _trend_result()
    heavy.volume_status = SimpleNamespace(value="放量上涨")
    confirmed, _, _ = _ma_release_confirmation(
        "RELEASING_UP",
        trend_result=heavy,
        structure_context=structure,
    )
    assert confirmed is True


def test_weekly_ready_monthly_missing_and_intraday_stays_missing():
    history = _history()
    target_index = len(history) - 1
    target = history.iloc[target_index]["date"].date()
    weekly_end = history.iloc[target_index - 4]["date"].date()
    monthly_end = history.iloc[target_index - 20]["date"].date()

    def _completed(_market, _target, timeframe):
        return weekly_end if timeframe == "1w" else monthly_end if timeframe == "1mo" else None

    with patch(
        "src.services.multi_timeframe_structure_service.resolve_completed_timeframe_bar_date",
        side_effect=_completed,
    ):
        context = build_multi_timeframe_structure_context(
            stock_code="600519",
            history=history,
            target_date=target,
            market="cn",
            trend_analyzer=_FakeTrendAnalyzer(),
            daily_trend_result=_daily_trend(history, target_index),
            daily_price_structure_context={"historical_replay_eligible": True},
        )

    assert context["timeframes"]["weekly"]["status"] in {"READY", "PARTIAL"}
    assert context["timeframes"]["weekly"]["summary"]
    assert context["timeframes"]["monthly"]["status"] == "MISSING"
    assert context["timeframes"]["monthly"]["reason"] == "WARMUP_INSUFFICIENT"
    for key in ("60m", "30m", "15m", "5m"):
        assert context["timeframes"][key]["status"] == "MISSING"
        assert "ma_structure" not in context["timeframes"][key]
        assert (
            context["timeframes"][key]["reason"]
            == "INTRADAY_COMPLETED_BAR_CONTRACT_NOT_READY"
        )
    assert context["independent_action_authority"] is False
    assert context["cross_timeframe_vote_counting"] is False
    assert context["cross_run_persistence_policy"] == CROSS_RUN_PERSISTENCE_POLICY
    assert context["cross_run_persistence_eligible"] is False
    assert context["cross_run_persistence_reason"] == "ADJUSTMENT_BASIS_NOT_PERSISTED"


def test_monthly_trend_projection_requires_26_completed_bars():
    rows = []
    for index in range(26):
        year = 2023 + index // 12
        month = index % 12 + 1
        day = pd.Timestamp(year=year, month=month, day=15)
        close = 50.0 + index
        rows.append(
            {
                "date": day,
                "open": close - 0.5,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 100_000 + index * 100,
                "data_source": "FixtureFetcher",
            }
        )
    history_26 = pd.DataFrame(rows)
    history_25 = history_26.iloc[1:].reset_index(drop=True)
    target = history_26.iloc[-1]["date"].date()

    def _completed(_market, _target, timeframe):
        return target if timeframe == "1mo" else None

    def _build(history):
        with patch(
            "src.services.multi_timeframe_structure_service.resolve_completed_timeframe_bar_date",
            side_effect=_completed,
        ):
            return build_multi_timeframe_structure_context(
                stock_code="600519",
                history=history,
                target_date=target,
                market="cn",
                trend_analyzer=_FakeTrendAnalyzer(),
                daily_trend_result=_trend_result(),
                daily_price_structure_context={"historical_replay_eligible": True},
            )

    monthly_25 = _build(history_25)["timeframes"]["monthly"]
    monthly_26 = _build(history_26)["timeframes"]["monthly"]

    assert monthly_25["observations"] == 25
    assert monthly_25["trend"]["status"] == "MISSING"
    assert monthly_25["ma_structure"]["status"] == "PARTIAL"
    assert monthly_25["ma_structure"]["readiness"]["slope_cross"]["status"] == "MISSING"
    assert monthly_26["observations"] == 26
    assert monthly_26["trend"]["status"] == "READY"
    assert monthly_26["ma_structure"]["status"] == "PARTIAL"
    assert monthly_26["ma_structure"]["readiness"]["slope_cross"]["status"] == "READY"
    assert monthly_26["ma_structure"]["readiness"]["compression_context"]["status"] == "MISSING"


def test_target_date_trim_preserves_prefix_equivalence():
    prefix = _history(periods=150)
    full = _history(periods=150, future=15)
    target_index = len(prefix) - 1
    target = prefix.iloc[-1]["date"].date()
    weekly_end = prefix.iloc[-5]["date"].date()
    monthly_end = prefix.iloc[-21]["date"].date()

    def _completed(_market, _target, timeframe):
        return weekly_end if timeframe == "1w" else monthly_end if timeframe == "1mo" else None

    kwargs = dict(
        stock_code="600519",
        target_date=target,
        market="cn",
        trend_analyzer=_FakeTrendAnalyzer(),
        daily_trend_result=_daily_trend(prefix, target_index),
        daily_price_structure_context={"historical_replay_eligible": True},
    )
    with patch(
        "src.services.multi_timeframe_structure_service.resolve_completed_timeframe_bar_date",
        side_effect=_completed,
    ):
        full_context = build_multi_timeframe_structure_context(history=full, **kwargs)
        prefix_context = build_multi_timeframe_structure_context(history=prefix, **kwargs)

    assert full_context == prefix_context
    assert (
        full_context["timeframes"]["daily"]["ma_structure"]
        == prefix_context["timeframes"]["daily"]["ma_structure"]
    )


def test_missing_source_and_unproven_period_fail_closed():
    history = _history().drop(columns=["data_source"])
    target = history.iloc[-1]["date"].date()
    with patch(
        "src.services.multi_timeframe_structure_service.resolve_completed_timeframe_bar_date",
        return_value=None,
    ):
        context = build_multi_timeframe_structure_context(
            stock_code="600519",
            history=history,
            target_date=target,
            market="cn",
            trend_analyzer=_FakeTrendAnalyzer(),
            daily_trend_result=SimpleNamespace(
                trend_status=SimpleNamespace(value="盘整"), ma_alignment="均线缠绕"
            ),
            daily_price_structure_context={"historical_replay_eligible": False},
        )
    assert context["status"] == "PARTIAL"
    assert context["source_alignment"]["status"] == "UNPROVEN"
    assert context["timeframes"]["daily"]["ma_structure"]["status"] == "MISSING"
    assert context["timeframes"]["daily"]["ma_structure"]["reason"] == "SOURCE_ALIGNMENT_UNPROVEN"
    assert context["timeframes"]["weekly"]["reason"] == "COMPLETED_PERIOD_UNPROVEN"
    assert context["timeframes"]["monthly"]["reason"] == "COMPLETED_PERIOD_UNPROVEN"


def test_b5_raw_baostock_typed_identity_without_row_source_closes_strict_pit_to_mtf_receipt() -> None:
    """Real direct BaoStock leaves source in typed attrs; input and MTF must agree."""
    from src.services.evidence_traceability_registry import (
        build_method_execution_receipt, build_runtime_trace,
    )
    from src.services.multi_timeframe_structure_service import _normalize_daily_history
    from data_provider.daily_data_identity import canonical_identity_json

    rows = _history(periods=90)
    rows["amount"] = rows["volume"] * rows["close"]
    rows["pct_chg"] = [float(i % 7) / 100.0 for i in range(len(rows))]
    typed = _with_observed_identity(rows, provider="BaostockFetcher")
    direct = typed.drop(columns=["data_source"])
    assert "daily_data_identity" in direct.attrs
    assert "data_source" not in direct.columns
    target = direct.iloc[-1]["date"].date()
    observed = datetime(2026, 10, 13, 10, tzinfo=timezone.utc)
    source = build_completed_history_identity(
        direct, stock_code="600519", market="cn",
        target_date=target, observed_at=observed,
    )
    normalized = _normalize_daily_history(direct, target_date=target)
    consumed = build_completed_history_identity(
        normalized, stock_code="600519", market="cn",
        target_date=target, observed_at=observed,
    )
    assert source["provider_identity"] == "BaostockFetcher"
    assert source["adjustment_basis"] == "qfq"
    assert source["price_identity_reasons"] == []
    assert source["daily_data_identity_hash"] == consumed["daily_data_identity_hash"]
    assert source["data_snapshot_identity"] == consumed["data_snapshot_identity"]
    assert source["available_at_max"] >= "2026-10-13T10:00:00"

    with patch(
        "src.services.multi_timeframe_structure_service.resolve_completed_timeframe_bar_date",
        return_value=None,
    ):
        mtf = build_multi_timeframe_structure_context(
            stock_code="600519", history=direct, target_date=target,
            market="cn", trend_analyzer=_FakeTrendAnalyzer(),
            daily_trend_result=_trend_result(),
            snapshot_observed_at=observed,
        )
    assert mtf["data_snapshot_identity"] == source["data_snapshot_identity"]
    receipt = build_method_execution_receipt(
        "MTF", output=mtf, asset_route="STOCK", stock_code="600519",
        market="cn", target_date=target, timeframe="multi",
        input_identity={**source, "stock_code": "600519", "market": "cn",
                        "target_date": target.isoformat()},
    )
    factor = {
        "asset_type": "stock",
        "multi_timeframe_structure_context": mtf,
        "method_execution_receipt_policy": "REQUIRED",
        "method_execution_receipts": {"MTF": receipt},
    }
    trace = build_runtime_trace(factor)
    parent = next(x for x in trace["observations"] if x["requirement_id"] == "MTF")
    assert parent["reason"] == "METHOD_INVOCATION_VERIFIED"
    assert parent["state"] in ("READY", "PARTIAL")

    # Source label laundering or stale attrs must be rejected, not reissued.
    for broken in ("forged_row_source", "stale_price", "mixed_persisted_identity"):
        changed = direct.copy()
        if broken == "forged_row_source":
            changed["data_source"] = "TestData"
        elif broken == "stale_price":
            changed.loc[0, "close"] += 0.3
        else:
            identity = changed.attrs["daily_data_identity"]
            changed["data_identity_json"] = canonical_identity_json(identity)
            changed["data_identity_hash"] = identity["identity_hash"]
            changed.loc[11, "data_identity_hash"] = "f" * 64
        proof = build_completed_history_identity(
            changed, stock_code="600519", market="cn",
            target_date=target, observed_at=observed,
        )
        assert proof["provider_identity"] is None, broken
        assert proof["adjustment_basis"] is None, broken
        assert proof["price_identity_reasons"], broken
        assert proof["data_snapshot_identity"] != source["data_snapshot_identity"]

    # An old plain source string remains descriptive only.
    legacy = build_completed_history_identity(
        rows, stock_code="600519", market="cn",
        target_date=target, observed_at=observed,
    )
    assert legacy["provider_identity"] == "FixtureFetcher"
    assert legacy["adjustment_basis"] is None
    assert legacy["price_identity_reasons"]


def test_b5_valid_typed_daily_source_without_row_label_reaches_monthly_consumer():
    """A complete 1d typed identity, not a fabricated row label, owns M/W source."""
    from src.services.multi_timeframe_structure_service import (
        _aggregate_completed_bars,
        _normalize_daily_history,
        _source_alignment,
    )

    original = _with_observed_identity(_history(periods=150), provider="BaostockFetcher")
    unlabeled = original.drop(columns=["data_source"])
    assert "daily_data_identity" in unlabeled.attrs
    target = unlabeled.iloc[-1]["date"].date()
    normalized = _normalize_daily_history(unlabeled, target_date=target)
    monthly = _aggregate_completed_bars(
        normalized, completed_through=target, period_freq="M",
    )
    assert _source_alignment(normalized)["status"] == "SINGLE_SOURCE"
    assert _source_alignment(normalized)["sources"] == ["BaostockFetcher"]
    assert _source_alignment(monthly)["status"] == "SINGLE_SOURCE"


def test_b5_typed_daily_source_rejects_forged_or_mixed_row_labels():
    """A plain data_source string cannot override a different typed provider."""
    from src.services.multi_timeframe_structure_service import (
        _normalize_daily_history,
        _source_alignment,
    )

    history = _with_observed_identity(_history(periods=90), provider="BaostockFetcher")
    target = history.iloc[-1]["date"].date()
    for forgery in ("all_wrong", "alternating", "case_variant"):
        corrupted = history.copy()
        if forgery == "case_variant":
            corrupted = corrupted.drop(columns=["data_source"])
            corrupted["Data_Source"] = "FORGED_SOURCE"
        else:
            corrupted["data_source"] = (
                "FORGED_SOURCE" if forgery == "all_wrong"
                else ["BaostockFetcher" if i % 2 else "FORGED_SOURCE"
                      for i in range(len(corrupted))]
            )
        projected = _normalize_daily_history(corrupted, target_date=target)
        assert _source_alignment(projected)["status"] == "UNPROVEN", forgery


def test_b5_typed_daily_source_rejects_modified_values_with_stale_identity():
    """Rebinding a stale attr must not launder changed OHLCV into valid source."""
    from data_provider.daily_data_identity import extract_daily_data_identity
    from src.services.multi_timeframe_structure_service import (
        _normalize_daily_history,
        _source_alignment,
    )

    original = _with_observed_identity(_history(periods=90), provider="BaostockFetcher")
    altered = original.copy()
    altered.loc[0, "close"] = float(altered.loc[0, "close"]) + 0.125
    projected = _normalize_daily_history(
        altered, target_date=original.iloc[-1]["date"].date(),
    )
    assert _source_alignment(projected)["status"] == "UNPROVEN"
    assert extract_daily_data_identity(projected, strict=False) is None


def test_b5_persisted_daily_identity_rows_must_match_attached_identity():
    """Mixed persisted JSON/hash records cannot inherit a clean frame attr."""
    from data_provider.daily_data_identity import canonical_identity_json
    from src.services.multi_timeframe_structure_service import (
        _normalize_daily_history,
        _source_alignment,
    )

    source = _with_observed_identity(_history(periods=90), provider="BaostockFetcher")
    identity = source.attrs["daily_data_identity"]
    source["data_identity_json"] = canonical_identity_json(identity)
    source["data_identity_hash"] = identity["identity_hash"]
    target = source.iloc[-1]["date"].date()
    valid = _normalize_daily_history(source, target_date=target)
    assert _source_alignment(valid)["status"] == "SINGLE_SOURCE"

    corrupted = source.copy()
    corrupted.loc[10, "data_identity_hash"] = "f" * 64
    projected = _normalize_daily_history(corrupted, target_date=target)
    assert _source_alignment(projected)["status"] == "UNPROVEN"


def test_b5_legacy_row_only_input_is_not_fabricated_as_typed_source():
    """Preserve legacy descriptive source rows; do not invent a DataIdentity."""
    from data_provider.daily_data_identity import extract_daily_data_identity
    from src.services.multi_timeframe_structure_service import (
        _normalize_daily_history,
        _source_alignment,
    )

    history = _history(periods=90)
    projected = _normalize_daily_history(
        history, target_date=history.iloc[-1]["date"].date(),
    )
    assert _source_alignment(projected)["status"] == "SINGLE_SOURCE"
    assert extract_daily_data_identity(projected, strict=False) is None


def test_completed_history_identity_is_prefix_safe_and_changes_with_consumed_bytes():
    raw_prefix = _history(periods=150)
    target = raw_prefix.iloc[-1]["date"].date()
    requested_end = target.isoformat()
    prefix = _with_observed_identity(
        raw_prefix,
        provider="AkshareFetcher",
        requested_end=requested_end,
    )
    full = _with_observed_identity(
        _history(periods=150, future=8),
        provider="AkshareFetcher",
        requested_end=requested_end,
    )
    observed_at = datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc)

    kwargs = dict(
        stock_code="600519",
        target_date=target,
        market="cn",
        trend_analyzer=_FakeTrendAnalyzer(),
        daily_trend_result=_trend_result(),
        daily_price_structure_context={"historical_replay_eligible": True},
        snapshot_observed_at=observed_at,
    )
    with patch(
        "src.services.multi_timeframe_structure_service.resolve_completed_timeframe_bar_date",
        return_value=None,
    ):
        prefix_context = build_multi_timeframe_structure_context(history=prefix, **kwargs)
        full_context = build_multi_timeframe_structure_context(history=full, **kwargs)
        changed = prefix.copy()
        changed.loc[10, "close"] = float(changed.loc[10, "close"]) + 1.0
        changed_context = build_multi_timeframe_structure_context(history=changed, **kwargs)

    assert prefix_context["data_snapshot_identity"] == full_context["data_snapshot_identity"]
    assert prefix_context["data_snapshot_identity"] != changed_context["data_snapshot_identity"]
    assert prefix_context["provider_identity"] == "AkshareFetcher"
    assert prefix_context["adjustment_basis"] == "qfq"
    assert prefix_context["available_at_max"] == "2026-09-17T10:00:00+00:00"
    assert prefix_context["cross_run_persistence_eligible"] is True
    assert prefix_context["cross_run_persistence_reason"] == "DAILY_DATA_IDENTITY_READY"


def test_baostock_complete_daily_source_identity_survives_mtf_projection_with_pct_chg():
    """Real-source daily change column must survive 1d→MTF provenance hashing."""
    raw = _history(periods=85)
    raw["amount"] = [float(100000 + i * 10) for i in range(len(raw))]
    raw["pct_chg"] = [float(i % 7) / 100 for i in range(len(raw))]
    frame = _with_observed_identity(raw, provider="BaostockFetcher", basis="qfq")
    target = frame.iloc[-1]["date"].date()
    observed = datetime(2026, 10, 13, 3, 10, tzinfo=timezone.utc)
    direct = build_completed_history_identity(
        frame, stock_code="600519", market="cn",
        target_date=target, observed_at=observed,
    )
    kwargs = dict(
        stock_code="600519", target_date=target, market="cn",
        trend_analyzer=_FakeTrendAnalyzer(),
        daily_trend_result=_trend_result(),
        daily_price_structure_context={"historical_replay_eligible": False},
        snapshot_observed_at=observed,
    )
    changed = raw.copy()
    changed.loc[10, "pct_chg"] = float(changed.loc[10, "pct_chg"]) + 1.0
    changed_frame = _with_observed_identity(
        changed, provider="BaostockFetcher", basis="qfq",
    )
    with patch(
        "src.services.multi_timeframe_structure_service.resolve_completed_timeframe_bar_date",
        return_value=None,
    ):
        mtf = build_multi_timeframe_structure_context(history=frame, **kwargs)
        changed_mtf = build_multi_timeframe_structure_context(
            history=changed_frame, **kwargs,
        )
    assert mtf["data_snapshot_identity"] == direct["data_snapshot_identity"]
    assert mtf["daily_data_identity_hash"] == direct["daily_data_identity_hash"]
    assert mtf["provider_identity"] == "BaostockFetcher"
    assert mtf["adjustment_basis"] == "qfq"
    assert mtf["price_identity_reasons"] == []
    assert changed_mtf["data_snapshot_identity"] != mtf["data_snapshot_identity"]


def test_completed_history_identity_binds_observed_price_basis_into_snapshot_hash():
    raw = _history(periods=40)
    target = raw.iloc[-1]["date"].date()
    qfq_frame = _with_observed_identity(raw, provider="AkshareFetcher", basis="qfq")
    hfq_frame = _with_observed_identity(raw, provider="AkshareFetcher", basis="hfq")

    qfq = build_completed_history_identity(
        qfq_frame,
        stock_code="600519",
        market="cn",
        target_date=target,
    )
    hfq = build_completed_history_identity(
        hfq_frame,
        stock_code="600519",
        market="cn",
        target_date=target,
    )

    assert qfq["adjustment_basis"] == "qfq"
    assert hfq["adjustment_basis"] == "hfq"
    assert qfq["data_snapshot_schema_version"] == "completed-daily-history-v3"
    assert hfq["data_snapshot_identity"] != qfq["data_snapshot_identity"]


def test_completed_history_availability_never_backdates_typed_source_observation():
    # The data source first observed this snapshot in September 2026; a caller
    # replaying a 2025 session must not replace its actual availability clock.
    frame = _with_observed_identity(_history(periods=150), provider="AkshareFetcher")
    target = frame.iloc[-1]["date"].date()
    replay_time = datetime(2025, 9, 30, 12, 0, tzinfo=timezone.utc)
    source_time = "2026-09-17T10:00:00+00:00"

    earlier = build_completed_history_identity(
        frame, stock_code="600519", market="cn",
        target_date=target, observed_at=replay_time,
    )
    assert earlier["adjustment_basis"] == "qfq"
    assert earlier["daily_data_identity_state"] == "OBSERVED"
    assert earlier["available_at_max"] == source_time
    assert earlier["snapshot_observed_at"] == source_time

    # A genuinely later runtime observation is still the conservative maximum.
    later = build_completed_history_identity(
        frame, stock_code="600519", market="cn", target_date=target,
        observed_at=datetime(2026, 9, 18, 8, 0, tzinfo=timezone.utc),
    )
    assert later["available_at_max"] == "2026-09-18T08:00:00+00:00"
    assert later["data_snapshot_identity"] == earlier["data_snapshot_identity"]

    # Prove the existing MTF consumer forwards the same fail-closed clock.
    with patch(
        "src.services.multi_timeframe_structure_service.resolve_completed_timeframe_bar_date",
        return_value=None,
    ):
        mtf = build_multi_timeframe_structure_context(
            stock_code="600519", history=frame, target_date=target,
            market="cn", trend_analyzer=_FakeTrendAnalyzer(),
            daily_trend_result=_trend_result(),
            daily_price_structure_context={"historical_replay_eligible": False},
            snapshot_observed_at=replay_time,
        )
    assert mtf["available_at_max"] == source_time


def test_provider_name_alone_never_proves_qfq():
    assert proven_adjustment_basis("TencentFetcher") is None
    frame = _with_observed_identity(_history(periods=5), provider="TencentFetcher")
    identity = frame.attrs["daily_data_identity"]
    assert proven_adjustment_basis("TencentFetcher", identity) == "qfq"
    assert proven_adjustment_basis("DifferentFetcher", identity) is None


def test_higher_timeframe_summary_preserves_material_structure_event_after_three_descriptors():
    summary = _human_summary(
        {
            "ma_alignment": "MA_FIXTURE",
            "volume_status": "VOLUME_FIXTURE",
            "macd_signal": "MACD_FIXTURE",
        },
        {"structure_event": {"state": "FAILED_UP_BREAKOUT"}},
    )

    assert summary == (
        "MA_FIXTURE；VOLUME_FIXTURE；MACD_FIXTURE；"
        "向上突破失败并回到结构位下方"
    )


def test_higher_timeframe_summary_without_structure_event_keeps_existing_projection():
    summary = _human_summary(
        {
            "ma_alignment": "MA_FIXTURE",
            "volume_status": "VOLUME_FIXTURE",
            "macd_signal": "MACD_FIXTURE",
        },
        {},
    )

    assert summary == "MA_FIXTURE；VOLUME_FIXTURE；MACD_FIXTURE"
