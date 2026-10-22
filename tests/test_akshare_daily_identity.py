from __future__ import annotations

import sys
from types import SimpleNamespace

import pandas as pd

from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.daily_data_identity import extract_daily_data_identity


def _raw_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "日期": ["2026-10-20", "2026-10-21"],
            "开盘": [10.0, 10.2],
            "收盘": [10.1, 10.3],
            "最高": [10.4, 10.5],
            "最低": [9.9, 10.1],
            "成交量": [1000, 1200],
            "成交额": [1010000.0, 1236000.0],
            "涨跌幅": [1.0, 1.98],
        }
    )


def _fetcher() -> AkshareFetcher:
    item = AkshareFetcher(sleep_min=0, sleep_max=0)
    item._set_random_user_agent = lambda: None
    item._enforce_rate_limit = lambda: None
    return item


def test_stock_em_daily_attaches_observed_route_basis_and_documented_units(monkeypatch):
    fake = SimpleNamespace(stock_zh_a_hist=lambda **kwargs: _raw_frame())
    monkeypatch.setitem(sys.modules, "akshare", fake)
    frame = _fetcher().get_daily_data(
        "600519", start_date="2026-10-20", end_date="2026-10-21"
    )
    identity = extract_daily_data_identity(frame, strict=True)
    assert identity["identity_state"] == "OBSERVED"
    assert identity["provider_route"] == "akshare.stock_zh_a_hist"
    assert identity["instrument"] == "600519"
    assert identity["observed_adjustment_basis"] == "qfq"
    assert identity["volume_unit"] == "LOT_100_SHARES"
    assert identity["amount_unit"] == "CNY"
    assert "akshare_docs@fe10d1de" in identity["basis_evidence"]


def test_etf_em_daily_keeps_undocumented_volume_and_amount_units_unknown(monkeypatch):
    fake = SimpleNamespace(fund_etf_hist_em=lambda **kwargs: _raw_frame())
    monkeypatch.setitem(sys.modules, "akshare", fake)
    frame = _fetcher().get_daily_data(
        "510300", start_date="2026-10-20", end_date="2026-10-21"
    )
    identity = extract_daily_data_identity(frame, strict=True)
    assert identity["identity_state"] == "OBSERVED"
    assert identity["provider_route"] == "akshare.fund_etf_hist_em"
    assert identity["instrument"] == "510300"
    assert identity["observed_adjustment_basis"] == "qfq"
    assert identity["volume_unit"] == "UNKNOWN"
    assert identity["amount_unit"] == "UNKNOWN"
