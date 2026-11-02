"""Offline native-service integration; NOT a full Pipeline/SMTP/PIT acceptance."""
from __future__ import annotations

import ast
import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import subprocess

import numpy as np
import pandas as pd
import pytest

from src.config import Config

from src.services import evidence_traceability_registry as reg
from src.services.cost_structure_service import build_cost_structure_context
from src.services.candlestick_pattern_service import (
    build_completed_timeframe_candlestick_context,
)
from src.services.data_object_admission import (
    compile_data_object_admission_view,
    seal_data_object_admission_receipt,
    seal_source_rights_evidence,
)
from src.services.factor_decision_summary import build_stock_factor_decision_summary
from src.services.multi_timeframe_structure_service import build_multi_timeframe_structure_context
from src.services.pattern_trigger_service import build_pattern_trigger_context, _dedupe_patterns
from src.services.price_structure_service import build_price_structure_context
from src.services.research_state_projection import (
    STRATEGY_ELIGIBILITY_SCHEMA_VERSION, STRATEGY_ELIGIBILITY_REQUIRED_EVIDENCE,
    build_strategy_eligibility_identity,
)
from src.services.supply_demand_service import build_supply_demand_context
from src.services.volatility_momentum_service import build_volatility_momentum_context
from src.stock_analyzer import MACDStatus, StockTrendAnalyzer, TrendAnalysisResult

ROOT = Path(__file__).resolve().parents[1]
# Independent accepted strategy contract, not an expected set generated from the code under test.
EXPECTED_REQUIRED = {
    "market_regime_permission", "sector_industry_strength", "quality", "valuation",
    "weekly_trend_structure", "daily_trend_structure", "daily_pullback_or_supply_contraction",
    "volume_price_confirmation", "distribution_risk_clear", "thirty_minute_trigger", "risk_reward",
}

EXPECTED_METHOD_WINDOW_IDS = {
    "MARKET_SECTOR_REGIME",
    "RELATIVE_STRENGTH",
    "MA_LEVEL_ALIGNMENT_DAILY",
    "SUPPLY_RELATIVE_VOLUME",
    "SUPPLY_DIRECTIONAL_VOLUME",
    "SUPPLY_CMF",
    "COST_ROLLING_REFERENCE",
    "PRICE_PIVOT_SWING",
    "BREAKOUT_RETEST_FAILED",
    "MACD",
    "RSI",
    "ROC",
    "VOLATILITY_TR_SMA",
    "CONFIRMED_DIVERGENCE",
    "CUP_HANDLE",
    "DOUBLE_BOTTOM",
    "VCP",
    "FLAT_BASE",
    "TIGHT_CONSOLIDATION",
    "MTF_MA_LEVEL_ALIGNMENT",
    "MTF_MOMENTUM_CONTEXT",
    "MTF_PRICE_STRUCTURE",
    "MA_SLOPE_CROSS",
    "MA_COMPRESSION_RELEASE",
    "QUALITY",
    "VALUATION",
    "DISTRIBUTION",
    "RISK_REWARD",
    "CANDLESTICK",
    "ATR_WILDER",
    "ADX_DMI",
    "BOLLINGER",
    "KDJ",
    "OBV_ADL",
    "MFI",
    "VWAP",
    "AVWAP_VOLUME_PROFILE",
    "CHAN",
    "WAVE",
    "ETF_SPECIFIC",
    "GLOBAL",
    "BREADTH",
    "PROBABILITY",
}
EXPECTED_RESEARCH_UNIVERSE_REQUIREMENTS = {
    "REGIME", "TREND_RS", "SUPPLY", "COST", "STRUCTURE", "MOMENTUM", "PATTERN", "MTF",
    "QUALITY", "VALUATION", "DISTRIBUTION", "RISK_REWARD", "CANDLESTICK",
    "EXTRA_INDICATORS", "AVWAP_PROFILE", "CHAN", "WAVE", "ETF_SPECIFIC", "GLOBAL", "BREADTH",
    "PROBABILITY", "INTRADAY_60m", "INTRADAY_30m", "INTRADAY_15m", "INTRADAY_5m", "DECISION",
}
EXPECTED_METHOD_WINDOW_CLASSES = {
    "FIXED_ROLLING",
    "RECURSIVE_WARMUP",
    "ADAPTIVE_CONTEXT",
    "VARIABLE_STRUCTURE",
    "EVENT_ANCHORED",
    "CROSS_SECTIONAL_ASOF",
    "VINTAGE_ASOF",
    "INCREMENTAL_STATE_MACHINE",
}


def _accepted_bindings():
    return reg.EVIDENCE_BINDINGS + reg.DEFERRED_BINDINGS + (reg.DECISION_BINDING,)


def _strict_daily_identity(target, *, stock_code="600519", market="cn", provider="synthetic", basis="qfq"):
    return {
        "data_snapshot_identity": "a" * 64,
        "provider_identity": provider,
        "adjustment_basis": basis,
        "price_identity_reasons": [],
        "stock_code": stock_code,
        "market": market,
        "target_date": target.isoformat(),
    }


def _rehash_receipt(receipt):
    receipt = deepcopy(receipt)
    body = dict(receipt)
    body.pop("receipt_hash", None)
    receipt["receipt_hash"] = reg.digest(body)
    return receipt


@pytest.fixture(autouse=True)
def explicit_native_config(monkeypatch):
    """Native service input, not an environment/provider or Pipeline substitute.

    Config loading and full Pipeline imports remain separately blocked when
    dependencies are missing; this suite does not claim to validate them.
    """
    config = Config()
    monkeypatch.setattr("src.stock_analyzer.get_config", lambda: config)
    yield


def bars(n=180):
    x = np.arange(n, dtype=float)
    close = 30 + x * .025 + 1.2 * np.sin(x / 3)
    return pd.DataFrame({"date": pd.bdate_range("2025-01-02", periods=n).date,
                         "open": close - .12, "high": close + .5, "low": close - .5,
                         "close": close, "volume": 100000 + (x % 9) * 1000,
                         "data_source": "synthetic"})


def native_summary(frame=None, target=None):
    # Positive native contract fixture must supply the largest currently
    # executed recursive owner window; 180 completed bars now prove PARTIAL.
    frame = bars(320) if frame is None else frame
    target = target or frame["date"].iloc[-1]
    completed = frame[frame["date"] <= target].copy()
    trend = StockTrendAnalyzer().analyze(completed, "600519")
    supply = build_supply_demand_context(stock_code="600519", history=frame, target_date=target)
    price = build_price_structure_context(stock_code="600519", history=frame, target_date=target, market="cn")
    cost = build_cost_structure_context(stock_code="600519", history=frame, target_date=target, market="cn", chip_data=None)
    momentum = build_volatility_momentum_context(stock_code="600519", history=frame, target_date=target,
                                                market="cn", trend_result=trend, price_structure_context=price)
    pattern = build_pattern_trigger_context(stock_code="600519", history=frame, target_date=target,
                                            market="cn", price_structure_context=price, supply_demand_context=supply)
    return build_stock_factor_decision_summary(
        trend, supply_demand_context=supply, cost_structure_context=cost,
        price_structure_context=price, volatility_momentum_context=momentum,
        pattern_trigger_context=pattern, include_canonical=True,
    )


def native_summary_with_mtf(*, intraday=False, volume_unit="UNKNOWN", typed_daily=False):
    frame = bars(520)
    if typed_daily:
        from tests.test_multi_timeframe_structure_service import _with_observed_identity
        frame = _with_observed_identity(frame, provider="synthetic")
    target = frame["date"].iloc[-1]
    completed = frame[frame["date"] <= target].copy()
    trend_analyzer = StockTrendAnalyzer()
    trend = trend_analyzer.analyze(completed, "600519")
    price = build_price_structure_context(
        stock_code="600519", history=frame, target_date=target, market="cn",
    )
    factor = native_summary(frame=frame, target=target)
    intraday_timeframes = None
    snapshot_observed_at = None
    if intraday:
        from tests.test_multi_timeframe_structure_service import _intraday_fixture

        intraday_timeframes = {
            timeframe: _intraday_fixture(
                timeframe, target, volume_unit=volume_unit, periods=320,
            )
            for timeframe in ("60m", "30m", "15m", "5m")
        }
        snapshot_observed_at = (
            pd.Timestamp(target).tz_localize("Asia/Shanghai")
            + pd.Timedelta(hours=17)
        ).tz_convert("UTC").to_pydatetime()
    factor["multi_timeframe_structure_context"] = build_multi_timeframe_structure_context(
        stock_code="600519",
        history=frame,
        target_date=target,
        market="cn",
        trend_analyzer=trend_analyzer,
        daily_trend_result=trend,
        daily_price_structure_context=price,
        snapshot_observed_at=snapshot_observed_at,
        intraday_timeframes=intraday_timeframes,
    )
    factor["evidence_traceability"] = reg.build_runtime_trace(factor)
    return factor


def test_registry_owners_and_strategy_are_source_bound():
    reg.validate_registry()
    owners = reg.validate_source_bindings(ROOT)
    assert set(owners) == {b.requirement_id for b in reg.EVIDENCE_BINDINGS} | {"DECISION"}
    assert set(STRATEGY_ELIGIBILITY_REQUIRED_EVIDENCE) == EXPECTED_REQUIRED
    assert reg.MANIFEST_HASH == reg.digest(reg.manifest_document())
    assert reg.manifest_document()["product_slots"]["baseline_sha256"] == "039ca6394baf9cf39494cc29f512802b114c8227f8c965723197a8df4b9de823"


def test_legacy_audit_membership_and_hash_are_not_redefined():
    expected = ("strategy_id", "contract_version", "composite_score", "canonical_decision",
                "market_sector_regime", "trend_relative_strength", "supply_demand_volume_price",
                "cost_structure_evidence", "price_structure_evidence", "volatility_momentum_evidence",
                "pattern_trigger_evidence", "multi_timeframe_structure_context")
    assert reg.ledger_evidence_keys() == expected
    assert reg.digest({"schema_version": "stock-factor-evidence-v1", "fields": list(expected)}) == "426d7f21de79ad26e12ea39b4c686b489b90dc24d88f0750bcb5aec421265647"
    assert "distribution_risk_evidence" not in reg.ledger_evidence_keys()
    assert "distribution_risk_evidence" in reg.current_manifest_evidence_keys()


def test_orphan_strategy_and_duplicate_bindings_are_rejected():
    with pytest.raises(reg.TraceabilityError, match="ORPHAN_STRATEGY"):
        reg.validate_registry(strategy=[replace(reg.STRATEGY_BINDINGS[0], requirement_id="INVENTED")])
    with pytest.raises(reg.TraceabilityError, match="DUPLICATE_BINDING"):
        reg.validate_registry(bindings=reg.EVIDENCE_BINDINGS + (reg.EVIDENCE_BINDINGS[0],))


@pytest.mark.parametrize("missing_id", ["CHAN", "WAVE", "REGIME"])
def test_accepted_requirement_set_cannot_self_certify_after_deletion(missing_id):
    candidate = tuple(b for b in _accepted_bindings() if b.requirement_id != missing_id)
    with pytest.raises(reg.TraceabilityError, match="ACCEPTED_REQUIREMENT_SET_MISMATCH"):
        reg.validate_registry(bindings=candidate)


def test_binding_route_timeframe_and_metric_metadata_are_validated():
    bindings = list(_accepted_bindings())
    bindings[0] = replace(bindings[0], asset_routes=())
    with pytest.raises(reg.TraceabilityError, match="INVALID_ASSET_ROUTE"):
        reg.validate_registry(bindings=bindings)

    bindings = list(_accepted_bindings())
    bindings[0] = replace(bindings[0], timeframe="2h")
    with pytest.raises(reg.TraceabilityError, match="INVALID_TIMEFRAME"):
        reg.validate_registry(bindings=bindings)

    for field, invalid, message in (
        ("kind", "text", "INVALID_METRIC_KIND"),
        ("unit", "mystery", "INVALID_METRIC_UNIT"),
        ("timeframe", "2h", "INVALID_METRIC_TIMEFRAME"),
    ):
        metrics = [dict(metric) for metric in reg.METRIC_BINDINGS]
        metrics[0][field] = invalid
        with pytest.raises(reg.TraceabilityError, match=message):
            reg.validate_registry(metrics=metrics)


def test_method_window_policy_view_is_complete_manifest_bound_and_non_mutating():
    factor = native_summary()
    learning_before = reg.learning_projection(factor)
    manifest_before = reg.MANIFEST_HASH
    metrics_before = reg.digest(reg.METRIC_BINDINGS)

    view = reg.compile_method_window_policy_view()
    methods = {row["method_id"]: row for row in view["methods"]}

    assert view["schema_version"] == "method-window-policy-view-v2"
    assert view["source_manifest_hash"] == manifest_before
    assert set(view["window_classes"]) == EXPECTED_METHOD_WINDOW_CLASSES
    assert set(methods) == EXPECTED_METHOD_WINDOW_IDS
    assert set(view["timeframes"]) == set(reg.TIMEFRAMES)
    assert {gate["timeframe"] for gate in view["timeframe_gates"]} == {"60m", "30m", "15m", "5m"}
    assert all(gate["implementation_state"] == "DEFERRED_WITH_OWNER_AND_REENTRY" for gate in view["timeframe_gates"])
    assert view["decision_authority"]["window_method"] is False

    assert methods["MACD"]["window_classes"] == ("RECURSIVE_WARMUP",)
    # Preserve the independent accepted constructor as predecessor evidence.
    assert next(p for p in reg.DECLARED_METHOD_WINDOW_PROFILES
                if p.method_id == "MACD").current_execution_timeframes == ("daily",)
    assert next(p for p in reg.DECLARED_METHOD_WINDOW_PROFILES
                if p.method_id == "RSI").current_execution_timeframes == ("daily",)
    assert methods["MACD"]["current_execution_timeframes"] == reg.TIMEFRAMES
    assert methods["RSI"]["current_execution_timeframes"] == reg.TIMEFRAMES
    assert methods["MACD"]["target_timeframes"] == reg.TIMEFRAMES
    for method_id in (
        "SUPPLY_RELATIVE_VOLUME", "SUPPLY_DIRECTIONAL_VOLUME",
        "SUPPLY_CMF", "COST_ROLLING_REFERENCE",
    ):
        assert methods[method_id]["current_execution_timeframes"] == reg.TIMEFRAMES
    assert methods["MA_SLOPE_CROSS"]["leaf_implementation_state"] == "EXISTING_REUSED"
    assert methods["MA_SLOPE_CROSS"]["current_execution_timeframes"] == reg.TIMEFRAMES
    assert methods["MA_COMPRESSION_RELEASE"]["leaf_implementation_state"] == "EXISTING_REUSED"
    assert methods["MA_COMPRESSION_RELEASE"]["current_execution_timeframes"] == reg.TIMEFRAMES
    assert set(methods["MA_COMPRESSION_RELEASE"]["window_classes"]) == {
        "FIXED_ROLLING",
        "ADAPTIVE_CONTEXT",
        "INCREMENTAL_STATE_MACHINE",
    }
    assert methods["MTF_MA_LEVEL_ALIGNMENT"]["current_execution_timeframes"] == (
        "monthly", "weekly", "60m", "30m", "15m", "5m",
    )
    assert methods["MTF_MOMENTUM_CONTEXT"]["current_execution_timeframes"] == (
        "monthly", "weekly", "60m", "30m", "15m", "5m",
    )
    assert methods["MTF_MA_LEVEL_ALIGNMENT"]["target_timeframes"] == reg.NON_DAILY_TECHNICAL_TIMEFRAMES
    assert methods["CUP_HANDLE"]["window_classes"] == ("VARIABLE_STRUCTURE",)
    for method_id in (
        "CUP_HANDLE", "DOUBLE_BOTTOM", "VCP", "FLAT_BASE",
        "TIGHT_CONSOLIDATION", "CANDLESTICK",
    ):
        assert methods[method_id]["current_execution_timeframes"] == reg.TIMEFRAMES
    assert methods["CANDLESTICK"]["leaf_implementation_state"] == "EXISTING_REUSED"
    assert methods["CANDLESTICK"]["canonical_path"] == "pattern_trigger_evidence.context.candlestick"
    assert methods["CANDLESTICK"]["current_execution_timeframes"] == reg.TIMEFRAMES
    assert set(methods["CANDLESTICK"]["window_classes"]) == {"ADAPTIVE_CONTEXT", "EVENT_ANCHORED"}
    assert set(methods["ADX_DMI"]["window_classes"]) == {"FIXED_ROLLING", "RECURSIVE_WARMUP"}
    assert set(methods["KDJ"]["window_classes"]) == {"FIXED_ROLLING", "RECURSIVE_WARMUP"}
    assert methods["ATR_WILDER"]["window_classes"] == ("RECURSIVE_WARMUP",)
    assert methods["ATR_WILDER"]["requirement_id"] == "MOMENTUM"
    assert methods["ATR_WILDER"]["leaf_implementation_state"] == "EXISTING_REUSED"
    for method_id in (
        "ROC", "VOLATILITY_TR_SMA", "ATR_WILDER", "CONFIRMED_DIVERGENCE",
    ):
        assert methods[method_id]["current_execution_timeframes"] == reg.TIMEFRAMES
    assert methods["ATR_WILDER"]["canonical_path"] == "volatility_momentum_evidence"
    assert methods["CHAN"]["window_classes"] == (
        "VARIABLE_STRUCTURE", "INCREMENTAL_STATE_MACHINE",
    )
    assert methods["CHAN"]["current_execution_timeframes"] == ("daily",)
    assert methods["CHAN"]["leaf_implementation_state"] == "EXISTING_REUSED"
    assert methods["DISTRIBUTION"]["current_execution_timeframes"] == ("daily",)
    assert methods["DISTRIBUTION"]["leaf_implementation_state"] == "EXISTING_REUSED"
    assert methods["VALUATION"]["target_timeframes"] == ("asset",)
    assert methods["VALUATION"]["window_classes"] == ("VINTAGE_ASOF", "CROSS_SECTIONAL_ASOF")
    assert methods["VALUATION"]["consumer_scope"] == "ASSET_LAYER"
    assert methods["VALUATION"]["current_execution_timeframes"] == ("asset",)

    pattern_slots = {
        slot["id"]
        for slot in reg.load_slot_map()["slots"]
        if "PATTERN" in slot["requirements"]
    }
    assert set(methods["CUP_HANDLE"]["parent_product_slot_ids"]) == pattern_slots
    assert set(methods["CANDLESTICK"]["parent_product_slot_ids"]) == pattern_slots
    assert methods["CANDLESTICK"]["parent_learning_metric_ids"] == ()
    assert "pattern_trigger_evidence.context.candlestick" not in reg.ledger_evidence_keys()
    assert set(methods["MACD"]["parent_learning_metric_ids"]) == {
        metric["id"] for metric in reg.METRIC_BINDINGS if metric["requirement_id"] == "MOMENTUM"
    }
    assert set(methods["ATR_WILDER"]["parent_learning_metric_ids"]) == {
        metric["id"] for metric in reg.METRIC_BINDINGS if metric["requirement_id"] == "MOMENTUM"
    }
    metric_ids = {metric["id"] for metric in reg.METRIC_BINDINGS}
    assert {"daily.atr_wilder14", "daily.atr_wilder14_pct"} <= metric_ids

    current_manifest = reg.manifest_document()
    assert current_manifest["method_contracts"]["MOMENTUM"] == {
        "version": "volatility-momentum-v2",
        "warmup": 61,
        "config_hash": "9cf1fb3f0cdd64752e6a589c480a16fc88b3daeb4a88c4b7a897a4e36d9706c9",
    }
    assert current_manifest["method_contracts"]["MTF"] == {
        "version": "completed-daily-plus-intraday-context-v2",
        "warmup": 26,
        "config_hash": "6a06b13b33761fafcdeae55f1d560dd1334a9c57cc12ddfdb2db5e0d429f0acc",
    }
    pre_intraday_manifest = deepcopy(current_manifest)
    pre_intraday_manifest["method_contracts"]["MTF"] = {
        "version": "completed-daily-resample-v1",
        "warmup": 26,
        "config_hash": "5ea82ff077b04d411d4f62b41800d07ebbd7a547726db2481e3aa07db61c5b0f",
    }
    # Explicit manifest-version refreeze for accepted source-bound Quality/Valuation
    # owners; historical pre-admission digests remain in Git. V2.5 is unchanged.
    assert reg.digest(pre_intraday_manifest) == "a27f1bd77baec12b37c62179d3a15db5f7047506a36dbe4c31f033a771155dd9"
    assert reg.MANIFEST_HASH == "f0bbecdc5d5bad05e7a9e5268834c9b6ac0ebb57a6ea877112e7690b3b84e323"
    assert reg.MANIFEST_HASH == manifest_before == reg.digest(reg.manifest_document())
    assert reg.digest(reg.METRIC_BINDINGS) == metrics_before == "ac2e214238229453592464d99ed6341651f7d232c9339da66d2fec7fc4d6729a"
    assert not any(
        any(token in metric["id"] for token in ("slope", "compression", "release", "cross"))
        for metric in reg.METRIC_BINDINGS
    )
    assert tuple(reg.manifest_document()) == (
        "schema_version", "baseline_sha256", "evidence", "metrics", "product_slots",
        "method_contracts", "strategy", "ledger_keys", "timeframes", "data_policy",
        "product_policy", "learning_policy",
    )
    assert reg.learning_projection(factor) == learning_before


def test_method_policy_classifies_all_accepted_methods_without_broadening_manifest_or_learning():
    factor = native_summary()
    learning_before = reg.learning_projection(factor)
    manifest_before = reg.MANIFEST_HASH
    metrics_before = reg.digest(reg.METRIC_BINDINGS)

    view = reg.compile_method_window_policy_view()
    methods = {row["method_id"]: row for row in view["methods"]}

    assert view["schema_version"] == "method-window-policy-view-v2"
    assert set(methods) == EXPECTED_METHOD_WINDOW_IDS
    assert len(methods) == 43
    assert {row["consumer_scope"] for row in methods.values()} == {
        "TECHNICAL_MATRIX", "ASSET_LAYER", "RESEARCH_SHADOW", "MODEL_LAYER",
    }
    assert methods["MACD"]["family_targets"] == ("MOMENTUM",)
    assert methods["ATR_WILDER"]["family_targets"] == ("MOMENTUM",)
    assert methods["OBV_ADL"]["family_targets"] == ("SUPPLY",)
    assert methods["MFI"]["family_targets"] == ("SUPPLY",)
    assert methods["VWAP"]["family_targets"] == ("COST",)
    assert methods["AVWAP_VOLUME_PROFILE"]["family_targets"] == ("COST",)
    assert methods["BOLLINGER"]["family_targets"] == ("PATTERN",)
    assert methods["CANDLESTICK"]["family_targets"] == ("PATTERN",)
    assert methods["DISTRIBUTION"]["consumer_scope"] == "ASSET_LAYER"
    assert set(methods["DISTRIBUTION"]["family_targets"]) == {"SUPPLY", "STRUCTURE", "PATTERN"}
    assert methods["CHAN"]["consumer_scope"] == "RESEARCH_SHADOW"
    assert set(methods["CHAN"]["family_targets"]) == {"STRUCTURE", "PATTERN", "MTF"}
    assert methods["WAVE"]["consumer_scope"] == "RESEARCH_SHADOW"
    assert methods["PROBABILITY"]["consumer_scope"] == "MODEL_LAYER"
    assert methods["QUALITY"]["consumer_scope"] == "ASSET_LAYER"
    assert methods["GLOBAL"]["consumer_scope"] == "ASSET_LAYER"
    assert methods["GLOBAL"]["family_targets"] == ("REGIME",)
    assert view["source_manifest_hash"] == manifest_before
    assert reg.MANIFEST_HASH == manifest_before == reg.digest(reg.manifest_document())
    assert reg.digest(reg.METRIC_BINDINGS) == metrics_before
    after = reg.learning_projection(factor)
    assert after["values"] == learning_before["values"]
    assert after["training_admitted"] is False


