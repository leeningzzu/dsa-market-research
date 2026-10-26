# -*- coding: utf-8 -*-
"""Deterministic volatility/momentum evidence over completed daily bars.

V1 reuses DSA's existing MACD/RSI formulas and the confirmed Pivot/Swing owner.
Process diagnostics are observational only: they do not classify a market "world",
automatically switch strategies, or create independent action authority.
"""

from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
import math
from math import isfinite
from typing import Any, Dict, List, Optional

import pandas as pd

from data_provider.daily_data_identity import bind_verified_daily_source_view


VOLATILITY_MOMENTUM_SCHEMA_VERSION = "volatility-momentum-v2"
ALGORITHM_VERSION = "volatility-momentum-v2"
MIN_OBSERVATIONS = 21
READY_OBSERVATIONS = 61
SOURCE_ALIGNMENT_WINDOW = 61
DIAGNOSTIC_RETURN_WINDOW = 60
VARIANCE_SCALE = 5
WILDER_ATR_PERIOD = 14
_RECURSIVE_STARTUP_POLICY_VERSION = "bounded-native-recursive-startup-v1"
_RECURSIVE_SEED_RESIDUAL_BOUND = 0.01

_CONFIG = {
    "algorithm_version": ALGORITHM_VERSION,
    "minimum_observations": MIN_OBSERVATIONS,
    "ready_observations": READY_OBSERVATIONS,
    "source_alignment_window": SOURCE_ALIGNMENT_WINDOW,
    "diagnostic_return_window": DIAGNOSTIC_RETURN_WINDOW,
    "variance_scale": VARIANCE_SCALE,
    "roc_windows": [20, 60],
    "realized_volatility_window": 20,
    "true_range_sma_window": 20,
    "wilder_atr_period": WILDER_ATR_PERIOD,
    "divergence_oscillators": ["MACD_DIF", "RSI_12"],
}
CONFIG_HASH = sha256(
    json.dumps(_CONFIG, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


def _safe_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _normalize_history(history: Any, *, target_date: date) -> pd.DataFrame:
    required = ["date", "open", "high", "low", "close"]
    if not isinstance(history, pd.DataFrame) or history.empty:
        return pd.DataFrame(columns=required)
    frame = history.copy()
    frame.columns = [str(column).lower() for column in frame.columns]
    if any(column not in frame.columns for column in required):
        return pd.DataFrame(columns=required)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    for column in ("open", "high", "low", "close"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=required)
    frame = frame[frame["date"] <= target_date]
    frame = frame.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    columns = required + (["data_source"] if "data_source" in frame.columns else [])
    result = frame[columns].reset_index(drop=True)
    return bind_verified_daily_source_view(history, result)


def _invalid_ohlc(frame: pd.DataFrame) -> bool:
    return bool(
        (
            (frame["open"] <= 0)
            | (frame["high"] <= 0)
            | (frame["low"] <= 0)
            | (frame["close"] <= 0)
            | (frame["high"] < frame["low"])
            | (frame["high"] < frame["open"])
            | (frame["high"] < frame["close"])
            | (frame["low"] > frame["open"])
            | (frame["low"] > frame["close"])
        ).any()
    )


def _source_alignment(frame: pd.DataFrame) -> Dict[str, Any]:
    window = frame.tail(SOURCE_ALIGNMENT_WINDOW)
    if "data_source" not in window.columns:
        return {"status": "UNPROVEN", "sources": [], "rows_complete": False}
    text = window["data_source"].map(
        lambda value: str(value).strip() if value not in (None, "") else ""
    )
    sources = sorted({value for value in text.tolist() if value})
    complete = bool((text != "").all())
    return {
        "status": "SINGLE_SOURCE" if len(sources) == 1 and complete else "UNPROVEN",
        "sources": sources,
        "rows_complete": complete,
    }


def _indicator_frame(frame: pd.DataFrame) -> pd.DataFrame:
    # Reuse the exact Production report formulas instead of introducing a parallel TA implementation.
    from src.stock_analyzer import StockTrendAnalyzer

    analyzer = StockTrendAnalyzer()
    calculated = analyzer._calculate_macd(frame.copy())
    return analyzer._calculate_rsi(calculated)


def _roc_pct(close: pd.Series, sessions: int) -> Optional[float]:
    values = pd.to_numeric(close, errors="coerce").dropna()
    if len(values) <= sessions:
        return None
    base = float(values.iloc[-sessions - 1])
    latest = float(values.iloc[-1])
    if base <= 0:
        return None
    return (latest / base - 1.0) * 100.0


def _realized_volatility_20d_pct(close: pd.Series) -> Optional[float]:
    returns = pd.to_numeric(close, errors="coerce").pct_change().dropna().tail(20)
    if len(returns) < 2:
        return None
    value = float(returns.std(ddof=1)) * (252 ** 0.5) * 100.0
    return value if isfinite(value) else None


def _true_range_sma_20_pct(frame: pd.DataFrame) -> Optional[float]:
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)
    tr_sma = true_range.tail(20).dropna().mean()
    last_close = _safe_float(close.dropna().iloc[-1]) if not close.dropna().empty else None
    if pd.isna(tr_sma) or last_close is None or last_close <= 0:
        return None
    return float(tr_sma) / last_close * 100.0


def _wilder_atr(frame: pd.DataFrame, period: int = WILDER_ATR_PERIOD) -> tuple[Optional[float], Optional[float]]:
    """Return Wilder ATR and ATR/close over completed bars only.

    The first row has no previous-close transition and is excluded from the
    seed.  The next ``period`` true ranges seed the recursive RMA; every later
    value uses ``(previous * (period - 1) + current) / period``.
    """
    if period <= 0 or len(frame) <= period:
        return None, None
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1)
    seed = true_range.iloc[1 : period + 1].dropna()
    if len(seed) != period:
        return None, None
    atr = _safe_float(seed.mean())
    if atr is None:
        return None, None
    for value in true_range.iloc[period + 1 :]:
        current = _safe_float(value)
        if current is None:
            return None, None
        atr = (atr * (period - 1) + current) / period
    last_close = _safe_float(close.dropna().iloc[-1]) if not close.dropna().empty else None
    if last_close is None or last_close <= 0:
        return None, None
    return atr, atr / last_close * 100.0


