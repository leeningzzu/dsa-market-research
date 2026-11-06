import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.akshare_fetcher import AkshareFetcher
import data_provider.akshare_fetcher as akshare_fetcher_module
from src.services.screening.snapshot import fetch_cn_etf_snapshot
from data_provider.base import BaseFetcher, DataFetchError, DataFetcherManager
from data_provider.efinance_fetcher import EfinanceFetcher


@pytest.fixture(autouse=True)
def _reset_daily_source_health():
    DataFetcherManager.reset_daily_source_health()
    akshare_fetcher_module._etf_realtime_cache.update({
        "data": None,
        "timestamp": 0,
    })
    yield
    DataFetcherManager.reset_daily_source_health()
    akshare_fetcher_module._etf_realtime_cache.update({
        "data": None,
        "timestamp": 0,
    })


def _make_efinance_fetcher() -> EfinanceFetcher:
    with patch(
        "data_provider.efinance_fetcher.get_config",
        return_value=SimpleNamespace(enable_eastmoney_patch=False),
    ):
        return EfinanceFetcher(sleep_min=0, sleep_max=0)


def _make_akshare_fetcher() -> AkshareFetcher:
    with patch(
        "data_provider.akshare_fetcher.get_config",
        return_value=SimpleNamespace(enable_eastmoney_patch=False),
    ):
        return AkshareFetcher(sleep_min=0, sleep_max=0)


def _history_frame(code: str = "563230") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "股票代码": [code] * 5,
            "日期": pd.date_range("2026-01-01", periods=5).strftime("%Y-%m-%d"),
            "开盘": [10.0, 10.1, 10.2, 10.3, 10.4],
            "收盘": [10.1, 10.2, 10.3, 10.4, 10.5],
            "最高": [10.2, 10.3, 10.4, 10.5, 10.6],
            "最低": [9.9, 10.0, 10.1, 10.2, 10.3],
            "成交量": [1000, 1100, 1200, 1300, 1400],
            "成交额": [10100, 11220, 12360, 13520, 14700],
            "涨跌幅": [0.0, 0.99, 0.98, 0.97, 0.96],
        }
    )


def _run_efinance_daily(stock_code: str) -> tuple[pd.DataFrame, MagicMock]:
    fetcher = _make_efinance_fetcher()
    fake_efinance = types.SimpleNamespace(
        stock=types.SimpleNamespace(get_quote_history=MagicMock(name="get_quote_history"))
    )
    call = MagicMock(return_value=_history_frame())

    with patch.dict(sys.modules, {"efinance": fake_efinance}):
        with patch("data_provider.efinance_fetcher._ef_call_with_timeout", call):
            with patch.object(fetcher, "_set_random_user_agent"), patch.object(
                fetcher, "_enforce_rate_limit"
            ):
                df = fetcher.get_daily_data(
                    stock_code,
                    start_date="2026-01-01",
                    end_date="2026-01-05",
                )

    return df, call


def test_efinance_sh_etf_uses_eastmoney_quote_id_mode() -> None:
    df, call = _run_efinance_daily("563230")

    kwargs = call.call_args.kwargs
    assert kwargs["stock_codes"] == "1.563230"
    assert kwargs["quote_id_mode"] is True
    assert kwargs["use_id_cache"] is False
    assert kwargs["beg"] == "20260101"
    assert kwargs["end"] == "20260105"
    assert kwargs["klt"] == 101
    assert kwargs["fqt"] == 1
    assert {"ma5", "ma10", "ma20"}.issubset(df.columns)


def test_efinance_sz_etf_uses_eastmoney_quote_id_mode() -> None:
    _, call = _run_efinance_daily("159919")

    assert call.call_args.kwargs["stock_codes"] == "0.159919"
    assert call.call_args.kwargs["quote_id_mode"] is True
    assert call.call_args.kwargs["use_id_cache"] is False


def test_efinance_etf_code_variants_use_sh_secid() -> None:
    for stock_code in ("SH563230", "SH.563230", "563230.SH"):
        _, call = _run_efinance_daily(stock_code)
        assert call.call_args.kwargs["stock_codes"] == "1.563230"


