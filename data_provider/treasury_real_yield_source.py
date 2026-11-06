"""Official Treasury real yields and derived breakeven -> DSA source seam.

Pure adapters only. They do not fetch, persist, schedule, render, train or own a
second Registry. Current observations may carry UNKNOWN publisher revision and
vintage only through DSA's explicit CURRENT_OBSERVATION NO_SEND contract.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree as ET

from data_provider.daily_data_identity import sha256_payload
from src.services.data_object_admission import validate_b1_provider_preflight


class TreasuryRealYieldSourceError(ValueError):
    """Invalid official source, parent identity, timing, rights or arithmetic."""


REAL_DATASET_ID = "G_UST_REAL"
REAL_PROVIDER_IDENTITY = "USTreasuryRealYieldCurveOfficialXML"
BREAKEVEN_DATASET_ID = "G_INFL_BREAKEVEN"
BREAKEVEN_PROVIDER_IDENTITY = "USTreasuryNominalRealBreakevenDerived"
CONSUMER_SCOPE = "MARKET_GLOBAL_TRANSMISSION"
SOURCE_SCHEMA = "data-object-source-observation-v1"
NOMINAL_PROVIDER_IDENTITY = "USTreasuryYieldCurveOfficialXML"
NOMINAL_PRODUCER_AUTHORITY = (
    "data_provider.treasury_yield_curve_source."
    "build_treasury_yield_source_observation"
)
REAL_PRODUCER_AUTHORITY = (
    "data_provider.treasury_real_yield_source."
    "build_treasury_real_yield_source_observation"
)
_PROVIDER_PATH = "/resource-center/data-chart-center/interest-rates/pages/xml"
_ATOM = "{http://www.w3.org/2005/Atom}"
_ODATA_DATA = "{http://schemas.microsoft.com/ado/2007/08/dataservices}"
_ODATA_META = "{http://schemas.microsoft.com/ado/2007/08/dataservices/metadata}"
_REAL_FIELDS = ("NEW_DATE", "TC_5YEAR", "TC_7YEAR", "TC_10YEAR", "TC_20YEAR", "TC_30YEAR")
_REAL_UNITS = {
    "real_yield_5y": "PERCENT_PER_YEAR",
    "real_yield_7y": "PERCENT_PER_YEAR",
    "real_yield_10y": "PERCENT_PER_YEAR",
    "real_yield_20y": "PERCENT_PER_YEAR",
    "real_yield_30y": "PERCENT_PER_YEAR",
    "date": "ISO_DATE",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _aware_utc(value: Any, field: str) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(
            _text(value).replace("Z", "+00:00")
        )
    except (TypeError, ValueError) as exc:
        raise TreasuryRealYieldSourceError("TREASURY_REAL_" + field + "_INVALID") from exc
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TreasuryRealYieldSourceError("TREASURY_REAL_" + field + "_NAIVE")
    return parsed.astimezone(timezone.utc)


def _query_month(url: str) -> str:
    parts = urlsplit(_text(url))
    if (
        parts.scheme != "https"
        or parts.netloc != "home.treasury.gov"
        or parts.path != _PROVIDER_PATH
        or parts.fragment
    ):
        raise TreasuryRealYieldSourceError("TREASURY_REAL_SOURCE_URL_NOT_OFFICIAL")
    try:
        query = parse_qs(parts.query, strict_parsing=True)
    except ValueError as exc:
        raise TreasuryRealYieldSourceError("TREASURY_REAL_SOURCE_QUERY_INVALID") from exc
    if (
        set(query) != {"data", "field_tdr_date_value_month"}
        or query["data"] != ["daily_treasury_real_yield_curve"]
        or len(query["field_tdr_date_value_month"]) != 1
    ):
        raise TreasuryRealYieldSourceError("TREASURY_REAL_SOURCE_QUERY_INVALID")
    month = query["field_tdr_date_value_month"][0]
    if len(month) != 6 or not month.isdigit():
        raise TreasuryRealYieldSourceError("TREASURY_REAL_SOURCE_MONTH_INVALID")
    try:
        date(int(month[:4]), int(month[4:]), 1)
    except ValueError as exc:
        raise TreasuryRealYieldSourceError("TREASURY_REAL_SOURCE_MONTH_INVALID") from exc
    return month


def _number(value: Any, field: str) -> Decimal:
    try:
        number = Decimal(_text(value))
    except (InvalidOperation, ValueError) as exc:
        raise TreasuryRealYieldSourceError("TREASURY_REAL_VALUE_INVALID:" + field) from exc
    if not number.is_finite() or abs(number) > Decimal("100"):
        raise TreasuryRealYieldSourceError("TREASURY_REAL_VALUE_INVALID:" + field)
    return number


def _sha(value: Any) -> bool:
    text = _text(value).lower()
    return len(text) == 64 and all(char in "0123456789abcdef" for char in text)


def build_treasury_real_yield_source_observation(
    response_xml: bytes,
    *,
    request_url: str,
    observed_at: datetime,
    b1_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile current official 5/7/10/20/30-year real-yield observations."""
    if not isinstance(b1_context, Mapping):
        raise TreasuryRealYieldSourceError("TREASURY_REAL_B1_OPERATION_MISSING")
    rights_by_dataset = b1_context.get("rights_by_dataset")
    if not isinstance(rights_by_dataset, Mapping):
        raise TreasuryRealYieldSourceError("TREASURY_REAL_B1_RIGHTS_MISSING")
    observed = _aware_utc(observed_at, "OBSERVED_AT")
    research_asof = _aware_utc(b1_context.get("research_as_of"), "ASOF")
    if observed > research_asof:
        raise TreasuryRealYieldSourceError("TREASURY_REAL_RESPONSE_AFTER_ASOF")
    grant = validate_b1_provider_preflight(
        rights_by_dataset.get(REAL_DATASET_ID),
        operation_id=_text(b1_context.get("operation_id")),
        intended_use=_text(b1_context.get("intended_use")),
        dataset_id=REAL_DATASET_ID,
        consumer_scope=CONSUMER_SCOPE,
        research_as_of=research_asof,
    )
    if grant["provider_identity"] != REAL_PROVIDER_IDENTITY:
        raise TreasuryRealYieldSourceError("TREASURY_REAL_PROVIDER_GRANT_MISMATCH")
    if _aware_utc(grant["terms_observed_at"], "TERMS_OBSERVED_AT") > observed:
        raise TreasuryRealYieldSourceError("TREASURY_REAL_TERMS_AFTER_RESPONSE_OBSERVATION")

    month = _query_month(request_url)
    if (
        not isinstance(response_xml, bytes)
        or not response_xml
        or len(response_xml) > 1_000_000
        or b"<!DOCTYPE" in response_xml.upper()
        or b"<!ENTITY" in response_xml.upper()
    ):
        raise TreasuryRealYieldSourceError("TREASURY_REAL_RESPONSE_BYTES_INVALID")
    try:
        root = ET.fromstring(response_xml)
    except ET.ParseError as exc:
        raise TreasuryRealYieldSourceError("TREASURY_REAL_RESPONSE_XML_INVALID") from exc
    if root.tag != _ATOM + "feed":
        raise TreasuryRealYieldSourceError("TREASURY_REAL_ATOM_FEED_INVALID")
    entries = root.findall(_ATOM + "entry")
    if not (1 <= len(entries) <= 31):
        raise TreasuryRealYieldSourceError("TREASURY_REAL_ATOM_ENTRY_COUNT_INVALID")

    rows: dict[str, dict[str, str]] = {}
    latest_feed_update: datetime | None = None
    for entry in entries:
        content = entry.findall(_ATOM + "content")
        if len(content) != 1 or content[0].get("type") != "application/xml":
            raise TreasuryRealYieldSourceError("TREASURY_REAL_OFFICIAL_CONTENT_MISSING")
        properties = content[0].findall(_ODATA_META + "properties")
        if len(properties) != 1:
            raise TreasuryRealYieldSourceError("TREASURY_REAL_ODATA_PROPERTIES_MISSING")
        fields: dict[str, str] = {}
        for field in _REAL_FIELDS:
            nodes = properties[0].findall(_ODATA_DATA + field)
            if len(nodes) != 1:
                raise TreasuryRealYieldSourceError(
                    "TREASURY_REAL_REQUIRED_FIELD_INVALID:" + field
                )
            fields[field] = _text(nodes[0].text)
        raw_date = fields["NEW_DATE"]
        if len(raw_date) < 19 or raw_date[10:19] != "T00:00:00":
            raise TreasuryRealYieldSourceError("TREASURY_REAL_ROW_DATE_TIME_INVALID")
        try:
            row_date = date.fromisoformat(raw_date[:10])
        except ValueError as exc:
            raise TreasuryRealYieldSourceError("TREASURY_REAL_ROW_DATE_INVALID") from exc
        iso = row_date.isoformat()
        if iso[:7].replace("-", "") != month or row_date > observed.date():
            raise TreasuryRealYieldSourceError("TREASURY_REAL_ROW_OUTSIDE_KNOWN_MONTH")
        if iso in rows:
            raise TreasuryRealYieldSourceError("TREASURY_REAL_DUPLICATE_ROW_DATE")
        update_node = entry.find(_ATOM + "updated")
        if update_node is None or not _text(update_node.text):
            raise TreasuryRealYieldSourceError("TREASURY_REAL_ATOM_UPDATED_MISSING")
        updated = _aware_utc(update_node.text, "ATOM_UPDATED")
        if updated > observed:
            raise TreasuryRealYieldSourceError("TREASURY_REAL_ATOM_UPDATED_AFTER_OBSERVED")
        latest_feed_update = max(latest_feed_update, updated) if latest_feed_update else updated
        rows[iso] = {
            field: str(_number(fields[field], field))
            for field in _REAL_FIELDS[1:]
        }

    completed = max(rows)
    latest = rows[completed]
    facts = {
        "latest_completed_date": completed,
        "real_yield_5y_pct_per_year": latest["TC_5YEAR"],
        "real_yield_7y_pct_per_year": latest["TC_7YEAR"],
        "real_yield_10y_pct_per_year": latest["TC_10YEAR"],
        "real_yield_20y_pct_per_year": latest["TC_20YEAR"],
        "real_yield_30y_pct_per_year": latest["TC_30YEAR"],
        "observed_month_entry_count": len(rows),
        "feed_updated_at_not_historical_publication": latest_feed_update.isoformat(),
    }
    content_hash = sha256(response_xml).hexdigest()
    observed_iso = observed.isoformat()
    source_identity = sha256_payload({
        "dataset_id": REAL_DATASET_ID,
        "provider_identity": REAL_PROVIDER_IDENTITY,
        "provider_route": request_url,
        "source_content_sha256": content_hash,
        "source_observed_at": observed_iso,
        "completed_through": completed,
    })
    observation = {
        "schema_version": SOURCE_SCHEMA,
        "dataset_id": REAL_DATASET_ID,
        "state": "PARTIAL",
        "reason_codes": (
            "NO_PER_ROW_FIRST_RELEASE_OR_HISTORICAL_PIT_VINTAGE",
            "CURRENT_OBSERVATION_REQUIRES_EXPLICIT_OPERATION_SCOPE",
        ),
        "provider_identity": REAL_PROVIDER_IDENTITY,
        "provider_route": request_url,
        "producer_authority": (
            "data_provider.treasury_real_yield_source."
            "build_treasury_real_yield_source_observation"
        ),
        "source_identity_hash": source_identity,
        "source_content_sha256": content_hash,
        "source_observed_at": observed_iso,
        "source_available_at": observed_iso,
        "completed_through": completed,
        "units": dict(_REAL_UNITS),
        "currency": "NOT_APPLICABLE",
        "adjustment_basis": "NOT_APPLICABLE",
        "revision": "UNKNOWN",
        "vintage": "UNKNOWN",
        "historical_pit_eligible": False,
        "independent_source_value_admitted": True,
        "strategy_upgrade_authority": False,
        "facts": facts,
    }
    observation["observation_hash"] = sha256_payload(observation)
    return observation


