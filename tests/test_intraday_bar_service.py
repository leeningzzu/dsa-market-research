from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from data_provider.intraday_data_identity import (
    attach_intraday_data_identity,
    build_intraday_data_identity,
    extract_intraday_data_identity,
)
from src.services.intraday_bar_service import (
    IntradayBarError,
    aggregate_completed_intraday_bars,
    normalize_cn_completed_5m_bars,
    validate_complete_cn_5m_sessions,
)


def _raw_full_day() -> pd.DataFrame:
    labels = list(pd.date_range("2026-09-23 09:35:00", "2026-09-23 11:30:00", freq="5min"))
    labels += list(pd.date_range("2026-09-23 13:05:00", "2026-09-23 15:00:00", freq="5min"))
    rows = []
    for index, ts in enumerate(labels):
        base = 100.0 + index
        rows.append(
            {
                "date": "2026-09-23",
                "time": ts.strftime("%Y%m%d%H%M%S") + "000",
                "code": "sh.600519",
                "open": base,
                "high": base + 2.0,
                "low": base - 1.0,
                "close": base + 1.0,
                "volume": 10 + index,
                "amount": 1000 + index,
                "adjustflag": "2",
            }
        )
    return pd.DataFrame(rows)


def _canonical_full_day() -> pd.DataFrame:
    raw = _raw_full_day()
    observed = datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc)
    frame = normalize_cn_completed_5m_bars(
        raw,
        stock_code="600519",
        data_source="BaostockFetcher",
        observed_at=observed,
    )
    identity = build_intraday_data_identity(
        frame,
        provider_identity="BaostockFetcher",
        provider_route="baostock.query_history_k_data_plus",
        package_version="0.9.4",
        query_identity={"code": "sh.600519", "frequency": "5", "adjustflag": "2"},
        requested_adjustment_basis="qfq",
        observed_adjustment_basis="qfq",
        basis_evidence="successful_nonempty_query:adjustflag=2",
        timeframe="5m",
        timezone_name="Asia/Shanghai",
        session_calendar="XSHG",
        currency="CNY",
        volume_unit="UNKNOWN",
        amount_unit="UNKNOWN",
        requested_start="2026-09-23",
        requested_end="2026-09-23",
        identity_state="OBSERVED",
        observed_at=observed,
        source_rows_sha256="c" * 64,
    )
    attach_intraday_data_identity(frame, identity)
    frame["intraday_data_identity_hash"] = identity["identity_hash"]
    return frame


@pytest.mark.parametrize(("target", "expected_rows"), [("15m", 16), ("30m", 8), ("60m", 4)])
def test_full_cn_session_aggregates_to_exact_cardinality(target: str, expected_rows: int) -> None:
    source = _canonical_full_day()
    derived = aggregate_completed_intraday_bars(source, target_timeframe=target)
    assert len(source) == 48
    assert len(derived) == expected_rows
    identity = extract_intraday_data_identity(derived, strict=True)
    assert identity["timeframe"] == target
    assert identity["parent_identity_hash"] == extract_intraday_data_identity(source, strict=True)["identity_hash"]
    assert "amount" not in derived.columns
    assert identity["amount_unit"] == "UNKNOWN"


def test_aggregation_never_crosses_lunch_and_uses_ohlcv_rules() -> None:
    source = _canonical_full_day()
    derived = aggregate_completed_intraday_bars(source, target_timeframe="60m")
    labels = [value.strftime("%H:%M") for value in derived["bar_end"]]
    assert labels == ["10:30", "11:30", "14:00", "15:00"]
    first = derived.iloc[0]
    assert first["open"] == source.iloc[0]["open"]
    assert first["close"] == source.iloc[11]["close"]
    assert first["high"] == source.iloc[:12]["high"].max()
    assert first["low"] == source.iloc[:12]["low"].min()
    assert first["volume"] == source.iloc[:12]["volume"].sum()


def test_missing_5m_bar_drops_only_its_fixed_window_without_sliding() -> None:
    original = _canonical_full_day()
    source = original.drop(index=[4]).reset_index(drop=True)
    observed = datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc)
    identity = build_intraday_data_identity(
        source,
        provider_identity="BaostockFetcher",
        provider_route="baostock.query_history_k_data_plus",
        package_version="0.9.4",
        query_identity={"code": "sh.600519", "frequency": "5", "adjustflag": "2"},
        requested_adjustment_basis="qfq",
        observed_adjustment_basis="qfq",
        basis_evidence="successful_nonempty_query:adjustflag=2",
        timeframe="5m",
        timezone_name="Asia/Shanghai",
        session_calendar="XSHG",
        currency="CNY",
        volume_unit="UNKNOWN",
        amount_unit="UNKNOWN",
        requested_start="2026-09-23",
        requested_end="2026-09-23",
        identity_state="OBSERVED",
        observed_at=observed,
        source_rows_sha256="e" * 64,
    )
    attach_intraday_data_identity(source, identity)
    source["intraday_data_identity_hash"] = identity["identity_hash"]
    derived = aggregate_completed_intraday_bars(source, target_timeframe="15m")
    assert len(derived) == 15
    assert pd.Timestamp("2026-09-23 10:00:00", tz="Asia/Shanghai") not in set(derived["bar_end"])
    assert derived.attrs["intraday_aggregation_receipt"]["dropped_incomplete_windows"] == 1


