"""Independent current-market breadth contract for the existing Tushare owner.

The fixture is provider-shaped but offline. It proves exact same-day universe,
trading-status and quote coverage; it never grants source rights or historical
PIT and never replaces the existing DSA market/report owners.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


TARGET = "20261103"
OBSERVED = datetime(2026, 11, 3, 8, 30, tzinfo=timezone.utc)


def basic(*, include_bse: bool = True) -> pd.DataFrame:
    rows = [
        {"ts_code": "600001.SH", "name": "SH-A", "market": "主板", "exchange": "SSE", "list_status": "L", "list_date": "20000101", "delist_date": None},
        {"ts_code": "000001.SZ", "name": "SZ-A", "market": "主板", "exchange": "SZSE", "list_status": "L", "list_date": "19910101", "delist_date": None},
        {"ts_code": "300001.SZ", "name": "CY-A", "market": "创业板", "exchange": "SZSE", "list_status": "L", "list_date": "20091030", "delist_date": None},
    ]
    if include_bse:
        rows.append({"ts_code": "920001.BJ", "name": "BJ-A", "market": "北交所", "exchange": "BSE", "list_status": "L", "list_date": "20250102", "delist_date": None})
    return pd.DataFrame(rows)


def daily(*, include_bse: bool = True) -> pd.DataFrame:
    rows = [
        {"ts_code": "600001.SH", "trade_date": TARGET, "close": 11.0, "pre_close": 10.0, "vol": 100.0, "amount": 1000.0},
        {"ts_code": "000001.SZ", "trade_date": TARGET, "close": 9.0, "pre_close": 10.0, "vol": 200.0, "amount": 2000.0},
        {"ts_code": "300001.SZ", "trade_date": TARGET, "close": 10.0, "pre_close": 10.0, "vol": 150.0, "amount": 1500.0},
    ]
    # The BSE fixture is explicitly suspended and therefore absent from daily.
    return pd.DataFrame(rows)


def suspensions(*, include_bse: bool = True) -> pd.DataFrame:
    rows = []
    if include_bse:
        rows.append({"ts_code": "920001.BJ", "trade_date": TARGET, "suspend_type": "S"})
    return pd.DataFrame(rows, columns=["ts_code", "trade_date", "suspend_type"])


def build(*, include_bse: bool = True):
    from data_provider.tushare_market_breadth_source import build_tushare_current_market_breadth_frame

    return build_tushare_current_market_breadth_frame(
        daily(include_bse=include_bse),
        basic(include_bse=include_bse),
        suspensions(include_bse=include_bse),
        trade_date=TARGET,
        current_completed_trade_date=TARGET,
        observed_at=OBSERVED,
        research_as_of=OBSERVED,
    )


def test_three_batch_parents_compile_complete_all_a_observation() -> None:
    from data_provider.market_breadth_source import compile_market_breadth_observation

    frame = build()
    assert frame.attrs["dsa_market_breadth_context"]["universe"]["scope"] == "CN_ALL_A"
    observation = compile_market_breadth_observation(
        frame, frame.attrs["dsa_market_breadth_context"]
    )
    assert observation["facts"]["market_universe_complete"] is True
    assert observation["facts"]["coverage_scope"] == "CN_ALL_A"
    assert observation["facts"]["missing_markets"] == []
    assert observation["facts"]["excluded_by_reason"] == {"SUSPENDED": ["920001"]}
    assert (observation["facts"]["up_count"], observation["facts"]["down_count"], observation["facts"]["flat_count"]) == (1, 1, 1)
    assert observation["historical_pit_eligible"] is False
    assert observation["strategy_upgrade_authority"] is False


def test_missing_bse_is_verified_partial_not_complete_market() -> None:
    from data_provider.market_breadth_source import compile_market_breadth_observation
    from src.market_analyzer import MarketAnalyzer, MarketOverview

    frame = build(include_bse=False)
    observation = compile_market_breadth_observation(
        frame, frame.attrs["dsa_market_breadth_context"]
    )
    assert observation["facts"]["market_universe_complete"] is False
    assert observation["facts"]["coverage_scope"] == "CN_SH_SZ_A_EXCLUDES_BJ"
    assert observation["facts"]["missing_markets"] == ["BSE"]

    analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
    analyzer.profile = SimpleNamespace(has_market_stats=True)
    analyzer._build_representative_index_roles = lambda value: {}
    # A weekend/holiday market review may legitimately consume the most recent
    # completed session while preserving that source date explicitly.
    overview = MarketOverview(
        date="2026-11-04", up_count=1, down_count=1, flat_count=1,
        breadth_denominator=3, market_universe_complete=False,
        data_object_source_observations={"M_BASIC_BREADTH": observation},
    )
    brief = analyzer._build_market_regime_brief(overview)
    assert brief["breadth"]["status"] == "PARTIAL"
    assert brief["breadth"]["coverage_scope"] == "VERIFIED_PARTIAL_SH_SZ_EXCLUDES_BJ"
    assert brief["breadth"]["missing_markets"] == ["BSE"]
    assert brief["breadth"]["completed_through"] == "2026-11-03"
    assert brief["breadth"]["product_admitted"] is False


@pytest.mark.parametrize("fault", [
    "missing_daily", "overlap", "wrong_day", "duplicate_basic",
    "unknown_exchange", "historical_context", "basic_limit",
    "daily_limit", "suspend_limit",
])
def test_batch_parent_contract_rejects_incomplete_or_conflicting_inputs(fault: str) -> None:
    from data_provider.tushare_market_breadth_source import (
        TushareMarketBreadthError, build_tushare_current_market_breadth_frame,
    )

    d, b, s = daily(), basic(), suspensions()
    if fault == "missing_daily":
        d = d[d["ts_code"] != "300001.SZ"].copy()
    elif fault == "overlap":
        s = pd.concat([s, pd.DataFrame([{"ts_code": "600001.SH", "trade_date": TARGET, "suspend_type": "S"}])], ignore_index=True)
    elif fault == "wrong_day":
        d.loc[0, "trade_date"] = "20261102"
    elif fault == "duplicate_basic":
        b = pd.concat([b, b.iloc[[0]]], ignore_index=True)
    elif fault == "unknown_exchange":
        b.loc[0, "exchange"] = "UNKNOWN"
    elif fault == "basic_limit":
        b = pd.concat([b.iloc[[0]]] * 6000, ignore_index=True)
    elif fault == "daily_limit":
        d = pd.concat([d.iloc[[0]]] * 6000, ignore_index=True)
    elif fault == "suspend_limit":
        s = pd.concat([s.iloc[[0]]] * 5000, ignore_index=True)
    current_completed = "20261104" if fault == "historical_context" else TARGET
    with pytest.raises(TushareMarketBreadthError):
        build_tushare_current_market_breadth_frame(
            d, b, s, trade_date=TARGET,
            current_completed_trade_date=current_completed,
            observed_at=OBSERVED, research_as_of=OBSERVED,
        )


def test_market_consumer_rejects_source_after_review_date() -> None:
    from data_provider.market_breadth_source import compile_market_breadth_observation
    from src.market_analyzer import MarketAnalyzer, MarketOverview

    frame = build()
    observation = compile_market_breadth_observation(
        frame, frame.attrs["dsa_market_breadth_context"]
    )
    analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
    analyzer.profile = SimpleNamespace(has_market_stats=True)
    analyzer._build_representative_index_roles = lambda value: {}
    overview = MarketOverview(
        date="2026-11-02", up_count=1, down_count=1, flat_count=1,
        breadth_denominator=3,
        data_object_source_observations={"M_BASIC_BREADTH": observation},
    )
    with pytest.raises(ValueError, match="BREADTH_SOURCE_AFTER_REVIEW_DATE"):
        analyzer._build_market_regime_brief(overview)


@pytest.mark.parametrize("field,value", [
    ("missing_markets", []),
    ("market_universe_complete", True),
    ("coverage_scope", "CN_ALL_A"),
])
def test_partial_scope_cannot_be_resealed_as_complete(field, value) -> None:
    from copy import deepcopy
    from data_provider.daily_data_identity import sha256_payload
    from data_provider.market_breadth_source import (
        MarketBreadthError, compile_market_breadth_observation,
        validate_market_breadth_observation,
    )

    frame = build(include_bse=False)
    observation = compile_market_breadth_observation(
        frame, frame.attrs["dsa_market_breadth_context"]
    )
    forged = deepcopy(observation)
    forged["facts"][field] = value
    forged.pop("observation_hash")
    forged["observation_hash"] = sha256_payload(forged)
    with pytest.raises(MarketBreadthError):
        validate_market_breadth_observation(
            forged, target_date="2026-11-03"
        )


def test_existing_tushare_market_stats_uses_three_batch_endpoints() -> None:
    from data_provider.tushare_fetcher import TushareFetcher

    fetcher = TushareFetcher.__new__(TushareFetcher)
    fetcher._api = MagicMock()
    fetcher.date_list = None
    fetcher._date_list_end = None
    fetcher._get_china_now = MagicMock(return_value=datetime(2026, 11, 4, 18, 0))
    fetcher._get_trade_dates = MagicMock(return_value=["20261104", TARGET])
    fetcher._pick_trade_date = MagicMock(return_value=TARGET)
    responses = {
        "daily": daily(), "stock_basic": basic(), "suspend_d": suspensions(),
    }
    fetcher._call_api_with_rate_limit = MagicMock(side_effect=lambda name, **kwargs: responses[name].copy())

    result = fetcher.get_market_stats()

    assert result is not None
    assert result["market_universe_complete"] is True
    assert result["breadth_denominator"] == 3
    assert result["data_object_source_observations"]["M_BASIC_BREADTH"]["facts"]["universe_count"] == 4
    names = [call.args[0] for call in fetcher._call_api_with_rate_limit.call_args_list]
    assert names == ["daily", "stock_basic", "suspend_d"]
    daily_call, basic_call, suspend_call = fetcher._call_api_with_rate_limit.call_args_list
    assert daily_call.kwargs["trade_date"] == TARGET
    assert "ts_code" not in daily_call.kwargs
    assert basic_call.kwargs["list_status"] == "L"
    assert "exchange" in basic_call.kwargs["fields"]
    assert suspend_call.kwargs == {
        "trade_date": TARGET,
        "suspend_type": "S",
        "fields": "ts_code,trade_date,suspend_type",
    }


def test_provider_failure_never_falls_back_to_unverified_counts() -> None:
    from data_provider.tushare_fetcher import TushareFetcher

    fetcher = TushareFetcher.__new__(TushareFetcher)
    fetcher._api = MagicMock()
    fetcher._get_china_now = MagicMock(return_value=datetime(2026, 11, 4, 18, 0))
    fetcher._get_trade_dates = MagicMock(return_value=["20261104", TARGET])
    fetcher._pick_trade_date = MagicMock(return_value=TARGET)
    fetcher._call_api_with_rate_limit = MagicMock(side_effect=RuntimeError("suspend source unavailable"))
    assert fetcher.get_market_stats() is None


def test_market_consumer_clears_counts_after_receipt_validation_failure() -> None:
    from data_provider.market_breadth_source import compile_market_breadth_observation
    from src.market_analyzer import MarketAnalyzer, MarketOverview

    frame = build()
    observation = compile_market_breadth_observation(
        frame, frame.attrs["dsa_market_breadth_context"]
    )
    analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
    analyzer.region = "cn"
    analyzer._log_context = lambda: "test"
    analyzer.data_manager = MagicMock()
    analyzer.data_manager.get_market_stats.return_value = {
        "up_count": 99,
        "down_count": 1,
        "flat_count": 1,
        "limit_up_count": 4,
        "limit_down_count": 2,
        "total_amount": 123.0,
        "breadth_denominator": 101,
        "data_object_source_observations": {
            "M_BASIC_BREADTH": observation,
        },
    }
    overview = MarketOverview(date="2026-11-04")

    analyzer._get_market_statistics(overview)

    assert (overview.up_count, overview.down_count, overview.flat_count) == (0, 0, 0)
    assert overview.breadth_denominator == 0
    assert overview.total_amount == 0.0
    assert overview.data_object_source_observations == {}
    assert overview.market_universe_complete is False
