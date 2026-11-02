"""Official SSE public disclosures -> existing DSA data-object source seam.

Pure adapter only: no network, persistence, scheduler, report, model or second
Registry.  The caller supplies same-run response bytes and a validated B1
NO_SEND operation context.  Current public disclosures are never historical
PIT merely because DSA observed them.
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
from src.services.data_object_admission import validate_b1_provider_preflight


class SSEPublicDisclosureSourceError(ValueError):
    """Invalid SSE disclosure, source identity, timing or operation scope."""


PROVIDER_IDENTITY = "SSEOfficialPublicDisclosure"
SOURCE_SCHEMA = "data-object-source-observation-v1"
BUNDLE_SCHEMA = "sse-public-disclosure-bundle-v1"
_QUERY_HOST = "query.sse.com.cn"
_QUERY_PATH = "/commonQuery.do"
_COMPANY_SQL = "COMMON_SSE_CP_GPJCTPZ_GPLB_GPGK_GSGK_C"
_ETF_BASIC_SQL = "COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_JBXX_C"
_ETF_BASKET_SQL = "COMMON_SSE_CP_JJLB_ETFJJGK_GGSGSHQD_COMPONENT_C"
_CAPITAL_SQL = "COMMON_SSE_CP_GPJCTPZ_GPLB_GPGK_GBJG_C"
_DIVIDEND_SQL = "COMMON_SSE_CP_GPJCTPZ_GPLB_LRFP_FH_L"
_EQUITY_CHANGE_PATH = "/security/stock/queryEquityChangeAndReason.do"
_CORE_DATASET_IDS = (
    "P_CODE_IDENTITY", "P_INDUSTRY", "S_CLASSIFICATION",
    "E_NAV_IOPV", "E_SHARES", "E_BASKET",
)
_DATASET_CONSUMERS = {
    "P_CODE_IDENTITY": "ASSET_RESEARCH_BRIEF",
    "P_INDUSTRY": "ASSET_RESEARCH_BRIEF",
    "S_CLASSIFICATION": "MARKET_SECTOR_SECTION",
    "E_NAV_IOPV": "ETF_ASSET_BRIEF",
    "E_SHARES": "ETF_ASSET_BRIEF",
    "E_BASKET": "ETF_ASSET_BRIEF",
    "P_SHARES_EVENTS": "ASSET_RESEARCH_BRIEF",
    "P_DIVIDEND": "ASSET_RESEARCH_BRIEF",
}


def _aware_utc(value: Any, field: str) -> datetime:
    try:
        parsed = (
            value if isinstance(value, datetime)
            else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        )
    except (TypeError, ValueError) as exc:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_" + field + "_INVALID"
        ) from exc
    if (
        not isinstance(parsed, datetime)
        or parsed.tzinfo is None
        or parsed.utcoffset() is None
    ):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_" + field + "_NAIVE"
        )
    return parsed.astimezone(timezone.utc)


def _strict_json(
    payload: bytes,
    *,
    sql_id: str,
    pagination: bool = False,
) -> tuple[dict[str, Any], list[dict]]:
    if (
        not isinstance(payload, bytes)
        or not payload
        or len(payload) > 2_000_000
    ):
        raise SSEPublicDisclosureSourceError("SSE_DISCLOSURE_RESPONSE_BYTES_INVALID")

    def reject_constant(value: str) -> None:
        raise ValueError("non-finite JSON constant: " + value)

    try:
        document = json.loads(
            payload.decode("utf-8"), parse_constant=reject_constant
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_RESPONSE_JSON_INVALID"
        ) from exc
    if not isinstance(document, Mapping):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_RESPONSE_SCHEMA_INVALID"
        )
    expected_pagination = "true" if pagination else "false"
    if document.get("sqlId") != sql_id or str(
        document.get("isPagination")
    ).lower() != expected_pagination:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_RESPONSE_SQL_ID_MISMATCH"
        )
    if document.get("actionErrors") not in (None, [], {}) or document.get(
        "fieldErrors"
    ) not in (None, [], {}):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_RESPONSE_REPORTED_ERROR"
        )
    rows = document.get("result")
    if not isinstance(rows, list) or not all(
        isinstance(row, Mapping) for row in rows
    ):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_RESULT_SCHEMA_INVALID"
        )
    return dict(document), [dict(row) for row in rows]


def _validate_request_url(
    url: str,
    *,
    sql_id: str,
    code_key: str,
    expected_code: str,
    pagination: bool = False,
    extra_query: Mapping[str, str] | None = None,
) -> None:
    parts = urlsplit(str(url or ""))
    if (
        parts.scheme != "https"
        or parts.netloc != _QUERY_HOST
        or parts.path != _QUERY_PATH
        or parts.fragment
    ):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_SOURCE_URL_NOT_OFFICIAL"
        )
    try:
        query = parse_qs(
            parts.query, strict_parsing=True, keep_blank_values=True
        )
    except ValueError as exc:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_SOURCE_QUERY_INVALID"
        ) from exc
    expected = {
        "isPagination": ["true" if pagination else "false"],
        "sqlId": [sql_id],
        code_key: [expected_code],
    }
    for key, value in (extra_query or {}).items():
        expected[str(key)] = [str(value)]
    if query != expected:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_SOURCE_QUERY_INVALID"
        )


def _validate_equity_change_request_url(
    url: str,
    *,
    company_code: str,
) -> None:
    parts = urlsplit(str(url or ""))
    if (
        parts.scheme != "https"
        or parts.netloc != _QUERY_HOST
        or parts.path != _EQUITY_CHANGE_PATH
        or parts.fragment
    ):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_SOURCE_URL_NOT_OFFICIAL"
        )
    try:
        query = parse_qs(
            parts.query, strict_parsing=True, keep_blank_values=True
        )
    except ValueError as exc:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_SOURCE_QUERY_INVALID"
        ) from exc
    expected = {
        "isPagination": ["true"],
        "pageHelp.pageSize": ["50"],
        "pageHelp.pageNo": ["1"],
        "pageHelp.beginPage": ["1"],
        "pageHelp.cacheSize": ["1"],
        "pageHelp.endPage": ["1"],
        "companyCode": [company_code],
    }
    if query != expected:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_SOURCE_QUERY_INVALID"
        )


def _strict_equity_change_json(
    payload: bytes,
    *,
    company_code: str,
) -> tuple[dict[str, Any], list[dict]]:
    if not isinstance(payload, bytes) or not payload or len(payload) > 2_000_000:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_RESPONSE_BYTES_INVALID"
        )
    try:
        document = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_RESPONSE_JSON_INVALID"
        ) from exc
    if (
        not isinstance(document, Mapping)
        or str(document.get("companyCode") or "") != company_code
        or str(document.get("isPagination")).lower() != "true"
    ):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_EQUITY_CHANGE_SCHEMA_INVALID"
        )
    rows = document.get("result")
    page_help = document.get("pageHelp")
    page_rows = page_help.get("data") if isinstance(page_help, Mapping) else None
    if not isinstance(rows, list):
        rows = page_rows
    if (
        not isinstance(rows, list)
        or not rows
        or not all(isinstance(row, Mapping) for row in rows)
        or (isinstance(page_rows, list) and list(rows) != list(page_rows))
    ):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_EQUITY_CHANGE_ROWS_INVALID"
        )
    if isinstance(page_help, Mapping) and int(page_help.get("total") or 0) != len(rows):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_EQUITY_CHANGE_COUNT_MISMATCH"
        )
    return dict(document), [dict(row) for row in rows]


def _parse_iso_date(value: Any, *, field: str) -> date:
    text = str(value or "").strip()
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_" + field + "_INVALID"
        ) from exc
    return parsed


def _ten_thousand_shares(value: Any, *, field: str) -> int:
    text = _decimal_text(value, field=field)
    assert text is not None
    shares = Decimal(text) * Decimal("10000")
    if shares != shares.to_integral_value() or shares < 0:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_" + field + "_INVALID"
        )
    return int(shares)


def _parse_yyyymmdd(value: Any, *, field: str) -> date:
    text = str(value or "").strip()
    if not re.fullmatch(r"20\d{6}", text):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_" + field + "_INVALID"
        )
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError as exc:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_" + field + "_INVALID"
        ) from exc


def _decimal_text(value: Any, *, field: str, allow_dash: bool = False) -> str | None:
    # Numeric zero is a lawful disclosed value (for example B-shares=0).
    # Do not erase it through truthiness coercion.
    text = ("" if value is None else str(value)).strip().replace(",", "")
    if allow_dash and text in {"", "-"}:
        return None
    text = text.lstrip("￥¥")
    try:
        number = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_" + field + "_INVALID"
        ) from exc
    if not number.is_finite():
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_" + field + "_INVALID"
        )
    return str(number)


def _integer(value: Any, *, field: str, minimum: int = 0) -> int:
    # Keep integer zero distinct from missing.
    text = ("" if value is None else str(value)).strip()
    if not re.fullmatch(r"\d+", text):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_" + field + "_INVALID"
        )
    result = int(text)
    if result < minimum:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_" + field + "_INVALID"
        )
    return result


def _validate_grant(
    b1_context: Mapping[str, Any],
    *,
    dataset_id: str,
    observed_at: datetime,
) -> dict[str, Any]:
    rights_by_dataset = b1_context.get("rights_by_dataset")
    if not isinstance(rights_by_dataset, Mapping):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_B1_RIGHTS_MISSING"
        )
    grant = validate_b1_provider_preflight(
        rights_by_dataset.get(dataset_id),
        operation_id=b1_context.get("operation_id"),
        intended_use=b1_context.get("intended_use"),
        dataset_id=dataset_id,
        consumer_scope=_DATASET_CONSUMERS[dataset_id],
        research_as_of=b1_context.get("research_as_of"),
    )
    if grant["provider_identity"] != PROVIDER_IDENTITY:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_PROVIDER_GRANT_MISMATCH"
        )
    if _aware_utc(grant["terms_observed_at"], "TERMS_OBSERVED_AT") > observed_at:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_TERMS_AFTER_SOURCE_OBSERVATION"
        )
    return grant


def _observation(
    *,
    dataset_id: str,
    state: str,
    reason_codes: tuple[str, ...],
    provider_route: str,
    response_bytes: bytes,
    observed_at: datetime,
    completed_through: str,
    units: Mapping[str, str],
    currency: str,
    adjustment_basis: str,
    revision: str,
    vintage: str,
    facts: Mapping[str, Any],
    producer_authority: str = (
        "data_provider.sse_public_disclosure_source."
        "build_sse_public_disclosure_bundle"
    ),
) -> dict[str, Any]:
    if state not in {"READY", "PARTIAL"}:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_SOURCE_STATE_INVALID"
        )
    content_hash = sha256(response_bytes).hexdigest()
    observed_iso = observed_at.isoformat()
    identity = sha256_payload({
        "dataset_id": dataset_id,
        "provider_identity": PROVIDER_IDENTITY,
        "provider_route": provider_route,
        "source_content_sha256": content_hash,
        "source_observed_at": observed_iso,
        "completed_through": completed_through,
    })
    result = {
        "schema_version": SOURCE_SCHEMA,
        "dataset_id": dataset_id,
        "state": state,
        "reason_codes": tuple(reason_codes),
        "provider_identity": PROVIDER_IDENTITY,
        "provider_route": provider_route,
        "producer_authority": producer_authority,
        "source_identity_hash": identity,
        "source_content_sha256": content_hash,
        "source_observed_at": observed_iso,
        # No backdating: the public response is admitted no earlier than DSA
        # actually observed the current disclosure.
        "source_available_at": observed_iso,
        "completed_through": completed_through,
        "units": dict(units),
        "currency": currency,
        "adjustment_basis": adjustment_basis,
        "revision": revision,
        "vintage": vintage,
        "historical_pit_eligible": False,
        "independent_source_value_admitted": True,
        "strategy_upgrade_authority": False,
        "facts": dict(facts),
    }
    result["observation_hash"] = sha256_payload(result)
    return result


def build_sse_public_disclosure_bundle(
    company_response_json: bytes,
    etf_basic_response_json: bytes,
    etf_basket_response_json: bytes,
    *,
    company_request_url: str,
    etf_basic_request_url: str,
    etf_basket_request_url: str,
    company_code: str,
    fund_code: str,
    observed_at: datetime,
    b1_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Build current Stock/Sector/ETF disclosure observations in one run.

    Only P_CODE_IDENTITY and E_BASKET have enough current response semantics
    to become NO_SEND Product candidates.  Industry classification, NAV/IOPV
    and shares remain PARTIAL because version/validity, actual IOPV time/value
    or total-share/split facts are absent from these exact responses.
    """
    if not re.fullmatch(r"\d{6}", str(company_code or "")):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_COMPANY_CODE_INVALID"
        )
    if not re.fullmatch(r"\d{6}", str(fund_code or "")):
        raise SSEPublicDisclosureSourceError("SSE_DISCLOSURE_FUND_CODE_INVALID")
    if not isinstance(b1_context, Mapping):
        raise SSEPublicDisclosureSourceError("SSE_DISCLOSURE_B1_OPERATION_MISSING")
    observed = _aware_utc(observed_at, "OBSERVED_AT")
    as_of = _aware_utc(b1_context.get("research_as_of"), "ASOF")
    if observed > as_of:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_RESPONSE_AFTER_ASOF"
        )
    for dataset_id in _CORE_DATASET_IDS:
        _validate_grant(
            b1_context, dataset_id=dataset_id, observed_at=observed
        )

    _validate_request_url(
        company_request_url,
        sql_id=_COMPANY_SQL,
        code_key="COMPANY_CODE",
        expected_code=company_code,
    )
    _validate_request_url(
        etf_basic_request_url,
        sql_id=_ETF_BASIC_SQL,
        code_key="FUNDID2",
        expected_code=fund_code,
    )
    _validate_request_url(
        etf_basket_request_url,
        sql_id=_ETF_BASKET_SQL,
        code_key="FUNDID2",
        expected_code=fund_code,
    )
    _company_doc, company_rows = _strict_json(
        company_response_json, sql_id=_COMPANY_SQL
    )
    _basic_doc, basic_rows = _strict_json(
        etf_basic_response_json, sql_id=_ETF_BASIC_SQL
    )
    _basket_doc, basket_rows = _strict_json(
        etf_basket_response_json, sql_id=_ETF_BASKET_SQL
    )
    if len(company_rows) != 1:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_COMPANY_ROW_COUNT_INVALID"
        )
    company = company_rows[0]
    if (
        str(company.get("A_STOCK_CODE") or "").strip() != company_code
        or str(company.get("COMPANY_CODE") or "").strip() != company_code
        or str(company.get("STATE_CODE_A_DESC") or "").strip() != "上市"
    ):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_COMPANY_IDENTITY_MISMATCH"
        )
    listing_date = _parse_yyyymmdd(
        company.get("A_LIST_DATE"), field="COMPANY_LISTING_DATE"
    )
    if listing_date > observed.date():
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_COMPANY_LISTING_DATE_FUTURE"
        )
    full_name = str(company.get("FULL_NAME") or "").strip()
    short_name = str(company.get("SECURITY_ABBR_A_CN") or "").strip()
    csrc_code = str(company.get("CSRC_CODE") or "").strip()
    csrc_code_desc = str(company.get("CSRC_CODE_DESC") or "").strip()
    csrc_great_code = str(company.get("CSRC_GREAT_CODE") or "").strip()
    csrc_great_desc = str(company.get("CSRC_GREAT_CODE_DESC") or "").strip()
    if not all((full_name, short_name, csrc_code, csrc_code_desc,
                csrc_great_code, csrc_great_desc)):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_COMPANY_REQUIRED_FIELD_MISSING"
        )
    observed_iso = observed.isoformat()
    company_hash = sha256(company_response_json).hexdigest()
    stock_observations = {
        "P_CODE_IDENTITY": _observation(
            dataset_id="P_CODE_IDENTITY",
            state="READY",
            reason_codes=("OFFICIAL_CURRENT_COMPANY_IDENTITY_OBSERVED",),
            provider_route=company_request_url,
            response_bytes=company_response_json,
            observed_at=observed,
            completed_through=observed.date().isoformat(),
            units={
                "identity": "TEXT",
                "listing_date": "ISO_DATE",
                "status": "TEXT",
            },
            currency="NOT_APPLICABLE",
            adjustment_basis="NOT_APPLICABLE",
            revision="SSE_CURRENT_RESPONSE_SHA256:" + company_hash,
            vintage=observed_iso,
            facts={
                "stock_code": company_code,
                "full_name": full_name,
                "short_name": short_name,
                "listing_date": listing_date.isoformat(),
                "listing_state": "上市",
                "listing_board": str(company.get("LIST_BOARD") or "").strip(),
                "security_type": str(company.get("SEC_TYPE") or "").strip(),
            },
        ),
        "P_INDUSTRY": _observation(
            dataset_id="P_INDUSTRY",
            state="PARTIAL",
            reason_codes=(
                "REVENUE_STRUCTURE_NOT_IN_CURRENT_COMPANY_RESPONSE",
                "CLASSIFICATION_VERSION_AND_VALIDITY_NOT_PUBLISHED",
            ),
            provider_route=company_request_url,
            response_bytes=company_response_json,
            observed_at=observed,
            completed_through=observed.date().isoformat(),
            units={"industry_code": "TEXT", "industry_name": "TEXT"},
            currency="NOT_APPLICABLE",
            adjustment_basis="NOT_APPLICABLE",
            revision="UNKNOWN",
            vintage="UNKNOWN",
            facts={
                "stock_code": company_code,
                "csrc_code": csrc_code,
                "csrc_code_desc": csrc_code_desc,
                "csrc_great_code": csrc_great_code,
                "csrc_great_code_desc": csrc_great_desc,
            },
        ),
        "S_CLASSIFICATION": _observation(
            dataset_id="S_CLASSIFICATION",
            state="PARTIAL",
            reason_codes=(
                "CLASSIFICATION_STANDARD_VERSION_NOT_PUBLISHED",
                "SECURITY_ASSIGNMENT_VALIDITY_INTERVAL_NOT_PUBLISHED",
            ),
            provider_route=company_request_url,
            response_bytes=company_response_json,
            observed_at=observed,
            completed_through=observed.date().isoformat(),
            units={"classification_code": "TEXT", "classification_name": "TEXT"},
            currency="NOT_APPLICABLE",
            adjustment_basis="NOT_APPLICABLE",
            revision="UNKNOWN",
            vintage="UNKNOWN",
            facts={
                "stock_code": company_code,
                "standard_owner": "CSRC_DISCLOSURE_VIA_SSE",
                "csrc_code": csrc_code,
                "csrc_code_desc": csrc_code_desc,
                "csrc_great_code": csrc_great_code,
                "csrc_great_code_desc": csrc_great_desc,
            },
        ),
    }

    if len(basic_rows) != 1:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_ETF_BASIC_ROW_COUNT_INVALID"
        )
    basic = basic_rows[0]
    if str(basic.get("TRADE_CODE") or "").strip() != fund_code:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_ETF_CODE_MISMATCH"
        )
    trading_day = _parse_yyyymmdd(
        basic.get("TRADING_DAY"), field="ETF_TRADING_DAY"
    )
    previous_day = _parse_yyyymmdd(
        basic.get("PRE_TRADING_DAY"), field="ETF_PREVIOUS_TRADING_DAY"
    )
    if trading_day > observed.date() or previous_day > trading_day:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_ETF_TRADING_DAY_INVALID"
        )
    nav = _decimal_text(basic.get("NAV"), field="ETF_NAV")
    if nav is None or Decimal(nav) <= 0:
        raise SSEPublicDisclosureSourceError("SSE_DISCLOSURE_ETF_NAV_INVALID")
    creation_unit = _integer(
        basic.get("CREATION_REDEMPTION_UNIT"),
        field="ETF_CREATION_REDEMPTION_UNIT",
        minimum=1,
    )
    record_num = _integer(
        basic.get("RECORD_NUM"), field="ETF_RECORD_NUM", minimum=1
    )
    file_id = str(basic.get("FILE_ID") or "").strip()
    fund_name = str(basic.get("FUND_NAME") or "").strip()
    manager = str(basic.get("FUND_COMP_NAME") or "").strip()
    publish_iopv = str(basic.get("PUBLISH_IOPV") or "").strip()
    if (
        not re.fullmatch(r"\d+", file_id)
        or not fund_name
        or not manager
        or publish_iopv not in {"是", "否"}
    ):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_ETF_REQUIRED_FIELD_MISSING"
        )
    if len(basket_rows) != record_num:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_ETF_BASKET_COUNT_MISMATCH"
        )
    component_ids: set[str] = set()
    components: list[dict[str, Any]] = []
    for row in basket_rows:
        component_id = str(row.get("INSTRUMENT_ID") or "").strip()
        component_name = str(row.get("INSTRUMENT_NAME") or "").strip()
        if (
            not re.fullmatch(r"\d{6}", component_id)
            or component_id in component_ids
            or not component_name
        ):
            raise SSEPublicDisclosureSourceError(
                "SSE_DISCLOSURE_ETF_BASKET_COMPONENT_INVALID"
            )
        component_ids.add(component_id)
        quantity = _integer(
            row.get("QUANTITY"), field="ETF_COMPONENT_QUANTITY", minimum=0
        )
        substitution_flag = str(row.get("SUBSTITUTION_FLAG") or "").strip()
        if substitution_flag not in {"1", "2"}:
            raise SSEPublicDisclosureSourceError(
                "SSE_DISCLOSURE_ETF_SUBSTITUTION_FLAG_INVALID"
            )
        components.append({
            "instrument_id": component_id,
            "instrument_name": component_name,
            "quantity": quantity,
            "substitution_flag": substitution_flag,
            "substitution_cash_amount_cny": _decimal_text(
                row.get("SUBSTITUTION_CASH_AMOUNT"),
                field="ETF_SUBSTITUTION_CASH",
                allow_dash=True,
            ),
            "creation_premium_rate": str(
                row.get("CREATION_PREMIUM_RATE") or ""
            ).strip(),
            "redemption_discount_rate": str(
                row.get("REDEMPTION_DISCOUNT_RATE") or ""
            ).strip(),
            "underlying_market_code": str(
                row.get("UNDERLYION_SECURITY_ID") or ""
            ).strip(),
        })
    etf_observations = {
        "E_NAV_IOPV": _observation(
            dataset_id="E_NAV_IOPV",
            state="PARTIAL",
            reason_codes=(
                "OFFICIAL_NAV_OBSERVED",
                "ACTUAL_IOPV_VALUE_AND_PUBLISH_TIMESTAMP_NOT_IN_RESPONSE",
            ),
            provider_route=etf_basic_request_url,
            response_bytes=etf_basic_response_json,
            observed_at=observed,
            completed_through=trading_day.isoformat(),
            units={"nav": "CNY_PER_FUND_SHARE", "publish_iopv": "BOOLEAN_TEXT"},
            currency="CNY",
            adjustment_basis="NOT_APPLICABLE",
            revision="SSE_PCF_FILE_ID:" + file_id,
            vintage="UNKNOWN",
            facts={
                "fund_code": fund_code,
                "fund_name": fund_name,
                "manager": manager,
                "trading_day": trading_day.isoformat(),
                "nav_cny_per_share": nav,
                "publish_iopv": publish_iopv == "是",
            },
        ),
        "E_SHARES": _observation(
            dataset_id="E_SHARES",
            state="PARTIAL",
            reason_codes=(
                "CREATION_REDEMPTION_UNIT_OBSERVED",
                "TOTAL_SHARES_SPLIT_AND_DAILY_SHARE_CHANGE_NOT_IN_RESPONSE",
            ),
            provider_route=etf_basic_request_url,
            response_bytes=etf_basic_response_json,
            observed_at=observed,
            completed_through=trading_day.isoformat(),
            units={"creation_redemption_unit": "FUND_SHARE"},
            currency="NOT_APPLICABLE",
            adjustment_basis="NOT_APPLICABLE",
            revision="SSE_PCF_FILE_ID:" + file_id,
            vintage="UNKNOWN",
            facts={
                "fund_code": fund_code,
                "trading_day": trading_day.isoformat(),
                "creation_redemption_unit_shares": creation_unit,
                "creation_redemption_status": str(
                    basic.get("CREATION_REDEMPTION") or ""
                ).strip(),
            },
        ),
        "E_BASKET": _observation(
            dataset_id="E_BASKET",
            state="READY",
            reason_codes=("OFFICIAL_CURRENT_PCF_BASKET_OBSERVED",),
            provider_route=etf_basket_request_url,
            response_bytes=etf_basket_response_json,
            observed_at=observed,
            completed_through=trading_day.isoformat(),
            units={
                "quantity": "SHARE",
                "substitution_cash": "CNY",
                "rate": "PERCENT",
            },
            currency="CNY",
            adjustment_basis="NOT_APPLICABLE",
            revision="SSE_PCF_FILE_ID:" + file_id,
            vintage=observed_iso,
            facts={
                "fund_code": fund_code,
                "fund_name": fund_name,
                "trading_day": trading_day.isoformat(),
                "previous_trading_day": previous_day.isoformat(),
                "file_id": file_id,
                "component_count": len(components),
                "creation_redemption_unit_shares": creation_unit,
                "components": components,
            },
        ),
    }
    bundle = {
        "schema_version": BUNDLE_SCHEMA,
        "provider_identity": PROVIDER_IDENTITY,
        "observed_at": observed_iso,
        "stock_code": company_code,
        "fund_code": fund_code,
        "stock_observations": stock_observations,
        "etf_observations": etf_observations,
        "storage_admitted": False,
        "training_admitted": False,
        "redistribution_admitted": False,
        "strategy_upgrade_authority": False,
    }
    bundle["bundle_hash"] = sha256_payload(bundle)
    return bundle


