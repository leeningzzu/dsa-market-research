# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import MagicMock

import pandas as pd

from data_provider.daily_data_identity import (
    attach_daily_data_identity,
    build_daily_data_identity,
)

from src.services.relative_strength_service import (
    CN_BENCHMARK_PROXY,
    RelativeStrengthService,
    compute_completed_timeframe_etf_peer_relative_strength_context,
    compute_completed_timeframe_relative_strength_context,
    compute_etf_peer_relative_strength_context,
    compute_relative_strength_context,
)


def _history(*, periods: int = 70, start_price: float = 100.0, end_price: float = 110.0) -> pd.DataFrame:
    dates = pd.bdate_range("2026-01-02", periods=periods)
    closes = [start_price + (end_price - start_price) * i / max(periods - 1, 1) for i in range(periods)]
    return pd.DataFrame({"date": dates, "close": closes})


def _with_identity(frame: pd.DataFrame, *, provider: str = "Fetcher") -> pd.DataFrame:
    frame = frame.copy()
    frame["data_source"] = provider
    start = pd.to_datetime(frame.iloc[0]["date"]).date().isoformat()
    end = pd.to_datetime(frame.iloc[-1]["date"]).date().isoformat()
    identity = build_daily_data_identity(
        frame,
        provider_identity=provider,
        provider_route="synthetic-test",
        actual_response_branch="synthetic_qfq",
        requested_adjustment_basis="qfq",
        observed_adjustment_basis="qfq",
        basis_evidence="SYNTHETIC_TEST_EXPLICIT",
        requested_start=start,
        requested_end=end,
        currency="CNY",
        volume_unit="shares",
        amount_unit="CNY",
        identity_state="OBSERVED",
    )
    return attach_daily_data_identity(frame, identity)


def _with_identity_only(
    frame: pd.DataFrame,
    *,
    provider: str = "Fetcher",
) -> pd.DataFrame:
    frame = frame.copy()
    start = pd.to_datetime(frame.iloc[0]["date"]).date().isoformat()
    end = pd.to_datetime(frame.iloc[-1]["date"]).date().isoformat()
    identity = build_daily_data_identity(
        frame,
        provider_identity=provider,
        provider_route="synthetic-typed-no-row-source",
        actual_response_branch="synthetic_qfq",
        requested_adjustment_basis="qfq",
        observed_adjustment_basis="qfq",
        basis_evidence="SYNTHETIC_TEST_EXPLICIT",
        requested_start=start,
        requested_end=end,
        currency="CNY",
        volume_unit="shares",
        amount_unit="CNY",
        identity_state="OBSERVED",
    )
    return attach_daily_data_identity(frame, identity)


def test_completed_timeframe_relative_strength_is_generic_source_bound_and_fail_closed():
    target = pd.DataFrame({
        "bar_end": pd.date_range("2026-10-01 10:00", periods=30, freq="30min", tz="Asia/Shanghai"),
        "close": [100.0 + index for index in range(30)],
    })
    benchmark = target.copy()
    benchmark["close"] = [100.0 + index * 0.5 for index in range(30)]
    common = {
        "provider_identity": "FixtureFetcher",
        "adjustment_basis": "qfq",
        "timeframe": "30m",
        "completed_bar_only": True,
        "available_at_max": target.iloc[-1]["bar_end"].isoformat(),
    }
    result = compute_completed_timeframe_relative_strength_context(
        target_code="600519",
        benchmark_code="510300",
        market="cn",
        timeframe="30m",
        target_bars=target,
        benchmark_bars=benchmark,
        target_bar_end=target.iloc[-1]["bar_end"],
        target_input_identity={**common, "identity_hash": "a" * 64},
        benchmark_input_identity={**common, "identity_hash": "b" * 64},
        horizon_bars=20,
    )
    assert result["status"] == "READY"
    assert result["reason"] == "EXACT_SHARED_COMPLETED_BAR_ENDPOINTS_SOURCE_ALIGNED"
    assert result["timeframe"] == "30m"
    assert result["relative"]["relative_ratio_change_pct"] > 0
    assert result["input_identity"]["target"]["identity_hash"] == "a" * 64
    assert result["input_identity"]["benchmark"]["identity_hash"] == "b" * 64
    assert result["independent_action_authority"] is False

    mismatched = compute_completed_timeframe_relative_strength_context(
        target_code="600519",
        benchmark_code="510300",
        market="cn",
        timeframe="30m",
        target_bars=target,
        benchmark_bars=benchmark,
        target_bar_end=target.iloc[-1]["bar_end"],
        target_input_identity={**common, "identity_hash": "a" * 64},
        benchmark_input_identity={
            **common, "provider_identity": "OtherFetcher", "identity_hash": "b" * 64,
        },
        horizon_bars=20,
    )
    assert mismatched["status"] == "UNKNOWN"
    assert mismatched["reason"] == "TIMEFRAME_RS_SOURCE_OR_BASIS_MISMATCH"


