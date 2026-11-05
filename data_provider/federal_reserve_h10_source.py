"""Official Federal Reserve H.10 current release -> existing DSA source seam.

Pure adapter only: no network, persistence, scheduler, report, model, or second
Registry. The caller supplies one current official release response and an exact
B1 NO_SEND operation context. The weekly page proves the current published
observation only; it never becomes a historical PIT vintage.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html.parser import HTMLParser
import re
from typing import Any, Mapping
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from data_provider.daily_data_identity import sha256_payload
from src.services.data_object_admission import validate_b1_provider_preflight


class FederalReserveH10SourceError(ValueError):
    """Invalid official H.10 release, current-run clock, or rights binding."""


DATASET_ID = "G_USD_BROAD"
PROVIDER_IDENTITY = "FederalReserveBoardH10OfficialRelease"
CONSUMER_SCOPE = "MARKET_GLOBAL_TRANSMISSION"
SOURCE_SCHEMA = "data-object-source-observation-v1"
PRODUCER_AUTHORITY = (
    "data_provider.federal_reserve_h10_source."
    "build_federal_reserve_h10_source_observation"
)
_OFFICIAL_PATHS = {"/releases/h10/current/", "/releases/h10/current/default.htm"}
_RELEASE_RE = re.compile(
    r"\bRelease Date:\s*([A-Z][a-z]+\s+\d{1,2},\s+20\d{2})\b"
)
_DATE_CELL_RE = re.compile(r"^([A-Z][a-z]{2})[.]?\s+(\d{1,2})$")


class _H10Parser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored = 0
        self._cell_depth = 0
        self._cell_parts: list[str] = []
        self._row: list[str] = []
        self.rows: list[list[str]] = []
        self.visible_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"}:
            self._ignored += 1
        elif not self._ignored and lowered in {"td", "th"}:
            self._cell_depth += 1
            self._cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "noscript"} and self._ignored:
            self._ignored -= 1
        elif not self._ignored and lowered in {"td", "th"} and self._cell_depth:
            self._cell_depth -= 1
            self._row.append(" ".join(" ".join(self._cell_parts).split()))
            self._cell_parts = []
        elif not self._ignored and lowered == "tr" and self._row:
            self.rows.append(self._row)
            self._row = []

    def handle_data(self, data: str) -> None:
        if self._ignored or not data.strip():
            return
        self.visible_parts.append(data.strip())
        if self._cell_depth:
            self._cell_parts.append(data)


def _aware_utc(value: Any, field: str) -> datetime:
    try:
        result = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except (TypeError, ValueError) as exc:
        raise FederalReserveH10SourceError("FED_H10_" + field + "_INVALID") from exc
    if not isinstance(result, datetime) or result.tzinfo is None or result.utcoffset() is None:
        raise FederalReserveH10SourceError("FED_H10_" + field + "_NAIVE")
    return result.astimezone(timezone.utc)


def _official_url(value: str) -> str:
    parts = urlsplit(str(value or ""))
    if (
        parts.scheme != "https"
        or parts.netloc != "www.federalreserve.gov"
        or parts.path not in _OFFICIAL_PATHS
        or parts.query
        or parts.fragment
    ):
        raise FederalReserveH10SourceError("FED_H10_SOURCE_URL_NOT_OFFICIAL")
    return "https://www.federalreserve.gov" + parts.path


def _parse_date_cell(value: str, release_date: date) -> date:
    match = _DATE_CELL_RE.fullmatch(str(value or "").strip())
    if match is None:
        raise FederalReserveH10SourceError("FED_H10_DATE_HEADER_INVALID")
    month, day = match.groups()
    try:
        candidate = datetime.strptime(
            f"{month} {int(day)} {release_date.year}", "%b %d %Y"
        ).date()
    except ValueError as exc:
        raise FederalReserveH10SourceError("FED_H10_DATE_HEADER_INVALID") from exc
    if candidate > release_date:
        candidate = candidate.replace(year=release_date.year - 1)
    return candidate


def _number(value: str) -> Decimal | None:
    text = str(value or "").strip()
    if text.upper() == "ND":
        return None
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise FederalReserveH10SourceError("FED_H10_BROAD_VALUE_INVALID") from exc
    if not number.is_finite() or number <= 0 or number >= Decimal("1000"):
        raise FederalReserveH10SourceError("FED_H10_BROAD_VALUE_INVALID")
    return number


def _decimal_text(value: Decimal) -> str:
    text = format(value.normalize(), "f")
    return text if "." in text else text + ".0"


def build_federal_reserve_h10_source_observation(
    response_html: bytes,
    *,
    request_url: str,
    observed_at: datetime,
    b1_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one current broad-dollar observation from official H.10 HTML."""
    if not isinstance(response_html, bytes) or not response_html or len(response_html) > 1_000_000:
        raise FederalReserveH10SourceError("FED_H10_RESPONSE_BYTES_INVALID")
    official_url = _official_url(request_url)
    try:
        text = response_html.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FederalReserveH10SourceError("FED_H10_RESPONSE_UTF8_INVALID") from exc
    parser = _H10Parser()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        raise FederalReserveH10SourceError("FED_H10_RESPONSE_HTML_INVALID") from exc

    visible = " ".join(" ".join(parser.visible_parts).split())
    releases = _RELEASE_RE.findall(visible)
    if len(releases) != 1:
        raise FederalReserveH10SourceError("FED_H10_RELEASE_DATE_INVALID")
    try:
        release_date = datetime.strptime(releases[0], "%B %d, %Y").date()
    except ValueError as exc:
        raise FederalReserveH10SourceError("FED_H10_RELEASE_DATE_INVALID") from exc

    header_rows = [
        row for row in parser.rows
        if len(row) >= 3
        and row[0].strip().upper() == "COUNTRY"
        and row[1].strip().upper() == "CURRENCY"
    ]
    broad_rows = [
        row for row in parser.rows
        if row and row[0].strip().upper() in {"1) BROAD", "BROAD"}
    ]
    if len(header_rows) != 1 or len(broad_rows) != 1:
        raise FederalReserveH10SourceError("FED_H10_TABLE_IDENTITY_INVALID")
    header = header_rows[0]
    broad = broad_rows[0]
    if len(header) != len(broad) or broad[1].strip().upper() != "JAN06=100":
        raise FederalReserveH10SourceError("FED_H10_BROAD_SCHEMA_INVALID")

    dates = [_parse_date_cell(value, release_date) for value in header[2:]]
    if dates != sorted(dates) or len(dates) != len(set(dates)) or dates[-1] > release_date:
        raise FederalReserveH10SourceError("FED_H10_DATE_SEQUENCE_INVALID")
    values = [_number(value) for value in broad[2:]]
    observed_values = [
        (day, value) for day, value in zip(dates, values) if value is not None
    ]
    if not observed_values:
        raise FederalReserveH10SourceError("FED_H10_BROAD_VALUES_MISSING")
    completed_date, latest_value = observed_values[-1]

    if not isinstance(b1_context, Mapping):
        raise FederalReserveH10SourceError("FED_H10_B1_OPERATION_MISSING")
    rights = b1_context.get("rights_by_dataset")
    if not isinstance(rights, Mapping):
        raise FederalReserveH10SourceError("FED_H10_B1_RIGHTS_MISSING")
    observed = _aware_utc(observed_at, "OBSERVED_AT")
    as_of = _aware_utc(b1_context.get("research_as_of"), "ASOF")
    published = datetime.combine(
        release_date, time(16, 15), tzinfo=ZoneInfo("America/New_York")
    ).astimezone(timezone.utc)
    if published > observed or observed > as_of:
        raise FederalReserveH10SourceError("FED_H10_CLOCK_ORDER_INVALID")
    grant = validate_b1_provider_preflight(
        rights.get(DATASET_ID),
        operation_id=b1_context.get("operation_id"),
        intended_use=b1_context.get("intended_use"),
        dataset_id=DATASET_ID,
        consumer_scope=CONSUMER_SCOPE,
        research_as_of=as_of,
    )
    if grant["provider_identity"] != PROVIDER_IDENTITY:
        raise FederalReserveH10SourceError("FED_H10_PROVIDER_GRANT_MISMATCH")
    if _aware_utc(grant["terms_observed_at"], "TERMS_OBSERVED_AT") > observed:
        raise FederalReserveH10SourceError("FED_H10_TERMS_AFTER_OBSERVATION")

    content_hash = sha256(response_html).hexdigest()
    observed_iso = observed.isoformat()
    facts = {
        "release_date": release_date.isoformat(),
        "source_published_at": published.isoformat(),
        "latest_completed_date": completed_date.isoformat(),
        "broad_dollar_index_jan2006_100": _decimal_text(latest_value),
        "index_base": "JAN06=100",
        "series_label": "1) BROAD",
        "observed_release_value_count": len(observed_values),
        "definition": (
            "WEIGHTED_AVERAGE_USD_VALUE_AGAINST_BROAD_GROUP_OF_MAJOR_US_TRADING_PARTNERS"
        ),
    }
    identity = sha256_payload({
        "dataset_id": DATASET_ID,
        "provider_identity": PROVIDER_IDENTITY,
        "provider_route": official_url,
        "source_content_sha256": content_hash,
        "source_observed_at": observed_iso,
        "completed_through": completed_date.isoformat(),
    })
    observation = {
        "schema_version": SOURCE_SCHEMA,
        "dataset_id": DATASET_ID,
        "state": "PARTIAL",
        "reason_codes": (
            "CURRENT_OFFICIAL_H10_RELEASE_OBSERVED",
            "NO_HISTORICAL_FIRST_RELEASE_VINTAGE_OR_PIT_AUTHORITY",
        ),
        "provider_identity": PROVIDER_IDENTITY,
        "provider_route": official_url,
        "producer_authority": PRODUCER_AUTHORITY,
        "source_identity_hash": identity,
        "source_content_sha256": content_hash,
        "source_observed_at": observed_iso,
        "source_available_at": observed_iso,
        "completed_through": completed_date.isoformat(),
        "units": {
            "broad_dollar_index": "INDEX_JAN2006_100",
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
