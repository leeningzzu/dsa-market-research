# -*- coding: utf-8 -*-
"""Coverage-aware multi-timeframe structure over canonical completed daily history.

V1 reuses the existing daily history owner, StockTrendAnalyzer and confirmed
Pivot/Swing owner. It does not fetch data, create action authority or pretend
that intraday bars exist. Weekly/monthly bars are deterministic OHLCV
aggregates of already-completed daily bars; the current partial higher-timeframe
period is excluded unless the exchange calendar proves it complete.
"""

from __future__ import annotations

from datetime import date, datetime
from hashlib import sha256
import json
from typing import Any, Dict, Optional

import pandas as pd

from data_provider.daily_data_identity import (
    attach_daily_data_identity,
    extract_daily_data_identity,
    rebind_daily_data_identity,
)

from src.core.trading_calendar import resolve_completed_timeframe_bar_date
from src.services.price_structure_service import build_price_structure_context
from src.services.pit_identity import build_completed_history_identity
from src.services.evidence_traceability_registry import describe_macd_state


SCHEMA_VERSION = "multi-timeframe-structure-v1"
ALGORITHM_VERSION = "completed-daily-resample-v1"
TREND_MIN_BARS = 26
CROSS_RUN_PERSISTENCE_POLICY = "BLOCK_UNTIL_ADJUSTMENT_BASIS_PERSISTED"

