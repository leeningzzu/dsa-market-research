"""Typed provenance for one normalized daily market-data frame.

The identity travels beside a ``pandas.DataFrame`` and may be serialized into
``StockDaily``.  Provider names and requested parameters are descriptive only;
only observed response/query evidence may prove an adjustment basis.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
import hashlib
import json
import math
from typing import Any, Optional

import pandas as pd


DAILY_DATA_IDENTITY_SCHEMA_VERSION = "daily-data-identity-v1"
DAILY_DATA_IDENTITY_FRAME_ATTR = "daily_data_identity"

_IDENTITY_STATES = frozenset({"OBSERVED", "UNCLASSIFIED"})
_ADJUSTMENT_BASES = frozenset({"raw", "qfq", "hfq", "total_return"})
_CONTENT_COLUMNS = ("date", "open", "high", "low", "close", "volume", "amount", "pct_chg")


class DailyDataIdentityError(ValueError):
    """Raised when a daily-data provenance object is malformed or mismatched."""


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    )


def sha256_payload(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def build_daily_data_identity(
    frame: pd.DataFrame,
    *,
    provider_identity: str,
    provider_route: str,
    actual_response_branch: str,
    requested_adjustment_basis: Optional[str],
    observed_adjustment_basis: Optional[str],
    basis_evidence: str,
    requested_start: Optional[str],
    requested_end: Optional[str],
    currency: str,
    volume_unit: str,
    amount_unit: str,
    identity_state: str,
    observed_at: Optional[datetime] = None,
    raw_response_sha256: Optional[str] = None,
) -> dict[str, Any]:
    returned_start, returned_end = _returned_range(frame)
    content_payload = _frame_content_payload(frame)
    payload: dict[str, Any] = {
        "schema_version": DAILY_DATA_IDENTITY_SCHEMA_VERSION,
        "identity_state": str(identity_state or "").strip().upper(),
        "provider_identity": str(provider_identity or "").strip(),
        "provider_route": str(provider_route or "").strip(),
        "actual_response_branch": str(actual_response_branch or "").strip(),
        "requested_adjustment_basis": _basis(requested_adjustment_basis),
        "observed_adjustment_basis": _basis(observed_adjustment_basis),
        "basis_evidence": str(basis_evidence or "").strip(),
        "timeframe": "1d",
        "currency": str(currency or "").strip().upper(),
        "volume_unit": str(volume_unit or "").strip(),
        "amount_unit": str(amount_unit or "").strip(),
        "requested_start": _date_text(requested_start),
        "requested_end": _date_text(requested_end),
        "returned_start": returned_start,
        "returned_end": returned_end,
        "observed_at": _utc_text(observed_at or datetime.now(timezone.utc)),
        "raw_response_sha256": _optional_sha256(raw_response_sha256),
        "content_sha256": sha256_payload(content_payload),
        "row_count": int(len(content_payload)),
    }
    payload["identity_hash"] = _identity_hash(payload)
    return validate_daily_data_identity(payload)


def build_unclassified_daily_data_identity(
    frame: pd.DataFrame,
    *,
    provider_identity: str,
    provider_route: str,
    requested_start: Optional[str],
    requested_end: Optional[str],
    actual_response_branch: str = "UNOBSERVED",
) -> dict[str, Any]:
    return build_daily_data_identity(
        frame,
        provider_identity=provider_identity,
        provider_route=provider_route,
        actual_response_branch=actual_response_branch,
        requested_adjustment_basis=None,
        observed_adjustment_basis=None,
        basis_evidence="PROVIDER_DID_NOT_EXPOSE_OBSERVED_BASIS",
        requested_start=requested_start,
        requested_end=requested_end,
        currency="UNKNOWN",
        volume_unit="UNKNOWN",
        amount_unit="UNKNOWN",
        identity_state="UNCLASSIFIED",
    )


def validate_daily_data_identity(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DailyDataIdentityError("daily data identity must be an object")
    payload = json.loads(canonical_json(dict(value)))
    if payload.get("schema_version") != DAILY_DATA_IDENTITY_SCHEMA_VERSION:
        raise DailyDataIdentityError("daily data identity schema_version mismatch")
    state = str(payload.get("identity_state") or "").strip().upper()
    if state not in _IDENTITY_STATES:
        raise DailyDataIdentityError("daily data identity_state is invalid")
    for key in (
        "provider_identity",
        "provider_route",
        "actual_response_branch",
        "basis_evidence",
        "timeframe",
        "currency",
        "volume_unit",
        "amount_unit",
        "content_sha256",
        "identity_hash",
    ):
        if not str(payload.get(key) or "").strip():
            raise DailyDataIdentityError(f"daily data identity missing {key}")
    if payload.get("timeframe") != "1d":
        raise DailyDataIdentityError("daily data identity timeframe must be 1d")
    observed_basis = _basis(payload.get("observed_adjustment_basis"))
    requested_basis = _basis(payload.get("requested_adjustment_basis"))
    payload["observed_adjustment_basis"] = observed_basis
    payload["requested_adjustment_basis"] = requested_basis
    if state == "OBSERVED" and observed_basis not in _ADJUSTMENT_BASES:
        raise DailyDataIdentityError("OBSERVED identity requires an observed adjustment basis")
    if state == "UNCLASSIFIED" and observed_basis is not None:
        raise DailyDataIdentityError("UNCLASSIFIED identity cannot claim an observed adjustment basis")
    for key in ("content_sha256", "identity_hash"):
        if not _is_sha256(payload.get(key)):
            raise DailyDataIdentityError(f"daily data identity {key} must be exact 64-hex")
    raw_hash = payload.get("raw_response_sha256")
    if raw_hash is not None and not _is_sha256(raw_hash):
        raise DailyDataIdentityError("raw_response_sha256 must be exact 64-hex")
    try:
        row_count = int(payload.get("row_count"))
    except (TypeError, ValueError):
        raise DailyDataIdentityError("daily data identity row_count must be an integer") from None
    if row_count < 0:
        raise DailyDataIdentityError("daily data identity row_count must be non-negative")
    payload["row_count"] = row_count
    start = _date_text(payload.get("returned_start"))
    end = _date_text(payload.get("returned_end"))
    if (start is None) != (end is None):
        raise DailyDataIdentityError("returned_start and returned_end must be jointly present")
    if start and end and start > end:
        raise DailyDataIdentityError("returned date range is reversed")
    payload["returned_start"] = start
    payload["returned_end"] = end
    payload["requested_start"] = _date_text(payload.get("requested_start"))
    payload["requested_end"] = _date_text(payload.get("requested_end"))
    if payload["requested_start"] and payload["requested_end"] and payload["requested_start"] > payload["requested_end"]:
        raise DailyDataIdentityError("requested date range is reversed")
    if _identity_hash(payload) != payload.get("identity_hash"):
        raise DailyDataIdentityError("daily data identity hash mismatch")
    return payload


def rebind_daily_data_identity(identity: Any, frame: pd.DataFrame) -> dict[str, Any]:
    payload = validate_daily_data_identity(identity)
    returned_start, returned_end = _returned_range(frame)
    content_payload = _frame_content_payload(frame)
    payload.update(
        {
            "returned_start": returned_start,
            "returned_end": returned_end,
            "content_sha256": sha256_payload(content_payload),
            "row_count": int(len(content_payload)),
        }
    )
    payload["identity_hash"] = _identity_hash(payload)
    return validate_daily_data_identity(payload)


def attach_daily_data_identity(
    frame: pd.DataFrame,
    identity: Any,
    *,
    rebind: bool = False,
) -> pd.DataFrame:
    payload = rebind_daily_data_identity(identity, frame) if rebind else validate_daily_data_identity(identity)
    frame.attrs[DAILY_DATA_IDENTITY_FRAME_ATTR] = payload
    return frame


def extract_daily_data_identity(
    frame: Any,
    *,
    strict: bool = False,
) -> Optional[dict[str, Any]]:
    try:
        if isinstance(frame, pd.DataFrame):
            attr_value = frame.attrs.get(DAILY_DATA_IDENTITY_FRAME_ATTR)
            if attr_value is not None:
                return validate_daily_data_identity(attr_value)
            if {"data_identity_json", "data_identity_hash"}.issubset(frame.columns):
                records = frame[["data_identity_json", "data_identity_hash"]].to_dict("records")
                return extract_daily_data_identity_from_records(records, strict=True)
        return None
    except (DailyDataIdentityError, TypeError, ValueError, json.JSONDecodeError):
        if strict:
            raise
        return None


def extract_daily_data_identity_from_records(
    records: Sequence[Any],
    *,
    strict: bool = False,
) -> Optional[dict[str, Any]]:
    try:
        values: list[tuple[str, str]] = []
        for record in records:
            if isinstance(record, Mapping):
                raw_json = record.get("data_identity_json")
                raw_hash = record.get("data_identity_hash")
            else:
                raw_json = getattr(record, "data_identity_json", None)
                raw_hash = getattr(record, "data_identity_hash", None)
            json_text = str(raw_json or "").strip()
            hash_text = str(raw_hash or "").strip().lower()
            if not json_text or not hash_text:
                if values or json_text or hash_text:
                    raise DailyDataIdentityError("daily data identity is incomplete across rows")
                continue
            values.append((json_text, hash_text))
        if not values:
            return None
        if len(values) != len(records) or len(set(values)) != 1:
            raise DailyDataIdentityError("daily data identity is mixed across rows")
        json_text, hash_text = values[0]
        payload = validate_daily_data_identity(json.loads(json_text))
        if payload["identity_hash"] != hash_text:
            raise DailyDataIdentityError("persisted daily data identity hash mismatch")
        if canonical_identity_json(payload) != json_text:
            raise DailyDataIdentityError("persisted daily data identity JSON is not canonical")
        return payload
    except (DailyDataIdentityError, TypeError, ValueError, json.JSONDecodeError):
        if strict:
            raise
        return None


def canonical_identity_json(identity: Any) -> str:
    return canonical_json(validate_daily_data_identity(identity))


def daily_data_identity_storage_values(frame: Any) -> tuple[Optional[str], Optional[str]]:
    identity = extract_daily_data_identity(frame, strict=True)
    if identity is None:
        return None, None
    return canonical_identity_json(identity), identity["identity_hash"]


def ensure_daily_data_identity(
    frame: pd.DataFrame,
    *,
    provider_identity: str,
    provider_route: str,
    requested_start: Optional[str],
    requested_end: Optional[str],
) -> dict[str, Any]:
    identity = extract_daily_data_identity(frame, strict=True)
    if identity is None:
        identity = build_unclassified_daily_data_identity(
            frame,
            provider_identity=provider_identity,
            provider_route=provider_route,
            requested_start=requested_start,
            requested_end=requested_end,
        )
    if _provider_key(identity.get("provider_identity")) != _provider_key(provider_identity):
        raise DailyDataIdentityError("daily data identity provider does not match selected fetcher")
    identity = rebind_daily_data_identity(identity, frame)
    attach_daily_data_identity(frame, identity)
    return identity


def identity_is_durable_price_ready(identity: Any) -> bool:
    try:
        payload = validate_daily_data_identity(identity)
    except DailyDataIdentityError:
        return False
    return bool(
        payload.get("identity_state") == "OBSERVED"
        and payload.get("observed_adjustment_basis") in _ADJUSTMENT_BASES
        and payload.get("currency") not in {None, "", "UNKNOWN"}
        and payload.get("volume_unit") not in {None, "", "UNKNOWN"}
        and payload.get("amount_unit") not in {None, "", "UNKNOWN"}
    )


def _identity_hash(payload: Mapping[str, Any]) -> str:
    # Fetch time is availability metadata, not the semantic/content identity.
    value = {key: item for key, item in payload.items() if key not in {"identity_hash", "observed_at"}}
    return sha256_payload(value)


def _frame_content_payload(frame: Any) -> list[dict[str, Any]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return []
    working = frame.copy()
    if "date" in working.columns:
        working["date"] = pd.to_datetime(working["date"], errors="coerce")
        working = working.sort_values("date", kind="stable")
    records: list[dict[str, Any]] = []
    for row in working.to_dict("records"):
        records.append(
            {
                key: _date_text(row.get(key)) if key == "date" else _finite_number(row.get(key))
                for key in _CONTENT_COLUMNS
            }
        )
    return records


def _returned_range(frame: Any) -> tuple[Optional[str], Optional[str]]:
    if not isinstance(frame, pd.DataFrame) or frame.empty or "date" not in frame.columns:
        return None, None
    dates = pd.to_datetime(frame["date"], errors="coerce").dropna()
    if dates.empty:
        return None, None
    return dates.min().date().isoformat(), dates.max().date().isoformat()


def _basis(value: Any) -> Optional[str]:
    text = str(value or "").strip().lower()
    return text or None


def _provider_key(value: Any) -> str:
    return str(value or "").strip().lower()


def _date_text(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    if not text:
        return None
    parsed = pd.to_datetime(text[:10], errors="coerce")
    return None if pd.isna(parsed) else parsed.date().isoformat()


def _utc_text(value: Any) -> Optional[str]:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _finite_number(value: Any) -> Optional[float]:
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
        raise DailyDataIdentityError("raw_response_sha256 must be exact 64-hex")
    return text


def _is_sha256(value: Any) -> bool:
    text = str(value or "").strip().lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    raise TypeError(f"unsupported daily data identity value: {type(value).__name__}")