def test_timeframe_family_matrix_is_exact_56_cells_and_keeps_deferred_methods_visible_without_downgrade():
    factor = native_summary()
    matrix = reg.compile_timeframe_family_matrix(factor)
    cells = {(row["timeframe"], row["family"]): row for row in matrix["cells"]}

    assert len(matrix["cells"]) == 56
    assert len(cells) == 56
    assert set(matrix["timeframes"]) == set(reg.TIMEFRAMES)
    assert set(matrix["families"]) == set(reg.TECHNICAL_EVIDENCE_FAMILIES)
    daily_supply = cells[("daily", "SUPPLY")]
    assert daily_supply["state"] == "READY"
    assert {item["method_id"] for item in daily_supply["method_coverage"] if item["state"] == "READY"} >= {
        "SUPPLY_RELATIVE_VOLUME", "SUPPLY_DIRECTIONAL_VOLUME", "SUPPLY_CMF",
    }
    weekly_supply = cells[("weekly", "SUPPLY")]
    assert weekly_supply["state"] == "MISSING"
    daily_momentum = cells[("daily", "MOMENTUM")]
    assert daily_momentum["state"] == "READY"
    method_states = {item["method_id"]: item["state"] for item in daily_momentum["method_coverage"]}
    assert method_states["MACD"] == "READY"
    assert method_states["ATR_WILDER"] == "READY"
    assert method_states["ADX_DMI"] == "NOT_ADMITTED"
    assert method_states["KDJ"] == "NOT_ADMITTED"
    weekly_momentum = cells[("weekly", "MOMENTUM")]
    weekly_states = {item["method_id"]: item["state"] for item in weekly_momentum["method_coverage"]}
    # The method is admitted on weekly bars, but this no-MTF fixture has no
    # weekly carrier; the honest state is MISSING, never a fabricated value.
    assert weekly_states["ATR_WILDER"] == "MISSING"
    daily_regime = cells[("daily", "REGIME")]
    assert daily_regime["state"] in {"READY", "PARTIAL", "MISSING", "UNKNOWN"}
    assert daily_regime["projection_scope"] == "SHARED_ASSET_CONTEXT"
    assert daily_regime["shared_parent_context"] is True
    assert {row["method_id"] for row in daily_regime["method_coverage"]} == {
        "MARKET_SECTOR_REGIME"
    }

    matrix_method_ids = {
        item["method_id"]
        for cell in matrix["cells"]
        for item in cell["method_coverage"]
    }
    non_matrix_ids = {item["method_id"] for item in matrix["non_matrix_methods"]}
    assert matrix_method_ids | non_matrix_ids == EXPECTED_METHOD_WINDOW_IDS
    assert matrix_method_ids.isdisjoint(non_matrix_ids)
    assert "MARKET_SECTOR_REGIME" not in non_matrix_ids
    assert non_matrix_ids >= {
        "QUALITY", "VALUATION", "DISTRIBUTION", "RISK_REWARD",
        "CHAN", "WAVE", "ETF_SPECIFIC", "GLOBAL", "BREADTH", "PROBABILITY",
    }
    assert len(matrix["matrix_hash"]) == 64


def test_all_56_cells_have_executable_contract_and_regime_is_shared_not_seven_votes():
    factor = native_summary_with_mtf(
        intraday=True, volume_unit="share", typed_daily=True,
    )
    matrix = reg.compile_timeframe_family_matrix(factor)
    validated = reg.validate_timeframe_family_matrix(matrix)

    assert validated["implementation_complete"] is True
    assert validated["executable_cell_count"] == 56
    assert validated["empty_method_cell_ids"] == ()
    assert len(matrix["cells"]) == 56
    for cell in matrix["cells"]:
        assert cell["implementation_state"] == "EXECUTABLE"
        assert cell["method_coverage"]
        assert cell["reader_projection"]["missingness_visible"] is True
        assert cell["reader_projection"]["numeric_requires_product_and_method_ready"] is True
        assert cell["independent_action_authority"] is False
        assert cell["cross_timeframe_vote_counting"] is False
        for method in cell["method_coverage"]:
            assert method["method_id"]
            assert method["owner"]
            assert method["canonical_path"]
            assert method["reason"]
            assert len(method["method_receipt_hash"]) == 64
            assert method["independent_action_authority"] is False

    regime_cells = [cell for cell in matrix["cells"] if cell["family"] == "REGIME"]
    assert len(regime_cells) == 7
    assert all(cell["state"] != "NOT_APPLICABLE" for cell in regime_cells)
    assert {
        method["method_id"]
        for cell in regime_cells
        for method in cell["method_coverage"]
    } == {"MARKET_SECTOR_REGIME"}
    assert all(
        cell["projection_scope"] == "SHARED_ASSET_CONTEXT"
        and cell["shared_parent_context"] is True
        and cell["cross_timeframe_vote_counting"] is False
        for cell in regime_cells
    )
    parent_hashes = {cell["shared_parent_hash"] for cell in regime_cells}
    assert len(parent_hashes) == 1
    assert len(next(iter(parent_hashes))) == 64

    damaged = deepcopy(matrix)
    damaged["cells"][0]["method_coverage"] = []
    damaged["cells"][0]["applicable_method_ids"] = ()
    with pytest.raises(reg.TraceabilityError, match="TIMEFRAME_FAMILY_CELL_METHOD_EMPTY"):
        reg.validate_timeframe_family_matrix(damaged)


def test_relative_strength_is_an_executable_method_on_all_seven_timeframes():
    policy = reg.compile_method_window_policy_view()
    methods = {row["method_id"]: row for row in policy["methods"]}
    assert methods["RELATIVE_STRENGTH"]["current_execution_timeframes"] == reg.TIMEFRAMES

    factor = native_summary_with_mtf(
        intraday=True, volume_unit="share", typed_daily=True,
    )
    matrix = reg.compile_timeframe_family_matrix(factor)
    cells = {
        (row["timeframe"], row["family"]): row
        for row in matrix["cells"]
    }
    for timeframe in reg.TIMEFRAMES:
        methods = {
            row["method_id"]: row
            for row in cells[(timeframe, "TREND_RS")]["method_coverage"]
        }
        rs = methods["RELATIVE_STRENGTH"]
        assert rs["state"] in reg.TIMEFRAME_FAMILY_MATRIX_STATES
        assert rs["reason"]
        assert len(rs["method_receipt_hash"]) == 64
        if timeframe != "daily":
            assert rs["state"] == "MISSING"
            assert rs["reason"] == "TIMEFRAME_RELATIVE_STRENGTH_CONTEXT_MISSING"

def test_timeframe_etf_peer_rs_reaches_exact_cell_and_peer_tamper_fails_closed():
    factor = native_summary_with_mtf(
        intraday=True, volume_unit="share", typed_daily=True,
    )
    frame = factor["multi_timeframe_structure_context"]["timeframes"]["30m"]
    target_hash = frame["intraday_identity_hash"]
    identity = {
        "provider_identity": "synthetic",
        "adjustment_basis": "qfq",
        "timeframe": "30m",
        "completed_bar_only": True,
        "available_at_max": frame["latest_bar_end"],
    }
    frame["relative_strength"] = {
        "schema_version": "timeframe-etf-peer-relative-strength-v1",
        "status": "READY",
        "reason": "TIMEFRAME_ETF_PEER_UNIVERSE_READY",
        "timeframe": "30m",
        "target_bar_end": frame["latest_bar_end"],
        "horizon_bars": 20,
        "input_identity": {
            "target": {**identity, "identity_hash": target_hash},
            "peers": [
                {**identity, "identity_hash": char * 64}
                for char in ("b", "c", "d")
            ],
        },
        "data_quality": {
            "source_alignment": "MATCHED",
            "exact_shared_endpoints": True,
        },
        "peer_universe": {"universe_hash": "e" * 64},
        "relative": {
            "state": "OUTPERFORMING",
            "rank": 1,
            "rank_denominator": 4,
            "percentile": 100.0,
            "excess_vs_peer_median_pct_points": 2.75,
        },
        "independent_action_authority": False,
    }
    matrix = reg.compile_timeframe_family_matrix(factor)
    cell = next(
        row for row in matrix["cells"]
        if row["timeframe"] == "30m" and row["family"] == "TREND_RS"
    )
    rs = next(
        row for row in cell["method_coverage"]
        if row["method_id"] == "RELATIVE_STRENGTH"
    )
    assert rs["state"] == "READY"
    assert rs["reason"] == "TIMEFRAME_ETF_PEER_UNIVERSE_READY"
    assert rs["runtime_source_binding"]["runtime_source_bound"] is True
    assert rs["runtime_source_binding"]["identity_kind"] == (
        "TIMEFRAME_ETF_PEER_UNIVERSE_INPUT"
    )
    assert len(rs["runtime_source_binding"]["comparison_identity_hashes"]) == 3
    assert cell["state"] == "PARTIAL"
    assert cell["independent_action_authority"] is False

    tampered = deepcopy(factor)
    tampered["multi_timeframe_structure_context"]["timeframes"]["30m"][
        "relative_strength"
    ]["input_identity"]["peers"][0]["identity_hash"] = "bad"
    damaged = reg.compile_timeframe_family_matrix(tampered)
    damaged_cell = next(
        row for row in damaged["cells"]
        if row["timeframe"] == "30m" and row["family"] == "TREND_RS"
    )
    damaged_rs = next(
        row for row in damaged_cell["method_coverage"]
        if row["method_id"] == "RELATIVE_STRENGTH"
    )
    assert damaged_rs["state"] == "UNKNOWN"
    assert damaged_rs["reason"] == (
        "TIMEFRAME_RELATIVE_STRENGTH_SOURCE_IDENTITY_UNPROVEN"
    )


def test_source_bound_supply_and_cost_follow_actual_seven_timeframe_carriers_without_authority():
    factor = native_summary_with_mtf(
        intraday=True, volume_unit="share", typed_daily=True,
    )
    matrix = reg.compile_timeframe_family_matrix(factor)
    cells = {(row["timeframe"], row["family"]): row for row in matrix["cells"]}

    weekly_supply = cells[("weekly", "SUPPLY")]
    weekly_cost = cells[("weekly", "COST")]
    assert weekly_supply["state"] == "READY"
    assert weekly_cost["state"] == "READY"
    assert {
        row["method_id"] for row in weekly_supply["method_coverage"]
        if row["state"] == "READY"
    } >= {
        "SUPPLY_RELATIVE_VOLUME", "SUPPLY_DIRECTIONAL_VOLUME", "SUPPLY_CMF",
    }
    assert next(
        row for row in weekly_cost["method_coverage"]
        if row["method_id"] == "COST_ROLLING_REFERENCE"
    )["state"] == "READY"

    # This deterministic fixture has fewer than the monthly method windows;
    # execution is admitted but readiness remains honestly MISSING.
    assert cells[("monthly", "SUPPLY")]["state"] == "MISSING"
    assert cells[("monthly", "COST")]["state"] == "MISSING"
    for timeframe in ("60m", "30m", "15m", "5m"):
        assert cells[(timeframe, "SUPPLY")]["state"] == "PARTIAL"
        assert cells[(timeframe, "COST")]["state"] == "PARTIAL"

    for timeframe, family in (
        ("weekly", "SUPPLY"), ("weekly", "COST"),
        ("30m", "SUPPLY"), ("30m", "COST"),
    ):
        for method in cells[(timeframe, family)]["method_coverage"]:
            if method["method_id"] in reg.SOURCE_BOUND_TIMEFRAME_METHOD_IDS:
                binding = method["runtime_source_binding"]
                assert binding["runtime_source_bound"] is True
                assert binding["data_rights_admitted"] is False
                assert binding["strategy_admitted"] is False
                assert method["independent_action_authority"] is False


def test_source_bound_momentum_methods_use_exact_timeframe_carrier_and_tamper_fails():
    factor = native_summary_with_mtf(
        intraday=True, volume_unit="share", typed_daily=True,
    )
    matrix = reg.compile_timeframe_family_matrix(factor)
    cells = {(row["timeframe"], row["family"]): row for row in matrix["cells"]}

    for timeframe in ("60m", "30m", "15m", "5m"):
        methods = {
            row["method_id"]: row
            for row in cells[(timeframe, "MOMENTUM")]["method_coverage"]
        }
        for method_id in ("ROC", "VOLATILITY_TR_SMA", "ATR_WILDER"):
            method = methods[method_id]
            assert method["state"] == "READY"
            assert method["runtime_source_binding"]["runtime_source_bound"] is True
            assert method["runtime_source_binding"]["data_rights_admitted"] is False
            assert method["independent_action_authority"] is False
        divergence = methods["CONFIRMED_DIVERGENCE"]
        assert divergence["state"] == "MISSING"
        assert divergence["reason"] == "CONFIRMED_SAME_KIND_PIVOT_PAIR_MISSING"
        assert divergence["independent_action_authority"] is False

    forged = deepcopy(factor)
    forged["multi_timeframe_structure_context"]["timeframes"]["30m"][
        "volatility_momentum"
    ]["source_identity_hash"] = "0" * 64
    forged_matrix = reg.compile_timeframe_family_matrix(forged)
    forged_cell = next(
        row for row in forged_matrix["cells"]
        if (row["timeframe"], row["family"]) == ("30m", "MOMENTUM")
    )
    forged_methods = {
        row["method_id"]: row for row in forged_cell["method_coverage"]
    }
    for method_id in ("ROC", "VOLATILITY_TR_SMA", "ATR_WILDER", "CONFIRMED_DIVERGENCE"):
        method = forged_methods[method_id]
        assert method["state"] == "UNKNOWN"
        assert method["reason"] == "TIMEFRAME_MOMENTUM_SOURCE_IDENTITY_MISMATCH"
        assert method["runtime_source_binding"]["runtime_source_bound"] is False
        assert method["independent_action_authority"] is False


def test_source_bound_classical_patterns_use_weekly_carrier_and_tamper_fails():
    factor = native_summary_with_mtf(
        intraday=True, volume_unit="share", typed_daily=True,
    )
    matrix = reg.compile_timeframe_family_matrix(factor)
    cell = next(
        row for row in matrix["cells"]
        if (row["timeframe"], row["family"]) == ("weekly", "PATTERN")
    )
    methods = {
        row["method_id"]: row for row in cell["method_coverage"]
    }
    for method_id in (
        "CUP_HANDLE", "DOUBLE_BOTTOM", "VCP", "FLAT_BASE",
        "TIGHT_CONSOLIDATION",
    ):
        method = methods[method_id]
        assert method["state"] == "READY"
        assert method["reason"] in {
            "TIMEFRAME_CLASSICAL_PATTERN_OBSERVED",
            "TIMEFRAME_CLASSICAL_PATTERN_EVALUATED_NO_SIGNAL",
        }
        assert method["runtime_source_binding"]["runtime_source_bound"] is True
        assert method["runtime_source_binding"]["data_rights_admitted"] is False
        assert method["independent_action_authority"] is False

    forged = deepcopy(factor)
    forged["multi_timeframe_structure_context"]["timeframes"]["weekly"][
        "pattern_trigger"
    ]["broad_patterns"]["source_identity_hash"] = "0" * 64
    forged_matrix = reg.compile_timeframe_family_matrix(forged)
    forged_cell = next(
        row for row in forged_matrix["cells"]
        if (row["timeframe"], row["family"]) == ("weekly", "PATTERN")
    )
    forged_methods = {
        row["method_id"]: row for row in forged_cell["method_coverage"]
    }
    for method_id in (
        "CUP_HANDLE", "DOUBLE_BOTTOM", "VCP", "FLAT_BASE",
        "TIGHT_CONSOLIDATION",
    ):
        method = forged_methods[method_id]
        assert method["state"] == "UNKNOWN"
        assert method["reason"] == "TIMEFRAME_CLASSICAL_PATTERN_RECEIPT_UNBOUND"
        assert method["runtime_source_binding"]["runtime_source_bound"] is False
        assert method["independent_action_authority"] is False


def _factor_with_source_bound_30m_candlestick():
    from tests.test_candlestick_pattern_service import (
        _completed_30m_history,
        _completed_30m_structure,
    )

    factor = native_summary_with_mtf(
        intraday=True, volume_unit="share", typed_daily=True,
    )
    history = _completed_30m_history()
    target = pd.Timestamp(history.iloc[-1]["date"]).tz_convert("UTC")
    frame = factor["multi_timeframe_structure_context"]["timeframes"]["30m"]
    source_identity_hash = frame["intraday_identity_hash"]
    structure = _completed_30m_structure(history)
    structure["intraday_identity_hash"] = source_identity_hash
    evidence = build_completed_timeframe_candlestick_context(
        stock_code="600519", history=history, timeframe="30m",
        target_bar_end=target, market="cn",
        price_structure_context=structure,
        supply_demand_context={
            "status": "READY", "target_date": target.date().isoformat(),
            "state": "BALANCED",
        },
        source_identity_hash=source_identity_hash,
        evidence_available_at=target + pd.Timedelta(hours=2),
    )
    assert evidence["status"] == "READY"
    frame["pattern_trigger"] = {
        "status": "READY",
        "reason": evidence["reason"],
        "timeframe": "30m",
        "candlestick": evidence,
        "broad_patterns": {
            "status": "NOT_ADMITTED",
            "reason": "NON_DAILY_CLASSICAL_BASE_GEOMETRY_NOT_YET_ADMITTED",
        },
        "independent_action_authority": False,
        "strategy_admitted": False,
        "learning_admitted": False,
    }
    factor["evidence_traceability"] = reg.build_runtime_trace(factor)
    return factor


def test_source_bound_30m_candlestick_reaches_pattern_matrix_without_authority():
    factor = _factor_with_source_bound_30m_candlestick()
    matrix = factor["evidence_traceability"]["timeframe_family_matrix"]
    cell = next(
        row for row in matrix["cells"]
        if (row["timeframe"], row["family"]) == ("30m", "PATTERN")
    )
    method = next(
        row for row in cell["method_coverage"]
        if row["method_id"] == "CANDLESTICK"
    )
    # Candlestick is source-bound, but this hand-built fixture intentionally
    # leaves the broad classical-pattern carrier unadmitted.
    assert cell["state"] == "PARTIAL"
    assert method["state"] == "READY"
    assert method["reason"] == "TIMEFRAME_SOURCE_BOUND_CANDLESTICK_OBSERVED"
    assert method["canonical_path"].endswith("30m.pattern_trigger.candlestick")
    assert method["runtime_source_binding"]["runtime_source_bound"] is True
    assert method["runtime_source_binding"]["data_rights_admitted"] is False
    assert method["independent_action_authority"] is False
    assert factor["strategy_eligibility"]["required_evidence"][
        "thirty_minute_trigger"
    ] == "UNKNOWN"


def test_forged_30m_candlestick_without_timeframe_carrier_is_unknown():
    factor = _factor_with_source_bound_30m_candlestick()
    frame = factor["multi_timeframe_structure_context"]["timeframes"]["30m"]
    frame["intraday_identity_hash"] = None
    factor["evidence_traceability"] = reg.build_runtime_trace(factor)
    cell = next(
        row for row in factor["evidence_traceability"]["timeframe_family_matrix"]["cells"]
        if (row["timeframe"], row["family"]) == ("30m", "PATTERN")
    )
    method = next(row for row in cell["method_coverage"] if row["method_id"] == "CANDLESTICK")
    assert method["state"] == "UNKNOWN"
    assert method["reason"] == "TIMEFRAME_CANDLESTICK_RECEIPT_UNBOUND"
    assert method["runtime_source_binding"]["runtime_source_bound"] is False


def test_forged_supply_and_cost_values_without_timeframe_source_carrier_remain_unknown():
    frame = {
        "status": "PARTIAL",
        "completed_bar_only": True,
        "observations": 80,
        "source_alignment": {"status": "SINGLE_SOURCE", "sources": ["Fixture"]},
        "supply_demand": {
            "status": "READY",
            "relative_volume": {"volume_ratio_20_completed_bars": 1.2},
            "directional_volume": {"signed_volume_balance": 0.2},
            "close_location_flow": {"cmf_20": 0.1},
        },
        "cost_bar_reference": {
            "status": "READY",
            "window_20": {"status": "READY"},
            "window_60": {"status": "READY"},
        },
    }
    matrix = reg.compile_timeframe_family_matrix({
        "multi_timeframe_structure_context": {"timeframes": {"30m": frame}},
    })
    cells = {(row["timeframe"], row["family"]): row for row in matrix["cells"]}
    for family in ("SUPPLY", "COST"):
        methods = [
            row for row in cells[("30m", family)]["method_coverage"]
            if row["method_id"] in reg.SOURCE_BOUND_TIMEFRAME_METHOD_IDS
        ]
        assert methods
        assert all(row["state"] == "UNKNOWN" for row in methods)
        assert all(row["reason"] == "TIMEFRAME_SOURCE_CARRIER_UNBOUND" for row in methods)
        assert all(
            row["runtime_source_binding"]["runtime_source_bound"] is False
            for row in methods
        )


def _minute_matrix_volume_context(*, volume_status=None, admitted_volume=False, price_momentum=True):
    # Independent accepted family evidence: price momentum is not supply/volume.
    frame = {
        "status": "PARTIAL",
        "admitted_methods": ["TREND", "MA", "MACD", "RSI"] + (["VOLUME"] if admitted_volume else []),
        "trend": {
            "status": "READY",
            "ma_alignment": "MA5>MA10>MA20",
            "volume_status": volume_status,
            "volume_ratio_5bar": 1.25 if volume_status else None,
            "macd_status": "BULLISH" if price_momentum else None,
            "rsi_status": "NEUTRAL" if price_momentum else None,
        },
        "macd_startup_stability": {"status": "READY"} if price_momentum else {"status": "MISSING"},
        "rsi_startup_stability": {"status": "READY"} if price_momentum else {"status": "MISSING"},
        "ma_structure": {"status": "READY"},
        "price_structure": {"status": "MISSING"},
        "strategy_admitted": False,
    }
    matrix = reg.compile_timeframe_family_matrix({
        "multi_timeframe_structure_context": {"timeframes": {"30m": frame}},
    })
    assert len(matrix["cells"]) == 56
    return {(x["timeframe"], x["family"]): x for x in matrix["cells"]}


def test_30m_supply_missing_when_only_macd_rsi_but_volume_not_admitted():
    cells = _minute_matrix_volume_context(volume_status=None, admitted_volume=False)
    supply = cells[("30m", "SUPPLY")]
    assert supply["state"] == "MISSING"
    volume_method = next(x for x in supply["method_coverage"] if x["method_id"] == "MTF_MOMENTUM_CONTEXT")
    assert volume_method["state"] == "MISSING"
    assert cells[("30m", "MOMENTUM")]["state"] == "PARTIAL"
    assert cells[("30m", "MTF")]["state"] == "PARTIAL"
    assert all(not x["independent_action_authority"] for x in supply["method_coverage"])


def test_30m_supply_keeps_legitimate_volume_and_rejects_forged_label():
    permitted = _minute_matrix_volume_context(volume_status="量能正常", admitted_volume=True)
    assert permitted[("30m", "SUPPLY")]["state"] == "PARTIAL"
    assert permitted[("30m", "MOMENTUM")]["state"] == "PARTIAL"

    unlabeled = _minute_matrix_volume_context(volume_status=None, admitted_volume=True)
    assert unlabeled[("30m", "SUPPLY")]["state"] == "MISSING"

    forged = _minute_matrix_volume_context(volume_status="量能正常", admitted_volume=False)
    assert forged[("30m", "SUPPLY")]["state"] == "MISSING"


