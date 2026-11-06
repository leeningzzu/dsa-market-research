"""Read-only US Treasury XML -> existing DSA Global source-observation seam.

No network, persistence, scheduler, report, model, or alternative Registry.
Caller must pass raw bytes from its *current* authorized HTTP response and the
trusted observation clock; this pure adapter does not authenticate a past fetch.
The feed Atom.updated is NOT an original per-date publication/vintage or PIT.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import re
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree as ET

from data_provider.daily_data_identity import sha256_payload
from src.services.data_object_admission import (
    validate_b1_provider_preflight,
)


class TreasuryYieldSourceError(ValueError):
    """Invalid official source, timing, or operation scope: fail closed."""


DATASET_ID = "G_UST_NOMINAL"
PROVIDER_IDENTITY = "USTreasuryYieldCurveOfficialXML"
CONSUMER_SCOPE = "MARKET_GLOBAL_TRANSMISSION"
SOURCE_SCHEMA = "data-object-source-observation-v1"
_PROVIDER_PATH = "/resource-center/data-chart-center/interest-rates/pages/xml"
_ATOM = "{http://www.w3.org/2005/Atom}"
_ODATA_DATA = "{http://schemas.microsoft.com/ado/2007/08/dataservices}"
_ODATA_META = "{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}"
_FIELDS = ("NEW_DATE", "BC_3MONTH", "BC_2YEAR", "BC_10YEAR")
_UNITS = {
    "yield_3m": "PERCENT_PER_YEAR",
    "yield_2y": "PERCENT_PER_YEAR",
    "yield_10y": "PERCENT_PER_YEAR",
    "spread_10y_3m": "PERCENTAGE_POINT",
    "date": "ISO_DATE",
}


def _aware_utc(value: Any, field: str) -> datetime:
    try:
        result = (
            value if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except (TypeError, ValueError) as exc:
        raise TreasuryYieldSourceError("TREASURY_" + field + "_INVALID") from exc
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise TreasuryYieldSourceError("TREASURY_" + field + "_NAIVE")
    return result.astimezone(timezone.utc)


def _query_month(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https" or parts.netloc != "home.treasury.gov"
        or parts.path != _PROVIDER_PATH or parts.fragment
    ):
        raise TreasuryYieldSourceError("TREASURY_SOURCE_URL_NOT_OFFICIAL")
    try:
        query = parse_qs(parts.query, strict_parsing=True)
    except ValueError as exc:
        raise TreasuryYieldSourceError("TREASURY_SOURCE_QUERY_INVALID") from exc
    if (
        set(query) != {"data", "field_tdr_date_value_month"}
        or query["data"] != ["daily_treasury_yield_curve"]
        or len(query["field_tdr_date_value_month"]) != 1
    ):
        raise TreasuryYieldSourceError("TREASURY_SOURCE_QUERY_INVALID")
    month = query["field_tdr_date_value_month"][0]
    if not re.fullmatch(r"20\d{4}", month):
        raise TreasuryYieldSourceError("TREASURY_SOURCE_MONTH_INVALID")
    try:
        date(int(month[:4]), int(month[4:]), 1)
    except ValueError as exc:
        raise TreasuryYieldSourceError("TREASURY_SOURCE_MONTH_INVALID") from exc
    return month


def _yield_value(value: str, key: str) -> Decimal:
    try:
        number = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TreasuryYieldSourceError("TREASURY_VALUE_INVALID:" + key) from exc
    if not number.is_finite() or abs(number) > Decimal("100"):
        raise TreasuryYieldSourceError("TREASURY_VALUE_INVALID:" + key)
    return number


def build_treasury_yield_source_observation(
    response_xml: bytes,
    *,
    request_url: str,
    observed_at: datetime,
    b1_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Build *source-only* proof from a caller's same-run official HTTP bytes.

    Rights preflight and a strict observed-at <= as-of clock are required.
    DSA's Registry/Rights-v2 compiler alone decides Product admission later.
    This must never be used to reclassify an earlier A-share decision snapshot.
    """
    if not isinstance(b1_context, Mapping):
        raise TreasuryYieldSourceError("TREASURY_B1_OPERATION_MISSING")
    rights_by_dataset = b1_context.get("rights_by_dataset")
    if not isinstance(rights_by_dataset, Mapping):
        raise TreasuryYieldSourceError("TREASURY_B1_RIGHTS_MISSING")
    observed = _aware_utc(observed_at, "OBSERVED_AT")
    as_of = _aware_utc(b1_context.get("research_as_of"), "ASOF")
    if observed > as_of:
        raise TreasuryYieldSourceError("TREASURY_RESPONSE_AFTER_ASOF")
    grant = validate_b1_provider_preflight(
        rights_by_dataset.get(DATASET_ID),
        operation_id=b1_context.get("operation_id"),
        intended_use=b1_context.get("intended_use"),
        dataset_id=DATASET_ID,
        consumer_scope=CONSUMER_SCOPE,
        research_as_of=as_of,
    )
    if grant["provider_identity"] != PROVIDER_IDENTITY:
        raise TreasuryYieldSourceError("TREASURY_PROVIDER_GRANT_MISMATCH")
    if _aware_utc(grant["terms_observed_at"], "TERMS_OBSERVED_AT") > observed:
        raise TreasuryYieldSourceError("TREASURY_TERMS_AFTER_RESPONSE_OBSERVATION")
    month = _query_month(request_url)
    if (
        not isinstance(response_xml, bytes) or not response_xml
        or len(response_xml) > 1_000_000
        or b"<!DOCTYPE" in response_xml.upper()
        or b"<!ENTITY" in response_xml.upper()
    ):
        raise TreasuryYieldSourceError("TREASURY_RESPONSE_BYTES_INVALID")
    try:
        root = ET.fromstring(response_xml)
    except ET.ParseError as exc:
        raise TreasuryYieldSourceError("TREASURY_RESPONSE_XML_INVALID") from exc
    if root.tag != _ATOM + "feed":
        raise TreasuryYieldSourceError("TREASURY_ATOM_FEED_INVALID")
    entries = root.findall(_ATOM + "entry")
    if not (1 <= len(entries) <= 31):
        raise TreasuryYieldSourceError("TREASURY_ATOM_ENTRY_COUNT_INVALID")
    rows: dict[str, dict[str, str]] = {}
    last_feed_update = None
    for entry in entries:
        # Treasury's official Atom/OData contract places each field directly
        # under entry/content/m:properties/d:FIELD. Local-name-only recursion
        # could accept an unrelated namespace or an attacker-selected subtree.
        content = entry.findall(_ATOM + "content")
        if len(content) != 1 or content[0].get("type") != "application/xml":
            raise TreasuryYieldSourceError("TREASURY_OFFICIAL_CONTENT_MISSING")
        properties = content[0].findall(_ODATA_META + "properties")
        if len(properties) != 1:
            raise TreasuryYieldSourceError("TREASURY_OFFICIAL_ODATA_PROPERTIES_MISSING")
        fields: dict[str, str] = {}
        for field in _FIELDS:
            nodes = properties[0].findall(_ODATA_DATA + field)
            if len(nodes) != 1:
                raise TreasuryYieldSourceError("TREASURY_REQUIRED_ODATA_FIELD_INVALID:" + field)
            fields[field] = (nodes[0].text or "").strip()
        if not re.fullmatch(
            r"[0-9]{4}-[0-9]{2}-[0-9]{2}T00:00:00(?:[.]0+)?", fields["NEW_DATE"]
        ):
            raise TreasuryYieldSourceError("TREASURY_ROW_DATE_TIME_INVALID")
        try:
            row_date = date.fromisoformat(fields["NEW_DATE"][:10])
        except ValueError as exc:
            raise TreasuryYieldSourceError("TREASURY_ROW_DATE_INVALID") from exc
        iso = row_date.isoformat()
        if (
            iso[:7].replace("-", "") != month
            or row_date > observed.date()
        ):
            raise TreasuryYieldSourceError("TREASURY_ROW_OUTSIDE_KNOWN_MONTH")
        if iso in rows:
            raise TreasuryYieldSourceError("TREASURY_DUPLICATE_ROW_DATE")
        feed_updated = entry.find(_ATOM + "updated")
        if feed_updated is None or not feed_updated.text:
            raise TreasuryYieldSourceError("TREASURY_ATOM_UPDATED_MISSING")
        update_time = _aware_utc(feed_updated.text, "ATOM_UPDATED")
        if update_time > observed:
            raise TreasuryYieldSourceError("TREASURY_ATOM_UPDATED_AFTER_OBSERVED")
        if last_feed_update is None or update_time > last_feed_update:
            last_feed_update = update_time
        rates = {
            name: _yield_value(fields[name], name)
            for name in ("BC_3MONTH", "BC_2YEAR", "BC_10YEAR")
        }
        rows[iso] = {key: str(value) for key, value in rates.items()}
    completed = max(rows)
    latest = rows[completed]
    facts = {
        "latest_completed_date": completed,
        "yield_3m_pct_per_year": latest["BC_3MONTH"],
        "yield_2y_pct_per_year": latest["BC_2YEAR"],
        "yield_10y_pct_per_year": latest["BC_10YEAR"],
        "spread_10y_3m_percentage_points": str(
            Decimal(latest["BC_10YEAR"]) - Decimal(latest["BC_3MONTH"])
        ),
        "observed_month_entry_count": len(rows),
        # This is a FEED update, never a per-row first publication timestamp.
        "feed_updated_at_not_historical_publication": last_feed_update.isoformat(),
    }
    content_hash = sha256(response_xml).hexdigest()
    observed_iso = observed.isoformat()
    source_identity = sha256_payload({
        "dataset_id": DATASET_ID, "provider_identity": PROVIDER_IDENTITY,
        "provider_route": request_url, "source_content_sha256": content_hash,
        "source_observed_at": observed_iso, "completed_through": completed,
    })
    observation = {
        "schema_version": SOURCE_SCHEMA,
        "dataset_id": DATASET_ID,
        "state": "PARTIAL",
        "reason_codes": (
            "NO_PER_ROW_FIRST_RELEASE_OR_HISTORICAL_PIT_VINTAGE",
            "UPSTREAM_REVISION_AND_VINTAGE_NOT_PROVEN",
        ),
        "provider_identity": PROVIDER_IDENTITY,
        "provider_route": request_url,
        "producer_authority": (
            "data_provider.treasury_yield_curve_source."
            "build_treasury_yield_source_observation"
        ),
        "source_identity_hash": source_identity,
        "source_content_sha256": content_hash,
        "source_observed_at": observed_iso,
        "source_available_at": observed_iso,
        "completed_through": completed,
        "units": dict(_UNITS),
        "currency": "NOT_APPLICABLE",
        "adjustment_basis": "NOT_APPLICABLE",
        # The feed-wide Atom.updated and response SHA are not a provider
        # revision chain; observed_at never substitutes for upstream vintage.
        "revision": "UNKNOWN",
        "vintage": "UNKNOWN",
        "historical_pit_eligible": False,
        "independent_source_value_admitted": True,
        "strategy_upgrade_authority": False,
        "facts": facts,
    }
    observation["observation_hash"] = sha256_payload(observation)
    return observation
