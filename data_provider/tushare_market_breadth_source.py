"""Pure Tushare batch parents -> existing DSA market-breadth snapshot.

This module performs no network request and grants no data rights.  It binds the
three official Tushare Pro response shapes needed for a current completed-day
breadth denominator: ``daily``, ``stock_basic`` and ``suspend_d``.  Historical
backfill is deliberately outside this contract because current ``stock_basic``
is not a historical constituent table.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
import re
from zoneinfo import ZoneInfo

import pandas as pd

from data_provider.daily_data_identity import sha256_payload
from data_provider.market_breadth_source import (
    market_snapshot_content_hash,
    seal_breadth_universe,
)


class TushareMarketBreadthError(ValueError):
    """Three batch responses cannot support the claimed current denominator."""


_ALLOWED_MARKETS = {"主板", "创业板", "科创板", "北交所"}
_SUFFIX_TO_EXCHANGE = {"SH": "SSE", "SZ": "SZSE", "BJ": "BSE"}
_REQUIRED_DAILY = {"ts_code", "trade_date", "close", "pre_close", "vol", "amount"}
_REQUIRED_BASIC = {
    "ts_code", "name", "market", "exchange", "list_status",
    "list_date", "delist_date",
}
_REQUIRED_SUSPEND = {"ts_code", "trade_date", "suspend_type"}
_ENDPOINT_ROW_LIMITS = {"BASIC": 6000, "DAILY": 6000, "SUSPEND": 5000}


def _target(value: str) -> tuple[str, date]:
    text = str(value or "").strip()
    if not re.fullmatch(r"\d{8}", text):
        raise TushareMarketBreadthError("TUSHARE_BREADTH_TRADE_DATE_INVALID")
    try:
        parsed = datetime.strptime(text, "%Y%m%d").date()
    except ValueError as exc:
        raise TushareMarketBreadthError("TUSHARE_BREADTH_TRADE_DATE_INVALID") from exc
    return text, parsed


def _clock(value, field: str) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
        except (TypeError, ValueError) as exc:
            raise TushareMarketBreadthError(
                "TUSHARE_BREADTH_CLOCK_INVALID:" + field
            ) from exc
    if result.tzinfo is None or result.utcoffset() is None:
        raise TushareMarketBreadthError(
            "TUSHARE_BREADTH_CLOCK_NAIVE:" + field
        )
    return result.astimezone(timezone.utc)


def _ts_code(value) -> tuple[str, str, str]:
    text = str(value or "").strip().upper()
    match = re.fullmatch(r"(\d{6})[.](SH|SZ|BJ)", text)
    if not match:
        raise TushareMarketBreadthError("TUSHARE_BREADTH_INSTRUMENT_INVALID")
    code, suffix = match.groups()
    return code, suffix, _SUFFIX_TO_EXCHANGE[suffix]


def _require_frame(
    frame, required: set[str], kind: str, *, allow_empty=False,
) -> pd.DataFrame:
    if not isinstance(frame, pd.DataFrame):
        raise TushareMarketBreadthError("TUSHARE_BREADTH_" + kind + "_INVALID")
    if not required.issubset(frame.columns):
        raise TushareMarketBreadthError("TUSHARE_BREADTH_" + kind + "_FIELDS_MISSING")
    if frame.empty and not allow_empty:
        raise TushareMarketBreadthError("TUSHARE_BREADTH_" + kind + "_EMPTY")
    # Tushare endpoint documentation declares these page maxima.  Landing
    # exactly on the maximum cannot prove the response was not truncated.
    limit = _ENDPOINT_ROW_LIMITS[kind]
    if len(frame) >= limit:
        raise TushareMarketBreadthError(
            "TUSHARE_BREADTH_" + kind + "_ROW_LIMIT_REACHED"
        )
    return frame.copy()


def _normalized_basic(frame: pd.DataFrame, target: date) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for row in frame.to_dict("records"):
        code, suffix, expected_exchange = _ts_code(row["ts_code"])
        market = str(row.get("market") or "").strip()
        exchange = str(row.get("exchange") or "").strip().upper()
        status = str(row.get("list_status") or "").strip().upper()
        if market not in _ALLOWED_MARKETS:
            continue
        if exchange != expected_exchange or status != "L":
            raise TushareMarketBreadthError(
                "TUSHARE_BREADTH_BASIC_EXCHANGE_OR_STATUS_INVALID"
            )
        list_text = str(row.get("list_date") or "").strip()
        try:
            list_date = datetime.strptime(list_text, "%Y%m%d").date()
        except ValueError as exc:
            raise TushareMarketBreadthError(
                "TUSHARE_BREADTH_BASIC_LIST_DATE_INVALID"
            ) from exc
        if list_date > target:
            continue
        delist_text = str(row.get("delist_date") or "").strip()
        if delist_text and delist_text.lower() != "nan":
            try:
                if datetime.strptime(delist_text, "%Y%m%d").date() <= target:
                    continue
            except ValueError as exc:
                raise TushareMarketBreadthError(
                    "TUSHARE_BREADTH_BASIC_DELIST_DATE_INVALID"
                ) from exc
        if code in result:
            raise TushareMarketBreadthError("TUSHARE_BREADTH_BASIC_DUPLICATE")
        result[code] = {
            "code": code,
            "ts_code": f"{code}.{suffix}",
            "name": str(row.get("name") or "").strip(),
            "market": market,
            "exchange": exchange,
            "list_date": list_text,
        }
    if not result:
        raise TushareMarketBreadthError("TUSHARE_BREADTH_BASIC_EMPTY")
    return result


def _normalized_daily(frame: pd.DataFrame, target_text: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for row in frame.to_dict("records"):
        if str(row.get("trade_date") or "").strip() != target_text:
            raise TushareMarketBreadthError("TUSHARE_BREADTH_DAILY_DATE_MISMATCH")
        code, suffix, _exchange = _ts_code(row["ts_code"])
        if code in result:
            raise TushareMarketBreadthError("TUSHARE_BREADTH_DAILY_DUPLICATE")
        try:
            close = float(row["close"])
            pre_close = float(row["pre_close"])
            amount = float(row["amount"]) * 1000.0  # Tushare daily amount: thousand CNY.
        except (TypeError, ValueError) as exc:
            raise TushareMarketBreadthError(
                "TUSHARE_BREADTH_DAILY_NUMERIC_INVALID"
            ) from exc
        result[code] = {
            "ts_code": f"{code}.{suffix}",
            "close": close,
            "pre_close": pre_close,
            "amount": amount,
        }
    return result


def _normalized_suspensions(frame: pd.DataFrame, target_text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for row in frame.to_dict("records"):
        if str(row.get("trade_date") or "").strip() != target_text:
            raise TushareMarketBreadthError("TUSHARE_BREADTH_SUSPEND_DATE_MISMATCH")
        if str(row.get("suspend_type") or "").strip().upper() != "S":
            raise TushareMarketBreadthError("TUSHARE_BREADTH_SUSPEND_TYPE_INVALID")
        code, suffix, _exchange = _ts_code(row["ts_code"])
        if code in result:
            raise TushareMarketBreadthError("TUSHARE_BREADTH_SUSPEND_DUPLICATE")
        result[code] = f"{code}.{suffix}"
    return result


def build_tushare_current_market_breadth_frame(
    daily: pd.DataFrame,
    stock_basic: pd.DataFrame,
    suspend_d: pd.DataFrame,
    *,
    trade_date: str,
    current_completed_trade_date: str,
    observed_at,
    research_as_of,
) -> pd.DataFrame:
    """Bind three same-run Tushare batches to one current completed-day frame.

    ``stock_basic`` is admitted only for the latest current observation.  This
    function does not claim historical membership, source rights, storage,
    learning or strategy authority.
    """
    target_text, target = _target(trade_date)
    current_text, _current = _target(current_completed_trade_date)
    if current_text != target_text:
        raise TushareMarketBreadthError(
            "TUSHARE_BREADTH_HISTORICAL_STOCK_BASIC_FORBIDDEN"
        )
    observed = _clock(observed_at, "observed_at")
    as_of = _clock(research_as_of, "research_as_of")
    completed = datetime.combine(
        target, time(15, 0), tzinfo=ZoneInfo("Asia/Shanghai")
    ).astimezone(timezone.utc)
    if not completed <= observed <= as_of:
        raise TushareMarketBreadthError("TUSHARE_BREADTH_CLOCK_ORDER_INVALID")

    basic_frame = _require_frame(stock_basic, _REQUIRED_BASIC, "BASIC")
    daily_frame = _require_frame(daily, _REQUIRED_DAILY, "DAILY")
    suspend_frame = _require_frame(
        suspend_d, _REQUIRED_SUSPEND, "SUSPEND", allow_empty=True
    )
    universe = _normalized_basic(basic_frame, target)
    quotes = _normalized_daily(daily_frame, target_text)
    suspensions = _normalized_suspensions(suspend_frame, target_text)

    universe_codes = set(universe)
    if not set(quotes) <= universe_codes or not set(suspensions) <= universe_codes:
        raise TushareMarketBreadthError("TUSHARE_BREADTH_ORPHAN_SOURCE_ROW")
    if set(quotes) & set(suspensions):
        raise TushareMarketBreadthError("TUSHARE_BREADTH_DAILY_SUSPEND_OVERLAP")
    missing = universe_codes - set(quotes) - set(suspensions)
    if missing:
        raise TushareMarketBreadthError(
            "TUSHARE_BREADTH_UNEXPLAINED_UNIVERSE_GAP:"
            + ",".join(sorted(missing)[:10])
        )

    rows = []
    for code in sorted(universe):
        base = universe[code]
        quote = quotes.get(code)
        if quote is not None:
            rows.append({
                "ts_code": quote["ts_code"], "name": base["name"],
                "close": quote["close"], "pre_close": quote["pre_close"],
                "amount": quote["amount"], "trading_status": "TRADING",
            })
        else:
            rows.append({
                "ts_code": suspensions[code], "name": base["name"],
                "close": None, "pre_close": None, "amount": 0.0,
                "trading_status": "SUSPENDED",
            })
    frame = pd.DataFrame(rows)

    exchanges = {item["exchange"] for item in universe.values()}
    unknown = exchanges - {"SSE", "SZSE", "BSE"}
    if unknown or not {"SSE", "SZSE"} <= exchanges:
        raise TushareMarketBreadthError("TUSHARE_BREADTH_EXCHANGE_COVERAGE_INVALID")
    scope = "CN_ALL_A" if "BSE" in exchanges else "CN_SH_SZ_A_EXCLUDES_BJ"
    basic_identity = sha256_payload([
        universe[code] for code in sorted(universe)
    ])
    sealed_universe = seal_breadth_universe({
        "scope": scope,
        "trade_date": target.isoformat(),
        "codes": sorted(universe),
        "source_identity_hash": basic_identity,
        "available_at": observed.isoformat(),
    })
    frame.attrs["dsa_market_breadth_context"] = {
        "provider_identity": "TushareFetcher",
        "provider_route": "TusharePro:daily+stock_basic+suspend_d",
        "source_content_sha256": market_snapshot_content_hash(frame),
        "source_observed_at": observed.isoformat(),
        "source_available_at": observed.isoformat(),
        "completed_at": completed.isoformat(),
        "trade_date": target.isoformat(),
        "currency": "CNY",
        "amount_unit": "CNY",
        "price_basis": "RAW",
        "research_as_of": as_of.isoformat(),
        "universe": sealed_universe,
    }
    return frame