def test_30m_momentum_missing_when_only_volume_without_macd_rsi():
    cells = _minute_matrix_volume_context(
        volume_status="量能正常", admitted_volume=True, price_momentum=False,
    )
    assert cells[("30m", "SUPPLY")]["state"] == "PARTIAL"
    assert cells[("30m", "MOMENTUM")]["state"] == "MISSING"
    assert cells[("30m", "MTF")]["state"] == "PARTIAL"


def test_complete_research_universe_view_prevents_scope_collapse_beyond_7x8():
    factor = native_summary()
    trace = reg.validate_runtime_trace(factor)
    universe = trace["complete_research_universe"]
    requirements = {row["requirement_id"]: row for row in universe["requirements"]}
    methods = {row["method_id"]: row for row in universe["methods"]}

    assert universe["schema_version"] == "complete-research-universe-view-v1"
    assert set(requirements) == EXPECTED_RESEARCH_UNIVERSE_REQUIREMENTS
    assert set(methods) == EXPECTED_METHOD_WINDOW_IDS
    assert universe["counts"] == {
        "requirements": len(EXPECTED_RESEARCH_UNIVERSE_REQUIREMENTS),
        "methods": len(EXPECTED_METHOD_WINDOW_IDS),
        "strategy_bindings": 13,
        "technical_matrix_cells": 56,
        "atomic_subfields": 7,
        "atomic_subfields_product_admitted": 0,
    }
    assert {row["plane"] for row in universe["planes"]} == {
        "TECHNICAL_EVIDENCE", "FUNDAMENTAL_AND_STRATEGY", "MARKET_AND_ASSET",
        "RESEARCH_SHADOW", "MODEL_LAYER", "DECISION",
    }
    assert requirements["VALUATION"]["plane"] == "FUNDAMENTAL_AND_STRATEGY"
    assert requirements["GLOBAL"]["plane"] == "MARKET_AND_ASSET"
    assert requirements["BREADTH"]["plane"] == "MARKET_AND_ASSET"
    assert requirements["ETF_SPECIFIC"]["plane"] == "MARKET_AND_ASSET"
    assert requirements["CHAN"]["plane"] == "RESEARCH_SHADOW"
    assert requirements["WAVE"]["plane"] == "RESEARCH_SHADOW"
    assert methods["MA_LEVEL_ALIGNMENT_DAILY"]["consumer_scope"] == "TECHNICAL_MATRIX"
    assert methods["MA_SLOPE_CROSS"]["family_targets"] == ("TREND_RS", "MTF")
    assert methods["MA_COMPRESSION_RELEASE"]["family_targets"] == ("MTF",)
    assert methods["CANDLESTICK"]["family_targets"] == ("PATTERN",)
    assert universe["timeframe_family_matrix_hash"] == trace["timeframe_family_matrix_hash"]
    body = dict(universe)
    body.pop("universe_hash")
    assert reg.digest(body) == universe["universe_hash"]
    assert trace["complete_research_universe_hash"] == universe["universe_hash"]
    data_gate = universe["full_domain_data_gate"]
    assert data_gate["full_domain_accounted_pass"] is True
    assert data_gate["full_domain_product_ready"] is False
    assert data_gate["accounted_object_count"] == 76
    assert data_gate["domain_counts"] == {
        "GLOBAL": 18, "CN_MARKET": 18, "SECTOR": 7,
        "ETF": 12, "STOCK": 17, "RECORD": 4,
    }
    assert data_gate["unresolved_product_dataset_ids"]
    assert set(universe["valuation_route_ids"]) == {
        "MATURE_INDUSTRIAL_CONSUMER", "HIGH_GROWTH_TECH", "BANK", "INSURANCE",
        "CYCLICAL_RESOURCE", "UTILITY_INFRASTRUCTURE", "REIT",
        "LOSS_MAKING_EARLY_GROWTH", "ETF",
    }
    assert set(universe["known_atomic_subfield_obligations"]) == {
        "GLOBAL_US_ACTUAL_INFLATION", "GLOBAL_US_CORE_INFLATION",
        "GLOBAL_US_SERVICE_INFLATION", "GLOBAL_US_5Y_INFLATION_EXPECTATION",
        "GLOBAL_SOX_INDEX", "GLOBAL_TECH_ETF_FLOW",
        "GLOBAL_SEMICONDUCTOR_SOFTWARE_RELATIVE",
    }



def test_exact_atomic_subfield_reuses_operation_admission_without_broad_proxy():
    dataset_id = "GLOBAL_US_ACTUAL_INFLATION"
    operation_id = "atomic-nosend-20261023"
    facts = {
        "series_id": "SYNTHETIC_CPI",
        "reference_period": "2026-09",
        "index_value": 321.5,
        "yoy_pct": 2.4,
        "release_published_at": "2026-10-23T08:30:00+00:00",
        "seasonal_adjustment": "SEASONALLY_ADJUSTED",
        "value_unit": "INDEX_1982_84_100",
    }
    source = {
        "dataset_id": dataset_id,
        "state": "PARTIAL",
        "provider_identity": "SyntheticAtomicProvider",
        "provider_route": "synthetic.atomic.release",
        "source_identity_hash": "1" * 64,
        "source_content_sha256": "2" * 64,
        "source_observed_at": "2026-10-23T09:00:00+00:00",
        "source_available_at": "2026-10-23T08:30:00+00:00",
        "completed_through": "2026-10-23",
        "units": {"value": "PERCENT"},
        "currency": "NOT_APPLICABLE",
        "adjustment_basis": "NOT_APPLICABLE",
        "revision": "SYNTHETIC_RELEASE_V1",
        "vintage": "2026-10-23T08:30:00+00:00",
        "facts": facts,
        "source_fact_hash": reg.digest(facts),
    }
    rights = seal_source_rights_evidence({
        "schema_version": "source-rights-evidence-v2",
        "operation_id": operation_id,
        "intended_use": "NO_SEND_RESEARCH",
        "provider_identity": source["provider_identity"],
        "upstream_source_owner": "SYNTHETIC_FIXTURE_NOT_REAL_LICENSE",
        "upstream_rights_state": "VERIFIED",
        "dataset_id": dataset_id,
        "consumer_scope": "MARKET_GLOBAL_TRANSMISSION",
        "terms_uri": "https://example.org/atomic-test-contract-only",
        "terms_sha256": "3" * 64,
        "terms_observed_at": "2026-10-22T09:00:00+00:00",
        "terms_scope": "SYNTHETIC_TEST_CONTRACT",
        "product_use_clause": "Synthetic fixture explicitly allows internal no-send research.",
        "product_use_clause_sha256": "c2a9b98537611cb0c9e9cb165ab0a4a699d84f5a2644a5e1e23740d0af55c6e1",
        "rights": {
            "product_use": "ALLOWED",
            "storage": "FORBIDDEN",
            "training": "FORBIDDEN",
            "redistribution": "FORBIDDEN",
        },
    })
    receipt = seal_data_object_admission_receipt({
        "schema_version": "data-object-admission-receipt-v1",
        "dataset_id": dataset_id,
        "provider_identity": source["provider_identity"],
        "provider_route": source["provider_route"],
        "native_frequency": "monthly/released",
        "source_identity_hash": source["source_identity_hash"],
        "content_sha256": source["source_content_sha256"],
        "observed_at": source["source_observed_at"],
        "available_at": source["source_available_at"],
        "completed_through": source["completed_through"],
        "units": source["units"],
        "currency": source["currency"],
        "adjustment_basis": source["adjustment_basis"],
        "revision": source["revision"],
        "vintage": source["vintage"],
        "rights": rights["rights"],
        "rights_evidence": rights,
        "historical_pit_eligible": False,
        "consumer_scope": "MARKET_GLOBAL_TRANSMISSION",
    })
    factor = native_summary()
    factor.update({
        "research_as_of": "2026-10-23T10:00:00+00:00",
        "research_operation_id": operation_id,
        "research_intended_use": "NO_SEND_RESEARCH",
        "atomic_subfield_source_observations": {dataset_id: source},
        "atomic_subfield_admission_receipts": {dataset_id: receipt},
    })
    universe = reg.compile_complete_research_universe_view(factor)
    atomic = universe["atomic_subfield_admission"]
    by_id = {row["dataset_id"]: row for row in atomic["objects"]}

    assert len(atomic["objects"]) == 7
    assert atomic["product_admitted_count"] == 1
    assert by_id[dataset_id]["product_admitted"] is True
    assert by_id[dataset_id]["storage_admitted"] is False
    assert by_id[dataset_id]["learning_admitted"] is False
    assert universe["atomic_subfield_product_admitted_ids"] == (dataset_id,)
    obligation = next(
        row for row in universe["atomic_subfield_obligations"]
        if row["dataset_id"] == dataset_id
    )
    assert obligation["facts"] == facts
    assert obligation["source_fact_hash"] == reg.digest(facts)
    assert len(universe["atomic_subfield_admission_hash"]) == 64

    missing = reg.compile_complete_research_universe_view(native_summary())
    assert missing["atomic_subfield_admission"]["product_admitted_count"] == 0
    assert missing["atomic_subfield_product_admitted_ids"] == ()

    forged_hash = deepcopy(factor)
    forged_hash["atomic_subfield_source_observations"][dataset_id][
        "source_fact_hash"
    ] = "0" * 64
    with pytest.raises(
        reg.TraceabilityError,
        match="ATOMIC_SUBFIELD_SOURCE_FACT_HASH_MISMATCH",
    ):
        reg.compile_complete_research_universe_view(forged_hash)

    forged = deepcopy(factor)
    forged["atomic_subfield_source_observations"] = {
        "GLOBAL_NOT_ACCEPTED": source,
    }
    with pytest.raises(
        reg.TraceabilityError,
        match="ATOMIC_SUBFIELD_SOURCE_OBSERVATION_ORPHAN",
    ):
        reg.compile_complete_research_universe_view(forged)


def test_generic_nonprice_source_observation_is_partial_source_not_strategy_authority():
    body = {
        "schema_version": "data-object-source-observation-v1",
        "dataset_id": "P_CODE_IDENTITY",
        "state": "PARTIAL",
        "reason_codes": ("BOARD_AND_CORPORATE_ACTIONS_NOT_IN_BASIC_RESPONSE",),
        "provider_identity": "BaostockFetcher",
        "provider_route": "baostock.query_stock_basic",
        "producer_authority": "BaostockFetcher.get_cn_stock_nonprice_research_bundle",
        "source_identity_hash": "1" * 64,
        "source_content_sha256": "2" * 64,
        "source_observed_at": "2026-10-28T07:30:00+00:00",
        "source_available_at": "2026-10-28T07:30:00+00:00",
        "completed_through": "2026-10-27",
        "units": {"identity": "TEXT"},
        "currency": "NOT_APPLICABLE",
        "adjustment_basis": "NOT_APPLICABLE",
        "revision": "observed-response-sha256:" + "2" * 64,
        "vintage": "2026-10-28T07:30:00+00:00",
        "historical_pit_eligible": False,
        "independent_source_value_admitted": True,
        "strategy_upgrade_authority": False,
        "facts": {"code": "sh.600519", "name": "贵州茅台"},
    }
    body["observation_hash"] = reg.digest(body)
    factor = native_summary()
    factor["data_object_source_observations"] = {
        "P_CODE_IDENTITY": body,
    }

    universe = reg.compile_complete_research_universe_view(factor)
    row = next(
        item for item in universe["data_objects"]
        if item["dataset_id"] == "P_CODE_IDENTITY"
    )
    assert row["state"] == "PARTIAL"
    assert row["provider_identity"] == "BaostockFetcher"
    assert row["source_fact_hash"] == reg.digest(body["facts"])
    assert row["historical_pit_eligible"] is False
    assert row["strategy_upgrade_authority"] is False
    assert universe["full_domain_data_gate"]["full_domain_product_ready"] is False

    forged = deepcopy(factor)
    forged["data_object_source_observations"]["P_CODE_IDENTITY"] = {
        **body,
        "facts": {"code": "sh.000001", "name": "伪造"},
    }
    with pytest.raises(
        reg.TraceabilityError,
        match="DATA_OBJECT_SOURCE_OBSERVATION_HASH_MISMATCH",
    ):
        reg.compile_complete_research_universe_view(forged)


