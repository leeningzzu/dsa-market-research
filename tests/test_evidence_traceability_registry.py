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
from src.services.data_object_admission import (
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


def native_summary_with_mtf(*, intraday=False, volume_unit="UNKNOWN"):
    frame = bars(520)
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
    assert methods["MACD"]["current_execution_timeframes"] == ("daily",)
    assert methods["MACD"]["target_timeframes"] == reg.TIMEFRAMES
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
    assert methods["CUP_HANDLE"]["current_execution_timeframes"] == ("daily",)
    assert methods["CANDLESTICK"]["leaf_implementation_state"] == "EXISTING_REUSED"
    assert methods["CANDLESTICK"]["canonical_path"] == "pattern_trigger_evidence.context.candlestick"
    assert methods["CANDLESTICK"]["current_execution_timeframes"] == ("daily",)
    assert set(methods["CANDLESTICK"]["window_classes"]) == {"ADAPTIVE_CONTEXT", "EVENT_ANCHORED"}
    assert set(methods["ADX_DMI"]["window_classes"]) == {"FIXED_ROLLING", "RECURSIVE_WARMUP"}
    assert set(methods["KDJ"]["window_classes"]) == {"FIXED_ROLLING", "RECURSIVE_WARMUP"}
    assert methods["ATR_WILDER"]["window_classes"] == ("RECURSIVE_WARMUP",)
    assert methods["ATR_WILDER"]["requirement_id"] == "MOMENTUM"
    assert methods["ATR_WILDER"]["leaf_implementation_state"] == "EXISTING_REUSED"
    assert methods["ATR_WILDER"]["current_execution_timeframes"] == ("daily",)
    assert methods["ATR_WILDER"]["canonical_path"] == "volatility_momentum_evidence"
    assert methods["CHAN"]["window_classes"] == ("INCREMENTAL_STATE_MACHINE",)
    assert methods["CHAN"]["current_execution_timeframes"] == ()
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
        "config_hash": "b6b2cfe28159ba691dcc6f7c42479210286d6cfb362c58b5b577bd772f735c25",
    }
    pre_intraday_manifest = deepcopy(current_manifest)
    pre_intraday_manifest["method_contracts"]["MTF"] = {
        "version": "completed-daily-resample-v1",
        "warmup": 26,
        "config_hash": "5ea82ff077b04d411d4f62b41800d07ebbd7a547726db2481e3aa07db61c5b0f",
    }
    # Explicit manifest-version refreeze for accepted source-bound Quality/Valuation
    # owners; historical pre-admission digests remain in Git. V2.5 is unchanged.
    assert reg.digest(pre_intraday_manifest) == "93755e701f5be6d2262ec5cdd8413fe4f232f9ae71e85778f86e4c6f672c338f"
    assert reg.MANIFEST_HASH == "72626f853407bac7ac7a500707aa40b6c89b307f944c39a303b5601f15fbd64e"
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
    assert weekly_states["ATR_WILDER"] == "NOT_ADMITTED"
    assert cells[("daily", "REGIME")]["state"] == "NOT_APPLICABLE"

    matrix_method_ids = {
        item["method_id"]
        for cell in matrix["cells"]
        for item in cell["method_coverage"]
    }
    non_matrix_ids = {item["method_id"] for item in matrix["non_matrix_methods"]}
    assert matrix_method_ids | non_matrix_ids == EXPECTED_METHOD_WINDOW_IDS
    assert {item["method_id"] for item in matrix["non_matrix_methods"]} >= {
        "MARKET_SECTOR_REGIME", "QUALITY", "VALUATION", "DISTRIBUTION", "RISK_REWARD",
        "CHAN", "WAVE", "ETF_SPECIFIC", "GLOBAL", "BREADTH", "PROBABILITY",
    }
    assert len(matrix["matrix_hash"]) == 64


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
        date(2026, 9, 25),
    ]
    minute = _canonical_session_set(dates)
    frame = _history(330)
    frame["date"] = pd.bdate_range(end="2026-09-25", periods=len(frame))
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
        requested_start="2025-01-01", requested_end="2026-09-25",
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
        date(2026, 9, 25),
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
    frame["date"] = pd.bdate_range(end="2026-09-25", periods=len(frame))
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
        requested_end="2026-09-25",
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
        "schema_version": "source-rights-evidence-v1",
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


def test_mtf_execution_populates_only_its_admitted_family_context_and_not_supply_or_cost():
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
    assert cells[("weekly", "MOMENTUM")]["state"] == "MISSING"
    assert cells[("weekly", "TREND_RS")]["state"] == "PARTIAL"
    assert cells[("weekly", "MTF")]["state"] == "READY"
    assert cells[("weekly", "SUPPLY")]["state"] == "PARTIAL"
    assert cells[("weekly", "COST")]["state"] == "NOT_ADMITTED"
    assert cells[("weekly", "PATTERN")]["state"] == "NOT_ADMITTED"
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
        assert cells[(timeframe, "COST")]["state"] == "NOT_ADMITTED"
        # Four descriptive pivot owners exist; absent confirmation is MISSING,
        # not READY or a strategy trigger.
        assert cells[(timeframe, "STRUCTURE")]["state"] == "MISSING"
        assert cells[(timeframe, "PATTERN")]["state"] == "NOT_ADMITTED"
        assert cells[(timeframe, "REGIME")]["state"] == "NOT_APPLICABLE"
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
        assert cells[(timeframe, "SUPPLY")]["state"] == "MISSING"
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

    forged_macd_all_timeframes = list(profiles)
    index = next(i for i, profile in enumerate(forged_macd_all_timeframes) if profile.method_id == "MACD")
    forged_macd_all_timeframes[index] = replace(
        forged_macd_all_timeframes[index],
        current_execution_timeframes=reg.TIMEFRAMES,
    )
    with pytest.raises(reg.TraceabilityError, match="CURRENT_EXECUTION_INTRADAY_GATE_DEFERRED"):
        reg.validate_method_window_profiles(forged_macd_all_timeframes)


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
