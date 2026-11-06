"""Offline independent breadth coverage and provenance tests, not live market facts."""
from copy import deepcopy
from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest


def frame():
    return pd.DataFrame({
        "代码": ["600001", "000001", "600002"],
        "名称": ["fixture-a", "fixture-b", "fixture-c"],
        "最新价": [11.0, 9.0, 10.0], "昨收": [10.0, 10.0, 10.0],
        "成交额": [1000.0, 2000.0, 0.0],
        "trading_status": ["TRADING", "TRADING", "SUSPENDED"],
    })


def context(df=None):
    from data_provider.market_breadth_source import market_snapshot_content_hash, seal_breadth_universe
    df = frame() if df is None else df
    return {
        "provider_identity": "SyntheticSnapshotProvider",
        "provider_route": "synthetic.all_a_close",
        "source_content_sha256": market_snapshot_content_hash(df),
        "source_observed_at": "2026-10-30T08:05:00+00:00",
        "source_available_at": "2026-10-30T07:05:00+00:00",
        "completed_at": "2026-10-30T07:00:00+00:00",
        "trade_date": "2026-10-30", "currency": "CNY", "amount_unit": "CNY",
        "price_basis": "RAW", "research_as_of": "2026-10-30T08:06:00+00:00",
        "universe": seal_breadth_universe({
            "scope": "CN_ALL_A", "trade_date": "2026-10-30",
            "codes": ["600001", "000001", "600002"],
            "source_identity_hash": "a" * 64,
            "available_at": "2026-10-30T06:00:00+00:00",
        }),
    }


@pytest.mark.parametrize("provider", ["akshare", "efinance", "tushare"])
def test_existing_three_market_stats_owners_reject_duplicate_security(provider):
    import importlib
    cls = {"akshare": "AkshareFetcher", "efinance": "EfinanceFetcher", "tushare": "TushareFetcher"}[provider]
    owner = getattr(importlib.import_module("data_provider." + provider + "_fetcher"), cls)
    duplicated = pd.concat([frame(), frame().iloc[[0]]], ignore_index=True)
    with pytest.raises(ValueError, match="BREADTH_DUPLICATE_INSTRUMENT"):
        owner._calc_market_stats(owner.__new__(owner), duplicated)


def test_existing_snapshot_owner_preserves_denominator_and_typed_observation():
    from data_provider.akshare_fetcher import AkshareFetcher
    df = frame()
    df.attrs["dsa_market_breadth_context"] = context(df)
    result = AkshareFetcher._calc_market_stats(AkshareFetcher.__new__(AkshareFetcher), df)
    assert result["up_count"] == result["down_count"] == 1
    assert result["flat_count"] == 0
    assert result["breadth_denominator"] == 2
    assert result["excluded_count"] == 1
    observation = result["data_object_source_observations"]["M_BASIC_BREADTH"]
    assert observation["facts"]["eligible_count"] == 2
    assert observation["facts"]["universe_count"] == 3
    assert observation["facts"]["excluded_by_reason"] == {"SUSPENDED": ["600002"]}
    assert observation["historical_pit_eligible"] is False
    assert observation["strategy_upgrade_authority"] is False


def test_typed_breadth_enters_existing_registry_without_granting_product():
    from data_provider.market_breadth_source import compile_market_breadth_observation
    from src.services.evidence_traceability_registry import compile_complete_research_universe_view
    obs = compile_market_breadth_observation(frame(), context())
    view = compile_complete_research_universe_view({
        "asset_type": "stock", "data_object_source_observations": {"M_BASIC_BREADTH": obs},
    })
    row = next(x for x in view["data_objects"] if x["dataset_id"] == "M_BASIC_BREADTH")
    assert row["state"] == "PARTIAL"
    assert row["source_observation_hash"] == obs["observation_hash"]
    # Generic source rows do not own a rights field. Verify the actual
    # admission consumer, rather than inventing a field on this projection.
    from src.services.data_object_admission import compile_data_object_admission_view
    admission = compile_data_object_admission_view(view["data_objects"])
    assert admission["product_admitted_count"] == 0
    assert admission["learning_admitted_count"] == 0
    assert row["strategy_upgrade_authority"] is False