def _wilder_seed_history_per_start(period: int) -> int:
    """Observed minimum from the method's own RMA decay, not a global window."""
    if period <= 1:
        raise ValueError("WILDER_PERIOD_INVALID")
    return period + 1 + math.ceil(
        math.log(_RECURSIVE_SEED_RESIDUAL_BOUND) / math.log1p(-1.0 / period)
    )


def rsi_startup_comparison_required_bars() -> int:
    from src.stock_analyzer import StockTrendAnalyzer

    return 2 * _wilder_seed_history_per_start(StockTrendAnalyzer.RSI_LONG)


def atr_startup_comparison_required_bars() -> int:
    return 2 * _wilder_seed_history_per_start(WILDER_ATR_PERIOD)


def _recursive_numeric_startup_receipt(
    frame: pd.DataFrame, *, method: str,
    required_observations: int, compute: Any,
    absolute_tolerance: Optional[float] = None,
    relative_tolerance: Optional[float] = None,
) -> Dict[str, Any]:
    """Compare three native suffix starts to full completed observed history.

    Comparison never certifies unseen prehistory, provider rights, vintage,
    point-in-time knowledge, independent actions, or model eligibility.
    """
    observations = len(frame)
    receipt = {
        "policy_version": _RECURSIVE_STARTUP_POLICY_VERSION,
        "method": method,
        "status": "MISSING",
        "reason": method + "_STARTUP_HISTORY_INSUFFICIENT",
        "required_observations": required_observations,
        "observations": observations,
        "evaluated_startup_lengths": [],
        "max_endpoint_difference": None,
        "absolute_tolerance": None,
        "numeric_agreement": None,
        "state_agreement": None,
        "readiness_scope": "OBSERVED_COMPLETED_SUFFIXES_ONLY",
        "unobserved_prehistory_convergence": "NOT_PROVEN",
        "strategy_admitted": False,
        "learning_admitted": False,
    }
    if observations < required_observations:
        return receipt
    lengths = tuple((observations * fraction) // 6 for fraction in (3, 4, 5))
    if len(set(lengths)) != 3 or min(lengths) < required_observations // 2:
        return {**receipt, "reason": method + "_DISTINCT_STARTS_INSUFFICIENT"}
    try:
        reference, reference_state = compute(frame)
        if not reference or any(
            value is None or not math.isfinite(float(value))
            for value in reference.values()
        ):
            raise ValueError("RECURSIVE_REFERENCE_INVALID")
        tolerance = (
            float(absolute_tolerance) if absolute_tolerance is not None
            else max(
                1e-10, float(relative_tolerance) *
                max(abs(float(v)) for v in reference.values()),
            )
        )
        if not math.isfinite(tolerance) or tolerance <= 0:
            raise ValueError("RECURSIVE_TOLERANCE_INVALID")
        numeric_match = True
        state_match = True
        max_delta = 0.0
        for length in lengths:
            values, state = compute(frame.iloc[-length:].reset_index(drop=True))
            if set(values) != set(reference):
                raise ValueError("RECURSIVE_SCHEMA_MISMATCH")
            delta = max(
                abs(float(reference[key]) - float(values[key]))
                for key in reference
            )
            if not math.isfinite(delta):
                raise ValueError("RECURSIVE_VALUE_NONFINITE")
            max_delta = max(max_delta, delta)
            numeric_match = numeric_match and delta <= tolerance
            state_match = state_match and state == reference_state
    except (KeyError, TypeError, ValueError, ArithmeticError, IndexError):
        return {
            **receipt, "status": "UNKNOWN",
            "reason": method + "_STARTUP_NATIVE_INVALID",
        }
    admitted = numeric_match and state_match
    return {
        **receipt,
        "status": "READY" if admitted else "UNKNOWN",
        "reason": (
            method + "_OBSERVED_STARTS_AGREE"
            if admitted else method + "_STARTUP_NUMERIC_OR_STATE_DRIFT"
        ),
        "evaluated_startup_lengths": [*lengths, observations],
        "max_endpoint_difference": max_delta,
        "absolute_tolerance": tolerance,
        "numeric_agreement": numeric_match,
        "state_agreement": state_match,
        "reference_state": reference_state,
        # These numbers come from this exact native reference computation
        # only after its independent suffix-start numeric+state agreement.
        "reference_values": (
            {key: float(value) for key, value in reference.items()}
            if admitted else None
        ),
    }


def _rsi_startup_stability(frame: pd.DataFrame, analyzer: Any) -> Dict[str, Any]:
    from src.stock_analyzer import StockTrendAnalyzer, TrendAnalysisResult

    required = rsi_startup_comparison_required_bars()
    if not isinstance(analyzer, StockTrendAnalyzer):
        return {
            "method": "RSI", "status": "UNKNOWN",
            "reason": "NATIVE_RSI_OWNER_UNAVAILABLE",
            "required_observations": required,
            "readiness_scope": "OBSERVED_COMPLETED_SUFFIXES_ONLY",
            "strategy_admitted": False, "learning_admitted": False,
        }

    def calculate(part: pd.DataFrame):
        close = pd.to_numeric(part["close"], errors="raise")
        if close.isna().any() or bool((close <= 0).any()):
            raise ValueError("INVALID_CLOSE")
        values = analyzer._calculate_rsi(pd.DataFrame({"close": close}))
        projection = TrendAnalysisResult(code="RSI_NATIVE_STARTUP_AUDIT")
        analyzer._analyze_rsi(values, projection)
        result = {
            f"RSI_{period}_{side}": float(values.iloc[index][f"RSI_{period}"])
            for period in (analyzer.RSI_SHORT, analyzer.RSI_MID, analyzer.RSI_LONG)
            for side, index in (("latest", -1), ("previous", -2))
        }
        return result, getattr(getattr(projection, "rsi_status", None), "name", None)

    return _recursive_numeric_startup_receipt(
        frame, method="RSI", required_observations=required,
        compute=calculate, absolute_tolerance=0.5,
    )


def _atr_startup_stability(frame: pd.DataFrame) -> Dict[str, Any]:
    def calculate(part: pd.DataFrame):
        now, _ = _wilder_atr(part)
        previous, _ = _wilder_atr(part.iloc[:-1])
        return {"atr": now, "previous_atr": previous}, None

    return _recursive_numeric_startup_receipt(
        frame, method="ATR_WILDER",
        required_observations=atr_startup_comparison_required_bars(),
        compute=calculate, relative_tolerance=0.01,
    )


def _lag1_corr(series: pd.Series) -> Optional[float]:
    values = pd.to_numeric(series, errors="coerce").dropna()
    if len(values) < 3:
        return None
    current = values.iloc[1:].reset_index(drop=True)
    previous = values.iloc[:-1].reset_index(drop=True)
    value = current.corr(previous)
    return _safe_float(value)


def _variance_scaling_ratio_5(close: pd.Series) -> Optional[float]:
    values = pd.to_numeric(close, errors="coerce").dropna()
    one = values.pct_change().dropna().tail(DIAGNOSTIC_RETURN_WINDOW)
    five = values.pct_change(VARIANCE_SCALE).dropna().tail(DIAGNOSTIC_RETURN_WINDOW)
    if len(one) < 10 or len(five) < 5:
        return None
    one_var = float(one.var(ddof=1))
    five_var = float(five.var(ddof=1))
    if not isfinite(one_var) or not isfinite(five_var) or one_var <= 0:
        return None
    return five_var / (VARIANCE_SCALE * one_var)


def _process_diagnostics(close: pd.Series) -> Dict[str, Any]:
    returns = pd.to_numeric(close, errors="coerce").pct_change().dropna().tail(DIAGNOSTIC_RETURN_WINDOW)
    autocorr = _lag1_corr(returns)
    abs_autocorr = _lag1_corr(returns.abs())
    skew = _safe_float(returns.skew()) if len(returns) >= 3 else None
    excess_kurtosis = _safe_float(returns.kurt()) if len(returns) >= 4 else None
    tail_event_count = None
    if len(returns) >= 10:
        std = _safe_float(returns.std(ddof=1))
        mean = _safe_float(returns.mean())
        if std is not None and mean is not None and std > 0:
            tail_event_count = int(((returns - mean).abs() > 3.0 * std).sum())
    return {
        "timeframe": "1d",
        "window_returns": int(len(returns)),
        "return_autocorr_lag1": autocorr,
        "variance_scaling_ratio_5": _variance_scaling_ratio_5(close),
        "variance_scaling_semantics": "DESCRIPTIVE_OVERLAPPING_5_SESSION_RATIO_NOT_SIGNIFICANCE_TEST",
        "abs_return_autocorr_lag1": abs_autocorr,
        "skew_60": skew,
        "excess_kurtosis_60": excess_kurtosis,
        "tail_event_count_abs_3sigma": tail_event_count,
        "classification_authority": "OBSERVATIONAL_ONLY",
        "world_classification": "NOT_PERFORMED",
        "automatic_strategy_switching": False,
        "deferred_models": ["ADF_STATIONARITY", "OU_HALF_LIFE", "GARCH", "HMM_REGIME_SWITCHING"],
    }


def _pivot_pair(price_structure_context: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(price_structure_context, dict):
        return None
    if str(price_structure_context.get("schema_version") or "") != "price-structure-v1":
        return None
    if str(price_structure_context.get("status") or "").upper() != "READY":
        return None
    if price_structure_context.get("historical_replay_eligible") is not True:
        return None
    candidates: List[Dict[str, Any]] = []
    pivots = [item for item in price_structure_context.get("pivots", []) if isinstance(item, dict)]
    for kind in ("LOW", "HIGH"):
        same_kind = [
            item
            for item in pivots
            if item.get("kind") == kind
            and not bool(item.get("provisional"))
            and item.get("origin_time")
            and item.get("confirmed_at")
            and _safe_float(item.get("price")) is not None
        ]
        same_kind.sort(key=lambda item: (str(item["confirmed_at"]), str(item["origin_time"])))
        if len(same_kind) >= 2:
            candidates.append({"kind": kind, "first": same_kind[-2], "second": same_kind[-1]})
    if not candidates:
        return None
    return max(candidates, key=lambda item: str(item["second"]["confirmed_at"]))


def _confirmed_divergence(indicators: pd.DataFrame, price_structure_context: Any) -> Dict[str, Any]:
    pair = _pivot_pair(price_structure_context)
    if pair is None:
        return {
            "status": "MISSING",
            "state": "NONE",
            "reason": "CONFIRMED_SAME_KIND_PIVOT_PAIR_MISSING",
            "signals": [],
            "independent_confirmation_group_count": 0,
        }
    first = pair["first"]
    second = pair["second"]
    first_date = pd.to_datetime(first["origin_time"], errors="coerce").date()
    second_date = pd.to_datetime(second["origin_time"], errors="coerce").date()
    indexed = indicators.copy()
    indexed["date"] = pd.to_datetime(indexed["date"], errors="coerce").dt.date
    first_rows = indexed[indexed["date"] == first_date]
    second_rows = indexed[indexed["date"] == second_date]
    if first_rows.empty or second_rows.empty:
        return {
            "status": "MISSING",
            "state": "NONE",
            "reason": "PIVOT_ORIGIN_INDICATOR_VALUE_MISSING",
            "signals": [],
            "independent_confirmation_group_count": 0,
        }

    first_price = float(first["price"])
    second_price = float(second["price"])
    if pair["kind"] == "LOW":
        price_extension = second_price < first_price
        divergence_state = "BULLISH_DIVERGENCE"
        oscillator_diverges = lambda a, b: b > a
    else:
        price_extension = second_price > first_price
        divergence_state = "BEARISH_DIVERGENCE"
        oscillator_diverges = lambda a, b: b < a

    group = (
        f"same_swing:{pair['kind'].lower()}:"
        f"{first['origin_time']}:{second['origin_time']}"
    )
    signals: List[Dict[str, Any]] = []
    if price_extension:
        for oscillator in ("MACD_DIF", "RSI_12"):
            first_value = _safe_float(first_rows.iloc[-1].get(oscillator))
            second_value = _safe_float(second_rows.iloc[-1].get(oscillator))
            if first_value is None or second_value is None:
                continue
            if oscillator_diverges(first_value, second_value):
                signals.append(
                    {
                        "oscillator": oscillator,
                        "state": divergence_state,
                        "first_value": first_value,
                        "second_value": second_value,
                        "correlation_group": group,
                    }
                )
    return {
        "status": "READY",
        "state": divergence_state if signals else "NONE",
        "price_pivot_kind": pair["kind"],
        "first_origin_time": first["origin_time"],
        "second_origin_time": second["origin_time"],
        "confirmed_at": second["confirmed_at"],
        "provisional": False,
        "correlation_group": group,
        "signals": signals,
        "independent_confirmation_group_count": 1 if signals else 0,
        "same_swing_double_counting": "PROHIBITED",
    }


def _empty_payload(base: Dict[str, Any], *, status: str, reason: str, observations: int = 0) -> Dict[str, Any]:
    return {
        **base,
        "status": status,
        "reason": reason,
        "observations": observations,
        "volatility": {"status": "MISSING"},
        "momentum": {"status": "MISSING"},
        "confirmed_divergence": {"status": "MISSING", "state": "NONE", "signals": []},
        "process_diagnostics": {"status": "MISSING", "classification_authority": "NONE"},
        "historical_replay_eligible": False,
    }


def build_volatility_momentum_context(
    *,
    stock_code: str,
    history: Any,
    target_date: Optional[date],
    market: Optional[str] = None,
    trend_result: Any = None,
    price_structure_context: Any = None,
) -> Dict[str, Any]:
    """Build daily volatility/momentum evidence without fetching or action authority."""
    base = {
        "schema_version": VOLATILITY_MOMENTUM_SCHEMA_VERSION,
        "family": "volatility_momentum",
        "stock_code": stock_code,
        "market": str(market or "").lower() or None,
        "timeframe": "1d",
        "completed_bar_only": True,
        "algorithm_version": ALGORITHM_VERSION,
        "config_hash": CONFIG_HASH,
        "hard_veto": False,
        "independent_action_authority": False,
        "automatic_strategy_switching": False,
    }
    if not isinstance(target_date, date):
        return _empty_payload(base, status="UNKNOWN", reason="TARGET_DATE_UNKNOWN")

    frame = _normalize_history(history, target_date=target_date)
    base["target_date"] = target_date.isoformat()
    if frame.empty:
        return _empty_payload(base, status="MISSING", reason="COMPLETED_OHLC_MISSING")
    if frame.iloc[-1]["date"] != target_date:
        return _empty_payload(
            base, status="MISSING", reason="TARGET_DATE_BAR_MISSING", observations=int(len(frame))
        )
    if len(frame) < MIN_OBSERVATIONS:
        return _empty_payload(
            base, status="MISSING", reason="WARMUP_INSUFFICIENT", observations=int(len(frame))
        )
    if _invalid_ohlc(frame):
        return _empty_payload(
            base, status="UNKNOWN", reason="INVALID_OHLC", observations=int(len(frame))
        )

    alignment = _source_alignment(frame)
    indicators = _indicator_frame(frame)
    close = indicators["close"]
    realized_vol = _realized_volatility_20d_pct(close)
    tr_sma_pct = _true_range_sma_20_pct(indicators)
    atr_wilder, atr_wilder_pct = _wilder_atr(indicators)
    roc20 = _roc_pct(close, 20)
    roc60 = _roc_pct(close, 60)

    # Distinct native recursive methods must not promote one another.
    from src.stock_analyzer import StockTrendAnalyzer
    from src.services.multi_timeframe_structure_service import _macd_startup_stability

    owner = StockTrendAnalyzer()
    recursive = {
        "macd": _macd_startup_stability(frame, owner),
        "rsi": _rsi_startup_stability(frame, owner),
        "atr_wilder": _atr_startup_stability(frame),
    }
    same_observed_source = (
        "data_source" in frame.columns
        and bool(frame["data_source"].notna().all())
        and bool((frame["data_source"].astype(str).str.strip() != "").all())
        and frame["data_source"].astype(str).nunique() == 1
    )
    if not same_observed_source:
        recursive = {
            method: (
                {**item, "status": "UNKNOWN",
                 "reason": "RECURSIVE_SOURCE_IDENTITY_INCONSISTENT"}
                if item["status"] == "READY" else item
            )
            for method, item in recursive.items()
        }
    macd_receipt = recursive["macd"]
    rsi_receipt = recursive["rsi"]
    atr_receipt = recursive["atr_wilder"]
    macd_admitted = macd_receipt["status"] == "READY"
    rsi_admitted = rsi_receipt["status"] == "READY"
    atr_admitted = atr_receipt["status"] == "READY"
    macd_status = getattr(getattr(trend_result, "macd_status", None), "value", None)
    rsi_status = getattr(getattr(trend_result, "rsi_status", None), "value", None)
    latest = indicators.iloc[-1]
    momentum = {
        "status": (
            "READY" if trend_result is not None and macd_admitted and rsi_admitted
            else "PARTIAL"
        ),
        "roc_20_pct": roc20,
        "roc_60_pct": roc60,
        "macd": {
            "dif": _safe_float(latest.get("MACD_DIF")) if macd_admitted else None,
            "dea": _safe_float(latest.get("MACD_DEA")) if macd_admitted else None,
            "bar": _safe_float(latest.get("MACD_BAR")) if macd_admitted else None,
            "status": macd_status if macd_admitted else None,
            "formula_owner": "StockTrendAnalyzer._calculate_macd",
            "admission": macd_receipt,
            "raw_observation": {"status": macd_status, "admitted": False}
            if not macd_admitted else None,
        },
        "rsi": {
            "rsi_6": _safe_float(latest.get("RSI_6")) if rsi_admitted else None,
            "rsi_12": _safe_float(latest.get("RSI_12")) if rsi_admitted else None,
            "rsi_24": _safe_float(latest.get("RSI_24")) if rsi_admitted else None,
            "status": rsi_status if rsi_admitted else None,
            "formula_owner": "StockTrendAnalyzer._calculate_rsi",
            "admission": rsi_receipt,
            "raw_observation": {"status": rsi_status, "admitted": False}
            if not rsi_admitted else None,
        },
    }
    volatility = {
        "status": "READY" if (
            atr_admitted and all(
                v is not None for v in (realized_vol, tr_sma_pct)
            )
        ) else "PARTIAL",
        "realized_volatility_20d_annualized_pct": realized_vol,
        "true_range_sma_20_pct": tr_sma_pct,
        "true_range_semantics": "SIMPLE_MEAN_TRUE_RANGE_20_NOT_WILDER_ATR",
        "atr_wilder_14": atr_wilder if atr_admitted else None,
        "atr_wilder_14_pct": atr_wilder_pct if atr_admitted else None,
        "atr_wilder_admission": atr_receipt,
        "atr_wilder_raw_observation": {
            "value": atr_wilder, "pct": atr_wilder_pct, "admitted": False,
        } if not atr_admitted else None,
        "atr_wilder_period": WILDER_ATR_PERIOD,
        "atr_wilder_semantics": "TR_TRANSITIONS_1_TO_14_MEAN_THEN_WILDER_RMA_ALPHA_1_OVER_14",
        "legacy_screening_alias_note": "screening atr_20_pct uses the same TR-SMA/close formula",
    }
    process = _process_diagnostics(close)
    process["status"] = "READY" if len(frame) >= READY_OBSERVATIONS else "PARTIAL"
    structure_target = (
        str(price_structure_context.get("target_date") or "")
        if isinstance(price_structure_context, dict)
        else ""
    )
    if structure_target != target_date.isoformat():
        divergence = {
            "status": "MISSING",
            "state": "NONE",
            "reason": "PRICE_STRUCTURE_TARGET_DATE_UNPROVEN",
            "signals": [],
            "independent_confirmation_group_count": 0,
        }
    else:
        admissible_indicators = indicators.copy()
        if not macd_admitted:
            admissible_indicators["MACD_DIF"] = float("nan")
        if not rsi_admitted:
            admissible_indicators["RSI_12"] = float("nan")
        divergence = _confirmed_divergence(admissible_indicators, price_structure_context)
        if not (macd_admitted or rsi_admitted):
            divergence = {
                "status": "MISSING", "state": "NONE", "signals": [],
                "reason": "RECURSIVE_OSCILLATORS_NOT_ADMITTED",
                "independent_confirmation_group_count": 0,
            }

    if alignment["status"] != "SINGLE_SOURCE":
        status = "PARTIAL"
        reason = "SOURCE_ALIGNMENT_UNPROVEN"
    elif len(frame) < READY_OBSERVATIONS:
        status = "PARTIAL"
        reason = "READY_WARMUP_INSUFFICIENT"
    elif trend_result is None:
        status = "PARTIAL"
        reason = "CANONICAL_TREND_PROJECTION_MISSING"
    elif not (macd_admitted and rsi_admitted and atr_admitted):
        status = "PARTIAL"
        reason = "RECURSIVE_METHOD_ADMISSION_INCOMPLETE"
    else:
        status = "READY"
        reason = "VOLATILITY_MOMENTUM_READY"

    return {
        **base,
        "status": status,
        "reason": reason,
        "observations": int(len(frame)),
        "start_date": frame.iloc[0]["date"].isoformat(),
        "end_date": frame.iloc[-1]["date"].isoformat(),
        "source_alignment": alignment,
        "volatility": volatility,
        "momentum": momentum,
        "recursive_startup_admission": recursive,
        "confirmed_divergence": divergence,
        "process_diagnostics": process,
        "historical_replay_eligible": status == "READY",
    }
