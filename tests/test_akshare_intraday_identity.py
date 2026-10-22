from __future__ import annotations

from datetime import datetime, timezone
import sys
from types import SimpleNamespace

import pandas as pd
import pytest

from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.base import DataFetchError
from data_provider.intraday_data_identity import extract_intraday_data_identity


def _raw_5m() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "时间": ["2026-09-23 09:35:00", "2026-09-23 09:40:00", "2026-09-23 09:45:00"],
            "开盘": [10.0, 10.5, 11.0],
            "收盘": [10.5, 11.0, 11.5],
            "最高": [11.0, 11.5, 12.0],
            "最低": [9.0, 10.0, 10.5],
            "成交量": [100, 120, 130],
            "成交额": [1050.0, 1320.0, 1495.0],
        }
    )


def _fetcher() -> AkshareFetcher:
    item = AkshareFetcher(sleep_min=0, sleep_max=0)
    item._set_random_user_agent = lambda: None
    item._enforce_rate_limit = lambda: None
    return item


def test_stock_akshare_5m_binds_exact_route_and_documented_volume_unit(monkeypatch):
    calls = []
    fake = SimpleNamespace(
        stock_zh_a_hist_min_em=lambda **kwargs: calls.append(kwargs) or _raw_5m(),
        fund_etf_hist_min_em=lambda **kwargs: pytest.fail("ETF route not expected"),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake)
    monkeypatch.setattr("data_provider.akshare_fetcher.metadata.version", lambda _: "1.18.97")
    frame = _fetcher().get_intraday_data(
        "600519",
        start_date="2026-09-23",
        end_date="2026-09-23",
        observed_at=datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc),
    )
    identity = extract_intraday_data_identity(frame, strict=True)
    assert calls == [{
        "symbol": "600519",
        "start_date": "2026-09-23 09:30:00",
        "end_date": "2026-09-23 15:00:00",
        "period": "5",
        "adjust": "qfq",
    }]
    assert identity["provider_route"] == "akshare.stock_zh_a_hist_min_em"
    assert identity["package_version"] == "1.18.97"
    assert identity["observed_adjustment_basis"] == "qfq"
    assert identity["volume_unit"] == "LOT_100_SHARES"
    assert identity["amount_unit"] == "UNKNOWN"
    assert frame["session"].tolist() == ["2026-09-23:AM"] * 3


def test_etf_akshare_5m_uses_etf_route_and_keeps_undocumented_units_unknown(monkeypatch):
    calls = []
    fake = SimpleNamespace(
        stock_zh_a_hist_min_em=lambda **kwargs: pytest.fail("stock route not expected"),
        fund_etf_hist_min_em=lambda **kwargs: calls.append(kwargs) or _raw_5m(),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake)
    monkeypatch.setattr("data_provider.akshare_fetcher.metadata.version", lambda _: "1.18.97")
    frame = _fetcher().get_intraday_data(
        "510300",
        start_date="2026-09-23",
        end_date="2026-09-23",
        observed_at=datetime(2026, 9, 23, 8, 0, tzinfo=timezone.utc),
    )
    identity = extract_intraday_data_identity(frame, strict=True)
    assert len(calls) == 1
    assert identity["provider_route"] == "akshare.fund_etf_hist_min_em"
    assert identity["volume_unit"] == "UNKNOWN"
    assert identity["amount_unit"] == "UNKNOWN"


def test_unadmitted_frequency_stops_before_provider_call(monkeypatch):
    fake = SimpleNamespace(
        stock_zh_a_hist_min_em=lambda **kwargs: pytest.fail("provider must not be called"),
        fund_etf_hist_min_em=lambda **kwargs: pytest.fail("provider must not be called"),
    )
    monkeypatch.setitem(sys.modules, "akshare", fake)
    with pytest.raises(DataFetchError, match="canonical 5m"):
        _fetcher().get_intraday_data(
            "600519",
            start_date="2026-09-23",
            end_date="2026-09-23",
            frequency="15",
        )
