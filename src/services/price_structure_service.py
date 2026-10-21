# -*- coding: utf-8 -*-
"""Deterministic confirmed-pivot price-structure evidence over completed daily bars.

This V1 owner deliberately stops at reusable structure primitives: confirmed pivots,
alternating swings, active support/resistance levels, and completed-close
breakout/retest/failure states. Classical shapes such as cup-and-handle, VCP, and
double bottoms remain Pattern/Trigger responsibilities.
"""

from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from typing import Any, Dict, List, Optional

import pandas as pd


PRICE_STRUCTURE_SCHEMA_VERSION = "price-structure-v1"
PIVOT_ALGORITHM_VERSION = "confirmed-pivot-v2"
PIVOT_LEFT_BARS = 2
PIVOT_RIGHT_BARS = 2
MIN_OBSERVATIONS = 10
SOURCE_ALIGNMENT_POLICY = "FULL_NORMALIZED_HISTORY"

_CONFIG = {
    "algorithm_version": PIVOT_ALGORITHM_VERSION,
    "left_bars": PIVOT_LEFT_BARS,
    "right_bars": PIVOT_RIGHT_BARS,
    "minimum_observations": MIN_OBSERVATIONS,
    "source_alignment_policy": SOURCE_ALIGNMENT_POLICY,
}
CONFIG_HASH = sha256(
    json.dumps(_CONFIG, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


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
    return frame[columns].reset_index(drop=True)


def _source_alignment(frame: pd.DataFrame) -> Dict[str, Any]:
    base = {
        "coverage": SOURCE_ALIGNMENT_POLICY,
        "observations": int(len(frame)),
        "start_date": frame.iloc[0]["date"].isoformat() if not frame.empty else None,
        "end_date": frame.iloc[-1]["date"].isoformat() if not frame.empty else None,
    }
    if "data_source" not in frame.columns:
        return {**base, "status": "UNPROVEN", "sources": [], "rows_complete": False}
    text = frame["data_source"].map(
        lambda value: str(value).strip() if value not in (None, "") else ""
    )
    sources = sorted({value for value in text.tolist() if value})
    complete = bool((text != "").all())
    status = "SINGLE_SOURCE" if len(sources) == 1 and complete else "UNPROVEN"
    return {
        **base,
        "status": status,
        "sources": sources,
        "rows_complete": complete,
    }


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


def _invalidation(frame: pd.DataFrame, *, pivot: Dict[str, Any]) -> Dict[str, Any]:
    level = float(pivot["price"])
    confirm_index = int(pivot["_confirmed_index"])
    if pivot["kind"] == "HIGH":
        rule = "COMPLETED_CLOSE_ABOVE_LEVEL"
        predicate = lambda value: value > level
    else:
        rule = "COMPLETED_CLOSE_BELOW_LEVEL"
        predicate = lambda value: value < level
    # Preserve completed-close invalidation, but avoid rebuilding a full
    # pandas row for every later bar and every confirmed pivot.
    closes = frame["close"].to_numpy(copy=False)
    dates = frame["date"].tolist()
    for index in range(confirm_index + 1, len(frame)):
        if predicate(float(closes[index])):
            return {
                "rule": rule,
                "state": "TRIGGERED",
                "triggered_at": dates[index].isoformat(),
            }
    return {"rule": rule, "state": "ACTIVE", "triggered_at": None}


def _confirmed_pivots(frame: pd.DataFrame) -> List[Dict[str, Any]]:
    pivots: List[Dict[str, Any]] = []
    left = PIVOT_LEFT_BARS
    right = PIVOT_RIGHT_BARS
    highs = frame["high"].to_numpy(copy=False)
    lows = frame["low"].to_numpy(copy=False)
    dates = frame["date"].tolist()
    for index in range(left, len(frame) - right):
        center_high = float(highs[index])
        center_low = float(lows[index])
        neighbor_indices = list(range(index - left, index)) + list(range(index + 1, index + right + 1))
        is_high = all(center_high > float(highs[item]) for item in neighbor_indices)
        is_low = all(center_low < float(lows[item]) for item in neighbor_indices)
        # A single outside bar can be both a local high and low. V1 treats that
        # geometry as ambiguous instead of inventing an intra-bar ordering.
        if is_high and is_low:
            continue
        kind = "HIGH" if is_high else "LOW" if is_low else None
        if kind is None:
            continue
        price = center_high if kind == "HIGH" else center_low
        record: Dict[str, Any] = {
            "kind": kind,
            "price": round(price, 6),
            "origin_time": dates[index].isoformat(),
            "confirmed_at": dates[index + right].isoformat(),
            "provisional": False,
            "algorithm_version": PIVOT_ALGORITHM_VERSION,
            "config_hash": CONFIG_HASH,
            "_origin_index": index,
            "_confirmed_index": index + right,
        }
        record["invalidation"] = _invalidation(frame, pivot=record)
        pivots.append(record)
    return pivots


def _compress_pivots(pivots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    compressed: List[Dict[str, Any]] = []
    for pivot in pivots:
        if not compressed or compressed[-1]["kind"] != pivot["kind"]:
            compressed.append(pivot)
            continue
        prior = compressed[-1]
        replace = (
            pivot["price"] > prior["price"]
            if pivot["kind"] == "HIGH"
            else pivot["price"] < prior["price"]
        )
        if replace:
            compressed[-1] = pivot
    return compressed


def _swings(pivots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    compressed = _compress_pivots(pivots)
    swings: List[Dict[str, Any]] = []
    for start, end in zip(compressed, compressed[1:]):
        if start["kind"] == end["kind"]:
            continue
        start_price = float(start["price"])
        end_price = float(end["price"])
        swings.append(
            {
                "direction": "UP" if start["kind"] == "LOW" else "DOWN",
                "origin_time": start["origin_time"],
                "confirmed_at": end["confirmed_at"],
                "start_kind": start["kind"],
                "start_price": round(start_price, 6),
                "end_kind": end["kind"],
                "end_price": round(end_price, 6),
                "price_change_pct": round((end_price / start_price - 1.0) * 100.0, 6),
                "provisional": False,
                "algorithm_version": PIVOT_ALGORITHM_VERSION,
                "config_hash": CONFIG_HASH,
            }
        )
    return swings


def _event_for_pivot(frame: pd.DataFrame, pivot: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    level = float(pivot["price"])
    confirm_index = int(pivot["_confirmed_index"])
    closes = frame["close"].to_numpy(copy=False)
    highs = frame["high"].to_numpy(copy=False)
    lows = frame["low"].to_numpy(copy=False)
    dates = frame["date"].tolist()
    break_index: Optional[int] = None
    for index in range(confirm_index + 1, len(frame)):
        previous_close = float(closes[index - 1])
        close = float(closes[index])
        crossed = (
            previous_close <= level and close > level
            if pivot["kind"] == "HIGH"
            else previous_close >= level and close < level
        )
        if crossed:
            break_index = index
            break
    if break_index is None:
        return None

    if pivot["kind"] == "HIGH":
        state = "UP_BREAKOUT"
        invalidation_rule = "COMPLETED_CLOSE_BELOW_BROKEN_RESISTANCE"
    else:
        state = "DOWN_BREAKDOWN"
        invalidation_rule = "COMPLETED_CLOSE_ABOVE_BROKEN_SUPPORT"
    event_index = break_index

    for index in range(break_index + 1, len(frame)):
        close = float(closes[index])
        if pivot["kind"] == "HIGH":
            if close < level:
                state = "FAILED_UP_BREAKOUT"
                event_index = index
                break
            if float(lows[index]) <= level <= close:
                state = "UP_BREAKOUT_RETEST_HOLD"
                event_index = index
        else:
            if close > level:
                state = "FAILED_DOWN_BREAKDOWN"
                event_index = index
                break
            if close <= level <= float(highs[index]):
                state = "DOWN_BREAKDOWN_RETEST_REJECT"
                event_index = index

    return {
        "state": state,
        "level": round(level, 6),
        "level_kind": pivot["kind"],
        "level_origin_time": pivot["origin_time"],
        "level_confirmed_at": pivot["confirmed_at"],
        "event_time": dates[event_index].isoformat(),
        "provisional": False,
        "invalidation": invalidation_rule,
        "algorithm_version": PIVOT_ALGORITHM_VERSION,
        "config_hash": CONFIG_HASH,
        "_event_index": event_index,
    }


def _latest_event(frame: pd.DataFrame, pivots: List[Dict[str, Any]]) -> Dict[str, Any]:
    events = [event for pivot in pivots if (event := _event_for_pivot(frame, pivot)) is not None]
    if not events:
        return {"state": "NONE", "provisional": False}
    latest = max(events, key=lambda item: int(item["_event_index"]))
    return {key: value for key, value in latest.items() if not key.startswith("_")}


def _active_level(pivots: List[Dict[str, Any]], *, kind: str, close: float) -> Optional[Dict[str, Any]]:
    active = [
        pivot
        for pivot in pivots
        if pivot["kind"] == kind and pivot["invalidation"]["state"] == "ACTIVE"
    ]
    if kind == "LOW":
        eligible = [pivot for pivot in active if float(pivot["price"]) <= close]
        chosen = max(eligible, key=lambda item: float(item["price"]), default=None)
    else:
        eligible = [pivot for pivot in active if float(pivot["price"]) >= close]
        chosen = min(eligible, key=lambda item: float(item["price"]), default=None)
    if chosen is None:
        return None
    return {
        "status": "READY",
        "price": chosen["price"],
        "origin_time": chosen["origin_time"],
        "confirmed_at": chosen["confirmed_at"],
        "algorithm_version": chosen["algorithm_version"],
        "config_hash": chosen["config_hash"],
    }


def _public_pivot(pivot: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in pivot.items() if not key.startswith("_")}



def build_confirmed_intraday_structure_context(
    frame: pd.DataFrame, *, timeframe: str,
) -> Dict[str, Any]:
    """One confirmed-pivot producer for completed 60m/30m/15m/5m context.

    MTF admission owns clock, source, price basis and complete-bar verification.
    Lower-timeframe pivots remain descriptive context only: they never create a
    strategy trigger, historical PIT label, learning feature or action authority.
    """
    if timeframe not in {"60m", "30m", "15m", "5m"}:
        raise ValueError("UNSUPPORTED_CONFIRMED_INTRADAY_STRUCTURE_TIMEFRAME")
    base = {
        "schema_version": PRICE_STRUCTURE_SCHEMA_VERSION,
        "timeframe": timeframe,
        "completed_bar_only": True,
        "algorithm_version": PIVOT_ALGORITHM_VERSION,
        "config_hash": CONFIG_HASH,
        "pivot_left_bars": PIVOT_LEFT_BARS,
        "pivot_right_bars": PIVOT_RIGHT_BARS,
        "provisional_candidates_excluded": True,
        "independent_action_authority": False,
        "strategy_admitted": False,
        "learning_admitted": False,
        "historical_replay_eligible": False,
    }
    if len(frame) < MIN_OBSERVATIONS:
        return {**base, "status": "MISSING", "reason": "WARMUP_INSUFFICIENT",
                "pivots": [], "swings": [],
                "structure_event": {"state": "NONE", "provisional": False}}
    if _invalid_ohlc(frame) or _source_alignment(frame)["status"] != "SINGLE_SOURCE":
        return {**base, "status": "UNKNOWN", "reason": f"INVALID_OR_MIXED_SOURCE_{timeframe.upper()}",
                "pivots": [], "swings": [],
                "structure_event": {"state": "NONE", "provisional": False}}

    # Existing pivot geometry uses date=bar_end, not a truncated trading date.
    # Keep the confirmation bar time and record the provider-known time
    # separately; a late observation never becomes an earlier PIT label.
    known_at = {
        bar_end.isoformat(): available.isoformat()
        for bar_end, available in zip(frame["date"], frame["available_at"])
    }
    pivots = _confirmed_pivots(frame)
    for pivot in pivots:
        pivot["confirmed_available_at"] = known_at[pivot["confirmed_at"]]
        invalidation_at = pivot["invalidation"].get("triggered_at")
        if invalidation_at is not None:
            pivot["invalidation"]["triggered_available_at"] = known_at[invalidation_at]
    swings = _swings(pivots)
    for swing in swings:
        swing["confirmed_available_at"] = known_at[swing["confirmed_at"]]
    close = float(frame.iloc[-1]["close"])
    event = _latest_event(frame, pivots)
    if event.get("event_time"):
        event["event_available_at"] = known_at[event["event_time"]]
    support = _active_level(pivots, kind="LOW", close=close)
    resistance = _active_level(pivots, kind="HIGH", close=close)
    context_parts = [f"{timeframe}已确认枢轴{len(pivots)}个"] if pivots else []
    if event.get("state") != "NONE":
        context_parts.append(
            f"结构事件`{event['state']}`，关键位{event['level']}，"
            f"完成K线时间{event['event_time']}，本次数据可用时间{event['event_available_at']}，"
            f"失效规则`{event['invalidation']}`"
        )
    if support is not None:
        context_parts.append(f"已确认支撑{support['price']}（确认于{support['confirmed_at']}）")
    if resistance is not None:
        context_parts.append(f"已确认压力{resistance['price']}（确认于{resistance['confirmed_at']}）")
    if pivots:
        context_parts.append("仅为价格结构观察，不构成独立交易触发")
    return {
        **base,
        "status": "READY" if pivots else "MISSING",
        "reason": "CONFIRMED_PIVOTS_READY" if pivots else "NO_CONFIRMED_PIVOTS",
        "observations": int(len(frame)),
        "source_alignment": _source_alignment(frame),
        "pivots": [_public_pivot(item) for item in pivots[-20:]],
        "swings": swings[-12:],
        "nearest_support": support or {"status": "MISSING"},
        "nearest_resistance": resistance or {"status": "MISSING"},
        "structure_event": event,
        "current_close": round(close, 6),
        "summary": "；".join(context_parts) if pivots else None,
    }


def build_confirmed_30m_structure_context(frame: pd.DataFrame) -> Dict[str, Any]:
    """Legacy 30m alias; all admitted intraday intervals share this algorithm."""
    return build_confirmed_intraday_structure_context(frame, timeframe="30m")


def build_price_structure_context(
    *,
    stock_code: str,
    history: Any,
    target_date: Optional[date],
    market: Optional[str] = None,
) -> Dict[str, Any]:
    """Build replay-safe daily Price Structure evidence without fetching or action authority."""
    base = {
        "schema_version": PRICE_STRUCTURE_SCHEMA_VERSION,
        "family": "price_structure",
        "stock_code": stock_code,
        "market": str(market or "").lower() or None,
        "completed_bar_only": True,
        "algorithm_version": PIVOT_ALGORITHM_VERSION,
        "config_hash": CONFIG_HASH,
        "pivot_left_bars": PIVOT_LEFT_BARS,
        "pivot_right_bars": PIVOT_RIGHT_BARS,
        "provisional_candidates_excluded": True,
        "hard_veto": False,
        "independent_action_authority": False,
    }
    if not isinstance(target_date, date):
        return {
            **base,
            "status": "UNKNOWN",
            "reason": "TARGET_DATE_UNKNOWN",
            "pivots": [],
            "swings": [],
            "nearest_support": {"status": "MISSING"},
            "nearest_resistance": {"status": "MISSING"},
            "range_state": "UNKNOWN",
            "structure_event": {"state": "NONE", "provisional": False},
            "historical_replay_eligible": False,
        }

    frame = _normalize_history(history, target_date=target_date)
    base["target_date"] = target_date.isoformat()
    if frame.empty:
        return {
            **base,
            "status": "MISSING",
            "reason": "COMPLETED_OHLC_MISSING",
            "pivots": [],
            "swings": [],
            "nearest_support": {"status": "MISSING"},
            "nearest_resistance": {"status": "MISSING"},
            "range_state": "UNKNOWN",
            "structure_event": {"state": "NONE", "provisional": False},
            "historical_replay_eligible": False,
        }
    if frame.iloc[-1]["date"] != target_date:
        return {
            **base,
            "status": "MISSING",
            "reason": "TARGET_DATE_BAR_MISSING",
            "observations": int(len(frame)),
            "pivots": [],
            "swings": [],
            "nearest_support": {"status": "MISSING"},
            "nearest_resistance": {"status": "MISSING"},
            "range_state": "UNKNOWN",
            "structure_event": {"state": "NONE", "provisional": False},
            "historical_replay_eligible": False,
        }
    if len(frame) < MIN_OBSERVATIONS:
        return {
            **base,
            "status": "MISSING",
            "reason": "WARMUP_INSUFFICIENT",
            "observations": int(len(frame)),
            "pivots": [],
            "swings": [],
            "nearest_support": {"status": "MISSING"},
            "nearest_resistance": {"status": "MISSING"},
            "range_state": "UNKNOWN",
            "structure_event": {"state": "NONE", "provisional": False},
            "historical_replay_eligible": False,
        }
    if _invalid_ohlc(frame):
        return {
            **base,
            "status": "UNKNOWN",
            "reason": "INVALID_OHLC",
            "observations": int(len(frame)),
            "pivots": [],
            "swings": [],
            "nearest_support": {"status": "MISSING"},
            "nearest_resistance": {"status": "MISSING"},
            "range_state": "UNKNOWN",
            "structure_event": {"state": "NONE", "provisional": False},
            "historical_replay_eligible": False,
        }

    alignment = _source_alignment(frame)
    pivots = _confirmed_pivots(frame)
    swings = _swings(pivots)
    close = float(frame.iloc[-1]["close"])
    support = _active_level(pivots, kind="LOW", close=close)
    resistance = _active_level(pivots, kind="HIGH", close=close)
    latest_event = _latest_event(frame, pivots)

    event_state = str(latest_event.get("state") or "NONE")
    if support is not None and resistance is not None:
        range_state = "BETWEEN_CONFIRMED_LEVELS"
    elif event_state in {"UP_BREAKOUT", "UP_BREAKOUT_RETEST_HOLD"}:
        range_state = "ABOVE_OR_TESTING_CONFIRMED_RESISTANCE"
    elif event_state == "FAILED_UP_BREAKOUT":
        range_state = "FAILED_BREAKOUT_RETURNED_BELOW_LEVEL"
    elif event_state in {"DOWN_BREAKDOWN", "DOWN_BREAKDOWN_RETEST_REJECT"}:
        range_state = "BELOW_OR_TESTING_CONFIRMED_SUPPORT"
    elif event_state == "FAILED_DOWN_BREAKDOWN":
        range_state = "FAILED_BREAKDOWN_RETURNED_ABOVE_LEVEL"
    else:
        range_state = "OPEN_STRUCTURE"

    if not pivots:
        status = "PARTIAL"
        reason = "NO_CONFIRMED_PIVOTS"
    elif alignment["status"] != "SINGLE_SOURCE":
        status = "PARTIAL"
        reason = "SOURCE_ALIGNMENT_UNPROVEN"
    else:
        status = "READY"
        reason = "CONFIRMED_PIVOTS_READY"

    return {
        **base,
        "status": status,
        "reason": reason,
        "observations": int(len(frame)),
        "start_date": frame.iloc[0]["date"].isoformat(),
        "end_date": frame.iloc[-1]["date"].isoformat(),
        "source_alignment": alignment,
        "current_close": round(close, 6),
        "pivots": [_public_pivot(item) for item in pivots[-20:]],
        "swings": swings[-12:],
        "nearest_support": support or {"status": "MISSING"},
        "nearest_resistance": resistance or {"status": "MISSING"},
        "range_state": range_state,
        "structure_event": latest_event,
        "historical_replay_eligible": status == "READY",
    }