def test_completed_timeframe_etf_peer_rs_is_source_bound_ranked_and_order_stable():
    endpoint = pd.date_range(
        "2026-10-01 10:00", periods=30, freq="30min", tz="Asia/Shanghai",
    )
    target = pd.DataFrame({
        "bar_end": endpoint,
        "close": [100.0 + index for index in range(30)],
    })
    peers = {
        "510050": pd.DataFrame({
            "bar_end": endpoint,
            "close": [100.0 + index * 0.2 for index in range(30)],
        }),
        "159919": pd.DataFrame({
            "bar_end": endpoint,
            "close": [100.0 + index * 0.4 for index in range(30)],
        }),
        "510500": pd.DataFrame({
            "bar_end": endpoint,
            "close": [100.0 + index * 0.3 for index in range(30)],
        }),
    }
    base_identity = {
        "provider_identity": "FixtureFetcher",
        "adjustment_basis": "qfq",
        "timeframe": "30m",
        "completed_bar_only": True,
        "available_at_max": endpoint[-1].isoformat(),
    }
    target_identity = {**base_identity, "identity_hash": "a" * 64}
    peer_identities = {
        code: {**base_identity, "identity_hash": char * 64}
        for code, char in zip(peers, ("b", "c", "d"))
    }
    first = compute_completed_timeframe_etf_peer_relative_strength_context(
        target_code="510300",
        market="cn",
        timeframe="30m",
        target_bars=target,
        peer_bars=peers,
        target_bar_end=endpoint[-1],
        target_input_identity=target_identity,
        peer_input_identities=peer_identities,
        horizon_bars=20,
        min_peer_count=3,
    )
    reversed_peers = dict(reversed(list(peers.items())))
    second = compute_completed_timeframe_etf_peer_relative_strength_context(
        target_code="510300",
        market="cn",
        timeframe="30m",
        target_bars=target,
        peer_bars=reversed_peers,
        target_bar_end=endpoint[-1],
        target_input_identity=target_identity,
        peer_input_identities=peer_identities,
        horizon_bars=20,
        min_peer_count=3,
    )
    assert first["status"] == "READY"
    assert first["reason"] == "TIMEFRAME_ETF_PEER_UNIVERSE_READY"
    assert first["relative"]["rank"] == 1
    assert first["relative"]["rank_denominator"] == 4
    assert first["relative"]["state"] == "OUTPERFORMING"
    assert first["peer_universe"]["eligible_peer_count"] == 3
    assert first["peer_universe"]["universe_hash"] == second["peer_universe"]["universe_hash"]
    assert first["peers"] == second["peers"]
    assert first["independent_action_authority"] is False

    bad_identities = dict(peer_identities)
    bad_identities["510500"] = {
        **bad_identities["510500"], "provider_identity": "OtherFetcher",
    }
    blocked = compute_completed_timeframe_etf_peer_relative_strength_context(
        target_code="510300",
        market="cn",
        timeframe="30m",
        target_bars=target,
        peer_bars=peers,
        target_bar_end=endpoint[-1],
        target_input_identity=target_identity,
        peer_input_identities=bad_identities,
        horizon_bars=20,
        min_peer_count=3,
    )
    assert blocked["status"] == "MISSING"
    assert blocked["reason"] == "TIMEFRAME_ETF_PEER_UNIVERSE_INSUFFICIENT"
    assert blocked["peer_universe"]["excluded_peers"] == [{
        "code": "510500",
        "reason": "TIMEFRAME_ETF_PEER_SOURCE_BASIS_OR_IDENTITY_MISMATCH",
    }]


