"""Official Federal Reserve policy release -> existing DSA source seam.

Pure adapter only: no network, persistence, scheduler, report, model or second
Registry. The caller supplies current official response bytes and a validated
B1 NO_SEND operation context. Unknown publisher correction history is allowed
only through the explicit CURRENT_OBSERVATION receipt and never becomes PIT.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
from hashlib import sha256
from html.parser import HTMLParser
import re
from typing import Any, Mapping
from urllib.parse import urlsplit

from data_provider.daily_data_identity import sha256_payload
from src.services.data_object_admission import validate_b1_provider_preflight


class FederalReservePolicySourceError(ValueError):
    """Invalid official policy document, timing, rights or cross-check."""


DATASET_ID = "G_US_POLICY"
PROVIDER_IDENTITY = "FederalReserveOfficialPolicyRelease"
CONSUMER_SCOPE = "MARKET_GLOBAL_TRANSMISSION"
SOURCE_SCHEMA = "data-object-source-observation-v1"
_STATEMENT_RE = re.compile(
    r"^/newsevents/pressreleases/monetary(20\d{6})a[.]htm$"
)
_IMPLEMENTATION_RE = re.compile(
    r"^/newsevents/pressreleases/monetary(20\d{6})a1[.]htm$"
)
_NUMBER = r"\d+(?:-\d+/\d+|[.]\d+)?"


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._ignored += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"} and self._ignored:
            self._ignored -= 1

    def handle_data(self, data: str) -> None:
        if not self._ignored and data.strip():
            self.parts.append(data.strip())


def _visible_text(payload: bytes, field: str) -> str:
    if not isinstance(payload, bytes) or not payload or len(payload) > 1_000_000:
        raise FederalReservePolicySourceError("FED_POLICY_" + field + "_BYTES_INVALID")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FederalReservePolicySourceError(
            "FED_POLICY_" + field + "_UTF8_INVALID"
        ) from exc
    parser = _TextExtractor()
    try:
        parser.feed(text)
        parser.close()
    except Exception as exc:
        raise FederalReservePolicySourceError(
            "FED_POLICY_" + field + "_HTML_INVALID"
        ) from exc
    return " ".join(" ".join(parser.parts).split())


def _aware_utc(value: Any, field: str) -> datetime:
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except (TypeError, ValueError) as exc:
        raise FederalReservePolicySourceError(
            "FED_POLICY_" + field + "_INVALID"
        ) from exc
    if not isinstance(parsed, datetime) or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FederalReservePolicySourceError("FED_POLICY_" + field + "_NAIVE")
    return parsed.astimezone(timezone.utc)


def _official_document(url: str, pattern: re.Pattern[str], field: str) -> tuple[str, str]:
    parts = urlsplit(str(url or ""))
    match = pattern.fullmatch(parts.path)
    if (
        parts.scheme != "https"
        or parts.netloc != "www.federalreserve.gov"
        or parts.query
        or parts.fragment
        or match is None
    ):
        raise FederalReservePolicySourceError(
            "FED_POLICY_" + field + "_URL_NOT_OFFICIAL"
        )
    return match.group(1), parts.path.rsplit("/", 1)[-1]


def _number(value: str) -> Fraction:
    if "-" in value:
        integer, fraction = value.split("-", 1)
        return Fraction(integer) + Fraction(fraction)
    return Fraction(value)


def _decimal_text(value: Fraction) -> str:
    decimal = Decimal(value.numerator) / Decimal(value.denominator)
    text = format(decimal.normalize(), "f")
    return text if "." in text else text + ".0"


def build_federal_reserve_policy_source_observation(
    statement_html: bytes,
    implementation_html: bytes,
    *,
    statement_url: str,
    implementation_url: str,
    observed_at: datetime,
    b1_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Compile one same-run current official FOMC release observation."""
    if not isinstance(b1_context, Mapping):
        raise FederalReservePolicySourceError("FED_POLICY_B1_OPERATION_MISSING")
    rights_by_dataset = b1_context.get("rights_by_dataset")
    if not isinstance(rights_by_dataset, Mapping):
        raise FederalReservePolicySourceError("FED_POLICY_B1_RIGHTS_MISSING")
    observed = _aware_utc(observed_at, "OBSERVED_AT")
    research_asof = _aware_utc(b1_context.get("research_as_of"), "ASOF")
    if observed > research_asof:
        raise FederalReservePolicySourceError("FED_POLICY_RESPONSE_AFTER_ASOF")
    grant = validate_b1_provider_preflight(
        rights_by_dataset.get(DATASET_ID),
        operation_id=str(b1_context.get("operation_id") or ""),
        intended_use=str(b1_context.get("intended_use") or ""),
        dataset_id=DATASET_ID,
        consumer_scope=CONSUMER_SCOPE,
        research_as_of=research_asof,
    )
    if grant["provider_identity"] != PROVIDER_IDENTITY:
        raise FederalReservePolicySourceError("FED_POLICY_PROVIDER_GRANT_MISMATCH")
    if _aware_utc(grant["terms_observed_at"], "TERMS_OBSERVED_AT") > observed:
        raise FederalReservePolicySourceError("FED_POLICY_TERMS_AFTER_SOURCE_OBSERVATION")

    statement_date, statement_id = _official_document(
        statement_url, _STATEMENT_RE, "STATEMENT"
    )
    implementation_date, implementation_id = _official_document(
        implementation_url, _IMPLEMENTATION_RE, "IMPLEMENTATION"
    )
    if statement_date != implementation_date:
        raise FederalReservePolicySourceError("FED_POLICY_DOCUMENT_DATE_MISMATCH")

    statement = _visible_text(statement_html, "STATEMENT")
    implementation = _visible_text(implementation_html, "IMPLEMENTATION")
    release_date = datetime.strptime(statement_date, "%Y%m%d")
    # Windows strftime does not support %-d.
    publisher_date = f"{release_date.strftime('%B')} {release_date.day}, {release_date.year}"
    if publisher_date not in statement or publisher_date not in implementation:
        raise FederalReservePolicySourceError("FED_POLICY_PUBLISHER_DATE_MISMATCH")

    clock_matches = re.findall(
        r"For release at (\d{1,2}):(\d{2}) p[.]m[.] (EDT|EST)", statement
    )
    if len(clock_matches) != 1:
        raise FederalReservePolicySourceError("FED_POLICY_RELEASE_CLOCK_INVALID")
    hour, minute, zone = clock_matches[0]
    published = release_date.replace(
        hour=int(hour) % 12 + 12,
        minute=int(minute),
        tzinfo=timezone(timedelta(hours=-4 if zone == "EDT" else -5)),
    ).astimezone(timezone.utc)
    if published > observed:
        raise FederalReservePolicySourceError("FED_POLICY_RELEASE_AFTER_OBSERVATION")

    decisions = re.findall(
        r"The Committee decided to (maintain|raise|lower) the target range for "
        r"the federal funds rate (?:at|to) (" + _NUMBER + r") to (" + _NUMBER + r") percent",
        statement,
    )
    if len(decisions) != 1:
        raise FederalReservePolicySourceError("FED_POLICY_TARGET_RANGE_INVALID")
    action, lower_text, upper_text = decisions[0]
    lower = _number(lower_text)
    upper = _number(upper_text)
    if not (Fraction(0) <= lower < upper <= Fraction(100)):
        raise FederalReservePolicySourceError("FED_POLICY_TARGET_RANGE_INVALID")
    expected_implementation = f"target range of {lower_text} to {upper_text} percent"
    if expected_implementation not in implementation:
        raise FederalReservePolicySourceError("FED_POLICY_IMPLEMENTATION_MISMATCH")

    statement_sha = sha256(statement_html).hexdigest()
    implementation_sha = sha256(implementation_html).hexdigest()
    combined_sha = sha256_payload({
        "statement_sha256": statement_sha,
        "implementation_sha256": implementation_sha,
    })
    observed_iso = observed.isoformat()
    published_iso = published.isoformat()
    facts = {
        "publisher": "Board of Governors of the Federal Reserve System",
        "release_date": release_date.date().isoformat(),
        "source_published_at": published_iso,
        "decision": action.upper(),
        "target_lower_pct_per_year": _decimal_text(lower),
        "target_upper_pct_per_year": _decimal_text(upper),
        "statement_document_id": statement_id,
        "implementation_document_id": implementation_id,
        "implementation_agrees": True,
        "publisher_correction_history": "NOT_USED_FOR_CURRENT_OBSERVATION",
        "historical_pit_eligible": False,
    }
    source_identity = sha256_payload({
        "dataset_id": DATASET_ID,
        "provider_identity": PROVIDER_IDENTITY,
        "statement_url": statement_url,
        "implementation_url": implementation_url,
        "source_content_sha256": combined_sha,
        "source_observed_at": observed_iso,
        "completed_through": published_iso,
    })
    observation = {
        "schema_version": SOURCE_SCHEMA,
        "dataset_id": DATASET_ID,
        "state": "READY",
        "reason_codes": (
            "OFFICIAL_POLICY_RELEASE_AND_IMPLEMENTATION_AGREE",
            "CURRENT_OBSERVATION_ONLY_NO_HISTORICAL_PIT",
        ),
        "provider_identity": PROVIDER_IDENTITY,
        "provider_route": statement_url + " | " + implementation_url,
        "producer_authority": (
            "data_provider.federal_reserve_policy_source."
            "build_federal_reserve_policy_source_observation"
        ),
        "source_identity_hash": source_identity,
        "source_content_sha256": combined_sha,
        "source_observed_at": observed_iso,
        "source_available_at": published_iso,
        "completed_through": published_iso,
        "units": {
            "target_lower": "PERCENT_PER_YEAR",
            "target_upper": "PERCENT_PER_YEAR",
            "published_at": "ISO8601_UTC",
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
