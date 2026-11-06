"""Official SSE + SZSE completed-session turnover -> existing DSA B1 seam.

Pure adapter only: no network, persistence, scheduler, report, model or second
Registry. The caller supplies same-run official response bytes plus one
operation-matched composite Rights-v2 bundle. The admitted value is explicitly
the two-exchange published stock turnover; it is not exact all-A turnover,
full-A/BSE breadth, turnover rate, relative volume, PIT, learning or action
authority.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from data_provider.daily_data_identity import sha256_payload
from src.services.data_object_admission import (
    RIGHTS_EVIDENCE_BUNDLE_SCHEMA_VERSION,
    TEMPORAL_SCOPE_CURRENT_OBSERVATION,
    validate_b1_provider_preflight,
)


class CNExchangeTurnoverSourceError(ValueError):
    """Invalid official exchange response, source identity, clock or B1 scope."""


PROVIDER_IDENTITY = "SSESZSEOfficialDailyTurnover"
SOURCE_SCHEMA = "data-object-source-observation-v1"
FACT_SCHEMA = "cn-exchange-turnover-facts-v1"
DATASET_ID = "M_TURNOVER"
CONSUMER_SCOPE = "MARKET_REGIME_BRIEF"
PRODUCER_AUTHORITY = (
    "data_provider.cn_exchange_turnover_source."
    "build_cn_exchange_turnover_observation"
)
SSE_QUERY_HOST = "query.sse.com.cn"
SSE_QUERY_PATH = "/commonQuery.do"
SSE_SQL_ID = "COMMON_SSE_SJ_GPSJ_CJGK_MRGK_C"
SSE_PRODUCT_CODES = ("01", "02", "03", "11", "17")
SZSE_QUERY_HOST = "www.szse.cn"
SZSE_QUERY_PATH = "/api/report/ShowReport/data"
SZSE_CATALOG_ID = "scsj_gprdgk_after"
RIGHTS_COMPONENTS = (
    (
        "SSEOfficialDailyTurnoverTerms",
        "https://www.sse.com.cn/home/legal/",
    ),
    (
        "SZSEOfficialDailyTurnoverTerms",
        "https://www.szse.cn/application/laws/",
    ),
)
_SSE_ROW_FIELDS = frozenset({
    "TOTAL_VALUE",
    "TRADE_VOL",
    "AVG_PE_RATE",
    "TOTAL_TO_RATE",
    "TRADE_AMT",
    "TRADE_NUM",
    "NEGO_VALUE",
    "NEGO_TO_RATE",
    "TRADE_DATE",
    "LIST_NUM",
    "PRODUCT_CODE",
})
_SZSE_DATA_FIELDS = frozenset({"zbmc", "xz", "cy", "gp"})
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
_FACT_FIELDS = frozenset({
    "schema_version",
    "trade_date",
    "coverage_scope",
    "sse_stock_total_turnover_100m_cny",
    "sse_a_share_turnover_100m_cny",
    "sse_b_share_turnover_100m_cny",
    "sse_cdr_turnover_100m_cny",
    "sse_listed_stock_count",
    "szse_main_board_turnover_100m_cny",
    "szse_chinext_turnover_100m_cny",
    "szse_total_turnover_100m_cny",
    "szse_scope_includes_depositary_receipts",
    "combined_two_exchange_stock_turnover_100m_cny",
    "combined_two_exchange_stock_turnover_cny",
    "two_exchange_turnover_value_admitted",
    "bse_included",
    "exact_all_a_turnover_admitted",
    "all_a_bse_denominator_admitted",
    "breadth_denominator_admitted",
    "turnover_rate_admitted",
    "relative_volume_admitted",
})


def _aware_utc(value: Any, field: str) -> datetime:
    try:
        parsed = (
            value
            if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except (TypeError, ValueError) as exc:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_" + field + "_INVALID"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_" + field + "_NAIVE"
        )
    return parsed.astimezone(timezone.utc)


def _strict_json(payload: bytes, *, label: str, limit: int) -> Any:
    if not isinstance(payload, bytes) or not payload or len(payload) > limit:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_" + label + "_BYTES_INVALID"
        )

    def reject_constant(value: str) -> None:
        raise ValueError("non-finite JSON constant: " + value)

    try:
        return json.loads(
            payload.decode("utf-8"),
            parse_constant=reject_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_" + label + "_JSON_INVALID"
        ) from exc


def _normalized_decimal(
    value: Any,
    field: str,
    *,
    allow_zero: bool = True,
) -> Decimal:
    text = str(value or "").strip().replace(",", "")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_" + field + "_INVALID"
        ) from exc
    if not number.is_finite() or number < 0 or (not allow_zero and number == 0):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_" + field + "_INVALID"
        )
    return number


def _decimal_text(value: Decimal) -> str:
    normalized = format(value, "f").rstrip("0").rstrip(".")
    return normalized or "0"


def _strict_nonnegative_int(value: Any, field: str) -> int:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d+", text):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_" + field + "_INVALID"
        )
    return int(text)


def _parse_compact_date(value: Any, field: str) -> date:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{8}", text):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_" + field + "_INVALID"
        )
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError as exc:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_" + field + "_INVALID"
        ) from exc


def _validate_sse_request_url(url: str, target: date) -> None:
    parts = urlsplit(str(url or ""))
    if (
        parts.scheme != "https"
        or parts.netloc != SSE_QUERY_HOST
        or parts.path != SSE_QUERY_PATH
        or parts.fragment
    ):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SSE_SOURCE_URL_NOT_OFFICIAL"
        )
    try:
        query = parse_qs(
            parts.query,
            strict_parsing=True,
            keep_blank_values=True,
        )
    except ValueError as exc:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SSE_QUERY_INVALID"
        ) from exc
    expected = {
        "sqlId": [SSE_SQL_ID],
        "PRODUCT_CODE": [",".join(SSE_PRODUCT_CODES)],
        "type": ["inParams"],
        "SEARCH_DATE": [target.isoformat()],
    }
    if query != expected:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SSE_QUERY_INVALID"
        )


def _validate_szse_request_url(url: str) -> None:
    parts = urlsplit(str(url or ""))
    if (
        parts.scheme != "https"
        or parts.netloc != SZSE_QUERY_HOST
        or parts.path != SZSE_QUERY_PATH
        or parts.fragment
    ):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SZSE_SOURCE_URL_NOT_OFFICIAL"
        )
    try:
        query = parse_qs(
            parts.query,
            strict_parsing=True,
            keep_blank_values=True,
        )
    except ValueError as exc:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SZSE_QUERY_INVALID"
        ) from exc
    if query != {
        "SHOWTYPE": ["JSON"],
        "CATALOGID": [SZSE_CATALOG_ID],
        "loading": ["first"],
    }:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SZSE_QUERY_INVALID"
        )


def _parse_sse_turnover(
    payload: bytes,
    *,
    target: date,
) -> dict[str, Any]:
    document = _strict_json(payload, label="SSE_RESPONSE", limit=2_000_000)
    if not isinstance(document, Mapping):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SSE_RESPONSE_SCHEMA_INVALID"
        )
    if (
        document.get("sqlId") != SSE_SQL_ID
        or str(document.get("isPagination")).lower() != "false"
        or document.get("type") != "inParams"
        or document.get("actionErrors") not in (None, [], {})
        or document.get("fieldErrors") not in (None, [], {})
    ):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SSE_RESPONSE_IDENTITY_INVALID"
        )
    rows = document.get("result")
    if (
        not isinstance(rows, list)
        or len(rows) != len(SSE_PRODUCT_CODES)
        or not all(isinstance(row, Mapping) for row in rows)
    ):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SSE_RESULT_SCHEMA_INVALID"
        )
    parsed: dict[str, dict[str, Any]] = {}
    for raw in rows:
        row = dict(raw)
        if frozenset(row) != _SSE_ROW_FIELDS:
            raise CNExchangeTurnoverSourceError(
                "CN_EXCHANGE_TURNOVER_SSE_RESULT_FIELD_DRIFT"
            )
        code = str(row.get("PRODUCT_CODE") or "").strip()
        if code not in SSE_PRODUCT_CODES or code in parsed:
            raise CNExchangeTurnoverSourceError(
                "CN_EXCHANGE_TURNOVER_SSE_PRODUCT_CODE_INVALID"
            )
        if _parse_compact_date(row.get("TRADE_DATE"), "SSE_TRADE_DATE") != target:
            raise CNExchangeTurnoverSourceError(
                "CN_EXCHANGE_TURNOVER_SSE_TRADE_DATE_MISMATCH"
            )
        parsed[code] = {
            "trade_amount_100m_cny": _normalized_decimal(
                row.get("TRADE_AMT"),
                "SSE_TRADE_AMT",
            ),
            "trade_volume_100m_shares": _normalized_decimal(
                row.get("TRADE_VOL"),
                "SSE_TRADE_VOL",
            ),
            "listed_count": _strict_nonnegative_int(
                row.get("LIST_NUM"),
                "SSE_LIST_NUM",
            ),
        }
    if tuple(sorted(parsed)) != tuple(sorted(SSE_PRODUCT_CODES)):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SSE_REQUIRED_CODE_MISSING"
        )
    components = ("01", "02", "03")
    if parsed["17"]["listed_count"] != sum(
        parsed[code]["listed_count"] for code in components
    ):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SSE_TOTAL_COUNT_DRIFT"
        )
    for field in ("trade_amount_100m_cny", "trade_volume_100m_shares"):
        if parsed["17"][field] != sum(
            (parsed[code][field] for code in components),
            Decimal("0"),
        ):
            raise CNExchangeTurnoverSourceError(
                "CN_EXCHANGE_TURNOVER_SSE_TOTAL_VALUE_DRIFT"
            )
    return parsed


def _condition_defaults(metadata: Mapping[str, Any]) -> dict[str, str]:
    conditions = metadata.get("conditions")
    if not isinstance(conditions, list) or len(conditions) != 5:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SZSE_CONDITIONS_INVALID"
        )
    result: dict[str, str] = {}
    for item in conditions:
        if not isinstance(item, Mapping):
            raise CNExchangeTurnoverSourceError(
                "CN_EXCHANGE_TURNOVER_SZSE_CONDITIONS_INVALID"
            )
        name = str(item.get("name") or "").strip()
        if not name or name in result:
            raise CNExchangeTurnoverSourceError(
                "CN_EXCHANGE_TURNOVER_SZSE_CONDITIONS_INVALID"
            )
        result[name] = str(item.get("defaultValue") or "").strip()
    return result


def _parse_szse_turnover(
    payload: bytes,
    *,
    target: date,
) -> dict[str, Decimal]:
    document = _strict_json(payload, label="SZSE_RESPONSE", limit=2_000_000)
    if not isinstance(document, list) or len(document) != 1:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SZSE_RESPONSE_SCHEMA_INVALID"
        )
    block = document[0]
    if not isinstance(block, Mapping) or set(block) != {"metadata", "data", "error"}:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SZSE_RESPONSE_SCHEMA_INVALID"
        )
    if block.get("error") is not None:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SZSE_REPORTED_ERROR"
        )
    metadata = block.get("metadata")
    if not isinstance(metadata, Mapping):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SZSE_METADATA_INVALID"
        )
    conditions = _condition_defaults(metadata)
    expected_conditions = {
        "txtQueryDate": target.isoformat(),
        "tjzqlb": "D",
        "bkbz": "GP|XZ|CY",
        "cjbz": "cjl|cjje",
        "cxzs": "成交量（亿）|成交金额（亿元）",
    }
    if (
        metadata.get("catalogid") != SZSE_CATALOG_ID
        or metadata.get("name") != "日度概况"
        or metadata.get("tabkey") != "tab1"
        or conditions != expected_conditions
        or metadata.get("cols") != {
            "zbmc": "指标名称",
            "xz": "主板",
            "cy": "创业板",
            "gp": "深市合计",
        }
        or "数据统计范围包含存托凭证" not in str(metadata.get("footer") or "")
    ):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SZSE_METADATA_IDENTITY_INVALID"
        )
    rows = block.get("data")
    if (
        not isinstance(rows, list)
        or len(rows) != 2
        or not all(isinstance(row, Mapping) for row in rows)
    ):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SZSE_DATA_SCHEMA_INVALID"
        )
    parsed: dict[str, dict[str, Decimal]] = {}
    for raw in rows:
        row = dict(raw)
        if frozenset(row) != _SZSE_DATA_FIELDS:
            raise CNExchangeTurnoverSourceError(
                "CN_EXCHANGE_TURNOVER_SZSE_DATA_FIELD_DRIFT"
            )
        label = str(row.get("zbmc") or "").strip()
        if label not in {"成交量（亿）", "成交金额（亿元）"} or label in parsed:
            raise CNExchangeTurnoverSourceError(
                "CN_EXCHANGE_TURNOVER_SZSE_ROW_LABEL_INVALID"
            )
        parsed[label] = {
            "main": _normalized_decimal(row.get("xz"), "SZSE_MAIN"),
            "chinext": _normalized_decimal(row.get("cy"), "SZSE_CHINEXT"),
            "total": _normalized_decimal(row.get("gp"), "SZSE_TOTAL"),
        }
    if set(parsed) != {"成交量（亿）", "成交金额（亿元）"}:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SZSE_REQUIRED_ROW_MISSING"
        )
    for values in parsed.values():
        if values["main"] + values["chinext"] != values["total"]:
            raise CNExchangeTurnoverSourceError(
                "CN_EXCHANGE_TURNOVER_SZSE_TOTAL_VALUE_DRIFT"
            )
    return {
        "main_amount": parsed["成交金额（亿元）"]["main"],
        "chinext_amount": parsed["成交金额（亿元）"]["chinext"],
        "total_amount": parsed["成交金额（亿元）"]["total"],
        "total_volume": parsed["成交量（亿）"]["total"],
    }


def _validate_grant(
    b1_context: Mapping[str, Any],
    *,
    observed_at: datetime,
) -> dict[str, Any]:
    if not isinstance(b1_context, Mapping):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_B1_OPERATION_MISSING"
        )
    if (
        str(b1_context.get("temporal_scope") or "").upper()
        != TEMPORAL_SCOPE_CURRENT_OBSERVATION
    ):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_CURRENT_OBSERVATION_REQUIRED"
        )
    grant = validate_b1_provider_preflight(
        (b1_context.get("rights_by_dataset") or {}).get(DATASET_ID),
        operation_id=b1_context.get("operation_id"),
        intended_use=b1_context.get("intended_use"),
        dataset_id=DATASET_ID,
        consumer_scope=CONSUMER_SCOPE,
        research_as_of=b1_context.get("research_as_of"),
    )
    if (
        grant.get("schema_version") != RIGHTS_EVIDENCE_BUNDLE_SCHEMA_VERSION
        or grant.get("provider_identity") != PROVIDER_IDENTITY
    ):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_PROVIDER_GRANT_MISMATCH"
        )
    components = grant.get("components")
    if not isinstance(components, (list, tuple)) or tuple(
        (
            str(component.get("provider_identity") or ""),
            str(component.get("terms_uri") or ""),
        )
        for component in components
        if isinstance(component, Mapping)
    ) != RIGHTS_COMPONENTS:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_COMPONENT_TERMS_MISMATCH"
        )
    if _aware_utc(
        grant["terms_observed_at"], "TERMS_OBSERVED_AT"
    ) > observed_at:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_TERMS_AFTER_SOURCE_OBSERVATION"
        )
    return grant


def _compound_provider_route(sse_url: str, szse_url: str) -> str:
    return "SSE=" + sse_url + ";SZSE=" + szse_url


def _split_provider_route(value: Any) -> tuple[str, str]:
    text = str(value or "")
    if not text.startswith("SSE=") or ";SZSE=" not in text:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_PROVIDER_ROUTE_INVALID"
        )
    sse_url, szse_url = text[4:].split(";SZSE=", 1)
    return sse_url, szse_url


def build_cn_exchange_turnover_observation(
    sse_response_json: bytes,
    szse_response_json: bytes,
    *,
    sse_request_url: str,
    szse_request_url: str,
    expected_trade_date: str,
    observed_at: datetime,
    b1_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Build one current SSE+SZSE official two-exchange turnover observation."""
    try:
        target = date.fromisoformat(str(expected_trade_date))
    except ValueError as exc:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_EXPECTED_DATE_INVALID"
        ) from exc
    _validate_sse_request_url(sse_request_url, target)
    _validate_szse_request_url(szse_request_url)
    observed = _aware_utc(observed_at, "OBSERVED_AT")
    as_of = _aware_utc(b1_context.get("research_as_of"), "ASOF")
    if observed > as_of:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_RESPONSE_AFTER_ASOF"
        )
    _validate_grant(b1_context, observed_at=observed)
    from src.core.trading_calendar import (
        resolve_latest_completed_session_fail_closed,
    )
    latest_complete = resolve_latest_completed_session_fail_closed(
        "cn", current_time=observed,
    )
    if target > latest_complete:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SESSION_NOT_COMPLETED"
        )
    sse = _parse_sse_turnover(sse_response_json, target=target)
    szse = _parse_szse_turnover(szse_response_json, target=target)
    sse_total = sse["17"]["trade_amount_100m_cny"]
    sse_a = (
        sse["01"]["trade_amount_100m_cny"]
        + sse["03"]["trade_amount_100m_cny"]
    )
    sse_b = sse["02"]["trade_amount_100m_cny"]
    sse_cdr = sse["11"]["trade_amount_100m_cny"]
    combined = sse_total + szse["total_amount"]
    combined_cny = combined * Decimal("100000000")
    if combined_cny != combined_cny.to_integral_value():
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_CNY_CONVERSION_NOT_INTEGRAL"
        )
    facts = {
        "schema_version": FACT_SCHEMA,
        "trade_date": target.isoformat(),
        "coverage_scope": "SSE_AND_SZSE_OFFICIAL_DAILY_STOCK_TURNOVER",
        "sse_stock_total_turnover_100m_cny": _decimal_text(sse_total),
        "sse_a_share_turnover_100m_cny": _decimal_text(sse_a),
        "sse_b_share_turnover_100m_cny": _decimal_text(sse_b),
        "sse_cdr_turnover_100m_cny": _decimal_text(sse_cdr),
        "sse_listed_stock_count": sse["17"]["listed_count"],
        "szse_main_board_turnover_100m_cny": _decimal_text(
            szse["main_amount"]
        ),
        "szse_chinext_turnover_100m_cny": _decimal_text(
            szse["chinext_amount"]
        ),
        "szse_total_turnover_100m_cny": _decimal_text(
            szse["total_amount"]
        ),
        "szse_scope_includes_depositary_receipts": True,
        "combined_two_exchange_stock_turnover_100m_cny": _decimal_text(
            combined
        ),
        "combined_two_exchange_stock_turnover_cny": int(combined_cny),
        "two_exchange_turnover_value_admitted": True,
        "bse_included": False,
        "exact_all_a_turnover_admitted": False,
        "all_a_bse_denominator_admitted": False,
        "breadth_denominator_admitted": False,
        "turnover_rate_admitted": False,
        "relative_volume_admitted": False,
    }
    observed_iso = observed.isoformat()
    sse_hash = sha256(sse_response_json).hexdigest()
    szse_hash = sha256(szse_response_json).hexdigest()
    content_hash = sha256_payload({
        "sse_response_sha256": sse_hash,
        "szse_response_sha256": szse_hash,
    })
    provider_route = _compound_provider_route(
        sse_request_url, szse_request_url,
    )
    identity_hash = sha256_payload({
        "dataset_id": DATASET_ID,
        "provider_identity": PROVIDER_IDENTITY,
        "provider_route": provider_route,
        "source_content_sha256": content_hash,
        "source_observed_at": observed_iso,
        "completed_through": target.isoformat(),
    })
    result = {
        "schema_version": SOURCE_SCHEMA,
        "dataset_id": DATASET_ID,
        "state": "PARTIAL",
        "reason_codes": (
            "SSE_AND_SZSE_OFFICIAL_TWO_EXCHANGE_TURNOVER_OBSERVED",
            "SZSE_TOTAL_INCLUDES_DEPOSITARY_RECEIPTS",
            "BSE_NOT_INCLUDED_DUE_SEPARATE_RIGHTS_GATE",
            "EXACT_ALL_A_BREADTH_TURNOVER_RATE_AND_RELATIVE_VOLUME_NOT_ADMITTED",
            "CURRENT_OBSERVATION_ONLY",
        ),
        "provider_identity": PROVIDER_IDENTITY,
        "provider_route": provider_route,
        "producer_authority": PRODUCER_AUTHORITY,
        "source_identity_hash": identity_hash,
        "source_content_sha256": content_hash,
        "source_observed_at": observed_iso,
        "source_available_at": observed_iso,
        "completed_through": target.isoformat(),
        "units": {
            "exchange_turnover": "CNY_100_MILLION",
            "combined_turnover": "CNY",
            "listed_count": "COUNT",
        },
        "currency": "CNY",
        "adjustment_basis": "NOT_APPLICABLE",
        "revision": "UNKNOWN",
        "vintage": "UNKNOWN",
        "historical_pit_eligible": False,
        "independent_source_value_admitted": True,
        "strategy_upgrade_authority": False,
        "facts": facts,
    }
    result["observation_hash"] = sha256_payload(result)
    return validate_cn_exchange_turnover_observation(
        result,
        expected_trade_date=target.isoformat(),
    )