def test_completed_timeframe_stock_rs_requires_entire_same_bar_grid_not_only_endpoints() -> None:
    """A missing/misaligned middle 30m bar must not produce a 20-bar RS READY."""
    ends = pd.date_range(
        "2026-10-01 10:00", periods=30, freq="30min", tz="Asia/Shanghai",
    )
    benchmark = pd.DataFrame({
        "bar_end": ends, "close": [100.0 + i * 0.2 for i in range(30)],
    })
    target = benchmark.copy()
    target["close"] = [100.0 + i * 0.4 for i in range(30)]
    identity = {
        "provider_identity": "FixtureFetcher", "adjustment_basis": "qfq",
        "timeframe": "30m", "completed_bar_only": True,
        "available_at_max": ends[-1].isoformat(),
    }
    good = compute_completed_timeframe_relative_strength_context(
        target_code="600519", benchmark_code="510300", market="cn",
        timeframe="30m", target_bars=target, benchmark_bars=benchmark,
        target_bar_end=ends[-1],
        target_input_identity={**identity, "identity_hash": "a" * 64},
        benchmark_input_identity={**identity, "identity_hash": "b" * 64},
        horizon_bars=20,
    )
    assert good["status"] == "READY"

    broken = target.copy()
    broken.loc[21, "bar_end"] = ends[21] + pd.Timedelta(minutes=5)
    bad = compute_completed_timeframe_relative_strength_context(
        target_code="600519", benchmark_code="510300", market="cn",
        timeframe="30m", target_bars=broken, benchmark_bars=benchmark,
        target_bar_end=ends[-1],
        target_input_identity={**identity, "identity_hash": "a" * 64},
        benchmark_input_identity={**identity, "identity_hash": "b" * 64},
        horizon_bars=20,
    )
    assert bad["status"] == "MISSING"
    assert bad["reason"] == "TIMEFRAME_RS_FULL_COMPLETED_GRID_MISMATCH"
    assert bad["relative"] == {}
    assert bad["independent_action_authority"] is False


def test_completed_timeframe_etf_peer_rs_excludes_middle_bar_grid_mismatch() -> None:
    """One peer with only end-point alignment must not inflate eligible rank."""
    ends = pd.date_range(
        "2026-10-01 10:00", periods=30, freq="30min", tz="Asia/Shanghai",
    )
    target = pd.DataFrame({
        "bar_end": ends, "close": [100.0 + i * 0.3 for i in range(30)],
    })
    peers = {
        code: pd.DataFrame({
            "bar_end": ends, "close": [100.0 + i * multiplier for i in range(30)],
        })
        for code, multiplier in (
            ("510050", 0.2), ("510500", 0.4), ("159919", 0.1),
        )
    }
    ident = {
        "provider_identity": "FixtureFetcher", "adjustment_basis": "qfq",
        "timeframe": "30m", "completed_bar_only": True,
        "available_at_max": ends[-1].isoformat(),
    }
    target_id = {**ident, "identity_hash": "a" * 64}
    peer_ids = {
        code: {**ident, "identity_hash": char * 64}
        for code, char in (("510050", "b"), ("510500", "c"), ("159919", "d"))
    }
    good = compute_completed_timeframe_etf_peer_relative_strength_context(
        target_code="510300", market="cn", timeframe="30m",
        target_bars=target, peer_bars=peers, target_bar_end=ends[-1],
        target_input_identity=target_id, peer_input_identities=peer_ids,
        horizon_bars=20, min_peer_count=3,
    )
    assert good["status"] == "READY"
    broken_peers = dict(peers)
    broken_peers["510500"] = peers["510500"].copy()
    broken_peers["510500"].loc[21, "bar_end"] = ends[21] + pd.Timedelta(minutes=5)
    bad = compute_completed_timeframe_etf_peer_relative_strength_context(
        target_code="510300", market="cn", timeframe="30m",
        target_bars=target, peer_bars=broken_peers, target_bar_end=ends[-1],
        target_input_identity=target_id, peer_input_identities=peer_ids,
        horizon_bars=20, min_peer_count=3,
    )
    assert bad["status"] == "MISSING"
    assert bad["reason"] == "TIMEFRAME_ETF_PEER_UNIVERSE_INSUFFICIENT"
    assert bad["peer_universe"]["eligible_peer_count"] == 2
    assert bad["peer_universe"]["excluded_peers"] == [{
        "code": "510500", "reason": "TIMEFRAME_ETF_PEER_FULL_COMPLETED_GRID_MISMATCH",
    }]
    assert bad["relative"] == {}