def test_akshare_etf_uses_fund_etf_hist_em() -> None:
    fetcher = _make_akshare_fetcher()
    fake_akshare = types.SimpleNamespace(fund_etf_hist_em=MagicMock(return_value=_history_frame()))

    with patch.dict(sys.modules, {"akshare": fake_akshare}):
        with patch.object(fetcher, "_set_random_user_agent"), patch.object(
            fetcher, "_enforce_rate_limit"
        ):
            df = fetcher._fetch_raw_data("563230", "2026-01-01", "2026-01-05")

    assert df is not None
    fake_akshare.fund_etf_hist_em.assert_called_once_with(
        symbol="563230",
        period="daily",
        start_date="20260101",
        end_date="20260105",
        adjust="qfq",
    )


def test_akshare_etf_snapshot_falls_back_to_live_sina_and_normalizes_codes() -> None:
    fetcher = _make_akshare_fetcher()
    sina_frame = pd.DataFrame(
        {
            "代码": ["sh510300", "sz159915"],
            "名称": ["沪深300ETF", "创业板ETF"],
            "最新价": [4.12, 2.34],
            "涨跌额": [0.02, -0.01],
            "涨跌幅": [0.49, -0.43],
            "买入": [4.11, 2.33],
            "卖出": [4.12, 2.34],
            "昨收": [4.10, 2.35],
            "今开": [4.10, 2.35],
            "最高": [4.13, 2.36],
            "最低": [4.09, 2.32],
            "成交量": [1000000, 2000000],
            "成交额": [4120000, 4680000],
        }
    )
    fake_akshare = types.SimpleNamespace(
        fund_etf_spot_em=MagicMock(side_effect=ConnectionError("em down")),
        fund_etf_category_sina=MagicMock(return_value=sina_frame),
    )
    circuit = MagicMock()

    with patch.dict(sys.modules, {"akshare": fake_akshare}), patch(
        "data_provider.akshare_fetcher.get_realtime_circuit_breaker",
        return_value=circuit,
    ), patch("data_provider.akshare_fetcher.time.sleep"), patch.object(
        fetcher, "_set_random_user_agent"
    ), patch.object(fetcher, "_enforce_rate_limit"):
        actual = fetcher.get_etf_realtime_snapshot()
        quote = fetcher._get_etf_realtime_quote("510300")

    assert list(actual["代码"]) == ["510300", "159915"]
    assert actual[["最新价", "涨跌幅", "成交额"]].notna().all().all()
    assert actual["开盘价"].tolist() == [4.10, 2.35]
    assert actual["最高价"].tolist() == [4.13, 2.36]
    assert actual["最低价"].tolist() == [4.09, 2.32]
    assert actual.attrs["snapshot_source"] == "dsa:akshare_etf_sina"
    assert actual.attrs["provider_route"] == "akshare.fund_etf_category_sina"
    assert actual.attrs["fallback_used"] is False
    assert actual.attrs["provider_fallback_used"] is True
    assert quote is not None
    assert quote.source.value == "akshare_sina"
    assert quote.fallback_from == "akshare_em"
    assert quote.currency == "CNY"
    assert len(actual.attrs["source_errors"]) == 2
    assert fake_akshare.fund_etf_spot_em.call_count == 2
    fake_akshare.fund_etf_category_sina.assert_called_once_with(symbol="ETF基金")
    circuit.record_success.assert_called_once_with("akshare_etf")
    assert isinstance(akshare_fetcher_module._etf_realtime_cache["data"], pd.DataFrame)


def test_akshare_etf_snapshot_does_not_cache_empty_when_all_sources_fail() -> None:
    fetcher = _make_akshare_fetcher()
    fake_akshare = types.SimpleNamespace(
        fund_etf_spot_em=MagicMock(side_effect=ConnectionError("em down")),
        fund_etf_category_sina=MagicMock(side_effect=ConnectionError("sina down")),
    )
    circuit = MagicMock()

    with patch.dict(sys.modules, {"akshare": fake_akshare}), patch(
        "data_provider.akshare_fetcher.get_realtime_circuit_breaker",
        return_value=circuit,
    ), patch("data_provider.akshare_fetcher.time.sleep"), patch.object(
        fetcher, "_set_random_user_agent"
    ), patch.object(fetcher, "_enforce_rate_limit"):
        actual = fetcher.get_etf_realtime_snapshot()

    assert actual.empty
    assert actual.attrs["snapshot_source"] == "dsa:akshare_etf_unavailable"
    assert actual.attrs["fallback_used"] is False
    assert actual.attrs["provider_fallback_used"] is False
    assert len(actual.attrs["source_errors"]) == 3
    assert akshare_fetcher_module._etf_realtime_cache["data"] is None
    circuit.record_failure.assert_called_once()