def validate_cn_exchange_turnover_observation(
    value: Mapping[str, Any],
    *,
    expected_trade_date: str | None = None,
) -> dict[str, Any]:
    """Revalidate one composite turnover observation at Registry boundary."""
    if not isinstance(value, Mapping) or set(value) != _OBSERVATION_FIELDS:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_OBSERVATION_SCHEMA_MISMATCH"
        )
    row = dict(value)
    expected_reason_codes = (
        "SSE_AND_SZSE_OFFICIAL_TWO_EXCHANGE_TURNOVER_OBSERVED",
        "SZSE_TOTAL_INCLUDES_DEPOSITARY_RECEIPTS",
        "BSE_NOT_INCLUDED_DUE_SEPARATE_RIGHTS_GATE",
        "EXACT_ALL_A_BREADTH_TURNOVER_RATE_AND_RELATIVE_VOLUME_NOT_ADMITTED",
        "CURRENT_OBSERVATION_ONLY",
    )
    if (
        row.get("schema_version") != SOURCE_SCHEMA
        or row.get("dataset_id") != DATASET_ID
        or row.get("state") != "PARTIAL"
        or tuple(row.get("reason_codes") or ()) != expected_reason_codes
        or row.get("provider_identity") != PROVIDER_IDENTITY
        or row.get("producer_authority") != PRODUCER_AUTHORITY
        or row.get("historical_pit_eligible") is not False
        or row.get("independent_source_value_admitted") is not True
        or row.get("strategy_upgrade_authority") is not False
        or row.get("currency") != "CNY"
        or row.get("adjustment_basis") != "NOT_APPLICABLE"
        or row.get("revision") != "UNKNOWN"
        or row.get("vintage") != "UNKNOWN"
        or row.get("units") != {
            "exchange_turnover": "CNY_100_MILLION",
            "combined_turnover": "CNY",
            "listed_count": "COUNT",
        }
    ):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_OBSERVATION_INVALID"
        )
    for field in (
        "source_identity_hash",
        "source_content_sha256",
        "observation_hash",
    ):
        text = str(row.get(field) or "").lower()
        if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
            raise CNExchangeTurnoverSourceError(
                "CN_EXCHANGE_TURNOVER_OBSERVATION_HASH_INVALID:" + field
            )
    expected_hash = sha256_payload({
        key: row[key] for key in row if key != "observation_hash"
    })
    if row["observation_hash"] != expected_hash:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_OBSERVATION_HASH_MISMATCH"
        )
    observed = _aware_utc(row.get("source_observed_at"), "OBSERVED_AT")
    available = _aware_utc(row.get("source_available_at"), "AVAILABLE_AT")
    if available != observed:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_AVAILABLE_AT_MISMATCH"
        )
    completed = date.fromisoformat(str(row.get("completed_through") or ""))
    if expected_trade_date is not None and completed != date.fromisoformat(
        str(expected_trade_date)
    ):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_COMPLETED_THROUGH_MISMATCH"
        )
    sse_url, szse_url = _split_provider_route(row.get("provider_route"))
    _validate_sse_request_url(sse_url, completed)
    _validate_szse_request_url(szse_url)
    facts = row.get("facts")
    if not isinstance(facts, Mapping) or set(facts) != _FACT_FIELDS:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_FACTS_SCHEMA_INVALID"
        )
    false_flags = (
        "bse_included",
        "exact_all_a_turnover_admitted",
        "all_a_bse_denominator_admitted",
        "breadth_denominator_admitted",
        "turnover_rate_admitted",
        "relative_volume_admitted",
    )
    if (
        facts.get("schema_version") != FACT_SCHEMA
        or facts.get("trade_date") != completed.isoformat()
        or facts.get("coverage_scope")
        != "SSE_AND_SZSE_OFFICIAL_DAILY_STOCK_TURNOVER"
        or facts.get("szse_scope_includes_depositary_receipts") is not True
        or facts.get("two_exchange_turnover_value_admitted") is not True
        or any(facts.get(field) is not False for field in false_flags)
        or type(facts.get("sse_listed_stock_count")) is not int
        or facts["sse_listed_stock_count"] < 0
    ):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_FACTS_INVALID"
        )
    decimal_fields = (
        "sse_stock_total_turnover_100m_cny",
        "sse_a_share_turnover_100m_cny",
        "sse_b_share_turnover_100m_cny",
        "sse_cdr_turnover_100m_cny",
        "szse_main_board_turnover_100m_cny",
        "szse_chinext_turnover_100m_cny",
        "szse_total_turnover_100m_cny",
        "combined_two_exchange_stock_turnover_100m_cny",
    )
    numbers: dict[str, Decimal] = {}
    for field in decimal_fields:
        number = _normalized_decimal(facts.get(field), field.upper())
        if _decimal_text(number) != str(facts.get(field)):
            raise CNExchangeTurnoverSourceError(
                "CN_EXCHANGE_TURNOVER_FACT_NOT_NORMALIZED:" + field
            )
        numbers[field] = number
    if (
        numbers["sse_a_share_turnover_100m_cny"]
        + numbers["sse_b_share_turnover_100m_cny"]
        != numbers["sse_stock_total_turnover_100m_cny"]
        or numbers["szse_main_board_turnover_100m_cny"]
        + numbers["szse_chinext_turnover_100m_cny"]
        != numbers["szse_total_turnover_100m_cny"]
        or numbers["sse_stock_total_turnover_100m_cny"]
        + numbers["szse_total_turnover_100m_cny"]
        != numbers["combined_two_exchange_stock_turnover_100m_cny"]
    ):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_FACT_TOTAL_DRIFT"
        )
    combined_cny = facts.get("combined_two_exchange_stock_turnover_cny")
    if (
        type(combined_cny) is not int
        or combined_cny
        != int(
            numbers["combined_two_exchange_stock_turnover_100m_cny"]
            * Decimal("100000000")
        )
    ):
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_FACT_CNY_DRIFT"
        )
    identity_hash = sha256_payload({
        "dataset_id": DATASET_ID,
        "provider_identity": PROVIDER_IDENTITY,
        "provider_route": row["provider_route"],
        "source_content_sha256": row["source_content_sha256"],
        "source_observed_at": row["source_observed_at"],
        "completed_through": row["completed_through"],
    })
    if row["source_identity_hash"] != identity_hash:
        raise CNExchangeTurnoverSourceError(
            "CN_EXCHANGE_TURNOVER_SOURCE_IDENTITY_HASH_MISMATCH"
        )
    return row