def test_exact_shared_endpoint_relative_strength_is_ready_and_formula_is_auditable():
    benchmark = _history(periods=70, start_price=100.0, end_price=110.0)
    target = benchmark.iloc[-1]["date"].date()
    stock = benchmark.copy()
    stock["close"] = stock["close"] * pd.Series(
        [1.0 + 0.20 * i / (len(stock) - 1) for i in range(len(stock))]
    )

    context = compute_relative_strength_context(
        stock_code="600519",
        market="cn",
        stock_history=stock,
        benchmark_history=benchmark,
        benchmark={**CN_BENCHMARK_PROXY, "source": "test"},
        target_date=target,
    )

    assert context["status"] == "READY"
    assert context["benchmark"]["kind"] == "etf_proxy"
    assert context["benchmark"]["code"] == "510300"
    assert context["benchmark"]["observations"] == 61
    assert context["benchmark"]["end_date"] == target.isoformat()
    assert context["relative"]["state"] == "OUTPERFORMING"
    assert context["relative"]["relative_ratio_change_pct"] > 0
    assert context["relative"]["excess_return_pct_points"] > 0


def test_typed_identity_provider_binds_source_without_row_label():
    benchmark = _with_identity_only(_history(periods=70))
    stock = _with_identity_only(_history(periods=70, end_price=125.0))
    target = benchmark.iloc[-1]["date"].date()

    context = compute_relative_strength_context(
        stock_code="600519",
        market="cn",
        stock_history=stock,
        benchmark_history=benchmark,
        benchmark={**CN_BENCHMARK_PROXY, "source": "Fetcher"},
        target_date=target,
        require_source_alignment=True,
    )

    assert context["status"] == "READY"
    assert context["reason"] == "EXACT_SHARED_ENDPOINTS_SOURCE_ALIGNED"
    assert context["data_quality"]["source_alignment"] == "MATCHED"
    assert context["data_quality"]["stock_endpoint_sources"] == ["Fetcher"]


def test_benchmark_warmup_and_exact_target_are_fail_closed():
    short_benchmark = _history(periods=60)
    target = short_benchmark.iloc[-1]["date"].date()
    missing = compute_relative_strength_context(
        stock_code="600519",
        market="cn",
        stock_history=short_benchmark,
        benchmark_history=short_benchmark,
        benchmark=CN_BENCHMARK_PROXY,
        target_date=target,
    )
    assert missing["status"] == "MISSING"
    assert missing["reason"] == "BENCHMARK_WARMUP_INSUFFICIENT"

    benchmark = _history(periods=70)
    future_target = benchmark.iloc[-1]["date"].date() + pd.Timedelta(days=1)
    target_missing = compute_relative_strength_context(
        stock_code="600519",
        market="cn",
        stock_history=benchmark,
        benchmark_history=benchmark,
        benchmark=CN_BENCHMARK_PROXY,
        target_date=future_target,
    )
    assert target_missing["status"] == "MISSING"
    assert target_missing["reason"] == "BENCHMARK_TARGET_DATE_MISSING"


def test_stock_must_have_same_benchmark_start_and_end_dates():
    benchmark = _history(periods=70)
    target = benchmark.iloc[-1]["date"].date()
    benchmark_window = benchmark.tail(61)
    shared_start = benchmark_window.iloc[0]["date"]
    stock = benchmark[benchmark["date"] != shared_start].copy()

    context = compute_relative_strength_context(
        stock_code="600519",
        market="cn",
        stock_history=stock,
        benchmark_history=benchmark,
        benchmark=CN_BENCHMARK_PROXY,
        target_date=target,
    )
    assert context["status"] == "MISSING"
    assert context["reason"] == "STOCK_SHARED_ENDPOINT_MISSING"