def _sse_json_fixture(
    sql_id, rows, *, pagination="false", page_help=None
):
    document = {
        "actionErrors": [],
        "fieldErrors": {},
        "isPagination": pagination,
        "queryDate": "",
        "result": rows,
        "sqlId": sql_id,
    }
    if page_help is not None:
        document["pageHelp"] = page_help
    return json.dumps(
        document, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def _sse_disclosure_fixtures():
    company = _sse_json_fixture(
        "COMMON_SSE_CP_GPJCTPZ_GPLB_GPGK_GSGK_C",
        [{
            "A_LIST_DATE": "20010827",
            "A_STOCK_CODE": "600519",
            "COMPANY_CODE": "600519",
            "FULL_NAME": "贵州茅台酒股份有限公司",
            "SECURITY_ABBR_A_CN": "贵州茅台",
            "STATE_CODE_A_DESC": "上市",
            "LIST_BOARD": "1",
            "SEC_TYPE": "主板A",
            "CSRC_CODE": "C",
            "CSRC_CODE_DESC": "制造业",
            "CSRC_GREAT_CODE": "15",
            "CSRC_GREAT_CODE_DESC": "酒、饮料和精制茶制造业",
        }],
    )
    basic = _sse_json_fixture(
        "COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C",
        [{
            "NAV": "￥4.1176",
            "CREATION_REDEMPTION_UNIT": "900000",
            "FUND_NAME": "华泰柏瑞沪深300交易型开放式指数证券投资基金",
            "FUND_COMP_NAME": "华泰柏瑞基金管理有限公司",
            "CREATION_REDEMPTION": "申购和赎回皆允许",
            "TRADING_DAY": "20261030",
            "PRE_TRADING_DAY": "20261029",
            "FILE_ID": "1508913",
            "RECORD_NUM": "2",
            "PUBLISH_IOPV": "是",
            "TRADE_CODE": "510300",
        }],
    )
    basket = _sse_json_fixture(
        "COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_COMPONENT_C",
        [
            {
                "INSTRUMENT_ID": "000001",
                "INSTRUMENT_NAME": "平安银行",
                "QUANTITY": "1600",
                "SUBSTITUTION_FLAG": "1",
                "SUBSTITUTION_CASH_AMOUNT": "18464.000",
                "CREATION_PREMIUM_RATE": "10%",
                "REDEMPTION_DISCOUNT_RATE": "10%",
                "UNDERLYION_SECURITY_ID": "102",
            },
            {
                "INSTRUMENT_ID": "600519",
                "INSTRUMENT_NAME": "贵州茅台",
                "QUANTITY": "0",
                "SUBSTITUTION_FLAG": "2",
                "SUBSTITUTION_CASH_AMOUNT": "-",
                "CREATION_PREMIUM_RATE": "10%",
                "REDEMPTION_DISCOUNT_RATE": "10%",
                "UNDERLYION_SECURITY_ID": "101",
            },
        ],
    )
    urls = {
        "company": (
            "https://query.sse.com.cn/commonQuery.do?isPagination=false"
            "&sqlId=COMMON_SSE_CP_GPJCTPZ_GPLB_GPGK_GSGK_C"
            "&COMPANY_CODE=600519"
        ),
        "etf_basic": (
            "https://query.sse.com.cn/commonQuery.do?isPagination=false"
            "&sqlId=COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C"
            "&FUNDID2=510300"
        ),
        "etf_basket": (
            "https://query.sse.com.cn/commonQuery.do?isPagination=false"
            "&sqlId=COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_COMPONENT_C"
            "&FUNDID2=510300"
        ),
    }
    return company, basic, basket, urls


def _sse_synthetic_rights(
    dataset_id, consumer_scope, *, terms_observed_at="2026-10-31T03:00:00+00:00"
):
    from hashlib import sha256
    clause = "SYNTHETIC ONLY - current SSE disclosure may be used in NO_SEND tests."
    return seal_source_rights_evidence({
        "schema_version": "source-rights-evidence-v2",
        "operation_id": "fixture-sse-disclosure-nosend",
        "intended_use": "NO_SEND_RESEARCH",
        "provider_identity": "SSEOfficialPublicDisclosure",
        "upstream_source_owner": "synthetic-unit-test-only",
        "upstream_rights_state": "VERIFIED",
        "dataset_id": dataset_id,
        "consumer_scope": consumer_scope,
        "terms_uri": "https://example.invalid/terms/sse-fixture",
        "terms_sha256": "d" * 64,
        "terms_observed_at": terms_observed_at,
        "terms_scope": "SYNTHETIC_TEST_CONTRACT",
        "product_use_clause": clause,
        "product_use_clause_sha256": sha256(clause.encode("utf-8")).hexdigest(),
        "rights": {
            "product_use": "ALLOWED",
            "storage": "FORBIDDEN",
            "training": "FORBIDDEN",
            "redistribution": "FORBIDDEN",
        },
    })


def _sse_b1_context(*, terms_observed_at="2026-10-31T03:00:00+00:00"):
    consumers = {
        "P_CODE_IDENTITY": "ASSET_RESEARCH_BRIEF",
        "P_INDUSTRY": "ASSET_RESEARCH_BRIEF",
        "S_CLASSIFICATION": "MARKET_SECTOR_SECTION",
        "E_NAV_IOPV": "ETF_ASSET_BRIEF",
        "E_SHARES": "ETF_ASSET_BRIEF",
        "E_BASKET": "ETF_ASSET_BRIEF",
        "P_SHARES_EVENTS": "ASSET_RESEARCH_BRIEF",
        "P_DIVIDEND": "ASSET_RESEARCH_BRIEF",
    }
    return {
        "schema_version": "b1-operation-rights-context-v1",
        "operation_id": "fixture-sse-disclosure-nosend",
        "intended_use": "NO_SEND_RESEARCH",
        "allowed_instruments": ["600519", "510300"],
        "research_as_of": "2026-10-31T06:00:00+00:00",
        "rights_by_dataset": {
            dataset_id: _sse_synthetic_rights(
                dataset_id,
                consumer,
                terms_observed_at=terms_observed_at,
            )
            for dataset_id, consumer in consumers.items()
        },
    }


def _build_sse_fixture_bundle(*, b1=None, company=None, basic=None, basket=None, urls=None):
    from datetime import datetime, timezone
    from data_provider.sse_public_disclosure_source import (
        build_sse_public_disclosure_bundle,
    )
    base_company, base_basic, base_basket, base_urls = _sse_disclosure_fixtures()
    return build_sse_public_disclosure_bundle(
        base_company if company is None else company,
        base_basic if basic is None else basic,
        base_basket if basket is None else basket,
        company_request_url=(urls or base_urls)["company"],
        etf_basic_request_url=(urls or base_urls)["etf_basic"],
        etf_basket_request_url=(urls or base_urls)["etf_basket"],
        company_code="600519",
        fund_code="510300",
        observed_at=datetime(2026, 10, 31, 5, 0, tzinfo=timezone.utc),
        b1_context=b1 or _sse_b1_context(),
    )


def _sse_event_fixtures(*, reconciled=False):
    capital_total = "125619.78" if reconciled else "125008.16"
    capital = _sse_json_fixture(
        "COMMON_SSE_CP_GPJCTPZ_GPLB_GPGK_GBJG_C",
        [{
            "TOTAL_DOMESTIC_VOL": capital_total,
            "A_LIMIT_VOL": "0.00",
            "TRADE_DATE": "20261030",
            "B_VOL": "0.00",
            "SPECIAL_VOL": "-",
            "TOTAL_UNLIMIT_VOL": capital_total,
            "A_UNLIMIT_VOL": capital_total,
            "CDR_VOL": "0.00",
        }],
    )
    event_rows = [
        {
            "AShares": 125619.78,
            "BShares": 0,
            "changeReason": "51",
            "changeReasonDesc": "送股",
            "realDate": "2015-07-20",
            "seq": 6667628,
            "totalShares": 125619.78,
        },
        {
            "AShares": 7150,
            "BShares": 0,
            "changeReason": "31",
            "changeReasonDesc": "A股新上市",
            "realDate": "2001-08-27",
            "seq": 330322,
            "totalShares": 25000,
        },
    ]
    events = json.dumps({
        "companyCode": "600519",
        "isPagination": "true",
        "result": event_rows,
        "pageHelp": {"total": len(event_rows), "data": event_rows},
    }, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    dividend_rows = [
        {
            "A_BEFR_TAX_DIV": "28.02423",
            "A_STOCK_CODE": "600519",
            "A_DIV_DATE": "20260626",
            "A_REG_DATE": "20260625",
            "COMPANY_CODE": "600519",
            "A_STOCK_VOL": "125008.16",
        },
        {
            "A_BEFR_TAX_DIV": "23.957",
            "A_STOCK_CODE": "600519",
            "A_DIV_DATE": "20251219",
            "A_REG_DATE": "20251218",
            "COMPANY_CODE": "600519",
            "A_STOCK_VOL": "125227.02",
        },
    ]
    dividend = _sse_json_fixture(
        "COMMON_SSE_CP_GPJCTPZ_GPLB_LRFP_FH_L",
        dividend_rows,
        pagination="true",
        page_help={"total": len(dividend_rows), "data": dividend_rows},
    )
    urls = {
        "capital": (
            "https://query.sse.com.cn/commonQuery.do?isPagination=false"
            "&sqlId=COMMON_SSE_CP_GPJCTPZ_GPLB_GPGK_GBJG_C"
            "&COMPANY_CODE=600519"
        ),
        "events": (
            "https://query.sse.com.cn/security/stock/"
            "queryEquityChangeAndReason.do?isPagination=true"
            "&pageHelp.pageSize=50&pageHelp.pageNo=1"
            "&pageHelp.beginPage=1&pageHelp.cacheSize=1"
            "&pageHelp.endPage=1&companyCode=600519"
        ),
        "dividend": (
            "https://query.sse.com.cn/commonQuery.do?isPagination=true"
            "&pageHelp.pageSize=20&pageHelp.pageNo=1"
            "&pageHelp.beginPage=1&pageHelp.cacheSize=1"
            "&pageHelp.endPage=1"
            "&sqlId=COMMON_SSE_CP_GPJCTPZ_GPLB_LRFP_FH_L"
            "&IS_STAR=0&CONDITION_ZBA=1&CONDITION_ZBB="
            "&COMPANY_CODE=600519"
        ),
    }
    return capital, events, dividend, urls


def _build_sse_event_fixture_bundle(
    *, b1=None, capital=None, events=None, dividend=None, urls=None,
    reconciled=False,
):
    from datetime import datetime, timezone
    from data_provider.sse_public_disclosure_source import (
        build_sse_stock_event_disclosure_bundle,
    )
    values = _sse_event_fixtures(reconciled=reconciled)
    base_capital, base_events, base_dividend, base_urls = values
    return build_sse_stock_event_disclosure_bundle(
        base_capital if capital is None else capital,
        base_events if events is None else events,
        base_dividend if dividend is None else dividend,
        capital_request_url=(urls or base_urls)["capital"],
        equity_change_request_url=(urls or base_urls)["events"],
        dividend_request_url=(urls or base_urls)["dividend"],
        company_code="600519",
        observed_at=datetime(2026, 10, 31, 5, 0, tzinfo=timezone.utc),
        b1_context=b1 or _sse_b1_context(),
    )


def test_sse_stock_events_keep_real_mismatch_partial_and_admit_dividend():
    from src.services.data_object_admission import (
        compile_b1_runtime_admission_receipts,
    )

    bundle = _build_sse_event_fixture_bundle(reconciled=False)
    shares = bundle["stock_observations"]["P_SHARES_EVENTS"]
    dividend = bundle["stock_observations"]["P_DIVIDEND"]
    assert shares["state"] == "PARTIAL"
    assert "LATEST_EQUITY_EVENT_DOES_NOT_RECONCILE_CURRENT_CAPITAL" in (
        shares["reason_codes"]
    )
    assert dividend["state"] == "READY"
    assert dividend["facts"]["record_count"] == 2
    assert dividend["facts"][
        "latest_cash_dividend_before_tax_cny_per_share"
    ] == "28.02423"

    factor = native_summary()
    factor["data_object_source_observations"] = bundle["stock_observations"]
    objects = reg._compile_accepted_data_object_scope(factor)
    b1 = _sse_b1_context()
    receipts = compile_b1_runtime_admission_receipts(objects, b1_context=b1)
    view = compile_data_object_admission_view(
        objects, receipts, as_of=b1["research_as_of"],
        operation_id=b1["operation_id"],
        intended_use=b1["intended_use"],
    )
    admitted = {
        row["dataset_id"] for row in view["objects"]
        if row["product_admitted"]
    }
    assert admitted == {"P_DIVIDEND"}
    shares_row = next(
        row for row in view["objects"]
        if row["dataset_id"] == "P_SHARES_EVENTS"
    )
    assert shares_row["product_admitted"] is False
    assert "UNITS_BASIS_REVISION_OR_VINTAGE_UNKNOWN" in (
        shares_row["reasons"]
    )


def test_sse_stock_event_reconciled_positive_is_reachable_without_learning():
    from src.services.data_object_admission import (
        compile_b1_runtime_admission_receipts,
    )

    bundle = _build_sse_event_fixture_bundle(reconciled=True)
    assert bundle["stock_observations"]["P_SHARES_EVENTS"]["state"] == "READY"
    factor = native_summary()
    factor["data_object_source_observations"] = bundle["stock_observations"]
    objects = reg._compile_accepted_data_object_scope(factor)
    b1 = _sse_b1_context()
    receipts = compile_b1_runtime_admission_receipts(objects, b1_context=b1)
    view = compile_data_object_admission_view(
        objects, receipts, as_of=b1["research_as_of"],
        operation_id=b1["operation_id"],
        intended_use=b1["intended_use"],
    )
    admitted = {
        row["dataset_id"] for row in view["objects"]
        if row["product_admitted"]
    }
    assert admitted == {"P_DIVIDEND", "P_SHARES_EVENTS"}
    assert not any(row["learning_admitted"] for row in view["objects"])


def test_sse_event_bundle_merges_with_company_disclosures_in_pipeline():
    from datetime import datetime, timezone
    from src.core.pipeline import StockAnalysisPipeline

    company, basic, basket, urls = _sse_disclosure_fixtures()
    capital, events, dividend, event_urls = _sse_event_fixtures()
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.b1_rights_context = _sse_b1_context()
    pipeline._b1_nonprice_shared_observations = {}
    pipeline._b1_treasury_global_observations = {}
    pipeline._b1_nonprice_stock_bundles = {}
    pipeline._b1_sse_stock_disclosure_observations = {}
    pipeline._b1_sse_etf_disclosure_observations = {}
    pipeline._ingest_b1_sse_public_disclosure_responses(
        company_response_json=company,
        etf_basic_response_json=basic,
        etf_basket_response_json=basket,
        company_request_url=urls["company"],
        etf_basic_request_url=urls["etf_basic"],
        etf_basket_request_url=urls["etf_basket"],
        company_code="600519", fund_code="510300",
        observed_at=datetime(2026, 10, 31, 5, 0, tzinfo=timezone.utc),
    )
    event_bundle = pipeline._ingest_b1_sse_stock_event_disclosure_responses(
        capital_response_json=capital,
        equity_change_response_json=events,
        dividend_response_json=dividend,
        capital_request_url=event_urls["capital"],
        equity_change_request_url=event_urls["events"],
        dividend_request_url=event_urls["dividend"],
        company_code="600519",
        observed_at=datetime(2026, 10, 31, 5, 1, tzinfo=timezone.utc),
    )
    stock = pipeline._b1_nonprice_fundamental_context(
        code="600519", asset_route="STOCK", market="cn",
    )
    assert set(stock["data_object_source_observations"]) == {
        "P_CODE_IDENTITY", "P_INDUSTRY", "S_CLASSIFICATION",
        "P_SHARES_EVENTS", "P_DIVIDEND",
    }
    assert stock["data_object_source_observations"]["P_DIVIDEND"] == (
        event_bundle["stock_observations"]["P_DIVIDEND"]
    )
    with pytest.raises(ValueError, match="B1_SSE_DISCLOSURE_SOURCE_ALREADY_BOUND"):
        pipeline._ingest_b1_sse_stock_event_disclosure_responses(
            capital_response_json=capital,
            equity_change_response_json=events,
            dividend_response_json=dividend,
            capital_request_url=event_urls["capital"],
            equity_change_request_url=event_urls["events"],
            dividend_request_url=event_urls["dividend"],
            company_code="600519",
            observed_at=datetime(2026, 10, 31, 5, 2, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_host", "bad_capital_date", "duplicate_event",
        "future_event", "dividend_future", "duplicate_dividend",
        "bad_dividend", "missing_rights", "late_terms",
    ],
)
def test_sse_stock_event_disclosure_rejects_bad_source_clock_and_schema(mutation):
    from datetime import datetime, timezone
    from data_provider.sse_public_disclosure_source import (
        SSEPublicDisclosureSourceError,
        build_sse_stock_event_disclosure_bundle,
    )

    capital, events, dividend, urls = _sse_event_fixtures()
    capital_doc = json.loads(capital)
    events_doc = json.loads(events)
    dividend_doc = json.loads(dividend)
    b1 = _sse_b1_context()
    if mutation == "wrong_host":
        urls["capital"] = urls["capital"].replace(
            "query.sse.com.cn", "fake.sse.com.cn"
        )
    elif mutation == "bad_capital_date":
        capital_doc["result"][0]["TRADE_DATE"] = "20261301"
    elif mutation == "duplicate_event":
        events_doc["result"][1]["seq"] = events_doc["result"][0]["seq"]
        events_doc["pageHelp"]["data"] = events_doc["result"]
    elif mutation == "future_event":
        events_doc["result"][0]["realDate"] = "2026-11-01"
        events_doc["pageHelp"]["data"] = events_doc["result"]
    elif mutation == "dividend_future":
        dividend_doc["result"][0]["A_DIV_DATE"] = "20261101"
    elif mutation == "duplicate_dividend":
        dividend_doc["result"][1]["A_REG_DATE"] = (
            dividend_doc["result"][0]["A_REG_DATE"]
        )
        dividend_doc["result"][1]["A_DIV_DATE"] = (
            dividend_doc["result"][0]["A_DIV_DATE"]
        )
    elif mutation == "bad_dividend":
        dividend_doc["result"][0]["A_BEFR_TAX_DIV"] = "NaN"
    elif mutation == "missing_rights":
        b1["rights_by_dataset"].pop("P_DIVIDEND")
    elif mutation == "late_terms":
        b1 = _sse_b1_context(
            terms_observed_at="2026-10-31T05:30:00+00:00"
        )
    capital = json.dumps(capital_doc, ensure_ascii=False).encode("utf-8")
    events = json.dumps(events_doc, ensure_ascii=False).encode("utf-8")
    dividend = json.dumps(dividend_doc, ensure_ascii=False).encode("utf-8")
    with pytest.raises(
        (SSEPublicDisclosureSourceError, ValueError),
        match="SSE_DISCLOSURE_|B1_",
    ):
        build_sse_stock_event_disclosure_bundle(
            capital,
            events,
            dividend,
            capital_request_url=urls["capital"],
            equity_change_request_url=urls["events"],
            dividend_request_url=urls["dividend"],
            company_code="600519",
            observed_at=datetime(2026, 10, 31, 5, 0, tzinfo=timezone.utc),
            b1_context=b1,
        )


def test_sse_public_disclosures_reach_registry_with_only_two_product_objects():
    from src.services.data_object_admission import compile_b1_runtime_admission_receipts

    bundle = _build_sse_fixture_bundle()
    assert set(bundle["stock_observations"]) == {
        "P_CODE_IDENTITY", "P_INDUSTRY", "S_CLASSIFICATION",
    }
    assert set(bundle["etf_observations"]) == {
        "E_NAV_IOPV", "E_SHARES", "E_BASKET",
    }
    assert bundle["stock_observations"]["P_CODE_IDENTITY"]["state"] == "READY"
    assert bundle["etf_observations"]["E_BASKET"]["facts"]["component_count"] == 2
    assert bundle["storage_admitted"] is False
    assert bundle["training_admitted"] is False

    b1 = _sse_b1_context()
    stock_factor = native_summary()
    stock_factor["asset_type"] = "stock"
    stock_factor["data_object_source_observations"] = bundle["stock_observations"]
    stock_objects = reg._compile_accepted_data_object_scope(stock_factor)
    stock_receipts = compile_b1_runtime_admission_receipts(
        stock_objects, b1_context=b1,
    )
    stock_view = compile_data_object_admission_view(
        stock_objects,
        stock_receipts,
        as_of=b1["research_as_of"],
        operation_id=b1["operation_id"],
        intended_use=b1["intended_use"],
    )
    stock_admitted = {
        row["dataset_id"] for row in stock_view["objects"]
        if row["product_admitted"]
    }
    assert stock_admitted == {"P_CODE_IDENTITY"}
    for dataset_id in ("P_INDUSTRY", "S_CLASSIFICATION"):
        row = next(
            item for item in stock_view["objects"]
            if item["dataset_id"] == dataset_id
        )
        assert row["product_admitted"] is False
        assert "UNITS_BASIS_REVISION_OR_VINTAGE_UNKNOWN" in row["reasons"]

    etf_factor = native_summary()
    etf_factor["asset_type"] = "etf"
    etf_factor["data_object_source_observations"] = bundle["etf_observations"]
    etf_objects = reg._compile_accepted_data_object_scope(etf_factor)
    etf_receipts = compile_b1_runtime_admission_receipts(
        etf_objects, b1_context=b1,
    )
    etf_view = compile_data_object_admission_view(
        etf_objects,
        etf_receipts,
        as_of=b1["research_as_of"],
        operation_id=b1["operation_id"],
        intended_use=b1["intended_use"],
    )
    etf_admitted = {
        row["dataset_id"] for row in etf_view["objects"]
        if row["product_admitted"]
    }
    assert etf_admitted == {"E_BASKET"}
    for dataset_id in ("E_NAV_IOPV", "E_SHARES"):
        row = next(
            item for item in etf_view["objects"]
            if item["dataset_id"] == dataset_id
        )
        assert row["product_admitted"] is False
        assert "UNITS_BASIS_REVISION_OR_VINTAGE_UNKNOWN" in row["reasons"]
    assert reg.compile_full_domain_data_gate(
        stock_objects, stock_view,
    )["full_domain_product_ready"] is False
    assert reg.compile_full_domain_data_gate(
        etf_objects, etf_view,
    )["full_domain_product_ready"] is False


def test_sse_public_disclosure_bundle_reaches_pipeline_routes_without_network():
    from datetime import datetime, timezone
    from src.core.pipeline import StockAnalysisPipeline

    company, basic, basket, urls = _sse_disclosure_fixtures()
    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.b1_rights_context = _sse_b1_context()
    pipeline._b1_nonprice_shared_observations = {}
    pipeline._b1_treasury_global_observations = {}
    pipeline._b1_nonprice_stock_bundles = {}
    pipeline._b1_sse_stock_disclosure_observations = {}
    pipeline._b1_sse_etf_disclosure_observations = {}
    bundle = pipeline._ingest_b1_sse_public_disclosure_responses(
        company_response_json=company,
        etf_basic_response_json=basic,
        etf_basket_response_json=basket,
        company_request_url=urls["company"],
        etf_basic_request_url=urls["etf_basic"],
        etf_basket_request_url=urls["etf_basket"],
        company_code="600519",
        fund_code="510300",
        observed_at=datetime(2026, 10, 31, 5, 0, tzinfo=timezone.utc),
    )
    stock = pipeline._b1_nonprice_fundamental_context(
        code="600519", asset_route="STOCK", market="cn",
    )
    etf = pipeline._b1_nonprice_fundamental_context(
        code="510300", asset_route="ETF", market="cn",
    )
    assert set(stock["data_object_source_observations"]) == set(
        bundle["stock_observations"]
    )
    assert set(etf["data_object_source_observations"]) == set(
        bundle["etf_observations"]
    )
    assert stock["coverage"]["product_fact_count"] == 0
    assert etf["coverage"]["product_fact_count"] == 0
    with pytest.raises(ValueError, match="B1_SSE_DISCLOSURE_SOURCE_ALREADY_BOUND"):
        pipeline._ingest_b1_sse_public_disclosure_responses(
            company_response_json=company,
            etf_basic_response_json=basic,
            etf_basket_response_json=basket,
            company_request_url=urls["company"],
            etf_basic_request_url=urls["etf_basic"],
            etf_basket_request_url=urls["etf_basket"],
            company_code="600519",
            fund_code="510300",
            observed_at=datetime(2026, 10, 31, 5, 1, tzinfo=timezone.utc),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_host", "wrong_sql_id", "wrong_company_code", "wrong_fund_code",
        "duplicate_component", "basket_count_mismatch", "future_trading_day",
        "bad_nav", "missing_rights", "late_terms", "response_after_asof",
        "reported_error",
    ],
)
def test_sse_public_disclosure_rejects_source_rights_clock_and_schema(mutation):
    from datetime import datetime, timezone
    from data_provider.sse_public_disclosure_source import (
        SSEPublicDisclosureSourceError,
        build_sse_public_disclosure_bundle,
    )
    company, basic, basket, urls = _sse_disclosure_fixtures()
    company_doc = json.loads(company)
    basic_doc = json.loads(basic)
    basket_doc = json.loads(basket)
    b1 = _sse_b1_context()
    observed = datetime(2026, 10, 31, 5, 0, tzinfo=timezone.utc)
    if mutation == "wrong_host":
        urls["company"] = urls["company"].replace(
            "query.sse.com.cn", "fake.sse.com.cn"
        )
    elif mutation == "wrong_sql_id":
        company_doc["sqlId"] = "WRONG_SQL"
    elif mutation == "wrong_company_code":
        company_doc["result"][0]["A_STOCK_CODE"] = "600000"
    elif mutation == "wrong_fund_code":
        basic_doc["result"][0]["TRADE_CODE"] = "510050"
    elif mutation == "duplicate_component":
        basket_doc["result"][1]["INSTRUMENT_ID"] = "000001"
    elif mutation == "basket_count_mismatch":
        basic_doc["result"][0]["RECORD_NUM"] = "3"
    elif mutation == "future_trading_day":
        basic_doc["result"][0]["TRADING_DAY"] = "20261102"
    elif mutation == "bad_nav":
        basic_doc["result"][0]["NAV"] = "NaN"
    elif mutation == "missing_rights":
        b1["rights_by_dataset"].pop("E_BASKET")
    elif mutation == "late_terms":
        b1 = _sse_b1_context(terms_observed_at="2026-10-31T05:30:00+00:00")
    elif mutation == "response_after_asof":
        observed = datetime(2026, 10, 31, 7, 0, tzinfo=timezone.utc)
    elif mutation == "reported_error":
        company_doc["actionErrors"] = ["failure"]
    company = json.dumps(company_doc, ensure_ascii=False).encode("utf-8")
    basic = json.dumps(basic_doc, ensure_ascii=False).encode("utf-8")
    basket = json.dumps(basket_doc, ensure_ascii=False).encode("utf-8")
    with pytest.raises(
        (SSEPublicDisclosureSourceError, ValueError), match="SSE_DISCLOSURE_|B1_"
    ):
        build_sse_public_disclosure_bundle(
            company,
            basic,
            basket,
            company_request_url=urls["company"],
            etf_basic_request_url=urls["etf_basic"],
            etf_basket_request_url=urls["etf_basket"],
            company_code="600519",
            fund_code="510300",
            observed_at=observed,
            b1_context=b1,
        )


def test_sse_disclosure_observation_tamper_and_rights_only_are_nontriggers():
    from src.services.data_object_admission import compile_b1_runtime_admission_receipts

    bundle = _build_sse_fixture_bundle()
    factor = native_summary()
    factor["data_object_source_observations"] = bundle["stock_observations"]
    forged = deepcopy(factor)
    forged["data_object_source_observations"]["P_CODE_IDENTITY"]["facts"] = {
        **bundle["stock_observations"]["P_CODE_IDENTITY"]["facts"],
        "full_name": "forged",
    }
    with pytest.raises(
        reg.TraceabilityError,
        match="DATA_OBJECT_SOURCE_OBSERVATION_HASH_MISMATCH",
    ):
        reg.compile_complete_research_universe_view(forged)
    empty = reg._compile_accepted_data_object_scope(native_summary())
    assert compile_b1_runtime_admission_receipts(
        empty, b1_context=_sse_b1_context(),
    ) == {}


def _treasury_xml_fixture():
    # Independently documented Treasury Atom/OData hierarchy, not arbitrary XML.
    return (
        b'<?xml version="1.0" encoding="utf-8"?>'
        b'<feed xmlns="http://www.w3.org/2005/Atom" '
        b'xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices" '
        b'xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata">'
        b'<entry><updated>2026-10-29T19:07:07Z</updated>'
        b'<content type="application/xml"><m:properties>'
        b'<d:NEW_DATE>2026-10-28T00:00:00</d:NEW_DATE>'
        b'<d:BC_3MONTH>4.20</d:BC_3MONTH><d:BC_2YEAR>4.64</d:BC_2YEAR>'
        b'<d:BC_10YEAR>5.04</d:BC_10YEAR></m:properties></content></entry>'
        b'<entry><updated>2026-10-29T19:07:07Z</updated>'
        b'<content type="application/xml"><m:properties>'
        b'<d:NEW_DATE>2026-10-29T00:00:00</d:NEW_DATE>'
        b'<d:BC_3MONTH>4.18</d:BC_3MONTH><d:BC_2YEAR>4.61</d:BC_2YEAR>'
        b'<d:BC_10YEAR>4.98</d:BC_10YEAR></m:properties></content></entry></feed>'
    )


def _treasury_synthetic_rights(*, terms_observed_at="2026-10-30T03:00:00+00:00"):
    from hashlib import sha256
    clause = "SYNTHETIC ONLY - source use is allowed for isolated unit testing."
    return seal_source_rights_evidence({
        "schema_version": "source-rights-evidence-v2",
        "operation_id": "fixture-treasury-nosend-only",
        "intended_use": "NO_SEND_RESEARCH",
        "provider_identity": "USTreasuryYieldCurveOfficialXML",
        "upstream_source_owner": "synthetic-unit-test-only",
        "upstream_rights_state": "VERIFIED",
        "dataset_id": "G_UST_NOMINAL",
        "consumer_scope": "MARKET_GLOBAL_TRANSMISSION",
        "terms_uri": "https://example.invalid/terms/fixture-only",
        "terms_sha256": "a" * 64,
        "terms_observed_at": terms_observed_at,
        "terms_scope": "SYNTHETIC_TEST_CONTRACT",
        "product_use_clause": clause,
        "product_use_clause_sha256": sha256(clause.encode("utf-8")).hexdigest(),
        "rights": {
            "product_use": "ALLOWED", "storage": "FORBIDDEN",
            "training": "FORBIDDEN", "redistribution": "FORBIDDEN",
        },
    })


def test_treasury_official_xml_native_source_to_existing_registry_and_admission():
    """Synthetic body/rights test the seam, NEVER a live Product grant or PIT."""
    from datetime import datetime, timezone
    from hashlib import sha256
    from data_provider.treasury_yield_curve_source import (
        build_treasury_yield_source_observation,
    )
    from src.services.data_object_admission import compile_b1_runtime_admission_receipts

    body = _treasury_xml_fixture()
    grant = _treasury_synthetic_rights()
    asof = "2026-10-30T06:00:00+00:00"
    b1 = {
        "operation_id": "fixture-treasury-nosend-only",
        "intended_use": "NO_SEND_RESEARCH",
        "research_as_of": asof,
        "rights_by_dataset": {"G_UST_NOMINAL": grant},
    }
    source = build_treasury_yield_source_observation(
        body,
        request_url="https://home.treasury.gov/resource-center/data-chart-center/interest-rates/pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value_month=202610",
        observed_at=datetime(2026, 10, 30, 5, 0, tzinfo=timezone.utc),
        b1_context=b1,
    )
    assert source["dataset_id"] == "G_UST_NOMINAL"
    assert source["facts"]["yield_10y_pct_per_year"] == "4.98"
    assert source["facts"]["spread_10y_3m_percentage_points"] == "0.80"
    assert source["source_content_sha256"] == sha256(body).hexdigest()
    assert source["source_available_at"] == source["source_observed_at"]
    assert source["historical_pit_eligible"] is False
    assert source["strategy_upgrade_authority"] is False
    factor = native_summary()
    factor["data_object_source_observations"] = {"G_UST_NOMINAL": source}
    obligations = reg._compile_accepted_data_object_scope(factor)
    actual = next(row for row in obligations if row["dataset_id"] == "G_UST_NOMINAL")
    assert actual["source_content_sha256"] == sha256(body).hexdigest()
    receipts = compile_b1_runtime_admission_receipts(obligations, b1_context=b1)
    view = compile_data_object_admission_view(
        obligations, receipts, as_of=asof,
        operation_id=b1["operation_id"], intended_use=b1["intended_use"],
    )
    assert view["product_admitted_count"] == 0  # even synthetic fixture lacks native vintage
    admitted = next(row for row in view["objects"] if row["dataset_id"] == "G_UST_NOMINAL")
    assert admitted["product_admitted"] is False
    assert "UNITS_BASIS_REVISION_OR_VINTAGE_UNKNOWN" in admitted["reasons"]
    assert admitted["learning_admitted"] is False
    assert admitted["storage_admitted"] is False
    assert admitted["strategy_upgrade_authority"] is False
    assert reg.compile_full_domain_data_gate(obligations, view)["full_domain_product_ready"] is False
    tampered = deepcopy(factor)
    tampered["data_object_source_observations"]["G_UST_NOMINAL"]["facts"] = {
        **source["facts"], "yield_10y_pct_per_year": "9.99",
    }
    with pytest.raises(reg.TraceabilityError, match="DATA_OBJECT_SOURCE_OBSERVATION_HASH_MISMATCH"):
        reg.compile_complete_research_universe_view(tampered)
    # A grant without raw response must remain a lawful nontrigger.
    empty = reg._compile_accepted_data_object_scope(native_summary())
    assert compile_b1_runtime_admission_receipts(empty, b1_context=b1) == {}


@pytest.mark.parametrize("mutation", [
    "wrong_host", "wrong_month", "future_observation",
    "future_feed_date", "duplicate_date", "bad_rate", "missing_rights",
    "late_terms", "unexpected_content", "wrong_field_namespace",
    "wrong_property_container", "non_midnight_date",
])
def test_treasury_xml_native_producer_rejects_invalid_source_and_rights(mutation):
    from datetime import datetime, timezone
    from data_provider.treasury_yield_curve_source import (
        TreasuryYieldSourceError, build_treasury_yield_source_observation,
    )
    body = _treasury_xml_fixture()
    url = ("https://home.treasury.gov/resource-center/data-chart-center/"
           "interest-rates/pages/xml?data=daily_treasury_yield_curve"
           "&field_tdr_date_value_month=202610")
    observed = datetime(2026, 10, 30, 5, 0, tzinfo=timezone.utc)
    asof = "2026-10-30T06:00:00+00:00"
    grant = _treasury_synthetic_rights()
    if mutation == "wrong_host":
        url = url.replace("home.treasury.gov", "fake.treasury.gov")
    elif mutation == "wrong_month":
        url = url.replace("202610", "202609")
    elif mutation == "future_observation":
        asof = "2026-10-29T07:00:00+00:00"
    elif mutation == "future_feed_date":
        body = body.replace(b"2026-10-29T00:00:00", b"2026-10-31T00:00:00")
    elif mutation == "duplicate_date":
        body = body.replace(b"2026-10-28T00:00:00", b"2026-10-29T00:00:00")
    elif mutation == "bad_rate":
        body = body.replace(b"<d:BC_10YEAR>4.98", b"<d:BC_10YEAR>NaN")
    elif mutation == "missing_rights":
        grant = None
    elif mutation == "late_terms":
        grant = _treasury_synthetic_rights(
            terms_observed_at="2026-10-31T00:00:00+00:00"
        )
    elif mutation == "unexpected_content":
        body = b"<html><body>not a Treasury Atom feed</body></html>"
    elif mutation == "wrong_field_namespace":
        body = body.replace(
            b'xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices"',
            b'xmlns:d="urn:untrusted-local-name-spoof"',
        )
    elif mutation == "wrong_property_container":
        body = body.replace(b"<m:properties>", b"<m:unrelated>").replace(
            b"</m:properties>", b"</m:unrelated>"
        )
    elif mutation == "non_midnight_date":
        body = body.replace(
            b"2026-10-29T00:00:00", b"2026-10-29T08:30:00"
        )
    b1 = {
        "operation_id": "fixture-treasury-nosend-only",
        "intended_use": "NO_SEND_RESEARCH",
        "research_as_of": asof,
        "rights_by_dataset": {} if grant is None else {"G_UST_NOMINAL": grant},
    }
    with pytest.raises((TreasuryYieldSourceError, ValueError), match="TREASURY_|B1_"):
        build_treasury_yield_source_observation(
            body, request_url=url, observed_at=observed, b1_context=b1,
        )


def test_treasury_native_observation_reaches_existing_pipeline_factor_context_without_network():
    """One run-local response, no automatic provider request or DB write."""
    from datetime import datetime, timezone
    from src.core.pipeline import StockAnalysisPipeline

    pipeline = StockAnalysisPipeline.__new__(StockAnalysisPipeline)
    pipeline.b1_rights_context = {
        "schema_version": "b1-operation-rights-context-v1",
        "operation_id": "fixture-treasury-nosend-only",
        "intended_use": "NO_SEND_RESEARCH",
        "allowed_instruments": ["600519"],
        "rights_by_dataset": {"G_UST_NOMINAL": _treasury_synthetic_rights()},
        "research_as_of": "2026-10-30T06:00:00+00:00",
    }
    pipeline._b1_nonprice_shared_observations = {}
    pipeline._b1_nonprice_stock_bundles = {}
    observation = pipeline._ingest_b1_treasury_official_response(
        response_xml=_treasury_xml_fixture(),
        request_url=(
            "https://home.treasury.gov/resource-center/data-chart-center/"
            "interest-rates/pages/xml?data=daily_treasury_yield_curve"
            "&field_tdr_date_value_month=202610"
        ),
        observed_at=datetime(2026, 10, 30, 5, 0, tzinfo=timezone.utc),
    )
    fundamental = pipeline._b1_nonprice_fundamental_context(
        code="600519", asset_route="STOCK", market="cn",
    )
    assert fundamental["source_chain"] == ["USTreasuryYieldCurveOfficialXML"]
    # Source observation is not a Product-admitted fact; a count must never
    # elevate it before exact operation-grant and revision/vintage admission.
    assert fundamental["coverage"]["source_observation_count"] == 1
    assert fundamental["coverage"]["product_fact_count"] == 0
    assert fundamental["data_object_source_observations"]["G_UST_NOMINAL"] == observation
    factor = native_summary()
    factor["data_object_source_observations"] = fundamental["data_object_source_observations"]
    actual = reg.compile_complete_research_universe_view(factor)
    global_object = next(
        row for row in actual["data_objects"]
        if row["dataset_id"] == "G_UST_NOMINAL"
    )
    assert global_object["state"] == "PARTIAL"
    assert global_object["source_content_sha256"] == observation["source_content_sha256"]
    assert actual["full_domain_data_gate"]["full_domain_product_ready"] is False
    with pytest.raises(ValueError, match="B1_TREASURY_SOURCE_ALREADY_BOUND"):
        pipeline._ingest_b1_treasury_official_response(
            response_xml=_treasury_xml_fixture(),
            request_url=(
                "https://home.treasury.gov/resource-center/data-chart-center/"
                "interest-rates/pages/xml?data=daily_treasury_yield_curve"
                "&field_tdr_date_value_month=202610"
            ),
            observed_at=datetime(2026, 10, 30, 5, 2, tzinfo=timezone.utc),
        )


def test_treasury_feed_observation_clock_never_impersonates_upstream_revision_or_vintage():
    """Accepted B1 rules: response SHA and observation time are not upstream revisions."""
    from datetime import datetime, timezone
    from data_provider.treasury_yield_curve_source import build_treasury_yield_source_observation
    from src.services.data_object_admission import compile_b1_runtime_admission_receipts
    grant = _treasury_synthetic_rights()
    b1 = {
        "operation_id": "fixture-treasury-nosend-only",
        "intended_use": "NO_SEND_RESEARCH",
        "research_as_of": "2026-10-30T06:00:00+00:00",
        "rights_by_dataset": {"G_UST_NOMINAL": grant},
    }
    source = build_treasury_yield_source_observation(
        _treasury_xml_fixture(),
        request_url=(
            "https://home.treasury.gov/resource-center/data-chart-center/"
            "interest-rates/pages/xml?data=daily_treasury_yield_curve"
            "&field_tdr_date_value_month=202610"
        ),
        observed_at=datetime(2026, 10, 30, 5, 0, tzinfo=timezone.utc),
        b1_context=b1,
    )
    # No per-row revision chain or provider-confirmed vintage was supplied.
    assert source["revision"] == "UNKNOWN"
    assert source["vintage"] == "UNKNOWN"
    assert source["historical_pit_eligible"] is False
    factor = native_summary()
    factor["data_object_source_observations"] = {"G_UST_NOMINAL": source}
    obligations = reg._compile_accepted_data_object_scope(factor)
    receipts = compile_b1_runtime_admission_receipts(obligations, b1_context=b1)
    view = compile_data_object_admission_view(
        obligations, receipts, as_of=b1["research_as_of"],
        operation_id=b1["operation_id"], intended_use=b1["intended_use"],
    )
    row = next(x for x in view["objects"] if x["dataset_id"] == "G_UST_NOMINAL")
    assert row["product_admitted"] is False
    assert "UNITS_BASIS_REVISION_OR_VINTAGE_UNKNOWN" in row["reasons"]


def test_foundation_receipt_separates_raw_declared_effective_and_runtime_states():
    factor = native_summary()
    receipt = reg.compile_foundation_evidence_receipt(factor)

    assert receipt["method_profile_count"] == 43
    assert receipt["existing_method_count"] == 30
    assert receipt["deferred_method_count"] == 13
    assert receipt["technical_existing_method_count"] == 25
    assert receipt["target_method_timeframe_pairs"] == 172
    assert receipt["declared_execution_pairs"] == 34
    # CI93->technical-foundation predecessor: 136 effective/19 overrides.
    # MACD, RSI and Relative Strength gain six explicit non-daily seams each;
    # missing benchmark inputs stay MISSING and never become Product facts.
    assert receipt["effective_execution_pairs"] == 136 + 3 * 6 == 154
    assert len(receipt["effective_override_rows"]) == 19 + 3 == 22
    assert receipt["all_56_cells_executable"] is True
    assert receipt["regime_shared_projection_count"] == 7
    assert receipt["matrix_implementation_receipt"]["executable_cell_count"] == 56
    assert receipt["matrix_cell_count"] == 56
    assert sum(receipt["matrix_state_counts"].values()) == 56
    assert receipt["policy"]["declaration_is_not_runtime_ready"] is True
    assert receipt["policy"]["effective_profiles_supersede_raw_constructor_scope"] is True
    assert receipt["policy"]["historical_execution_never_backfills_current_product"] is True
    assert receipt["policy"]["independent_action_authority"] is False
    assert len(receipt["receipt_hash"]) == 64


def test_foundation_receipt_rejects_resurrection_of_raw_pre_override_profiles(monkeypatch):
    declared = {
        profile.method_id: profile
        for profile in reg.DECLARED_METHOD_WINDOW_PROFILES
    }
    resurrected = tuple(
        replace(
            profile,
            current_execution_timeframes=(
                declared[profile.method_id].current_execution_timeframes
            ),
        )
        for profile in reg.METHOD_WINDOW_PROFILES
    )
    monkeypatch.setattr(reg, "METHOD_WINDOW_PROFILES", resurrected)

    with pytest.raises(
        reg.TraceabilityError,
        match="FOUNDATION_EFFECTIVE_METHOD_SCOPE_MISMATCH",
    ):
        native_summary()


def test_full_domain_data_gate_rejects_deletion_duplicate_domain_and_state_tamper():
    factor = native_summary()
    objects = factor["evidence_traceability"]["complete_research_universe"]["data_objects"]
    gate = reg.compile_full_domain_data_gate(objects)
    assert gate["full_domain_accounted_pass"] is True
    assert gate["full_domain_product_ready"] is False

    with pytest.raises(reg.TraceabilityError, match="FULL_DOMAIN_DATA_OBJECT_SET_MISMATCH"):
        reg.compile_full_domain_data_gate(objects[:-1])
    duplicate = deepcopy(objects)
    duplicate[-1] = deepcopy(duplicate[0])
    with pytest.raises(reg.TraceabilityError, match="FULL_DOMAIN_DATA_OBJECT_SET_MISMATCH"):
        reg.compile_full_domain_data_gate(duplicate)
    wrong_domain = deepcopy(objects)
    wrong_domain[0]["domain"] = "STOCK"
    with pytest.raises(
        reg.TraceabilityError,
        match="CATALOG_BINDING_MISMATCH",
    ):
        reg.compile_full_domain_data_gate(wrong_domain)
    swapped_domains = deepcopy(objects)
    global_row = next(row for row in swapped_domains if row["domain"] == "GLOBAL")
    market_row = next(row for row in swapped_domains if row["domain"] == "CN_MARKET")
    global_row["domain"], market_row["domain"] = (
        market_row["domain"], global_row["domain"]
    )
    with pytest.raises(
        reg.TraceabilityError,
        match="CATALOG_BINDING_MISMATCH",
    ):
        reg.compile_full_domain_data_gate(swapped_domains)
    wrong_state = deepcopy(objects)
    wrong_state[0]["state"] = "READY_BY_ASSERTION"
    with pytest.raises(reg.TraceabilityError, match="STATE_INVALID"):
        reg.compile_full_domain_data_gate(wrong_state)


def test_full_domain_empty_admission_is_lawful_accounting_nontrigger():
    universe = reg.compile_complete_research_universe_view({
        "asset_type": "stock",
        "data_object_admission_receipts": {},
    })
    admission = universe["data_object_admission"]
    gate = universe["full_domain_data_gate"]

    assert len(admission["objects"]) == 76
    assert admission["product_admitted_count"] == 0
    assert admission["counts"] == {
        "LEARNING_ADMITTED": 0,
        "NOT_APPLICABLE": 12,
        "NOT_OBSERVED": 64,
        "PRODUCT_ADMITTED": 0,
        "SOURCE_IDENTITY_ONLY": 0,
    }
    assert gate["full_domain_accounted_pass"] is True
    assert gate["full_domain_product_ready"] is False
    assert gate["current_product_applicable_object_count"] == 60
    assert gate["record_object_count"] == 4
    assert len(gate["unresolved_product_dataset_ids"]) == 60
    assert len(gate["unresolved_all_applicable_dataset_ids"]) == 64
    assert set(gate["unresolved_record_dataset_ids"]) == {
        "L_LEDGER", "L_OUTCOME", "L_PIT_MANIFEST", "L_IMMUTABLE_BYTES",
    }

    forged = deepcopy(admission)
    forged["objects"].pop()
    with pytest.raises(reg.TraceabilityError, match="FULL_DOMAIN_DATA_ADMISSION_SET_MISMATCH"):
        reg.compile_full_domain_data_gate(universe["data_objects"], forged)


def _fully_product_admitted_full_domain(asset_type, *, include_record=False):
    operation_id = "op-full-domain-positive-20261026-r001"
    intended_use = "NO_SEND_RESEARCH"
    universe = reg.compile_complete_research_universe_view({
        "asset_type": asset_type,
    })
    obligations = deepcopy(universe["data_objects"])
    receipts = {}
    rights = {
        "product_use": "ALLOWED",
        "storage": "FORBIDDEN",
        "training": "FORBIDDEN",
        "redistribution": "FORBIDDEN",
    }
    for obligation in obligations:
        if obligation["state"] == "NOT_APPLICABLE":
            continue
        if obligation["domain"] == "RECORD" and not include_record:
            continue
        dataset_id = obligation["dataset_id"]
        provider = "synthetic-lawful-full-domain-provider"
        provider_route = "synthetic.full_domain." + dataset_id.lower()
        source_identity_hash = reg.digest({
            "dataset_id": dataset_id,
            "kind": "source_identity",
        })
        content_sha256 = reg.digest({
            "dataset_id": dataset_id,
            "kind": "source_content",
        })
        observed_at = "2026-10-21T10:00:00+00:00"
        available_at = "2026-10-21T09:00:00+00:00"
        completed_through = "2026-10-20"
        units = {"value": "EXPLICIT_TEST_NATIVE_UNIT"}
        currency = "CNY"
        adjustment_basis = "NOT_APPLICABLE"
        revision = "SYNTHETIC_TEST_REVISION_1"
        vintage = "2026-10-21T09:00:00+00:00"
        obligation.update({
            "state": "READY",
            "reason": "SYNTHETIC_FULL_DOMAIN_READY_TEST_ONLY",
            "provider_identity": provider,
            "provider_route": provider_route,
            "source_identity_hash": source_identity_hash,
            "source_content_sha256": content_sha256,
            "source_observed_at": observed_at,
            "source_available_at": available_at,
            "completed_through": completed_through,
            "units": units,
            "currency": currency,
            "adjustment_basis": adjustment_basis,
            "revision": revision,
            "vintage": vintage,
        })
        rights_evidence = seal_source_rights_evidence({
            "schema_version": "source-rights-evidence-v2",
            "operation_id": operation_id,
            "intended_use": intended_use,
            "provider_identity": provider,
            "upstream_source_owner": "synthetic-licensed-upstream",
            "upstream_rights_state": "VERIFIED",
            "dataset_id": dataset_id,
            "consumer_scope": obligation["product_consumer"],
            "terms_uri": "https://example.invalid/terms/full-domain-test",
            "terms_sha256": "c" * 64,
            "terms_observed_at": "2026-10-21T08:00:00+00:00",
            "terms_scope": "SYNTHETIC_TEST_CONTRACT",
            "product_use_clause": "Synthetic fixture explicitly allows internal no-send research.",
            "product_use_clause_sha256": "c2a9b98537611cb0c9e9cb165ab0a4a699d84f5a2644a5e1e23740d0af55c6e1",
            "rights": rights,
        })
        receipts[dataset_id] = seal_data_object_admission_receipt({
            "schema_version": "data-object-admission-receipt-v1",
            "dataset_id": dataset_id,
            "provider_identity": provider,
            "provider_route": provider_route,
            "native_frequency": obligation["native_frequency"],
            "source_identity_hash": source_identity_hash,
            "content_sha256": content_sha256,
            "observed_at": observed_at,
            "available_at": available_at,
            "completed_through": completed_through,
            "units": units,
            "currency": currency,
            "adjustment_basis": adjustment_basis,
            "revision": revision,
            "vintage": vintage,
            "rights": rights,
            "rights_evidence": rights_evidence,
            "historical_pit_eligible": False,
            "consumer_scope": obligation["product_consumer"],
        })
    admission = compile_data_object_admission_view(
        obligations,
        receipts,
        as_of="2026-10-21T10:30:00+00:00",
        operation_id=operation_id,
        intended_use=intended_use,
    )
    return obligations, admission


@pytest.mark.parametrize(
    ("asset_type", "expected_product", "expected_applicable"),
    (("stock", 60, 64), ("etf", 55, 59)),
)
def test_full_domain_data_gate_current_product_ready_does_not_require_future_record_artifacts(
    asset_type, expected_product, expected_applicable,
):
    obligations, admission = _fully_product_admitted_full_domain(asset_type)
    gate = reg.compile_full_domain_data_gate(obligations, admission)

    assert gate["full_domain_accounted_pass"] is True
    assert gate["full_domain_product_ready"] is True
    assert gate["full_domain_all_obligations_ready"] is False
    assert gate["applicable_object_count"] == expected_applicable
    assert gate["current_product_applicable_object_count"] == expected_product
    assert gate["product_admitted_count"] == expected_product
    assert gate["current_product_admitted_count"] == expected_product
    assert gate["unresolved_product_dataset_ids"] == ()
    assert set(gate["unresolved_record_dataset_ids"]) == {
        "L_LEDGER", "L_OUTCOME", "L_PIT_MANIFEST", "L_IMMUTABLE_BYTES",
    }
    assert admission["learning_admitted_count"] == 0


def test_full_domain_product_ready_requires_object_ready_not_only_product_rights():
    obligations, admission = _fully_product_admitted_full_domain("stock")
    partial = next(
        row for row in obligations
        if row["domain"] == "STOCK" and row["dataset_id"] == "P_CASH_FLOW"
    )
    partial["state"] = "PARTIAL"
    partial["reason"] = "SYNTHETIC_PARTIAL_SOURCE_TEST_ONLY"

    gate = reg.compile_full_domain_data_gate(obligations, admission)

    assert gate["product_admitted_count"] == 60
    assert gate["current_product_admitted_count"] == 60
    assert gate["current_product_ready_count"] == 59
    assert gate["full_domain_product_ready"] is False
    assert "P_CASH_FLOW" in gate["unresolved_product_dataset_ids"]
    assert gate["policy"][
        "product_admission_without_object_ready_is_partial_not_full_domain"
    ] is True


def test_full_domain_data_gate_all_obligations_ready_remains_a_separate_later_state():
    obligations, admission = _fully_product_admitted_full_domain(
        "stock", include_record=True,
    )
    gate = reg.compile_full_domain_data_gate(obligations, admission)

    assert gate["full_domain_product_ready"] is True
    assert gate["full_domain_all_obligations_ready"] is True
    assert gate["unresolved_product_dataset_ids"] == ()
    assert gate["unresolved_record_dataset_ids"] == ()


def test_full_domain_data_gate_rejects_cross_asset_admission_scope():
    stock_obligations, _stock_admission = _fully_product_admitted_full_domain(
        "stock"
    )
    _etf_obligations, etf_admission = _fully_product_admitted_full_domain("etf")

    with pytest.raises(
        reg.TraceabilityError,
        match="FULL_DOMAIN_DATA_ADMISSION_APPLICABILITY_MISMATCH",
    ):
        reg.compile_full_domain_data_gate(stock_obligations, etf_admission)


def test_full_domain_data_gate_rejects_admission_from_other_runtime_bytes():
    obligations, admission = _fully_product_admitted_full_domain("stock")
    changed = deepcopy(obligations)
    next(
        row for row in changed if row["state"] != "NOT_APPLICABLE"
    )["source_content_sha256"] = "f" * 64

    with pytest.raises(
        reg.TraceabilityError,
        match="FULL_DOMAIN_DATA_ADMISSION_RUNTIME_BINDING_MISMATCH",
    ):
        reg.compile_full_domain_data_gate(changed, admission)


def _exchange_daily_test_window(end, count):
    from datetime import date
    from src.core.trading_calendar import resolve_session_range_fail_closed

    sessions = resolve_session_range_fail_closed("cn", date(2025, 1, 2), end)
    assert sessions is not None and len(sessions) >= count
    return pd.DatetimeIndex(sessions[-count:])


def _closed_typed_stock_source_factor(*, source_version_known: bool = False):
    """Original DSA price identity + ten completed XSHG sessions + real MTF receipt."""
    from datetime import datetime, date, timezone
    from data_provider.daily_data_identity import (
        attach_daily_data_identity, build_daily_data_identity,
    )
    from tests.test_intraday_bar_service import _canonical_session_set
    from tests.test_multi_timeframe_structure_service import _history
    from src.services.pit_identity import build_completed_history_identity
    from src.stock_analyzer import StockTrendAnalyzer

    dates = [
        date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 16),
        date(2026, 9, 17), date(2026, 9, 18), date(2026, 9, 21),
        date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24),
        date(2026, 9, 28),
    ]
    minute = _canonical_session_set(dates)
    frame = _history(330)
    frame["date"] = _exchange_daily_test_window(dates[-1], len(frame))
    frame["data_source"] = "BaostockFetcher"
    frame["amount"] = frame["close"] * frame["volume"]
    observed = datetime(2026, 10, 2, tzinfo=timezone.utc)
    identity = build_daily_data_identity(
        frame, provider_identity="BaostockFetcher",
        provider_route="baostock.query_history_k_data_plus",
        actual_response_branch="fixture:adjustflag=2",
        requested_adjustment_basis="qfq",
        observed_adjustment_basis="qfq",
        basis_evidence="independent_fixture",
        requested_start="2025-01-01", requested_end="2026-09-28",
        currency="CNY", volume_unit="share", amount_unit="CNY",
        identity_state="OBSERVED", observed_at=observed,
        revision=("SYNTHETIC_FIXTURE_REVISION_1" if source_version_known else None),
        vintage=(observed.isoformat() if source_version_known else None),
    )
    attach_daily_data_identity(frame, identity)
    target = dates[-1]
    analyzer = StockTrendAnalyzer()
    trend = analyzer.analyze(frame.copy(), "600519")
    mtf = build_multi_timeframe_structure_context(
        stock_code="600519", history=frame, target_date=target, market="cn",
        trend_analyzer=analyzer, daily_trend_result=trend,
        snapshot_observed_at=observed,
        intraday_timeframes={"5m": minute},
    )
    source = build_completed_history_identity(
        frame, stock_code="600519", market="cn",
        target_date=target, observed_at=observed,
    )
    source.update(stock_code="600519", market="cn", target_date=target.isoformat())
    receipt = reg.build_method_execution_receipt(
        "MTF", output=mtf, asset_route="STOCK", stock_code="600519",
        market="cn", target_date=target, timeframe="multi",
        input_identity=source,
    )
    factor = {
        "asset_type": "stock",
        "multi_timeframe_structure_context": mtf,
        "method_execution_receipt_policy": "REQUIRED",
        "method_execution_receipts": {"MTF": receipt},
    }
    return factor, minute