_CONFIG = {
    "algorithm_version": ALGORITHM_VERSION,
    "trend_min_bars": TREND_MIN_BARS,
    "cross_run_persistence_policy": CROSS_RUN_PERSISTENCE_POLICY,
    "weekly_period": "W-FRI",
    "monthly_period": "M",
    "intraday_policy": "MISSING_UNTIL_COMPLETED_BAR_CONTRACT",
}
CONFIG_HASH = sha256(
    json.dumps(_CONFIG, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()


MA_STRUCTURE_SCHEMA_VERSION = "ma-structure-evidence-v2"
MA_STRUCTURE_ALGORITHM_VERSION = "sma-bundle-readiness-lifecycle-v2"
MA_CORE_PERIODS = (5, 10, 20)
MA_OPTIONAL_LONG_PERIOD = 60
MA_SLOPE_LOOKBACK = 5
MA_COMPRESSION_REFERENCE_WINDOW = 20
MA_COMPRESSION_QUANTILE = 0.25
MA_COMPRESSION_PERSISTENCE = 3
MA_LEVEL_READY_BARS = max(MA_CORE_PERIODS)
MA_SLOPE_CROSS_READY_BARS = MA_LEVEL_READY_BARS + MA_SLOPE_LOOKBACK + 1
MA_COMPRESSION_READY_BARS = MA_LEVEL_READY_BARS + MA_COMPRESSION_REFERENCE_WINDOW
_MA_STRUCTURE_CONFIG = {
    "schema_version": MA_STRUCTURE_SCHEMA_VERSION,
    "algorithm_version": MA_STRUCTURE_ALGORITHM_VERSION,
    "ma_type": "SMA",
    "core_periods": MA_CORE_PERIODS,
    "optional_long_period": MA_OPTIONAL_LONG_PERIOD,
    "slope_lookback": MA_SLOPE_LOOKBACK,
    "level_ready_bars": MA_LEVEL_READY_BARS,
    "slope_cross_ready_bars": MA_SLOPE_CROSS_READY_BARS,
    "compression_ready_bars": MA_COMPRESSION_READY_BARS,
    "compression_reference_window": MA_COMPRESSION_REFERENCE_WINDOW,
    "compression_reference_observations_required": MA_COMPRESSION_REFERENCE_WINDOW,
    "compression_quantile": MA_COMPRESSION_QUANTILE,
    "compression_persistence": MA_COMPRESSION_PERSISTENCE,
    "compression_requires_absolute_spread_low": True,
    "release_memory_bars": MA_SLOPE_LOOKBACK,
    "release_confirmation": "STRUCTURE_BREAKOUT_AND_DIRECTIONAL_HEAVY_VOLUME",
    "correlation_group": "price_contraction",
    "parameter_policy": "VERSIONED_CANDIDATE_PARAMETERS_NOT_UNIVERSAL_CONSTANTS",
}
MA_STRUCTURE_CONFIG_HASH = sha256(
    json.dumps(_MA_STRUCTURE_CONFIG, sort_keys=True, separators=(",", ":")).encode("utf-8")
).hexdigest()

_ROLE = {
    "monthly": "LONG_TERM_CONTEXT",
    "weekly": "PRIMARY_TREND_CONTEXT",
    "daily": "PRIMARY_SETUP",
    "60m": "OPTIONAL_BRIDGE",
    "30m": "PRIMARY_STRUCTURE",
    "15m": "TRIGGER_CONFIRMATION",
    "5m": "MICRO_TIMING",
}


def _normalize_daily_history(history: Any, *, target_date: date) -> pd.DataFrame:
    required = ["date", "open", "high", "low", "close", "volume"]
    if not isinstance(history, pd.DataFrame) or history.empty:
        return pd.DataFrame(columns=required)
    identity = extract_daily_data_identity(history, strict=False)
    frame = history.copy()
    frame.columns = [str(column).lower() for column in frame.columns]
    if any(column not in frame.columns for column in required):
        return pd.DataFrame(columns=required)
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=required)
    frame = frame[frame["date"] <= target_date]
    frame = frame.sort_values("date").drop_duplicates(subset=["date"], keep="last")
    optional = [
        column
        for column in ("amount", "data_source", "data_identity_json", "data_identity_hash")
        if column in frame.columns
    ]
    result = frame[required + optional].reset_index(drop=True)
    if identity is not None:
        attach_daily_data_identity(result, rebind_daily_data_identity(identity, result))
    return result


def _invalid_ohlcv(frame: pd.DataFrame) -> bool:
    if frame.empty:
        return False
    return bool(
        (
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
        ).any()
    )


def _source_alignment(frame: pd.DataFrame) -> Dict[str, Any]:
    if "data_source" not in frame.columns:
        return {"status": "UNPROVEN", "sources": [], "rows_complete": False}
    text = frame["data_source"].map(
        lambda value: str(value).strip() if value not in (None, "") else ""
    )
    sources = sorted({value for value in text.tolist() if value})
    complete = bool((text != "").all())
    return {
        "status": "SINGLE_SOURCE" if len(sources) == 1 and complete else "UNPROVEN",
        "sources": sources,
        "rows_complete": complete,
    }


def _aggregate_completed_bars(
    frame: pd.DataFrame,
    *,
    completed_through: date,
    period_freq: str,
) -> pd.DataFrame:
    eligible = frame[frame["date"] <= completed_through].copy()
    if eligible.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    eligible["_period"] = pd.to_datetime(eligible["date"]).dt.to_period(period_freq)
    records = []
    for _, group in eligible.groupby("_period", sort=True):
        group = group.sort_values("date")
        record = {
            "date": group.iloc[-1]["date"],
            "open": float(group.iloc[0]["open"]),
            "high": float(group["high"].max()),
            "low": float(group["low"].min()),
            "close": float(group.iloc[-1]["close"]),
            "volume": float(group["volume"].sum()),
        }
        if "data_source" in group.columns:
            sources = [
                str(value).strip()
                for value in group["data_source"].tolist()
                if str(value).strip()
            ]
            if sources and len(sources) == len(group) and len(set(sources)) == 1:
                record["data_source"] = sources[0]
        records.append(record)
    return pd.DataFrame(records)


def _ma_ordering(ma5: float, ma10: float, ma20: float) -> str:
    if ma5 > ma10 > ma20:
        return "BULLISH"
    if ma5 < ma10 < ma20:
        return "BEARISH"
    return "MIXED"


def _ma_cross_events(current: pd.Series, previous: Optional[pd.Series]) -> list[str]:
    if previous is None:
        return []
    events: list[str] = []
    for fast, slow in (("MA5", "MA10"), ("MA10", "MA20")):
        prev_diff = float(previous[fast]) - float(previous[slow])
        curr_diff = float(current[fast]) - float(current[slow])
        if prev_diff <= 0 < curr_diff:
            events.append(f"{fast}_CROSS_ABOVE_{slow}")
        elif prev_diff >= 0 > curr_diff:
            events.append(f"{fast}_CROSS_BELOW_{slow}")
    return events


def _ma_release_confirmation(
    state: str,
    *,
    trend_result: Any,
    structure_context: Optional[Dict[str, Any]],
) -> tuple[bool, Optional[str], Optional[str]]:
    structure_event = (
        structure_context.get("structure_event")
        if isinstance(structure_context, dict)
        else None
    )
    structure_state = (
        str((structure_event or {}).get("state") or "")
        if isinstance(structure_event, dict)
        else ""
    )
    volume_status = str(
        getattr(getattr(trend_result, "volume_status", None), "value", "")
        or getattr(trend_result, "volume_status", "")
        or ""
    )
    if state == "RELEASING_UP":
        confirmed = (
            structure_state in {"UP_BREAKOUT", "UP_BREAKOUT_RETEST_HOLD"}
            and volume_status == "放量上涨"
        )
    elif state == "RELEASING_DOWN":
        confirmed = (
            structure_state in {"DOWN_BREAKOUT", "DOWN_BREAKOUT_RETEST_HOLD"}
            and volume_status == "放量下跌"
        )
    else:
        confirmed = False
    return confirmed, structure_state or None, volume_status or None


def _ma_state_summary(evidence: Dict[str, Any]) -> Optional[str]:
    state = str(evidence.get("state") or "")
    ordering = str(evidence.get("ordering") or "MIXED")
    ordering_text = {
        "BULLISH": "多头排列",
        "BEARISH": "空头排列",
        "MIXED": "混合排列",
    }.get(ordering, "排列未定")
    if state == "COMPRESSING":
        return f"均线束由发散转向收敛，当前{ordering_text}，方向尚未确认"
    if state == "COMPRESSED":
        return f"均线束已进入压缩区间，当前{ordering_text}，仍需量价与结构确认方向"
    if state == "RELEASING_UP":
        suffix = "，量价与结构已确认" if not evidence.get("provisional") else "，尚待量价与结构确认"
        return "均线由粘连向上释放" + suffix
    if state == "RELEASING_DOWN":
        suffix = "，量价与结构已确认" if not evidence.get("provisional") else "，尚待量价与结构确认"
        return "均线由粘连向下释放" + suffix
    if state == "FAILED_RELEASE":
        return "此前均线释放未能延续，需等待新的结构确认"
    if evidence.get("cross_events"):
        return f"均线发生{'/'.join(evidence['cross_events'])}，仅作趋势上下文，不单独构成方向确认"
    return None


def build_ma_structure_evidence(
    bars: pd.DataFrame,
    *,
    timeframe: str,
    trend_result: Any = None,
    structure_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministic descriptive MA structure over one completed-bar prefix."""

    base = {
        "schema_version": MA_STRUCTURE_SCHEMA_VERSION,
        "method_id": "MA_COMPRESSION_RELEASE",
        "method_ids": ("MA_SLOPE_CROSS", "MA_COMPRESSION_RELEASE"),
        "timeframe": timeframe,
        "ma_type": "SMA",
        "period_set": MA_CORE_PERIODS,
        "optional_long_reference": MA_OPTIONAL_LONG_PERIOD,
        "level_ready_bars": MA_LEVEL_READY_BARS,
        "slope_cross_ready_bars": MA_SLOPE_CROSS_READY_BARS,
        "compression_ready_bars": MA_COMPRESSION_READY_BARS,
        "slope_lookback": MA_SLOPE_LOOKBACK,
        "normalization": "PRICE_PERCENT",
        "compression_reference_window": MA_COMPRESSION_REFERENCE_WINDOW,
        "compression_quantile": MA_COMPRESSION_QUANTILE,
        "compression_persistence_required": MA_COMPRESSION_PERSISTENCE,
        "algorithm_version": MA_STRUCTURE_ALGORITHM_VERSION,
        "config_hash": MA_STRUCTURE_CONFIG_HASH,
        "correlation_group": "price_contraction",
        "independent_action_authority": False,
        "strategy_admitted": False,
        "learning_admitted": False,
    }

    def _readiness(observations: int, reference_observations: int = 0) -> Dict[str, Any]:
        level_ready = observations >= MA_LEVEL_READY_BARS
        slope_ready = observations >= MA_SLOPE_CROSS_READY_BARS
        context_ready = (
            observations >= MA_COMPRESSION_READY_BARS
            and reference_observations == MA_COMPRESSION_REFERENCE_WINDOW
        )
        return {
            "level": {
                "status": "READY" if level_ready else "MISSING",
                "required_observations": MA_LEVEL_READY_BARS,
                "observations": observations,
            },
            "slope_cross": {
                "status": "READY" if slope_ready else "MISSING",
                "required_observations": MA_SLOPE_CROSS_READY_BARS,
                "observations": observations,
            },
            "compression_context": {
                "status": "READY" if context_ready else "MISSING",
                "required_observations": MA_COMPRESSION_READY_BARS,
                "required_reference_observations": MA_COMPRESSION_REFERENCE_WINDOW,
                "reference_observations": reference_observations,
            },
            "event_lifecycle": {
                "status": "READY" if context_ready else "MISSING",
                "requires_compression_context": True,
            },
        }

    if not isinstance(bars, pd.DataFrame) or bars.empty:
        return {
            **base,
            "status": "MISSING",
            "state": "UNKNOWN",
            "reason": "BARS_MISSING",
            "observations": 0,
            "readiness": _readiness(0),
        }
    required = {"date", "close"}
    if not required.issubset(bars.columns):
        return {
            **base,
            "status": "MISSING",
            "state": "UNKNOWN",
            "reason": "BAR_FIELDS_MISSING",
            "observations": 0,
            "readiness": _readiness(0),
        }

    frame = bars.loc[:, ["date", "close"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    frame = frame.dropna(subset=["date", "close"]).reset_index(drop=True)
    observations = int(len(frame))
    if bool((frame["close"] <= 0).any()):
        return {
            **base,
            "status": "UNKNOWN",
            "state": "UNKNOWN",
            "reason": "DENOMINATOR_INVALID",
            "observations": observations,
            "readiness": _readiness(observations),
        }

    for period in (*MA_CORE_PERIODS, MA_OPTIONAL_LONG_PERIOD):
        frame[f"MA{period}"] = frame["close"].rolling(
            period,
            min_periods=period,
        ).mean()

    frame["bundle_spread_pct"] = (
        frame[["MA5", "MA10", "MA20"]].max(axis=1)
        - frame[["MA5", "MA10", "MA20"]].min(axis=1)
    ) / frame["close"] * 100.0
    frame["bundle_spread_abs"] = (
        frame[["MA5", "MA10", "MA20"]].max(axis=1)
        - frame[["MA5", "MA10", "MA20"]].min(axis=1)
    )
    bundle_ready = frame[["MA5", "MA10", "MA20"]].notna().all(axis=1)
    frame.loc[~bundle_ready, "bundle_spread_pct"] = float("nan")
    frame.loc[~bundle_ready, "bundle_spread_abs"] = float("nan")

    if observations < MA_LEVEL_READY_BARS:
        return {
            **base,
            "status": "MISSING",
            "state": "UNKNOWN",
            "reason": "LEVEL_WARMUP_INSUFFICIENT",
            "observations": observations,
            "readiness": _readiness(observations),
        }

    final_index = observations - 1
    final_row = frame.iloc[final_index]
    close = float(final_row["close"])
    ma_values = {
        f"ma{period}": float(final_row[f"MA{period}"])
        for period in MA_CORE_PERIODS
    }
    optional_long = final_row[f"MA{MA_OPTIONAL_LONG_PERIOD}"]
    if pd.notna(optional_long):
        ma_values[f"ma{MA_OPTIONAL_LONG_PERIOD}"] = float(optional_long)
    ordering = _ma_ordering(ma_values["ma5"], ma_values["ma10"], ma_values["ma20"])
    bar_end = final_row["date"].isoformat()
    price_location = (
        "ABOVE_BUNDLE"
        if close > max(ma_values["ma5"], ma_values["ma10"], ma_values["ma20"])
        else "BELOW_BUNDLE"
        if close < min(ma_values["ma5"], ma_values["ma10"], ma_values["ma20"])
        else "INSIDE_BUNDLE"
    )
    prior_reference = frame.iloc[
        max(0, final_index - MA_COMPRESSION_REFERENCE_WINDOW):final_index
    ]["bundle_spread_pct"].dropna()
    reference_observations = int(len(prior_reference))
    readiness = _readiness(observations, reference_observations)
    partial = {
        **base,
        "status": "PARTIAL",
        "state": "UNKNOWN",
        "observations": observations,
        "readiness": readiness,
        "ordering": ordering,
        "ma_values": ma_values,
        "slope_vector_pct": {},
        "cross_events": [],
        "bar_end": bar_end,
        "completed_bar_identity": {
            "timeframe": timeframe,
            "bar_end": bar_end,
            "completed_bar_only": True,
        },
        "price_location": price_location,
        "origin_time": None,
        "confirmed_at": None,
        "provisional": False,
        "release_direction": None,
        "release_confirmed_at": None,
        "invalidation": None,
        "material": False,
        "summary": None,
    }
    if observations < MA_SLOPE_CROSS_READY_BARS:
        return {
            **partial,
            "reason": "SLOPE_CROSS_WARMUP_INSUFFICIENT",
        }

    slope_row = frame.iloc[final_index - MA_SLOPE_LOOKBACK]
    slope_vector = {
        f"ma{period}": (
            (float(final_row[f"MA{period}"]) - float(slope_row[f"MA{period}"]))
            / close
            * 100.0
        )
        for period in MA_CORE_PERIODS
    }
    previous_row = frame.iloc[final_index - 1]
    cross_events = _ma_cross_events(final_row, previous_row)
    partial.update(
        {
            "slope_vector_pct": slope_vector,
            "cross_events": cross_events,
            "material": bool(cross_events),
        }
    )
    partial["summary"] = _ma_state_summary(partial)
    if observations < MA_COMPRESSION_READY_BARS:
        return {
            **partial,
            "reason": "COMPRESSION_CONTEXT_WARMUP_INSUFFICIENT",
        }

    lifecycle: list[Dict[str, Any]] = []
    for index in range(MA_COMPRESSION_READY_BARS - 1, len(frame)):
        row = frame.iloc[index]
        if any(pd.isna(row[f"MA{period}"]) for period in MA_CORE_PERIODS):
            continue
        prior = frame.iloc[
            index - MA_COMPRESSION_REFERENCE_WINDOW:index
        ]["bundle_spread_pct"].dropna()
        prior_abs = frame.iloc[
            index - MA_COMPRESSION_REFERENCE_WINDOW:index
        ]["bundle_spread_abs"].dropna()
        if (
            len(prior) != MA_COMPRESSION_REFERENCE_WINDOW
            or len(prior_abs) != MA_COMPRESSION_REFERENCE_WINDOW
        ):
            continue

        close = float(row["close"])
        ma_values = {f"ma{period}": float(row[f"MA{period}"]) for period in MA_CORE_PERIODS}
        long_value = row[f"MA{MA_OPTIONAL_LONG_PERIOD}"]
        if pd.notna(long_value):
            ma_values[f"ma{MA_OPTIONAL_LONG_PERIOD}"] = float(long_value)
        current_spread = float(row["bundle_spread_pct"])
        threshold = float(prior.quantile(MA_COMPRESSION_QUANTILE))
        historical_percentile = float((prior <= current_spread).mean() * 100.0)
        current_spread_abs = float(row["bundle_spread_abs"])
        threshold_abs = float(prior_abs.quantile(MA_COMPRESSION_QUANTILE))
        historical_percentile_abs = float(
            (prior_abs <= current_spread_abs).mean() * 100.0
        )

        slope_vector: Dict[str, float] = {}
        slope_row = frame.iloc[index - MA_SLOPE_LOOKBACK]
        for period in MA_CORE_PERIODS:
            slope_vector[f"ma{period}"] = (
                (float(row[f"MA{period}"]) - float(slope_row[f"MA{period}"]))
                / close
                * 100.0
            )

        ordering = _ma_ordering(
            ma_values["ma5"],
            ma_values["ma10"],
            ma_values["ma20"],
        )
        previous_row = frame.iloc[index - 1] if index > 0 else None
        cross_events = _ma_cross_events(row, previous_row)
        cross_window = frame.iloc[
            index - MA_COMPRESSION_REFERENCE_WINDOW:index + 1
        ]
        cross_count = 0
        comparison_count = 0
        for pair_index in range(1, len(cross_window)):
            pair_previous = cross_window.iloc[pair_index - 1]
            pair_current = cross_window.iloc[pair_index]
            for fast, slow in (("MA5", "MA10"), ("MA10", "MA20")):
                if pd.isna(pair_previous[fast]) or pd.isna(pair_previous[slow]):
                    continue
                if pd.isna(pair_current[fast]) or pd.isna(pair_current[slow]):
                    continue
                comparison_count += 1
                prev_diff = float(pair_previous[fast]) - float(pair_previous[slow])
                curr_diff = float(pair_current[fast]) - float(pair_current[slow])
                if (prev_diff <= 0 < curr_diff) or (prev_diff >= 0 > curr_diff):
                    cross_count += 1
        crossing_density = (
            float(cross_count / comparison_count)
            if comparison_count
            else 0.0
        )
        previous_spread = (
            float(previous_row["bundle_spread_pct"])
            if previous_row is not None and pd.notna(previous_row["bundle_spread_pct"])
            else current_spread
        )
        previous_spread_abs = (
            float(previous_row["bundle_spread_abs"])
            if previous_row is not None and pd.notna(previous_row["bundle_spread_abs"])
            else current_spread_abs
        )
        spread_change = current_spread - previous_spread
        within_group = abs(ma_values["ma5"] - ma_values["ma10"]) / close * 100.0
        between_group = (
            abs(((ma_values["ma5"] + ma_values["ma10"]) / 2.0) - ma_values["ma20"])
            / close
            * 100.0
        )

        persistence = 0
        for back_index in range(index, -1, -1):
            spread = frame.iloc[back_index]["bundle_spread_pct"]
            spread_abs = frame.iloc[back_index]["bundle_spread_abs"]
            if (
                pd.isna(spread)
                or pd.isna(spread_abs)
                or float(spread) > threshold
                or float(spread_abs) > threshold_abs
            ):
                break
            persistence += 1

        previous = lifecycle[-1] if lifecycle and lifecycle[-1]["index"] == index - 1 else None
        low_spread = (
            current_spread <= threshold
            and current_spread_abs <= threshold_abs
        )
        absolute_contraction = current_spread_abs < previous_spread_abs - 1e-12
        continuing_compression = bool(
            previous
            and previous["state"] in {"COMPRESSING", "COMPRESSED"}
        )
        compressed = low_spread and (
            absolute_contraction
            or continuing_compression
            or current_spread_abs <= 1e-12
        )
        recent_compressed = any(
            record["state"] == "COMPRESSED"
            for record in lifecycle[-MA_SLOPE_LOOKBACK:]
        )
        if compressed:
            state = (
                "COMPRESSED"
                if persistence >= MA_COMPRESSION_PERSISTENCE
                else "COMPRESSING"
            )
        elif recent_compressed and spread_change > 0:
            if ordering == "BULLISH" and all(value > 0 for value in slope_vector.values()):
                state = "RELEASING_UP"
            elif ordering == "BEARISH" and all(value < 0 for value in slope_vector.values()):
                state = "RELEASING_DOWN"
            else:
                state = "WIDE_TREND"
        elif previous and previous["state"] in {"RELEASING_UP", "RELEASING_DOWN"}:
            expected_ordering = "BULLISH" if previous["state"] == "RELEASING_UP" else "BEARISH"
            expected_sign = 1.0 if expected_ordering == "BULLISH" else -1.0
            if (
                ordering == expected_ordering
                and spread_change >= 0
                and all(value * expected_sign > 0 for value in slope_vector.values())
            ):
                state = previous["state"]
            else:
                state = "FAILED_RELEASE"
        else:
            state = "WIDE_TREND"

        current_date = row["date"].isoformat()
        same_compression_lifecycle = (
            previous
            and previous["state"] in {"COMPRESSING", "COMPRESSED"}
            and state in {"COMPRESSING", "COMPRESSED"}
        )
        same_release_lifecycle = (
            previous
            and previous["state"] in {"RELEASING_UP", "RELEASING_DOWN"}
            and state in {previous["state"], "FAILED_RELEASE"}
        )
        origin_time = (
            previous["origin_time"]
            if previous and (previous["state"] == state or same_compression_lifecycle or same_release_lifecycle)
            else current_date
        )
        confirmed_at = None
        provisional = state in {"COMPRESSING", "RELEASING_UP", "RELEASING_DOWN"}
        if state == "COMPRESSED":
            confirmed_at = (
                previous.get("confirmed_at")
                if previous and previous["state"] == "COMPRESSED"
                else current_date
            )
        elif state in {"WIDE_TREND", "FAILED_RELEASE"}:
            confirmed_at = current_date

        lifecycle.append(
            {
                "index": index,
                "state": state,
                "origin_time": origin_time,
                "confirmed_at": confirmed_at,
                "provisional": provisional,
                "ordering": ordering,
                "ma_values": ma_values,
                "slope_vector_pct": slope_vector,
                "cross_events": cross_events,
                "within_group_spread_pct": within_group,
                "between_group_spread_pct": between_group,
                "bundle_spread_pct": current_spread,
                "bundle_spread_abs": current_spread_abs,
                "bundle_spread_change_pct_points": spread_change,
                "compression_threshold_pct": threshold,
                "compression_threshold_abs": threshold_abs,
                "historical_percentile": historical_percentile,
                "historical_percentile_abs": historical_percentile_abs,
                "crossing_density": crossing_density,
                "compression_persistence": persistence,
                "bar_end": current_date,
                "price_location": (
                    "ABOVE_BUNDLE"
                    if close > max(ma_values["ma5"], ma_values["ma10"], ma_values["ma20"])
                    else "BELOW_BUNDLE"
                    if close < min(ma_values["ma5"], ma_values["ma10"], ma_values["ma20"])
                    else "INSIDE_BUNDLE"
                ),
            }
        )

    if not lifecycle:
        return {
            **partial,
            "status": "PARTIAL",
            "state": "UNKNOWN",
            "reason": "REFERENCE_WINDOW_INSUFFICIENT",
        }

    final = dict(lifecycle[-1])
    final.pop("index", None)
    state = final["state"]
    confirmed, structure_state, volume_status = _ma_release_confirmation(
        state,
        trend_result=trend_result,
        structure_context=structure_context,
    )
    if state in {"RELEASING_UP", "RELEASING_DOWN"} and confirmed:
        final["provisional"] = False
        final["confirmed_at"] = final["bar_end"]
        final["release_confirmed_at"] = final["bar_end"]
    else:
        final["release_confirmed_at"] = None

    release_direction = (
        "UP" if state == "RELEASING_UP"
        else "DOWN" if state == "RELEASING_DOWN"
        else None
    )
    invalidation = None
    if state == "RELEASING_UP":
        invalidation = "BULLISH_ORDER_BREAK_OR_SPREAD_REENTERS_COMPRESSION"
    elif state == "RELEASING_DOWN":
        invalidation = "BEARISH_ORDER_BREAK_OR_SPREAD_REENTERS_COMPRESSION"
    elif state in {"COMPRESSING", "COMPRESSED"}:
        invalidation = "SPREAD_EXPANDS_WITHOUT_VALID_RELEASE_LIFECYCLE"

    evidence = {
        **base,
        **final,
        "status": "READY",
        "reason": "MA_STRUCTURE_READY",
        "observations": observations,
        "readiness": _readiness(
            observations,
            MA_COMPRESSION_REFERENCE_WINDOW,
        ),
        "completed_bar_identity": {
            "timeframe": timeframe,
            "bar_end": final["bar_end"],
            "completed_bar_only": True,
        },
        "release_direction": release_direction,
        "structure_context": structure_state,
        "volume_price_context": volume_status,
        "invalidation": invalidation,
        "material": bool(
            state in {
                "COMPRESSING",
                "COMPRESSED",
                "RELEASING_UP",
                "RELEASING_DOWN",
                "FAILED_RELEASE",
            }
            or final["cross_events"]
        ),
    }
    evidence["summary"] = _ma_state_summary(evidence)
    return evidence


def _trend_direction(trend_status: Any) -> str:
    text = str(getattr(trend_status, "value", trend_status) or "")
    if "多头" in text:
        return "BULLISH"
    if "空头" in text:
        return "BEARISH"
    if "盘整" in text:
        return "NEUTRAL"
    return "UNKNOWN"


def _trend_projection(result: Any) -> Dict[str, Any]:
    return {
        "status": "READY",
        "trend_status": str(getattr(getattr(result, "trend_status", None), "value", "")) or None,
        "direction": _trend_direction(getattr(result, "trend_status", None)),
        "ma_alignment": str(getattr(result, "ma_alignment", "") or "") or None,
        "trend_strength": getattr(result, "trend_strength", None),
        "ma5": getattr(result, "ma5", None),
        "ma10": getattr(result, "ma10", None),
        "ma20": getattr(result, "ma20", None),
        "volume_status": str(getattr(getattr(result, "volume_status", None), "value", "")) or None,
        "volume_ratio_5bar": getattr(result, "volume_ratio_5d", None),
        "macd_status": str(getattr(getattr(result, "macd_status", None), "value", "")) or None,
        "macd_signal": describe_macd_state(result) or None,
        "rsi_status": str(getattr(getattr(result, "rsi_status", None), "value", "")) or None,
        "rsi_signal": str(getattr(result, "rsi_signal", "") or "") or None,
        "independent_action_authority": False,
        "signal_score_consumed": False,
        "buy_signal_consumed": False,
    }


def _structure_text(context: Dict[str, Any]) -> Optional[str]:
    event = context.get("structure_event") if isinstance(context, dict) else None
    state = str((event or {}).get("state") or "") if isinstance(event, dict) else ""
    labels = {
        "UP_BREAKOUT": "结构向上突破已确认",
        "UP_BREAKOUT_RETEST_HOLD": "突破后回踩仍守住结构位",
        "FAILED_UP_BREAKOUT": "向上突破失败并回到结构位下方",
        "DOWN_BREAKOUT": "结构向下跌破已确认",
        "DOWN_BREAKOUT_RETEST_HOLD": "跌破后反抽未收复结构位",
        "FAILED_DOWN_BREAKOUT": "向下跌破失败并重新收复结构位",
    }
    return labels.get(state)


def _human_summary(
    trend: Optional[Dict[str, Any]],
    structure: Dict[str, Any],
    ma_structure: Optional[Dict[str, Any]] = None,
) -> Optional[str]:
    parts = []
    if isinstance(trend, dict):
        if trend.get("ma_alignment"):
            parts.append(str(trend["ma_alignment"]))
        if isinstance(ma_structure, dict) and ma_structure.get("material") and ma_structure.get("summary"):
            parts.append(str(ma_structure["summary"]))
        if trend.get("volume_status"):
            parts.append(str(trend["volume_status"]))
        signal = str(trend.get("macd_signal") or "").strip()
        if signal and signal != "数据不足":
            parts.append(signal.lstrip("⭐⚡✅❌⚠✓ "))
    structure_text = _structure_text(structure)
    if structure_text:
        parts.append(structure_text)
    return "；".join(parts) or None


def _empty_timeframe(key: str, *, status: str, reason: str) -> Dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "role": _ROLE[key],
        "summary": None,
        "independent_action_authority": False,
    }


def _build_higher_timeframe(
    *,
    key: str,
    timeframe: str,
    period_freq: str,
    stock_code: str,
    market: Optional[str],
    target_date: date,
    daily_frame: pd.DataFrame,
    trend_analyzer: Any,
) -> Dict[str, Any]:
    completed_through = resolve_completed_timeframe_bar_date(market, target_date, timeframe)
    if completed_through is None:
        return _empty_timeframe(key, status="MISSING", reason="COMPLETED_PERIOD_UNPROVEN")
    bars = _aggregate_completed_bars(
        daily_frame,
        completed_through=completed_through,
        period_freq=period_freq,
    )
    if bars.empty:
        return _empty_timeframe(key, status="MISSING", reason="COMPLETED_BARS_MISSING")
    latest_bar_date = bars.iloc[-1]["date"]
    base = {
        "role": _ROLE[key],
        "timeframe": timeframe,
        "completed_bar_only": True,
        "completed_through": completed_through.isoformat(),
        "latest_bar_date": latest_bar_date.isoformat(),
        "observations": int(len(bars)),
        "source_alignment": _source_alignment(bars),
        "algorithm_version": ALGORITHM_VERSION,
        "config_hash": CONFIG_HASH,
        "independent_action_authority": False,
    }
    if latest_bar_date != completed_through:
        return {
            **base,
            "status": "PARTIAL",
            "reason": "COMPLETED_PERIOD_END_BAR_MISSING",
            "summary": None,
            "historical_replay_eligible": False,
        }
    if base["source_alignment"]["status"] != "SINGLE_SOURCE":
        return {
            **base,
            "status": "PARTIAL",
            "reason": "SOURCE_ALIGNMENT_UNPROVEN",
            "summary": None,
            "historical_replay_eligible": False,
        }

    structure = build_price_structure_context(
        stock_code=stock_code,
        history=bars,
        target_date=latest_bar_date,
        market=market,
    )
    trend_result = None
    trend = None
    if len(bars) >= TREND_MIN_BARS:
        trend_result = trend_analyzer.analyze(bars.copy(), stock_code)
        trend = _trend_projection(trend_result)

    ma_structure = build_ma_structure_evidence(
        bars,
        timeframe=key,
        trend_result=trend_result,
        structure_context=structure,
    )
    structure_ready = structure.get("status") == "READY"
    trend_ready = trend is not None
    if trend_ready and structure_ready:
        status, reason = "READY", "TIMEFRAME_READY"
    elif trend_ready or structure_ready:
        status, reason = "PARTIAL", "TIMEFRAME_PARTIAL_EVIDENCE"
    else:
        status, reason = "MISSING", "WARMUP_INSUFFICIENT"
    return {
        **base,
        "status": status,
        "reason": reason,
        "trend": trend or {"status": "MISSING", "reason": "WARMUP_INSUFFICIENT"},
        "ma_structure": ma_structure,
        "price_structure": structure,
        "summary": _human_summary(trend, structure, ma_structure),
        "historical_replay_eligible": bool(
            structure.get("historical_replay_eligible") and status in {"READY", "PARTIAL"}
        ),
    }


def build_multi_timeframe_structure_context(
    *,
    stock_code: str,
    history: Any,
    target_date: Optional[date],
    market: Optional[str],
    trend_analyzer: Any,
    daily_trend_result: Any = None,
    daily_price_structure_context: Any = None,
    snapshot_observed_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Build one coverage-aware M/W/D structure carrier from completed daily bars."""
    base = {
        "schema_version": SCHEMA_VERSION,
        "family": "multi_timeframe_structure",
        "stock_code": stock_code,
        "market": str(market or "").lower() or None,
        "completed_bar_only": True,
        "algorithm_version": ALGORITHM_VERSION,
        "config_hash": CONFIG_HASH,
        "independent_action_authority": False,
        "cross_timeframe_vote_counting": False,
        # Cross-run history is eligible only after the typed daily identity
        # proves the consumed provider route, adjustment basis and units.
        "cross_run_persistence_policy": CROSS_RUN_PERSISTENCE_POLICY,
        "cross_run_persistence_eligible": False,
        "cross_run_persistence_reason": "ADJUSTMENT_BASIS_NOT_PERSISTED",
    }
    missing_intraday = {
        key: _empty_timeframe(
            key,
            status="MISSING",
            reason="INTRADAY_COMPLETED_BAR_CONTRACT_NOT_READY",
        )
        for key in ("60m", "30m", "15m", "5m")
    }
    if not isinstance(target_date, date):
        return {
            **base,
            "status": "UNKNOWN",
            "reason": "TARGET_DATE_UNKNOWN",
            "timeframes": {
                "monthly": _empty_timeframe("monthly", status="MISSING", reason="TARGET_DATE_UNKNOWN"),
                "weekly": _empty_timeframe("weekly", status="MISSING", reason="TARGET_DATE_UNKNOWN"),
                "daily": _empty_timeframe("daily", status="MISSING", reason="TARGET_DATE_UNKNOWN"),
                **missing_intraday,
            },
            "historical_replay_eligible": False,
        }

    frame = _normalize_daily_history(history, target_date=target_date)
    base["target_date"] = target_date.isoformat()
    if not frame.empty:
        base.update(
            build_completed_history_identity(
                frame,
                stock_code=stock_code,
                market=market,
                target_date=target_date,
                observed_at=snapshot_observed_at,
            )
        )
        identity_reasons = list(base.get("price_identity_reasons") or [])
        if base.get("adjustment_basis") and not identity_reasons:
            base["cross_run_persistence_eligible"] = True
            base["cross_run_persistence_reason"] = "DAILY_DATA_IDENTITY_READY"
        else:
            base["cross_run_persistence_eligible"] = False
            base["cross_run_persistence_reason"] = (
                "ADJUSTMENT_BASIS_NOT_PERSISTED"
                if "ADJUSTMENT_BASIS_NOT_PERSISTED" in identity_reasons
                else (identity_reasons[0] if identity_reasons else "DAILY_DATA_IDENTITY_MISSING")
            )
    if frame.empty or frame.iloc[-1]["date"] != target_date:
        reason = "COMPLETED_DAILY_HISTORY_MISSING" if frame.empty else "TARGET_DATE_BAR_MISSING"
        return {
            **base,
            "status": "MISSING",
            "reason": reason,
            "timeframes": {
                "monthly": _empty_timeframe("monthly", status="MISSING", reason=reason),
                "weekly": _empty_timeframe("weekly", status="MISSING", reason=reason),
                "daily": _empty_timeframe("daily", status="MISSING", reason=reason),
                **missing_intraday,
            },
            "historical_replay_eligible": False,
        }
    if _invalid_ohlcv(frame):
        return {
            **base,
            "status": "UNKNOWN",
            "reason": "INVALID_OHLCV",
            "timeframes": {
                "monthly": _empty_timeframe("monthly", status="UNKNOWN", reason="INVALID_OHLCV"),
                "weekly": _empty_timeframe("weekly", status="UNKNOWN", reason="INVALID_OHLCV"),
                "daily": _empty_timeframe("daily", status="UNKNOWN", reason="INVALID_OHLCV"),
                **missing_intraday,
            },
            "historical_replay_eligible": False,
        }

    alignment = _source_alignment(frame)
    daily_summary = None
    daily_direction = "UNKNOWN"
    if daily_trend_result is not None:
        daily_direction = _trend_direction(getattr(daily_trend_result, "trend_status", None))
        daily_summary = str(getattr(daily_trend_result, "ma_alignment", "") or "") or None
    daily_structure = (
        daily_price_structure_context
        if isinstance(daily_price_structure_context, dict)
        else {}
    )
    daily_ma_structure = build_ma_structure_evidence(
        frame,
        timeframe="daily",
        trend_result=daily_trend_result,
        structure_context=daily_structure,
    )
    if alignment["status"] != "SINGLE_SOURCE":
        daily_ma_structure = {
            **daily_ma_structure,
            "status": "MISSING",
            "state": "UNKNOWN",
            "reason": "SOURCE_ALIGNMENT_UNPROVEN",
            "material": False,
            "summary": None,
        }
    daily = {
        "status": "READY"
        if daily_trend_result is not None and alignment["status"] == "SINGLE_SOURCE"
        else "PARTIAL",
        "reason": "DAILY_REFERENCE_READY"
        if daily_trend_result is not None
        else "DAILY_TREND_MISSING",
        "role": _ROLE["daily"],
        "timeframe": "1d",
        "completed_bar_only": True,
        "completed_through": target_date.isoformat(),
        "latest_bar_date": target_date.isoformat(),
        "observations": int(len(frame)),
        "source_alignment": alignment,
        "trend": {"status": "READY", "direction": daily_direction}
        if daily_trend_result is not None
        else {"status": "MISSING"},
        "ma_structure": daily_ma_structure,
        "price_structure": daily_structure,
        "summary": _human_summary(
            _trend_projection(daily_trend_result)
            if daily_trend_result is not None
            else None,
            daily_structure,
            daily_ma_structure,
        )
        or daily_summary,
        "independent_action_authority": False,
        "historical_replay_eligible": bool(
            daily_structure.get("historical_replay_eligible")
        ),
    }
    monthly = _build_higher_timeframe(
        key="monthly",
        timeframe="1mo",
        period_freq="M",
        stock_code=stock_code,
        market=market,
        target_date=target_date,
        daily_frame=frame,
        trend_analyzer=trend_analyzer,
    )
    weekly = _build_higher_timeframe(
        key="weekly",
        timeframe="1w",
        period_freq="W-FRI",
        stock_code=stock_code,
        market=market,
        target_date=target_date,
        daily_frame=frame,
        trend_analyzer=trend_analyzer,
    )

    directions = []
    for item in (monthly, weekly, daily):
        trend = item.get("trend") if isinstance(item, dict) else None
        direction = (
            str((trend or {}).get("direction") or "UNKNOWN")
            if isinstance(trend, dict)
            else "UNKNOWN"
        )
        if direction in {"BULLISH", "BEARISH", "NEUTRAL"}:
            directions.append(direction)
    if not directions:
        alignment_state = "DATA_INSUFFICIENT"
    elif len(set(directions)) == 1:
        alignment_state = f"ALIGNED_{directions[0]}"
    elif len(directions) == 1:
        alignment_state = "SINGLE_TIMEFRAME"
    else:
        alignment_state = "MIXED"

    ready_high = [
        item for item in (monthly, weekly) if item.get("status") in {"READY", "PARTIAL"}
    ]
    return {
        **base,
        "status": "READY" if ready_high else "PARTIAL",
        "reason": "MTF_HIGHER_TIMEFRAME_AVAILABLE"
        if ready_high
        else "HIGHER_TIMEFRAME_NOT_READY",
        "source_alignment": alignment,
        "timeframes": {
            "monthly": monthly,
            "weekly": weekly,
            "daily": daily,
            **missing_intraday,
        },
        "cross_timeframe": {
            "trend_alignment": alignment_state,
            "rule": "CONFIRMATION_OR_CONFLICT_NOT_INDEPENDENT_VOTES",
        },
        "historical_replay_eligible": bool(
            daily.get("historical_replay_eligible")
            and all(item.get("historical_replay_eligible") for item in ready_high)
        )
        if ready_high
        else False,
    }