def test_future_rows_are_trimmed_and_underperformance_is_preserved():
    benchmark = _history(periods=70, start_price=100.0, end_price=120.0)
    target = benchmark.iloc[-2]["date"].date()
    stock = benchmark.copy()
    stock["close"] = [100.0 + 5.0 * i / (len(stock) - 1) for i in range(len(stock))]

    context = compute_relative_strength_context(
        stock_code="600519",
        market="cn",
        stock_history=stock,
        benchmark_history=benchmark,
        benchmark=CN_BENCHMARK_PROXY,
        target_date=target,
    )
    assert context["status"] == "READY"
    assert context["benchmark"]["end_date"] == target.isoformat()
    assert context["relative"]["state"] == "UNDERPERFORMING"
    assert context["relative"]["relative_ratio_change_pct"] < 0


def test_service_caches_one_benchmark_fetch_per_target_date_and_rejects_non_cn():
    benchmark = _with_identity(_history(periods=70), provider="Fetcher")
    target = benchmark.iloc[-1]["date"].date()
    manager = MagicMock()
    manager.get_daily_data.return_value = (benchmark, "Fetcher")
    service = RelativeStrengthService(fetcher_manager=manager)

    stock_history = _with_identity(benchmark, provider="Fetcher")
    first = service.build_context(
        stock_code="600519", market="cn", stock_history=stock_history, target_date=target
    )
    second = service.build_context(
        stock_code="000001", market="cn", stock_history=stock_history, target_date=target
    )
    unsupported = service.build_context(
        stock_code="AAPL", market="us", stock_history=benchmark, target_date=target
    )

    assert first["status"] == "READY"
    assert second["status"] == "READY"
    assert unsupported["status"] == "NOT_SUPPORTED"
    manager.get_daily_data.assert_called_once_with(
        "510300", end_date=target.isoformat(), days=120
    )


def test_service_marks_cross_provider_price_return_as_partial_not_ready():
    benchmark = _with_identity(_history(periods=70), provider="BenchmarkFetcher")
    target = benchmark.iloc[-1]["date"].date()
    manager = MagicMock()
    manager.get_daily_data.return_value = (benchmark, "BenchmarkFetcher")
    service = RelativeStrengthService(fetcher_manager=manager)
    stock = _with_identity(benchmark, provider="StockFetcher")

    context = service.build_context(
        stock_code="600519", market="cn", stock_history=stock, target_date=target
    )
    assert context["status"] == "PARTIAL"
    assert context["reason"] == "SOURCE_ALIGNMENT_UNPROVEN"
    assert context["data_quality"]["source_alignment"] == "UNPROVEN"
    assert context["relative"]["state"] in {"OUTPERFORMING", "UNDERPERFORMING", "NEUTRAL"}


def test_service_provider_name_without_typed_price_identity_is_not_ready():
    benchmark = _history(periods=70).assign(data_source="Fetcher")
    target = benchmark.iloc[-1]["date"].date()
    manager = MagicMock()
    manager.get_daily_data.return_value = (benchmark, "Fetcher")
    service = RelativeStrengthService(fetcher_manager=manager)

    context = service.build_context(
        stock_code="600519", market="cn", stock_history=benchmark, target_date=target
    )

    assert context["status"] == "PARTIAL"
    assert context["reason"] == "SEMANTIC_PRICE_IDENTITY_UNPROVEN"
    assert context["input_identity"]["stock"]["adjustment_basis"] is None
    assert context["input_identity"]["benchmark"]["adjustment_basis"] is None


def test_service_benchmark_fetch_failure_is_unknown_not_fabricated():
    manager = MagicMock()
    manager.get_daily_data.side_effect = RuntimeError("provider unavailable")
    service = RelativeStrengthService(fetcher_manager=manager)
    stock = _history(periods=70)
    target = stock.iloc[-1]["date"].date()

    context = service.build_context(
        stock_code="600519", market="cn", stock_history=stock, target_date=target
    )
    assert context["status"] == "UNKNOWN"
    assert context["reason"] == "BENCHMARK_FETCH_UNAVAILABLE"


