# -*- coding: utf-8 -*-
"""Deterministic source-bound extended OHLCV indicators.

This owner computes the eleven previously deferred numeric leaves on completed
bars only.  It never fetches data, grants data rights, creates a strategy vote,
persists history, or promotes model features.  Every output remains bound to an
explicit source identity, completed-bar clock and source volume unit.
"""
from __future__ import annotations

from hashlib import sha256
import json
import math
from numbers import Real
from typing import Any, Mapping, Optional

import pandas as pd


SCHEMA_VERSION = "extended-indicator-evidence-v1"
ALGORITHM_VERSION = "deterministic-ohlcv-extra-indicators-v1"
ADX_DMI_PERIOD = 14
BOLLINGER_PERIOD = 20
BOLLINGER_STD_MULTIPLIER = 2.0
KDJ_PERIOD = 9
KDJ_K_SMOOTHING = 3
KDJ_D_SMOOTHING = 3
OBV_ADL_WINDOW = 60
MFI_PERIOD = 14
READY_OBSERVATIONS = 160
SUPPORTED_TIMEFRAMES = ("monthly", "weekly", "daily", "60m", "30m", "15m", "5m")
LEAF_NAMES = (
    "adx", "plus_di", "minus_di", "bollinger_upper", "bollinger_lower",
    "kdj_k", "kdj_d", "kdj_j", "obv", "adl", "mfi",
)
_CONFIG = {
    "algorithm_version": ALGORITHM_VERSION,
    "adx_dmi_period": ADX_DMI_PERIOD,
    "bollinger_period": BOLLINGER_PERIOD,
    "bollinger_std_multiplier": BOLLINGER_STD_MULTIPLIER,
    "kdj_period": KDJ_PERIOD,
    "kdj_k_smoothing": KDJ_K_SMOOTHING,
    "kdj_d_smoothing": KDJ_D_SMOOTHING,
    "obv_adl_window": OBV_ADL_WINDOW,
    "mfi_period": MFI_PERIOD,
    "ready_observations": READY_OBSERVATIONS,
    "supported_timeframes": list(SUPPORTED_TIMEFRAMES),
    "correlation_groups": {
        "ADX_DMI": "same_swing_directional_strength",
        "BOLLINGER": "same_price_dispersion_envelope",
        "KDJ": "same_swing_momentum",
        "OBV_ADL": "same_volume_flow",
        "MFI": "same_volume_flow",
    },
}
CONFIG_HASH = sha256(
    json.dumps(_CONFIG, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _valid_sha256(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def _method(
    method_id: str,
    *,
    status: str,
    reason: str,
    values: Mapping[str, Any],
    admission: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "method_id": method_id,
        "status": status,
        "reason": reason,
        "values": dict(values),
        "admission": dict(admission or {}),
        "correlation_group": _CONFIG["correlation_groups"][method_id],
        "independent_action_authority": False,
        "strategy_admitted": False,
        "learning_admitted": False,
        "historical_pit_eligible": False,
    }


def _empty(
    base: Mapping[str, Any], *, status: str, reason: str, observations: int = 0,
) -> dict[str, Any]:
    methods = {
        method_id: _method(
            method_id,
            status="MISSING" if status == "MISSING" else "UNKNOWN",
            reason=reason,
            values={},
        )
        for method_id in ("ADX_DMI", "BOLLINGER", "KDJ", "OBV_ADL", "MFI")
    }
    return {
        **dict(base),
        "status": status,
        "reason": reason,
        "observations": observations,
        "methods": methods,
        "values": {name: None for name in LEAF_NAMES},
    }


def _normalize(
    bars: Any,
    *,
    timeframe: str,
    target_bar_end: Any,
) -> tuple[pd.DataFrame | None, str | None]:
    required = ("date", "open", "high", "low", "close", "volume", "data_source")
    if not isinstance(bars, pd.DataFrame) or bars.empty:
        return None, "COMPLETED_OHLCV_INPUT_MISSING"
    frame = bars.copy()
    frame.columns = [str(column).lower() for column in frame.columns]
    if any(column not in frame.columns for column in required):
        return None, "COMPLETED_OHLCV_FIELDS_MISSING"
    frame = frame[list(required)].copy()
    intraday = timeframe in {"60m", "30m", "15m", "5m"}
    if intraday:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce", utc=True)
        target = pd.to_datetime(target_bar_end, errors="coerce", utc=True)
    else:
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
        parsed = pd.to_datetime(target_bar_end, errors="coerce")
        target = parsed.date() if not pd.isna(parsed) else None
    if frame["date"].isna().any() or target is None or pd.isna(target):
        return None, "BAR_TIMESTAMP_INVALID"
    if frame["date"].duplicated().any() or not frame["date"].is_monotonic_increasing:
        return None, "BAR_TIME_DUPLICATE_OR_OUT_OF_ORDER"
    if frame.iloc[-1]["date"] != target:
        return None, "TARGET_COMPLETED_BAR_MISSING"
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame[["open", "high", "low", "close", "volume"]].isna().any().any():
        return None, "OHLCV_NON_NUMERIC_OR_NONFINITE"
    if not all(
        math.isfinite(float(value))
        for value in frame[["open", "high", "low", "close", "volume"]].to_numpy().ravel()
    ):
        return None, "OHLCV_NON_NUMERIC_OR_NONFINITE"
    invalid = (
        (frame["open"] <= 0)
        | (frame["high"] <= 0)
        | (frame["low"] <= 0)
        | (frame["close"] <= 0)
        | (frame["volume"] < 0)
        | (frame["high"] < frame["low"])
        | (frame["high"] < frame["open"])
        | (frame["high"] < frame["close"])
        | (frame["low"] > frame["open"])
        | (frame["low"] > frame["close"])
    )
    if bool(invalid.any()):
        return None, "INVALID_OHLCV_GEOMETRY_OR_UNIT"
    sources = frame["data_source"].map(lambda value: str(value or "").strip())
    if not bool((sources != "").all()) or sources.nunique() != 1:
        return None, "SOURCE_ALIGNMENT_UNPROVEN"
    frame["data_source"] = sources
    return frame.reset_index(drop=True), None


def _adx_dmi(frame: pd.DataFrame, period: int = ADX_DMI_PERIOD) -> dict[str, float] | None:
    if len(frame) < 2 * period + 1:
        return None
    high = frame["high"].astype(float).tolist()
    low = frame["low"].astype(float).tolist()
    close = frame["close"].astype(float).tolist()
    tr: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for index in range(1, len(frame)):
        up = high[index] - high[index - 1]
        down = low[index - 1] - low[index]
        tr.append(max(
            high[index] - low[index],
            abs(high[index] - close[index - 1]),
            abs(low[index] - close[index - 1]),
        ))
        plus_dm.append(up if up > down and up > 0 else 0.0)
        minus_dm.append(down if down > up and down > 0 else 0.0)
    tr_sum = sum(tr[:period])
    plus_sum = sum(plus_dm[:period])
    minus_sum = sum(minus_dm[:period])
    if tr_sum <= 0:
        return None
    dx_values: list[float] = []
    plus_di = minus_di = 0.0
    for index in range(period - 1, len(tr)):
        if index > period - 1:
            tr_sum = tr_sum - tr_sum / period + tr[index]
            plus_sum = plus_sum - plus_sum / period + plus_dm[index]
            minus_sum = minus_sum - minus_sum / period + minus_dm[index]
        if tr_sum <= 0:
            return None
        plus_di = 100.0 * plus_sum / tr_sum
        minus_di = 100.0 * minus_sum / tr_sum
        denominator = plus_di + minus_di
        dx_values.append(
            100.0 * abs(plus_di - minus_di) / denominator if denominator > 0 else 0.0
        )
    if len(dx_values) < period:
        return None
    adx = sum(dx_values[:period]) / period
    for dx in dx_values[period:]:
        adx = (adx * (period - 1) + dx) / period
    result = {"adx": adx, "plus_di": plus_di, "minus_di": minus_di}
    return result if all(_finite(value) is not None for value in result.values()) else None


def _bollinger(frame: pd.DataFrame) -> dict[str, float] | None:
    if len(frame) < BOLLINGER_PERIOD:
        return None
    window = frame["close"].astype(float).iloc[-BOLLINGER_PERIOD:]
    middle = float(window.mean())
    standard_deviation = float(window.std(ddof=0))
    result = {
        "bollinger_upper": middle + BOLLINGER_STD_MULTIPLIER * standard_deviation,
        "bollinger_lower": middle - BOLLINGER_STD_MULTIPLIER * standard_deviation,
    }
    return result if all(_finite(value) is not None for value in result.values()) else None


def _kdj(frame: pd.DataFrame) -> dict[str, float] | None:
    if len(frame) < KDJ_PERIOD:
        return None
    high = frame["high"].astype(float)
    low = frame["low"].astype(float)
    close = frame["close"].astype(float)
    k_value = 50.0
    d_value = 50.0
    observed = False
    for index in range(KDJ_PERIOD - 1, len(frame)):
        highest = float(high.iloc[index - KDJ_PERIOD + 1:index + 1].max())
        lowest = float(low.iloc[index - KDJ_PERIOD + 1:index + 1].min())
        denominator = highest - lowest
        if denominator <= 0:
            return None
        rsv = (float(close.iloc[index]) - lowest) / denominator * 100.0
        k_value = ((KDJ_K_SMOOTHING - 1) * k_value + rsv) / KDJ_K_SMOOTHING
        d_value = ((KDJ_D_SMOOTHING - 1) * d_value + k_value) / KDJ_D_SMOOTHING
        observed = True
    if not observed:
        return None
    result = {
        "kdj_k": k_value,
        "kdj_d": d_value,
        "kdj_j": 3.0 * k_value - 2.0 * d_value,
    }
    return result if all(_finite(value) is not None for value in result.values()) else None


def _obv_adl(frame: pd.DataFrame) -> tuple[dict[str, float] | None, str | None]:
    if len(frame) < OBV_ADL_WINDOW:
        return None, "OBV_ADL_WARMUP_INSUFFICIENT"
    window = frame.iloc[-OBV_ADL_WINDOW:].reset_index(drop=True)
    close = window["close"].astype(float)
    high = window["high"].astype(float)
    low = window["low"].astype(float)
    volume = window["volume"].astype(float)
    obv = 0.0
    adl = 0.0
    for index in range(len(window)):
        if index:
            obv += (
                volume.iloc[index]
                if close.iloc[index] > close.iloc[index - 1]
                else -volume.iloc[index]
                if close.iloc[index] < close.iloc[index - 1]
                else 0.0
            )
        bar_range = high.iloc[index] - low.iloc[index]
        if bar_range <= 0:
            return None, "ADL_ZERO_RANGE_BAR"
        multiplier = (
            (close.iloc[index] - low.iloc[index])
            - (high.iloc[index] - close.iloc[index])
        ) / bar_range
        adl += multiplier * volume.iloc[index]
    result = {"obv": float(obv), "adl": float(adl)}
    return (
        result if all(_finite(value) is not None for value in result.values()) else None,
        None,
    )


def _mfi(frame: pd.DataFrame) -> dict[str, float] | None:
    if len(frame) < MFI_PERIOD + 1:
        return None
    window = frame.iloc[-(MFI_PERIOD + 1):].reset_index(drop=True)
    typical = (
        window["high"].astype(float)
        + window["low"].astype(float)
        + window["close"].astype(float)
    ) / 3.0
    volume = window["volume"].astype(float)
    positive = 0.0
    negative = 0.0
    for index in range(1, len(window)):
        raw = float(typical.iloc[index] * volume.iloc[index])
        if typical.iloc[index] > typical.iloc[index - 1]:
            positive += raw
        elif typical.iloc[index] < typical.iloc[index - 1]:
            negative += raw
    total = positive + negative
    if total <= 0:
        return None
    value = 100.0 * positive / total
    return {"mfi": value} if _finite(value) is not None else None


def _recursive_stability(
    frame: pd.DataFrame,
    *,
    method_id: str,
    compute: Any,
    absolute_tolerance: float,
) -> dict[str, Any]:
    receipt = {
        "policy_version": "observed-completed-suffix-startup-v1",
        "method": method_id,
        "status": "MISSING",
        "reason": method_id + "_STARTUP_HISTORY_INSUFFICIENT",
        "observations": int(len(frame)),
        "required_observations": READY_OBSERVATIONS,
        "evaluated_startup_lengths": [],
        "absolute_tolerance": absolute_tolerance,
        "max_endpoint_difference": None,
        "numeric_agreement": None,
        "unobserved_prehistory_convergence": "NOT_PROVEN",
        "strategy_admitted": False,
        "learning_admitted": False,
    }
    if len(frame) < READY_OBSERVATIONS:
        return receipt
    lengths = tuple(sorted({len(frame) // 2, len(frame) * 2 // 3, len(frame) * 5 // 6}))
    try:
        reference = compute(frame)
        if not isinstance(reference, Mapping) or not reference:
            raise ValueError("REFERENCE_MISSING")
        max_difference = 0.0
        for length in lengths:
            candidate = compute(frame.iloc[-length:].reset_index(drop=True))
            if not isinstance(candidate, Mapping) or set(candidate) != set(reference):
                raise ValueError("STARTUP_SCHEMA_MISMATCH")
            difference = max(
                abs(float(reference[key]) - float(candidate[key])) for key in reference
            )
            if not math.isfinite(difference):
                raise ValueError("STARTUP_NONFINITE")
            max_difference = max(max_difference, difference)
        admitted = max_difference <= absolute_tolerance
    except (TypeError, ValueError, ArithmeticError, IndexError):
        return {**receipt, "status": "UNKNOWN", "reason": method_id + "_STARTUP_NATIVE_INVALID"}
    return {
        **receipt,
        "status": "READY" if admitted else "UNKNOWN",
        "reason": (
            method_id + "_OBSERVED_STARTS_AGREE"
            if admitted else method_id + "_STARTUP_NUMERIC_DRIFT"
        ),
        "evaluated_startup_lengths": [*lengths, len(frame)],
        "max_endpoint_difference": max_difference,
        "numeric_agreement": admitted,
        "reference_values": (
            {key: float(value) for key, value in reference.items()} if admitted else None
        ),
    }


def build_extended_indicator_context(
    *,
    stock_code: str,
    bars: Any,
    timeframe: str,
    target_bar_end: Any,
    source_identity_hash: Optional[str],
    volume_unit: Optional[str],
    market: Optional[str] = None,
) -> dict[str, Any]:
    """Compute eleven source-bound leaves without granting action or PIT authority."""
    base = {
        "schema_version": SCHEMA_VERSION,
        "family": "extended_indicators",
        "stock_code": str(stock_code or "").strip(),
        "market": str(market or "").strip().lower() or None,
        "timeframe": timeframe,
        "completed_bar_only": True,
        "algorithm_version": ALGORITHM_VERSION,
        "config_hash": CONFIG_HASH,
        "source_identity_hash": source_identity_hash,
        "volume_unit": str(volume_unit or "").strip(),
        "independent_action_authority": False,
        "strategy_admitted": False,
        "learning_admitted": False,
        "data_rights_admitted": False,
        "historical_pit_eligible": False,
    }
    if timeframe not in SUPPORTED_TIMEFRAMES:
        return _empty(base, status="UNKNOWN", reason="TIMEFRAME_UNSUPPORTED")
    if not _valid_sha256(source_identity_hash):
        return _empty(base, status="UNKNOWN", reason="TYPED_SOURCE_IDENTITY_MISSING")
    frame, error = _normalize(
        bars, timeframe=timeframe, target_bar_end=target_bar_end,
    )
    if error or frame is None:
        return _empty(
            base,
            status="MISSING" if error in {
                "COMPLETED_OHLCV_INPUT_MISSING", "COMPLETED_OHLCV_FIELDS_MISSING",
                "TARGET_COMPLETED_BAR_MISSING",
            } else "UNKNOWN",
            reason=error or "OHLCV_NORMALIZATION_FAILED",
            observations=int(len(bars)) if isinstance(bars, pd.DataFrame) else 0,
        )
    explicit_volume_unit = str(volume_unit or "").strip().upper()
    volume_semantics_ready = explicit_volume_unit not in {"", "UNKNOWN", "UNSPECIFIED", "MISSING"}

    adx_values = _adx_dmi(frame)
    adx_admission = _recursive_stability(
        frame,
        method_id="ADX_DMI",
        compute=_adx_dmi,
        absolute_tolerance=1.0,
    )
    if adx_admission.get("status") != "READY":
        adx_values = None
    bollinger_values = _bollinger(frame)
    kdj_values = _kdj(frame)
    kdj_admission = _recursive_stability(
        frame,
        method_id="KDJ",
        compute=_kdj,
        absolute_tolerance=0.5,
    )
    if kdj_admission.get("status") != "READY":
        kdj_values = None
    obv_adl_values, obv_adl_reason = (
        _obv_adl(frame) if volume_semantics_ready
        else (None, "VOLUME_UNIT_UNKNOWN")
    )
    mfi_values = _mfi(frame) if volume_semantics_ready else None

    methods = {
        "ADX_DMI": _method(
            "ADX_DMI",
            status="READY" if adx_values is not None else str(adx_admission.get("status") or "MISSING"),
            reason=(
                "ADX_DMI_SOURCE_BOUND_READY"
                if adx_values is not None else str(adx_admission.get("reason") or "ADX_DMI_NOT_READY")
            ),
            values=adx_values or {},
            admission=adx_admission,
        ),
        "BOLLINGER": _method(
            "BOLLINGER",
            status="READY" if bollinger_values is not None else "MISSING",
            reason="BOLLINGER_20_READY" if bollinger_values is not None else "BOLLINGER_WARMUP_INSUFFICIENT",
            values=bollinger_values or {},
        ),
        "KDJ": _method(
            "KDJ",
            status="READY" if kdj_values is not None else str(kdj_admission.get("status") or "MISSING"),
            reason=(
                "KDJ_SOURCE_BOUND_READY"
                if kdj_values is not None else str(kdj_admission.get("reason") or "KDJ_NOT_READY")
            ),
            values=kdj_values or {},
            admission=kdj_admission,
        ),
        "OBV_ADL": _method(
            "OBV_ADL",
            status="READY" if obv_adl_values is not None else (
                "UNKNOWN" if not volume_semantics_ready else "MISSING"
            ),
            reason=(
                "OBV_ADL_FIXED_60_BAR_SOURCE_VOLUME_READY"
                if obv_adl_values is not None else obv_adl_reason or "OBV_ADL_NOT_READY"
            ),
            values=obv_adl_values or {},
        ),
        "MFI": _method(
            "MFI",
            status="READY" if mfi_values is not None else (
                "UNKNOWN" if not volume_semantics_ready else "MISSING"
            ),
            reason=(
                "MFI_14_SOURCE_VOLUME_READY"
                if mfi_values is not None else
                "VOLUME_UNIT_UNKNOWN" if not volume_semantics_ready else "MFI_FLOW_DENOMINATOR_EMPTY"
            ),
            values=mfi_values or {},
        ),
    }
    values = {name: None for name in LEAF_NAMES}
    for method in methods.values():
        if method["status"] == "READY":
            values.update(method["values"])
    states = tuple(method["status"] for method in methods.values())
    status = (
        "READY" if all(state == "READY" for state in states)
        else "PARTIAL" if any(state == "READY" for state in states)
        else "UNKNOWN" if any(state == "UNKNOWN" for state in states)
        else "MISSING"
    )
    return {
        **base,
        "status": status,
        "reason": (
            "ALL_EXTENDED_INDICATOR_METHODS_READY"
            if status == "READY" else "EXTENDED_INDICATOR_METHOD_COVERAGE_INCOMPLETE"
        ),
        "observations": int(len(frame)),
        "completed_through": frame.iloc[-1]["date"].isoformat(),
        "source_alignment": {
            "status": "SINGLE_SOURCE",
            "sources": [str(frame.iloc[-1]["data_source"])],
            "rows_complete": True,
        },
        "methods": methods,
        "values": values,
        "correlation_group_policy": {
            "same_group_double_counting": "PROHIBITED",
            "groups": dict(_CONFIG["correlation_groups"]),
        },
    }
