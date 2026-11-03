# -*- coding: utf-8 -*-
"""Deterministic dated fundamental quality/growth evidence.

This is a thin DSA-owned Product evidence service, not a score, model, stock
picker or valuation substitute.  It consumes only Product-admitted financial
objects, preserves known-at/vintage identity, reports transparent accounting
ratios and has no strategy, veto, learning or independent action authority.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timezone
import math
from typing import Any

from src.services.data_object_admission import (
    DataObjectAdmissionError,
    validate_compiled_data_object_admission,
)
from src.services.pit_identity import sha256_payload


SCHEMA_VERSION = "fundamental-quality-growth-evidence-v1"
ALGORITHM_VERSION = "dated-growth-cash-conversion-leverage-v1"
REQUIRED_DATASET_IDS = (
    "P_FIN_VINTAGE",
    "P_EARNINGS_GROWTH",
    "P_CASH_FLOW",
    "P_BALANCE_SHEET",
)
FIELD_SOURCES = {
    "revenue_growth_yoy_pct": "P_EARNINGS_GROWTH",
    "net_profit_growth_yoy_pct": "P_EARNINGS_GROWTH",
    "gross_margin_pct": "P_EARNINGS_GROWTH",
    "operating_cash_flow": "P_CASH_FLOW",
    "net_income": "P_EARNINGS_GROWTH",
    "total_debt": "P_BALANCE_SHEET",
    "total_assets": "P_BALANCE_SHEET",
}
_CONFIG = {
    "algorithm_version": ALGORITHM_VERSION,
    "required_dataset_ids": REQUIRED_DATASET_IDS,
    "field_sources": FIELD_SOURCES,
    "composite_score": False,
    "strategy_upgrade_authority": False,
    "veto_override_authority": False,
    "learning_admitted": False,
}
CONFIG_HASH = sha256_payload(_CONFIG)


class FundamentalQualityError(ValueError):
    """Raised when a purported READY quality receipt is stale or escalated."""


def compile_fundamental_quality(
    payload: Mapping[str, Any] | None,
    *,
    as_of: date | datetime | str | None = None,
) -> dict[str, Any]:
    """Compile source-bound quality/growth facts without a composite score.

    Missing fields remain ``MISSING``; future/vintage/right/identity failures are
    ``NOT_ADMITTED``.  READY means the displayed accounting facts are lawful for
    this Product run only.  It does not imply strategy eligibility or quality
    superiority and cannot override any veto.
    """
    base = {
        "schema_version": SCHEMA_VERSION,
        "algorithm_version": ALGORITHM_VERSION,
        "config_hash": CONFIG_HASH,
        "effective_parameters": dict(_CONFIG),
        "status": "MISSING",
        "reason_codes": (),
        "asset_id": None,
        "asset_type": None,
        "currency": None,
        "financial_period_end": None,
        "published_at": None,
        "financial_version": None,
        "as_of": None,
        "data_admission_dataset_ids": (),
        "data_admission_hashes": (),
        "data_source_receipt_hashes": (),
        "field_lineage": None,
        "metrics": None,
        "summary_zh": "质量成长：数据不足，暂不判断。",
        "uncertainty_zh": (
            "财务期间、公告时点、来源权利或必要字段未完整准入；"
            "不补造质量分、增长结论或机构判断。"
        ),
        "product_admitted": False,
        "learning_admitted": False,
        "strategy_upgrade_authority": False,
        "veto_override_authority": False,
        "independent_action_authority": False,
        "receipt_hash": None,
    }
    if not isinstance(payload, Mapping):
        return {**base, "reason_codes": ("QUALITY_INPUT_MISSING",)}

    asset_id = _text(payload.get("asset_id"))
    asset_type = _text(payload.get("asset_type")).lower()
    currency = _text(payload.get("currency")).upper()
    financial_period_end = _date_text(payload.get("financial_period_end"))
    financial_version = _text(payload.get("financial_version"))
    published_at = _aware_datetime(payload.get("published_at"))
    research_as_of = _as_of_ceiling(as_of or payload.get("as_of"))
    reasons: list[str] = []
    if not asset_id:
        reasons.append("ASSET_ID_MISSING")
    if asset_type != "stock":
        reasons.append("QUALITY_ASSET_TYPE_NOT_STOCK")
    if not currency or currency == "UNKNOWN":
        reasons.append("QUALITY_CURRENCY_UNKNOWN")
    if not financial_period_end or not financial_version:
        reasons.append("FINANCIAL_PERIOD_OR_VERSION_MISSING")
    if published_at is None:
        reasons.append("PUBLISHED_AT_MISSING_OR_NAIVE")
    if research_as_of is None:
        reasons.append("QUALITY_ASOF_MISSING_OR_NAIVE")
    elif published_at is not None and published_at > research_as_of:
        reasons.append("FINANCIAL_INFORMATION_NOT_KNOWN_AT_ASOF")

    admissions = payload.get("data_admissions")
    normalized: list[dict[str, Any]] = []
    if not isinstance(admissions, Sequence) or isinstance(admissions, (str, bytes)):
        reasons.append("QUALITY_DATA_ADMISSION_MISSING")
    else:
        for item in admissions:
            if not isinstance(item, Mapping):
                reasons.append("QUALITY_DATA_ADMISSION_INVALID")
                continue
            try:
                normalized.append(
                    validate_compiled_data_object_admission(
                        item, as_of=research_as_of,
                    )
                )
            except DataObjectAdmissionError as exc:
                reasons.append(
                    "QUALITY_DATA_NOT_KNOWN_AT_ASOF"
                    if "OUTSIDE_RESEARCH_ASOF" in str(exc)
                    else "QUALITY_DATA_ADMISSION_INVALID"
                )
    admission_index = {
        _text(item.get("dataset_id")): item
        for item in normalized if _text(item.get("dataset_id"))
    }
    dataset_ids = tuple(sorted(admission_index))
    if len(dataset_ids) != len(normalized):
        reasons.append("QUALITY_DATASET_IDENTITY_INVALID")
    missing_datasets = [item for item in REQUIRED_DATASET_IDS if item not in admission_index]
    if missing_datasets:
        reasons.append("QUALITY_REQUIRED_DATASET_MISSING:" + ",".join(missing_datasets))
    if any(item.get("product_admitted") is not True for item in normalized):
        reasons.append("QUALITY_DATA_NOT_PRODUCT_ADMITTED")
    for item in normalized:
        item_currency = _text(item.get("currency")).upper()
        if item_currency not in {currency, "NOT_APPLICABLE"}:
            reasons.append("QUALITY_DATA_CURRENCY_MISMATCH")
        if len(_text(item.get("admission_hash"))) != 64:
            reasons.append("QUALITY_DATA_ADMISSION_IDENTITY_MISSING")
        if len(_text(item.get("receipt_hash"))) != 64:
            reasons.append("QUALITY_DATA_RECEIPT_IDENTITY_MISSING")
    primary = admission_index.get("P_FIN_VINTAGE")
    if isinstance(primary, Mapping):
        if _date_text(primary.get("completed_through")) != financial_period_end:
            reasons.append("QUALITY_PRIMARY_PERIOD_MISMATCH")

    common = {
        **base,
        "asset_id": asset_id or None,
        "asset_type": asset_type or None,
        "currency": currency or None,
        "financial_period_end": financial_period_end or None,
        "published_at": published_at.isoformat() if published_at else None,
        "financial_version": financial_version or None,
        "as_of": research_as_of.isoformat() if research_as_of else None,
        "data_admission_dataset_ids": dataset_ids,
        "data_admission_hashes": tuple(sorted(
            _text(item.get("admission_hash")) for item in normalized
        )),
        "data_source_receipt_hashes": tuple(sorted(
            _text(item.get("receipt_hash")) for item in normalized
        )),
    }
    if reasons:
        not_admitted_markers = (
            "NOT_KNOWN_AT_ASOF", "NOT_PRODUCT_ADMITTED", "ADMISSION_INVALID",
            "CURRENCY_MISMATCH", "PERIOD_MISMATCH", "ASSET_TYPE_NOT_STOCK",
        )
        status = "NOT_ADMITTED" if any(
            marker in reason for reason in reasons for marker in not_admitted_markers
        ) else "MISSING"
        return {
            **common,
            "status": status,
            "reason_codes": tuple(dict.fromkeys(reasons)),
        }

    metrics = payload.get("metrics")
    metric_sources = payload.get("metric_sources")
    if not isinstance(metrics, Mapping):
        return {**common, "reason_codes": ("QUALITY_METRICS_MISSING",)}
    if not isinstance(metric_sources, Mapping):
        return {**common, "reason_codes": ("QUALITY_FIELD_LINEAGE_MISSING",)}
    normalized_sources = {
        _text(key): _text(value) for key, value in metric_sources.items()
    }
    if set(normalized_sources) != set(metrics):
        return {
            **common,
            "status": "NOT_ADMITTED",
            "reason_codes": ("QUALITY_FIELD_LINEAGE_KEYS_MISMATCH",),
        }
    expected_sources = {key: FIELD_SOURCES[key] for key in metrics if key in FIELD_SOURCES}
    if len(expected_sources) != len(metrics) or normalized_sources != expected_sources:
        return {
            **common,
            "status": "NOT_ADMITTED",
            "reason_codes": ("QUALITY_FIELD_LINEAGE_MISMATCH",),
            "field_lineage": normalized_sources,
        }
    if any(dataset_id not in admission_index for dataset_id in normalized_sources.values()):
        return {
            **common,
            "status": "NOT_ADMITTED",
            "reason_codes": ("QUALITY_FIELD_SOURCE_MISSING",),
            "field_lineage": normalized_sources,
        }

    missing_fields = [key for key in FIELD_SOURCES if key not in metrics]
    if missing_fields:
        return {
            **common,
            "reason_codes": ("QUALITY_REQUIRED_METRIC_MISSING:" + ",".join(missing_fields),),
            "field_lineage": normalized_sources,
        }
    try:
        revenue_growth = _finite(metrics["revenue_growth_yoy_pct"])
        profit_growth = _finite(metrics["net_profit_growth_yoy_pct"])
        gross_margin = _finite(metrics["gross_margin_pct"])
        operating_cash_flow = _finite(metrics["operating_cash_flow"])
        net_income = _finite(metrics["net_income"])
        total_debt = _nonnegative(metrics["total_debt"])
        total_assets = _positive(metrics["total_assets"])
    except FundamentalQualityError as exc:
        return {
            **common,
            "reason_codes": (str(exc),),
            "field_lineage": normalized_sources,
        }
    cash_conversion = (
        operating_cash_flow / net_income if net_income > 0 else None
    )
    debt_to_assets = total_debt / total_assets
    growth_direction = (
        "BOTH_POSITIVE" if revenue_growth > 0 and profit_growth > 0
        else "BOTH_NEGATIVE" if revenue_growth < 0 and profit_growth < 0
        else "MIXED"
    )
    cash_conversion_state = (
        "NOT_COMPARABLE_NON_POSITIVE_NET_INCOME"
        if cash_conversion is None
        else "CASH_COVERS_EARNINGS" if cash_conversion >= 1.0
        else "POSITIVE_BELOW_EARNINGS" if cash_conversion >= 0
        else "NEGATIVE_OPERATING_CASH_FLOW"
    )
    normalized_metrics = {
        "revenue_growth_yoy_pct": _round(revenue_growth),
        "net_profit_growth_yoy_pct": _round(profit_growth),
        "gross_margin_pct": _round(gross_margin),
        "operating_cash_flow": _round(operating_cash_flow),
        "net_income": _round(net_income),
        "cash_conversion_ratio": _round(cash_conversion) if cash_conversion is not None else None,
        "total_debt": _round(total_debt),
        "total_assets": _round(total_assets),
        "debt_to_assets_ratio": _round(debt_to_assets),
        "growth_direction": growth_direction,
        "cash_conversion_state": cash_conversion_state,
    }
    cash_text = (
        "不可比（净利润非正）"
        if cash_conversion is None else f"{cash_conversion:.2f}倍"
    )
    summary = (
        "质量成长："
        f"收入同比 {revenue_growth:.2f}%，净利润同比 {profit_growth:.2f}%，"
        f"毛利率 {gross_margin:.2f}%，经营现金流/净利润 {cash_text}，"
        f"债务/资产 {debt_to_assets:.2%}。"
        "以上仅是同一财务版本下的可核事实，不构成综合质量分、策略升级或否决覆盖。"
    )
    document = {
        **common,
        "status": "READY",
        "reason_codes": (),
        "field_lineage": normalized_sources,
        "metrics": normalized_metrics,
        "summary_zh": summary,
        "uncertainty_zh": (
            "质量成长材料仅覆盖本次已列财务字段；行业口径、会计政策、"
            "一次性项目、资本开支质量和更长历史稳定性仍需单独核验。"
        ),
        "product_admitted": True,
        "learning_admitted": False,
    }
    if "accounting_ratio_input" in payload:
        ratios = _compile_accounting_ratios(
            payload["accounting_ratio_input"], common, admission_index,
        )
        document["accounting_ratios"] = ratios
        document["summary_zh"] += " " + ratios["summary_zh"]
    document["receipt_hash"] = sha256_payload({
        key: value for key, value in document.items() if key != "receipt_hash"
    })
    return document


_ACCOUNTING_RATIO_FIELDS = {
    "earnings_common": ("P_EARNINGS_GROWTH", "MONEY"),
    "diluted_earnings_common": ("P_EARNINGS_GROWTH", "MONEY"),
    "weighted_basic_shares": ("P_SHARES_EVENTS", "SHARE"),
    "weighted_diluted_shares": ("P_SHARES_EVENTS", "SHARE"),
    "ending_shares": ("P_SHARES_EVENTS", "SHARE"),
    "equity_begin": ("P_BALANCE_SHEET", "MONEY"),
    "equity_end": ("P_BALANCE_SHEET", "MONEY"),
    "price": ("P_DAILY", "PER_SHARE"),
    "ebit": ("P_EARNINGS_GROWTH", "MONEY"),
    "effective_tax_rate": ("P_EARNINGS_GROWTH", "FRACTION"),
    "invested_capital_begin": ("P_BALANCE_SHEET", "MONEY"),
    "invested_capital_end": ("P_BALANCE_SHEET", "MONEY"),
}
_ROIC_FIELDS = frozenset({
    "ebit", "effective_tax_rate", "invested_capital_begin", "invested_capital_end",
})


def _compile_accounting_ratios(raw, common, admission_index):
    """Optional field-level TTM arithmetic inside the existing Quality consumer.

    No provider ratio, market multiple or score is accepted as a substitute for
    the explicitly dated numerator and denominator. Does not grant data rights.
    """
    base = {
        "schema_version": "dated-accounting-ratios-v1", "status": "NOT_ADMITTED",
        "reason_codes": (), "values": {}, "value_states": {},
        "summary_zh": "EPS/ROE/PE/PB/ROIC：输入口径或来源不足，不补值。",
        "strategy_upgrade_authority": False, "learning_admitted": False,
    }

    def reject(reason, status="NOT_ADMITTED"):
        return {**base, "status": status, "reason_codes": (reason,)}

    if not isinstance(raw, Mapping) or raw.get("schema_version") != "dated-accounting-ratio-input-v1":
        return reject("RATIO_INPUT_SCHEMA_INVALID")
    for key in ("asset_id", "financial_version", "financial_period_end"):
        if raw.get(key) != common.get(key):
            return reject("RATIO_FINANCIAL_IDENTITY_MISMATCH:" + key)
    if raw.get("earnings_basis") != "TTM":
        return reject("RATIO_EARNINGS_BASIS_NOT_TTM")
    metrics, sources, units = (raw.get(key) for key in ("metrics", "field_sources", "field_units"))
    if not all(isinstance(item, Mapping) for item in (metrics, sources, units)):
        return reject("RATIO_FIELD_LINEAGE_MISSING")
    if set(metrics) != set(sources) or set(metrics) != set(units) or set(metrics) - set(_ACCOUNTING_RATIO_FIELDS):
        return reject("RATIO_FIELD_KEYS_MISMATCH")
    if set(_ACCOUNTING_RATIO_FIELDS) - _ROIC_FIELDS - set(metrics):
        return reject("RATIO_REQUIRED_FIELD_MISSING", "MISSING")
    currency = common["currency"]
    unit_names = {"MONEY": currency, "PER_SHARE": currency + "_PER_SHARE"}
    for key in metrics:
        dataset, unit = _ACCOUNTING_RATIO_FIELDS[key]
        if sources[key] != dataset or units[key] != unit_names.get(unit, unit):
            return reject("RATIO_FIELD_LINEAGE_OR_UNITS_MISMATCH:" + key)
        if dataset not in admission_index or admission_index[dataset].get("product_admitted") is not True:
            return reject("RATIO_FIELD_SOURCE_NOT_ADMITTED:" + dataset)
        if dataset != "P_DAILY" and _date_text(admission_index[dataset].get("completed_through")) != common["financial_period_end"]:
            return reject("RATIO_SOURCE_PERIOD_MISMATCH:" + dataset)

    price_source = admission_index["P_DAILY"]
    if str(price_source.get("adjustment_basis", "")).upper() != "RAW":
        return reject("RATIO_PRICE_BASIS_NOT_RAW")
    price_date = _date_text(raw.get("price_date"))
    research_time = _as_of_ceiling(common["as_of"])
    if (
        not price_date or price_date != _date_text(price_source.get("completed_through"))
        or research_time is None or price_date > research_time.date().isoformat()
    ):
        return reject("RATIO_PRICE_DATE_MISMATCH_OR_FUTURE")
    from src.services.business_valuation_service import (
        BusinessValuationError, validate_industry_valuation_route,
    )
    try:
        plan = validate_industry_valuation_route(raw.get("industry_route_plan"))
    except (BusinessValuationError, TypeError):
        return reject("RATIO_INDUSTRY_PLAN_INVALID")
    if plan.get("status") != "READY" or plan.get("asset_id") != common["asset_id"]:
        return reject("RATIO_INDUSTRY_PLAN_NOT_BOUND")
    plan_time = _aware_datetime(plan.get("as_of"))
    if plan_time is None or plan_time > research_time:
        return reject("RATIO_INDUSTRY_PLAN_FUTURE")
    is_financial = plan.get("route_id") in {"BANK", "INSURANCE"}
    applicability = "FINANCIAL" if is_financial else "NON_FINANCIAL"
    if raw.get("industry_applicability") != applicability:
        return reject("RATIO_INDUSTRY_APPLICABILITY_MISMATCH")
    try:
        numbers = {key: _finite(value) for key, value in metrics.items()}
        shares_basic = _positive(numbers["weighted_basic_shares"])
        shares_diluted = _positive(numbers["weighted_diluted_shares"])
        shares_end = _positive(numbers["ending_shares"])
        price = _positive(numbers["price"])
    except FundamentalQualityError:
        return reject("RATIO_NUMERATOR_OR_DENOMINATOR_INVALID")
    basic = numbers["earnings_common"] / shares_basic
    diluted = numbers["diluted_earnings_common"] / shares_diluted
    begin, end = numbers["equity_begin"], numbers["equity_end"]
    book = end / shares_end
    states = {}
    values = {
        "eps_basic": basic, "eps_diluted": diluted,
        "roe_fraction": numbers["earnings_common"] / ((begin + end) / 2) if min(begin, end) > 0 else None,
        "book_value_per_share": book,
        "pe_basic_ttm": price / basic if basic > 0 else None,
        "pe_diluted_ttm": price / diluted if diluted > 0 else None,
        "pb": price / book if book > 0 else None,
        "roic_nopat_fraction": None,
    }
    for key in values:
        states[key] = "READY" if values[key] is not None else "NOT_COMPARABLE_NON_POSITIVE_EQUITY"
    for key in ("pe_basic_ttm", "pe_diluted_ttm"):
        if values[key] is None:
            states[key] = "NOT_COMPARABLE_NON_POSITIVE_EARNINGS"
    # Reported diluted EPS must not become higher than basic EPS by including
    # antidilutive instruments. Never silently substitute the basic denominator.
    if diluted > basic + 1e-12:
        values["eps_diluted"] = values["pe_diluted_ttm"] = None
        states["eps_diluted"] = states["pe_diluted_ttm"] = "NOT_COMPARABLE_ANTIDILUTIVE_INPUT"
    if is_financial:
        states["roic_nopat_fraction"] = "NOT_APPLICABLE_FINANCIAL_INDUSTRY"
    elif not _ROIC_FIELDS <= set(numbers):
        states["roic_nopat_fraction"] = "MISSING_ROIC_INPUTS"
    else:
        tax = numbers["effective_tax_rate"]
        capital_start, capital_end = numbers["invested_capital_begin"], numbers["invested_capital_end"]
        if not 0 <= tax <= 1 or min(capital_start, capital_end) <= 0:
            return reject("RATIO_ROIC_TAX_OR_CAPITAL_INVALID")
        values["roic_nopat_fraction"] = numbers["ebit"] * (1 - tax) / ((capital_start + capital_end) / 2)
        states["roic_nopat_fraction"] = "READY"
    values = {key: _round(value) if value is not None else None for key, value in values.items()}
    status = "PARTIAL" if any(value.startswith(("MISSING", "NOT_COMPARABLE")) for value in states.values()) else "READY"
    display = lambda key: str(values[key]) if values[key] is not None else states[key]
    document = {
        **base, "status": status, "values": values, "value_states": states,
        "earnings_basis": "TTM", "price_date": price_date,
        "financial_version": common["financial_version"], "financial_period_end": common["financial_period_end"],
        "asset_id": common["asset_id"], "as_of": common["as_of"],
        "industry_route_plan_hash": plan.get("route_plan_hash"),
        "input_values": numbers, "field_sources": dict(sources), "field_units": dict(units),
        "source_admission_hashes": {key: admission_index[key]["admission_hash"] for key in sorted(set(sources.values()))},
        "summary_zh": (
            f"TTM EPS(基本/稀释) {display('eps_basic')}/{display('eps_diluted')}；"
            f"ROE {display('roe_fraction')}（比例）；PE(基本/稀释) {display('pe_basic_ttm')}/{display('pe_diluted_ttm')}；"
            f"PB {display('pb')}；ROIC(NOPAT/平均投入资本) {display('roic_nopat_fraction')}。"
            "比率不是合理倍数或收益预测；负利润/负权益不解释为便宜。"
        ),
    }
    document["receipt_hash"] = sha256_payload(document)
    return document


def validate_fundamental_quality(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != SCHEMA_VERSION:
        raise FundamentalQualityError("FUNDAMENTAL_QUALITY_SCHEMA_INVALID")
    result = dict(value)
    if result.get("status") != "READY":
        return result
    receipt_hash = _text(result.get("receipt_hash"))
    if len(receipt_hash) != 64:
        raise FundamentalQualityError("FUNDAMENTAL_QUALITY_RECEIPT_MISSING")
    expected = sha256_payload({key: result[key] for key in result if key != "receipt_hash"})
    if receipt_hash != expected:
        raise FundamentalQualityError("FUNDAMENTAL_QUALITY_RECEIPT_STALE")
    if result.get("product_admitted") is not True:
        raise FundamentalQualityError("FUNDAMENTAL_QUALITY_PRODUCT_NOT_ADMITTED")
    if any(result.get(key) is not False for key in (
        "learning_admitted", "strategy_upgrade_authority",
        "veto_override_authority", "independent_action_authority",
    )):
        raise FundamentalQualityError("FUNDAMENTAL_QUALITY_AUTHORITY_ESCALATION")
    return result


def _text(value: Any) -> str:
    return str(value or "").strip()


def _finite(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FundamentalQualityError("QUALITY_METRIC_NOT_NUMERIC") from exc
    if not math.isfinite(number):
        raise FundamentalQualityError("QUALITY_METRIC_NOT_FINITE")
    return number


def _positive(value: Any) -> float:
    number = _finite(value)
    if number <= 0:
        raise FundamentalQualityError("QUALITY_METRIC_MUST_BE_POSITIVE")
    return number


def _nonnegative(value: Any) -> float:
    number = _finite(value)
    if number < 0:
        raise FundamentalQualityError("QUALITY_METRIC_MUST_BE_NONNEGATIVE")
    return number


def _round(value: float) -> float:
    return round(float(value), 6)


def _date_text(value: Any) -> str:
    text = _text(value)
    if not text:
        return ""
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return ""


def _aware_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        result = value
    else:
        text = _text(value)
        if not text:
            return None
        try:
            result = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if result.tzinfo is None or result.utcoffset() is None:
        return None
    return result.astimezone(timezone.utc)


def _as_of_ceiling(value: date | datetime | str | None) -> datetime | None:
    if isinstance(value, datetime):
        return _aware_datetime(value)
    if isinstance(value, date):
        return datetime.combine(value, time.max, tzinfo=timezone.utc)
    text = _text(value)
    if not text:
        return None
    parsed = _aware_datetime(text)
    if parsed is not None:
        return parsed
    try:
        return datetime.combine(date.fromisoformat(text[:10]), time.max, tzinfo=timezone.utc)
    except ValueError:
        return None
