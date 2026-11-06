# -*- coding: utf-8 -*-
"""Pure completed-bar supply/demand and volume-price evidence.

The first slice intentionally uses only persisted OHLCV bars. It does not fetch
providers, infer institutional intent, persist state, or own action authority.
"""

from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
from typing import Any, Dict, Optional

import pandas as pd

from data_provider.daily_data_identity import bind_verified_daily_source_view


SUPPLY_DEMAND_SCHEMA_VERSION = "supply-demand-volume-price-v1"
VOLUME_PRICE_PHASE_SCHEMA_VERSION = "volume-price-phase-candidate-v1"
NO_WARMUP = None
WINDOW_SESSIONS = 20
REQUIRED_OBSERVATIONS = WINDOW_SESSIONS + 1
ALGORITHM_VERSION = "supply-demand-volume-price-v1"
_CONFIG = {
    "algorithm_version": ALGORITHM_VERSION,
    "window_sessions": WINDOW_SESSIONS,
    "required_observations": REQUIRED_OBSERVATIONS,
    "relative_volume": "LATEST_OVER_PRIOR_20_MEAN",
    "directional_volume": "UP_DOWN_VOLUME_BALANCE",
    "close_location_flow": "CMF20",
    "completed_bar_only": True,
    "institutional_intent_inferred": False,
}
CONFIG_HASH = sha256(
    json.dumps(_CONFIG, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


def _status_payload(*, status: str, reason: str, stock_code: str, target_date: Optional[date]) -> Dict[str, Any]:
    return {
        "schema_version": SUPPLY_DEMAND_SCHEMA_VERSION,
        "family": "supply_demand_volume_price",
        "status": status,
        "reason": reason,
        "stock_code": stock_code,
        "target_date": target_date.isoformat() if isinstance(target_date, date) else None,
        "window_sessions": WINDOW_SESSIONS,
        "completed_bar_only": True,
        "algorithm_version": ALGORITHM_VERSION,
        "config_hash": CONFIG_HASH,
        "effective_parameters": dict(_CONFIG),
        "state": "UNKNOWN",
        "relative_volume": {},
        "directional_volume": {},
        "close_location_flow": {},
        "data_quality": {},
        "institutional_intent_claim": "NOT_INFERRED",
    }


def _normalize_history(history: Any, *, target_date: date, preserve_bar_end: bool = False) -> pd.DataFrame:
    required = ["date", "high", "low", "close", "volume"]
    if not isinstance(history, pd.DataFrame) or history.empty:
        return pd.DataFrame(columns=required)
    frame = history.copy()
    frame.columns = [str(column).lower() for column in frame.columns]
    if any(column not in frame.columns for column in required):
        return pd.DataFrame(columns=required)
    frame["date"] = (
        pd.to_datetime(frame["date"], errors="coerce", utc=True)
        if preserve_bar_end
        else pd.to_datetime(frame["date"], errors="coerce").dt.date
    )
    for column in ("high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=required)
    frame = frame[
        frame["date"].dt.date <= target_date
        if preserve_bar_end else frame["date"] <= target_date
    ]
    if preserve_bar_end:
        frame = frame.reset_index(drop=True)
    else:
        frame = frame.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    columns = required + (["data_source"] if "data_source" in frame.columns else [])
    result = frame[columns].reset_index(drop=True)
    return result if preserve_bar_end else bind_verified_daily_source_view(history, result)


def build_supply_demand_context(
    *,
    stock_code: str,
    history: Any,
    target_date: Optional[date],
    timeframe: str = "daily",
) -> Dict[str, Any]:
    """Reuse original 20-bar formulas at one completed bar frequency.

    For intraday, typed source identity and complete exchange sessions must
    already be verified by the MTF parent. No data or action authority here.
    """
    if timeframe not in {"daily", "weekly", "monthly", "60m", "30m", "15m", "5m"}:
        return _status_payload(
            status="UNKNOWN", reason="TIMEFRAME_UNSUPPORTED",
            stock_code=stock_code, target_date=target_date,
        )

    def _period_result(payload: Dict[str, Any]) -> Dict[str, Any]:
        if timeframe == "daily":
            return payload
        result = dict(payload)
        result["timeframe"] = timeframe
        result["window_completed_bars"] = result.pop("window_sessions")
        parameters = dict(result["effective_parameters"])
        parameters.pop("window_sessions", None)
        parameters.update(timeframe=timeframe, window_completed_bars=WINDOW_SESSIONS)
        result["effective_parameters"] = parameters
        result["config_hash"] = sha256(json.dumps(
            parameters, ensure_ascii=True, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        quality = result.get("data_quality")
        if isinstance(quality, dict) and "start_date" in quality:
            quality = dict(quality)
            quality["start_bar_end"] = quality.pop("start_date")
            quality["end_bar_end"] = quality.pop("end_date")
            result["data_quality"] = quality
        volume = result.get("relative_volume")
        if isinstance(volume, dict) and "volume_ratio_20d" in volume:
            volume = dict(volume)
            for old,new in (
                ("volume_ratio_20d","volume_ratio_20_completed_bars"),
                ("recent_5d_avg","recent_5bars_avg"),
                ("prior_5d_avg","prior_5bars_avg"),
                ("recent_vs_prior_5d_pct","recent_vs_prior_5bars_pct"),
            ):
                volume[new]=volume.pop(old)
            result["relative_volume"] = volume
        direction = result.get("directional_volume")
        if isinstance(direction, dict) and "up_days" in direction:
            direction = dict(direction)
            for old,new in (
                ("up_days","up_bars"), ("down_days","down_bars"),
                ("avg_up_day_volume","avg_up_bar_volume"),
                ("avg_down_day_volume","avg_down_bar_volume"),
            ):
                direction[new]=direction.pop(old)
            result["directional_volume"] = direction
        result.update(independent_action_authority=False, strategy_admitted=False,
                      data_rights_admitted=False)
        return result

    if not isinstance(target_date, date):
        return _status_payload(status="UNKNOWN", reason="TARGET_DATE_UNKNOWN", stock_code=stock_code, target_date=None)

    intraday = timeframe in {"60m", "30m", "15m", "5m"}
    if intraday and isinstance(history, pd.DataFrame) and "date" in history:
        stamps = pd.to_datetime(history["date"], errors="coerce", utc=True)
        if stamps.isna().any() or stamps.duplicated().any() or not stamps.is_monotonic_increasing:
            reason = (
                "TIMEFRAME_BAR_TIMESTAMP_INVALID" if stamps.isna().any() else
                "TIMEFRAME_BAR_TIMESTAMP_DUPLICATE" if stamps.duplicated().any() else
                "TIMEFRAME_BAR_ORDER_INVALID"
            )
            return _period_result(_status_payload(
                status="UNKNOWN", reason=reason,
                stock_code=stock_code, target_date=target_date,
            ))
    frame = _normalize_history(history, target_date=target_date, preserve_bar_end=intraday)
    if frame.empty:
        return _status_payload(status="MISSING", reason="COMPLETED_OHLCV_MISSING", stock_code=stock_code, target_date=target_date)
    if (frame.iloc[-1]["date"].date() if intraday else frame.iloc[-1]["date"]) != target_date:
        return _status_payload(status="MISSING", reason="TARGET_DATE_BAR_MISSING", stock_code=stock_code, target_date=target_date)
    if len(frame) < REQUIRED_OBSERVATIONS:
        return _status_payload(status="MISSING", reason="WARMUP_INSUFFICIENT", stock_code=stock_code, target_date=target_date)

    window = frame.tail(REQUIRED_OBSERVATIONS).reset_index(drop=True)
    metric_window = window.tail(WINDOW_SESSIONS).reset_index(drop=True)
    previous_20 = window.iloc[:-1].tail(WINDOW_SESSIONS)

    invalid_price = (
        (metric_window["high"] <= 0)
        | (metric_window["low"] <= 0)
        | (metric_window["close"] <= 0)
        | (metric_window["high"] < metric_window["low"])
        | (metric_window["close"] > metric_window["high"])
        | (metric_window["close"] < metric_window["low"])
    )
    if bool(invalid_price.any()) or bool((metric_window["volume"] < 0).any()):
        return _status_payload(status="UNKNOWN", reason="INVALID_OHLCV", stock_code=stock_code, target_date=target_date)
    if bool((metric_window["volume"] <= 0).any()) or float(previous_20["volume"].sum()) <= 0:
        return _status_payload(status="UNKNOWN", reason="NON_POSITIVE_VOLUME", stock_code=stock_code, target_date=target_date)

    previous_mean_volume = float(previous_20["volume"].mean())
    latest_volume = float(window.iloc[-1]["volume"])
    volume_ratio_20d = latest_volume / previous_mean_volume if previous_mean_volume > 0 else None

    recent_5_avg = float(window["volume"].tail(5).mean())
    prior_5_avg = float(window["volume"].iloc[-10:-5].mean())
    recent_vs_prior_5d_pct = (
        (recent_5_avg / prior_5_avg - 1.0) * 100.0 if prior_5_avg > 0 else None
    )

    close_diff = window["close"].diff().iloc[1:]
    directional_volume = window["volume"].iloc[1:]
    up_volume = float(directional_volume[close_diff > 0].sum())
    down_volume = float(directional_volume[close_diff < 0].sum())
    directional_total = up_volume + down_volume
    signed_volume_balance = (
        (up_volume - down_volume) / directional_total if directional_total > 0 else 0.0
    )
    up_count = int((close_diff > 0).sum())
    down_count = int((close_diff < 0).sum())
    avg_up_volume = up_volume / up_count if up_count else 0.0
    avg_down_volume = down_volume / down_count if down_count else 0.0

    spread = metric_window["high"] - metric_window["low"]
    multiplier = pd.Series(0.0, index=metric_window.index)
    nonzero_spread = spread > 0
    multiplier.loc[nonzero_spread] = (
        ((metric_window.loc[nonzero_spread, "close"] - metric_window.loc[nonzero_spread, "low"])
         - (metric_window.loc[nonzero_spread, "high"] - metric_window.loc[nonzero_spread, "close"]))
        / spread.loc[nonzero_spread]
    )
    total_volume = float(metric_window["volume"].sum())
    cmf_20 = float((multiplier * metric_window["volume"]).sum() / total_volume) if total_volume > 0 else 0.0

    if signed_volume_balance > 0 and cmf_20 > 0:
        state = "DEMAND_PRESSURE"
    elif signed_volume_balance < 0 and cmf_20 < 0:
        state = "SUPPLY_PRESSURE"
    elif signed_volume_balance == 0 and cmf_20 == 0:
        state = "BALANCED"
    else:
        state = "CONFLICT"

    source_values: set[str] = set()
    source_rows_complete = False
    if "data_source" in window.columns:
        source_text = window["data_source"].map(lambda value: str(value).strip() if value not in (None, "") else "")
        source_values = {value for value in source_text.tolist() if value}
        source_rows_complete = bool((source_text != "").all())
    source_alignment = "SINGLE_SOURCE" if len(source_values) == 1 and source_rows_complete else "UNPROVEN"
    status = "READY" if source_alignment == "SINGLE_SOURCE" else "PARTIAL"
    reason = "COMPLETED_OHLCV_SINGLE_SOURCE" if status == "READY" else "SOURCE_ALIGNMENT_UNPROVEN"

    return _period_result({
        "schema_version": SUPPLY_DEMAND_SCHEMA_VERSION,
        "family": "supply_demand_volume_price",
        "status": status,
        "reason": reason,
        "stock_code": stock_code,
        "target_date": target_date.isoformat(),
        "window_sessions": WINDOW_SESSIONS,
        "state": state,
        "completed_bar_only": True,
        "algorithm_version": ALGORITHM_VERSION,
        "config_hash": CONFIG_HASH,
        "effective_parameters": dict(_CONFIG),
        "relative_volume": {
            "volume_ratio_20d": round(volume_ratio_20d, 6) if volume_ratio_20d is not None else None,
            "recent_5d_avg": round(recent_5_avg, 6),
            "prior_5d_avg": round(prior_5_avg, 6),
            "recent_vs_prior_5d_pct": round(recent_vs_prior_5d_pct, 6) if recent_vs_prior_5d_pct is not None else None,
            "correlation_group": "relative_volume",
        },
        "directional_volume": {
            "signed_volume_balance": round(signed_volume_balance, 6),
            "avg_up_day_volume": round(avg_up_volume, 6),
            "avg_down_day_volume": round(avg_down_volume, 6),
            "up_days": up_count,
            "down_days": down_count,
            "correlation_group": "directional_volume",
        },
        "close_location_flow": {
            "cmf_20": round(cmf_20, 6),
            "correlation_group": "close_location_flow",
        },
        "data_quality": {
            "source_alignment": source_alignment,
            "sources": sorted(source_values),
            "observations": len(window),
            "start_date": window.iloc[0]["date"].isoformat(),
            "end_date": window.iloc[-1]["date"].isoformat(),
        },
        "institutional_intent_claim": "NOT_INFERRED",
    })


def compile_volume_price_phase_context(
    *,
    supply_demand_context: Any,
    price_structure_context: Any,
    timeframe: str,
) -> Dict[str, Any]:
    """Classify observable volume-price phases without inferring an operator.

    ``吸筹/洗盘/出货`` are represented only as deterministic *candidate phase*
    labels backed by completed-bar supply and confirmed price-structure states.
    The resolver neither identifies a market participant nor creates a buy/sell
    signal. Distribution/markdown candidates can contribute one de-duplicated
    risk veto only when an adverse confirmed structure event is also present.
    """
    normalized_timeframe = str(timeframe or "").strip()
    base = {
        "schema_version": VOLUME_PRICE_PHASE_SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "config_hash": CONFIG_HASH,
        "family": "volume_price_phase",
        "status": "MISSING",
        "reason_codes": (),
        "stock_code": None,
        "target_date": None,
        "timeframe": normalized_timeframe or None,
        "completed_bar_only": True,
        "source_alignment": None,
        "phase": "INDETERMINATE",
        "phase_label_zh": "量价阶段候选不明确",
        "supporting_observations": (),
        "correlation_group": "volume_price_structure_phase",
        "institutional_intent_claim": "NOT_INFERRED",
        "hard_veto": False,
        "veto_codes": (),
        "independent_action_authority": False,
        "strategy_upgrade_authority": False,
        "learning_admitted": False,
        "receipt_hash": None,
    }
    if normalized_timeframe not in {"monthly", "weekly", "daily", "60m", "30m", "15m", "5m"}:
        return {**base, "status": "UNKNOWN", "reason_codes": ("VOLUME_PRICE_PHASE_TIMEFRAME_INVALID",)}
    if not isinstance(supply_demand_context, dict) or not isinstance(price_structure_context, dict):
        return {**base, "reason_codes": ("VOLUME_PRICE_PHASE_PARENT_CONTEXT_MISSING",)}
    supply_status = str(supply_demand_context.get("status") or "").upper()
    structure_status = str(price_structure_context.get("status") or "").upper()
    if supply_status not in {"READY", "PARTIAL"} or structure_status not in {"READY", "PARTIAL"}:
        return {
            **base,
            "status": "MISSING",
            "reason_codes": ("VOLUME_PRICE_PHASE_PARENT_NOT_READY",),
        }
    stock_code = str(supply_demand_context.get("stock_code") or "").strip()
    structure_code = str(price_structure_context.get("stock_code") or "").strip()
    target_date = str(supply_demand_context.get("target_date") or "").strip()
    structure_date = str(price_structure_context.get("target_date") or "").strip()
    supply_timeframe = str(supply_demand_context.get("timeframe") or "daily").strip()
    structure_timeframe = str(price_structure_context.get("timeframe") or "daily").strip()
    if (
        not stock_code or stock_code != structure_code
        or not target_date or target_date != structure_date
        or supply_timeframe != normalized_timeframe
        or structure_timeframe != normalized_timeframe
    ):
        return {
            **base,
            "status": "UNKNOWN",
            "stock_code": stock_code or None,
            "target_date": target_date or None,
            "reason_codes": ("VOLUME_PRICE_PHASE_ASSET_OR_TIME_MISMATCH",),
        }
    if (
        supply_demand_context.get("completed_bar_only") is not True
        or price_structure_context.get("completed_bar_only") is not True
        or supply_demand_context.get("institutional_intent_claim") != "NOT_INFERRED"
    ):
        return {
            **base,
            "status": "UNKNOWN",
            "stock_code": stock_code,
            "target_date": target_date,
            "reason_codes": ("VOLUME_PRICE_PHASE_PARENT_SEMANTICS_INVALID",),
        }

    supply_state = str(supply_demand_context.get("state") or "UNKNOWN").upper()
    event = price_structure_context.get("structure_event")
    event = event if isinstance(event, dict) else {}
    event_state = str(event.get("state") or "NONE").upper()
    range_state = str(price_structure_context.get("range_state") or "UNKNOWN").upper()
    observations = (
        "SUPPLY:" + supply_state,
        "STRUCTURE_EVENT:" + event_state,
        "RANGE:" + range_state,
    )
    phase = "INDETERMINATE"
    label = "量价阶段候选不明确"
    hard_veto = False
    if supply_state == "DEMAND_PRESSURE" and event_state == "FAILED_DOWN_BREAKDOWN":
        phase, label = "WASHOUT_CANDIDATE", "洗盘候选（跌破失败后需求回流）"
    elif supply_state == "DEMAND_PRESSURE" and event_state in {"UP_BREAKOUT", "UP_BREAKOUT_RETEST_HOLD"}:
        phase, label = "MARKUP_CANDIDATE", "上升推进候选（需求配合突破）"
    elif supply_state == "SUPPLY_PRESSURE" and event_state == "FAILED_UP_BREAKOUT":
        phase, label, hard_veto = "DISTRIBUTION_CANDIDATE", "派发/出货风险候选（突破失败且供给占优）", True
    elif supply_state == "SUPPLY_PRESSURE" and event_state in {"DOWN_BREAKDOWN", "DOWN_BREAKDOWN_RETEST_REJECT"}:
        phase, label, hard_veto = "MARKDOWN_CANDIDATE", "下降推进候选（供给配合跌破）", True
    elif supply_state == "DEMAND_PRESSURE" and range_state == "BETWEEN_CONFIRMED_LEVELS":
        phase, label = "ACCUMULATION_CANDIDATE", "吸筹候选（区间内需求占优）"
    elif supply_state == "SUPPLY_PRESSURE" and range_state == "BETWEEN_CONFIRMED_LEVELS":
        phase, label = "DISTRIBUTION_CANDIDATE", "派发/出货风险候选（区间内供给占优）"

    status = "READY" if phase != "INDETERMINATE" and supply_status == structure_status == "READY" else "PARTIAL"
    reason_codes = (
        ("DETERMINISTIC_VOLUME_PRICE_PHASE_CANDIDATE",)
        if phase != "INDETERMINATE"
        else ("VOLUME_PRICE_PHASE_CONFLICT_OR_NO_CONFIRMED_EVENT",)
    )
    document = {
        **base,
        "status": status,
        "reason_codes": reason_codes,
        "stock_code": stock_code,
        "target_date": target_date,
        "phase": phase,
        "phase_label_zh": label,
        "supporting_observations": observations,
        "source_alignment": dict(supply_demand_context.get("data_quality") or {}),
        "hard_veto": hard_veto,
        "veto_codes": ("DISTRIBUTION_OR_MARKDOWN_CANDIDATE",) if hard_veto else (),
    }
    document["receipt_hash"] = sha256(
        json.dumps(
            {key: value for key, value in document.items() if key != "receipt_hash"},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return document
