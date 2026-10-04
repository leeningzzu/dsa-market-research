"""Canonical completed A-share intraday bars and deterministic session aggregation."""

from __future__ import annotations

from datetime import datetime, time, timezone
from hashlib import sha256
import json
from typing import Any

import pandas as pd

from data_provider.intraday_data_identity import (
    INTRADAY_DATA_IDENTITY_HASH_COLUMN,
    IntradayDataIdentityError,
    attach_intraday_data_identity,
    build_intraday_data_identity,
    extract_intraday_data_identity,
    intraday_amount_unit_is_admitted,
)


SCHEMA_VERSION = "intraday-bar-service-v1"
ALGORITHM_VERSION = "cn-completed-5m-session-aggregate-v1"
CN_TIMEZONE = "Asia/Shanghai"
CN_SESSION_CALENDAR = "XSHG"
_TARGET_SIZE = {"15m": 3, "30m": 6, "60m": 12}
_CONFIG = {
    "algorithm_version": ALGORITHM_VERSION,
    "source_timeframe": "5m",
    "targets": _TARGET_SIZE,
    "morning_right_labels": ["09:35", "11:30"],
    "afternoon_right_labels": ["13:05", "15:00"],
    "partial_bar_policy": "EXCLUDE_IF_BAR_END_AFTER_OBSERVED_AT",
    "lunch_crossing": "FORBIDDEN",
}
CONFIG_HASH = sha256(json.dumps(_CONFIG, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class IntradayBarError(ValueError):
    """Raised when completed intraday-bar invariants are violated."""


def normalize_cn_completed_5m_bars(
    frame: pd.DataFrame,
    *,
    stock_code: str,
    data_source: str,
    observed_at: datetime,
) -> pd.DataFrame:
    required = ["time", "open", "high", "low", "close", "volume"]
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return _empty_frame()
    if any(column not in frame.columns for column in required):
        raise IntradayBarError("5m source frame is missing required columns")
    observed = _market_timestamp(observed_at)
    working = frame.copy()
    raw_time = working["time"].astype(str)
    if (~raw_time.str.match(r"^\d{14,17}$")).any():
        raise IntradayBarError("invalid BaoStock intraday time format")
    parsed = pd.to_datetime(raw_time.str.slice(0, 14), format="%Y%m%d%H%M%S", errors="coerce")
    if parsed.isna().any():
        raise IntradayBarError("invalid intraday timestamp")
    working["bar_end"] = parsed.dt.tz_localize(CN_TIMEZONE)
    if "date" in working.columns:
        source_dates = pd.to_datetime(working["date"], errors="coerce")
        if source_dates.isna().any():
            raise IntradayBarError("invalid intraday source date")
        if (source_dates.dt.strftime("%Y-%m-%d") != working["bar_end"].dt.strftime("%Y-%m-%d")).any():
            raise IntradayBarError("source date does not match intraday time")
    working = working[working["bar_end"] <= observed].copy()
    if working.empty:
        return _empty_frame()
    if working["bar_end"].duplicated().any():
        raise IntradayBarError("duplicate 5m bar_end")
    working = working.sort_values("bar_end", kind="stable")
    sessions = []
    for value in working["bar_end"]:
        label = _session_label(pd.Timestamp(value))
        if label is None:
            raise IntradayBarError(f"off-session 5m bar_end: {value}")
        sessions.append(label)
    working["session"] = sessions
    for column in ("open", "high", "low", "close", "volume", "amount"):
        if column not in working.columns:
            continue
        working[column] = pd.to_numeric(working[column], errors="coerce")
        if working[column].isna().any():
            raise IntradayBarError(f"non-numeric intraday column: {column}")
    if (working[["open", "high", "low", "close"]] <= 0).any().any():
        raise IntradayBarError("non-positive intraday OHLC")
    if (working["volume"] < 0).any():
        raise IntradayBarError("negative intraday volume")
    if "amount" in working.columns and (working["amount"] < 0).any():
        raise IntradayBarError("negative intraday amount")
    if (
        (working["high"] < working[["open", "close", "low"]].max(axis=1))
        | (working["low"] > working[["open", "close", "high"]].min(axis=1))
    ).any():
        raise IntradayBarError("invalid intraday OHLC envelope")
    working["code"] = str(stock_code).strip()
    working["data_source"] = str(data_source).strip()
    if "adjustflag" not in working.columns:
        working["adjustflag"] = ""
    working["available_at"] = observed
    columns = ["code", "bar_end", "open", "high", "low", "close", "volume"]
    if "amount" in working.columns:
        columns.append("amount")
    columns.extend(["session", "available_at", "adjustflag", "data_source"])
    return working[columns].reset_index(drop=True)


def validate_complete_cn_5m_sessions(
    frame: pd.DataFrame,
    *,
    expected_session_dates: list[Any],
) -> dict[str, Any]:
    """Validate an exact closed set of complete XSHG 5m sessions."""
    try:
        identity = extract_intraday_data_identity(frame, strict=True)
    except IntradayDataIdentityError as exc:
        raise IntradayBarError(f"invalid canonical 5m identity: {exc}") from exc
    if identity is None or identity.get("timeframe") != "5m":
        raise IntradayBarError("complete-session validation requires canonical 5m identity")
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise IntradayBarError("complete-session validation requires non-empty 5m bars")
    required = {"bar_end", "session"}
    if not required.issubset(frame.columns):
        raise IntradayBarError("canonical 5m frame is missing session validation columns")

    normalized_expected: list[str] = []
    for value in list(expected_session_dates or []):
        try:
            text = pd.Timestamp(value).date().isoformat()
        except Exception as exc:
            raise IntradayBarError("expected session date is invalid") from exc
        if text in normalized_expected:
            raise IntradayBarError("expected session dates contain duplicates")
        normalized_expected.append(text)
    if not normalized_expected:
        raise IntradayBarError("expected session dates must be non-empty")
    if normalized_expected != sorted(normalized_expected):
        raise IntradayBarError("expected session dates must be strictly increasing")

    working = frame.copy()
    working["bar_end"] = pd.to_datetime(working["bar_end"], errors="coerce")
    if working["bar_end"].isna().any():
        raise IntradayBarError("canonical 5m frame contains invalid bar_end")
    local_dates: list[str] = []
    for value in working["bar_end"]:
        ts = pd.Timestamp(value)
        if ts.tzinfo is None:
            raise IntradayBarError("canonical 5m bar_end must be timezone-aware")
        local_dates.append(ts.tz_convert(CN_TIMEZONE).date().isoformat())
    working["_session_date"] = local_dates

    actual_dates = sorted(set(local_dates))
    if actual_dates != normalized_expected:
        raise IntradayBarError(
            f"5m session date mismatch: expected={normalized_expected}, actual={actual_dates}"
        )

    for session_date in normalized_expected:
        day = working[working["_session_date"] == session_date].copy()
        expected_labels = (
            _expected_half_session_labels(session_date, "AM")
            + _expected_half_session_labels(session_date, "PM")
        )
        observed_labels = [
            pd.Timestamp(value).tz_convert(CN_TIMEZONE)
            for value in day["bar_end"].tolist()
        ]
        if observed_labels != expected_labels:
            raise IntradayBarError(f"incomplete or noncanonical 5m session: {session_date}")
        expected_sessions = [f"{session_date}:AM"] * 24 + [f"{session_date}:PM"] * 24
        if day["session"].astype(str).tolist() != expected_sessions:
            raise IntradayBarError(f"5m session labels mismatch: {session_date}")

    return {
        "schema_version": "cn-complete-5m-session-set-v1",
        "identity_hash": identity["identity_hash"],
        "expected_session_dates": normalized_expected,
        "session_count": len(normalized_expected),
        "row_count": int(len(frame)),
        "bars_per_session": 48,
    }


def aggregate_completed_intraday_bars(
    frame: pd.DataFrame,
    *,
    target_timeframe: str,
) -> pd.DataFrame:
    target = str(target_timeframe or "").strip().lower()
    if target not in _TARGET_SIZE:
        raise IntradayBarError("target timeframe must be 15m, 30m or 60m")
    try:
        identity = extract_intraday_data_identity(frame, strict=True)
    except IntradayDataIdentityError as exc:
        raise IntradayBarError(f"invalid source intraday identity: {exc}") from exc
    if identity is None or identity.get("timeframe") != "5m":
        raise IntradayBarError("aggregation requires one canonical 5m identity")
    if frame.empty:
        return _empty_frame()
    if INTRADAY_DATA_IDENTITY_HASH_COLUMN in frame.columns:
        hashes = {str(value or "").strip().lower() for value in frame[INTRADAY_DATA_IDENTITY_HASH_COLUMN]}
        if hashes != {identity["identity_hash"]}:
            raise IntradayBarError("mixed intraday source identity")
    required = {"code", "bar_end", "open", "high", "low", "close", "volume", "session", "available_at", "data_source"}
    if not required.issubset(frame.columns):
        raise IntradayBarError("canonical 5m frame is missing aggregation columns")
    working = frame.copy()
    working["bar_end"] = pd.to_datetime(working["bar_end"], errors="coerce")
    working["available_at"] = pd.to_datetime(working["available_at"], errors="coerce")
    if working["bar_end"].isna().any() or working["available_at"].isna().any():
        raise IntradayBarError("invalid aggregation timestamps")
    if working["bar_end"].duplicated().any():
        raise IntradayBarError("duplicate canonical 5m bar_end")
    if any(a < b for a, b in zip(working["available_at"], working["bar_end"])):
        raise IntradayBarError("source availability precedes bar_end")
    if working["code"].nunique(dropna=False) != 1 or working["data_source"].nunique(dropna=False) != 1:
        raise IntradayBarError("mixed code/provider in canonical 5m frame")
    include_amount = "amount" in working.columns and intraday_amount_unit_is_admitted(identity)
    size = _TARGET_SIZE[target]
    rows: list[dict[str, Any]] = []
    dropped = 0
    for session_name, session_frame in working.groupby("session", sort=True):
        session_frame = session_frame.sort_values("bar_end", kind="stable")
        if session_frame.empty:
            continue
        date_text, half = _parse_session_name(str(session_name))
        expected = _expected_half_session_labels(date_text, half)
        by_end = {pd.Timestamp(row["bar_end"]): row for row in session_frame.to_dict("records")}
        for offset in range(0, len(expected), size):
            window = expected[offset : offset + size]
            if len(window) != size or not all(item in by_end for item in window):
                dropped += 1
                continue
            source_rows = [by_end[item] for item in window]
            item = {
                "code": source_rows[0]["code"],
                "bar_end": window[-1],
                "open": float(source_rows[0]["open"]),
                "high": max(float(row["high"]) for row in source_rows),
                "low": min(float(row["low"]) for row in source_rows),
                "close": float(source_rows[-1]["close"]),
                "volume": sum(float(row["volume"]) for row in source_rows),
                "session": session_name,
                "available_at": max(pd.Timestamp(row["available_at"]) for row in source_rows),
                "adjustflag": str(source_rows[0].get("adjustflag") or ""),
                "data_source": source_rows[0]["data_source"],
            }
            if include_amount:
                item["amount"] = sum(float(row["amount"]) for row in source_rows)
            rows.append(item)
    result = pd.DataFrame(rows)
    if result.empty:
        result = _empty_frame(include_amount=include_amount)
    else:
        result = result.sort_values("bar_end", kind="stable").reset_index(drop=True)
    parent_hash = identity["identity_hash"]
    derived = build_intraday_data_identity(
        result,
        provider_identity=identity["provider_identity"],
        provider_route=f"{identity['provider_route']}|{ALGORITHM_VERSION}",
        package_version=identity["package_version"],
        query_identity=identity["query_identity"],
        requested_adjustment_basis=identity.get("requested_adjustment_basis"),
        observed_adjustment_basis=identity.get("observed_adjustment_basis"),
        basis_evidence=identity["basis_evidence"],
        timeframe=target,
        timezone_name=identity["timezone"],
        session_calendar=identity["session_calendar"],
        currency=identity["currency"],
        volume_unit=identity["volume_unit"],
        amount_unit=identity["amount_unit"] if include_amount else "UNKNOWN",
        requested_start=identity.get("requested_start"),
        requested_end=identity.get("requested_end"),
        identity_state=identity["identity_state"],
        observed_at=_parse_utc(identity["observed_at"]),
        source_rows_sha256=identity.get("source_rows_sha256"),
        parent_identity_hash=parent_hash,
        derivation=f"{ALGORITHM_VERSION}:{target}",
    )
    attach_intraday_data_identity(result, derived)
    result[INTRADAY_DATA_IDENTITY_HASH_COLUMN] = derived["identity_hash"]
    result.attrs["intraday_aggregation_receipt"] = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "config_hash": CONFIG_HASH,
        "source_timeframe": "5m",
        "target_timeframe": target,
        "input_rows": int(len(frame)),
        "output_rows": int(len(result)),
        "dropped_incomplete_windows": int(dropped),
        "parent_identity_hash": parent_hash,
        "output_identity_hash": derived["identity_hash"],
    }
    return result


def _empty_frame(*, include_amount: bool = True) -> pd.DataFrame:
    columns = ["code", "bar_end", "open", "high", "low", "close", "volume"]
    if include_amount:
        columns.append("amount")
    columns.extend(["session", "available_at", "adjustflag", "data_source"])
    return pd.DataFrame(columns=columns)


def _market_timestamp(value: datetime) -> pd.Timestamp:
    if not isinstance(value, datetime):
        raise IntradayBarError("observed_at must be datetime")
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize(timezone.utc)
    return ts.tz_convert(CN_TIMEZONE)


def _session_label(value: pd.Timestamp) -> str | None:
    local = value.tz_convert(CN_TIMEZONE)
    clock = local.time().replace(tzinfo=None)
    if _is_right_label(clock, time(9, 35), time(11, 30)):
        return f"{local.date().isoformat()}:AM"
    if _is_right_label(clock, time(13, 5), time(15, 0)):
        return f"{local.date().isoformat()}:PM"
    return None


def _is_right_label(clock: time, start: time, end: time) -> bool:
    minute = clock.hour * 60 + clock.minute
    first = start.hour * 60 + start.minute
    last = end.hour * 60 + end.minute
    return clock.second == 0 and first <= minute <= last and (minute - first) % 5 == 0


def _parse_session_name(value: str) -> tuple[str, str]:
    if value.endswith(":AM"):
        return value[:-3], "AM"
    if value.endswith(":PM"):
        return value[:-3], "PM"
    raise IntradayBarError("invalid canonical session label")


def _expected_half_session_labels(date_text: str, half: str) -> list[pd.Timestamp]:
    if half == "AM":
        start, end = "09:35:00", "11:30:00"
    elif half == "PM":
        start, end = "13:05:00", "15:00:00"
    else:
        raise IntradayBarError("invalid half-session")
    return list(pd.date_range(f"{date_text} {start}", f"{date_text} {end}", freq="5min", tz=CN_TIMEZONE))


def _parse_utc(value: str) -> datetime:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    return ts.tz_convert("UTC").to_pydatetime()