def build_treasury_breakeven_source_observation(
    nominal_source: Mapping[str, Any],
    real_source: Mapping[str, Any],
    *,
    b1_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Derive same-date 10Y nominal-minus-real breakeven, with both parents frozen."""
    if not isinstance(nominal_source, Mapping) or not isinstance(real_source, Mapping):
        raise TreasuryRealYieldSourceError("TREASURY_BREAKEVEN_PARENT_INVALID")
    if nominal_source.get("dataset_id") != "G_UST_NOMINAL" or real_source.get("dataset_id") != REAL_DATASET_ID:
        raise TreasuryRealYieldSourceError("TREASURY_BREAKEVEN_PARENT_DATASET_MISMATCH")
    parent_contracts = (
        (
            nominal_source,
            NOMINAL_PROVIDER_IDENTITY,
            NOMINAL_PRODUCER_AUTHORITY,
            "yield_10y",
        ),
        (
            real_source,
            REAL_PROVIDER_IDENTITY,
            REAL_PRODUCER_AUTHORITY,
            "real_yield_10y",
        ),
    )
    for parent, provider_identity, producer_authority, unit_key in parent_contracts:
        units = parent.get("units")
        if (
            parent.get("schema_version") != SOURCE_SCHEMA
            or parent.get("provider_identity") != provider_identity
            or parent.get("producer_authority") != producer_authority
            or not isinstance(units, Mapping)
            or units.get(unit_key) != "PERCENT_PER_YEAR"
            or parent.get("currency") != "NOT_APPLICABLE"
            or parent.get("adjustment_basis") != "NOT_APPLICABLE"
        ):
            raise TreasuryRealYieldSourceError(
                "TREASURY_BREAKEVEN_PARENT_CONTRACT_MISMATCH"
            )
    for parent in (nominal_source, real_source):
        if (
            parent.get("historical_pit_eligible") is not False
            or parent.get("independent_source_value_admitted") is not True
            or parent.get("strategy_upgrade_authority") is not False
            or not _sha(parent.get("source_identity_hash"))
            or not _sha(parent.get("source_content_sha256"))
            or not _sha(parent.get("observation_hash"))
        ):
            raise TreasuryRealYieldSourceError("TREASURY_BREAKEVEN_PARENT_IDENTITY_INVALID")
        expected = sha256_payload({key: parent[key] for key in parent if key != "observation_hash"})
        if parent["observation_hash"] != expected:
            raise TreasuryRealYieldSourceError("TREASURY_BREAKEVEN_PARENT_HASH_MISMATCH")
    completed = _text(nominal_source.get("completed_through"))
    if not completed or completed != _text(real_source.get("completed_through")):
        raise TreasuryRealYieldSourceError("TREASURY_BREAKEVEN_DATE_MISMATCH")
    nominal_facts = nominal_source.get("facts")
    real_facts = real_source.get("facts")
    if not isinstance(nominal_facts, Mapping) or not isinstance(real_facts, Mapping):
        raise TreasuryRealYieldSourceError("TREASURY_BREAKEVEN_PARENT_FACTS_MISSING")
    if (
        _text(nominal_facts.get("latest_completed_date")) != completed
        or _text(real_facts.get("latest_completed_date")) != completed
    ):
        raise TreasuryRealYieldSourceError("TREASURY_BREAKEVEN_FACT_DATE_MISMATCH")
    nominal = _number(nominal_facts.get("yield_10y_pct_per_year"), "NOMINAL_10Y")
    real = _number(real_facts.get("real_yield_10y_pct_per_year"), "REAL_10Y")
    breakeven = nominal - real
    if not breakeven.is_finite() or abs(breakeven) > Decimal("100"):
        raise TreasuryRealYieldSourceError("TREASURY_BREAKEVEN_VALUE_INVALID")

    rights_by_dataset = b1_context.get("rights_by_dataset") if isinstance(b1_context, Mapping) else None
    if not isinstance(rights_by_dataset, Mapping):
        raise TreasuryRealYieldSourceError("TREASURY_BREAKEVEN_B1_RIGHTS_MISSING")
    research_asof = _aware_utc(b1_context.get("research_as_of"), "BREAKEVEN_ASOF")
    grant = validate_b1_provider_preflight(
        rights_by_dataset.get(BREAKEVEN_DATASET_ID),
        operation_id=_text(b1_context.get("operation_id")),
        intended_use=_text(b1_context.get("intended_use")),
        dataset_id=BREAKEVEN_DATASET_ID,
        consumer_scope=CONSUMER_SCOPE,
        research_as_of=research_asof,
    )
    if grant["provider_identity"] != BREAKEVEN_PROVIDER_IDENTITY:
        raise TreasuryRealYieldSourceError("TREASURY_BREAKEVEN_PROVIDER_GRANT_MISMATCH")
    observed = max(
        _aware_utc(nominal_source.get("source_observed_at"), "NOMINAL_OBSERVED_AT"),
        _aware_utc(real_source.get("source_observed_at"), "REAL_OBSERVED_AT"),
    )
    available = max(
        _aware_utc(nominal_source.get("source_available_at"), "NOMINAL_AVAILABLE_AT"),
        _aware_utc(real_source.get("source_available_at"), "REAL_AVAILABLE_AT"),
    )
    if observed > research_asof or available > research_asof:
        raise TreasuryRealYieldSourceError("TREASURY_BREAKEVEN_PARENT_AFTER_ASOF")
    if _aware_utc(grant["terms_observed_at"], "BREAKEVEN_TERMS_OBSERVED_AT") > observed:
        raise TreasuryRealYieldSourceError("TREASURY_BREAKEVEN_TERMS_AFTER_OBSERVATION")

    facts = {
        "latest_completed_date": completed,
        "nominal_yield_10y_pct_per_year": str(nominal),
        "real_yield_10y_pct_per_year": str(real),
        "breakeven_10y_percentage_points": str(breakeven),
        "formula": "NOMINAL_10Y_MINUS_REAL_10Y",
        "nominal_parent_observation_hash": nominal_source["observation_hash"],
        "real_parent_observation_hash": real_source["observation_hash"],
        "historical_pit_eligible": False,
    }
    parent_identity = {
        "nominal_source_identity_hash": nominal_source["source_identity_hash"],
        "nominal_source_content_sha256": nominal_source["source_content_sha256"],
        "real_source_identity_hash": real_source["source_identity_hash"],
        "real_source_content_sha256": real_source["source_content_sha256"],
    }
    source_identity = sha256_payload({
        "dataset_id": BREAKEVEN_DATASET_ID,
        "provider_identity": BREAKEVEN_PROVIDER_IDENTITY,
        "completed_through": completed,
        **parent_identity,
    })
    content_hash = sha256_payload({"facts": facts, **parent_identity})
    observation = {
        "schema_version": SOURCE_SCHEMA,
        "dataset_id": BREAKEVEN_DATASET_ID,
        "state": "READY",
        "reason_codes": (
            "SAME_DATE_NOMINAL_REAL_10Y_PARENTS_VERIFIED",
            "CURRENT_OBSERVATION_ONLY_NO_HISTORICAL_PIT",
        ),
        "provider_identity": BREAKEVEN_PROVIDER_IDENTITY,
        "provider_route": "DSA:USTreasuryNominal10Y-USTreasuryReal10Y",
        "producer_authority": (
            "data_provider.treasury_real_yield_source."
            "build_treasury_breakeven_source_observation"
        ),
        "source_identity_hash": source_identity,
        "source_content_sha256": content_hash,
        "source_observed_at": observed.isoformat(),
        "source_available_at": available.isoformat(),
        "completed_through": completed,
        "units": {
            "nominal_10y": "PERCENT_PER_YEAR",
            "real_10y": "PERCENT_PER_YEAR",
            "breakeven_10y": "PERCENTAGE_POINT",
            "date": "ISO_DATE",
        },
        "currency": "NOT_APPLICABLE",
        "adjustment_basis": "NOT_APPLICABLE",
        "revision": "UNKNOWN",
        "vintage": "UNKNOWN",
        "historical_pit_eligible": False,
        "independent_source_value_admitted": True,
        "strategy_upgrade_authority": False,
        "facts": facts,
    }
    observation["observation_hash"] = sha256_payload(observation)
    return observation
