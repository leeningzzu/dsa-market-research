"""Typed provenance for one normalized daily market-data frame.

The identity travels beside a ``pandas.DataFrame`` and may be serialized into
``StockDaily``.  Provider names and requested parameters are descriptive only;
only observed response/query evidence may prove an adjustment basis.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
from typing import Any, Optional

import pandas as pd


DAILY_DATA_IDENTITY_SCHEMA_VERSION = "daily-data-identity-v1"
DAILY_DATA_IDENTITY_FRAME_ATTR = "daily_data_identity"
DAILY_ACQUISITION_RECEIPT_SCHEMA_VERSION = "dsa-daily-acquisition-v1"
DAILY_ACQUISITION_RECEIPT_FRAME_ATTR = "dsa_daily_acquisition_receipt"
DAILY_WINDOW_EXTENSION_RECEIPT_SCHEMA_VERSION = "dsa-daily-window-extension-v1"

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
    instrument: Optional[str] = None,
    revision: Optional[str] = None,
    vintage: Optional[str] = None,
) -> dict[str, Any]:
    returned_start, returned_end = _returned_range(frame)
    content_payload = _frame_content_payload(frame)
    payload: dict[str, Any] = {
        "schema_version": DAILY_DATA_IDENTITY_SCHEMA_VERSION,
        "identity_state": str(identity_state or "").strip().upper(),
        "provider_identity": str(provider_identity or "").strip(),
        "provider_route": str(provider_route or "").strip(),
        "instrument": _instrument_text(instrument),
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
    revision_text = _optional_text(revision)
    vintage_text = _optional_text(vintage)
    if revision_text is not None:
        payload["revision"] = revision_text
    if vintage_text is not None:
        payload["vintage"] = vintage_text
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
    instrument: Optional[str] = None,
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
        instrument=instrument,
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
    if "instrument" in payload:
        payload["instrument"] = _instrument_text(payload.get("instrument"))
    for key in ("revision", "vintage"):
        if key in payload:
            semantic_value = _optional_text(payload.get(key))
            if semantic_value is None:
                raise DailyDataIdentityError(f"daily data identity {key} cannot be blank")
            payload[key] = semantic_value
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


def verified_observed_daily_frame_provider(frame: Any) -> Optional[str]:
    """Verify an OBSERVED producer against the original full daily-frame bytes.

    A bare provider label is never authority.  This shared, read-only check
    binds typed attrs / serialized per-row identities to row count, price
    content, source labels and adjustment evidence *before* callers project
    frequency-specific bars. It conveys no storage, PIT or training rights.
    """
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        return None
    names = [str(name).lower() for name in frame.columns]
    if len(set(names)) != len(names):
        return None
    persisted = [name for name in frame.columns
                 if str(name).lower() in {"data_identity_json", "data_identity_hash"}]
    source = [name for name in frame.columns if str(name).lower() == "data_source"]
    claimed = DAILY_DATA_IDENTITY_FRAME_ATTR in frame.attrs or bool(persisted)
    if not claimed:
        return None
    if persisted and (
        len(persisted) != 2
        or set(persisted) != {"data_identity_json", "data_identity_hash"}
    ):
        return None
    identity = extract_daily_data_identity(frame, strict=False)
    if not identity or identity.get("identity_state") != "OBSERVED":
        return None
    if persisted:
        serialized = extract_daily_data_identity_from_records(
            frame[["data_identity_json", "data_identity_hash"]].to_dict("records"),
            strict=False,
        )
        if not serialized or serialized.get("identity_hash") != identity.get("identity_hash"):
            return None
    try:
        rebound = rebind_daily_data_identity(identity, frame)
    except (DailyDataIdentityError, TypeError, ValueError):
        return None
    if any(
        identity.get(key) != rebound.get(key)
        for key in ("row_count", "returned_start", "returned_end", "content_sha256")
    ):
        return None
    provider = str(identity.get("provider_identity") or "").strip()
    if not provider:
        return None
    if source and (
        len(source) != 1
        or not frame[source[0]].map(lambda value: str(value).strip()).eq(provider).all()
    ):
        return None
    return provider


def bind_verified_daily_source_view(
    source_frame: Any,
    projected_frame: pd.DataFrame,
) -> pd.DataFrame:
    """Carry a verified typed provider into a derived read-only bar view.

    Existing row-level labels are never overwritten.  A frame without a valid
    OBSERVED content identity stays unbound.  This conveys no storage, PIT,
    training, redistribution, strategy, or action authority.
    """
    if not isinstance(projected_frame, pd.DataFrame):
        return projected_frame
    result = projected_frame.copy()
    if any(str(column).lower() == "data_source" for column in result.columns):
        return result
    provider = verified_observed_daily_frame_provider(source_frame)
    if provider:
        result["data_source"] = provider
    return result


def verify_observed_daily_frame_request(
    frame: Any,
    *,
    instrument: str,
    provider_identity: Optional[str],
    requested_start: Optional[Any],
    requested_end: Optional[Any],
    target_date: Any,
    observed_at_ceiling: Optional[datetime] = None,
) -> dict[str, Any]:
    """Bind one OBSERVED daily response to the exact request and content bytes.

    This is an admission check for run-local research only. It does not grant
    storage, redistribution, historical-PIT or training rights.
    """
    if not isinstance(frame, pd.DataFrame) or frame.empty or "date" not in frame.columns:
        raise DailyDataIdentityError("daily response frame is empty or undated")
    identity = extract_daily_data_identity(frame, strict=True)
    if identity is None or identity.get("identity_state") != "OBSERVED":
        raise DailyDataIdentityError("daily response requires an OBSERVED identity")
    verified_provider = verified_observed_daily_frame_provider(frame)
    if verified_provider is None:
        raise DailyDataIdentityError("daily response content identity is invalid")
    if provider_identity and _provider_key(verified_provider) != _provider_key(provider_identity):
        raise DailyDataIdentityError("daily response provider does not match selected fetcher")
    requested_instrument = _instrument_text(instrument)
    if not requested_instrument or _instrument_text(identity.get("instrument")) != requested_instrument:
        raise DailyDataIdentityError("daily response instrument does not match request")
    expected_start = _date_text(requested_start)
    expected_end = _date_text(requested_end)
    if identity.get("requested_start") != expected_start or identity.get("requested_end") != expected_end:
        raise DailyDataIdentityError("daily response request range does not match dispatch")
    if (
        identity.get("requested_adjustment_basis")
        and identity.get("requested_adjustment_basis") != identity.get("observed_adjustment_basis")
    ):
        raise DailyDataIdentityError("daily response adjustment basis does not match request")
    target = _date_text(target_date)
    if not target:
        raise DailyDataIdentityError("daily response target date is invalid")
    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.isna().any():
        raise DailyDataIdentityError("daily response contains an invalid date")
    normalized_dates = [value.date() for value in dates]
    if len(set(normalized_dates)) != len(normalized_dates):
        raise DailyDataIdentityError("daily response contains duplicate dates")
    if max(normalized_dates).isoformat() != target:
        raise DailyDataIdentityError("daily response does not end at the requested target")
    if any(value.isoformat() > target for value in normalized_dates):
        raise DailyDataIdentityError("daily response contains a future bar")
    if (
        identity.get("returned_start") != min(normalized_dates).isoformat()
        or identity.get("returned_end") != max(normalized_dates).isoformat()
        or identity.get("row_count") != len(frame)
    ):
        raise DailyDataIdentityError("daily response returned range/count is inconsistent")
    observed_at = _utc_datetime(identity.get("observed_at"))
    ceiling = observed_at_ceiling or datetime.now(timezone.utc)
    if ceiling.tzinfo is None or ceiling.utcoffset() is None:
        ceiling = ceiling.replace(tzinfo=timezone.utc)
    else:
        ceiling = ceiling.astimezone(timezone.utc)
    if observed_at is None or observed_at > ceiling + timedelta(minutes=1):
        raise DailyDataIdentityError("daily response observation time is in the future")
    return identity


def seal_daily_window_extension_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    """Seal the bounded same-provider daily-history extension genealogy."""
    payload = dict(value)
    payload.pop("receipt_hash", None)
    payload["receipt_hash"] = sha256_payload(payload)
    return validate_daily_window_extension_receipt(payload)


def validate_daily_window_extension_receipt(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DailyDataIdentityError("daily window extension receipt must be an object")
    payload = json.loads(canonical_json(dict(value)))
    if payload.get("schema_version") != DAILY_WINDOW_EXTENSION_RECEIPT_SCHEMA_VERSION:
        raise DailyDataIdentityError("daily window extension schema_version mismatch")
    for key in (
        "caller", "instrument", "market", "target_date", "method_id", "method_owner",
        "provider_identity", "provider_route", "initial_plan_hash", "final_plan_hash",
        "initial_source_identity_hash", "final_source_identity_hash", "final_status",
        "final_reason", "receipt_hash",
    ):
        if not str(payload.get(key) or "").strip():
            raise DailyDataIdentityError(f"daily window extension receipt missing {key}")
    for key in (
        "initial_plan_hash", "final_plan_hash", "initial_source_identity_hash",
        "final_source_identity_hash", "receipt_hash",
    ):
        if not _is_sha256(payload.get(key)):
            raise DailyDataIdentityError("daily window extension hashes must be exact 64-hex")
    if payload.get("method_id") != "MTF_MOMENTUM_CONTEXT.MONTHLY.MACD_STARTUP_COMPARISON":
        raise DailyDataIdentityError("daily window extension method_id mismatch")
    if payload.get("method_owner") != "StockTrendAnalyzer._calculate_macd":
        raise DailyDataIdentityError("daily window extension method_owner mismatch")
    if any(payload.get(key) is not False for key in (
        "storage_admitted", "strategy_admitted", "training_admitted",
        "independent_action_authority",
    )):
        raise DailyDataIdentityError("daily window extension cannot escalate authority")
    attempts = payload.get("attempts")
    call_count = payload.get("provider_method_call_count")
    if not isinstance(attempts, list) or len(attempts) < 2:
        raise DailyDataIdentityError("daily window extension requires at least two attempts")
    if isinstance(call_count, bool) or not isinstance(call_count, int) or call_count != len(attempts):
        raise DailyDataIdentityError("daily window extension provider call count mismatch")
    prior_months = 0
    prior_start: Optional[date] = None
    prior_completed = 0
    for index, attempt in enumerate(attempts, start=1):
        if not isinstance(attempt, Mapping) or attempt.get("attempt_number") != index:
            raise DailyDataIdentityError("daily window extension attempt sequence mismatch")
        requested_months = attempt.get("requested_monthly_bars")
        completed_months = attempt.get("completed_monthly_bars")
        returned_bars = attempt.get("returned_base_bars")
        if (
            isinstance(requested_months, bool) or not isinstance(requested_months, int)
            or requested_months <= prior_months
            or isinstance(completed_months, bool) or not isinstance(completed_months, int)
            or completed_months <= 0 or completed_months > requested_months
            or isinstance(returned_bars, bool) or not isinstance(returned_bars, int)
            or returned_bars <= 0
        ):
            raise DailyDataIdentityError("daily window extension attempt counts are invalid")
        request_start = _date_text(attempt.get("request_start"))
        request_end = _date_text(attempt.get("request_end"))
        returned_start = _date_text(attempt.get("returned_start"))
        returned_end = _date_text(attempt.get("returned_end"))
        if not all((request_start, request_end, returned_start, returned_end)):
            raise DailyDataIdentityError("daily window extension attempt dates are missing")
        current_start = date.fromisoformat(request_start)
        if prior_start is not None and current_start >= prior_start:
            raise DailyDataIdentityError("daily window extension starts must move backward")
        if completed_months < prior_completed:
            raise DailyDataIdentityError("daily window extension completed months regressed")
        if date.fromisoformat(returned_start) < current_start:
            raise DailyDataIdentityError("daily window extension returned before request start")
        if request_end != payload["target_date"] or returned_end != payload["target_date"]:
            raise DailyDataIdentityError("daily window extension target date mismatch")
        if (
            attempt.get("provider_identity") != payload["provider_identity"]
            or attempt.get("provider_route") != payload["provider_route"]
            or not _is_sha256(attempt.get("source_identity_hash"))
        ):
            raise DailyDataIdentityError("daily window extension source identity mismatch")
        if not str(attempt.get("adjustment_basis") or "").strip() or not str(
            attempt.get("currency") or ""
        ).strip():
            raise DailyDataIdentityError("daily window extension source basis is missing")
        if str(attempt.get("macd_status") or "") not in {
            "READY", "PARTIAL", "MISSING", "UNKNOWN",
        }:
            raise DailyDataIdentityError("daily window extension MACD state is invalid")
        if not str(attempt.get("macd_reason") or "").strip():
            raise DailyDataIdentityError("daily window extension MACD reason is missing")
        prior_months = requested_months
        prior_start = current_start
        prior_completed = completed_months
    first, last = attempts[0], attempts[-1]
    final_status = payload.get("final_status")
    if final_status == "READY" and last.get("macd_status") != "READY":
        raise DailyDataIdentityError("daily window extension READY is not method-bound")
    if final_status == "SOURCE_HISTORY_SHORT" and not (
        last.get("completed_monthly_bars") < last.get("requested_monthly_bars")
    ):
        raise DailyDataIdentityError("daily window extension short status is not source-bound")
    if (
        first.get("source_identity_hash") != payload["initial_source_identity_hash"]
        or last.get("source_identity_hash") != payload["final_source_identity_hash"]
        or payload.get("initial_request_start") != first.get("request_start")
        or payload.get("final_request_start") != last.get("request_start")
        or payload.get("final_requested_monthly_bars") != last.get("requested_monthly_bars")
    ):
        raise DailyDataIdentityError("daily window extension final binding mismatch")
    expected_hash = sha256_payload({key: item for key, item in payload.items() if key != "receipt_hash"})
    if payload.get("receipt_hash") != expected_hash:
        raise DailyDataIdentityError("daily window extension receipt hash mismatch")
    return payload


def seal_daily_acquisition_receipt(value: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(value)
    payload.pop("receipt_hash", None)
    payload["receipt_hash"] = sha256_payload(payload)
    return validate_daily_acquisition_receipt(payload)


def validate_daily_acquisition_receipt(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise DailyDataIdentityError("daily acquisition receipt must be an object")
    payload = json.loads(canonical_json(dict(value)))
    if payload.get("schema_version") != DAILY_ACQUISITION_RECEIPT_SCHEMA_VERSION:
        raise DailyDataIdentityError("daily acquisition receipt schema_version mismatch")
    mode = str(payload.get("acquisition_mode") or "").strip().upper()
    if mode not in {"VERIFIED_PROVIDER_CALL", "VERIFIED_CACHE_HIT"}:
        raise DailyDataIdentityError("daily acquisition mode is invalid")
    payload["acquisition_mode"] = mode
    instrument = _instrument_text(payload.get("instrument"))
    if not instrument:
        raise DailyDataIdentityError("daily acquisition instrument is missing")
    payload["instrument"] = instrument
    for key in (
        "caller", "asset_route", "market", "target_date", "provider_identity",
        "provider_route", "source_identity_hash", "request_evidence_status",
        "returned_start", "returned_end", "persistence_status", "actual_consumer",
        "receipt_hash",
    ):
        if not str(payload.get(key) or "").strip():
            raise DailyDataIdentityError(f"daily acquisition receipt missing {key}")
    if not _is_sha256(payload.get("source_identity_hash")) or not _is_sha256(payload.get("receipt_hash")):
        raise DailyDataIdentityError("daily acquisition hashes must be exact 64-hex")
    for key in ("returned_base_bars", "admitted_base_bars", "provider_method_call_count"):
        item = payload.get(key)
        if isinstance(item, bool) or not isinstance(item, int) or item < 0:
            raise DailyDataIdentityError(f"daily acquisition {key} must be a non-negative integer")
    if payload["admitted_base_bars"] > payload["returned_base_bars"]:
        raise DailyDataIdentityError("daily acquisition admitted bars exceed returned bars")
    if payload.get("provider_http_request_count") is not None:
        raise DailyDataIdentityError("daily acquisition cannot infer provider HTTP request count")
    requested_count = payload.get("requested_session_count")
    if mode == "VERIFIED_PROVIDER_CALL":
        if (
            payload.get("request_evidence_status") != "VERIFIED_PROVIDER_CALL"
            or not isinstance(payload.get("provider_method_call_count"), int)
            or isinstance(payload.get("provider_method_call_count"), bool)
            or payload.get("provider_method_call_count") < 1
            or isinstance(requested_count, bool)
            or not isinstance(requested_count, int)
            or requested_count <= 0
            or not payload.get("actual_request_start")
            or not payload.get("actual_request_end")
            or payload.get("cache_lookup_start") is not None
            or payload.get("cache_lookup_end") is not None
        ):
            raise DailyDataIdentityError("daily provider-call receipt is inconsistent")
    else:
        if (
            payload.get("request_evidence_status") != "VERIFIED_LAWFUL_CACHE_HIT"
            or payload.get("provider_method_call_count") != 0
            or requested_count is not None
            or payload.get("actual_request_start") is not None
            or payload.get("actual_request_end") is not None
            or not payload.get("cache_lookup_start")
            or not payload.get("cache_lookup_end")
        ):
            raise DailyDataIdentityError("daily cache-hit receipt is inconsistent")
    extension = payload.get("bounded_window_extension")
    provider_calls = payload.get("provider_method_call_count")
    if extension is None:
        if mode == "VERIFIED_PROVIDER_CALL" and provider_calls != 1:
            raise DailyDataIdentityError("multiple daily provider calls require extension receipt")
    else:
        if mode != "VERIFIED_PROVIDER_CALL":
            raise DailyDataIdentityError("daily cache hit cannot contain extension receipt")
        extension = validate_daily_window_extension_receipt(extension)
        payload["bounded_window_extension"] = extension
        if (
            provider_calls != extension["provider_method_call_count"]
            or payload.get("instrument") != extension["instrument"]
            or payload.get("target_date") != extension["target_date"]
            or payload.get("provider_identity") != extension["provider_identity"]
            or payload.get("provider_route") != extension["provider_route"]
            or payload.get("plan_hash") != extension["final_plan_hash"]
            or payload.get("source_identity_hash") != extension["final_source_identity_hash"]
            or payload.get("actual_request_start") != extension["final_request_start"]
        ):
            raise DailyDataIdentityError("daily acquisition extension binding mismatch")

    expected_hash = sha256_payload({key: item for key, item in payload.items() if key != "receipt_hash"})
    if payload.get("receipt_hash") != expected_hash:
        raise DailyDataIdentityError("daily acquisition receipt hash mismatch")
    return payload


def attach_daily_acquisition_receipt(frame: pd.DataFrame, receipt: Any) -> pd.DataFrame:
    frame.attrs[DAILY_ACQUISITION_RECEIPT_FRAME_ATTR] = validate_daily_acquisition_receipt(receipt)
    return frame


def extract_daily_acquisition_receipt(frame: Any, *, strict: bool = False) -> Optional[dict[str, Any]]:
    try:
        if not isinstance(frame, pd.DataFrame):
            return None
        value = frame.attrs.get(DAILY_ACQUISITION_RECEIPT_FRAME_ATTR)
        return None if value is None else validate_daily_acquisition_receipt(value)
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
    instrument: Optional[str] = None,
) -> dict[str, Any]:
    identity = extract_daily_data_identity(frame, strict=True)
    if identity is None:
        identity = build_unclassified_daily_data_identity(
            frame,
            provider_identity=provider_identity,
            provider_route=provider_route,
            requested_start=requested_start,
            requested_end=requested_end,
            instrument=instrument,
        )
    if _provider_key(identity.get("provider_identity")) != _provider_key(provider_identity):
        raise DailyDataIdentityError("daily data identity provider does not match selected fetcher")
    requested_instrument = _instrument_text(instrument)
    if requested_instrument:
        claimed_instrument = _instrument_text(identity.get("instrument"))
        if not claimed_instrument or claimed_instrument != requested_instrument:
            raise DailyDataIdentityError("daily data identity instrument does not match request")
    if identity.get("requested_start") != _date_text(requested_start):
        raise DailyDataIdentityError("daily data identity requested_start does not match request")
    if identity.get("requested_end") != _date_text(requested_end):
        raise DailyDataIdentityError("daily data identity requested_end does not match request")
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


def _instrument_text(value: Any) -> Optional[str]:
    text = str(value or "").strip().upper()
    if not text:
        return None
    if len(text) == 8 and text[:2] in {"SH", "SZ", "BJ"} and text[2:].isdigit():
        return text[2:]
    if len(text) == 9 and text[6:] in {".SH", ".SZ", ".BJ"} and text[:6].isdigit():
        return text[:6]
    return text


def _optional_text(value: Any) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def _utc_datetime(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
