"""Official SSE completed-session index closes -> existing DSA B1 seam.

Pure adapter only: no network, persistence, scheduler, report, model or second
Registry. The caller supplies same-run response bytes and an operation-matched
B1 CURRENT_OBSERVATION / NO_SEND context. SSE index close facts do not imply
full-A breadth, turnover, historical PIT, storage, learning or action authority.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qs, urlsplit

from data_provider.daily_data_identity import sha256_payload
from src.services.data_object_admission import (
    TEMPORAL_SCOPE_CURRENT_OBSERVATION,
    validate_b1_provider_preflight,
)


class SSEIndexCurrentSourceError(ValueError):
    """Invalid SSE index response, source identity, clock or B1 scope."""


PROVIDER_IDENTITY = "SSEOfficialIndexCurrent"
SOURCE_SCHEMA = "data-object-source-observation-v1"
FACT_SCHEMA = "sse-index-current-facts-v1"
DATASET_ID = "M_INDEX_CURRENT"
CONSUMER_SCOPE = "MARKET_REGIME_BRIEF"
PRODUCER_AUTHORITY = (
    "data_provider.sse_index_current_source."
    "build_sse_index_current_observation"
)
QUERY_HOST = "query.sse.com.cn"
QUERY_PATH = "/commonQuery.do"
SQL_ID = "COMMON_SSE_SJ_SZZS_ZDZSJYBBXXX"
DEFAULT_INDEX_CODES = ("000001", "000016", "000688")
_RESPONSE_ROW_FIELDS = frozenset({
    "AVG_PRICE",
    "AVG_VOL",
    "CLOSE_POINT",
    "INDEX_ABBR",
    "INDEX_CODE",
    "KIND_NUM",
    "PE_RATIO",
    "RANK",
    "TOTAL_VALUE",
    "TRADE_AMT",
    "TRADE_DATE",
    "VALUE_RATIO",
})
_OBSERVATION_FIELDS = frozenset({
    "schema_version",
    "dataset_id",
    "state",
    "reason_codes",
    "provider_identity",
    "provider_route",
    "producer_authority",
    "source_identity_hash",
    "source_content_sha256",
    "source_observed_at",
    "source_available_at",
    "completed_through",
    "units",
    "currency",
    "adjustment_basis",
    "revision",
    "vintage",
    "historical_pit_eligible",
    "independent_source_value_admitted",
    "strategy_upgrade_authority",
    "facts",
    "observation_hash",
})


def _aware_utc(value: Any, field: str) -> datetime:
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except (TypeError, ValueError) as exc:
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_" + field + "_INVALID"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_" + field + "_NAIVE"
        )
    return parsed.astimezone(timezone.utc)


def _parse_trade_date(value: Any) -> date:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{8}", text):
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_TRADE_DATE_INVALID"
        )
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError as exc:
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_TRADE_DATE_INVALID"
        ) from exc


def _normalized_positive_decimal(value: Any, field: str) -> str:
    text = str(value or "").strip()
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_" + field + "_INVALID"
        ) from exc
    if not number.is_finite() or number <= 0:
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_" + field + "_INVALID"
        )
    normalized = format(number, "f").rstrip("0").rstrip(".")
    return normalized or "0"


def _validate_request_url(url: str) -> None:
    parts = urlsplit(str(url or ""))
    if (
        parts.scheme != "https"
        or parts.netloc != QUERY_HOST
        or parts.path != QUERY_PATH
        or parts.fragment
    ):
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_SOURCE_URL_NOT_OFFICIAL"
        )
    try:
        query = parse_qs(
            parts.query,
            strict_parsing=True,
            keep_blank_values=True,
        )
    except ValueError as exc:
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_SOURCE_QUERY_INVALID"
        ) from exc
    if query != {"sqlId": [SQL_ID], "isPagination": ["false"]}:
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_SOURCE_QUERY_INVALID"
        )


def _strict_rows(payload: bytes) -> list[dict[str, Any]]:
    if (
        not isinstance(payload, bytes)
        or not payload
        or len(payload) > 2_000_000
    ):
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_RESPONSE_BYTES_INVALID"
        )

    def reject_constant(value: str) -> None:
        raise ValueError("non-finite JSON constant: " + value)

    try:
        document = json.loads(
            payload.decode("utf-8"),
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_RESPONSE_JSON_INVALID"
        ) from exc
    if not isinstance(document, Mapping):
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_RESPONSE_SCHEMA_INVALID"
        )
    if (
        document.get("sqlId") != SQL_ID
        or str(document.get("isPagination")).lower() != "false"
    ):
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_RESPONSE_SQL_ID_MISMATCH"
        )
    if document.get("actionErrors") not in (None, [], {}) or document.get(
        "fieldErrors"
    ) not in (None, [], {}):
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_RESPONSE_REPORTED_ERROR"
        )
    rows = document.get("result")
    if (
        not isinstance(rows, list)
        or not rows
        or not all(isinstance(row, Mapping) for row in rows)
    ):
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_RESULT_SCHEMA_INVALID"
        )
    result = [dict(row) for row in rows]
    if any(frozenset(row) != _RESPONSE_ROW_FIELDS for row in result):
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_RESULT_FIELD_DRIFT"
        )
    return result


def _validate_required_codes(value: Sequence[str]) -> tuple[str, ...]:
    codes = tuple(str(item or "").strip() for item in value)
    if (
        not codes
        or len(codes) != len(set(codes))
        or any(not re.fullmatch(r"\d{6}", code) for code in codes)
    ):
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_REQUIRED_CODES_INVALID"
        )
    return codes


def _validate_grant(
    b1_context: Mapping[str, Any],
    *,
    observed_at: datetime,
) -> dict[str, Any]:
    if not isinstance(b1_context, Mapping):
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_B1_OPERATION_MISSING"
        )
    if (
        str(b1_context.get("temporal_scope") or "").upper()
        != TEMPORAL_SCOPE_CURRENT_OBSERVATION
    ):
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_CURRENT_OBSERVATION_REQUIRED"
        )
    grant = validate_b1_provider_preflight(
        (b1_context.get("rights_by_dataset") or {}).get(DATASET_ID),
        operation_id=b1_context.get("operation_id"),
        intended_use=b1_context.get("intended_use"),
        dataset_id=DATASET_ID,
        consumer_scope=CONSUMER_SCOPE,
        research_as_of=b1_context.get("research_as_of"),
    )
    if grant["provider_identity"] != PROVIDER_IDENTITY:
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_PROVIDER_GRANT_MISMATCH"
        )
    if _aware_utc(
        grant["terms_observed_at"], "TERMS_OBSERVED_AT"
    ) > observed_at:
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_TERMS_AFTER_SOURCE_OBSERVATION"
        )
    return grant


def build_sse_index_current_observation(
    response_json: bytes,
    *,
    request_url: str,
    expected_trade_date: str,
    observed_at: datetime,
    b1_context: Mapping[str, Any],
    required_index_codes: Sequence[str] = DEFAULT_INDEX_CODES,
) -> dict[str, Any]:
    """Build one bounded SSE index-close source observation.

    The response admits only selected SSE index closes for one completed day.
    It intentionally excludes ``TRADE_AMT`` and all breadth/turnover claims.
    """
    _validate_request_url(request_url)
    observed = _aware_utc(observed_at, "OBSERVED_AT")
    as_of = _aware_utc(b1_context.get("research_as_of"), "ASOF")
    if observed > as_of:
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_RESPONSE_AFTER_ASOF"
        )
    _validate_grant(b1_context, observed_at=observed)

    target = date.fromisoformat(str(expected_trade_date))
    from src.core.trading_calendar import (
        resolve_latest_completed_session_fail_closed,
    )

    latest_complete = resolve_latest_completed_session_fail_closed(
        "cn",
        current_time=observed,
    )
    if target > latest_complete:
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_SESSION_NOT_COMPLETED"
        )
    codes = _validate_required_codes(required_index_codes)
    rows = _strict_rows(response_json)
    matched: dict[str, dict[str, Any]] = {}
    for row in rows:
        code = str(row.get("INDEX_CODE") or "").strip()
        if code not in codes:
            continue
        if code in matched:
            raise SSEIndexCurrentSourceError(
                "SSE_INDEX_CURRENT_DUPLICATE_INDEX_CODE"
            )
        row_date = _parse_trade_date(row.get("TRADE_DATE"))
        if row_date != target:
            raise SSEIndexCurrentSourceError(
                "SSE_INDEX_CURRENT_TRADE_DATE_MISMATCH"
            )
        abbreviation = str(row.get("INDEX_ABBR") or "").strip()
        if not abbreviation:
            raise SSEIndexCurrentSourceError(
                "SSE_INDEX_CURRENT_INDEX_ABBR_MISSING"
            )
        matched[code] = {
            "index_code": code,
            "index_abbr": abbreviation,
            "close_point": _normalized_positive_decimal(
                row.get("CLOSE_POINT"),
                "CLOSE_POINT",
            ),
        }
    missing = tuple(code for code in codes if code not in matched)
    if missing:
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_REQUIRED_INDEX_MISSING:"
            + ",".join(missing)
        )

    facts = {
        "schema_version": FACT_SCHEMA,
        "trade_date": target.isoformat(),
        "coverage_scope": "SSE_SELECTED_OFFICIAL_INDEXES",
        "required_index_codes": codes,
        "indices": tuple(matched[code] for code in codes),
        "excluded_market_components": ("SZSE", "BSE"),
        "breadth_denominator_admitted": False,
        "turnover_value_admitted": False,
    }
    observed_iso = observed.isoformat()
    content_hash = sha256(response_json).hexdigest()
    identity_hash = sha256_payload({
        "dataset_id": DATASET_ID,
        "provider_identity": PROVIDER_IDENTITY,
        "provider_route": request_url,
        "source_content_sha256": content_hash,
        "source_observed_at": observed_iso,
        "completed_through": target.isoformat(),
        "required_index_codes": codes,
    })
    result = {
        "schema_version": SOURCE_SCHEMA,
        "dataset_id": DATASET_ID,
        "state": "PARTIAL",
        "reason_codes": (
            "SSE_OFFICIAL_COMPLETED_INDEX_CLOSE_OBSERVED",
            "SZSE_AND_BSE_INDEXES_NOT_INCLUDED",
            "BREADTH_AND_TURNOVER_NOT_ADMITTED",
            "CURRENT_OBSERVATION_ONLY",
        ),
        "provider_identity": PROVIDER_IDENTITY,
        "provider_route": request_url,
        "producer_authority": PRODUCER_AUTHORITY,
        "source_identity_hash": identity_hash,
        "source_content_sha256": content_hash,
        "source_observed_at": observed_iso,
        "source_available_at": observed_iso,
        "completed_through": target.isoformat(),
        "units": {
            "close_point": "INDEX_POINTS",
            "index_count": "COUNT",
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
    result["observation_hash"] = sha256_payload(result)
    return validate_sse_index_current_observation(
        result,
        expected_trade_date=target.isoformat(),
    )


def validate_sse_index_current_observation(
    value: Mapping[str, Any],
    *,
    expected_trade_date: str | None = None,
) -> dict[str, Any]:
    """Revalidate one source observation at the Registry boundary."""
    if not isinstance(value, Mapping) or set(value) != _OBSERVATION_FIELDS:
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_OBSERVATION_SCHEMA_MISMATCH"
        )
    row = dict(value)
    if (
        row.get("schema_version") != SOURCE_SCHEMA
        or row.get("dataset_id") != DATASET_ID
        or row.get("state") != "PARTIAL"
        or row.get("provider_identity") != PROVIDER_IDENTITY
        or row.get("producer_authority") != PRODUCER_AUTHORITY
        or row.get("historical_pit_eligible") is not False
        or row.get("independent_source_value_admitted") is not True
        or row.get("strategy_upgrade_authority") is not False
        or row.get("currency") != "NOT_APPLICABLE"
        or row.get("adjustment_basis") != "NOT_APPLICABLE"
        or row.get("revision") != "UNKNOWN"
        or row.get("vintage") != "UNKNOWN"
        or row.get("units") != {
            "close_point": "INDEX_POINTS",
            "index_count": "COUNT",
        }
    ):
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_OBSERVATION_INVALID"
        )
    _validate_request_url(str(row.get("provider_route") or ""))
    for field in (
        "source_identity_hash",
        "source_content_sha256",
        "observation_hash",
    ):
        text = str(row.get(field) or "").lower()
        if len(text) != 64 or any(c not in "0123456789abcdef" for c in text):
            raise SSEIndexCurrentSourceError(
                "SSE_INDEX_CURRENT_OBSERVATION_HASH_INVALID:"
                + field
            )
    expected_hash = sha256_payload({
        key: row[key] for key in row if key != "observation_hash"
    })
    if row["observation_hash"] != expected_hash:
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_OBSERVATION_HASH_MISMATCH"
        )
    observed = _aware_utc(row.get("source_observed_at"), "OBSERVED_AT")
    available = _aware_utc(row.get("source_available_at"), "AVAILABLE_AT")
    if available != observed:
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_AVAILABLE_AT_MISMATCH"
        )
    completed = date.fromisoformat(str(row.get("completed_through") or ""))
    if expected_trade_date is not None and completed != date.fromisoformat(
        str(expected_trade_date)
    ):
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_COMPLETED_THROUGH_MISMATCH"
        )
    facts = row.get("facts")
    if not isinstance(facts, Mapping) or set(facts) != {
        "schema_version",
        "trade_date",
        "coverage_scope",
        "required_index_codes",
        "indices",
        "excluded_market_components",
        "breadth_denominator_admitted",
        "turnover_value_admitted",
    }:
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_FACTS_SCHEMA_INVALID"
        )
    codes = _validate_required_codes(facts.get("required_index_codes") or ())
    if (
        facts.get("schema_version") != FACT_SCHEMA
        or facts.get("trade_date") != completed.isoformat()
        or facts.get("coverage_scope") != "SSE_SELECTED_OFFICIAL_INDEXES"
        or tuple(facts.get("excluded_market_components") or ()) != ("SZSE", "BSE")
        or facts.get("breadth_denominator_admitted") is not False
        or facts.get("turnover_value_admitted") is not False
    ):
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_FACTS_INVALID"
        )
    indices = facts.get("indices")
    if not isinstance(indices, (list, tuple)) or len(indices) != len(codes):
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_INDEX_FACTS_INVALID"
        )
    seen: list[str] = []
    for item in indices:
        if not isinstance(item, Mapping) or set(item) != {
            "index_code",
            "index_abbr",
            "close_point",
        }:
            raise SSEIndexCurrentSourceError(
                "SSE_INDEX_CURRENT_INDEX_FACTS_INVALID"
            )
        code = str(item.get("index_code") or "")
        if code not in codes or code in seen or not str(
            item.get("index_abbr") or ""
        ).strip():
            raise SSEIndexCurrentSourceError(
                "SSE_INDEX_CURRENT_INDEX_FACTS_INVALID"
            )
        close = _normalized_positive_decimal(
            item.get("close_point"),
            "CLOSE_POINT",
        )
        if close != str(item.get("close_point")):
            raise SSEIndexCurrentSourceError(
                "SSE_INDEX_CURRENT_INDEX_FACTS_NOT_NORMALIZED"
            )
        seen.append(code)
    if tuple(seen) != codes:
        raise SSEIndexCurrentSourceError(
            "SSE_INDEX_CURRENT_INDEX_ORDER_MISMATCH"
        )
    return row