@pytest.mark.parametrize("fault", ["missing_member", "duplicate_member", "wrong_day", "future", "changed_value", "wrong_unit", "missing_status"])
def test_breadth_rejects_incomplete_or_mistimed_snapshot(fault):
    from data_provider.market_breadth_source import compile_market_breadth_observation, market_snapshot_content_hash
    df, ctx = frame(), context()
    if fault == "missing_member":
        df = df.iloc[:2].copy()
        ctx["source_content_sha256"] = market_snapshot_content_hash(df)
    elif fault == "duplicate_member":
        ctx["universe"]["codes"].append("600001")
    elif fault == "wrong_day":
        ctx["trade_date"] = "2026-10-29"
    elif fault == "future":
        ctx["source_observed_at"] = "2026-10-31T08:05:00+00:00"
    elif fault == "changed_value":
        df.loc[0, "最新价"] = 12.0
    elif fault == "wrong_unit":
        ctx["amount_unit"] = "UNKNOWN"
    else:
        df = df.drop(columns=["trading_status"])
    with pytest.raises(ValueError, match="BREADTH_"):
        compile_market_breadth_observation(df, ctx)


def test_counts_alone_are_not_complete_market_or_typed_product():
    from data_provider.akshare_fetcher import AkshareFetcher
    result = AkshareFetcher._calc_market_stats(AkshareFetcher.__new__(AkshareFetcher), frame())
    assert result["breadth_denominator"] == 2
    assert result["market_universe_complete"] is False
    assert result.get("data_object_source_observations", {}) == {}


def test_market_analyzer_preserves_breadth_evidence_not_only_six_scalars():
    from data_provider.akshare_fetcher import AkshareFetcher
    from src.market_analyzer import MarketAnalyzer, MarketOverview
    df = frame(); df.attrs["dsa_market_breadth_context"] = context(df)
    stats = AkshareFetcher._calc_market_stats(AkshareFetcher.__new__(AkshareFetcher), df)
    analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
    analyzer.region = "cn"
    analyzer.data_manager = MagicMock()
    analyzer._log_context = lambda: "offline"
    analyzer.data_manager.get_market_stats.return_value = stats
    overview = MarketOverview(date="2026-10-30")
    analyzer._get_market_statistics(overview)
    assert overview.breadth_denominator == 2
    assert overview.market_universe_complete is True
    assert overview.data_object_source_observations["M_BASIC_BREADTH"] == stats["data_object_source_observations"]["M_BASIC_BREADTH"]
    analyzer.profile = MagicMock(has_market_stats=True)
    analyzer._build_representative_index_roles = lambda value: {}
    brief = analyzer._build_market_regime_brief(overview)
    assert brief["breadth"]["coverage_scope"] == "VERIFIED_COMPLETE_UNIVERSE"
    assert brief["breadth"]["product_admitted"] is False
    overview.market_universe_complete = False
    overview.data_object_source_observations = {}
    assert analyzer._build_market_regime_brief(overview)["breadth"]["status"] == "PARTIAL"


def test_market_statistics_failure_clears_previous_observation():
    from src.market_analyzer import MarketAnalyzer, MarketOverview
    overview = MarketOverview(date="2026-10-30", up_count=99)
    overview.breadth_denominator = 99
    overview.market_universe_complete = True
    overview.data_object_source_observations = {"M_BASIC_BREADTH": {"old": True}}
    analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
    analyzer.region = "cn"; analyzer._log_context = lambda: "offline"
    analyzer.data_manager = MagicMock()
    analyzer.data_manager.get_market_stats.return_value = {}
    analyzer._get_market_statistics(overview)
    assert overview.up_count == 0
    assert overview.market_universe_complete is False
    assert overview.data_object_source_observations == {}


def test_breadth_report_rejects_resealed_denominator_and_stale_receipt():
    from data_provider.market_breadth_source import compile_market_breadth_observation, validate_market_breadth_observation
    from src.services.pit_identity import sha256_payload
    obs = compile_market_breadth_observation(frame(), context())
    bad = deepcopy(obs); bad["facts"]["eligible_count"] += 1
    bad.pop("observation_hash"); bad["observation_hash"] = sha256_payload(bad)
    with pytest.raises(ValueError, match="BREADTH_COUNT_CONSERVATION"):
        validate_market_breadth_observation(bad, target_date="2026-10-30")
    with pytest.raises(ValueError, match="BREADTH_TRADE_DATE_MISMATCH"):
        validate_market_breadth_observation(obs, target_date="2026-11-02")


