"""Pure completeness boundary for the existing DSA market-statistics owners.

No fetch, cache, DB or rights grant. A counted snapshot is not a complete market:
an independently supplied same-day universe and known-at clocks must agree.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, timezone
import math
import re
from zoneinfo import ZoneInfo

import pandas as pd

from data_provider.daily_data_identity import sha256_payload


class MarketBreadthError(ValueError):
    """The source cannot support the claimed breadth denominator."""


_ALIASES = {
    "code": ("代码", "股票代码", "ts_code", "stock_code", "code"),
    "close": ("最新价", "close", "lastPrice", "price"),
    "pre_close": ("昨收", "昨日收盘", "pre_close", "lastClose"),
    "amount": ("成交额", "amount"),
}

_UNIVERSE_SCOPE_MISSING_MARKETS = {
    "CN_ALL_A": (),
    "CN_SH_SZ_A_EXCLUDES_BJ": ("BSE",),
}


def _code(value) -> str:
    text = str(value).strip().upper()
    match = re.fullmatch(r"(?:(SH|SZ|BJ)[.]?)?(\d{6})(?:[.](SH|SZ|BJ))?", text)
    if not match:
        raise MarketBreadthError("BREADTH_INSTRUMENT_INVALID")
    prefix, code, suffix = match.groups()
    if prefix and suffix and prefix != suffix:
        raise MarketBreadthError("BREADTH_INSTRUMENT_EXCHANGE_CONFLICT")
    return code


def _columns(frame: pd.DataFrame) -> dict[str, str]:
    result = {}
    for name, choices in _ALIASES.items():
        col = next((key for key in choices if key in frame.columns), None)
        if col is None:
            raise MarketBreadthError("BREADTH_COLUMN_MISSING:" + name)
        result[name] = col
    return result


def validate_unique_market_instruments(frame: pd.DataFrame) -> list[str]:
    if not isinstance(frame, pd.DataFrame) or frame.empty:
        raise MarketBreadthError("BREADTH_SNAPSHOT_EMPTY")
    codes = [_code(value) for value in frame[_columns(frame)["code"]]]
    if len(codes) != len(set(codes)):
        raise MarketBreadthError("BREADTH_DUPLICATE_INSTRUMENT")
    return codes


def _number(value):
    if value is None or pd.isna(value) or str(value).strip() in {"", "-"}:
        return None
    if isinstance(value, bool):
        raise MarketBreadthError("BREADTH_NUMERIC_INVALID")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise MarketBreadthError("BREADTH_NUMERIC_INVALID") from exc
    if not math.isfinite(result):
        raise MarketBreadthError("BREADTH_NUMERIC_INVALID")
    return result


def _rows(frame: pd.DataFrame) -> list[dict]:
    codes = validate_unique_market_instruments(frame)
    cols = _columns(frame)
    result = []
    for code, (_, row) in zip(codes, frame.iterrows()):
        result.append({
            "code": code,
            **{key: _number(row[column]) for key, column in cols.items() if key != "code"},
            "trading_status": str(row.get("trading_status", "UNKNOWN")).strip().upper(),
        })
    return sorted(result, key=lambda item: item["code"])


def market_snapshot_content_hash(frame: pd.DataFrame) -> str:
    """Hash normalized observed fields, not a claimed upstream revision."""
    return sha256_payload(_rows(frame))


def _clock(value) -> datetime:
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise MarketBreadthError("BREADTH_CLOCK_INVALID") from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise MarketBreadthError("BREADTH_CLOCK_NAIVE")
    return result.astimezone(timezone.utc)


def _sha(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def seal_breadth_universe(value: Mapping) -> dict:
    """Seal explicit membership only; never invent an all-A security list."""
    if not isinstance(value, Mapping):
        raise MarketBreadthError("BREADTH_UNIVERSE_MISSING")
    required = {"scope", "trade_date", "codes", "source_identity_hash", "available_at"}
    body = {key: item for key, item in value.items() if key != "universe_hash"}
    if (
        set(body) != required
        or body["scope"] not in _UNIVERSE_SCOPE_MISSING_MARKETS
    ):
        raise MarketBreadthError("BREADTH_UNIVERSE_SCHEMA_INVALID")
    if not isinstance(body["codes"], (list, tuple)) or not body["codes"]:
        raise MarketBreadthError("BREADTH_UNIVERSE_CODES_INVALID")
    codes = [_code(item) for item in body["codes"]]
    if len(codes) != len(set(codes)):
        raise MarketBreadthError("BREADTH_DUPLICATE_INSTRUMENT")
    if not _sha(body["source_identity_hash"]):
        raise MarketBreadthError("BREADTH_UNIVERSE_IDENTITY_INVALID")
    try:
        date.fromisoformat(body["trade_date"])
    except (TypeError, ValueError) as exc:
        raise MarketBreadthError("BREADTH_UNIVERSE_DATE_INVALID") from exc
    _clock(body["available_at"])
    body["codes"] = sorted(codes)
    return {**body, "universe_hash": sha256_payload(body)}


def compile_market_breadth_observation(frame: pd.DataFrame, context: Mapping) -> dict:
    """Completed full-universe counts -> existing generic Registry source seam.

    PARTIAL is deliberate: this receipt grants neither Product use nor historical
    PIT. Missing/unknown status is not silently relabeled as a suspension.
    """
    if not isinstance(context, Mapping):
        raise MarketBreadthError("BREADTH_CONTEXT_MISSING")
    rows = _rows(frame)
    if context.get("source_content_sha256") != sha256_payload(rows):
        raise MarketBreadthError("BREADTH_SNAPSHOT_CONTENT_MISMATCH")
    universe = seal_breadth_universe(context.get("universe"))
    if context["universe"].get("universe_hash") != universe["universe_hash"]:
        raise MarketBreadthError("BREADTH_UNIVERSE_HASH_MISMATCH")
    if {row["code"] for row in rows} != set(universe["codes"]):
        raise MarketBreadthError("BREADTH_UNIVERSE_COVERAGE_MISMATCH")
    observed = _clock(context.get("source_observed_at"))
    available = _clock(context.get("source_available_at"))
    completed = _clock(context.get("completed_at"))
    as_of = _clock(context.get("research_as_of"))
    if not completed <= available <= observed <= as_of:
        raise MarketBreadthError("BREADTH_CLOCK_ORDER_INVALID")
    if _clock(universe["available_at"]) > observed:
        raise MarketBreadthError("BREADTH_UNIVERSE_NOT_KNOWN_AT")
    local_completed = completed.astimezone(ZoneInfo("Asia/Shanghai"))
    trade_date = str(context.get("trade_date", ""))
    if universe["trade_date"] != trade_date or local_completed.date().isoformat() != trade_date:
        raise MarketBreadthError("BREADTH_TRADE_DATE_MISMATCH")
    from src.core.trading_calendar import resolve_session_range_fail_closed
    target = local_completed.date()
    if local_completed.hour < 15 or resolve_session_range_fail_closed("cn", target, target) != [target]:
        raise MarketBreadthError("BREADTH_SESSION_NOT_COMPLETED")
    if context.get("currency") != "CNY" or context.get("amount_unit") != "CNY" or context.get("price_basis") != "RAW":
        raise MarketBreadthError("BREADTH_UNITS_OR_BASIS_INVALID")
    if any(not str(context.get(key) or "").strip() for key in ("provider_identity", "provider_route")):
        raise MarketBreadthError("BREADTH_PROVIDER_IDENTITY_MISSING")
    counts = {"up_count": 0, "down_count": 0, "flat_count": 0}
    excluded = []
    for row in rows:
        if row["trading_status"] == "SUSPENDED":
            if row["amount"] not in (None, 0.0):
                raise MarketBreadthError("BREADTH_SUSPENSION_CONTRADICTION")
            excluded.append(row["code"])
            continue
        if row["trading_status"] != "TRADING":
            raise MarketBreadthError("BREADTH_TRADING_STATUS_UNKNOWN")
        if any(row[key] is None or row[key] <= 0 for key in ("close", "pre_close", "amount")):
            raise MarketBreadthError("BREADTH_TRADING_QUOTE_INVALID")
        key = "up_count" if row["close"] > row["pre_close"] else "down_count" if row["close"] < row["pre_close"] else "flat_count"
        counts[key] += 1
    denominator = sum(counts.values())
    if not denominator:
        raise MarketBreadthError("BREADTH_ELIGIBLE_UNIVERSE_EMPTY")
    coverage_scope = universe["scope"]
    missing_markets = list(
        _UNIVERSE_SCOPE_MISSING_MARKETS[coverage_scope]
    )
    market_complete = not missing_markets
    facts = {
        **counts,
        "eligible_count": denominator,
        "breadth_ratio": counts["up_count"] / denominator,
        "universe_count": len(rows),
        "universe_hash": universe["universe_hash"],
        "coverage_scope": coverage_scope,
        "missing_markets": missing_markets,
        "market_universe_complete": market_complete,
        "excluded_by_reason": {"SUSPENDED": excluded} if excluded else {},
        "denominator_definition": (
            "EXACT_SAME_DAY_ALL_A_UNIVERSE_MINUS_EXPLICIT_SUSPENSIONS"
            if market_complete
            else "EXACT_SAME_DAY_SH_SZ_A_UNIVERSE_MINUS_EXPLICIT_SUSPENSIONS"
        ),
        "not_proven": [
            "PRICE_LIMIT_RULES", "MARKET_RIGHTS", "HISTORICAL_PIT",
            *([] if market_complete else ["BSE_BREADTH"]),
        ],
    }
    reason_codes = (
        ("COMPLETE_OBSERVED_UNIVERSE_COUNTS_NOT_PRODUCT",)
        if market_complete
        else ("VERIFIED_SH_SZ_UNIVERSE_EXCLUDES_BSE_NOT_PRODUCT",)
    )
    body = {
        "schema_version": "data-object-source-observation-v1",
        "dataset_id": "M_BASIC_BREADTH", "state": "PARTIAL",
        "reason_codes": reason_codes,
        "provider_identity": context["provider_identity"], "provider_route": context["provider_route"],
        "producer_authority": "data_provider.market_breadth_source.compile_market_breadth_observation",
        "source_content_sha256": context["source_content_sha256"],
        "source_observed_at": observed.isoformat(), "source_available_at": available.isoformat(),
        "completed_through": trade_date,
        "units": {"count": "SECURITY", "breadth_ratio": "FRACTION"},
        "currency": "NOT_APPLICABLE", "adjustment_basis": "RAW",
        "revision": "UNKNOWN", "vintage": "UNKNOWN",
        "historical_pit_eligible": False, "independent_source_value_admitted": True,
        "strategy_upgrade_authority": False, "facts": facts,
    }
    body["source_identity_hash"] = sha256_payload({
        "provider_identity": body["provider_identity"], "provider_route": body["provider_route"],
        "content": body["source_content_sha256"], "universe_hash": universe["universe_hash"],
        "observed_at": body["source_observed_at"], "trade_date": trade_date,
    })
    return {**body, "observation_hash": sha256_payload(body)}


def validate_market_breadth_observation(value: Mapping, *, target_date=None, as_of=None) -> dict:
    """Check the receipt again at a report/Registry consumer, including counts."""
    if not isinstance(value, Mapping):
        raise MarketBreadthError("BREADTH_OBSERVATION_MISSING")
    row = dict(value)
    if (
        row.get("schema_version") != "data-object-source-observation-v1"
        or row.get("dataset_id") != "M_BASIC_BREADTH" or row.get("state") != "PARTIAL"
        or row.get("historical_pit_eligible") is not False
        or row.get("strategy_upgrade_authority") is not False
        or row.get("independent_source_value_admitted") is not True
    ):
        raise MarketBreadthError("BREADTH_OBSERVATION_SCOPE_INVALID")
    if row.get("observation_hash") != sha256_payload({key: item for key, item in row.items() if key != "observation_hash"}):
        raise MarketBreadthError("BREADTH_OBSERVATION_HASH_MISMATCH")
    if any(not _sha(row.get(key)) for key in ("source_identity_hash", "source_content_sha256")):
        raise MarketBreadthError("BREADTH_SOURCE_IDENTITY_INVALID")
    # The outer digest proves byte integrity only. Re-check the producer's
    # fixed count/ratio semantics before an independently resealed receipt is
    # consumed. No change here grants Product, PIT, storage or action rights.
    if (
        row.get("units") != {"count": "SECURITY", "breadth_ratio": "FRACTION"}
        or row.get("currency") != "NOT_APPLICABLE"
        or row.get("adjustment_basis") != "RAW"
        or row.get("producer_authority") != "data_provider.market_breadth_source.compile_market_breadth_observation"
    ):
        raise MarketBreadthError("BREADTH_OBSERVATION_SEMANTICS_INVALID")
    if target_date is not None and row.get("completed_through") != str(target_date):
        raise MarketBreadthError("BREADTH_TRADE_DATE_MISMATCH")
    if _clock(row.get("source_available_at")) > _clock(row.get("source_observed_at")):
        raise MarketBreadthError("BREADTH_CLOCK_ORDER_INVALID")
    if as_of is not None and _clock(row["source_observed_at"]) > _clock(as_of):
        raise MarketBreadthError("BREADTH_OBSERVATION_AFTER_ASOF")
    facts = row.get("facts")
    if not isinstance(facts, Mapping):
        raise MarketBreadthError("BREADTH_FACTS_MISSING")
    fields = (
        "up_count", "down_count", "flat_count", "eligible_count",
        "universe_count",
    )
    if any(type(facts.get(key)) is not int or facts[key] < 0 for key in fields):
        raise MarketBreadthError("BREADTH_COUNT_INVALID")
    excluded = facts.get("excluded_by_reason")
    if not isinstance(excluded, Mapping) or set(excluded) - {"SUSPENDED"}:
        raise MarketBreadthError("BREADTH_EXCLUSION_INVALID")
    excluded_codes = excluded.get("SUSPENDED", [])
    if not isinstance(excluded_codes, list) or len(set(excluded_codes)) != len(excluded_codes):
        raise MarketBreadthError("BREADTH_EXCLUSION_INVALID")
    eligible = facts["eligible_count"]
    if (
        eligible <= 0
        or sum(facts[key] for key in fields[:3]) != eligible
        or facts["universe_count"] != eligible + len(excluded_codes)
    ):
        raise MarketBreadthError("BREADTH_COUNT_CONSERVATION")
    coverage_scope = facts.get("coverage_scope")
    if coverage_scope not in _UNIVERSE_SCOPE_MISSING_MARKETS:
        raise MarketBreadthError("BREADTH_COVERAGE_SCOPE_INVALID")
    expected_missing = list(
        _UNIVERSE_SCOPE_MISSING_MARKETS[coverage_scope]
    )
    if facts.get("missing_markets") != expected_missing:
        raise MarketBreadthError("BREADTH_MISSING_MARKETS_INVALID")
    complete = not expected_missing
    if facts.get("market_universe_complete") is not complete:
        raise MarketBreadthError("BREADTH_COMPLETENESS_INVALID")
    expected_definition = (
        "EXACT_SAME_DAY_ALL_A_UNIVERSE_MINUS_EXPLICIT_SUSPENSIONS"
        if complete
        else "EXACT_SAME_DAY_SH_SZ_A_UNIVERSE_MINUS_EXPLICIT_SUSPENSIONS"
    )
    if facts.get("denominator_definition") != expected_definition:
        raise MarketBreadthError("BREADTH_DENOMINATOR_DEFINITION_INVALID")
    if (
        not _sha(facts.get("universe_hash"))
        or facts.get("breadth_ratio") != facts["up_count"] / eligible
    ):
        raise MarketBreadthError("BREADTH_RATIO_OR_UNIVERSE_HASH_INVALID")
    expected_identity = sha256_payload({
        "provider_identity": row.get("provider_identity"),
        "provider_route": row.get("provider_route"),
        "content": row["source_content_sha256"],
        "universe_hash": facts["universe_hash"],
        "observed_at": row["source_observed_at"],
        "trade_date": row.get("completed_through"),
    })
    if row["source_identity_hash"] != expected_identity:
        raise MarketBreadthError("BREADTH_SOURCE_IDENTITY_MISMATCH")
    return row


def attach_breadth_evidence(stats: dict, frame: pd.DataFrame) -> dict:
    """Used by existing snapshot owners. Scalar statistics alone remain unproven."""
    codes = validate_unique_market_instruments(frame)
    denominator = sum(int(stats[key]) for key in ("up_count", "down_count", "flat_count"))
    if not 0 <= denominator <= len(codes):
        raise MarketBreadthError("BREADTH_DENOMINATOR_INVALID")
    result = {**stats, "breadth_denominator": denominator,
              "observed_unique_count": len(codes), "excluded_count": len(codes) - denominator,
              "market_universe_complete": False, "data_object_source_observations": {}}
    context = frame.attrs.get("dsa_market_breadth_context")
    if context is not None:
        obs = compile_market_breadth_observation(frame, context)
        if any(stats[key] != obs["facts"][key] for key in ("up_count", "down_count", "flat_count")):
            raise MarketBreadthError("BREADTH_LEGACY_CONSUMER_COUNT_MISMATCH")
        result["market_universe_complete"] = bool(
            obs["facts"]["market_universe_complete"]
        )
        result["data_object_source_observations"] = {"M_BASIC_BREADTH": obs}
    return result
