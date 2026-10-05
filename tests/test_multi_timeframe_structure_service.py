from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

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


def _intraday_fixture(timeframe: str, target, periods: int = 40) -> pd.DataFrame:
    end = pd.Timestamp(target).tz_localize("Asia/Shanghai") + pd.Timedelta(hours=15)
    ends = pd.date_range(end=end, periods=periods, freq="30min")
    observed = end + pd.Timedelta(hours=1)
    rows = []
    for index, bar_end in enumerate(ends):
        close = 100.0 + index * 0.2
        rows.append(
            {
                "code": "600519",
                "bar_end": bar_end,
                "open": close - 0.1,
                "high": close + 0.5,
                "low": close - 0.5,
                "close": close,
                "volume": 1000.0 + index,
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
        volume_unit="UNKNOWN",
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


def test_canonical_intraday_bars_fill_partial_trend_ma_slots_without_claiming_structure() -> None:
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
        assert item["price_structure"]["reason"] == "INTRADAY_PRICE_STRUCTURE_NOT_ADMITTED"
        assert item["pattern_trigger"]["status"] == "MISSING"
        assert item["independent_action_authority"] is False
        assert item["strategy_admitted"] is False
        assert item["learning_admitted"] is False
        assert item["historical_replay_eligible"] is False
        assert "分钟价格结构/形态尚未准入" in item["summary"]


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
    assert prefix_context["available_at_max"] == "2026-09-17T10:00:00"
    assert prefix_context["cross_run_persistence_eligible"] is True
    assert prefix_context["cross_run_persistence_reason"] == "DAILY_DATA_IDENTITY_READY"


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