def _closed_typed_etf_source_factor():
    """ETF price bars use the same typed source proof, never stock fundamentals."""
    from datetime import datetime, date, timezone
    from data_provider.daily_data_identity import (
        attach_daily_data_identity, build_daily_data_identity,
    )
    from data_provider.intraday_data_identity import (
        attach_intraday_data_identity, build_intraday_data_identity,
    )
    from tests.test_intraday_bar_service import _canonical_session_set
    from tests.test_multi_timeframe_structure_service import _history
    from src.services.pit_identity import build_completed_history_identity
    from src.stock_analyzer import StockTrendAnalyzer

    dates = [
        date(2026, 9, 14), date(2026, 9, 15), date(2026, 9, 16),
        date(2026, 9, 17), date(2026, 9, 18), date(2026, 9, 21),
        date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24),
        date(2026, 9, 28),
    ]
    minute = _canonical_session_set(dates).copy()
    minute.attrs.clear()
    minute = minute.drop(columns=["intraday_data_identity_hash"])
    minute["code"] = "510300"
    observed = datetime(2026, 10, 2, tzinfo=timezone.utc)
    minute_identity = build_intraday_data_identity(
        minute,
        provider_identity="BaostockFetcher",
        provider_route="baostock.query_history_k_data_plus",
        package_version="0.9.4",
        query_identity={"code": "sh.510300", "frequency": "5", "adjustflag": "2"},
        requested_adjustment_basis="qfq",
        observed_adjustment_basis="qfq",
        basis_evidence="independent_etf_fixture",
        timeframe="5m",
        timezone_name="Asia/Shanghai",
        session_calendar="XSHG",
        currency="CNY",
        volume_unit="UNKNOWN",
        amount_unit="UNKNOWN",
        requested_start=dates[0].isoformat(),
        requested_end=dates[-1].isoformat(),
        identity_state="OBSERVED",
        observed_at=observed,
        source_rows_sha256="e" * 64,
    )
    attach_intraday_data_identity(minute, minute_identity)
    minute["intraday_data_identity_hash"] = minute_identity["identity_hash"]

    frame = _history(330)
    frame["date"] = _exchange_daily_test_window(dates[-1], len(frame))
    frame["data_source"] = "BaostockFetcher"
    frame["amount"] = frame["close"] * frame["volume"]
    identity = build_daily_data_identity(
        frame,
        provider_identity="BaostockFetcher",
        provider_route="baostock.query_history_k_data_plus",
        actual_response_branch="fixture:etf;adjustflag=2",
        requested_adjustment_basis="qfq",
        observed_adjustment_basis="qfq",
        basis_evidence="independent_etf_fixture",
        requested_start="2025-01-01",
        requested_end="2026-09-28",
        currency="CNY",
        volume_unit="share",
        amount_unit="CNY",
        identity_state="OBSERVED",
        observed_at=observed,
    )
    attach_daily_data_identity(frame, identity)
    target = dates[-1]
    analyzer = StockTrendAnalyzer()
    trend = analyzer.analyze(frame.copy(), "510300")
    mtf = build_multi_timeframe_structure_context(
        stock_code="510300", history=frame, target_date=target, market="cn",
        trend_analyzer=analyzer, daily_trend_result=trend,
        snapshot_observed_at=observed,
        intraday_timeframes={"5m": minute},
    )
    source = build_completed_history_identity(
        frame, stock_code="510300", market="cn",
        target_date=target, observed_at=observed,
    )
    source.update(stock_code="510300", market="cn", target_date=target.isoformat())
    receipt = reg.build_method_execution_receipt(
        "MTF", output=mtf, asset_route="ETF", stock_code="510300",
        market="cn", target_date=target, timeframe="multi",
        input_identity=source,
    )
    factor = {
        "asset_type": "etf",
        "multi_timeframe_structure_context": mtf,
        "method_execution_receipt_policy": "REQUIRED",
        "method_execution_receipts": {"MTF": receipt},
    }
    return factor, minute