def build_sse_stock_event_disclosure_bundle(
    capital_response_json: bytes,
    equity_change_response_json: bytes,
    dividend_response_json: bytes,
    *,
    capital_request_url: str,
    equity_change_request_url: str,
    dividend_request_url: str,
    company_code: str,
    observed_at: datetime,
    b1_context: Mapping[str, Any],
) -> dict[str, Any]:
    """Build current stock-share and dividend observations from official SSE.

    Dividend records carry explicit registration/ex-dividend dates and units.
    Share events remain PARTIAL whenever the latest event history does not
    reconcile with the official current capital snapshot.
    """
    if not re.fullmatch(r"\d{6}", str(company_code or "")):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_COMPANY_CODE_INVALID"
        )
    if not isinstance(b1_context, Mapping):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_B1_OPERATION_MISSING"
        )
    observed = _aware_utc(observed_at, "OBSERVED_AT")
    as_of = _aware_utc(b1_context.get("research_as_of"), "ASOF")
    if observed > as_of:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_RESPONSE_AFTER_ASOF"
        )
    for dataset_id in ("P_SHARES_EVENTS", "P_DIVIDEND"):
        _validate_grant(
            b1_context, dataset_id=dataset_id, observed_at=observed
        )
    _validate_request_url(
        capital_request_url,
        sql_id=_CAPITAL_SQL,
        code_key="COMPANY_CODE",
        expected_code=company_code,
    )
    _validate_equity_change_request_url(
        equity_change_request_url, company_code=company_code
    )
    _validate_request_url(
        dividend_request_url,
        sql_id=_DIVIDEND_SQL,
        code_key="COMPANY_CODE",
        expected_code=company_code,
        pagination=True,
        extra_query={
            "pageHelp.pageSize": "20",
            "pageHelp.pageNo": "1",
            "pageHelp.beginPage": "1",
            "pageHelp.cacheSize": "1",
            "pageHelp.endPage": "1",
            "IS_STAR": "0",
            "CONDITION_ZBA": "1",
            "CONDITION_ZBB": "",
        },
    )
    _capital_doc, capital_rows = _strict_json(
        capital_response_json, sql_id=_CAPITAL_SQL
    )
    _events_doc, event_rows = _strict_equity_change_json(
        equity_change_response_json, company_code=company_code
    )
    dividend_doc, dividend_rows = _strict_json(
        dividend_response_json, sql_id=_DIVIDEND_SQL, pagination=True
    )
    if len(capital_rows) != 1:
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_CAPITAL_ROW_COUNT_INVALID"
        )
    capital = capital_rows[0]
    capital_date = _parse_yyyymmdd(
        capital.get("TRADE_DATE"), field="CAPITAL_TRADE_DATE"
    )
    if capital_date > observed.date():
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_CAPITAL_DATE_FUTURE"
        )
    current_total = _ten_thousand_shares(
        capital.get("TOTAL_DOMESTIC_VOL"), field="CAPITAL_TOTAL_SHARES"
    )
    current_a_unlimited = _ten_thousand_shares(
        capital.get("A_UNLIMIT_VOL"), field="CAPITAL_A_UNLIMITED_SHARES"
    )
    current_a_limited = _ten_thousand_shares(
        capital.get("A_LIMIT_VOL"), field="CAPITAL_A_LIMITED_SHARES"
    )
    current_total_unlimited = _ten_thousand_shares(
        capital.get("TOTAL_UNLIMIT_VOL"),
        field="CAPITAL_TOTAL_UNLIMITED_SHARES",
    )
    if (
        current_total <= 0
        or current_a_unlimited + current_a_limited > current_total
        or current_total_unlimited > current_total
    ):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_CAPITAL_ARITHMETIC_INVALID"
        )
    event_ids: set[int] = set()
    share_events: list[dict[str, Any]] = []
    for row in event_rows:
        seq = _integer(row.get("seq"), field="EQUITY_EVENT_SEQUENCE", minimum=1)
        if seq in event_ids:
            raise SSEPublicDisclosureSourceError(
                "SSE_DISCLOSURE_EQUITY_EVENT_DUPLICATE"
            )
        event_ids.add(seq)
        event_date = _parse_iso_date(
            row.get("realDate"), field="EQUITY_EVENT_DATE"
        )
        if event_date > observed.date():
            raise SSEPublicDisclosureSourceError(
                "SSE_DISCLOSURE_EQUITY_EVENT_DATE_FUTURE"
            )
        reason_code = str(row.get("changeReason") or "").strip()
        reason = str(row.get("changeReasonDesc") or "").strip()
        if not reason_code or not reason:
            raise SSEPublicDisclosureSourceError(
                "SSE_DISCLOSURE_EQUITY_EVENT_REASON_MISSING"
            )
        share_events.append({
            "sequence": seq,
            "event_date": event_date.isoformat(),
            "reason_code": reason_code,
            "reason": reason,
            "a_shares": _ten_thousand_shares(
                row.get("AShares"), field="EQUITY_EVENT_A_SHARES"
            ),
            "b_shares": _ten_thousand_shares(
                row.get("BShares"), field="EQUITY_EVENT_B_SHARES"
            ),
            "total_shares": _ten_thousand_shares(
                row.get("totalShares"), field="EQUITY_EVENT_TOTAL_SHARES"
            ),
        })
    share_events.sort(key=lambda row: (row["event_date"], row["sequence"]), reverse=True)
    latest_event_total = share_events[0]["total_shares"]
    shares_reconciled = latest_event_total == current_total
    shares_reason = (
        ("OFFICIAL_CURRENT_CAPITAL_AND_EVENT_HISTORY_RECONCILED",)
        if shares_reconciled
        else (
            "OFFICIAL_CURRENT_CAPITAL_OBSERVED",
            "LATEST_EQUITY_EVENT_DOES_NOT_RECONCILE_CURRENT_CAPITAL",
            "RECENT_SHARE_EVENT_COMPLETION_NOT_PROVEN",
        )
    )

    page_help = dividend_doc.get("pageHelp")
    total_records = (
        int(page_help.get("total") or 0)
        if isinstance(page_help, Mapping) else len(dividend_rows)
    )
    if not dividend_rows or total_records != len(dividend_rows):
        raise SSEPublicDisclosureSourceError(
            "SSE_DISCLOSURE_DIVIDEND_COUNT_MISMATCH"
        )
    dividend_keys: set[tuple[str, str]] = set()
    dividends: list[dict[str, Any]] = []
    for row in dividend_rows:
        if (
            str(row.get("COMPANY_CODE") or "").strip() != company_code
            or str(row.get("A_STOCK_CODE") or "").strip() != company_code
        ):
            raise SSEPublicDisclosureSourceError(
                "SSE_DISCLOSURE_DIVIDEND_CODE_MISMATCH"
            )
        record_date = _parse_yyyymmdd(
            row.get("A_REG_DATE"), field="DIVIDEND_RECORD_DATE"
        )
        ex_date = _parse_yyyymmdd(
            row.get("A_DIV_DATE"), field="DIVIDEND_EX_DATE"
        )
        if record_date > ex_date or ex_date > observed.date():
            raise SSEPublicDisclosureSourceError(
                "SSE_DISCLOSURE_DIVIDEND_DATE_INVALID"
            )
        key = (record_date.isoformat(), ex_date.isoformat())
        if key in dividend_keys:
            raise SSEPublicDisclosureSourceError(
                "SSE_DISCLOSURE_DIVIDEND_DUPLICATE"
            )
        dividend_keys.add(key)
        per_share = _decimal_text(
            row.get("A_BEFR_TAX_DIV"), field="DIVIDEND_PER_SHARE"
        )
        if per_share is None or Decimal(per_share) <= 0:
            raise SSEPublicDisclosureSourceError(
                "SSE_DISCLOSURE_DIVIDEND_PER_SHARE_INVALID"
            )
        dividends.append({
            "record_date": record_date.isoformat(),
            "ex_dividend_date": ex_date.isoformat(),
            "registered_shares": _ten_thousand_shares(
                row.get("A_STOCK_VOL"), field="DIVIDEND_REGISTERED_SHARES"
            ),
            "cash_dividend_before_tax_cny_per_share": per_share,
        })
    dividends.sort(key=lambda row: row["ex_dividend_date"], reverse=True)
    latest_dividend_date = dividends[0]["ex_dividend_date"]
    observed_iso = observed.isoformat()
    authority = (
        "data_provider.sse_public_disclosure_source."
        "build_sse_stock_event_disclosure_bundle"
    )
    stock_observations = {
        "P_SHARES_EVENTS": _observation(
            dataset_id="P_SHARES_EVENTS",
            state="READY" if shares_reconciled else "PARTIAL",
            reason_codes=shares_reason,
            provider_route=(
                capital_request_url + " | " + equity_change_request_url
            ),
            response_bytes=(capital_response_json + b"\x00" + equity_change_response_json),
            observed_at=observed,
            completed_through=capital_date.isoformat(),
            units={
                "shares": "SHARE",
                "event_date": "ISO_DATE",
                "reason": "TEXT",
            },
            currency="NOT_APPLICABLE",
            adjustment_basis="NOT_APPLICABLE",
            revision=(
                "SSE_CAPITAL_DATE:" + capital_date.isoformat()
                if shares_reconciled else "UNKNOWN"
            ),
            vintage=(observed_iso if shares_reconciled else "UNKNOWN"),
            facts={
                "stock_code": company_code,
                "capital_trade_date": capital_date.isoformat(),
                "current_total_domestic_shares": current_total,
                "current_a_unrestricted_shares": current_a_unlimited,
                "current_a_restricted_shares": current_a_limited,
                "current_total_unrestricted_shares": current_total_unlimited,
                "event_count": len(share_events),
                "latest_event_date": share_events[0]["event_date"],
                "latest_event_total_shares": latest_event_total,
                "events": share_events,
            },
            producer_authority=authority,
        ),
        "P_DIVIDEND": _observation(
            dataset_id="P_DIVIDEND",
            state="READY",
            reason_codes=("OFFICIAL_DIVIDEND_HISTORY_OBSERVED",),
            provider_route=dividend_request_url,
            response_bytes=dividend_response_json,
            observed_at=observed,
            completed_through=latest_dividend_date,
            units={
                "cash_dividend": "CNY_PER_SHARE",
                "registered_shares": "SHARE",
                "date": "ISO_DATE",
            },
            currency="CNY",
            adjustment_basis="NOT_APPLICABLE",
            revision=(
                "SSE_DIVIDEND_LATEST_EX_DATE:" + latest_dividend_date
                + ";RECORDS:" + str(len(dividends))
            ),
            vintage=observed_iso,
            facts={
                "stock_code": company_code,
                "record_count": len(dividends),
                "latest_ex_dividend_date": latest_dividend_date,
                "latest_cash_dividend_before_tax_cny_per_share": (
                    dividends[0]["cash_dividend_before_tax_cny_per_share"]
                ),
                "records": dividends,
            },
            producer_authority=authority,
        ),
    }
    bundle = {
        "schema_version": "sse-stock-event-disclosure-bundle-v1",
        "provider_identity": PROVIDER_IDENTITY,
        "observed_at": observed_iso,
        "stock_code": company_code,
        "stock_observations": stock_observations,
        "storage_admitted": False,
        "training_admitted": False,
        "redistribution_admitted": False,
        "strategy_upgrade_authority": False,
    }
    bundle["bundle_hash"] = sha256_payload(bundle)
    return bundle