def _horizon_return(frame: pd.DataFrame, sessions: int = 60) -> float:
    ordered = frame.sort_values("date").reset_index(drop=True)
    start = float(ordered.iloc[-(sessions + 1)]["close"])
    end = float(ordered.iloc[-1]["close"])
    return (end / start - 1.0) * 100.0


def test_etf_peer_rs_uses_one_asof_shared_endpoints_and_deterministic_rank():
    target = _with_identity(
        _history(periods=70, start_price=100.0, end_price=140.0),
        provider="Fetcher",
    )
    peers = {
        "510050": _with_identity(
            _history(periods=70, start_price=100.0, end_price=110.0),
            provider="Fetcher",
        ),
        "159919": _with_identity(
            _history(periods=70, start_price=100.0, end_price=130.0),
            provider="Fetcher",
        ),
        "510500": _with_identity(
            _history(periods=70, start_price=100.0, end_price=120.0),
            provider="Fetcher",
        ),
    }
    target_date = target.iloc[-1]["date"].date()

    context = compute_etf_peer_relative_strength_context(
        etf_code="510300",
        market="cn",
        target_history=target,
        peer_histories=peers,
        target_date=target_date,
        min_peer_count=3,
    )
    reversed_context = compute_etf_peer_relative_strength_context(
        etf_code="510300",
        market="cn",
        target_history=target,
        peer_histories=dict(reversed(list(peers.items()))),
        target_date=target_date,
        min_peer_count=3,
    )

    expected_returns = sorted(_horizon_return(frame) for frame in peers.values())
    assert context["status"] == "READY"
    assert context["reason"] == "EXACT_SHARED_ENDPOINTS_PEER_UNIVERSE_READY"
    assert context["target"]["return_pct"] == round(_horizon_return(target), 6)
    assert context["peer_universe"]["eligible_peer_count"] == 3
    assert context["peer_universe"]["excluded_peer_count"] == 0
    assert context["relative"]["rank"] == 1
    assert context["relative"]["rank_denominator"] == 4
    assert context["relative"]["percentile"] == 100.0
    assert context["relative"]["peer_median_return_pct"] == round(expected_returns[1], 6)
    assert context["relative"]["state"] == "OUTPERFORMING"
    assert context["data_quality"]["provider_identity"] == "Fetcher"
    assert context["data_quality"]["adjustment_basis"] == "qfq"
    assert context["peer_universe"]["universe_hash"] == reversed_context["peer_universe"]["universe_hash"]
    assert context["peers"] == reversed_context["peers"]
    assert context["independent_action_authority"] is False
    assert context["strategy_admitted"] is False
    assert context["learning_admitted"] is False
    assert context["data_rights_admitted"] is False


def test_etf_peer_rs_rejects_cross_provider_and_missing_shared_endpoint():
    target = _with_identity(
        _history(periods=70, start_price=100.0, end_price=120.0),
        provider="Fetcher",
    )
    target_date = target.iloc[-1]["date"].date()
    shared = _with_identity(
        _history(periods=70, start_price=100.0, end_price=115.0),
        provider="Fetcher",
    )
    cross_provider = _with_identity(
        _history(periods=70, start_price=100.0, end_price=130.0),
        provider="OtherFetcher",
    )
    missing_start = _with_identity(
        _history(periods=50, start_price=100.0, end_price=125.0),
        provider="Fetcher",
    )

    context = compute_etf_peer_relative_strength_context(
        etf_code="510300",
        market="cn",
        target_history=target,
        peer_histories={
            "510050": shared,
            "159919": cross_provider,
            "510500": missing_start,
        },
        target_date=target_date,
        min_peer_count=2,
    )

    assert context["status"] == "MISSING"
    assert context["reason"] == "ETF_PEER_UNIVERSE_INSUFFICIENT"
    assert context["peer_universe"]["eligible_peer_count"] == 1
    excluded = {
        row["code"]: row["reason"]
        for row in context["peer_universe"]["excluded_peers"]
    }
    assert excluded["159919"] == "PEER_SOURCE_OR_BASIS_MISMATCH"
    assert excluded["510500"] == "PEER_SHARED_ENDPOINT_MISSING"
    assert context["independent_action_authority"] is False
    assert context["strategy_admitted"] is False
    assert context["data_rights_admitted"] is False
