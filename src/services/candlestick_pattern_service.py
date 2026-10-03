# -*- coding: utf-8 -*-
"""Deterministic Candlestick/K-line geometry as a nested Pattern/Trigger leaf.

This owner is deliberately narrow: completed OHLC geometry, confirmed structural
location, and a replay-safe confirmation/failure lifecycle. It never fetches
data, never names a candle as an action, and never owns canonical BUY/SELL.
"""

from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from math import isfinite
from typing import Any, Dict, List, Optional

import pandas as pd


CANDLESTICK_SCHEMA_VERSION = "candlestick-pattern-v1"
ALGORITHM_VERSION = "candlestick-geometry-location-lifecycle-v1"
MIN_OBSERVATIONS = 2
SOURCE_ALIGNMENT_POLICY = "FULL_NORMALIZED_HISTORY"
DOJI_BODY_RANGE_MAX = 0.10
REJECTION_DOMINANT_WICK_BODY_MIN = 2.0
REJECTION_OPPOSITE_WICK_BODY_MAX = 0.5
MAX_EVENTS = 8

_CONFIG = {
    "algorithm_version": ALGORITHM_VERSION,
    "minimum_observations": MIN_OBSERVATIONS,
    "source_alignment_policy": SOURCE_ALIGNMENT_POLICY,
    "doji_body_range_max": DOJI_BODY_RANGE_MAX,
    "rejection_dominant_wick_body_min": REJECTION_DOMINANT_WICK_BODY_MIN,
    "rejection_opposite_wick_body_max": REJECTION_OPPOSITE_WICK_BODY_MAX,
    "max_events": MAX_EVENTS,
    "parameter_policy": "VERSIONED_CANDIDATE_PARAMETERS_NOT_UNIVERSAL_CONSTANTS",
}
CONFIG_HASH = sha256(
    json.dumps(_CONFIG, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()

_DIRECTIONAL_PRIORITY = {
    "BULLISH_ENGULFING": 20,
    "BEARISH_ENGULFING": 20,
    "LOWER_REJECTION": 10,
    "UPPER_REJECTION": 10,
}


def _safe_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _parse_date(value: Any) -> Optional[date]:
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


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
    if "volume" in frame.columns:
        frame["volume"] = pd.to_numeric(frame["volume"], errors="coerce")
    frame = frame.dropna(subset=required)
    frame = frame[frame["date"] <= target_date]
    frame = frame.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    optional = [column for column in ("volume", "data_source") if column in frame.columns]
    return frame[required + optional].reset_index(drop=True)


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
    base = {
        "coverage": SOURCE_ALIGNMENT_POLICY,
        "observations": int(len(frame)),
        "start_date": frame.iloc[0]["date"].isoformat() if not frame.empty else None,
        "end_date": frame.iloc[-1]["date"].isoformat() if not frame.empty else None,
    }
    if "data_source" not in frame.columns:
        return {**base, "status": "UNPROVEN", "sources": [], "rows_complete": False}
    values = frame["data_source"].map(
        lambda value: str(value).strip() if value not in (None, "") else ""
    )
    sources = sorted({value for value in values.tolist() if value})
    complete = bool((values != "").all())
    return {
        **base,
        "status": "SINGLE_SOURCE" if len(sources) == 1 and complete else "UNPROVEN",
        "sources": sources,
        "rows_complete": complete,
    }


def _alignment_matches(history_alignment: Dict[str, Any], structure_alignment: Dict[str, Any]) -> bool:
    keys = ("coverage", "observations", "start_date", "end_date", "rows_complete")
    return bool(
        history_alignment.get("status") == "SINGLE_SOURCE"
        and structure_alignment.get("status") == "SINGLE_SOURCE"
        and sorted(history_alignment.get("sources") or [])
        == sorted(structure_alignment.get("sources") or [])
        and all(history_alignment.get(key) == structure_alignment.get(key) for key in keys)
    )


def _geometry(row: pd.Series, previous: Optional[pd.Series]) -> Dict[str, Any]:
    open_value = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    close = float(row["close"])
    range_value = high - low
    body = abs(close - open_value)
    upper = high - max(open_value, close)
    lower = min(open_value, close) - low
    body_ratio = body / range_value if range_value > 0 else 0.0
    upper_to_body = upper / body if body > 0 else None
    lower_to_body = lower / body if body > 0 else None

    neutral: List[str] = []
    directional: List[Dict[str, str]] = []
    if range_value > 0 and body_ratio <= DOJI_BODY_RANGE_MAX:
        neutral.append("DOJI_INDECISION")
    if body > 0:
        if (
            lower_to_body is not None
            and upper_to_body is not None
            and lower_to_body >= REJECTION_DOMINANT_WICK_BODY_MIN
            and upper_to_body <= REJECTION_OPPOSITE_WICK_BODY_MAX
        ):
            directional.append({"event_type": "LOWER_REJECTION", "direction": "BULLISH"})
        if (
            upper_to_body is not None
            and lower_to_body is not None
            and upper_to_body >= REJECTION_DOMINANT_WICK_BODY_MIN
            and lower_to_body <= REJECTION_OPPOSITE_WICK_BODY_MAX
        ):
            directional.append({"event_type": "UPPER_REJECTION", "direction": "BEARISH"})

    if previous is not None:
        previous_open = float(previous["open"])
        previous_close = float(previous["close"])
        previous_high = float(previous["high"])
        previous_low = float(previous["low"])
        if high <= previous_high and low >= previous_low:
            neutral.append("INSIDE_BAR")
        if high >= previous_high and low <= previous_low:
            neutral.append("OUTSIDE_BAR")
        if (
            close > open_value
            and previous_close < previous_open
            and open_value <= previous_close
            and close >= previous_open
        ):
            directional.append({"event_type": "BULLISH_ENGULFING", "direction": "BULLISH"})
        if (
            close < open_value
            and previous_close > previous_open
            and open_value >= previous_close
            and close <= previous_open
        ):
            directional.append({"event_type": "BEARISH_ENGULFING", "direction": "BEARISH"})

    return {
        "range": round(range_value, 6),
        "body": round(body, 6),
        "upper_wick": round(upper, 6),
        "lower_wick": round(lower, 6),
        "body_to_range": round(body_ratio, 6) if range_value > 0 else None,
        "upper_wick_to_body": round(upper_to_body, 6) if upper_to_body is not None else None,
        "lower_wick_to_body": round(lower_to_body, 6) if lower_to_body is not None else None,
        "neutral_geometry": list(dict.fromkeys(neutral)),
        "directional_candidates": directional,
    }


def _latest_confirmed(items: List[Dict[str, Any]], *, origin: date) -> Optional[Dict[str, Any]]:
    eligible = []
    for item in items:
        confirmed = _parse_date(item.get("confirmed_at"))
        if confirmed is not None and confirmed <= origin:
            eligible.append(item)
    if not eligible:
        return None
    return max(
        eligible,
        key=lambda item: (
            str(item.get("confirmed_at") or ""),
            str(item.get("origin_time") or ""),
        ),
    )


def _context_for_direction(
    price_structure_context: Dict[str, Any],
    *,
    origin: date,
    row: pd.Series,
    direction: str,
) -> Optional[Dict[str, Any]]:
    swings = [
        item
        for item in (price_structure_context.get("swings") or [])
        if isinstance(item, dict)
    ]
    pivots = [
        item
        for item in (price_structure_context.get("pivots") or [])
        if isinstance(item, dict) and not bool(item.get("provisional"))
    ]
    prior_swing = _latest_confirmed(swings, origin=origin)
    if not isinstance(prior_swing, dict):
        return None
    required_swing = "DOWN" if direction == "BULLISH" else "UP"
    if str(prior_swing.get("direction") or "") != required_swing:
        return None

    required_kind = "LOW" if direction == "BULLISH" else "HIGH"
    known_pivots = [item for item in pivots if str(item.get("kind") or "") == required_kind]
    location = _latest_confirmed(known_pivots, origin=origin)
    if not isinstance(location, dict):
        return None
    level = _safe_float(location.get("price"))
    if level is None or level <= 0:
        return None
    if direction == "BULLISH":
        touches = float(row["low"]) <= level <= float(row["close"])
        location_state = "AT_CONFIRMED_SUPPORT"
    else:
        touches = float(row["high"]) >= level >= float(row["close"])
        location_state = "AT_CONFIRMED_RESISTANCE"
    if not touches:
        return None
    return {
        "prior_swing_ref": {
            key: prior_swing.get(key)
            for key in (
                "direction",
                "origin_time",
                "confirmed_at",
                "start_kind",
                "start_price",
                "end_kind",
                "end_price",
            )
        },
        "location_ref": {
            "state": location_state,
            "kind": required_kind,
            "price": round(level, 6),
            "origin_time": location.get("origin_time"),
            "confirmed_at": location.get("confirmed_at"),
        },
        "correlation_group": (
            f"confirmed_price_swing:{required_kind}:"
            f"{location.get('origin_time')}:{location.get('confirmed_at')}"
        ),
    }


def _volume_ref(
    supply_demand_context: Any,
    *,
    origin: date,
    target_date: date,
    direction: str,
) -> Dict[str, Any]:
    if origin != target_date:
        return {
            "status": "MISSING",
            "reason": "HISTORICAL_ORIGIN_VOLUME_CONTEXT_NOT_BOUND",
            "conflict": False,
        }
    if not isinstance(supply_demand_context, dict):
        return {
            "status": "MISSING",
            "reason": "SUPPLY_DEMAND_CONTEXT_MISSING",
            "conflict": False,
        }
    if str(supply_demand_context.get("target_date") or "") != target_date.isoformat():
        return {
            "status": "MISSING",
            "reason": "SUPPLY_DEMAND_TARGET_DATE_MISMATCH",
            "conflict": False,
        }
    state = str(supply_demand_context.get("state") or "").upper()
    conflict = bool(
        (direction == "BULLISH" and state == "SUPPLY_PRESSURE")
        or (direction == "BEARISH" and state == "DEMAND_PRESSURE")
    )
    return {
        "status": str(supply_demand_context.get("status") or "MISSING").upper(),
        "target_date": supply_demand_context.get("target_date"),
        "state": state or None,
        "conflict": conflict,
        "reason": "DIRECTIONAL_VOLUME_CONTEXT_CONFLICT" if conflict else "TARGET_DATE_CONTEXT_ONLY",
    }


def _lifecycle(frame: pd.DataFrame, *, index: int, direction: str) -> Dict[str, Any]:
    origin = frame.iloc[index]
    high = float(origin["high"])
    low = float(origin["low"])
    for future_index in range(index + 1, len(frame)):
        row = frame.iloc[future_index]
        close = float(row["close"])
        when = row["date"].isoformat()
        if direction == "BULLISH":
            if close > high:
                return {
                    "lifecycle": "CONFIRMED",
                    "confirmed_at": when,
                    "invalidated_at": None,
                    "provisional": False,
                }
            if close < low:
                return {
                    "lifecycle": "FAILED",
                    "confirmed_at": None,
                    "invalidated_at": when,
                    "provisional": False,
                }
        else:
            if close < low:
                return {
                    "lifecycle": "CONFIRMED",
                    "confirmed_at": when,
                    "invalidated_at": None,
                    "provisional": False,
                }
            if close > high:
                return {
                    "lifecycle": "FAILED",
                    "confirmed_at": None,
                    "invalidated_at": when,
                    "provisional": False,
                }
    return {
        "lifecycle": "FORMING",
        "confirmed_at": None,
        "invalidated_at": None,
        "provisional": True,
    }


def _event_summary(event: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(event, dict) or event.get("material") is not True:
        return None
    label = {
        "LOWER_REJECTION": "下影拒绝",
        "UPPER_REJECTION": "上影拒绝",
        "BULLISH_ENGULFING": "看涨吞没",
        "BEARISH_ENGULFING": "看跌吞没",
    }.get(str(event.get("event_type") or ""), "K线结构")
    state = {
        "FORMING": "形成中",
        "CONFIRMED": "已确认",
        "FAILED": "已失败",
    }.get(str(event.get("lifecycle") or ""), "状态未知")
    location = {
        "AT_CONFIRMED_SUPPORT": "确认支撑附近",
        "AT_CONFIRMED_RESISTANCE": "确认压力附近",
    }.get(str((event.get("location_ref") or {}).get("state") or ""), "已确认结构位置")
    return f"日线{label}在{location}{state}；仅作结构上下文，不独立构成操作信号"


def build_candlestick_pattern_context(
    *,
    stock_code: str,
    history: Any,
    target_date: Optional[date],
    market: Optional[str] = None,
    price_structure_context: Any = None,
    supply_demand_context: Any = None,
) -> Dict[str, Any]:
    base = {
        "schema_version": CANDLESTICK_SCHEMA_VERSION,
        "family": "pattern_trigger_leaf",
        "method_id": "CANDLESTICK",
        "stock_code": stock_code,
        "market": str(market or "").lower() or None,
        "timeframe": "1d",
        "target_date": target_date.isoformat() if isinstance(target_date, date) else None,
        "completed_bar_only": True,
        "algorithm_version": ALGORITHM_VERSION,
        "config_hash": CONFIG_HASH,
        "effective_parameters": dict(_CONFIG),
        "hard_veto": False,
        "independent_action_authority": False,
        "strategy_admitted": False,
        "learning_admitted": False,
    }
    if not isinstance(target_date, date):
        return {
            **base,
            "status": "UNKNOWN",
            "reason": "TARGET_DATE_UNKNOWN",
            "material": False,
            "historical_replay_eligible": False,
        }

    frame = _normalize_history(history, target_date=target_date)
    if frame.empty:
        return {
            **base,
            "status": "MISSING",
            "reason": "COMPLETED_OHLC_MISSING",
            "material": False,
            "historical_replay_eligible": False,
        }
    if frame.iloc[-1]["date"] != target_date:
        return {
            **base,
            "status": "MISSING",
            "reason": "TARGET_DATE_BAR_MISSING",
            "observations": int(len(frame)),
            "material": False,
            "historical_replay_eligible": False,
        }
    if len(frame) < MIN_OBSERVATIONS:
        return {
            **base,
            "status": "MISSING",
            "reason": "WARMUP_INSUFFICIENT",
            "observations": int(len(frame)),
            "material": False,
            "historical_replay_eligible": False,
        }
    if _invalid_ohlc(frame):
        return {
            **base,
            "status": "UNKNOWN",
            "reason": "INVALID_OHLC",
            "observations": int(len(frame)),
            "material": False,
            "historical_replay_eligible": False,
        }

    alignment = _source_alignment(frame)
    structure = price_structure_context if isinstance(price_structure_context, dict) else {}
    structure_alignment = (
        structure.get("source_alignment")
        if isinstance(structure.get("source_alignment"), dict)
        else {}
    )
    provenance_ready = bool(
        structure.get("status") == "READY"
        and structure.get("historical_replay_eligible") is True
        and str(structure.get("target_date") or "") == target_date.isoformat()
        and _alignment_matches(alignment, structure_alignment)
    )

    target_geometry = _geometry(
        frame.iloc[-1],
        frame.iloc[-2] if len(frame) >= 2 else None,
    )
    if not provenance_ready:
        return {
            **base,
            "status": "PARTIAL",
            "reason": "PRICE_STRUCTURE_PROVENANCE_UNPROVEN",
            "observations": int(len(frame)),
            "start_date": frame.iloc[0]["date"].isoformat(),
            "end_date": frame.iloc[-1]["date"].isoformat(),
            "source_alignment": alignment,
            "target_geometry": target_geometry,
            "events": [],
            "primary_event": None,
            "material": False,
            "summary": None,
            "historical_replay_eligible": False,
        }

    events_by_group: Dict[str, Dict[str, Any]] = {}
    for index in range(1, len(frame)):
        row = frame.iloc[index]
        origin = row["date"]
        geometry = _geometry(row, frame.iloc[index - 1])
        for candidate in geometry["directional_candidates"]:
            direction = candidate["direction"]
            context_ref = _context_for_direction(
                structure,
                origin=origin,
                row=row,
                direction=direction,
            )
            if context_ref is None:
                continue
            volume_ref = _volume_ref(
                supply_demand_context,
                origin=origin,
                target_date=target_date,
                direction=direction,
            )
            lifecycle = _lifecycle(frame, index=index, direction=direction)
            current_transition = bool(
                origin == target_date
                or lifecycle.get("confirmed_at") == target_date.isoformat()
                or lifecycle.get("invalidated_at") == target_date.isoformat()
            )
            event = {
                "event_type": candidate["event_type"],
                "direction": direction,
                "origin_time": origin.isoformat(),
                "geometry_confirmed_at": origin.isoformat(),
                "origin_high": round(float(row["high"]), 6),
                "origin_low": round(float(row["low"]), 6),
                **context_ref,
                **lifecycle,
                "volume_evidence_ref": volume_ref,
                "material": current_transition and not bool(volume_ref.get("conflict")),
                "reason_codes": (
                    ["STRUCTURE_CONTEXT_READY", "VOLUME_CONTEXT_CONFLICT"]
                    if volume_ref.get("conflict")
                    else ["STRUCTURE_CONTEXT_READY"]
                ),
                "algorithm_version": ALGORITHM_VERSION,
                "config_hash": CONFIG_HASH,
                "independent_action_authority": False,
            }
            group = f"{event['correlation_group']}:{origin.isoformat()}:{direction}"
            existing = events_by_group.get(group)
            if (
                existing is None
                or _DIRECTIONAL_PRIORITY[event["event_type"]]
                > _DIRECTIONAL_PRIORITY[existing["event_type"]]
            ):
                events_by_group[group] = event

    events = sorted(
        events_by_group.values(),
        key=lambda item: (
            str(item.get("origin_time") or ""),
            _DIRECTIONAL_PRIORITY.get(str(item.get("event_type") or ""), 0),
        ),
    )[-MAX_EVENTS:]
    material_events = [item for item in events if item.get("material") is True]
    primary = material_events[-1] if material_events else None
    summary = _event_summary(primary)
    return {
        **base,
        "status": "READY",
        "reason": "CANDLESTICK_CONTEXT_READY" if primary else "NO_MATERIAL_CANDLE_EVENT",
        "observations": int(len(frame)),
        "start_date": frame.iloc[0]["date"].isoformat(),
        "end_date": frame.iloc[-1]["date"].isoformat(),
        "source_alignment": alignment,
        "target_geometry": target_geometry,
        "events": events,
        "primary_event": primary,
        "material": primary is not None,
        "summary": summary,
        "historical_replay_eligible": True,
    }