def test_screening_etf_snapshot_reuses_provider_identity_and_live_fallback_metadata() -> None:
    raw = pd.DataFrame(
        {
            "代码": ["sh510300"],
            "名称": ["沪深300ETF"],
            "最新价": [4.12],
            "涨跌幅": [0.49],
            "成交额": [4120000],
        }
    )
    raw.attrs.update(
        {
            "snapshot_source": "dsa:akshare_etf_sina",
            "provider_route": "akshare.fund_etf_category_sina",
            "source_errors": ["akshare.fund_etf_spot_em: em down"],
            "fallback_used": False,
            "provider_fallback_used": True,
            "stale": False,
            "stale_age_hours": None,
        }
    )
    with patch(
        "data_provider.akshare_fetcher.AkshareFetcher.get_etf_realtime_snapshot",
        return_value=raw,
    ):
        actual = fetch_cn_etf_snapshot()

    assert actual["code"].tolist() == ["510300"]
    assert actual[["price", "change_pct", "amount"]].notna().all().all()
    assert actual.attrs["snapshot_source"] == "dsa:akshare_etf_sina"
    assert actual.attrs["provider_route"] == "akshare.fund_etf_category_sina"
    assert actual.attrs["source_errors"] == [
        "akshare.fund_etf_spot_em: em down"
    ]
    assert actual.attrs["fallback_used"] is False
    assert actual.attrs["provider_fallback_used"] is True


def test_manager_normalizes_prefixed_etf_before_efinance_secid_route() -> None:
    fetcher = _make_efinance_fetcher()
    manager = DataFetcherManager(fetchers=[fetcher])
    fake_efinance = types.SimpleNamespace(
        stock=types.SimpleNamespace(get_quote_history=MagicMock(name="get_quote_history"))
    )
    call = MagicMock(return_value=_history_frame())

    with patch.dict(sys.modules, {"efinance": fake_efinance}):
        with patch("data_provider.efinance_fetcher._ef_call_with_timeout", call):
            with patch.object(fetcher, "_set_random_user_agent"), patch.object(
                fetcher, "_enforce_rate_limit"
            ):
                df, source = manager.get_daily_data(
                    "SH563230",
                    start_date="2026-01-01",
                    end_date="2026-01-05",
                )

    assert source == "EfinanceFetcher"
    assert call.call_args.kwargs["stock_codes"] == "1.563230"
    assert {"ma5", "ma10", "ma20"}.issubset(df.columns)


class _EmptyEfinanceFetcher(BaseFetcher):
    name = "EfinanceFetcher"
    priority = 0

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame()

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


class _FailingEfinanceFetcher(BaseFetcher):
    name = "EfinanceFetcher"
    priority = 0

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        raise DataFetchError("efinance ETF history failed")

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


class _SuccessfulAkshareFetcher(BaseFetcher):
    name = "AkshareFetcher"
    priority = 1

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "code": [stock_code] * 5,
                "date": pd.date_range("2026-01-01", periods=5),
                "open": [10.0, 10.1, 10.2, 10.3, 10.4],
                "high": [10.2, 10.3, 10.4, 10.5, 10.6],
                "low": [9.9, 10.0, 10.1, 10.2, 10.3],
                "close": [10.1, 10.2, 10.3, 10.4, 10.5],
                "volume": [1000, 1100, 1200, 1300, 1400],
                "amount": [10100, 11220, 12360, 13520, 14700],
                "pct_chg": [0.0, 0.99, 0.98, 0.97, 0.96],
            }
        )

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


def test_manager_falls_back_and_keeps_etf_ma_columns() -> None:
    manager = DataFetcherManager(fetchers=[_EmptyEfinanceFetcher(), _SuccessfulAkshareFetcher()])

    df, source = manager.get_daily_data("563230", start_date="2026-01-01", end_date="2026-01-05")

    assert source == "AkshareFetcher"
    assert {"ma5", "ma10", "ma20"}.issubset(df.columns)
    assert df["ma5"].iloc[-1] == 10.3


def test_manager_falls_back_when_efinance_raises_and_keeps_etf_ma_columns() -> None:
    manager = DataFetcherManager(fetchers=[_FailingEfinanceFetcher(), _SuccessfulAkshareFetcher()])

    df, source = manager.get_daily_data("563230", start_date="2026-01-01", end_date="2026-01-05")

    assert source == "AkshareFetcher"
    assert {"ma5", "ma10", "ma20"}.issubset(df.columns)
    assert df["ma5"].iloc[-1] == 10.3
