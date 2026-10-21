# -*- coding: utf-8 -*-
"""Deterministic business-routed valuation without action authority.

The accepted nine-route contract is implemented here as one thin DSA service.
Every route requires explicit dated inputs, assumptions and Product-admitted
source receipts.  There are no default WACC/growth/multiples, no universal PE,
no LLM-generated values and no strategy/veto override.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timezone
import math
from typing import Any, Callable

from src.services.pit_identity import sha256_payload
from src.services.data_object_admission import (
    DataObjectAdmissionError,
    validate_compiled_data_object_admission,
)


SCHEMA_VERSION = "business-valuation-evidence-v1"
VALUATION_ROUTE_IDS = (
    "MATURE_INDUSTRIAL_CONSUMER",
    "HIGH_GROWTH_TECH",
    "BANK",
    "INSURANCE",
    "CYCLICAL_RESOURCE",
    "UTILITY_INFRASTRUCTURE",
    "REIT",
    "LOSS_MAKING_EARLY_GROWTH",
    "ETF",
)
_ROUTE_LABELS = {
    "MATURE_INDUSTRIAL_CONSUMER": "稳定工业/制造/成熟消费",
    "HIGH_GROWTH_TECH": "高成长科技/软件/半导体",
    "BANK": "银行",
    "INSURANCE": "保险",
    "CYCLICAL_RESOURCE": "周期/资源",
    "UTILITY_INFRASTRUCTURE": "公用事业/基础设施",
    "REIT": "REIT/类REIT",
    "LOSS_MAKING_EARLY_GROWTH": "亏损/早期成长",
    "ETF": "ETF",
}

# Route-specific source obligations keep a product-admitted receipt from being
# reused as a generic valuation permission. ``required_any`` expresses the
# bounded price observation alternatives; every other object is mandatory.
VALUATION_ROUTE_DATA_REQUIREMENTS = {
    "MATURE_INDUSTRIAL_CONSUMER": {
        "required_all": ("P_FIN_VINTAGE", "P_EARNINGS_GROWTH", "P_VAL_RATIO"),
        "required_any": (("P_DAILY", "P_QUOTE_LIQUID"),),
    },
    "HIGH_GROWTH_TECH": {
        "required_all": (
            "P_FIN_VINTAGE", "P_EARNINGS_GROWTH", "P_BALANCE_SHEET",
            "P_SHARES_EVENTS", "P_VAL_RATIO",
        ),
        "required_any": (("P_DAILY", "P_QUOTE_LIQUID"),),
    },
    "BANK": {
        "required_all": (
            "P_FIN_VINTAGE", "P_BALANCE_SHEET", "P_SHARES_EVENTS", "P_VAL_RATIO",
        ),
        "required_any": (("P_DAILY", "P_QUOTE_LIQUID"),),
    },
    "INSURANCE": {
        "required_all": (
            "P_FIN_VINTAGE", "P_BALANCE_SHEET", "P_SHARES_EVENTS", "P_VAL_RATIO",
        ),
        "required_any": (("P_DAILY", "P_QUOTE_LIQUID"),),
    },
    "CYCLICAL_RESOURCE": {
        "required_all": (
            "P_FIN_VINTAGE", "P_EARNINGS_GROWTH", "P_BALANCE_SHEET",
            "P_SHARES_EVENTS", "P_VAL_RATIO",
        ),
        "required_any": (("P_DAILY", "P_QUOTE_LIQUID"),),
    },
    "UTILITY_INFRASTRUCTURE": {
        "required_all": (
            "P_FIN_VINTAGE", "P_DIVIDEND", "P_EARNINGS_GROWTH",
            "M_CN_BOND", "M_ERP_VAL",
        ),
        "required_any": (("P_DAILY", "P_QUOTE_LIQUID"),),
    },
    "REIT": {
        "required_all": (
            "P_FIN_VINTAGE", "P_CASH_FLOW", "P_SHARES_EVENTS", "P_VAL_RATIO",
        ),
        "required_any": (("P_DAILY", "P_QUOTE_LIQUID"),),
    },
    "LOSS_MAKING_EARLY_GROWTH": {
        "required_all": (
            "P_FIN_VINTAGE", "P_EARNINGS_GROWTH", "P_BALANCE_SHEET",
            "P_SHARES_EVENTS", "P_VAL_RATIO",
        ),
        "required_any": (("P_DAILY", "P_QUOTE_LIQUID"),),
    },
    "ETF": {
        "required_all": ("E_NAV_IOPV", "E_PREMIUM", "E_UNDERLYING_VAL"),
        "required_any": (("E_PRICE_1D", "E_PREMIUM"),),
    },
}

# Every numeric input and assumption must name the admitted data object that
# supplied it.  This is a field-lineage contract, not a formula default.
VALUATION_ROUTE_FIELD_SOURCES = {
    "MATURE_INDUSTRIAL_CONSUMER": {
        "inputs": {"normalized_eps": "P_EARNINGS_GROWTH"},
        "assumptions": {"pe_low": "P_VAL_RATIO", "pe_high": "P_VAL_RATIO"},
    },
    "HIGH_GROWTH_TECH": {
        "inputs": {
            "revenue": "P_EARNINGS_GROWTH", "net_debt": "P_BALANCE_SHEET",
            "diluted_shares": "P_SHARES_EVENTS",
        },
        "assumptions": {
            "ev_sales_low": "P_VAL_RATIO", "ev_sales_high": "P_VAL_RATIO",
        },
    },
    "BANK": {
        "inputs": {"book_value_per_share": "P_BALANCE_SHEET"},
        "assumptions": {"pb_low": "P_VAL_RATIO", "pb_high": "P_VAL_RATIO"},
    },
    "INSURANCE": {
        "inputs": {"embedded_value_per_share": "P_BALANCE_SHEET"},
        "assumptions": {"pev_low": "P_VAL_RATIO", "pev_high": "P_VAL_RATIO"},
    },
    "CYCLICAL_RESOURCE": {
        "inputs": {
            "normalized_ebitda": "P_EARNINGS_GROWTH", "net_debt": "P_BALANCE_SHEET",
            "diluted_shares": "P_SHARES_EVENTS",
        },
        "assumptions": {
            "ev_ebitda_low": "P_VAL_RATIO", "ev_ebitda_high": "P_VAL_RATIO",
        },
    },
    "UTILITY_INFRASTRUCTURE": {
        "inputs": {"next_dividend_per_share": "P_DIVIDEND"},
        "assumptions": {
            "cost_of_equity_low": "M_ERP_VAL", "cost_of_equity_high": "M_ERP_VAL",
            "growth_low": "P_EARNINGS_GROWTH", "growth_high": "P_EARNINGS_GROWTH",
        },
    },
    "REIT": {
        "inputs": {"affo_per_share": "P_CASH_FLOW"},
        "assumptions": {"paffo_low": "P_VAL_RATIO", "paffo_high": "P_VAL_RATIO"},
    },
    "LOSS_MAKING_EARLY_GROWTH": {
        "inputs": {
            "revenue": "P_EARNINGS_GROWTH", "net_debt": "P_BALANCE_SHEET",
            "diluted_shares": "P_SHARES_EVENTS",
        },
        "assumptions": {
            "ev_sales_low": "P_VAL_RATIO", "ev_sales_high": "P_VAL_RATIO",
        },
    },
    "ETF": {
        "inputs": {"nav_per_share": "E_NAV_IOPV"},
        "assumptions": {
            "price_to_nav_low": "E_UNDERLYING_VAL",
            "price_to_nav_high": "E_UNDERLYING_VAL",
        },
    },
}


class BusinessValuationError(ValueError):
    """Raised when a purported READY valuation is malformed or stale."""


def compile_business_valuation(
    payload: Mapping[str, Any] | None,
    *,
    as_of: date | datetime | str | None = None,
) -> dict[str, Any]:
    """Compile one explicit route into a bounded fair-value range.

    Missing inputs produce MISSING; unavailable Product rights or future data
    produce NOT_ADMITTED.  A READY result remains descriptive Product material
    only and cannot alter the canonical action or neutralize a veto.
    """
    base = {
        "schema_version": SCHEMA_VERSION,
        "status": "MISSING",
        "reason_codes": (),
        "route_id": None,
        "route_label": None,
        "asset_id": None,
        "asset_type": None,
        "currency": None,
        "methods": (),
        "route_data_requirements": None,
        "field_lineage": None,
        "fair_value": None,
        "current_price": None,
        "price_position": "UNKNOWN",
        "summary_zh": "估值：数据不足，暂不判断高低。",
        "uncertainty_zh": "估值输入、方法或权利未完整准入，不补造区间。",
        "product_admitted": False,
        "learning_admitted": False,
        "strategy_upgrade_authority": False,
        "veto_override_authority": False,
        "independent_action_authority": False,
        "receipt_hash": None,
    }
    if not isinstance(payload, Mapping):
        return {**base, "reason_codes": ("VALUATION_INPUT_MISSING",)}
    route_id = _text(payload.get("route_id")).upper()
    if route_id not in VALUATION_ROUTE_IDS:
        return {
            **base,
            "route_id": route_id or None,
            "reason_codes": ("VALUATION_ROUTE_NOT_ADMITTED",),
        }
    asset_id = _text(payload.get("asset_id"))
    asset_type = _text(payload.get("asset_type")).lower()
    currency = _text(payload.get("currency")).upper()
    current_price = _positive(payload.get("current_price"))
    financial_period_end = _date_text(payload.get("financial_period_end"))
    financial_version = _text(payload.get("financial_version"))
    published_at = _aware_datetime(payload.get("published_at"))
    research_as_of = _as_of_ceiling(as_of or payload.get("as_of"))
    reasons: list[str] = []
    if not asset_id:
        reasons.append("ASSET_ID_MISSING")
    expected_asset_type = "etf" if route_id == "ETF" else "stock"
    if asset_type != expected_asset_type:
        reasons.append("ASSET_TYPE_ROUTE_MISMATCH")
    if not currency or currency == "UNKNOWN":
        reasons.append("VALUATION_CURRENCY_UNKNOWN")
    if current_price is None:
        reasons.append("CURRENT_PRICE_MISSING")
    if not financial_period_end or not financial_version:
        reasons.append("FINANCIAL_PERIOD_OR_VERSION_MISSING")
    if published_at is None:
        reasons.append("PUBLISHED_AT_MISSING_OR_NAIVE")
    if research_as_of is None:
        reasons.append("VALUATION_ASOF_MISSING_OR_NAIVE")
    elif published_at is not None and published_at > research_as_of:
        reasons.append("FINANCIAL_INFORMATION_NOT_KNOWN_AT_ASOF")
    admissions = payload.get("data_admissions")
    if not isinstance(admissions, Sequence) or isinstance(admissions, (str, bytes)) or not admissions:
        reasons.append("VALUATION_DATA_ADMISSION_MISSING")
        normalized_admissions: list[dict[str, Any]] = []
    else:
        normalized_admissions = []
        for item in admissions:
            if not isinstance(item, Mapping):
                reasons.append("VALUATION_DATA_ADMISSION_INVALID")
                continue
            try:
                normalized_admissions.append(
                    validate_compiled_data_object_admission(
                        item, as_of=research_as_of,
                    )
                )
            except DataObjectAdmissionError as exc:
                reasons.append(
                    "VALUATION_DATA_NOT_KNOWN_AT_ASOF"
                    if "OUTSIDE_RESEARCH_ASOF" in str(exc)
                    else "VALUATION_DATA_ADMISSION_INVALID"
                )
        dataset_ids = [_text(item.get("dataset_id")) for item in normalized_admissions]
        if any(not dataset_id for dataset_id in dataset_ids) or len(set(dataset_ids)) != len(dataset_ids):
            reasons.append("VALUATION_DATASET_IDENTITY_INVALID")
        if any(item.get("product_admitted") is not True for item in normalized_admissions):
            reasons.append("VALUATION_DATA_NOT_PRODUCT_ADMITTED")
        if any(len(_text(item.get("admission_hash"))) != 64 for item in normalized_admissions):
            reasons.append("VALUATION_DATA_ADMISSION_IDENTITY_MISSING")
        if any(len(_text(item.get("receipt_hash"))) != 64 for item in normalized_admissions):
            reasons.append("VALUATION_DATA_RECEIPT_IDENTITY_MISSING")
        for item in normalized_admissions:
            item_currency = _text(item.get("currency")).upper()
            if item_currency not in {currency, "NOT_APPLICABLE"}:
                reasons.append("VALUATION_DATA_CURRENCY_MISMATCH")
    common = {
        **base,
        "route_id": route_id,
        "route_label": _ROUTE_LABELS[route_id],
        "asset_id": asset_id or None,
        "asset_type": asset_type or None,
        "currency": currency or None,
        "current_price": current_price,
        "financial_period_end": financial_period_end or None,
        "published_at": published_at.isoformat() if published_at else None,
        "financial_version": financial_version or None,
        "as_of": research_as_of.isoformat() if research_as_of else None,
        "data_admission_hashes": tuple(
            sorted(_text(item.get("admission_hash")) for item in normalized_admissions)
        ),
        "data_source_receipt_hashes": tuple(
            sorted(_text(item.get("receipt_hash")) for item in normalized_admissions)
        ),
        "data_admission_dataset_ids": tuple(
            sorted(_text(item.get("dataset_id")) for item in normalized_admissions)
        ),
        "route_data_requirements": VALUATION_ROUTE_DATA_REQUIREMENTS[route_id],
    }
    if reasons:
        status = "NOT_ADMITTED" if any(
            reason in reasons
            for reason in (
                "FINANCIAL_INFORMATION_NOT_KNOWN_AT_ASOF",
                "VALUATION_DATA_NOT_KNOWN_AT_ASOF",
                "VALUATION_DATA_NOT_PRODUCT_ADMITTED",
                "VALUATION_DATA_ADMISSION_INVALID",
                "VALUATION_DATASET_IDENTITY_INVALID",
                "VALUATION_DATA_ADMISSION_IDENTITY_MISSING",
                "VALUATION_DATA_RECEIPT_IDENTITY_MISSING",
                "VALUATION_DATA_CURRENCY_MISMATCH",
                "ASSET_TYPE_ROUTE_MISMATCH",
            )
        ) else "MISSING"
        return {**common, "status": status, "reason_codes": tuple(dict.fromkeys(reasons))}
    inputs = payload.get("inputs")
    assumptions = payload.get("assumptions")
    input_sources = payload.get("input_sources")
    assumption_sources = payload.get("assumption_sources")
    if not isinstance(inputs, Mapping) or not isinstance(assumptions, Mapping):
        return {
            **common,
            "reason_codes": ("VALUATION_INPUTS_OR_ASSUMPTIONS_MISSING",),
        }
    if not isinstance(input_sources, Mapping) or not isinstance(assumption_sources, Mapping):
        return {
            **common,
            "reason_codes": ("VALUATION_FIELD_LINEAGE_MISSING",),
        }
    admission_index = {
        _text(item.get("dataset_id")): item for item in normalized_admissions
        if _text(item.get("dataset_id"))
    }
    requirement = VALUATION_ROUTE_DATA_REQUIREMENTS[route_id]
    missing_required = [
        dataset_id for dataset_id in requirement["required_all"]
        if dataset_id not in admission_index
    ]
    missing_groups = [
        group for group in requirement["required_any"]
        if not any(dataset_id in admission_index for dataset_id in group)
    ]
    expected_lineage = VALUATION_ROUTE_FIELD_SOURCES[route_id]
    lineage_reasons: list[str] = []
    if missing_required:
        lineage_reasons.append(
            "VALUATION_REQUIRED_DATASET_MISSING:" + ",".join(missing_required)
        )
    if missing_groups:
        lineage_reasons.extend(
            "VALUATION_REQUIRED_DATASET_GROUP_MISSING:" + "|".join(group)
            for group in missing_groups
        )
    normalized_input_sources = {
        _text(key): _text(value) for key, value in input_sources.items()
    }
    normalized_assumption_sources = {
        _text(key): _text(value) for key, value in assumption_sources.items()
    }
    if set(normalized_input_sources) != set(inputs):
        lineage_reasons.append("VALUATION_INPUT_LINEAGE_KEYS_MISMATCH")
    if set(normalized_assumption_sources) != set(assumptions):
        lineage_reasons.append("VALUATION_ASSUMPTION_LINEAGE_KEYS_MISMATCH")
    expected_input_sources = {
        key: value for key, value in expected_lineage["inputs"].items()
        if key in inputs
    }
    expected_assumption_sources = {
        key: value for key, value in expected_lineage["assumptions"].items()
        if key in assumptions
    }
    if normalized_input_sources != expected_input_sources:
        lineage_reasons.append("VALUATION_INPUT_LINEAGE_ROUTE_MISMATCH")
    if normalized_assumption_sources != expected_assumption_sources:
        lineage_reasons.append("VALUATION_ASSUMPTION_LINEAGE_ROUTE_MISMATCH")
    for dataset_id in (*normalized_input_sources.values(), *normalized_assumption_sources.values()):
        if dataset_id not in admission_index:
            lineage_reasons.append("VALUATION_FIELD_SOURCE_MISSING:" + dataset_id)
    primary_dataset_id = "E_NAV_IOPV" if route_id == "ETF" else "P_FIN_VINTAGE"
    primary = admission_index.get(primary_dataset_id)
    if isinstance(primary, Mapping) and _date_text(primary.get("completed_through")) != financial_period_end:
        lineage_reasons.append("VALUATION_PRIMARY_PERIOD_MISMATCH")
    if lineage_reasons:
        return {
            **common,
            "status": "NOT_ADMITTED" if any(
                reason.startswith("VALUATION_FIELD_SOURCE_NOT_ADMITTED")
                or reason in {
                    "VALUATION_INPUT_LINEAGE_ROUTE_MISMATCH",
                    "VALUATION_ASSUMPTION_LINEAGE_ROUTE_MISMATCH",
                    "VALUATION_PRIMARY_PERIOD_MISMATCH",
                }
                for reason in lineage_reasons
            ) else "MISSING",
            "reason_codes": tuple(dict.fromkeys(lineage_reasons)),
            "field_lineage": {
                "inputs": normalized_input_sources,
                "assumptions": normalized_assumption_sources,
            },
        }
    try:
        methods, low, high, sensitivity = _ROUTE_COMPILERS[route_id](inputs, assumptions)
    except BusinessValuationError as exc:
        return {
            **common,
            "reason_codes": (str(exc),),
        }
    if not (math.isfinite(low) and math.isfinite(high) and 0 < low <= high):
        return {
            **common,
            "reason_codes": ("VALUATION_RANGE_INVALID",),
        }
    low = _round(low)
    high = _round(high)
    midpoint = _round((low + high) / 2.0)
    position = (
        "BELOW_RANGE" if current_price < low
        else "ABOVE_RANGE" if current_price > high
        else "WITHIN_RANGE"
    )
    fair_value = {"low": low, "midpoint": midpoint, "high": high, "currency": currency}
    summary = (
        f"估值：{_ROUTE_LABELS[route_id]}路线，"
        f"基于{','.join(methods)}的可核区间为 {low:.2f}–{high:.2f} {currency}；"
        f"当前价格 {current_price:.2f}，位置为{_POSITION_LABELS[position]}。"
        "该区间依赖所列假设，不覆盖数据、风险、流动性或执行否决。"
    )
    document = {
        **common,
        "status": "READY",
        "reason_codes": (),
        "methods": methods,
        "fair_value": fair_value,
        "price_position": position,
        "summary_zh": summary,
        "uncertainty_zh": (
            "区间依赖本次已列数据、业务方法与显式上下界假设；"
            "敏感性仅覆盖该区间，不覆盖数据、风险、流动性或执行否决。"
        ),
        "inputs": _normalized_numeric_mapping(inputs),
        "assumptions": _normalized_numeric_mapping(assumptions),
        "field_lineage": {
            "inputs": normalized_input_sources,
            "assumptions": normalized_assumption_sources,
        },
        "sensitivity": sensitivity,
        "product_admitted": True,
        "learning_admitted": False,
    }
    document["receipt_hash"] = sha256_payload({
        key: value for key, value in document.items() if key != "receipt_hash"
    })
    return document


def validate_business_valuation(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema_version") != SCHEMA_VERSION:
        raise BusinessValuationError("BUSINESS_VALUATION_SCHEMA_INVALID")
    result = dict(value)
    if result.get("status") != "READY":
        return result
    receipt_hash = _text(result.get("receipt_hash"))
    if len(receipt_hash) != 64:
        raise BusinessValuationError("BUSINESS_VALUATION_RECEIPT_MISSING")
    expected = sha256_payload({key: result[key] for key in result if key != "receipt_hash"})
    if receipt_hash != expected:
        raise BusinessValuationError("BUSINESS_VALUATION_RECEIPT_STALE")
    if result.get("product_admitted") is not True:
        raise BusinessValuationError("BUSINESS_VALUATION_PRODUCT_NOT_ADMITTED")
    if any(result.get(key) is not False for key in (
        "learning_admitted", "strategy_upgrade_authority",
        "veto_override_authority", "independent_action_authority",
    )):
        raise BusinessValuationError("BUSINESS_VALUATION_AUTHORITY_ESCALATION")
    return result


def _multiple_per_share(inputs: Mapping[str, Any], assumptions: Mapping[str, Any], *, metric: str, low_key: str, high_key: str, method: str):
    value = _required_positive(inputs, metric)
    low_multiple, high_multiple = _multiple_range(assumptions, low_key, high_key)
    return (method,), value * low_multiple, value * high_multiple, {
        "metric": metric,
        "metric_value": value,
        "multiple_low": low_multiple,
        "multiple_high": high_multiple,
    }


def _enterprise_multiple(inputs: Mapping[str, Any], assumptions: Mapping[str, Any], *, metric: str, low_key: str, high_key: str, method: str):
    total_metric = _required_positive(inputs, metric)
    net_debt = _required_finite(inputs, "net_debt")
    shares = _required_positive(inputs, "diluted_shares")
    low_multiple, high_multiple = _multiple_range(assumptions, low_key, high_key)
    low = (total_metric * low_multiple - net_debt) / shares
    high = (total_metric * high_multiple - net_debt) / shares
    return (method,), low, high, {
        "metric": metric,
        "metric_value": total_metric,
        "net_debt": net_debt,
        "diluted_shares": shares,
        "multiple_low": low_multiple,
        "multiple_high": high_multiple,
    }


def _mature(inputs, assumptions):
    return _multiple_per_share(
        inputs, assumptions,
        metric="normalized_eps", low_key="pe_low", high_key="pe_high",
        method="NORMALIZED_EARNINGS_PE",
    )


def _high_growth(inputs, assumptions):
    return _enterprise_multiple(
        inputs, assumptions,
        metric="revenue", low_key="ev_sales_low", high_key="ev_sales_high",
        method="FORWARD_EV_SALES",
    )


def _bank(inputs, assumptions):
    return _multiple_per_share(
        inputs, assumptions,
        metric="book_value_per_share", low_key="pb_low", high_key="pb_high",
        method="PB_SUSTAINABLE_ROE_ROUTE",
    )


def _insurance(inputs, assumptions):
    return _multiple_per_share(
        inputs, assumptions,
        metric="embedded_value_per_share", low_key="pev_low", high_key="pev_high",
        method="PRICE_TO_EMBEDDED_VALUE",
    )


def _cyclical(inputs, assumptions):
    return _enterprise_multiple(
        inputs, assumptions,
        metric="normalized_ebitda", low_key="ev_ebitda_low", high_key="ev_ebitda_high",
        method="MID_CYCLE_EV_EBITDA",
    )


def _utility(inputs, assumptions):
    dps = _required_positive(inputs, "next_dividend_per_share")
    cost_low = _required_rate(assumptions, "cost_of_equity_low")
    cost_high = _required_rate(assumptions, "cost_of_equity_high")
    growth_low = _required_rate(assumptions, "growth_low", allow_negative=True)
    growth_high = _required_rate(assumptions, "growth_high", allow_negative=True)
    if not (cost_low <= cost_high and growth_low <= growth_high):
        raise BusinessValuationError("VALUATION_ASSUMPTION_RANGE_INVALID")
    if cost_high <= growth_low or cost_low <= growth_high:
        raise BusinessValuationError("DDM_COST_OF_EQUITY_MUST_EXCEED_GROWTH")
    low = dps / (cost_high - growth_low)
    high = dps / (cost_low - growth_high)
    return ("GORDON_DDM",), low, high, {
        "next_dividend_per_share": dps,
        "cost_of_equity_low": cost_low,
        "cost_of_equity_high": cost_high,
        "growth_low": growth_low,
        "growth_high": growth_high,
    }


def _reit(inputs, assumptions):
    return _multiple_per_share(
        inputs, assumptions,
        metric="affo_per_share", low_key="paffo_low", high_key="paffo_high",
        method="PRICE_TO_AFFO",
    )


def _early_growth(inputs, assumptions):
    return _enterprise_multiple(
        inputs, assumptions,
        metric="revenue", low_key="ev_sales_low", high_key="ev_sales_high",
        method="REVENUE_UNIT_ECONOMICS_EV_SALES",
    )


def _etf(inputs, assumptions):
    nav = _required_positive(inputs, "nav_per_share")
    low_ratio, high_ratio = _multiple_range(
        assumptions, "price_to_nav_low", "price_to_nav_high",
    )
    return ("NAV_PREMIUM_DISCOUNT_BAND",), nav * low_ratio, nav * high_ratio, {
        "nav_per_share": nav,
        "price_to_nav_low": low_ratio,
        "price_to_nav_high": high_ratio,
        "tracking_and_liquidity_are_separate_vetoes": True,
    }


_ROUTE_COMPILERS: dict[str, Callable[[Mapping[str, Any], Mapping[str, Any]], tuple]] = {
    "MATURE_INDUSTRIAL_CONSUMER": _mature,
    "HIGH_GROWTH_TECH": _high_growth,
    "BANK": _bank,
    "INSURANCE": _insurance,
    "CYCLICAL_RESOURCE": _cyclical,
    "UTILITY_INFRASTRUCTURE": _utility,
    "REIT": _reit,
    "LOSS_MAKING_EARLY_GROWTH": _early_growth,
    "ETF": _etf,
}
_POSITION_LABELS = {
    "BELOW_RANGE": "区间下方",
    "WITHIN_RANGE": "区间内",
    "ABOVE_RANGE": "区间上方",
}


def _multiple_range(values: Mapping[str, Any], low_key: str, high_key: str) -> tuple[float, float]:
    low = _required_positive(values, low_key)
    high = _required_positive(values, high_key)
    if low > high:
        raise BusinessValuationError("VALUATION_ASSUMPTION_RANGE_INVALID")
    return low, high


def _required_positive(values: Mapping[str, Any], key: str) -> float:
    value = _finite(values.get(key))
    if value is None or value <= 0:
        raise BusinessValuationError("VALUATION_REQUIRED_POSITIVE_INPUT_MISSING:" + key)
    return value


def _required_finite(values: Mapping[str, Any], key: str) -> float:
    value = _finite(values.get(key))
    if value is None:
        raise BusinessValuationError("VALUATION_REQUIRED_NUMERIC_INPUT_MISSING:" + key)
    return value


def _required_rate(values: Mapping[str, Any], key: str, *, allow_negative: bool = False) -> float:
    value = _required_finite(values, key)
    if value >= 1 or (not allow_negative and value <= 0) or (allow_negative and value <= -1):
        raise BusinessValuationError("VALUATION_RATE_OUT_OF_RANGE:" + key)
    return value


def _normalized_numeric_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    result = {}
    for key, value in values.items():
        number = _finite(value)
        result[str(key)] = _round(number) if number is not None else value
    return result


def _positive(value: Any) -> float | None:
    number = _finite(value)
    return number if number is not None and number > 0 else None


def _finite(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: float) -> float:
    return round(float(value), 6)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _date_text(value: Any) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = _text(value)
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ""


def _aware_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        text = _text(value).replace("Z", "+00:00")
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(timezone.utc)


def _as_of_ceiling(value: date | datetime | str | None) -> datetime | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime.combine(value, time.max, tzinfo=timezone.utc)
    parsed = _aware_datetime(value)
    if parsed is not None:
        return parsed
    text = _text(value)
    if text and "T" not in text and " " not in text:
        try:
            return datetime.combine(date.fromisoformat(text), time.max, tzinfo=timezone.utc)
        except ValueError:
            return None
    return None
