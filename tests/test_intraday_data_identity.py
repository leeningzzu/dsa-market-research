from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest

from data_provider.intraday_data_identity import (
    IntradayDataIdentityError,
    attach_intraday_data_identity,
    build_intraday_data_identity,
    canonical_intraday_identity_json,
    extract_intraday_data_identity,
)


def _frame() -> pd.DataFrame:
    ends = pd.date_range("2026-09-23 09:35:00", periods=3, freq="5min", tz="Asia/Shanghai")
    observed = pd.Timestamp("2026-09-23 10:00:00", tz="Asia/Shanghai")
    return pd.DataFrame(
        {
            "code": ["600519"] * 3,
            "bar_end": ends,
            "open": [10.0, 10.5, 11.0],
            "high": [11.0, 11.5, 12.0],
            "low": [9.5, 10.0, 10.5],
            "close": [10.5, 11.0, 11.5],
            "volume": [100.0, 120.0, 130.0],
            "amount": [1000.0, 1320.0, 1495.0],
            "session": ["2026-09-23:AM"] * 3,
            "available_at": [observed] * 3,
            "adjustflag": ["2"] * 3,
            "data_source": ["BaostockFetcher"] * 3,
        }
    )


def _identity(frame: pd.DataFrame) -> dict:
    return build_intraday_data_identity(
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
        observed_at=datetime(2026, 9, 23, 2, 0, tzinfo=timezone.utc),
        source_rows_sha256="a" * 64,
    )


def test_intraday_identity_round_trip_keeps_unknown_units_explicit() -> None:
    frame = _frame()
    identity = _identity(frame)
    attach_intraday_data_identity(frame, identity)
    extracted = extract_intraday_data_identity(frame, strict=True)
    assert extracted == identity
    assert extracted["timeframe"] == "5m"
    assert extracted["volume_unit"] == "UNKNOWN"
    assert extracted["amount_unit"] == "UNKNOWN"
    assert extracted["available_at_max"].endswith("+08:00")
    assert canonical_intraday_identity_json(identity)


def test_intraday_identity_tamper_fails_hash_validation() -> None:
    identity = _identity(_frame())
    tampered = dict(identity)
    tampered["provider_route"] = "other.route"
    with pytest.raises(IntradayDataIdentityError, match="hash mismatch"):
        attach_intraday_data_identity(_frame(), tampered)


def test_intraday_identity_detects_frame_content_drift() -> None:
    frame = _frame()
    identity = _identity(frame)
    attach_intraday_data_identity(frame, identity)
    frame.loc[1, "close"] = 99.0
    with pytest.raises(IntradayDataIdentityError, match="content does not match"):
        extract_intraday_data_identity(frame, strict=True)


def test_intraday_identity_rejects_available_at_before_bar_end() -> None:
    frame = _frame()
    frame.loc[2, "available_at"] = pd.Timestamp("2026-09-23 09:40:00", tz="Asia/Shanghai")
    with pytest.raises(IntradayDataIdentityError, match="precedes bar_end"):
        _identity(frame)


def test_intraday_identity_rejects_mixed_row_identity_hashes() -> None:
    frame = _frame()
    identity = _identity(frame)
    attach_intraday_data_identity(frame, identity)
    frame["intraday_data_identity_hash"] = identity["identity_hash"]
    frame.loc[1, "intraday_data_identity_hash"] = "b" * 64
    with pytest.raises(IntradayDataIdentityError, match="mixed intraday identity"):
        extract_intraday_data_identity(frame, strict=True)
