"""Typed provenance for canonical completed intraday market-data frames."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import math
from typing import Any, Optional

import pandas as pd

from .daily_data_identity import canonical_json, sha256_payload


INTRADAY_DATA_IDENTITY_SCHEMA_VERSION = "intraday-data-identity-v1"
INTRADAY_DATA_IDENTITY_FRAME_ATTR = "intraday_data_identity"
INTRADAY_DATA_IDENTITY_HASH_COLUMN = "intraday_data_identity_hash"

_IDENTITY_STATES = frozenset({"OBSERVED", "UNCLASSIFIED"})
_ADJUSTMENT_BASES = frozenset({"raw", "qfq", "hfq", "total_return"})
_TIMEFRAMES = frozenset({"5m", "15m", "30m", "60m"})
_CONTENT_COLUMNS = (
    "code",
    "bar_end",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "session",
    "available_at",
    "adjustflag",
    "data_source",
)


class IntradayDataIdentityError(ValueError):
    """Raised when intraday provenance is malformed or inconsistent."""


def build_intraday_data_identity(
    frame: pd.DataFrame,
    *,
    provider_identity: str,
    provider_route: str,
    package_version: str,
    query_identity: Mapping[str, Any],
    requested_adjustment_basis: Optional[str],
    observed_adjustment_basis: Optional[str],
    basis_evidence: str,
    timeframe: str,
    timezone_name: str,
    session_calendar: str,
    currency: str,
    volume_unit: str,
    amount_unit: str,
    requested_start: Optional[str],
    requested_end: Optional[str],
    identity_state: str,
    observed_at: Optional[datetime] = None,
    source_rows_sha256: Optional[str] = None,
    parent_identity_hash: Optional[str] = None,
    derivation: Optional[str] = None,
) -> dict[str, Any]:
    returned_start, returned_end = _returned_range(frame)
    payload: dict[str, Any] = {
        "schema_version": INTRADAY_DATA_IDENTITY_SCHEMA_VERSION,
        "identity_state": str(identity_state or "").strip().upper(),
        "provider_identity": str(provider_identity or "").strip(),
        "provider_route": str(provider_route or "").strip(),
        "package_version": str(package_version or "").strip(),
        "query_identity": dict(query_identity),
        "requested_adjustment_basis": _basis(requested_adjustment_basis),
        "observed_adjustment_basis": _basis(observed_adjustment_basis),
        "basis_evidence": str(basis_evidence or "").strip(),
        "timeframe": str(timeframe or "").strip().lower(),
        "timezone": str(timezone_name or "").strip(),
        "session_calendar": str(session_calendar or "").strip(),
        "currency": str(currency or "").strip().upper(),
        "volume_unit": str(volume_unit or "").strip() or "UNKNOWN",
        "amount_unit": str(amount_unit or "").strip() or "UNKNOWN",
        "requested_start": _optional_text(requested_start),
        "requested_end": _optional_text(requested_end),
        "returned_start": returned_start,
        "returned_end": returned_end,
        "observed_at": _utc_text(observed_at or datetime.now(timezone.utc)),
        "available_at_max": _available_at_max(frame),
        "source_rows_sha256": _optional_sha256(source_rows_sha256),
        "content_sha256": sha256_payload(_frame_content_payload(frame)),
        "row_count": int(len(frame)) if isinstance(frame, pd.DataFrame) else 0,
        "parent_identity_hash": _optional_sha256(parent_identity_hash),
        "derivation": _optional_text(derivation),
    }
    payload["identity_hash"] = _identity_hash(payload)
    return validate_intraday_data_identity(payload)


def validate_intraday_data_identity(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IntradayDataIdentityError("intraday data identity must be an object")
    payload = dict(value)
    if payload.get("schema_version") != INTRADAY_DATA_IDENTITY_SCHEMA_VERSION:
        raise IntradayDataIdentityError("intraday schema_version mismatch")
    state = str(payload.get("identity_state") or "").strip().upper()
    if state not in _IDENTITY_STATES:
        raise IntradayDataIdentityError("intraday identity_state is invalid")
    payload["identity_state"] = state
    timeframe = str(payload.get("timeframe") or "").strip().lower()
    if timeframe not in _TIMEFRAMES:
        raise IntradayDataIdentityError("intraday timeframe is invalid")
    payload["timeframe"] = timeframe
    for key in (
        "provider_identity",
        "provider_route",
        "package_version",
        "basis_evidence",
        "timezone",
        "session_calendar",
        "currency",
        "volume_unit",
        "amount_unit",
        "observed_at",
        "content_sha256",
        "identity_hash",
    ):
        if not str(payload.get(key) or "").strip():
            raise IntradayDataIdentityError(f"intraday identity missing {key}")
    if not isinstance(payload.get("query_identity"), Mapping) or not payload["query_identity"]:
        raise IntradayDataIdentityError("intraday query_identity must be a non-empty object")
    observed_basis = _basis(payload.get("observed_adjustment_basis"))
    requested_basis = _basis(payload.get("requested_adjustment_basis"))
    payload["observed_adjustment_basis"] = observed_basis
    payload["requested_adjustment_basis"] = requested_basis
    if state == "OBSERVED" and observed_basis not in _ADJUSTMENT_BASES:
        raise IntradayDataIdentityError("OBSERVED intraday identity requires observed adjustment basis")
    if state == "UNCLASSIFIED" and observed_basis is not None:
        raise IntradayDataIdentityError("UNCLASSIFIED intraday identity cannot claim observed basis")
    for key in ("content_sha256", "identity_hash"):
        if not _is_sha256(payload.get(key)):
            raise IntradayDataIdentityError(f"{key} must be exact 64-hex")
    for key in ("source_rows_sha256", "parent_identity_hash"):
        item = payload.get(key)
        if item is not None and not _is_sha256(item):
            raise IntradayDataIdentityError(f"{key} must be exact 64-hex when present")
    try:
        row_count = int(payload.get("row_count"))
    except (TypeError, ValueError):
        raise IntradayDataIdentityError("row_count must be an integer") from None
    if row_count < 0:
        raise IntradayDataIdentityError("row_count must be non-negative")
    payload["row_count"] = row_count
    if _identity_hash(payload) != str(payload.get("identity_hash") or "").lower():
        raise IntradayDataIdentityError("intraday data identity hash mismatch")
    return payload


def attach_intraday_data_identity(frame: pd.DataFrame, identity: Any) -> pd.DataFrame:
    payload = validate_intraday_data_identity(identity)
    frame.attrs[INTRADAY_DATA_IDENTITY_FRAME_ATTR] = payload
    return frame


def extract_intraday_data_identity(
    frame: Any,
    *,
    strict: bool = False,
) -> Optional[dict[str, Any]]:
    try:
        if not isinstance(frame, pd.DataFrame):
            return None
        value = frame.attrs.get(INTRADAY_DATA_IDENTITY_FRAME_ATTR)
        if value is None:
            return None
        payload = validate_intraday_data_identity(value)
        if payload["row_count"] != int(len(frame)):
            raise IntradayDataIdentityError("intraday frame row_count does not match identity")
        if payload["content_sha256"] != sha256_payload(_frame_content_payload(frame)):
            raise IntradayDataIdentityError("intraday frame content does not match identity")
        hashes = frame.get(INTRADAY_DATA_IDENTITY_HASH_COLUMN)
        if hashes is not None and len(frame):
            observed = {str(item or "").strip().lower() for item in hashes.tolist()}
            if observed != {payload["identity_hash"]}:
                raise IntradayDataIdentityError("mixed intraday identity hashes across rows")
        return payload
    except (IntradayDataIdentityError, TypeError, ValueError):
        if strict:
            raise
        return None


def canonical_intraday_identity_json(identity: Any) -> str:
    return canonical_json(validate_intraday_data_identity(identity))


def intraday_amount_unit_is_admitted(identity: Any) -> bool:
    try:
        payload = validate_intraday_data_identity(identity)
    except IntradayDataIdentityError:
        return False
    return payload.get("amount_unit") not in {None, "", "UNKNOWN"}


def _identity_hash(payload: Mapping[str, Any]) -> str:
    value = {key: item for key, item in payload.items() if key not in {"identity_hash", "observed_at"}}
    return sha256_payload(value)


def _frame_content_payload(frame: Any) -> list[dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    working = frame.copy()
    if "bar_end" not in working.columns:
        raise IntradayDataIdentityError("intraday frame missing bar_end")
    working["bar_end"] = pd.to_datetime(working["bar_end"], errors="coerce")
    if working["bar_end"].isna().any():
        raise IntradayDataIdentityError("intraday frame contains invalid bar_end")
    working = working.sort_values("bar_end", kind="stable")
    records: list[dict[str, Any]] = []
    for row in working.to_dict("records"):
        record: dict[str, Any] = {}
        for key in _CONTENT_COLUMNS:
            value = row.get(key)
            if key in {"bar_end", "available_at"}:
                record[key] = _timestamp_text(value)
            elif key in {"open", "high", "low", "close", "volume", "amount"}:
                record[key] = _finite_number(value)
            else:
                text = str(value or "").strip()
                record[key] = text or None
        records.append(record)
    return records


def _returned_range(frame: Any) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "bar_end" not in frame.columns:
        return None, None
    values = pd.to_datetime(frame["bar_end"], errors="coerce").dropna()
    if values.empty:
        return None, None
    return _timestamp_text(values.min()), _timestamp_text(values.max())


def _available_at_max(frame: Any) -> Optional[str]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "available_at" not in frame.columns:
        return None
    bar_end = pd.to_datetime(frame["bar_end"], errors="coerce")
    available = pd.to_datetime(frame["available_at"], errors="coerce")
    if bar_end.isna().any() or available.isna().any():
        raise IntradayDataIdentityError("invalid bar_end/available_at")
    if any(a < b for a, b in zip(available.tolist(), bar_end.tolist())):
        raise IntradayDataIdentityError("available_at precedes bar_end")
    return _timestamp_text(available.max())


def _basis(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    return text or None


def _optional_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _utc_text(value: Any) -> str:
    if not isinstance(value, datetime):
        raise IntradayDataIdentityError("observed_at must be datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _timestamp_text(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    if ts.tzinfo is None:
        raise IntradayDataIdentityError("intraday timestamps must be timezone-aware")
    return ts.isoformat()


def _finite_number(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _optional_sha256(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    if not text:
        return None
    if not _is_sha256(text):
        raise IntradayDataIdentityError("expected exact 64-hex sha256")
    return text


def _is_sha256(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)