def test_normalizer_excludes_future_partial_bar_and_rejects_off_session() -> None:
    raw = _raw_full_day().iloc[:3].copy()
    observed = datetime(2026, 9, 23, 1, 37, tzinfo=timezone.utc)
    frame = normalize_cn_completed_5m_bars(
        raw,
        stock_code="600519",
        data_source="BaostockFetcher",
        observed_at=observed,
    )
    assert [value.strftime("%H:%M") for value in frame["bar_end"]] == ["09:35"]

    bad = raw.iloc[:1].copy()
    bad.loc[bad.index[0], "time"] = "20260923120000000"
    with pytest.raises(IntradayBarError, match="off-session"):
        normalize_cn_completed_5m_bars(
            bad,
            stock_code="600519",
            data_source="BaostockFetcher",
            observed_at=datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc),
        )


def test_normalizer_rejects_date_time_mismatch() -> None:
    raw = _raw_full_day().iloc[:1].copy()
    raw.loc[raw.index[0], "date"] = "2026-09-24"
    with pytest.raises(IntradayBarError, match="source date does not match"):
        normalize_cn_completed_5m_bars(
            raw,
            stock_code="600519",
            data_source="BaostockFetcher",
            observed_at=datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc),
        )




def _attach_test_identity(
    frame: pd.DataFrame,
    session_dates: list[date],
    *,
    source_rows_sha256: str,
) -> pd.DataFrame:
    observed = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    identity = build_intraday_data_identity(
        frame,
        provider_identity="BaostockFetcher",
        provider_route="unit.session-set",
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
        observed_at=observed,
        source_rows_sha256=source_rows_sha256,
    )
    attach_intraday_data_identity(frame, identity)
    frame["intraday_data_identity_hash"] = identity["identity_hash"]
    return frame


def _canonical_session_set(session_dates: list[date]) -> pd.DataFrame:
    observed = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
    frames = []
    for session_date in session_dates:
        labels = list(
            pd.date_range(
                f"{session_date.isoformat()} 09:35:00",
                f"{session_date.isoformat()} 11:30:00",
                freq="5min",
            )
        )
        labels += list(
            pd.date_range(
                f"{session_date.isoformat()} 13:05:00",
                f"{session_date.isoformat()} 15:00:00",
                freq="5min",
            )
        )
        raw = pd.DataFrame(
            {
                "date": [session_date.isoformat()] * len(labels),
                "time": [value.strftime("%Y%m%d%H%M%S") + "000" for value in labels],
                "code": ["sh.600519"] * len(labels),
                "open": [100.0 + index * 0.01 for index in range(len(labels))],
                "high": [101.0 + index * 0.01 for index in range(len(labels))],
                "low": [99.0 + index * 0.01 for index in range(len(labels))],
                "close": [100.5 + index * 0.01 for index in range(len(labels))],
                "volume": [1000.0 + index for index in range(len(labels))],
                "amount": [100000.0 + index for index in range(len(labels))],
                "adjustflag": ["2"] * len(labels),
            }
        )
        frames.append(
            normalize_cn_completed_5m_bars(
                raw,
                stock_code="600519",
                data_source="BaostockFetcher",
                observed_at=observed,
            )
        )
    frame = pd.concat(frames, ignore_index=True)
    return _attach_test_identity(
        frame,
        session_dates,
        source_rows_sha256="9" * 64,
    )


def test_complete_session_validator_accepts_exact_ten_sessions() -> None:
    sessions = [
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
    frame = _canonical_session_set(sessions)
    receipt = validate_complete_cn_5m_sessions(
        frame,
        expected_session_dates=sessions,
    )
    assert len(frame) == 480
    assert receipt["session_count"] == 10
    assert receipt["row_count"] == 480
    assert receipt["bars_per_session"] == 48


def test_complete_session_validator_rejects_missing_bar_even_with_rebound_identity() -> None:
    sessions = [
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
    frame = _canonical_session_set(sessions).drop(index=[4]).reset_index(drop=True)
    frame = _attach_test_identity(
        frame.drop(columns=["intraday_data_identity_hash"]),
        sessions,
        source_rows_sha256="8" * 64,
    )
    with pytest.raises(IntradayBarError, match="incomplete or noncanonical"):
        validate_complete_cn_5m_sessions(
            frame,
            expected_session_dates=sessions,
        )
def test_aggregation_fails_closed_on_mixed_identity_hash() -> None:
    source = _canonical_full_day()
    source.loc[0, "intraday_data_identity_hash"] = "d" * 64
    with pytest.raises((IntradayBarError, ValueError), match="mixed intraday"):
        aggregate_completed_intraday_bars(source, target_timeframe="30m")