def test_m2_etf_typed_1d_5m_sources_progress_only_etf_price_objects():
    factor, minute = _closed_typed_etf_source_factor()
    trace = reg.build_runtime_trace(factor)
    data = {
        row["dataset_id"]: row
        for row in trace["complete_research_universe"]["data_objects"]
    }
    assert data["E_PRICE_1D"]["state"] == "PARTIAL"
    assert data["E_PRICE_5M"]["state"] == "PARTIAL"
    assert data["E_PRICE_1D"]["source_identity_hash"] == (
        factor["multi_timeframe_structure_context"]["daily_data_identity_hash"]
    )
    assert data["E_PRICE_5M"]["source_identity_hash"] == (
        minute.attrs["intraday_data_identity"]["identity_hash"]
    )
    assert data["E_PRICE_1D"]["independent_source_value_admitted"] is False
    assert data["E_PRICE_5M"]["strategy_upgrade_authority"] is False
    assert "UNITS_UNKNOWN" in data["E_PRICE_5M"]["reason"]
    assert data["P_DAILY"]["state"] == "NOT_APPLICABLE"
    assert data["P_5M"]["state"] == "NOT_APPLICABLE"
    for dataset_id in ("E_NAV_IOPV", "E_SHARES", "E_PREMIUM", "E_TRACKING", "E_BASKET"):
        assert data[dataset_id]["state"] == "UNKNOWN"

    missing_receipt = deepcopy(factor)
    missing_receipt["method_execution_receipts"] = {}
    missing = {
        row["dataset_id"]: row
        for row in reg.build_runtime_trace(missing_receipt)[
            "complete_research_universe"
        ]["data_objects"]
    }
    assert missing["E_PRICE_1D"]["state"] == "UNKNOWN"
    assert missing["E_PRICE_5M"]["state"] == "UNKNOWN"


def test_accepted_p_daily_and_p_5m_real_source_receipt_progress_is_scoped_not_full_admission():
    """An executed producer can progress source coverage without granting rights/PIT/trade."""
    factor, minute = _closed_typed_stock_source_factor()
    actual = reg.build_runtime_trace(factor)
    data = {x["dataset_id"]: x for x in actual["complete_research_universe"]["data_objects"]}
    assert data["P_DAILY"]["state"] == "PARTIAL"
    assert data["P_5M"]["state"] == "PARTIAL"
    assert data["P_DAILY"]["source_identity_hash"] == factor["multi_timeframe_structure_context"]["daily_data_identity_hash"]
    assert data["P_5M"]["source_identity_hash"] == minute.attrs["intraday_data_identity"]["identity_hash"]
    assert data["P_DAILY"]["independent_source_value_admitted"] is False
    assert data["P_5M"]["independent_source_value_admitted"] is False
    assert data["P_5M"]["strategy_upgrade_authority"] is False
    assert "UNITS" in data["P_5M"]["reason"]
    assert data["P_DAILY"]["revision"] == "UNKNOWN"
    assert data["P_DAILY"]["vintage"] == "UNKNOWN"
    assert data["P_5M"]["revision"] == "UNKNOWN"
    assert data["P_5M"]["vintage"] == "UNKNOWN"
    assert data["G_VIX"]["state"] == "UNKNOWN"
    assert data["E_NAV_IOPV"]["state"] == "NOT_APPLICABLE"
    assert factor["multi_timeframe_structure_context"]["timeframes"]["5m"]["strategy_admitted"] is False


def _p_daily_operation_admission(factor):
    source_trace = reg.build_runtime_trace(factor)
    source_row = next(
        row for row in source_trace["complete_research_universe"]["data_objects"]
        if row["dataset_id"] == "P_DAILY"
    )
    operation_id = "op-current-stock-nosend-20261002-r001"
    rights = {
        "product_use": "ALLOWED",
        "storage": "FORBIDDEN",
        "training": "FORBIDDEN",
        "redistribution": "FORBIDDEN",
    }
    rights_evidence = seal_source_rights_evidence({
        "schema_version": "source-rights-evidence-v2",
        "operation_id": operation_id,
        "intended_use": "NO_SEND_RESEARCH",
        "provider_identity": source_row["provider_identity"],
        "upstream_source_owner": "synthetic-licensed-upstream",
        "upstream_rights_state": "VERIFIED",
        "dataset_id": "P_DAILY",
        "consumer_scope": source_row["product_consumer"],
        "terms_uri": "https://example.invalid/terms/current-stock",
        "terms_sha256": "c" * 64,
        "terms_observed_at": "2026-10-01T00:00:00+00:00",
        "terms_scope": "SYNTHETIC_TEST_CONTRACT",
        "product_use_clause": "Synthetic fixture explicitly allows internal no-send research.",
        "product_use_clause_sha256": "c2a9b98537611cb0c9e9cb165ab0a4a699d84f5a2644a5e1e23740d0af55c6e1",
        "rights": rights,
    })
    receipt = seal_data_object_admission_receipt({
        "schema_version": "data-object-admission-receipt-v1",
        "dataset_id": "P_DAILY",
        "provider_identity": source_row["provider_identity"],
        "provider_route": source_row["provider_route"],
        "native_frequency": source_row["native_frequency"],
        "source_identity_hash": source_row["source_identity_hash"],
        "content_sha256": source_row["source_content_sha256"],
        "observed_at": source_row["source_observed_at"],
        "available_at": source_row["source_available_at"],
        "completed_through": source_row["completed_through"],
        "units": source_row["units"],
        "currency": source_row["currency"],
        "adjustment_basis": source_row["adjustment_basis"],
        "revision": source_row["revision"],
        "vintage": source_row["vintage"],
        "rights": rights,
        "rights_evidence": rights_evidence,
        "historical_pit_eligible": False,
        "consumer_scope": source_row["product_consumer"],
    })
    admitted = deepcopy(factor)
    admitted.update({
        "research_as_of": "2026-10-02T01:00:00+00:00",
        "research_operation_id": operation_id,
        "research_intended_use": "NO_SEND_RESEARCH",
        "data_object_admission_receipts": {"P_DAILY": receipt},
    })
    return source_row, receipt, admitted


def test_operation_rights_receipt_must_bind_the_current_registry_source_bytes():
    factor, _ = _closed_typed_stock_source_factor(source_version_known=True)
    source_row, receipt, admitted = _p_daily_operation_admission(factor)
    assert len(source_row["source_identity_hash"]) == 64
    assert len(source_row["source_content_sha256"]) == 64
    assert source_row["revision"] == "SYNTHETIC_FIXTURE_REVISION_1"

    trace = reg.build_runtime_trace(admitted)
    admission = trace["complete_research_universe"]["data_object_admission"]
    row = next(item for item in admission["objects"] if item["dataset_id"] == "P_DAILY")
    assert row["product_admitted"] is True
    assert row["runtime_source_bound"] is True
    assert admission["product_admitted_count"] == 1
    assert admission["learning_admitted_count"] == 0

    other_snapshot = deepcopy(receipt)
    other_snapshot["source_identity_hash"] = "d" * 64
    other_snapshot = seal_data_object_admission_receipt(other_snapshot)
    mismatched = deepcopy(admitted)
    mismatched["data_object_admission_receipts"] = {"P_DAILY": other_snapshot}
    trace = reg.build_runtime_trace(mismatched)
    admission = trace["complete_research_universe"]["data_object_admission"]
    row = next(item for item in admission["objects"] if item["dataset_id"] == "P_DAILY")
    assert row["product_admitted"] is False
    assert "RUNTIME_SOURCE_IDENTITY_MISMATCH" in row["reasons"]
    assert admission["product_admitted_count"] == 0


def test_operation_receipt_cannot_invent_unknown_registry_revision_or_vintage():
    factor, _ = _closed_typed_stock_source_factor()
    source_row, receipt, admitted = _p_daily_operation_admission(factor)
    assert source_row["revision"] == "UNKNOWN"
    assert source_row["vintage"] == "UNKNOWN"
    forged = deepcopy(receipt)
    forged["revision"] = "INVENTED_PROVIDER_REVISION"
    forged["vintage"] = source_row["source_observed_at"]
    forged = seal_data_object_admission_receipt(forged)
    admitted["data_object_admission_receipts"] = {"P_DAILY": forged}

    trace = reg.build_runtime_trace(admitted)
    admission = trace["complete_research_universe"]["data_object_admission"]
    row = next(item for item in admission["objects"] if item["dataset_id"] == "P_DAILY")
    assert row["product_admitted"] is False
    assert "RUNTIME_SOURCE_REVISION_MISMATCH" in row["reasons"]
    assert "RUNTIME_SOURCE_VINTAGE_MISMATCH" in row["reasons"]
    assert admission["product_admitted_count"] == 0

def test_accepted_source_p_daily_p_5m_fail_closed_without_invocation_or_with_wrong_clock():
    """Counterfeit state flags must not be treated as original machine-graph data facts."""
    from copy import deepcopy

    source, minute = _closed_typed_stock_source_factor()
    unbound = deepcopy(source)
    unbound.pop("method_execution_receipts")
    data = {x["dataset_id"]: x for x in reg.build_runtime_trace(unbound)["complete_research_universe"]["data_objects"]}
    assert data["P_DAILY"]["state"] == "UNKNOWN"
    assert data["P_5M"]["state"] == "UNKNOWN"

    stale_receipt = deepcopy(source)
    stale_receipt["multi_timeframe_structure_context"]["timeframes"]["5m"]["completed_source_session_receipt"]["session_count"] = 9
    data = {x["dataset_id"]: x for x in reg.build_runtime_trace(stale_receipt)["complete_research_universe"]["data_objects"]}
    assert data["P_DAILY"]["state"] == "UNKNOWN"
    assert data["P_5M"]["state"] == "UNKNOWN"

    wrong_target = deepcopy(source)
    wrong_target["multi_timeframe_structure_context"]["timeframes"]["daily"]["completed_through"] = "2026-09-24"
    data = {x["dataset_id"]: x for x in reg.build_runtime_trace(wrong_target)["complete_research_universe"]["data_objects"]}
    assert data["P_DAILY"]["state"] == "UNKNOWN"
    assert data["P_5M"]["state"] == "UNKNOWN"

    # Fully typed 5m data with an incomplete session is a legal negative.
    assert len(minute) == 480
    wrong_asset = deepcopy(source)
    wrong_asset["asset_type"] = "etf"
    data = {x["dataset_id"]: x for x in reg.build_runtime_trace(wrong_asset)["complete_research_universe"]["data_objects"]}
    assert data["P_DAILY"]["state"] == "NOT_APPLICABLE"
    assert data["P_5M"]["state"] == "NOT_APPLICABLE"


def test_mtf_execution_exposes_source_bound_supply_and_fail_closed_untyped_cost():
    factor = native_summary_with_mtf()
    trace = factor["evidence_traceability"]
    matrix = trace["timeframe_family_matrix"]
    cells = {(row["timeframe"], row["family"]): row for row in matrix["cells"]}

    assert cells[("weekly", "STRUCTURE")]["state"] == "READY"
    # The 520-day source does not supply 140 native weekly MACD bars or
    # 268 native RSI bars; weekly volume cannot stand in for momentum.
    week = factor["multi_timeframe_structure_context"]["timeframes"]["weekly"]
    assert week["macd_startup_stability"]["status"] == "MISSING"
    assert week["rsi_startup_stability"]["status"] == "MISSING"
    # The weekly method exists, but this fixture intentionally lacks a typed
    # daily source identity. UNKNOWN is the correct fail-closed state.
    assert cells[("weekly", "MOMENTUM")]["state"] == "UNKNOWN"
    assert cells[("weekly", "MOMENTUM")]["reason"] == "CURRENT_METHOD_STATE_UNKNOWN"
    assert cells[("weekly", "TREND_RS")]["state"] == "PARTIAL"
    assert cells[("weekly", "MTF")]["state"] == "READY"
    assert cells[("weekly", "SUPPLY")]["state"] == "READY"
    assert cells[("weekly", "COST")]["state"] == "UNKNOWN"
    assert cells[("weekly", "PATTERN")]["state"] == "UNKNOWN"
    weekly_candle = next(
        row for row in cells[("weekly", "PATTERN")]["method_coverage"]
        if row["method_id"] == "CANDLESTICK"
    )
    assert weekly_candle["reason"] == "TYPED_SOURCE_IDENTITY_MISSING"
    assert cells[("60m", "MTF")]["state"] == "MISSING"
    assert trace["method_window_policy_hash"] == reg.compile_method_window_policy_view()["view_hash"]
    assert trace["timeframe_family_matrix_hash"] == matrix["matrix_hash"]
    assert factor["strategy_eligibility"]["required_evidence"]["thirty_minute_trigger"] == "UNKNOWN"


def test_higher_timeframes_cannot_count_only_volume_as_momentum() -> None:
    """Weekly/monthly SUPPLY may survive while unverified RSI/MACD are missing."""
    from src.services.evidence_traceability_registry import _mtf_method_matrix_state

    for tf in ("weekly", "monthly"):
        row = {
            "status": "PARTIAL",
            "trend": {
                "status": "READY", "volume_status": "量能正常",
                "macd_status": None, "rsi_status": None,
            },
            "macd_startup_stability": {"status": "MISSING"},
            "rsi_startup_stability": {"status": "MISSING"},
            "independent_action_authority": False,
        }
        data = {"multi_timeframe_structure_context": {"timeframes": {tf: row}}}
        supply, _ = _mtf_method_matrix_state(
            "MTF_MOMENTUM_CONTEXT", data, tf, "SUPPLY",
        )
        momentum, _ = _mtf_method_matrix_state(
            "MTF_MOMENTUM_CONTEXT", data, tf, "MOMENTUM",
        )
        assert supply == "PARTIAL"
        assert momentum == "MISSING"
        row["macd_startup_stability"] = {"status": "READY"}
        row["trend"]["macd_status"] = "多头"
        momentum, _ = _mtf_method_matrix_state(
            "MTF_MOMENTUM_CONTEXT", data, tf, "MOMENTUM",
        )
        assert momentum == "PARTIAL"
        row["macd_startup_stability"] = {"status": "MISSING"}
        row["trend"]["macd_status"] = None
        row["rsi_startup_stability"] = {"status": "READY"}
        row["trend"]["rsi_status"] = "中性"
        momentum, _ = _mtf_method_matrix_state(
            "MTF_MOMENTUM_CONTEXT", data, tf, "MOMENTUM",
        )
        assert momentum == "PARTIAL"
        assert row["independent_action_authority"] is False


def test_minute_rsi_raw_status_without_independent_startup_is_not_momentum():
    """RSI has a recursive owner; a naked status must not bypass MTF."""
    from src.services.evidence_traceability_registry import _mtf_method_matrix_state

    frame = {
        "status": "PARTIAL",
        "admitted_methods": ["RSI"],
        "trend": {"status": "READY", "rsi_status": "NEUTRAL"},
        "strategy_admitted": False,
    }
    factor = {"multi_timeframe_structure_context": {"timeframes": {"30m": frame}}}
    state, _ = _mtf_method_matrix_state("MTF_MOMENTUM_CONTEXT", factor, "30m", "MOMENTUM")
    assert state == "MISSING"
    frame["rsi_startup_stability"] = {"status": "UNKNOWN"}
    state, _ = _mtf_method_matrix_state("MTF_MOMENTUM_CONTEXT", factor, "30m", "MOMENTUM")
    assert state == "MISSING"
    frame["rsi_startup_stability"] = {"status": "READY"}
    state, _ = _mtf_method_matrix_state("MTF_MOMENTUM_CONTEXT", factor, "30m", "MOMENTUM")
    assert state == "PARTIAL"
    assert frame["strategy_admitted"] is False


def test_minute_raw_macd_without_recursive_startup_receipt_cannot_enter_momentum_cell():
    """A naked 'golden cross' label is neither a verified minute MACD nor a 30m trigger."""
    from src.services.evidence_traceability_registry import _mtf_method_matrix_state

    factor = {
        "multi_timeframe_structure_context": {
            "timeframes": {
                "30m": {
                    "status": "PARTIAL",
                    "admitted_methods": ["MACD"],
                    "macd_startup_stability": {"status": "UNKNOWN"},
                    "trend": {"status": "READY", "macd_status": "零轴上金叉", "rsi_status": None},
                    "strategy_admitted": False,
                }
            }
        }
    }
    state, _ = _mtf_method_matrix_state("MTF_MOMENTUM_CONTEXT", factor, "30m", "MOMENTUM")
    assert state == "MISSING"
    factor["multi_timeframe_structure_context"]["timeframes"]["30m"][
        "macd_startup_stability"
    ] = {"status": "READY"}
    state, _ = _mtf_method_matrix_state("MTF_MOMENTUM_CONTEXT", factor, "30m", "MOMENTUM")
    assert state == "PARTIAL"
    factor["multi_timeframe_structure_context"]["timeframes"]["30m"][
        "admitted_methods"
    ] = []
    state, _ = _mtf_method_matrix_state("MTF_MOMENTUM_CONTEXT", factor, "30m", "MOMENTUM")
    assert state == "MISSING"


def test_current_intraday_context_keeps_unconfirmed_structure_and_trigger_unadmitted():
    # A valid typed volume unit is required for the existing SUPPLY PARTIAL positive.
    factor = native_summary_with_mtf(intraday=True, volume_unit="share")
    trace = factor["evidence_traceability"]
    cells = {
        (row["timeframe"], row["family"]): row
        for row in trace["timeframe_family_matrix"]["cells"]
    }

    for timeframe in ("60m", "30m", "15m", "5m"):
        assert cells[(timeframe, "TREND_RS")]["state"] == "PARTIAL"
        assert cells[(timeframe, "SUPPLY")]["state"] == "PARTIAL"
        assert cells[(timeframe, "MOMENTUM")]["state"] == "PARTIAL"
        assert cells[(timeframe, "MTF")]["state"] == "PARTIAL"
        assert cells[(timeframe, "COST")]["state"] == "PARTIAL"
        # Four descriptive pivot owners exist; absent confirmation is MISSING,
        # not READY or a strategy trigger.
        assert cells[(timeframe, "STRUCTURE")]["state"] == "MISSING"
        assert cells[(timeframe, "PATTERN")]["state"] == "MISSING"
        regime = cells[(timeframe, "REGIME")]
        assert regime["state"] == "UNKNOWN"
        assert regime["projection_scope"] == "SHARED_ASSET_CONTEXT"
        assert regime["shared_parent_context"] is True
        assert regime["cross_timeframe_vote_counting"] is False
    assert factor["strategy_eligibility"]["required_evidence"]["thirty_minute_trigger"] == "UNKNOWN"



def test_30m_structure_matrix_rejects_forged_ready_without_typed_pivot_identity() -> None:
    """A legacy display label cannot open a 30m evidence family by itself."""
    fake = {
        "multi_timeframe_structure_context": {
            "timeframes": {
                "30m": {
                    "status": "PARTIAL",
                    "completed_bar_only": True,
                    "price_structure": {
                        "status": "READY",
                        "pivots": [{"origin_time": "2026-10-14T13:30:00+08:00",
                                    "confirmed_at": "2026-10-14T14:30:00+08:00",
                                    "provisional": False}],
                    },
                    "admitted_methods": ["MA"],
                },
            },
        },
    }
    matrix = reg.compile_timeframe_family_matrix(fake)
    cell = next(c for c in matrix["cells"] if
                (c["timeframe"], c["family"]) == ("30m", "STRUCTURE"))
    method = next(x for x in cell["method_coverage"]
                  if x["method_id"] == "MTF_PRICE_STRUCTURE")
    assert method["state"] == "UNKNOWN"
    assert method["reason"] == "MTF_30M_STRUCTURE_RECEIPT_UNBOUND"
    assert not method["independent_action_authority"]


def test_native_intraday_unknown_volume_keeps_momentum_but_supply_missing():
    factor = native_summary_with_mtf(intraday=True)
    matrix = factor["evidence_traceability"]["timeframe_family_matrix"]
    cells = {(row["timeframe"], row["family"]): row for row in matrix["cells"]}
    assert len(cells) == 56
    for timeframe in ("60m", "30m", "15m", "5m"):
        frame = factor["multi_timeframe_structure_context"]["timeframes"][timeframe]
        assert frame["status"] == "PARTIAL"
        assert "VOLUME" not in frame["admitted_methods"]
        assert frame["trend"]["volume_status"] is None
        assert cells[(timeframe, "SUPPLY")]["state"] == "UNKNOWN"
        assert cells[(timeframe, "MOMENTUM")]["state"] == "PARTIAL"
        assert cells[(timeframe, "MTF")]["state"] == "PARTIAL"
        assert frame["strategy_admitted"] is False
        assert frame["learning_admitted"] is False