@pytest.mark.parametrize("field,value", [
    ("provider_identity", "OtherProvider"),
    ("provider_route", "other.route"),
    ("source_content_sha256", "f" * 64),
    ("source_observed_at", "2026-10-30T08:05:01+00:00"),
])
def test_breadth_resealed_outer_hash_does_not_change_bound_source_identity(field, value):
    from data_provider.market_breadth_source import (
        compile_market_breadth_observation, validate_market_breadth_observation,
    )
    from src.services.pit_identity import sha256_payload

    good = compile_market_breadth_observation(frame(), context())
    assert validate_market_breadth_observation(good, target_date="2026-10-30") == good
    bad = deepcopy(good)
    bad[field] = value
    bad.pop("observation_hash")
    bad["observation_hash"] = sha256_payload(bad)
    with pytest.raises(ValueError, match="BREADTH_SOURCE_IDENTITY_MISMATCH"):
        validate_market_breadth_observation(bad, target_date="2026-10-30")


@pytest.mark.parametrize("field,value", [
    ("currency", "USD"), ("adjustment_basis", "QFQ"),
    ("units", {"count": "LOT", "breadth_ratio": "PERCENT"}),
])
def test_breadth_consumer_rejects_resealed_wrong_dimension(field, value):
    from data_provider.market_breadth_source import (
        compile_market_breadth_observation, validate_market_breadth_observation,
    )
    from src.services.pit_identity import sha256_payload

    bad = compile_market_breadth_observation(frame(), context())
    bad[field] = value
    bad.pop("observation_hash")
    bad["observation_hash"] = sha256_payload(bad)
    with pytest.raises(ValueError, match="BREADTH_OBSERVATION_SEMANTICS_INVALID"):
        validate_market_breadth_observation(bad, target_date="2026-10-30")


def test_breadth_source_rejects_conflicting_exchange_qualifiers():
    from data_provider.market_breadth_source import validate_unique_market_instruments

    df = frame()
    df.loc[0, "代码"] = "SH600001.SZ"
    with pytest.raises(ValueError, match="BREADTH_INSTRUMENT_EXCHANGE_CONFLICT"):
        validate_unique_market_instruments(df)
    df.loc[0, "代码"] = "sh.600001.SH"
    assert validate_unique_market_instruments(df) == ["600001", "000001", "600002"]


def test_breadth_uses_existing_b1_memory_context_and_does_not_bypass_grant():
    from src.core.pipeline import StockAnalysisPipeline
    from src.services.data_object_admission import DataObjectAdmissionError
    from tests.test_data_object_admission import NO_SEND_CONTEXT, _rights_evidence
    ctx = context()
    grant = _rights_evidence(dataset_id="M_BASIC_BREADTH", provider_identity=ctx["provider_identity"], consumer_scope="MARKET_REGIME_BRIEF")
    operation = {**NO_SEND_CONTEXT, "research_as_of": ctx["research_as_of"], "rights_by_dataset": {"M_BASIC_BREADTH": grant}}
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline._bind_b1_run_context = MagicMock(return_value=operation)
    obs = pipeline._ingest_b1_market_breadth_snapshot(snapshot=frame(), snapshot_context=ctx)
    pipeline._bind_b1_run_context.assert_called_once_with(persist=False)
    assert pipeline._b1_nonprice_shared_observations["M_BASIC_BREADTH"] == obs
    fundamental = pipeline._b1_nonprice_fundamental_context(code="600519", asset_route="STOCK", market="cn")
    assert fundamental["data_object_source_observations"]["M_BASIC_BREADTH"] == obs
    # Other non-price acquisition may refresh its own shared map. The existing
    # breadth owner must survive without becoming transferable to another run.
    pipeline._b1_nonprice_shared_observations = {}
    again = pipeline._b1_nonprice_fundamental_context(code="600519", asset_route="STOCK", market="cn")
    assert again["data_object_source_observations"]["M_BASIC_BREADTH"] == obs
    pipeline._bind_b1_run_context.return_value = {**operation, "operation_id": "other-run"}
    with pytest.raises(DataObjectAdmissionError, match="RUN_CONTEXT_MISMATCH"):
        pipeline._b1_nonprice_fundamental_context(code="600519", asset_route="STOCK", market="cn")
    pipeline._bind_b1_run_context.return_value = operation
    with pytest.raises(DataObjectAdmissionError, match="ALREADY_BOUND"):
        pipeline._ingest_b1_market_breadth_snapshot(snapshot=frame(), snapshot_context=ctx)
    fresh = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    fresh._bind_b1_run_context = MagicMock(return_value={**operation, "rights_by_dataset": {}})
    with pytest.raises(DataObjectAdmissionError):
        fresh._ingest_b1_market_breadth_snapshot(snapshot=frame(), snapshot_context=ctx)
