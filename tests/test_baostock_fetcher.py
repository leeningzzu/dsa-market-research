from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from data_provider.baostock_fetcher import BaostockFetcher
from data_provider.base import DataFetchError
from data_provider.daily_data_identity import extract_daily_data_identity
from data_provider.intraday_data_identity import extract_intraday_data_identity


class _Result:
    fields = ["date", "open", "high", "low", "close", "volume", "amount", "pctChg"]

    def __init__(self, rows, *, error_code: str = "0", error_msg: str = "") -> None:
        self.error_code = error_code
        self.error_msg = error_msg
        self._rows = list(rows)
        self._index = -1

    def next(self) -> bool:
        self._index += 1
        return self._index < len(self._rows)

    def get_row_data(self):
        return self._rows[self._index]


class _FakeBaostock:
    def __init__(self, result: _Result) -> None:
        self.result = result
        self.calls = []

    def query_history_k_data_plus(self, **kwargs):
        self.calls.append(kwargs)
        return self.result


@contextmanager
def _session(fake):
    yield fake


def test_baostock_successful_adjustflag_two_binds_observed_qfq_identity() -> None:
    fake = _FakeBaostock(
        _Result(
            [
                ["2026-01-05", "10", "11", "9", "10.5", "1000", "10500", "1.0"],
                ["2026-01-06", "10.5", "12", "10", "11.5", "1100", "12650", "9.5"],
            ]
        )
    )
    fetcher = BaostockFetcher()
    with patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)):
        frame = fetcher.get_daily_data("600519", start_date="2026-01-01", end_date="2026-01-31")

    assert fake.calls == [
        {
            "code": "sh.600519",
            "fields": "date,open,high,low,close,volume,amount,pctChg",
            "start_date": "2026-01-01",
            "end_date": "2026-01-31",
            "frequency": "d",
            "adjustflag": "2",
        }
    ]
    identity = extract_daily_data_identity(frame, strict=True)
    assert identity["identity_state"] == "OBSERVED"
    assert identity["observed_adjustment_basis"] == "qfq"
    assert identity["actual_response_branch"] == "frequency=d;adjustflag=2"
    assert identity["provider_identity"] == "BaostockFetcher"
    assert identity["volume_unit"] == "share"
    assert identity["amount_unit"] == "CNY"


def test_baostock_failed_query_never_emits_observed_identity() -> None:
    fake = _FakeBaostock(_Result([], error_code="100", error_msg="query failed"))
    fetcher = BaostockFetcher()
    with patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)):
        with pytest.raises(DataFetchError, match="查询失败"):
            fetcher._fetch_raw_data("600519", "2026-01-01", "2026-01-31")

class _MinuteResult(_Result):
    fields = ["date", "time", "code", "open", "high", "low", "close", "volume", "amount", "adjustflag"]


def test_baostock_intraday_5m_seam_binds_identity_without_touching_daily_route() -> None:
    rows = [
        ["2026-09-23", "20260923093500000", "sh.600519", "10", "11", "9", "10.5", "100", "1050", "2"],
        ["2026-09-23", "20260923094000000", "sh.600519", "10.5", "11.5", "10", "11", "120", "1320", "2"],
        ["2026-09-23", "20260923094500000", "sh.600519", "11", "12", "10.5", "11.5", "130", "1495", "2"],
    ]
    fake = _FakeBaostock(_MinuteResult(rows))
    fetcher = BaostockFetcher()
    observed_at = datetime(2026, 9, 23, 2, 0, tzinfo=timezone.utc)
    with (
        patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)),
        patch("data_provider.baostock_fetcher.metadata.version", return_value="0.9.4"),
    ):
        frame = fetcher.get_intraday_data(
            "600519",
            start_date="2026-09-23",
            end_date="2026-09-23",
            frequency="5",
            observed_at=observed_at,
        )

    assert fake.calls == [
        {
            "code": "sh.600519",
            "fields": "date,time,code,open,high,low,close,volume,amount,adjustflag",
            "start_date": "2026-09-23",
            "end_date": "2026-09-23",
            "frequency": "5",
            "adjustflag": "2",
        }
    ]
    identity = extract_intraday_data_identity(frame, strict=True)
    assert identity["timeframe"] == "5m"
    assert identity["provider_identity"] == "BaostockFetcher"
    assert identity["observed_adjustment_basis"] == "qfq"
    assert identity["volume_unit"] == "UNKNOWN"
    assert identity["amount_unit"] == "UNKNOWN"
    assert frame["session"].tolist() == ["2026-09-23:AM"] * 3


def test_baostock_intraday_rejects_response_identity_drift() -> None:
    rows = [
        ["2026-09-23", "20260923093500000", "sh.600519", "10", "11", "9", "10.5", "100", "1050", "3"],
    ]
    fake = _FakeBaostock(_MinuteResult(rows))
    fetcher = BaostockFetcher()
    with patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)):
        with pytest.raises(DataFetchError, match="adjustflag mismatch"):
            fetcher.get_intraday_data(
                "600519",
                start_date="2026-09-23",
                end_date="2026-09-23",
                observed_at=datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc),
            )


def test_baostock_intraday_rejects_unadmitted_frequency_without_query() -> None:
    fake = _FakeBaostock(_MinuteResult([]))
    fetcher = BaostockFetcher()
    with patch.object(fetcher, "_baostock_session", side_effect=lambda: _session(fake)):
        with pytest.raises(DataFetchError, match="canonical 5m"):
            fetcher.get_intraday_data(
                "600519",
                start_date="2026-09-23",
                end_date="2026-09-23",
                frequency="15",
            )
    assert fake.calls == []