def test_method_window_policy_rejects_deletion_invalid_class_or_overclaim():
    profiles = list(reg.METHOD_WINDOW_PROFILES)

    deleted = [profile for profile in profiles if profile.method_id != "CUP_HANDLE"]
    with pytest.raises(reg.TraceabilityError, match="ACCEPTED_METHOD_WINDOW_SET_MISMATCH"):
        reg.validate_method_window_profiles(deleted)

    invalid_class = list(profiles)
    index = next(i for i, profile in enumerate(invalid_class) if profile.method_id == "CANDLESTICK")
    invalid_class[index] = replace(invalid_class[index], window_classes=("MAGIC_WINDOW",))
    with pytest.raises(reg.TraceabilityError, match="INVALID_METHOD_WINDOW_CLASS"):
        reg.validate_method_window_profiles(invalid_class)

    invalid_scope = list(profiles)
    index = next(i for i, profile in enumerate(invalid_scope) if profile.method_id == "ATR_WILDER")
    invalid_scope[index] = replace(invalid_scope[index], consumer_scope="MAGIC_SCOPE")
    with pytest.raises(reg.TraceabilityError, match="INVALID_METHOD_CONSUMER_SCOPE"):
        reg.validate_method_window_profiles(invalid_scope)

    invalid_family = list(profiles)
    index = next(i for i, profile in enumerate(invalid_family) if profile.method_id == "ATR_WILDER")
    invalid_family[index] = replace(invalid_family[index], family_targets=("MAGIC_FAMILY",))
    with pytest.raises(reg.TraceabilityError, match="INVALID_METHOD_FAMILY_TARGET"):
        reg.validate_method_window_profiles(invalid_family)

    missing_family = list(profiles)
    index = next(i for i, profile in enumerate(missing_family) if profile.method_id == "MACD")
    missing_family[index] = replace(missing_family[index], family_targets=())
    with pytest.raises(reg.TraceabilityError, match="TECHNICAL_METHOD_FAMILY_TARGET_MISSING"):
        reg.validate_method_window_profiles(missing_family)

    orphan = list(profiles)
    index = next(i for i, profile in enumerate(orphan) if profile.method_id == "VALUATION")
    orphan[index] = replace(orphan[index], requirement_id="INVENTED")
    with pytest.raises(reg.TraceabilityError, match="ORPHAN_METHOD_WINDOW_REQUIREMENT"):
        reg.validate_method_window_profiles(orphan)

    missing_ma_execution = list(profiles)
    index = next(i for i, profile in enumerate(missing_ma_execution) if profile.method_id == "MA_COMPRESSION_RELEASE")
    missing_ma_execution[index] = replace(
        missing_ma_execution[index],
        current_execution_timeframes=(),
    )
    with pytest.raises(reg.TraceabilityError, match="CURRENT_EXECUTION_MISSING"):
        reg.validate_method_window_profiles(missing_ma_execution)

    # MACD now has source-bound per-timeframe execution; this is a method
    # capability, not a data-rights, readiness, or strategy grant.
    macd = next(profile for profile in profiles if profile.method_id == "MACD")
    assert macd.current_execution_timeframes == reg.TIMEFRAMES
    assert "MACD" in reg.SOURCE_BOUND_TIMEFRAME_METHOD_IDS

    # Relative Strength now has an explicit seven-timeframe execution seam.
    # This admits only a deterministic consumer: without exact benchmark/peer
    # identities each non-daily leaf remains MISSING, not fabricated READY.
    relative_strength = next(
        profile for profile in profiles if profile.method_id == "RELATIVE_STRENGTH"
    )
    assert relative_strength.current_execution_timeframes == reg.TIMEFRAMES
    assert "RELATIVE_STRENGTH" in reg.SOURCE_BOUND_TIMEFRAME_METHOD_IDS
    forged_rs_outside_target = list(profiles)
    index = next(
        i for i, profile in enumerate(forged_rs_outside_target)
        if profile.method_id == "RELATIVE_STRENGTH"
    )
    forged_rs_outside_target[index] = replace(
        forged_rs_outside_target[index],
        current_execution_timeframes=(*reg.TIMEFRAMES, "asset"),
    )
    with pytest.raises(reg.TraceabilityError, match="CURRENT_EXECUTION_OUTSIDE_TARGET"):
        reg.validate_method_window_profiles(forged_rs_outside_target)

def test_runtime_trace_consumes_method_window_contract(monkeypatch):
    factor = native_summary()
    monkeypatch.setattr(
        reg,
        "METHOD_WINDOW_PROFILES",
        tuple(
            profile
            for profile in reg.METHOD_WINDOW_PROFILES
            if profile.method_id != "CUP_HANDLE"
        ),
    )
    with pytest.raises(reg.TraceabilityError, match="ACCEPTED_METHOD_WINDOW_SET_MISMATCH"):
        reg.build_runtime_trace(factor)


def test_method_window_policy_keeps_asset_only_valuation_as_legal_nontrigger():
    profiles = reg.validate_method_window_profiles()
    valuation = next(profile for profile in profiles if profile.method_id == "VALUATION")
    assert valuation.target_timeframes == ("asset",)
    assert valuation.current_execution_timeframes == ("asset",)
    assert valuation.implementation_state == "EXISTING_REUSED"
    assert valuation.consumer_scope == "ASSET_LAYER"
    assert valuation.family_targets == ()
    assert "VALUATION" in {
        row["method_id"] for row in reg.compile_timeframe_family_matrix(native_summary())["non_matrix_methods"]
    }


def test_score_trace_addition_preserves_independent_main_preimage_when_receipt_absent():
    """Original accepted git function is the golden for old-format traces.

    Read-only test oracle only: do not import old code into production or
    change the accepted machine graph/manifest.
    """
    import ast
    import subprocess

    old_source = subprocess.check_output(
        ["git", "show", "HEAD:src/services/evidence_traceability_registry.py"],
        text=True, encoding="utf-8",
    )
    old_module = ast.parse(old_source)
    original = next(
        node for node in old_module.body
        if isinstance(node, ast.FunctionDef) and node.name == "build_runtime_trace"
    )
    original.name = "_accepted_git_main_runtime_trace_oracle"
    namespace = dict(reg.__dict__)
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=[original], type_ignores=[]),
    ), "<accepted-git-main-runtime-trace>", "exec"), namespace)

    current = native_summary()
    current.pop("score_admission", None)
    previous = namespace["_accepted_git_main_runtime_trace_oracle"](current)
    actual = reg.build_runtime_trace(current)
    assert json.loads(reg.canonical_json(actual)) == json.loads(reg.canonical_json(previous))
    assert "score_admission" not in actual
    assert "composite_score" not in actual
    assert actual["runtime_trace_hash"] == previous["runtime_trace_hash"]


def test_runtime_trace_accepts_exact_json_wire_roundtrip_but_rejects_content_drift():
    factor = native_summary()
    stored = json.loads(reg.canonical_json(factor))
    assert reg.validate_runtime_trace(factor)["runtime_trace_hash"] == factor["evidence_traceability"]["runtime_trace_hash"]
    trace = reg.validate_runtime_trace(stored)
    assert trace["runtime_trace_hash"] == factor["evidence_traceability"]["runtime_trace_hash"]
    assert len(trace["timeframe_family_matrix"]["cells"]) == 56

    mutators = {
        "cell_state_forged": lambda t: t["timeframe_family_matrix"]["cells"][0].update(state="FORGED_READY"),
        "cell_deleted": lambda t: t["timeframe_family_matrix"]["cells"].pop(),
        "cell_order_changed": lambda t: t["timeframe_family_matrix"]["cells"].reverse(),
        "matrix_hash_forged": lambda t: t.update(timeframe_family_matrix_hash="0" * 64),
        "nested_matrix_hash_forged": lambda t: t["timeframe_family_matrix"].update(matrix_hash="0" * 64),
        "runtime_trace_hash_forged": lambda t: t.update(runtime_trace_hash="0" * 64),
        "matrix_missing": lambda t: t.pop("timeframe_family_matrix"),
        "research_universe_missing": lambda t: t.pop("complete_research_universe"),
        "research_universe_hash_forged": lambda t: t.update(complete_research_universe_hash="0" * 64),
        "valuation_requirement_removed": lambda t: t["complete_research_universe"]["requirements"].pop(
            next(i for i, row in enumerate(t["complete_research_universe"]["requirements"]) if row["requirement_id"] == "VALUATION")
        ),
        "global_requirement_removed": lambda t: t["complete_research_universe"]["requirements"].pop(
            next(i for i, row in enumerate(t["complete_research_universe"]["requirements"]) if row["requirement_id"] == "GLOBAL")
        ),
        "chan_method_removed": lambda t: t["complete_research_universe"]["methods"].pop(
            next(i for i, row in enumerate(t["complete_research_universe"]["methods"]) if row["method_id"] == "CHAN")
        ),
        "ma_method_removed": lambda t: t["complete_research_universe"]["methods"].pop(
            next(i for i, row in enumerate(t["complete_research_universe"]["methods"]) if row["method_id"] == "MA_LEVEL_ALIGNMENT_DAILY")
        ),
        "candlestick_method_removed": lambda t: t["complete_research_universe"]["methods"].pop(
            next(i for i, row in enumerate(t["complete_research_universe"]["methods"]) if row["method_id"] == "CANDLESTICK")
        ),
        "method_coverage_removed": lambda t: next(
            cell for cell in t["timeframe_family_matrix"]["cells"] if cell["method_coverage"]
        )["method_coverage"].clear(),
        "observation_removed": lambda t: t["observations"].pop(),
        "manifest_missing": lambda t: t.pop("manifest_hash"),
    }
    for name, mutate in mutators.items():
        forged = deepcopy(stored)
        mutate(forged["evidence_traceability"])
        assert forged != stored, name
        with pytest.raises(reg.TraceabilityError, match="RUNTIME_TRACE"):
            reg.validate_runtime_trace(forged)


def test_native_services_reach_strategy_trace_product_and_learning():
    factor = native_summary()
    assert "strategy_eligibility" in factor
    assert set(factor["strategy_eligibility"]["required_evidence"]) == EXPECTED_REQUIRED
    assert factor["strategy_eligibility"]["required_evidence"]["thirty_minute_trigger"] == "UNKNOWN"
    identity = build_strategy_eligibility_identity(factor["strategy_eligibility"], strategy_id=reg.STOCK_STRATEGY)
    assert identity["strategy_eligibility_state"] in {"UNKNOWN", "INELIGIBLE"}
    trace = reg.validate_runtime_trace(factor)
    assert trace["manifest_hash"] == reg.MANIFEST_HASH
    assert factor["evidence_product_coverage"]["runtime_trace_hash"] == trace["runtime_trace_hash"]
    assert factor["evidence_product_coverage"]["rendered"] is False
    learning = reg.learning_projection(factor)
    expected_cmf = factor["supply_demand_volume_price"]["completed_bar_context"]["close_location_flow"]["cmf_20"]
    assert learning["values"]["daily.cmf20"]["value"] == expected_cmf
    assert learning["values"]["daily.adx"]["value"] is None
    assert learning["values"]["weekly.ma5"]["value"] is None
    assert learning["training_admitted"] is False


def test_display_changes_do_not_rewrite_evidence_or_learning():
    factor = native_summary()
    before = reg.learning_projection(factor)
    factor["investor_brief"] = {"fused_paragraph": "IGNORE ME / 买入 / 99%"}
    factor["conclusion"] = "display only"
    assert reg.learning_projection(factor) == before
    assert "IGNORE ME" not in reg.canonical_json(before)


def test_orphan_evidence_and_stale_trace_fail_before_learning():
    factor = native_summary()
    orphan = deepcopy(factor)
    orphan["invented_evidence"] = {"status": "READY", "value": 99}
    with pytest.raises(reg.TraceabilityError, match="ORPHAN_CANONICAL"):
        reg.learning_projection(orphan)
    factor["supply_demand_volume_price"]["completed_bar_context"]["close_location_flow"]["cmf_20"] = .99
    with pytest.raises(reg.TraceabilityError, match="RUNTIME_TRACE"):
        reg.learning_projection(factor)


def test_a_fake_intraday_ready_flag_cannot_admit_the_deferred_method():
    factor = native_summary()
    factor["multi_timeframe_structure_context"] = {"timeframes": {"30m": {"status": "READY", "trigger": True}}}
    doc = reg.build_strategy_eligibility(factor, schema_version=STRATEGY_ELIGIBILITY_SCHEMA_VERSION)
    assert doc["required_evidence"]["thirty_minute_trigger"] == "UNKNOWN"
    assert doc["state"] != "ELIGIBLE"


def test_hard_veto_is_preserved_despite_indicator_words_or_high_score():
    trend = TrendAnalysisResult(code="600519")
    trend.signal_score = 100
    trend.macd_status = MACDStatus.GOLDEN_CROSS_ZERO
    trend.macd_signal = "强烈买入信号"
    factor = build_stock_factor_decision_summary(
        trend, include_canonical=True,
        daily_market_context={"market_light": {"status": "red", "data_quality": "ok"}},
    )
    assert factor["canonical_decision"]["hard_veto"] is True
    assert factor["canonical_decision"]["action"] == "PASS"
    assert factor["strategy_eligibility"]["required_evidence"]["market_regime_permission"] == "FAILED"
    assert "强烈买入信号" not in reg.canonical_json(factor)


def test_native_pattern_dedup_reuses_one_same_swing():
    result = _dedupe_patterns([
        {"pattern_type": "CONTRACTION_BASE", "subtype": "VCP", "correlation_group": "same-swing"},
        {"pattern_type": "CONTRACTION_BASE", "subtype": "FLAT_BASE", "correlation_group": "same-swing"},
    ])
    assert len(result) == 1
    assert result[0]["subtype"] == "VCP"


def test_future_bars_do_not_change_native_prior_evidence():
    frame = bars(200)
    target = frame["date"].iloc[159]
    baseline = native_summary(frame.iloc[:160].copy(), target)
    perturbed = frame.copy()
    perturbed.loc[160:, ["open", "high", "low", "close"]] *= 4
    actual = native_summary(perturbed, target)
    assert reg.learning_projection(actual) == reg.learning_projection(baseline)
    assert actual["evidence_traceability"] == baseline["evidence_traceability"]


def test_insufficient_warmup_never_creates_momentum_readiness():
    frame = bars(15)
    payload = build_volatility_momentum_context(stock_code="600519", history=frame, target_date=frame["date"].iloc[-1])
    assert payload["status"] == "MISSING"
    assert payload["reason"] == "WARMUP_INSUFFICIENT"


def test_macd_real_cross_is_descriptive_and_formula_source_unchanged():
    analyzer = StockTrendAnalyzer()
    frame = bars(30)
    frame["MACD_DIF"] = 1.0
    frame["MACD_DEA"] = 1.1
    frame["MACD_BAR"] = -.2
    frame.loc[29, ["MACD_DIF", "MACD_DEA", "MACD_BAR"]] = [1.2, 1.1, .2]
    result = TrendAnalysisResult(code="600519")
    analyzer._analyze_macd(frame, result)
    assert result.macd_status == MACDStatus.GOLDEN_CROSS_ZERO
    assert "不是独立买点" in result.macd_signal
    assert "强烈买入" not in result.macd_signal
    old = subprocess.check_output(["git", "show", "HEAD:src/stock_analyzer.py"], cwd=ROOT).decode("utf-8")
    new = (ROOT / "src/stock_analyzer.py").read_text(encoding="utf-8")
    for name in ("_calculate_macd", "_calculate_rsi", "_generate_signal"):
        def function_ast(text):
            return ast.dump(next(n for n in ast.walk(ast.parse(text)) if isinstance(n, ast.FunctionDef) and n.name == name), include_attributes=False)
        assert function_ast(old) == function_ast(new), name


def test_recursive_seed_sensitivity_is_measured_not_claimed_invariant():
    frame = bars(240)
    analyzer = StockTrendAnalyzer()
    long = analyzer._calculate_macd(frame.copy())["MACD_DIF"].iloc[-1]
    short = analyzer._calculate_macd(frame.tail(40).copy())["MACD_DIF"].iloc[-1]
    assert np.isfinite(long) and np.isfinite(short)
    assert abs(long - short) > 0  # An EMA depends on initialization; do not fake prefix invariance here.


@pytest.mark.parametrize("mutation", ["version", "config", "warmup", "source", "completed"])
def test_forged_ready_is_not_consumed_by_learning_or_product(mutation):
    from src.services.v2_5_evidence_coverage import compile_product_coverage
    factor = native_summary()
    context = factor["volatility_momentum_evidence"]["context"]
    if mutation == "version":
        context["algorithm_version"] = "old-or-unapproved"
    elif mutation == "config":
        context["config_hash"] = "0" * 64
    elif mutation == "warmup":
        context["observations"] = 1
    elif mutation == "source":
        context["source_alignment"]["status"] = "UNPROVEN"
    else:
        context["completed_bar_only"] = False
    context["status"] = "READY"
    factor["evidence_traceability"] = reg.build_runtime_trace(factor)
    trace = reg.validate_runtime_trace(factor)
    observed = next(x for x in trace["observations"] if x["requirement_id"] == "MOMENTUM")
    assert observed["state"] == "UNKNOWN"
    assert reg.learning_projection(factor)["values"]["daily.macd_dif"]["value"] is None
    slots = {x["slot_id"]: x for x in compile_product_coverage(factor)["slots"]}
    assert slots["detail.timeframe.momentum_divergence"]["state"] == "DATA_INSUFFICIENT"


def test_wrong_supply_version_is_not_a_strategy_confirmation():
    factor = native_summary()
    context = factor["supply_demand_volume_price"]["completed_bar_context"]
    context.update(status="READY", state="DEMAND_PRESSURE", schema_version="unapproved")
    doc = reg.build_strategy_eligibility(factor, schema_version=STRATEGY_ELIGIBILITY_SCHEMA_VERSION)
    assert doc["required_evidence"]["volume_price_confirmation"] == "UNKNOWN"


def test_macd_insufficient_history_does_not_turn_into_neutral_fact():
    result = TrendAnalysisResult(code="600519")
    result.macd_signal = "数据不足"
    assert reg.describe_macd_state(result) == ""




def test_candlestick_strict_receipt_binds_nested_leaf_without_strategy_or_learning_admission():
    factor = native_summary()
    binding = next(item for item in reg.EVIDENCE_BINDINGS if item.requirement_id == "CANDLESTICK")
    value = reg.at(factor, binding.path)
    assert isinstance(value, dict)
    target = pd.to_datetime(value["target_date"]).date()
    identity = _strict_daily_identity(target)
    receipt = reg.build_method_execution_receipt(
        "CANDLESTICK",
        output=value,
        asset_route="STOCK",
        stock_code="600519",
        market="cn",
        target_date=target,
        timeframe="daily",
        input_identity=identity,
    )
    state, reason = reg.method_observation(binding, value, receipt=receipt, require_receipt=True)
    assert state in {"READY", "PARTIAL"}
    assert reason == "METHOD_INVOCATION_VERIFIED"

    missing_state, missing_reason = reg.method_observation(binding, value, receipt=None, require_receipt=True)
    assert missing_state == "UNKNOWN"
    assert missing_reason == "METHOD_INVOCATION_RECEIPT_MISSING"

    wrong = deepcopy(receipt)
    wrong["output_hash"] = "0" * 64
    wrong = _rehash_receipt(wrong)
    wrong_state, wrong_reason = reg.method_observation(binding, value, receipt=wrong, require_receipt=True)
    assert wrong_state == "UNKNOWN"
    assert wrong_reason == "METHOD_RECEIPT_OUTPUT_MISMATCH"


def test_strict_supply_receipt_binds_actual_target_input_producer_and_output():
    factor = native_summary()
    context = factor["supply_demand_volume_price"]["completed_bar_context"]
    target = pd.to_datetime(context["target_date"]).date()
    identity = _strict_daily_identity(target)
    receipt = reg.build_method_execution_receipt(
        "SUPPLY",
        output=context,
        asset_route="STOCK",
        stock_code="600519",
        market="cn",
        target_date=target,
        timeframe="daily",
        input_identity=identity,
    )
    factor["method_execution_receipt_policy"] = "REQUIRED"
    factor["method_execution_receipts"] = {"SUPPLY": receipt}
    trace = reg.build_runtime_trace(factor)
    supply = next(item for item in trace["observations"] if item["requirement_id"] == "SUPPLY")
    assert supply["state"] == "READY"
    assert supply["reason"] == "METHOD_INVOCATION_VERIFIED"

    wrong_target = reg.build_method_execution_receipt(
        "SUPPLY",
        output=context,
        asset_route="STOCK",
        stock_code="600519",
        market="cn",
        target_date="2099-01-01",
        timeframe="daily",
        input_identity=identity,
    )
    factor["method_execution_receipts"]["SUPPLY"] = wrong_target
    trace = reg.build_runtime_trace(factor)
    supply = next(item for item in trace["observations"] if item["requirement_id"] == "SUPPLY")
    assert supply["state"] == "UNKNOWN"
    assert "TARGET" in supply["reason"]

    wrong_output = deepcopy(receipt)
    wrong_output["output_hash"] = "0" * 64
    factor["method_execution_receipts"]["SUPPLY"] = _rehash_receipt(wrong_output)
    trace = reg.build_runtime_trace(factor)
    supply = next(item for item in trace["observations"] if item["requirement_id"] == "SUPPLY")
    assert supply["state"] == "UNKNOWN"
    assert supply["reason"] == "METHOD_RECEIPT_OUTPUT_MISMATCH"

    wrong_source = deepcopy(receipt)
    wrong_source["producer"]["source_sha256"] = "0" * 64
    factor["method_execution_receipts"]["SUPPLY"] = _rehash_receipt(wrong_source)
    trace = reg.build_runtime_trace(factor)
    supply = next(item for item in trace["observations"] if item["requirement_id"] == "SUPPLY")
    assert supply["state"] == "UNKNOWN"
    assert supply["reason"] == "METHOD_RECEIPT_PRODUCER_MISMATCH"

    for identity_mutation, reason in (
        ({"adjustment_basis": None}, "METHOD_PRICE_IDENTITY_UNPROVEN"),
        ({"data_snapshot_identity": None}, "METHOD_INPUT_IDENTITY_MISSING"),
        ({"stock_code": "000001"}, "METHOD_INPUT_STOCK_CODE_MISMATCH"),
        ({"market": "us"}, "METHOD_INPUT_MARKET_MISMATCH"),
    ):
        bad_identity = {**identity, **identity_mutation}
        bad_receipt = reg.build_method_execution_receipt(
            "SUPPLY",
            output=context,
            asset_route="STOCK",
            stock_code="600519",
            market="cn",
            target_date=target,
            timeframe="daily",
            input_identity=bad_identity,
        )
        factor["method_execution_receipts"]["SUPPLY"] = bad_receipt
        trace = reg.build_runtime_trace(factor)
        supply = next(item for item in trace["observations"] if item["requirement_id"] == "SUPPLY")
        assert supply["state"] == "UNKNOWN"
        assert supply["reason"] == reason


