# -*- coding: utf-8 -*-
"""Closed-world bindings over DSA owners; never an indicator or trading engine.

The registry describes capability. A trace observes outputs, not invocation or
provider entitlement. Deferred methods cannot be enabled by a payload flag.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from functools import lru_cache
from hashlib import sha256
import ast
import json
import math
from numbers import Real
from pathlib import Path
from typing import Any

MANIFEST_VERSION = "evidence-product-traceability-v1"
TRACE_VERSION = "canonical-evidence-trace-v1"
METHOD_EXECUTION_RECEIPT_VERSION = "method-execution-receipt-v1"
STOCK_STRATEGY = "stock_trend_quality_pullback_v1"
BASELINE_SHA256 = "039ca6394baf9cf39494cc29f512802b114c8227f8c965723197a8df4b9de823"
BASELINE_BYTES = 125919
TIMEFRAMES = ("monthly", "weekly", "daily", "60m", "30m", "15m", "5m")
STATES = frozenset({"READY", "PARTIAL", "MISSING", "UNKNOWN", "NOT_APPLICABLE"})
METHOD_WINDOW_POLICY_VERSION = "method-window-policy-view-v1"
METHOD_WINDOW_CLASSES = frozenset({
    "FIXED_ROLLING",
    "RECURSIVE_WARMUP",
    "ADAPTIVE_CONTEXT",
    "VARIABLE_STRUCTURE",
    "EVENT_ANCHORED",
    "CROSS_SECTIONAL_ASOF",
    "VINTAGE_ASOF",
    "INCREMENTAL_STATE_MACHINE",
})
METHOD_WINDOW_STATES = frozenset({
    "EXISTING_REUSED",
    "DESIGN_BOUND",
    "DEFERRED_WITH_OWNER_AND_REENTRY",
})
METHOD_WINDOW_TIMEFRAMES = frozenset({"asset", *TIMEFRAMES})
NON_DAILY_TECHNICAL_TIMEFRAMES = ("monthly", "weekly", "60m", "30m", "15m", "5m")

VALID_ASSET_ROUTES = frozenset({"STOCK", "ETF", "MARKET"})
VALID_BINDING_TIMEFRAMES = frozenset({"asset", "daily", "multi", *TIMEFRAMES})
VALID_METRIC_TIMEFRAMES = frozenset({"daily", "weekly", "monthly"})
VALID_METRIC_UNITS = frozenset({
    "PRICE_BASIS_CURRENCY", "PRICE_BASIS_CURRENCY_PROXY", "ratio", "pct",
    "annualized_pct", "index_0_100", "METHOD_NOT_ADMITTED",
})

# This accepted contract is intentionally independent from the candidate tuples
# under validation. A self-consistent deletion therefore cannot certify itself.
ACCEPTED_REQUIREMENT_ROLES = frozenset({
    ("REGIME", "ADMITTED"), ("TREND_RS", "ADMITTED"), ("SUPPLY", "ADMITTED"),
    ("COST", "ADMITTED"), ("STRUCTURE", "ADMITTED"), ("MOMENTUM", "ADMITTED"),
    ("PATTERN", "ADMITTED"), ("MTF", "ADMITTED"),
    ("QUALITY", "DEFERRED"), ("VALUATION", "DEFERRED"), ("DISTRIBUTION", "DEFERRED"),
    ("RISK_REWARD", "DEFERRED"), ("CANDLESTICK", "DEFERRED"),
    ("EXTRA_INDICATORS", "DEFERRED"), ("AVWAP_PROFILE", "DEFERRED"),
    ("CHAN", "DEFERRED"), ("WAVE", "DEFERRED"), ("ETF_SPECIFIC", "DEFERRED"),
    ("GLOBAL", "DEFERRED"), ("BREADTH", "DEFERRED"), ("PROBABILITY", "DEFERRED"),
    ("INTRADAY_60m", "DEFERRED"), ("INTRADAY_30m", "DEFERRED"),
    ("INTRADAY_15m", "DEFERRED"), ("INTRADAY_5m", "DEFERRED"),
    ("DECISION", "DECISION"),
})


class TraceabilityError(ValueError):
    """A binding or claimed observation exceeds its actual proof."""


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def at(value: Any, path: str) -> Any:
    for key in path.split("."):
        if not isinstance(value, Mapping) or key not in value:
            return None
        value = value[key]
    return value


@dataclass(frozen=True)
class EvidenceBinding:
    requirement_id: str
    path: str
    owner: str
    callable_name: str
    fields: tuple[str, ...]
    version_ref: str
    warmup_ref: str
    correlation_group: str
    timeframe: str = "daily"
    asset_routes: tuple[str, ...] = ("STOCK", "ETF", "MARKET")
    implementation_state: str = "EXISTING_REUSED"
    reentry: str = "CURRENT_OWNER_VERSION_AND_OUTPUT_VALIDATION"
    ledger: bool = True


@dataclass(frozen=True)
class MethodWindowProfile:
    """Leaf-method design view bound to, but not part of, the runtime manifest hash."""

    method_id: str
    requirement_id: str
    window_classes: tuple[str, ...]
    target_timeframes: tuple[str, ...]
    current_execution_timeframes: tuple[str, ...] = ()
    implementation_state: str = "DEFERRED_WITH_OWNER_AND_REENTRY"


# References name existing formula/config owners; no copied thresholds or formulas.
EVIDENCE_BINDINGS = (
    EvidenceBinding("REGIME", "market_sector_regime", "src/services/factor_decision_summary.py", "_build_market_sector_regime_evidence", ("market_light", "stock_market_position"), "_MARKET_SECTOR_REGIME_VERSION", "UPSTREAM_REGIME_READINESS", "market_sector", "asset"),
    EvidenceBinding("TREND_RS", "trend_relative_strength", "src/services/factor_decision_summary.py", "_build_trend_relative_strength_evidence", ("date", "close", "data_source", "benchmark_close"), "_TREND_RELATIVE_STRENGTH_VERSION", "RELATIVE_STRENGTH_AND_TREND_OWNERS", "trend_price"),
    EvidenceBinding("SUPPLY", "supply_demand_volume_price", "src/services/supply_demand_service.py", "build_supply_demand_context", ("date", "high", "low", "close", "volume", "data_source"), "SUPPLY_DEMAND_SCHEMA_VERSION", "REQUIRED_OBSERVATIONS", "volume_pressure"),
    EvidenceBinding("COST", "cost_structure_evidence", "src/services/cost_structure_service.py", "build_cost_structure_context", ("date", "high", "low", "close", "volume", "data_source"), "COST_STRUCTURE_SCHEMA_VERSION", "REFERENCE_WINDOWS", "price_volume_cost"),
    EvidenceBinding("STRUCTURE", "price_structure_evidence", "src/services/price_structure_service.py", "build_price_structure_context", ("date", "open", "high", "low", "close", "data_source"), "PIVOT_ALGORITHM_VERSION", "MIN_OBSERVATIONS", "confirmed_price_swing"),
    EvidenceBinding("MOMENTUM", "volatility_momentum_evidence", "src/services/volatility_momentum_service.py", "build_volatility_momentum_context", ("date", "open", "high", "low", "close", "data_source"), "ALGORITHM_VERSION", "READY_OBSERVATIONS", "same_swing_momentum"),
    EvidenceBinding("PATTERN", "pattern_trigger_evidence", "src/services/pattern_trigger_service.py", "build_pattern_trigger_context", ("date", "open", "high", "low", "close", "volume", "data_source"), "ALGORITHM_VERSION", "MIN_OBSERVATIONS", "price_contraction"),
    EvidenceBinding("MTF", "multi_timeframe_structure_context", "src/services/multi_timeframe_structure_service.py", "build_multi_timeframe_structure_context", ("date", "open", "high", "low", "close", "volume", "data_source"), "ALGORITHM_VERSION", "TREND_MIN_BARS", "nested_price_structure", "multi"),
)

DECISION_BINDING = EvidenceBinding(
    "DECISION", "canonical_decision", "src/services/factor_decision_summary.py", "_canonical_decision",
    ("canonical_evidence",), "_CANONICAL_AUTHORITY", "STRATEGY_REQUIRED_EVIDENCE", "decision", "asset", ledger=False)

# A producer gap is an executable UNKNOWN, not a fake READY stub.
DEFERRED_BINDINGS = tuple(
    EvidenceBinding(rid, path, owner, "NOT_ADMITTED", fields, "NOT_ADMITTED", "NOT_ADMITTED", group,
                    timeframe=tf, implementation_state="DEFERRED_WITH_OWNER_AND_REENTRY", reentry=reentry, ledger=False)
    for rid, path, owner, fields, group, tf, reentry in (
        ("QUALITY", "quality_evidence", "FACTOR:fundamental_quality", ("published_at", "financial_version", "cash_flow"), "fundamentals", "asset", "PIT_QUALITY_DATA_AND_METHOD_ADMISSION"),
        ("VALUATION", "valuation_evidence", "FACTOR:valuation", ("published_at", "financial_version", "share_count", "valuation_assumptions"), "valuation", "asset", "BUSINESS_ROUTED_VALUATION_METHOD_ADMISSION"),
        ("DISTRIBUTION", "distribution_risk_evidence", "FACTOR:distribution_risk", ("date", "open", "high", "low", "close", "volume"), "volume_pressure", "daily", "DETERMINISTIC_DISTRIBUTION_CLEAR_RESOLVER"),
        ("RISK_REWARD", "risk_reward_evidence", "FACTOR:execution", ("entry", "stop", "cost", "execution_identity"), "execution", "asset", "ACCEPTED_RISK_REWARD_RESOLVER"),
        ("CANDLESTICK", "candlestick_evidence", "FACTOR:PatternTrigger", ("date", "open", "high", "low", "close", "volume"), "confirmed_price_swing", "daily", "CANONICAL_GEOMETRY_LOCATION_LIFECYCLE"),
        ("EXTRA_INDICATORS", "extended_indicator_evidence", "FACTOR:VolatilityMomentum", ("date", "open", "high", "low", "close", "volume"), "same_swing_momentum", "daily", "ADX_DMI_BOLLINGER_OBV_ADL_MFI_KDJ_INDEPENDENT_ADMISSION"),
        ("AVWAP_PROFILE", "anchored_cost_evidence", "FACTOR:CostStructure", ("anchor", "price_volume_distribution"), "price_volume_cost", "asset", "ANCHOR_AND_PRICE_VOLUME_METHOD_NOT_DAILY_PROXY"),
        ("CHAN", "chan_evidence", "FACTOR:ChanFeatureAdapter", ("date", "open", "high", "low", "close"), "confirmed_price_swing", "multi", "NARROW_ADAPTER_BAR_REPLAY_NO_BACKFILL"),
        ("WAVE", "wave_evidence", "FACTOR:WaveShadow", ("date", "open", "high", "low", "close"), "confirmed_price_swing", "multi", "RESEARCH_SHADOW_ONLY"),
        ("ETF_SPECIFIC", "etf_specific_evidence", "FACTOR:ETF", ("nav", "nav_at", "shares", "bid", "ask", "tracking_error"), "etf_execution", "asset", "ETF_DATA_IDENTITY_AND_METHOD_ADMISSION"),
        ("GLOBAL", "global_evidence", "FACTOR:MarketGlobal", ("published_at", "currency", "macro_series_version"), "market_global", "asset", "GLOBAL_SOURCE_AND_DATE_ADMISSION"),
        ("BREADTH", "market_breadth_evidence", "FACTOR:MarketBreadth", ("universe_as_of", "constituents", "close"), "market_breadth", "asset", "HISTORICAL_UNIVERSE_AND_COVERAGE"),
        ("PROBABILITY", "calibrated_probability_evidence", "FACTOR:Calibration", ("dataset_hash", "model_hash", "calibrator_hash", "promotion_id"), "model", "asset", "OUTCOME_PIT_OOT_CALIBRATION_SHADOW_PROMOTION"),
    )
) + tuple(
    EvidenceBinding("INTRADAY_" + tf, "multi_timeframe_structure_context.timeframes." + tf,
                    "src/services/multi_timeframe_structure_service.py", "NOT_ADMITTED",
                    ("bar_end", "open", "high", "low", "close", "volume", "session", "available_at"),
                    "NOT_ADMITTED", "NOT_ADMITTED", "nested_price_structure", timeframe=tf,
                    implementation_state="DEFERRED_WITH_OWNER_AND_REENTRY",
                    reentry="ONE_COMPLETED_5M_SOURCE_AND_SESSION_AGGREGATION", ledger=False)
    for tf in ("60m", "30m", "15m", "5m")
)


# Independent accepted leaf-method set. Candidate tuples below cannot certify their
# own deletion; this is the same fail-closed pattern as ACCEPTED_REQUIREMENT_ROLES.
ACCEPTED_METHOD_WINDOW_IDS = frozenset({
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
})

METHOD_WINDOW_PROFILES = (
    MethodWindowProfile(
        "MARKET_SECTOR_REGIME", "REGIME",
        ("CROSS_SECTIONAL_ASOF", "VINTAGE_ASOF"), ("asset",), ("asset",),
        "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "RELATIVE_STRENGTH", "TREND_RS",
        ("FIXED_ROLLING", "CROSS_SECTIONAL_ASOF"), TIMEFRAMES, ("daily",),
        "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "MA_LEVEL_ALIGNMENT_DAILY", "TREND_RS",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "SUPPLY_RELATIVE_VOLUME", "SUPPLY",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "SUPPLY_DIRECTIONAL_VOLUME", "SUPPLY",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "SUPPLY_CMF", "SUPPLY",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "COST_ROLLING_REFERENCE", "COST",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "PRICE_PIVOT_SWING", "STRUCTURE",
        ("VARIABLE_STRUCTURE",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "BREAKOUT_RETEST_FAILED", "STRUCTURE",
        ("VARIABLE_STRUCTURE", "EVENT_ANCHORED"), TIMEFRAMES, ("daily",),
        "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "MACD", "MOMENTUM",
        ("RECURSIVE_WARMUP",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "RSI", "MOMENTUM",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "ROC", "MOMENTUM",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "VOLATILITY_TR_SMA", "MOMENTUM",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "CONFIRMED_DIVERGENCE", "MOMENTUM",
        ("VARIABLE_STRUCTURE",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "CUP_HANDLE", "PATTERN",
        ("VARIABLE_STRUCTURE",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "DOUBLE_BOTTOM", "PATTERN",
        ("VARIABLE_STRUCTURE",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "VCP", "PATTERN",
        ("VARIABLE_STRUCTURE",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "FLAT_BASE", "PATTERN",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "TIGHT_CONSOLIDATION", "PATTERN",
        ("FIXED_ROLLING",), TIMEFRAMES, ("daily",), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "MTF_MA_LEVEL_ALIGNMENT", "MTF",
        ("FIXED_ROLLING",), NON_DAILY_TECHNICAL_TIMEFRAMES,
        ("monthly", "weekly"), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "MTF_MOMENTUM_CONTEXT", "MTF",
        ("FIXED_ROLLING", "RECURSIVE_WARMUP"), NON_DAILY_TECHNICAL_TIMEFRAMES,
        ("monthly", "weekly"), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "MTF_PRICE_STRUCTURE", "MTF",
        ("VARIABLE_STRUCTURE",), NON_DAILY_TECHNICAL_TIMEFRAMES,
        ("monthly", "weekly"), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "MA_SLOPE_CROSS", "MTF",
        ("FIXED_ROLLING",), TIMEFRAMES, ("monthly", "weekly", "daily"),
        "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "MA_COMPRESSION_RELEASE", "MTF",
        ("FIXED_ROLLING", "ADAPTIVE_CONTEXT", "INCREMENTAL_STATE_MACHINE"),
        TIMEFRAMES, ("monthly", "weekly", "daily"), "EXISTING_REUSED",
    ),
    MethodWindowProfile(
        "QUALITY", "QUALITY",
        ("VINTAGE_ASOF",), ("asset",), (), "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "VALUATION", "VALUATION",
        ("VINTAGE_ASOF",), ("asset",), (), "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "DISTRIBUTION", "DISTRIBUTION",
        ("ADAPTIVE_CONTEXT", "INCREMENTAL_STATE_MACHINE"), TIMEFRAMES, (),
        "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "RISK_REWARD", "RISK_REWARD",
        ("EVENT_ANCHORED",), ("asset",), (), "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "CANDLESTICK", "CANDLESTICK",
        ("ADAPTIVE_CONTEXT",), TIMEFRAMES, (), "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "ATR_WILDER", "EXTRA_INDICATORS",
        ("RECURSIVE_WARMUP",), TIMEFRAMES, (), "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "ADX_DMI", "EXTRA_INDICATORS",
        ("FIXED_ROLLING", "RECURSIVE_WARMUP"), TIMEFRAMES, (),
        "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "BOLLINGER", "EXTRA_INDICATORS",
        ("FIXED_ROLLING",), TIMEFRAMES, (), "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "KDJ", "EXTRA_INDICATORS",
        ("FIXED_ROLLING", "RECURSIVE_WARMUP"), TIMEFRAMES, (),
        "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "OBV_ADL", "EXTRA_INDICATORS",
        ("RECURSIVE_WARMUP",), TIMEFRAMES, (), "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "MFI", "EXTRA_INDICATORS",
        ("FIXED_ROLLING",), TIMEFRAMES, (), "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "VWAP", "AVWAP_PROFILE",
        ("EVENT_ANCHORED",), ("daily", "60m", "30m", "15m", "5m"), (),
        "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "AVWAP_VOLUME_PROFILE", "AVWAP_PROFILE",
        ("EVENT_ANCHORED",), TIMEFRAMES, (), "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "CHAN", "CHAN",
        ("INCREMENTAL_STATE_MACHINE",), TIMEFRAMES, (),
        "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "WAVE", "WAVE",
        ("VARIABLE_STRUCTURE", "INCREMENTAL_STATE_MACHINE"), TIMEFRAMES, (),
        "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "ETF_SPECIFIC", "ETF_SPECIFIC",
        ("FIXED_ROLLING", "CROSS_SECTIONAL_ASOF", "VINTAGE_ASOF"), ("asset",), (),
        "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "GLOBAL", "GLOBAL",
        ("FIXED_ROLLING", "VINTAGE_ASOF"), ("asset",), (),
        "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "BREADTH", "BREADTH",
        ("FIXED_ROLLING", "CROSS_SECTIONAL_ASOF"), ("asset",), (),
        "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
    MethodWindowProfile(
        "PROBABILITY", "PROBABILITY",
        ("VINTAGE_ASOF",), ("asset",), (), "DEFERRED_WITH_OWNER_AND_REENTRY",
    ),
)


@dataclass(frozen=True)
class StrategyBinding:
    clause: str
    classification: str
    requirement_id: str
    resolver: str


STRATEGY_BINDINGS = (
    StrategyBinding("market_regime_permission", "HARD_ELIGIBILITY", "REGIME", "market"),
    StrategyBinding("sector_industry_strength", "HARD_ELIGIBILITY", "REGIME", "sector"),
    StrategyBinding("leader_preference", "SELECTION_PRIOR", "REGIME", "not_used"),
    StrategyBinding("quality", "HARD_ELIGIBILITY", "QUALITY", "deferred"),
    StrategyBinding("valuation", "HARD_ELIGIBILITY", "VALUATION", "deferred"),
    StrategyBinding("monthly_trend_structure_when_ready", "CONTEXT_WHEN_READY", "MTF", "not_used"),
    StrategyBinding("weekly_trend_structure", "HARD_ELIGIBILITY", "MTF", "weekly"),
    StrategyBinding("daily_trend_structure", "HARD_ELIGIBILITY", "MTF", "daily"),
    StrategyBinding("daily_pullback_or_supply_contraction", "HARD_ELIGIBILITY", "SUPPLY", "pullback"),
    StrategyBinding("volume_price_confirmation", "HARD_ELIGIBILITY", "SUPPLY", "volume"),
    StrategyBinding("distribution_risk_clear", "HARD_ELIGIBILITY", "DISTRIBUTION", "deferred"),
    StrategyBinding("thirty_minute_trigger", "HARD_ELIGIBILITY", "INTRADAY_30m", "deferred"),
    StrategyBinding("risk_reward", "HARD_ELIGIBILITY", "RISK_REWARD", "deferred"),
)


def strategy_contract_coverage() -> tuple:
    return tuple((b.clause, b.classification, b.clause if b.classification == "HARD_ELIGIBILITY" else None)
                 for b in STRATEGY_BINDINGS)


def ledger_evidence_keys() -> tuple[str, ...]:
    # Existing v1 audit schema preserved; the registry owns this set from now on.
    return ("strategy_id", "contract_version", "composite_score", "canonical_decision") + tuple(
        b.path for b in EVIDENCE_BINDINGS if b.ledger)


# Leaf identities are also the numeric/state learning allowlist. No paragraph is a feature.
# Every weekly/monthly path names that actual timeframe; daily values never fill it.
METRIC_BINDINGS = tuple(
    {"id": name, "requirement_id": rid, "path": path, "kind": "number", "unit": unit, "timeframe": tf}
    for name, rid, path, unit, tf in (
        ("daily.price", "STRUCTURE", "price_structure_evidence.context.current_close", "PRICE_BASIS_CURRENCY", "daily"),
        ("daily.volume_ratio20", "SUPPLY", "supply_demand_volume_price.completed_bar_context.relative_volume.volume_ratio_20d", "ratio", "daily"),
        ("daily.directional_volume", "SUPPLY", "supply_demand_volume_price.completed_bar_context.directional_volume.signed_volume_balance", "ratio", "daily"),
        ("daily.cmf20", "SUPPLY", "supply_demand_volume_price.completed_bar_context.close_location_flow.cmf_20", "ratio", "daily"),
        ("daily.cost20_proxy", "COST", "cost_structure_evidence.context.bar_reference_cost.window_20.rolling_reference_price", "PRICE_BASIS_CURRENCY_PROXY", "daily"),
        ("daily.cost60_proxy", "COST", "cost_structure_evidence.context.bar_reference_cost.window_60.rolling_reference_price", "PRICE_BASIS_CURRENCY_PROXY", "daily"),
        ("daily.macd_dif", "MOMENTUM", "volatility_momentum_evidence.context.momentum.macd.dif", "PRICE_BASIS_CURRENCY", "daily"),
        ("daily.macd_dea", "MOMENTUM", "volatility_momentum_evidence.context.momentum.macd.dea", "PRICE_BASIS_CURRENCY", "daily"),
        ("daily.macd_bar", "MOMENTUM", "volatility_momentum_evidence.context.momentum.macd.bar", "PRICE_BASIS_CURRENCY", "daily"),
        ("daily.rsi6", "MOMENTUM", "volatility_momentum_evidence.context.momentum.rsi.rsi_6", "index_0_100", "daily"),
        ("daily.rsi12", "MOMENTUM", "volatility_momentum_evidence.context.momentum.rsi.rsi_12", "index_0_100", "daily"),
        ("daily.rsi24", "MOMENTUM", "volatility_momentum_evidence.context.momentum.rsi.rsi_24", "index_0_100", "daily"),
        ("daily.roc20", "MOMENTUM", "volatility_momentum_evidence.context.momentum.roc_20_pct", "pct", "daily"),
        ("daily.roc60", "MOMENTUM", "volatility_momentum_evidence.context.momentum.roc_60_pct", "pct", "daily"),
        ("daily.realized_volatility20", "MOMENTUM", "volatility_momentum_evidence.context.volatility.realized_volatility_20d_annualized_pct", "annualized_pct", "daily"),
        ("daily.tr_sma20_not_wilder_atr", "MOMENTUM", "volatility_momentum_evidence.context.volatility.true_range_sma_20_pct", "pct", "daily"),
    )
) + tuple(
    {"id": tf + "." + ma, "requirement_id": "MTF", "path": "multi_timeframe_structure_context.timeframes." + tf + ".trend." + ma,
     "kind": "number", "unit": "PRICE_BASIS_CURRENCY", "timeframe": tf}
    for tf in ("monthly", "weekly") for ma in ("ma5", "ma10", "ma20")
) + tuple(
    {"id": "daily." + name, "requirement_id": "EXTRA_INDICATORS", "path": "extended_indicator_evidence." + name,
     "kind": "number", "unit": "METHOD_NOT_ADMITTED", "timeframe": "daily"}
    for name in ("adx", "plus_di", "minus_di", "bollinger_upper", "bollinger_lower", "kdj_k", "kdj_d", "kdj_j", "obv", "adl", "mfi")
)


def load_slot_map(document=None) -> dict:
    if document is None:
        path = Path(__file__).resolve().parents[2] / "templates/v2_5/STOCK_DSA_V2_5_EVIDENCE_SLOT_MAP_V1.json"
        document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict) or document.get("schema_version") != "v25-evidence-slot-map-v1":
        raise TraceabilityError("SLOT_SCHEMA_MISMATCH")
    if (document.get("baseline_sha256") != BASELINE_SHA256 or document.get("baseline_bytes") != BASELINE_BYTES
            or document.get("rendering_authorized") is not False or document.get("authority") != "SEMANTIC_SIDECAR_ONLY"):
        raise TraceabilityError("SLOT_AUTHORITY_MISMATCH")
    if tuple(document.get("timeframes", ())) != TIMEFRAMES or set(document.get("envelopes", {})) != {"MARKET", "AUTO", "WATCHLIST"}:
        raise TraceabilityError("SLOT_ROUTE_MISMATCH")
    bindings = {b.requirement_id: b for b in EVIDENCE_BINDINGS + DEFERRED_BINDINGS + (DECISION_BINDING,)}
    slots = document.get("slots")
    if not isinstance(slots, list) or not slots:
        raise TraceabilityError("SLOT_LIST_EMPTY")
    ids = []
    for slot in slots:
        if not isinstance(slot, dict) or not slot.get("id") or not slot.get("requirements") or not slot.get("paths") or not slot.get("location_class"):
            raise TraceabilityError("SLOT_BINDING_INCOMPLETE")
        ids.append(slot["id"])
        if any(rid not in bindings for rid in slot["requirements"]):
            raise TraceabilityError("ORPHAN_PRODUCT_REQUIREMENT")
        owners = [bindings[rid].path for rid in slot["requirements"]]
        if any(not any(p == owner or p.startswith(owner + ".") for owner in owners) for p in slot["paths"]):
            raise TraceabilityError("ORPHAN_PRODUCT_PATH")
    if len(set(ids)) != len(ids):
        raise TraceabilityError("DUPLICATE_PRODUCT_SLOT")
    return document


@lru_cache(maxsize=16)
def _owner_literals(owner: str) -> dict:
    """Read named constants without importing provider-dependent modules.

    Source/config versions are process-frozen, like imported Python code. An
    edited candidate must be validated in a new process, not hot reloaded.
    Only literal values and an already-bound constant plus a literal are read;
    calls, attributes and arbitrary expressions are never executed.
    """
    source = (Path(__file__).resolve().parents[2] / owner).read_text(encoding="utf-8-sig")
    values = {}

    class BoundLiterals(ast.NodeTransformer):
        def visit_Name(self, node):
            return ast.parse(repr(values[node.id]), mode="eval").body if node.id in values else node

    for node in ast.parse(source).body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name):
            continue
        expression = BoundLiterals().visit(node.value)
        try:
            if isinstance(expression, ast.BinOp) and isinstance(expression.op, ast.Add):
                left, right = ast.literal_eval(expression.left), ast.literal_eval(expression.right)
                if type(left) is not int or type(right) is not int:
                    continue
                value = left + right
            else:
                value = ast.literal_eval(expression)
            values[node.targets[0].id] = value
        except (ValueError, TypeError, SyntaxError):
            continue
    return values


def method_contract(binding: EvidenceBinding) -> dict:
    values = _owner_literals(binding.owner)
    config = values.get("_CONFIG")
    return {"version": values.get(binding.version_ref), "warmup": values.get(binding.warmup_ref),
            "config_hash": digest(config) if isinstance(config, dict) else None}


def manifest_document() -> dict:
    return {"schema_version": MANIFEST_VERSION, "baseline_sha256": BASELINE_SHA256,
            "evidence": [asdict(b) for b in EVIDENCE_BINDINGS + DEFERRED_BINDINGS + (DECISION_BINDING,)],
            "metrics": METRIC_BINDINGS, "product_slots": load_slot_map(),
            "method_contracts": {b.requirement_id: method_contract(b) for b in EVIDENCE_BINDINGS},
            "strategy": {"strategy_id": STOCK_STRATEGY, "bindings": [asdict(b) for b in STRATEGY_BINDINGS]},
            "ledger_keys": ledger_evidence_keys(), "timeframes": TIMEFRAMES,
            "data_policy": {"completed_only": True, "source_mixing": False, "units": "EXPLICIT_SOURCE_UNITS",
                            "price_basis": "EXPLICIT_RAW_QFQ_HFQ", "availability": "KNOWN_AT_DECISION"},
            "product_policy": "SEMANTIC_SIDECAR_ONLY_NOT_RENDERED", "learning_policy": "NO_PROSE_FEATURES"}


MANIFEST_HASH = digest(manifest_document())


def _validate_method_window_profiles(
    profiles: tuple[MethodWindowProfile, ...],
    bindings: tuple[EvidenceBinding, ...],
) -> tuple[MethodWindowProfile, ...]:
    method_ids = [profile.method_id for profile in profiles]
    if len(method_ids) != len(set(method_ids)):
        raise TraceabilityError("DUPLICATE_METHOD_WINDOW_PROFILE")
    if frozenset(method_ids) != ACCEPTED_METHOD_WINDOW_IDS:
        raise TraceabilityError("ACCEPTED_METHOD_WINDOW_SET_MISMATCH")

    binding_lookup = {
        binding.requirement_id: binding
        for binding in bindings
        if binding.requirement_id != "DECISION"
    }
    expected_requirements = {
        requirement_id
        for requirement_id in binding_lookup
        if not requirement_id.startswith("INTRADAY_")
    }
    covered_requirements = set()
    intraday_gates = {
        binding.timeframe: binding
        for binding in binding_lookup.values()
        if binding.requirement_id.startswith("INTRADAY_")
    }

    for profile in profiles:
        binding = binding_lookup.get(profile.requirement_id)
        if binding is None or profile.requirement_id.startswith("INTRADAY_"):
            raise TraceabilityError("ORPHAN_METHOD_WINDOW_REQUIREMENT:" + profile.method_id)
        if not profile.window_classes or not set(profile.window_classes) <= METHOD_WINDOW_CLASSES:
            raise TraceabilityError("INVALID_METHOD_WINDOW_CLASS:" + profile.method_id)
        if profile.implementation_state not in METHOD_WINDOW_STATES:
            raise TraceabilityError("INVALID_METHOD_WINDOW_STATE:" + profile.method_id)
        if not profile.target_timeframes or not set(profile.target_timeframes) <= METHOD_WINDOW_TIMEFRAMES:
            raise TraceabilityError("INVALID_METHOD_TARGET_TIMEFRAME:" + profile.method_id)
        if len(profile.target_timeframes) != len(set(profile.target_timeframes)):
            raise TraceabilityError("DUPLICATE_METHOD_TARGET_TIMEFRAME:" + profile.method_id)
        if "asset" in profile.target_timeframes and profile.target_timeframes != ("asset",):
            raise TraceabilityError("ASSET_METHOD_TIMEFRAME_MIXED:" + profile.method_id)
        if not set(profile.current_execution_timeframes) <= set(profile.target_timeframes):
            raise TraceabilityError("CURRENT_EXECUTION_OUTSIDE_TARGET:" + profile.method_id)
        if profile.implementation_state == "EXISTING_REUSED":
            if not profile.current_execution_timeframes:
                raise TraceabilityError("CURRENT_EXECUTION_MISSING:" + profile.method_id)
            if binding.implementation_state != "EXISTING_REUSED":
                raise TraceabilityError("LEAF_METHOD_EXCEEDS_PARENT_ADMISSION:" + profile.method_id)
        elif profile.current_execution_timeframes:
            raise TraceabilityError("UNADMITTED_METHOD_CLAIMS_CURRENT_EXECUTION:" + profile.method_id)

        for timeframe in profile.current_execution_timeframes:
            gate = intraday_gates.get(timeframe)
            if gate is not None and gate.implementation_state != "EXISTING_REUSED":
                raise TraceabilityError("CURRENT_EXECUTION_INTRADAY_GATE_DEFERRED:" + profile.method_id)
        covered_requirements.add(profile.requirement_id)

    if covered_requirements != expected_requirements:
        raise TraceabilityError("METHOD_WINDOW_REQUIREMENT_COVERAGE_MISMATCH")
    return profiles


def validate_method_window_profiles(profiles=None) -> tuple[MethodWindowProfile, ...]:
    """Validate the leaf-method view against the current accepted registry."""

    profiles = tuple(METHOD_WINDOW_PROFILES if profiles is None else profiles)
    bindings = EVIDENCE_BINDINGS + DEFERRED_BINDINGS + (DECISION_BINDING,)
    return _validate_method_window_profiles(profiles, bindings)


def compile_method_window_policy_view(profiles=None) -> dict:
    """Derive method/window/Product/Learning coverage without mutating MANIFEST_HASH."""

    validate_registry()
    profiles = validate_method_window_profiles(profiles)
    slots = load_slot_map()["slots"]
    product_slots = {}
    for slot in slots:
        for requirement_id in slot["requirements"]:
            product_slots.setdefault(requirement_id, []).append(slot["id"])

    strategy_clauses = {}
    for binding in STRATEGY_BINDINGS:
        strategy_clauses.setdefault(binding.requirement_id, []).append(
            {
                "clause": binding.clause,
                "classification": binding.classification,
                "resolver": binding.resolver,
            }
        )

    learning_metrics = {}
    for metric in METRIC_BINDINGS:
        learning_metrics.setdefault(metric["requirement_id"], []).append(metric["id"])

    binding_lookup = {
        binding.requirement_id: binding
        for binding in EVIDENCE_BINDINGS + DEFERRED_BINDINGS
    }
    methods = []
    for profile in profiles:
        binding = binding_lookup[profile.requirement_id]
        parent_contract = (
            method_contract(binding)
            if binding.implementation_state == "EXISTING_REUSED"
            else {"version": None, "warmup": None, "config_hash": None}
        )
        methods.append(
            {
                "method_id": profile.method_id,
                "requirement_id": profile.requirement_id,
                "canonical_path": binding.path,
                "owner": binding.owner,
                "parent_callable": binding.callable_name,
                "correlation_group": binding.correlation_group,
                "asset_routes": binding.asset_routes,
                "window_classes": profile.window_classes,
                "target_timeframes": profile.target_timeframes,
                "current_execution_timeframes": profile.current_execution_timeframes,
                "leaf_implementation_state": profile.implementation_state,
                "parent_implementation_state": binding.implementation_state,
                "parent_method_contract": parent_contract,
                "parent_product_slot_ids": tuple(product_slots.get(profile.requirement_id, ())),
                "parent_strategy_clauses": tuple(strategy_clauses.get(profile.requirement_id, ())),
                "parent_learning_metric_ids": tuple(learning_metrics.get(profile.requirement_id, ())),
                "reentry": binding.reentry,
            }
        )

    timeframe_gates = tuple(
        {
            "requirement_id": binding.requirement_id,
            "timeframe": binding.timeframe,
            "canonical_path": binding.path,
            "implementation_state": binding.implementation_state,
            "reentry": binding.reentry,
        }
        for binding in DEFERRED_BINDINGS
        if binding.requirement_id.startswith("INTRADAY_")
    )
    document = {
        "schema_version": METHOD_WINDOW_POLICY_VERSION,
        "source_manifest_version": MANIFEST_VERSION,
        "source_manifest_hash": MANIFEST_HASH,
        "window_classes": tuple(sorted(METHOD_WINDOW_CLASSES)),
        "timeframes": TIMEFRAMES,
        "methods": methods,
        "timeframe_gates": timeframe_gates,
        "decision_authority": {
            "requirement_id": DECISION_BINDING.requirement_id,
            "canonical_path": DECISION_BINDING.path,
            "window_method": False,
        },
        "policy": {
            "derived_view_only": True,
            "changes_runtime_manifest": False,
            "window_type_is_method_property": True,
            "seven_by_eight_is_coverage_not_votes": True,
            "product_wider_than_learning": True,
            "missing_unknown_never_filled_by_prose": True,
        },
    }
    document["view_hash"] = digest(document)
    return document


def validate_registry(bindings=None, strategy=None, metrics=None) -> None:
    bindings = tuple(
        bindings
        if bindings is not None
        else EVIDENCE_BINDINGS + DEFERRED_BINDINGS + (DECISION_BINDING,)
    )
    strategy = tuple(strategy if strategy is not None else STRATEGY_BINDINGS)
    metrics = tuple(metrics if metrics is not None else METRIC_BINDINGS)
    ids = [b.requirement_id for b in bindings]
    paths = [b.path for b in bindings]
    if len(ids) != len(set(ids)) or len(paths) != len(set(paths)):
        raise TraceabilityError("DUPLICATE_BINDING")
    observed_roles = frozenset(
        (
            binding.requirement_id,
            "DECISION"
            if binding.requirement_id == "DECISION"
            else "ADMITTED"
            if binding.implementation_state == "EXISTING_REUSED"
            else "DEFERRED",
        )
        for binding in bindings
    )
    if observed_roles != ACCEPTED_REQUIREMENT_ROLES:
        raise TraceabilityError("ACCEPTED_REQUIREMENT_SET_MISMATCH")
    for binding in bindings:
        if not binding.owner or not binding.fields or not binding.reentry or not binding.correlation_group:
            raise TraceabilityError("INCOMPLETE_BINDING")
        if binding.implementation_state == "EXISTING_REUSED" and binding.callable_name == "NOT_ADMITTED":
            raise TraceabilityError("MISSING_METHOD_OWNER")
        if not binding.asset_routes or not set(binding.asset_routes) <= VALID_ASSET_ROUTES:
            raise TraceabilityError("INVALID_ASSET_ROUTE:" + binding.requirement_id)
        if binding.timeframe not in VALID_BINDING_TIMEFRAMES:
            raise TraceabilityError("INVALID_TIMEFRAME:" + binding.requirement_id)
    for binding in strategy:
        if binding.requirement_id not in ids:
            raise TraceabilityError("ORPHAN_STRATEGY_KEY:" + binding.clause)
    if len({b.clause for b in strategy}) != len(strategy):
        raise TraceabilityError("DUPLICATE_STRATEGY_KEY")
    lookup = {b.requirement_id: b for b in bindings}
    metric_ids = [m.get("id") for m in metrics]
    if len(metric_ids) != len(set(metric_ids)):
        raise TraceabilityError("DUPLICATE_METRIC")
    for metric in metrics:
        if metric.get("kind") != "number":
            raise TraceabilityError("INVALID_METRIC_KIND")
        if metric.get("unit") not in VALID_METRIC_UNITS:
            raise TraceabilityError("INVALID_METRIC_UNIT")
        if metric.get("timeframe") not in VALID_METRIC_TIMEFRAMES:
            raise TraceabilityError("INVALID_METRIC_TIMEFRAME")
        owner = lookup.get(metric["requirement_id"])
        if owner is None or not metric["path"].startswith(owner.path + "."):
            raise TraceabilityError("ORPHAN_LEARNING_METRIC")
    _validate_method_window_profiles(METHOD_WINDOW_PROFILES, bindings)


def validate_source_bindings(root: Path) -> dict:
    """Read-only adoption test; source existence is not runtime success."""
    identities = {}
    for binding in EVIDENCE_BINDINGS + (DECISION_BINDING,):
        path = root / binding.owner
        payload = path.read_bytes()
        tree = ast.parse(payload.decode("utf-8-sig"))
        symbols = {n.name for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        if binding.callable_name not in symbols:
            raise TraceabilityError("CALLABLE_NOT_FOUND:" + binding.requirement_id)
        identities[binding.requirement_id] = {"path": binding.owner, "sha256": sha256(payload).hexdigest(),
                                              "callable": binding.callable_name}
    return identities

@lru_cache(maxsize=32)
def _owner_source_sha256(owner: str) -> str:
    return sha256((Path(__file__).resolve().parents[2] / owner).read_bytes()).hexdigest()


def _binding(requirement_id: str) -> EvidenceBinding:
    for binding in EVIDENCE_BINDINGS + DEFERRED_BINDINGS + (DECISION_BINDING,):
        if binding.requirement_id == requirement_id:
            return binding
    raise TraceabilityError("UNKNOWN_REQUIREMENT:" + str(requirement_id))


def _identity_text(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return str(value.isoformat())
    return str(value).strip() or None


def build_method_execution_receipt(
    requirement_id: str,
    *,
    output: Any,
    asset_route: str,
    stock_code: Any,
    market: Any,
    target_date: Any,
    timeframe: str,
    input_identity: Any = None,
    upstream_hashes: Any = None,
) -> dict:
    """Freeze one actual producer invocation after its output exists."""
    binding = _binding(requirement_id)
    if binding.implementation_state != "EXISTING_REUSED":
        raise TraceabilityError("METHOD_NOT_ADMITTED:" + requirement_id)
    route = str(asset_route or "").strip().upper()
    if route not in VALID_ASSET_ROUTES or route not in binding.asset_routes:
        raise TraceabilityError("INVALID_RECEIPT_ASSET_ROUTE:" + requirement_id)
    if timeframe != binding.timeframe:
        raise TraceabilityError("INVALID_RECEIPT_TIMEFRAME:" + requirement_id)
    contract = method_contract(binding)
    payload = {
        "schema_version": METHOD_EXECUTION_RECEIPT_VERSION,
        "requirement_id": requirement_id,
        "asset_route": route,
        "stock_code": str(stock_code or "").strip() or None,
        "market": str(market or "").strip().lower() or None,
        "target_date": _identity_text(target_date),
        "timeframe": timeframe,
        "input_identity": dict(input_identity) if isinstance(input_identity, Mapping) else None,
        "upstream_hashes": dict(upstream_hashes) if isinstance(upstream_hashes, Mapping) else {},
        "producer": {
            "owner": binding.owner,
            "callable": binding.callable_name,
            "source_sha256": _owner_source_sha256(binding.owner),
            "version": contract["version"],
            "config_hash": contract["config_hash"],
        },
        "output_hash": digest(output),
    }
    payload["receipt_hash"] = digest(payload)
    return payload


def _observed_method_output(binding: EvidenceBinding, value: Mapping) -> Any:
    if binding.requirement_id == "SUPPLY":
        return value.get("completed_bar_context")
    if binding.requirement_id in {"COST", "STRUCTURE", "MOMENTUM", "PATTERN"}:
        return value.get("context")
    return value


def _receipt_required(factor: Mapping) -> bool:
    return factor.get("method_execution_receipt_policy") == "REQUIRED"


def _receipt_for(factor: Mapping, requirement_id: str) -> Any:
    receipts = factor.get("method_execution_receipts")
    return receipts.get(requirement_id) if isinstance(receipts, Mapping) else None


def _receipt_state(binding: EvidenceBinding, value: Mapping, receipt: Any) -> tuple[bool, str]:
    if not isinstance(receipt, Mapping):
        return False, "METHOD_INVOCATION_RECEIPT_MISSING"
    body = dict(receipt)
    receipt_hash = body.pop("receipt_hash", None)
    if body.get("schema_version") != METHOD_EXECUTION_RECEIPT_VERSION or digest(body) != receipt_hash:
        return False, "METHOD_RECEIPT_INVALID"
    if body.get("requirement_id") != binding.requirement_id:
        return False, "METHOD_RECEIPT_REQUIREMENT_MISMATCH"
    if body.get("asset_route") not in binding.asset_routes:
        return False, "METHOD_RECEIPT_ASSET_MISMATCH"
    if body.get("timeframe") != binding.timeframe:
        return False, "METHOD_RECEIPT_TIMEFRAME_MISMATCH"
    contract = method_contract(binding)
    expected_producer = {
        "owner": binding.owner,
        "callable": binding.callable_name,
        "source_sha256": _owner_source_sha256(binding.owner),
        "version": contract["version"],
        "config_hash": contract["config_hash"],
    }
    if body.get("producer") != expected_producer:
        return False, "METHOD_RECEIPT_PRODUCER_MISMATCH"
    observed_output = _observed_method_output(binding, value)
    if body.get("output_hash") != digest(observed_output):
        return False, "METHOD_RECEIPT_OUTPUT_MISMATCH"
    if (
        binding.requirement_id in {"SUPPLY", "COST", "STRUCTURE", "MOMENTUM", "PATTERN", "MTF"}
        and isinstance(observed_output, Mapping)
    ):
        for key, receipt_key in (("stock_code", "stock_code"), ("market", "market"), ("target_date", "target_date")):
            observed = str(observed_output.get(key) or "").strip().lower()
            claimed = str(body.get(receipt_key) or "").strip().lower()
            if observed and observed != claimed:
                return False, "METHOD_RECEIPT_" + key.upper() + "_MISMATCH"
    if binding.requirement_id in {"SUPPLY", "COST", "STRUCTURE", "MOMENTUM", "PATTERN", "MTF"}:
        identity = body.get("input_identity")
        if not isinstance(identity, Mapping) or not identity.get("data_snapshot_identity"):
            return False, "METHOD_INPUT_IDENTITY_MISSING"
        if not identity.get("provider_identity") or not identity.get("adjustment_basis"):
            return False, "METHOD_PRICE_IDENTITY_UNPROVEN"
        if identity.get("price_identity_reasons"):
            return False, "METHOD_PRICE_IDENTITY_UNPROVEN"
        for key in ("stock_code", "market", "target_date"):
            actual = str(identity.get(key) or "").strip().lower()
            claimed = str(body.get(key) or "").strip().lower()
            if actual and actual != claimed:
                return False, "METHOD_INPUT_" + key.upper() + "_MISMATCH"
        if binding.requirement_id == "MTF" and value.get("data_snapshot_identity") != identity.get("data_snapshot_identity"):
            return False, "METHOD_INPUT_SNAPSHOT_MISMATCH"
    if binding.requirement_id == "TREND_RS":
        identity = body.get("input_identity")
        stock = identity.get("stock") if isinstance(identity, Mapping) else None
        benchmark = identity.get("benchmark") if isinstance(identity, Mapping) else None
        if not isinstance(stock, Mapping) or not isinstance(benchmark, Mapping):
            return False, "RS_INPUT_IDENTITY_MISSING"
        if not stock.get("data_snapshot_identity") or not benchmark.get("data_snapshot_identity"):
            return False, "RS_INPUT_IDENTITY_MISSING"
        if not stock.get("provider_identity") or not benchmark.get("provider_identity"):
            return False, "RS_PROVIDER_IDENTITY_UNPROVEN"
        if not stock.get("adjustment_basis") or stock.get("adjustment_basis") != benchmark.get("adjustment_basis"):
            return False, "RS_PRICE_BASIS_UNPROVEN"
        if stock.get("price_identity_reasons") or benchmark.get("price_identity_reasons"):
            return False, "RS_PRICE_IDENTITY_UNPROVEN"
        rs = value.get("relative_strength")
        if not isinstance(rs, Mapping) or rs.get("input_identity") != identity:
            return False, "RS_WRAPPER_IDENTITY_MISMATCH"
        if rs.get("schema_version") != "relative-strength-v1":
            return False, "RS_SCHEMA_MISMATCH"
        if str(rs.get("stock_code") or "").strip() != str(body.get("stock_code") or "").strip():
            return False, "RS_STOCK_IDENTITY_MISMATCH"
        if str(stock.get("stock_code") or "").strip() != str(body.get("stock_code") or "").strip():
            return False, "RS_STOCK_INPUT_IDENTITY_MISMATCH"
        if str(stock.get("market") or "").strip().lower() != str(body.get("market") or "").strip().lower():
            return False, "RS_MARKET_IDENTITY_MISMATCH"
        if str(stock.get("target_date") or "") != str(body.get("target_date") or ""):
            return False, "RS_TARGET_IDENTITY_MISMATCH"
        benchmark_payload = rs.get("benchmark")
        if not isinstance(benchmark_payload, Mapping):
            return False, "RS_BENCHMARK_IDENTITY_MISSING"
        if str(benchmark.get("stock_code") or "").strip() != str(benchmark_payload.get("code") or "").strip():
            return False, "RS_BENCHMARK_IDENTITY_MISMATCH"
        quality = rs.get("data_quality")
        if not isinstance(quality, Mapping) or quality.get("source_alignment") != "MATCHED":
            return False, "RS_SOURCE_ALIGNMENT_UNPROVEN"
        if str(benchmark_payload.get("source") or "").strip() != str(benchmark.get("provider_identity") or "").strip():
            return False, "RS_BENCHMARK_SOURCE_MISMATCH"
        if quality.get("stock_endpoint_sources") != [stock.get("provider_identity")]:
            return False, "RS_STOCK_SOURCE_MISMATCH"
    if binding.requirement_id == "REGIME":
        parents = body.get("upstream_hashes")
        daily = parents.get("daily_market_context") if isinstance(parents, Mapping) else None
        structure = parents.get("market_structure_context") if isinstance(parents, Mapping) else None
        if not isinstance(daily, Mapping) or not isinstance(structure, Mapping):
            return False, "REGIME_UPSTREAM_IDENTITY_MISSING"
        if not daily.get("hash") or not structure.get("hash"):
            return False, "REGIME_UPSTREAM_HASH_MISSING"
        if value.get("upstream_identity") != parents:
            return False, "REGIME_UPSTREAM_HASH_MISMATCH"
        target = str(body.get("target_date") or "")
        for parent in (daily, structure):
            parent_date = str(parent.get("trade_date") or "")
            if parent_date and target and parent_date != target:
                return False, "REGIME_UPSTREAM_DATE_MISMATCH"
        structure_market = str(structure.get("market") or "").strip().lower()
        if structure_market and structure_market != str(body.get("market") or "").strip().lower():
            return False, "REGIME_MARKET_IDENTITY_MISMATCH"
        structure_stock = str(structure.get("stock_code") or "").strip()
        if structure_stock and structure_stock != str(body.get("stock_code") or "").strip():
            return False, "REGIME_STOCK_IDENTITY_MISMATCH"
    return True, "METHOD_INVOCATION_VERIFIED"



def _state(value: Any) -> str:
    if not isinstance(value, Mapping):
        return "MISSING"
    state = str(value.get("evidence_state", value.get("status", "UNKNOWN"))).upper()
    return state if state in STATES else "UNKNOWN"


def method_observation(
    binding: EvidenceBinding,
    value: Any,
    *,
    receipt: Any = None,
    require_receipt: bool = False,
) -> tuple[str, str]:
    """Validate metadata and, on Production paths, the actual execution receipt."""
    state = _state(value)
    if binding.implementation_state != "EXISTING_REUSED":
        return "UNKNOWN", "METHOD_NOT_ADMITTED"
    if not isinstance(value, Mapping):
        return "MISSING", "OUTPUT_NOT_OBSERVED"
    if binding.requirement_id == "DECISION":
        return ("READY" if value.get("evidence_state") == "PROVEN" else state), "CANONICAL_DECISION_OBSERVED"
    if state not in {"READY", "PARTIAL"}:
        return state, "OUTPUT_NOT_READY"
    context = value.get("completed_bar_context") if binding.requirement_id == "SUPPLY" else value.get("context", value)
    if not isinstance(context, Mapping):
        return "UNKNOWN", "METHOD_CONTEXT_MISSING"
    contract = method_contract(binding)
    version_key = ("schema_version" if "SCHEMA_VERSION" in binding.version_ref else
                   "algorithm_version" if "ALGORITHM_VERSION" in binding.version_ref else "version")
    if contract["version"] is None or context.get(version_key) != contract["version"]:
        return "UNKNOWN", "METHOD_VERSION_MISMATCH"
    if contract["config_hash"] and context.get("config_hash") != contract["config_hash"]:
        return "UNKNOWN", "METHOD_CONFIG_MISMATCH"
    if binding.requirement_id in {"COST", "STRUCTURE", "MOMENTUM", "PATTERN", "MTF"} and context.get("completed_bar_only") is not True:
        return "UNKNOWN", "COMPLETED_BAR_NOT_PROVEN"
    if state == "READY" and binding.timeframe == "daily" and isinstance(contract["warmup"], (int, tuple)):
        minimum = contract["warmup"]
        count = context.get("observations")
        alignment = at(context, "source_alignment.status")
        if binding.requirement_id == "SUPPLY":
            count = at(context, "data_quality.observations")
            alignment = at(context, "data_quality.source_alignment")
        elif binding.requirement_id == "COST":
            minimum = max(minimum)
            window = at(context, "bar_reference_cost.window_" + str(minimum)) or {}
            count, alignment = window.get("observations"), window.get("source_alignment")
        if type(count) is not int or count < minimum:
            return "UNKNOWN", "WARMUP_NOT_PROVEN"
        if alignment != "SINGLE_SOURCE":
            return "UNKNOWN", "SOURCE_ALIGNMENT_NOT_PROVEN"
    if require_receipt:
        receipt_ok, receipt_reason = _receipt_state(binding, value, receipt)
        if not receipt_ok:
            return "UNKNOWN", receipt_reason
        return state, receipt_reason
    return state, "METHOD_METADATA_VERIFIED_NOT_INVOCATION_PROOF"


def timeframe_ready(factor: Mapping, timeframe: str) -> bool:
    binding = next(b for b in EVIDENCE_BINDINGS if b.requirement_id == "MTF")
    parent = at(factor, binding.path)
    strict = _receipt_required(factor)
    state, _ = method_observation(
        binding, parent, receipt=_receipt_for(factor, "MTF"), require_receipt=strict,
    )
    frame = at(parent, "timeframes." + timeframe)
    minimum = method_contract(binding)["warmup"]
    return bool(
        timeframe in {"monthly", "weekly", "daily"} and state in {"READY", "PARTIAL"}
        and isinstance(frame, Mapping) and frame.get("completed_bar_only") is True
        and _state(frame) == "READY" and at(frame, "trend.status") == "READY"
        and at(frame, "source_alignment.status") == "SINGLE_SOURCE"
        and type(frame.get("observations")) is int and isinstance(minimum, int)
        and frame["observations"] >= minimum
    )


def _resolve(binding: StrategyBinding, factor: Mapping) -> str:
    kind = binding.resolver
    if kind in {"deferred", "not_used"}:
        return "UNKNOWN"
    owner = next(b for b in EVIDENCE_BINDINGS if b.requirement_id == binding.requirement_id)
    checked, _ = method_observation(
        owner,
        at(factor, owner.path),
        receipt=_receipt_for(factor, owner.requirement_id),
        require_receipt=_receipt_required(factor),
    )
    if checked not in {"READY", "PARTIAL"}:
        return "UNKNOWN"
    regime = at(factor, "market_sector_regime") or {}
    if kind == "market":
        if at(regime, "market.data_quality") != "ok":
            return "UNKNOWN"
        return {"PERMISSIVE": "SATISFIED", "RISK_OFF": "FAILED"}.get(at(regime, "market.state"), "UNKNOWN")
    if kind == "sector":
        if at(regime, "sector.status") != "ok":
            return "UNKNOWN"
        return {"SUPPORTIVE": "SATISFIED", "COOLING": "FAILED"}.get(at(regime, "sector.state"), "UNKNOWN")
    if kind in {"weekly", "daily"}:
        frame = at(factor, "multi_timeframe_structure_context.timeframes." + kind)
        if not timeframe_ready(factor, kind):
            return "UNKNOWN"
        if at(frame, "source_alignment.status") != "SINGLE_SOURCE" or _state(frame) != "READY":
            return "UNKNOWN"
        if _state(frame.get("price_structure")) != "READY" or _state(frame.get("trend")) != "READY":
            return "UNKNOWN"
        return {"BULLISH": "SATISFIED", "BEARISH": "FAILED"}.get(at(frame, "trend.direction"), "UNKNOWN")
    supply = at(factor, "supply_demand_volume_price")
    if _state(supply) != "READY" or _state(at(supply, "completed_bar_context")) != "READY":
        return "UNKNOWN"
    volume = at(supply, "legacy_volume.status")
    if volume == "放量下跌":
        return "FAILED"
    if kind == "pullback":
        return "SATISFIED" if volume == "缩量回调" else "UNKNOWN"
    # Only the existing completed-bar state is reused, never a new numeric threshold.
    return {"DEMAND_PRESSURE": "SATISFIED", "SUPPLY_PRESSURE": "FAILED"}.get(
        at(supply, "completed_bar_context.state"), "UNKNOWN")


def build_strategy_eligibility(factor: Mapping, *, schema_version: str) -> dict:
    validate_registry()
    required = {b.clause: _resolve(b, factor) for b in STRATEGY_BINDINGS if b.classification == "HARD_ELIGIBILITY"}
    if factor.get("strategy_id") != STOCK_STRATEGY:
        required = dict.fromkeys(required, "UNKNOWN")
    state = "INELIGIBLE" if "FAILED" in required.values() else "UNKNOWN"
    # No positive full-strategy admission: multiple methods, including 30m, are deferred.
    reasons = ["REQUIRED_EVIDENCE_FAILED"] if state == "INELIGIBLE" else ["REQUIRED_EVIDENCE_INCOMPLETE"]
    return {"schema_version": schema_version, "strategy_id": factor.get("strategy_id"),
            "state": state, "required_evidence": required, "reason_codes": reasons}


def build_runtime_trace(factor: Mapping) -> dict:
    validate_registry()
    known_paths = {b.path for b in EVIDENCE_BINDINGS + DEFERRED_BINDINGS + (DECISION_BINDING,)}
    if digest(manifest_document()) != MANIFEST_HASH:
        raise TraceabilityError("MANIFEST_CHANGED_DURING_PROCESS")
    for key, value in factor.items():
        if (key.endswith("_evidence") or (isinstance(value, Mapping) and "family" in value)) and key not in known_paths:
            raise TraceabilityError("ORPHAN_CANONICAL_EVIDENCE:" + key)
    observations = []
    for binding in EVIDENCE_BINDINGS + DEFERRED_BINDINGS + (DECISION_BINDING,):
        value = at(factor, binding.path)
        declared = _state(value)
        deferred = binding.implementation_state != "EXISTING_REUSED"
        admitted_state, admission_reason = method_observation(
            binding,
            value,
            receipt=_receipt_for(factor, binding.requirement_id),
            require_receipt=_receipt_required(factor),
        )
        observations.append({"requirement_id": binding.requirement_id, "path": binding.path,
                             "state": admitted_state, "declared_state": declared,
                             "method_state": "NOT_ADMITTED" if deferred else ("OUTPUT_OBSERVED" if value is not None else "NOT_OBSERVED"),
                             "output_hash": digest(value) if value is not None else None,
                             "correlation_group": binding.correlation_group,
                             "reason": binding.reentry if deferred else admission_reason})
    mtf = at(factor, "multi_timeframe_structure_context") or {}
    document = {"schema_version": TRACE_VERSION, "manifest_version": MANIFEST_VERSION,
                "manifest_hash": MANIFEST_HASH, "strategy_id": factor.get("strategy_id"),
                "data_snapshot_identity": mtf.get("data_snapshot_identity"),
                "data_identity": {key: mtf.get(key) for key in ("provider_identity", "adjustment_basis", "available_at_max", "target_date", "algorithm_version", "config_hash")},
                "observations": observations,
                "strategy_eligibility": factor.get("strategy_eligibility"),
                "canonical_decision": factor.get("canonical_decision"),
                "product_state": "NOT_RENDERED_NOT_AUTHORIZED",
                "ledger_state": "NOT_YET_PERSISTED",
                "independent_vote_count": None}
    document["runtime_trace_hash"] = digest(document)
    return document


def validate_runtime_trace(factor: Mapping) -> dict:
    actual = factor.get("evidence_traceability")
    expected = build_runtime_trace(factor)
    if actual != expected:
        raise TraceabilityError("RUNTIME_TRACE_MISSING_OR_STALE")
    return expected


def learning_projection(factor: Mapping) -> dict:
    """Versioned allowlisted values only; old audit snapshots remain untouched."""
    trace = validate_runtime_trace(factor)
    states = {o["requirement_id"]: o["state"] for o in trace["observations"]}
    values = {}
    for metric in METRIC_BINDINGS:
        value = at(factor, metric["path"])
        ready = states[metric["requirement_id"]] == "READY"
        if metric["timeframe"] in {"monthly", "weekly"}:
            ready = timeframe_ready(factor, metric["timeframe"]) and states["MTF"] in {"READY", "PARTIAL"}
        numeric = _metric_value_valid(metric, value)
        values[metric["id"]] = {"value": float(value) if ready and numeric else None,
                               "state": "READY" if ready and numeric else "MISSING_OR_UNADMITTED"}
    return {"schema_version": "stock-factor-numeric-evidence-v2", "manifest_hash": MANIFEST_HASH,
            "trace_identity": trace_identity(trace), "values": values,
            "training_admitted": False}


def _metric_value_valid(metric: Mapping, value: Any) -> bool:
    if not isinstance(value, Real) or isinstance(value, bool) or not math.isfinite(float(value)):
        return False
    number = float(value)
    metric_id = str(metric.get("id") or "")
    if metric_id in {"daily.macd_dif", "daily.macd_dea", "daily.macd_bar"}:
        return True
    unit = metric.get("unit")
    if unit == "index_0_100":
        return 0.0 <= number <= 100.0
    if unit == "annualized_pct":
        return number >= 0.0
    if unit in {"PRICE_BASIS_CURRENCY", "PRICE_BASIS_CURRENCY_PROXY"}:
        return number > 0.0
    if metric_id in {"daily.volume_ratio20", "daily.tr_sma20_not_wilder_atr"}:
        return number >= 0.0
    if metric_id in {"daily.directional_volume", "daily.cmf20"}:
        return -1.0 <= number <= 1.0
    return True


def describe_macd_state(result: Any) -> str:
    """Project an existing enum, never trust an action-like free-text signal."""
    if getattr(result, "macd_signal", None) == "数据不足":
        return ""
    raw = getattr(result, "macd_status", None)
    status = str(getattr(raw, "value", raw) or "")
    dif, dea = getattr(result, "macd_dif", None), getattr(result, "macd_dea", None)
    numeric = all(isinstance(v, Real) and not isinstance(v, bool) and math.isfinite(float(v)) for v in (dif, dea))
    if status in {"多头", "空头"}:
        if not numeric:
            return ""
        if dif > 0 and dea > 0:
            return "MACD DIF/DEA位于零轴上方，动量偏强"
        if dif < 0 and dea < 0:
            return "MACD DIF/DEA位于零轴下方，动量偏弱"
        return "MACD处于零轴附近或两线异侧，方向需结构确认"
    if status == "金叉" and numeric and dif < 0:
        return "MACD零轴下金叉，局部动量修复；不是趋势反转证明"
    descriptions = {
        "零轴上金叉": "MACD零轴上金叉，动量交叉确认；不是独立买点",
        "金叉": "MACD金叉，仅为动量修复；需核零轴位置与高周期结构",
        "死叉": "MACD死叉，动量转弱；结合高周期结构与风险条件",
        "上穿零轴": "MACD DIF上穿零轴，动量改善",
        "下穿零轴": "MACD DIF下穿零轴，动量走弱",
        "多头": "MACD零轴上方动量状态，仍需结构确认",
        "空头": "MACD零轴下方动量状态，仍需结构确认",
    }
    return descriptions.get(status, "")


def trace_identity(trace: Mapping) -> dict:
    return {key: trace.get(key) for key in ("manifest_version", "manifest_hash", "runtime_trace_hash", "data_snapshot_identity")}


validate_registry()
