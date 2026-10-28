"""Bounded BLS public-data adapter for three accepted US inflation atoms.

This module is a thin existing-DSA provider adapter, not a second data platform.
It performs no persistence, training, notification, strategy, or action effect.
Every request is preceded by operation-specific Rights-v2 validation, and the
returned values remain current NO_SEND Product facts rather than historical PIT.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from html import unescape
import json
import math
import re
from typing import Any, Mapping
from zoneinfo import ZoneInfo

import requests

from data_provider.daily_data_identity import sha256_payload
from src.services.data_object_admission import (
    DataObjectAdmissionError,
    validate_b1_provider_preflight,
)


BLS_ATOMIC_BUNDLE_SCHEMA_VERSION = "bls-us-inflation-atomic-bundle-v1"
BLS_ATOMIC_PROVIDER_IDENTITY = "BLSPublicDataAPI"
BLS_API_URL = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
BLS_CPI_RELEASE_URL = "https://www.bls.gov/news.release/cpi.nr0.htm"
BLS_SERIES_BY_DATASET = {
    "GLOBAL_US_ACTUAL_INFLATION": "CUSR0000SA0",
    "GLOBAL_US_CORE_INFLATION": "CUSR0000SA0L1E",
    "GLOBAL_US_SERVICE_INFLATION": "CUSR0000SASLE",
}
_BLS_CONSUMER_SCOPE = "MARKET_GLOBAL_TRANSMISSION"
_RELEASE_RE = re.compile(
    r"(?:embargoed\s+until|for\s+release)\s*"
    r"(?P<hour>\d{1,2}):(?P<minute>\d{2})\s*"
    r"(?P<ampm>a\.?m\.?|p\.?m\.?)\s*\(ET\)\s*"
    r"(?:[A-Za-z]+,\s*)?"
    r"(?P<date>[A-Za-z]+\s+\d{1,2},\s+\d{4})",
    re.IGNORECASE,
)


class BLSPublicDataError(RuntimeError):
    """Fail-closed boundary error for the bounded BLS adapter."""


def _aware(value: Any, *, field: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except ValueError as exc:
            raise BLSPublicDataError(f"BLS_{field}_INVALID") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise BLSPublicDataError(f"BLS_{field}_NAIVE")
    return result.astimezone(timezone.utc)


def _validate_grants(b1_context: Mapping[str, Any], *, research_as_of: datetime) -> None:
    if not isinstance(b1_context, Mapping):
        raise BLSPublicDataError("BLS_OPERATION_CONTEXT_MISSING")
    rights = b1_context.get("rights_by_dataset") or {}
    if not isinstance(rights, Mapping):
        raise BLSPublicDataError("BLS_OPERATION_RIGHTS_INVALID")
    for dataset_id in BLS_SERIES_BY_DATASET:
        try:
            grant = validate_b1_provider_preflight(
                rights.get(dataset_id),
                operation_id=b1_context.get("operation_id"),
                intended_use=b1_context.get("intended_use"),
                dataset_id=dataset_id,
                consumer_scope=_BLS_CONSUMER_SCOPE,
                research_as_of=research_as_of,
            )
        except DataObjectAdmissionError as exc:
            raise BLSPublicDataError(
                "BLS_OPERATION_RIGHTS_NOT_ADMITTED:" + dataset_id
            ) from exc
        if grant.get("provider_identity") != BLS_ATOMIC_PROVIDER_IDENTITY:
            raise BLSPublicDataError("BLS_PROVIDER_RIGHTS_MISMATCH:" + dataset_id)
        if any(
            grant["rights"].get(key) != "FORBIDDEN"
            for key in ("storage", "training", "redistribution")
        ):
            raise BLSPublicDataError("BLS_NO_SEND_RIGHTS_BOUNDARY_MISMATCH")


def _release_timestamp(html_bytes: bytes) -> datetime:
    text = unescape(html_bytes.decode("utf-8", errors="replace"))
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    match = _RELEASE_RE.search(text)
    if match is None:
        raise BLSPublicDataError("BLS_RELEASE_TIMESTAMP_MISSING")
    parsed = datetime.strptime(match.group("date"), "%B %d, %Y")
    hour = int(match.group("hour")) % 12
    if match.group("ampm").lower().startswith("p"):
        hour += 12
    local = parsed.replace(
        hour=hour,
        minute=int(match.group("minute")),
        tzinfo=ZoneInfo("America/New_York"),
    )
    return local.astimezone(timezone.utc)


def _series_fact(series: Mapping[str, Any], *, released_at: datetime) -> dict[str, Any]:
    series_id = str(series.get("seriesID") or "").strip()
    rows = [row for row in series.get("data") or () if isinstance(row, Mapping)]
    monthly = [
        row for row in rows
        if re.fullmatch(r"M(?:0[1-9]|1[0-2])", str(row.get("period") or ""))
    ]
    if not monthly:
        raise BLSPublicDataError("BLS_MONTHLY_SERIES_EMPTY:" + series_id)
    monthly.sort(
        key=lambda row: (int(row["year"]), int(str(row["period"])[1:])),
        reverse=True,
    )
    latest = monthly[0]
    try:
        index_value = float(latest["value"])
    except (KeyError, TypeError, ValueError) as exc:
        raise BLSPublicDataError("BLS_INDEX_VALUE_INVALID:" + series_id) from exc
    if not math.isfinite(index_value):
        raise BLSPublicDataError("BLS_INDEX_VALUE_INVALID:" + series_id)
    calculation = latest.get("calculations") or {}
    pct_changes = calculation.get("pct_changes") or {}
    yoy_raw = pct_changes.get("12")
    if yoy_raw in (None, ""):
        prior = next(
            (
                row for row in monthly
                if int(row["year"]) == int(latest["year"]) - 1
                and row.get("period") == latest.get("period")
            ),
            None,
        )
        if prior is None:
            raise BLSPublicDataError("BLS_YOY_INPUT_MISSING:" + series_id)
        try:
            prior_value = float(prior["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BLSPublicDataError("BLS_YOY_INPUT_INVALID:" + series_id) from exc
        if not math.isfinite(prior_value) or prior_value == 0:
            raise BLSPublicDataError("BLS_YOY_INPUT_INVALID:" + series_id)
        yoy = (index_value / prior_value - 1.0) * 100.0
    else:
        try:
            yoy = float(yoy_raw)
        except (TypeError, ValueError) as exc:
            raise BLSPublicDataError("BLS_YOY_VALUE_INVALID:" + series_id) from exc
    if not math.isfinite(yoy):
        raise BLSPublicDataError("BLS_YOY_VALUE_INVALID:" + series_id)
    month = int(str(latest["period"])[1:])
    return {
        "series_id": series_id,
        "reference_period": f"{int(latest['year']):04d}-{month:02d}",
        "index_value": index_value,
        "yoy_pct": yoy,
        "release_published_at": released_at.isoformat(),
        "seasonal_adjustment": "SEASONALLY_ADJUSTED",
        "value_unit": "INDEX_1982_84_100",
    }


def fetch_us_inflation_atomic_bundle(
    *,
    research_as_of: datetime | str,
    b1_context: Mapping[str, Any],
    session: requests.Session | None = None,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    """Fetch three exact BLS CPI atoms after Rights-v2 preflight."""
    ceiling = _aware(research_as_of, field="RESEARCH_ASOF")
    _validate_grants(b1_context, research_as_of=ceiling)
    client = session or requests.Session()
    start_year = ceiling.year - 1
    request_payload = {
        "seriesid": list(BLS_SERIES_BY_DATASET.values()),
        "startyear": str(start_year),
        "endyear": str(ceiling.year),
        "calculations": True,
    }
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "STOCK_DSA/1.0 internal-no-send-research",
    }
    try:
        api_response = client.post(
            BLS_API_URL,
            json=request_payload,
            headers=headers,
            timeout=float(timeout_seconds),
        )
        api_response.raise_for_status()
        release_response = client.get(
            BLS_CPI_RELEASE_URL,
            headers={"User-Agent": headers["User-Agent"]},
            timeout=float(timeout_seconds),
        )
        release_response.raise_for_status()
    except requests.RequestException as exc:
        raise BLSPublicDataError("BLS_PROVIDER_REQUEST_FAILED") from exc
    try:
        api_payload = api_response.json()
    except ValueError as exc:
        raise BLSPublicDataError("BLS_API_JSON_INVALID") from exc
    if api_payload.get("status") != "REQUEST_SUCCEEDED":
        raise BLSPublicDataError("BLS_API_REQUEST_NOT_SUCCEEDED")
    series_rows = api_payload.get("Results", {}).get("series") or []
    by_series = {
        str(row.get("seriesID") or ""): row
        for row in series_rows if isinstance(row, Mapping)
    }
    if set(BLS_SERIES_BY_DATASET.values()) - set(by_series):
        raise BLSPublicDataError("BLS_SERIES_SET_INCOMPLETE")
    released_at = _release_timestamp(release_response.content)
    if released_at > ceiling:
        raise BLSPublicDataError("BLS_RELEASE_AFTER_RESEARCH_ASOF")
    observed_at = datetime.now(timezone.utc)
    if observed_at > ceiling:
        # The actual response becomes known now.  A caller may not backdate it.
        ceiling = observed_at
    raw_identity = {
        "request_payload": request_payload,
        "api_sha256": sha256(api_response.content).hexdigest(),
        "release_sha256": sha256(release_response.content).hexdigest(),
        "observed_at": observed_at.isoformat(),
    }
    combined_content_hash = sha256_payload(raw_identity)
    observations: dict[str, dict[str, Any]] = {}
    for dataset_id, series_id in BLS_SERIES_BY_DATASET.items():
        facts = _series_fact(by_series[series_id], released_at=released_at)
        fact_hash = sha256_payload(facts)
        identity = {
            "dataset_id": dataset_id,
            "provider_identity": BLS_ATOMIC_PROVIDER_IDENTITY,
            "provider_route": "bls.publicAPI.v2.timeseries+CPI_release",
            "series_id": series_id,
            "content_sha256": combined_content_hash,
            "observed_at": observed_at.isoformat(),
            "available_at": released_at.isoformat(),
        }
        observations[dataset_id] = {
            "dataset_id": dataset_id,
            "state": "READY",
            "reason": "BLS_EXACT_SERIES_VALUE_AND_RELEASE_TIME_VERIFIED",
            "provider_identity": BLS_ATOMIC_PROVIDER_IDENTITY,
            "provider_route": identity["provider_route"],
            "source_identity_hash": sha256_payload(identity),
            "source_content_sha256": combined_content_hash,
            "source_observed_at": observed_at.isoformat(),
            "source_available_at": released_at.isoformat(),
            "completed_through": facts["reference_period"],
            "units": {
                "index_value": "INDEX_1982_84_100",
                "yoy_pct": "PERCENT",
                "release": "ISO_DATETIME",
            },
            "currency": "NOT_APPLICABLE",
            "adjustment_basis": "SEASONALLY_ADJUSTED",
            "revision": "observed-response-sha256:" + combined_content_hash,
            "vintage": released_at.isoformat(),
            "facts": facts,
            "source_fact_hash": fact_hash,
        }
    bundle = {
        "schema_version": BLS_ATOMIC_BUNDLE_SCHEMA_VERSION,
        "provider_identity": BLS_ATOMIC_PROVIDER_IDENTITY,
        "research_as_of": ceiling.isoformat(),
        "atomic_subfield_source_observations": observations,
        "storage_admitted": False,
        "training_admitted": False,
        "strategy_upgrade_authority": False,
        "independent_action_authority": False,
    }
    bundle["bundle_hash"] = sha256_payload(bundle)
    return bundle