def test_existing_cost_context_compiles_typed_p_cost_proxy_without_product_inflation():
    frame = bars(320)
    target = frame["date"].iloc[-1]
    trend = StockTrendAnalyzer().analyze(frame.copy(), "600519")
    cost = build_cost_structure_context(
        stock_code="600519", history=frame, chip_data=None,
        target_date=target, market="cn",
    )
    identity = {
        **_strict_daily_identity(target),
        "snapshot_observed_at": "2026-10-30T10:00:00+00:00",
        "available_at_max": "2026-10-30T10:00:00+00:00",
        "daily_data_identity": {
            "provider_route": "unit.daily.provider",
            "currency": "CNY",
            "volume_unit": "share",
            "amount_unit": "CNY",
            "content_sha256": "c" * 64,
            "revision": "UNKNOWN",
            "vintage": "UNKNOWN",
        },
    }
    receipt = reg.build_method_execution_receipt(
        "COST", output=cost, asset_route="STOCK", stock_code="600519",
        market="cn", target_date=target, timeframe="daily",
        input_identity=identity,
    )
    factor = build_stock_factor_decision_summary(
        trend,
        cost_structure_context=cost,
        method_execution_receipts={"COST": receipt},
        include_canonical=True,
    )
    trace = reg.validate_runtime_trace(factor)
    objects = {
        row["dataset_id"]: row
        for row in trace["complete_research_universe"]["data_objects"]
    }
    row = objects["P_COST_PROXY"]
    assert row["state"] == "READY"
    assert row["provider_identity"] == "synthetic"
    assert row["provider_route"] == "unit.daily.provider"
    assert row["producer_authority"].endswith("cost_structure_service.py::build_cost_structure_context")
    assert row["independent_source_value_admitted"] is True
    assert row["strategy_upgrade_authority"] is False
    assert row["units"]["rolling_reference_price"] == "CNY_PER_LISTED_UNIT"
    assert row["facts"]["window_20"]["rolling_reference_price"] > 0
    assert row["facts"]["window_60"]["rolling_reference_price"] > 0
    assert len(row["source_fact_hash"]) == len(row["source_observation_hash"]) == 64
    gate = trace["complete_research_universe"]["full_domain_data_gate"]
    assert gate["current_product_admitted_count"] == 0
    assert "P_COST_PROXY" in gate["unresolved_product_dataset_ids"]

    forged = deepcopy(factor)
    forged.pop("evidence_traceability", None)
    forged["cost_structure_evidence"]["context"]["bar_reference_cost"][
        "window_20"
    ]["rolling_reference_price"] += 1.0
    forged_trace = reg.build_runtime_trace(forged)
    forged_row = next(
        row for row in forged_trace["complete_research_universe"]["data_objects"]
        if row["dataset_id"] == "P_COST_PROXY"
    )
    assert forged_row["state"] == "UNKNOWN"
    assert forged_row["independent_source_value_admitted"] is False


def test_existing_etf_peer_rs_compiles_typed_e_peer_rs_without_product_inflation():
    from src.services.relative_strength_service import (
        compute_etf_peer_relative_strength_context,
    )
    from tests.test_relative_strength_service import _history, _with_identity

    target_history = _with_identity(
        _history(periods=70, start_price=100.0, end_price=140.0),
        provider="Fetcher",
    )
    peer_histories = {
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
    target_date = target_history.iloc[-1]["date"].date()
    peer_rs = compute_etf_peer_relative_strength_context(
        etf_code="510300", market="cn", target_history=target_history,
        peer_histories=peer_histories, target_date=target_date,
        horizon_sessions=60, min_peer_count=3,
    )
    assert peer_rs["status"] == "READY"
    trend = TrendAnalysisResult(code="510300")
    factor = build_stock_factor_decision_summary(
        trend,
        relative_strength_context=peer_rs,
        method_execution_receipts={},
        include_canonical=True,
        asset_type="etf",
    )
    trace = reg.validate_runtime_trace(factor)
    objects = {
        row["dataset_id"]: row
        for row in trace["complete_research_universe"]["data_objects"]
    }
    row = objects["E_PEER_RS"]
    assert row["state"] == "READY"
    assert row["provider_identity"] == "Fetcher"
    assert row["provider_route"] == "synthetic-test"
    assert row["producer_authority"].endswith(
        "relative_strength_service.py::compute_etf_peer_relative_strength_context"
    )
    assert row["independent_source_value_admitted"] is True
    assert row["strategy_upgrade_authority"] is False
    assert row["facts"]["relative"]["rank"] == 1
    assert row["facts"]["peer_universe"]["eligible_peer_count"] == 3
    assert len(row["facts"]["peer_universe"]["universe_hash"]) == 64
    assert trace["complete_research_universe"]["full_domain_data_gate"][
        "current_product_admitted_count"
    ] == 0

    forged = deepcopy(factor)
    forged.pop("evidence_traceability", None)
    forged["trend_relative_strength"]["relative_strength"]["peer_universe"][
        "universe_hash"
    ] = "0" * 64
    forged_trace = reg.build_runtime_trace(forged)
    forged_row = next(
        row for row in forged_trace["complete_research_universe"]["data_objects"]
        if row["dataset_id"] == "E_PEER_RS"
    )
    assert forged_row["state"] == "UNKNOWN"
    assert forged_row["independent_source_value_admitted"] is False


@pytest.mark.parametrize("mutation", ["schema", "stock", "benchmark", "source", "basis", "identity"])
def test_strict_trend_rs_rejects_forged_upstream_semantics(mutation):
    trend = TrendAnalysisResult(code="600519")
    target = "2026-09-30"
    identity = {
        "stock": {
            **_strict_daily_identity(pd.Timestamp(target).date()),
            "stock_code": "600519",
        },
        "benchmark": {
            **_strict_daily_identity(pd.Timestamp(target).date(), stock_code="510300"),
            "stock_code": "510300",
        },
    }
    rs = {
        "schema_version": "relative-strength-v1",
        "family": "trend_relative_strength",
        "status": "READY",
        "market": "cn",
        "stock_code": "600519",
        "target_date": target,
        "benchmark": {"code": "510300", "source": "synthetic"},
        "stock": {},
        "relative": {"state": "OUTPERFORMING"},
        "data_quality": {
            "source_alignment": "MATCHED",
            "stock_endpoint_sources": ["synthetic"],
        },
        "input_identity": deepcopy(identity),
    }
    if mutation == "schema":
        rs["schema_version"] = "forged"
    elif mutation == "stock":
        rs["stock_code"] = "000001"
    elif mutation == "benchmark":
        rs["benchmark"]["code"] = "159919"
    elif mutation == "source":
        rs["data_quality"]["source_alignment"] = "UNPROVEN"
    elif mutation == "basis":
        rs["input_identity"]["benchmark"]["adjustment_basis"] = "raw"
    else:
        rs.pop("input_identity")

    factor = build_stock_factor_decision_summary(
        trend,
        relative_strength_context=rs,
        method_execution_receipts={},
        include_canonical=True,
    )
    trace = reg.validate_runtime_trace(factor)
    observed = next(item for item in trace["observations"] if item["requirement_id"] == "TREND_RS")
    assert observed["state"] == "UNKNOWN"


def test_regime_parent_date_mismatch_is_unknown_under_strict_receipts():
    trend = TrendAnalysisResult(code="600519")
    factor = build_stock_factor_decision_summary(
        trend,
        daily_market_context={
            "trade_date": "2026-09-29",
            "region": "cn",
            "market_light": {"status": "green", "data_quality": "ok"},
        },
        market_structure_context={
            "status": "ok",
            "market": "cn",
            "trade_date": "2026-09-30",
            "stock_market_position": {},
        },
        method_execution_receipts={},
        include_canonical=True,
    )
    trace = reg.validate_runtime_trace(factor)
    observed = next(item for item in trace["observations"] if item["requirement_id"] == "REGIME")
    assert observed["state"] == "UNKNOWN"
    assert observed["reason"] == "REGIME_UPSTREAM_DATE_MISMATCH"


def test_ma60_is_missing_before_sixty_rows_and_exact_at_sixty():
    analyzer = StockTrendAnalyzer()
    for observations in (30, 59):
        calculated = analyzer._calculate_mas(bars(observations))
        assert pd.isna(calculated["MA60"].iloc[-1])
        assert pd.notna(calculated["MA20"].iloc[-1])
    calculated = analyzer._calculate_mas(bars(60))
    assert calculated["MA60"].iloc[-1] == pytest.approx(calculated["close"].mean())


def test_learning_metric_economic_domains_reject_invalid_values():
    factor = native_summary()
    factor["volatility_momentum_evidence"]["context"]["momentum"]["rsi"]["rsi_6"] = 1000.0
    factor["evidence_traceability"] = reg.build_runtime_trace(factor)
    assert reg.learning_projection(factor)["values"]["daily.rsi6"]["value"] is None

    factor = native_summary()
    factor["volatility_momentum_evidence"]["context"]["volatility"]["realized_volatility_20d_annualized_pct"] = -12.0
    factor["evidence_traceability"] = reg.build_runtime_trace(factor)
    assert reg.learning_projection(factor)["values"]["daily.realized_volatility20"]["value"] is None

    metrics = {metric["id"]: metric for metric in reg.METRIC_BINDINGS}
    rsi_metric = metrics["daily.rsi6"]
    volatility_metric = metrics["daily.realized_volatility20"]
    macd_metric = metrics["daily.macd_dif"]
    price_metric = metrics["daily.price"]
    cost_metric = metrics["daily.cost20_proxy"]
    ma_metric = metrics["weekly.ma5"]

    assert reg._metric_value_valid(rsi_metric, float("nan")) is False
    assert reg._metric_value_valid(rsi_metric, float("inf")) is False
    assert reg._metric_value_valid(volatility_metric, -0.01) is False
    assert reg._metric_value_valid(rsi_metric, -0.1) is False
    assert reg._metric_value_valid(rsi_metric, 0.0) is True
    assert reg._metric_value_valid(rsi_metric, 100.0) is True
    assert reg._metric_value_valid(rsi_metric, 100.1) is False

    for invalid in (float("nan"), float("inf"), True, False):
        assert reg._metric_value_valid(macd_metric, invalid) is False
    for positive_only_metric in (price_metric, cost_metric, ma_metric):
        assert reg._metric_value_valid(positive_only_metric, 0.0) is False
        assert reg._metric_value_valid(positive_only_metric, 1.0) is True


def test_short_180_bar_native_momentum_keeps_roc_but_no_recursive_feature_ready():
    """Native producer retains lawful fixed-window evidence and refuses RSI."""
    frame = bars(180)
    factor = native_summary(frame=frame)
    item = factor["volatility_momentum_evidence"]["context"]
    assert item["status"] == "PARTIAL"
    assert item["momentum"]["roc_20_pct"] is not None
    assert item["volatility"]["true_range_sma_20_pct"] is not None
    assert item["momentum"]["macd"]["admission"]["status"] == "READY"
    assert item["momentum"]["rsi"]["admission"]["status"] != "READY"
    assert item["momentum"]["rsi"]["rsi_12"] is None
    assert item["volatility"]["atr_wilder_admission"]["status"] == "READY"
    assert item["independent_action_authority"] is False
    values = reg.learning_projection(factor)["values"]
    assert values["daily.macd_dif"]["state"] == "MISSING_OR_UNADMITTED"
    assert values["daily.rsi12"]["state"] == "MISSING_OR_UNADMITTED"


@pytest.mark.parametrize(
    ("dif", "dea", "bar"),
    [
        (-0.27126150004790617, -0.29420148919316635, 0.04587997829052037),
        (0.0, 0.0, 0.0),
        (0.27126150004790617, 0.29420148919316635, -0.04587997829052037),
    ],
)
def test_learning_projection_preserves_finite_signed_macd_values(dif, dea, bar):
    factor = native_summary()
    macd = factor["volatility_momentum_evidence"]["context"]["momentum"]["macd"]
    macd.update(dif=dif, dea=dea, bar=bar)
    factor["evidence_traceability"] = reg.build_runtime_trace(factor)

    values = reg.learning_projection(factor)["values"]
    assert values["daily.macd_dif"] == {"value": pytest.approx(dif), "state": "READY"}
    assert values["daily.macd_dea"] == {"value": pytest.approx(dea), "state": "READY"}
    assert values["daily.macd_bar"] == {"value": pytest.approx(bar), "state": "READY"}

    for metric_id in ("daily.macd_dif", "daily.macd_dea", "daily.macd_bar"):
        metric = next(item for item in reg.METRIC_BINDINGS if item["id"] == metric_id)
        assert metric["unit"] == "PRICE_BASIS_CURRENCY"


def test_strict_mtf_parent_rejection_blocks_all_dependent_children_and_product_slots():
    from src.services.v2_5_evidence_coverage import compile_product_coverage

    factor = native_summary_with_mtf()
    factor["method_execution_receipt_policy"] = "REQUIRED"
    factor["method_execution_receipts"] = {}
    factor["evidence_traceability"] = reg.build_runtime_trace(factor)
    trace = reg.validate_runtime_trace(factor)

    parent = next(
        item for item in trace["observations"] if item["requirement_id"] == "MTF"
    )
    assert parent["state"] == "UNKNOWN"
    assert parent["reason"] == "METHOD_INVOCATION_RECEIPT_MISSING"

    weekly_cells = [
        cell
        for cell in trace["timeframe_family_matrix"]["cells"]
        if cell["timeframe"] == "weekly"
    ]
    assert len(weekly_cells) == len(reg.TECHNICAL_EVIDENCE_FAMILIES)
    assert all(cell["state"] not in {"READY", "PARTIAL"} for cell in weekly_cells)
    assert all(not cell["available_paths"] for cell in weekly_cells)
    mtf_children = [
        method
        for cell in weekly_cells
        for method in cell["method_coverage"]
        if method["requirement_id"] == "MTF"
        and method["leaf_implementation_state"] == "EXISTING_REUSED"
        and "weekly" in method["current_execution_timeframes"]
    ]
    assert mtf_children
    assert all(child["state"] == "UNKNOWN" for child in mtf_children)
    assert all(
        child["reason"] == "METHOD_INVOCATION_RECEIPT_MISSING"
        for child in mtf_children
    )

    coverage = compile_product_coverage(factor)
    assert all(
        slot["timeframe_states"].get("weekly") != "EVIDENCE_AVAILABLE"
        for slot in coverage["slots"]
    )
    learning = reg.learning_projection(factor)
    for metric_id in ("weekly.ma5", "weekly.ma10", "weekly.ma20"):
        assert learning["values"][metric_id] == {
            "value": None,
            "state": "MISSING_OR_UNADMITTED",
        }


def test_verified_mtf_parent_receipt_preserves_legal_child_and_product_nontrigger():
    from src.services.v2_5_evidence_coverage import compile_product_coverage

    factor = native_summary_with_mtf()
    context = factor["multi_timeframe_structure_context"]
    target = pd.to_datetime(context["target_date"]).date()
    identity = _strict_daily_identity(target)
    identity["data_snapshot_identity"] = context["data_snapshot_identity"]
    receipt = reg.build_method_execution_receipt(
        "MTF",
        output=context,
        asset_route="STOCK",
        stock_code="600519",
        market="cn",
        target_date=target,
        timeframe="multi",
        input_identity=identity,
    )
    factor["method_execution_receipt_policy"] = "REQUIRED"
    factor["method_execution_receipts"] = {"MTF": receipt}
    factor["evidence_traceability"] = reg.build_runtime_trace(factor)
    trace = reg.validate_runtime_trace(factor)

    parent = next(
        item for item in trace["observations"] if item["requirement_id"] == "MTF"
    )
    assert parent["state"] in {"READY", "PARTIAL"}
    assert parent["reason"] == "METHOD_INVOCATION_VERIFIED"
    weekly_trend = next(
        cell
        for cell in trace["timeframe_family_matrix"]["cells"]
        if cell["timeframe"] == "weekly" and cell["family"] == "TREND_RS"
    )
    assert weekly_trend["state"] in {"READY", "PARTIAL"}
    assert weekly_trend["available_paths"]

    coverage = compile_product_coverage(factor)
    trend_slot = next(
        slot for slot in coverage["slots"] if slot["slot_id"] == "detail.timeframe.trend_ma"
    )
    assert trend_slot["timeframe_states"]["weekly"] == "EVIDENCE_AVAILABLE"
    learning = reg.learning_projection(factor)
    for metric_id in ("weekly.ma5", "weekly.ma10", "weekly.ma20"):
        assert learning["values"][metric_id]["state"] == "READY"
        assert learning["values"][metric_id]["value"] is not None


def test_invalid_mtf_parent_receipt_blocks_dependent_product_and_learning():
    from src.services.v2_5_evidence_coverage import compile_product_coverage

    factor = native_summary_with_mtf()
    context = factor["multi_timeframe_structure_context"]
    target = pd.to_datetime(context["target_date"]).date()
    identity = _strict_daily_identity(target)
    identity["data_snapshot_identity"] = context["data_snapshot_identity"]
    receipt = reg.build_method_execution_receipt(
        "MTF",
        output=context,
        asset_route="STOCK",
        stock_code="600519",
        market="cn",
        target_date=target,
        timeframe="multi",
        input_identity=identity,
    )
    receipt["output_hash"] = "0" * 64
    receipt = _rehash_receipt(receipt)
    factor["method_execution_receipt_policy"] = "REQUIRED"
    factor["method_execution_receipts"] = {"MTF": receipt}
    factor["evidence_traceability"] = reg.build_runtime_trace(factor)
    trace = reg.validate_runtime_trace(factor)

    parent = next(
        item for item in trace["observations"] if item["requirement_id"] == "MTF"
    )
    assert parent["state"] == "UNKNOWN"
    assert parent["reason"] == "METHOD_RECEIPT_OUTPUT_MISMATCH"
    weekly_cells = [
        cell
        for cell in trace["timeframe_family_matrix"]["cells"]
        if cell["timeframe"] == "weekly"
    ]
    assert all(cell["state"] not in {"READY", "PARTIAL"} for cell in weekly_cells)
    assert all(not cell["available_paths"] for cell in weekly_cells)

    coverage = compile_product_coverage(factor)
    assert all(
        slot["timeframe_states"].get("weekly") != "EVIDENCE_AVAILABLE"
        for slot in coverage["slots"]
    )
    learning = reg.learning_projection(factor)
    for metric_id in ("weekly.ma5", "weekly.ma10", "weekly.ma20"):
        assert learning["values"][metric_id] == {
            "value": None,
            "state": "MISSING_OR_UNADMITTED",
        }


def test_typed_daily_identity_survives_all_price_owner_views_without_row_label():
    from datetime import datetime, timezone
    from data_provider.daily_data_identity import (
        attach_daily_data_identity,
        build_daily_data_identity,
    )

    frame = bars(320).drop(columns=["data_source"]).copy()
    frame["amount"] = frame["close"] * frame["volume"]
    target = frame["date"].iloc[-1]
    observed = datetime(2026, 10, 25, tzinfo=timezone.utc)
    identity = build_daily_data_identity(
        frame,
        provider_identity="BaostockFetcher",
        provider_route="baostock.query_history_k_data_plus",
        actual_response_branch="fixture:typed-no-row-source;adjustflag=2",
        requested_adjustment_basis="qfq",
        observed_adjustment_basis="qfq",
        basis_evidence="independent_typed_fixture",
        requested_start=str(frame["date"].iloc[0]),
        requested_end=str(target),
        currency="CNY",
        volume_unit="share",
        amount_unit="CNY",
        identity_state="OBSERVED",
        observed_at=observed,
        instrument="600519",
        revision="typed-fixture-r1",
        vintage=observed.isoformat(),
    )
    attach_daily_data_identity(frame, identity)

    factor = native_summary(frame=frame, target=target)
    supply = factor["supply_demand_volume_price"]["completed_bar_context"]
    cost = factor["cost_structure_evidence"]["context"]
    structure = factor["price_structure_evidence"]["context"]
    momentum = factor["volatility_momentum_evidence"]["context"]
    pattern = factor["pattern_trigger_evidence"]["context"]

    assert supply["status"] == "READY"
    assert supply["data_quality"]["source_alignment"] == "SINGLE_SOURCE"
    assert cost["status"] == "READY"
    assert cost["bar_reference_cost"]["window_60"][
        "source_alignment"
    ] == "SINGLE_SOURCE"
    assert structure["status"] == "READY"
    assert structure["source_alignment"]["status"] == "SINGLE_SOURCE"
    assert momentum["status"] == "READY"
    assert momentum["source_alignment"]["status"] == "SINGLE_SOURCE"
    assert pattern["status"] == "READY"
    assert pattern["history_source_alignment"]["status"] == "SINGLE_SOURCE"
    assert pattern["candlestick"]["status"] == "READY"
    assert pattern["candlestick"]["reason"] in {
        "NO_MATERIAL_CANDLE_EVENT",
        "MATERIAL_CANDLE_EVENT_OBSERVED",
    }

    tampered = frame.copy()
    tampered.loc[tampered.index[-1], "volume"] += 1.0
    tampered_factor = native_summary(frame=tampered, target=target)
    assert tampered_factor["price_structure_evidence"]["context"][
        "source_alignment"
    ]["status"] == "UNPROVEN"


def test_ready_partial_technical_methods_prefer_bound_timeframe_carrier():
    factor = native_summary_with_mtf()
    trace = reg.build_runtime_trace(factor)
    admitted = [
        row
        for cell in trace["timeframe_family_matrix"]["cells"]
        if cell["runtime_binding"]["runtime_source_bound"] is True
        for row in cell["method_coverage"]
        if row["state"] in {"READY", "PARTIAL"}
    ]

    assert admitted
    assert all(
        row["runtime_source_binding"]["runtime_source_bound"] is True
        for row in admitted
    )
    assert all(
        row["runtime_source_binding"]["binding_scope"]
        == "TIMEFRAME_CARRIER"
        for row in admitted
    )


def test_etf_daily_ma_is_independent_from_missing_peer_rs_only():
    factor = native_summary_with_mtf()
    factor["asset_type"] = "etf"
    factor["trend_relative_strength"]["relative_strength"] = {
        "status": "MISSING",
        "reason": "ETF_PEER_RS_INPUT_MISSING",
    }
    mtf = factor["multi_timeframe_structure_context"]
    factor["method_execution_receipt_policy"] = "REQUIRED"
    factor["method_execution_receipts"] = {
        "TREND_RS": reg.build_method_execution_receipt(
            "TREND_RS",
            output=factor["trend_relative_strength"],
            asset_route="ETF",
            stock_code=mtf["stock_code"],
            market="cn",
            target_date=mtf["target_date"],
            timeframe="daily",
            input_identity=None,
        ),
    }

    trace = reg.build_runtime_trace(factor)
    cell = next(
        row for row in trace["timeframe_family_matrix"]["cells"]
        if row["timeframe"] == "daily" and row["family"] == "TREND_RS"
    )
    methods = {row["method_id"]: row for row in cell["method_coverage"]}
    assert methods["RELATIVE_STRENGTH"]["state"] == "UNKNOWN"
    assert methods["MA_LEVEL_ALIGNMENT_DAILY"]["state"] == "READY"
    assert methods["MA_LEVEL_ALIGNMENT_DAILY"]["reason"] == (
        "DAILY_MA_SOURCE_BOUND_RS_BENCHMARK_INDEPENDENT"
    )
    assert methods["MA_LEVEL_ALIGNMENT_DAILY"]["runtime_source_binding"][
        "runtime_source_bound"
    ] is True

    forged = deepcopy(factor)
    receipt = deepcopy(forged["method_execution_receipts"]["TREND_RS"])
    receipt["output_hash"] = "0" * 64
    forged["method_execution_receipts"]["TREND_RS"] = _rehash_receipt(
        receipt
    )
    forged_trace = reg.build_runtime_trace(forged)
    forged_cell = next(
        row for row in forged_trace["timeframe_family_matrix"]["cells"]
        if row["timeframe"] == "daily" and row["family"] == "TREND_RS"
    )
    forged_methods = {
        row["method_id"]: row for row in forged_cell["method_coverage"]
    }
    assert forged_methods["MA_LEVEL_ALIGNMENT_DAILY"]["state"] not in {
        "READY", "PARTIAL",
    }

    unbound = deepcopy(factor)
    unbound["multi_timeframe_structure_context"]["timeframes"]["daily"][
        "source_alignment"
    ] = {"status": "UNKNOWN", "sources": []}
    unbound_trace = reg.build_runtime_trace(unbound)
    unbound_cell = next(
        row for row in unbound_trace["timeframe_family_matrix"]["cells"]
        if row["timeframe"] == "daily" and row["family"] == "TREND_RS"
    )
    unbound_methods = {
        row["method_id"]: row for row in unbound_cell["method_coverage"]
    }
    assert unbound_methods["MA_LEVEL_ALIGNMENT_DAILY"]["state"] not in {
        "READY", "PARTIAL",
    }


def test_noncanonical_legacy_call_does_not_trigger_new_product_or_learning():
    result = TrendAnalysisResult(code="600519")
    factor = build_stock_factor_decision_summary(result)
    assert "canonical_decision" not in factor
    assert "evidence_traceability" not in factor
    assert "evidence_product_coverage" not in factor
